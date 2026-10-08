#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Random panel stress test; readiness observations never change guest state."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import random
import re
import subprocess
import tempfile
import time

from behavior import QMP
from support import QEMU, ROOT, environment

# Read-only Felucca observations: valid debug block and the stage written
# after ui_draw returns. Bootguard is a later 30-second health check.
DEFAULT_READY = ("0x01c7c040:0x44424731", "0x01c7c06c:9")
INSTRUCTIONS_PER_MS = 125000  # Existing shift=3 functional clock: 8 ns/step.


class Failure(RuntimeError):
    def __init__(self, status, reason):
        super().__init__(reason)
        self.status = status


def memory_condition(text):
    try:
        address, values = text.split(":")
        address = int(address, 0)
        values = [int(word, 0) for word in values.split(",")]
        if (address & 3 or not values or address < 0x01c00000 or
                address + len(values) * 4 > 0x01c80000 or
                any(not 0 <= word <= 0xffffffff for word in values)):
            raise ValueError()
        return {"address": address, "values": values}
    except ValueError:
        raise argparse.ArgumentTypeError("use aligned SRAM ADDRESS:WORD[,WORD...]") from None


def panel_buttons():
    # Use the same contact/qcode declarations as Cocoa; encoders are not buttons.
    source = (ROOT / "src/include/ui/fm1-controls.h").read_text()
    contacts = re.findall(r'APPLY\("([^"\n]+)",\s*(\w+),\s*(\d+),\s*(\d+)\)', source)
    if not contacts:
        raise RuntimeError("panel contact declarations not found")
    return [{"label": label, "qcode": key.lower(), "column": int(column), "row": int(row)}
            for label, key, column, row in contacts]


class Stress:
    def __init__(self, args, directory):
        self.args, self.directory = args, directory
        self.started = time.monotonic()
        self.process = self.qmp = None
        self.last_cpu = None
        self.last_advance = self.started
        self.event_index = 0
        self.booted_at = None
        self.report = {"status": "starting", "passed": False, "seed": args.seed,
                       "firmware": str(args.firmware),
                       "firmware_sha256": hashlib.sha256(args.firmware.read_bytes()).hexdigest(),
                       "emulator_sha256": hashlib.sha256(QEMU.read_bytes()).hexdigest(),
                       "seconds": args.seconds, "time_basis": "host",
                       "hold_ms": args.hold_ms, "gap_ms": args.gap_ms,
                       "boot_timeout_seconds": args.boot_timeout,
                       "ready_memory": args.ready_memory, "ready_console": args.ready_console,
                       "buttons": panel_buttons(), "presses": {}, "failure": None}

    def alive(self):
        if self.process.poll() is not None:
            raise Failure("guest-fault", "guest exited unexpectedly")

    def request(self, command, arguments=None):
        self.alive()
        self.qmp.deadline = time.monotonic() + 2
        return self.qmp.request(command, arguments)

    def cpu(self):
        text = self.request("human-monitor-command", {"command-line": "info registers"})
        match = re.search(r"PC=([0-9a-fA-F]+).*instructions=(\d+)", text)
        if not match:
            raise RuntimeError("CPU observation lacks PC/instruction count")
        state = {"pc": int(match[1], 16), "instructions": int(match[2])}
        now = time.monotonic()
        if self.last_cpu is None or state["instructions"] != self.last_cpu["instructions"]:
            self.last_advance = now
        elif now - self.last_advance > 2:
            raise Failure("no-progress", "guest instruction counter stalled for two host seconds")
        self.last_cpu = state
        self.cpu_text = text
        return state

    def ready(self):
        self.cpu()
        probes = []
        for condition in self.args.ready_memory:
            self.qmp.deadline = time.monotonic() + 2
            values = self.qmp.words(condition["address"], len(condition["values"]))
            probes.append({**condition, "observed": values})
        self.report["boot_probes"] = probes
        matched = all(probe["observed"] == probe["values"] for probe in probes)
        if self.args.ready_console:
            console = (self.directory / "stdout.txt").read_text(errors="replace")
            matched &= re.search(self.args.ready_console, console) is not None
        return matched

    def key(self, button, down, log):
        state = self.cpu()
        event = {**button, "index": self.event_index,
                 "action": "press" if down else "release",
                 "host_seconds": time.monotonic() - self.started,
                 "guest_instructions": state["instructions"]}
        self.event_index += 1
        self.report["last_input"] = event
        log.write(json.dumps(event) + "\n")
        log.flush()  # Keep the attempted event even if dispatch crashes the guest.
        self.request("input-send-event", {"events": [{"type": "key", "data": {
            "down": down, "key": {"type": "qcode", "data": button["qcode"]}}}]})
        if down:
            presses = self.report["presses"]
            presses[button["qcode"]] = presses.get(button["qcode"], 0) + 1

    def delay(self, milliseconds, end):
        start = time.monotonic()
        target = self.cpu()["instructions"] + math.ceil(milliseconds * INSTRUCTIONS_PER_MS)
        while time.monotonic() < end:
            state = self.cpu()
            if time.monotonic() - start >= milliseconds / 1000 and state["instructions"] >= target:
                return
            time.sleep(min(0.005, max(0, end - time.monotonic())))

    def run(self, socket_path, out, err, log):
        command = [str(QEMU), "--qemu", "-M", "fm1-poc", "-accel", "tcg,thread=single",
                   "-icount", "shift=3,align=off,sleep=off", "-display", "none", "-S",
                   "-chardev", "stdio,id=console,signal=off", "-serial", "chardev:console",
                   "-monitor", "none", "-nodefaults", "-no-user-config",
                   "-qmp", f"unix:{socket_path},server=on,wait=off",
                   "-d", "guest_errors", "-D", str(self.directory / "guest.log"),
                   "-kernel", str(self.args.firmware), "-append", "application"]
        settings = {"FM1_POC_STATE_DIR": str(self.directory)}
        self.report.update(command=command, environment=settings, status="booting")
        self.process = subprocess.Popen(command, cwd=ROOT, env=environment(**settings),
                                        stdin=subprocess.PIPE, stdout=out, stderr=err)
        while not socket_path.exists():
            self.alive()
            if time.monotonic() - self.started > self.args.boot_timeout:
                raise Failure("boot-timeout", "QMP did not become available before the boot deadline")
            time.sleep(0.01)
        self.qmp = QMP(socket_path, time.monotonic() + 2)
        self.request("cont")
        while not self.ready():
            if time.monotonic() - self.started >= self.args.boot_timeout:
                raise Failure("boot-timeout", "readiness condition was not reached; no buttons clicked")
            time.sleep(0.05)
        self.booted_at = time.monotonic()
        self.report.update(status="clicking", boot_host_seconds=self.booted_at - self.started,
                           boot_cpu=self.last_cpu)
        print(f"Boot ready. Clicking for {self.args.seconds:g} host seconds; seed {self.args.seed}.", flush=True)
        end = self.booted_at + self.args.seconds
        rng = random.Random(self.args.seed)
        buttons = []
        held = None
        try:
            while time.monotonic() < end:
                if not buttons:
                    buttons = list(self.report["buttons"])
                    rng.shuffle(buttons)
                held = buttons.pop()
                self.key(held, True, log)
                # Finish the last hold even if the clicking deadline passes,
                # so its release cannot violate the minimum contact spacing.
                self.delay(self.args.hold_ms, math.inf)
                self.key(held, False, log)
                held = None
                self.delay(self.args.gap_ms, end)
            self.request("stop")
            self.cpu()
            self.report.update(status="completed", passed=True)
        finally:
            if held is not None and self.process.poll() is None:
                try:
                    self.key(held, False, log)
                except (RuntimeError, OSError):
                    pass

    def finish(self):
        # Snapshot while QMP is available; faults retain the emulator's existing
        # state.json, SRAM, audio and LCD captures without changing their schema.
        if self.process is not None and self.process.poll() is None:
            if self.qmp is not None:
                try:
                    self.request("stop")
                    self.cpu()
                    self.request("quit")
                except (RuntimeError, OSError):
                    pass
            try:
                self.process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.process.terminate()
                try:
                    self.process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                    self.process.wait(timeout=2)
        if self.process is not None and self.process.stdin:
            self.process.stdin.close()
        if self.qmp is not None:
            self.qmp.close()
        self.report.update(returncode=self.process.returncode if self.process else None,
                           elapsed_host_seconds=time.monotonic() - self.started,
                           last_cpu=self.last_cpu,
                           input_events=self.event_index)
        if self.booted_at is not None:
            self.report["active_host_seconds"] = time.monotonic() - self.booted_at
        state_path = self.directory / "state.json"
        if state_path.exists():
            state = json.loads(state_path.read_text())
            self.report["failure"] = {key: state[key] for key in ("reason", "pc", "instructions")}
            self.report.update(status="guest-fault", passed=False)
        elif self.report["failure"] is not None:
            self.report["failure"].update(self.last_cpu or {})
        if self.last_cpu is not None:
            (self.directory / "last-cpu.txt").write_text(self.cpu_text)
        (self.directory / "stress.json").write_text(json.dumps(self.report, indent=2) + "\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("firmware", type=Path, help="unchanged .bin, .fwsc or .ufw application")
    parser.add_argument("--seconds", type=float, default=30, help="host seconds of clicking after readiness (default: 30)")
    parser.add_argument("--boot-timeout", type=float, default=180, help="host boot deadline (default: 180)")
    parser.add_argument("--ready-memory", type=memory_condition, action="append", help="require SRAM ADDRESS:WORD[,WORD...]; repeat for multiple conditions")
    parser.add_argument("--ready-console", help="require this console regex; overrides default UI-ready check")
    parser.add_argument("--hold-ms", type=float, default=80, help="button hold in both host and guest milliseconds (default: 80)")
    parser.add_argument("--gap-ms", type=float, default=20, help="released gap in both host and guest milliseconds (minimum/default: 20)")
    parser.add_argument("--seed", type=int, help="random seed; saved with the input sequence")
    parser.add_argument("--output", type=Path, help="new capture directory; defaults to .cache/tests/stress/<timestamp>-<seed>")
    args = parser.parse_args()
    if any(not math.isfinite(value) or value <= 0 for value in (args.seconds, args.boot_timeout)):
        parser.error("seconds and boot timeout must be finite and positive")
    if any(not math.isfinite(value) or value < 20 for value in (args.hold_ms, args.gap_ms)):
        parser.error("hold and gap must each be at least 20 ms")
    if args.ready_console:
        try:
            re.compile(args.ready_console)
        except re.error as error:
            parser.error(f"invalid readiness regex: {error}")
    if args.ready_memory is None:
        args.ready_memory = [] if args.ready_console else [memory_condition(value) for value in DEFAULT_READY]
    args.firmware = args.firmware.resolve()
    if not args.firmware.is_file() or not QEMU.is_file():
        parser.error("firmware and built ./emulator must exist")
    if args.seed is None:
        args.seed = random.SystemRandom().getrandbits(64)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    directory = (args.output or ROOT / ".cache/tests/stress" / f"{stamp}-{args.seed}").resolve()
    if directory.exists():
        parser.error(f"capture directory already exists: {directory}")
    directory.mkdir(parents=True)
    stress = Stress(args, directory)
    print(f"Waiting for boot readiness. Evidence: {directory}", flush=True)
    with tempfile.TemporaryDirectory(prefix="fm1-stress-") as temporary:
        with (directory / "stdout.txt").open("wb") as out, (directory / "stderr.txt").open("wb") as err, (directory / "inputs.jsonl").open("w") as log:
            try:
                stress.run(Path(temporary) / "qmp", out, err, log)
            except KeyboardInterrupt:
                stress.report.update(status="interrupted", failure={"reason": "interrupted"})
            except (RuntimeError, OSError, ValueError) as error:
                status = error.status if isinstance(error, Failure) else "harness-error"
                stress.report.update(status=status, failure={"reason": str(error)})
            finally:
                stress.finish()
    failure = stress.report["failure"]
    if failure:
        where = f" at PC 0x{failure['pc']:08x}" if "pc" in failure else ""
        print(f"FAIL ({stress.report['status']}): {failure['reason']}{where}. Evidence: {directory}")
    else:
        print(f"PASS: {sum(stress.report['presses'].values())} clicks, {len(stress.report['presses'])}/{len(stress.report['buttons'])} buttons. Evidence: {directory}")
    return 0 if stress.report["passed"] else 130 if stress.report["status"] == "interrupted" else 1


if __name__ == "__main__":
    raise SystemExit(main())
