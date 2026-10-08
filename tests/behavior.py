#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Check unchanged Felucca boot, physical inputs, UI progress and guest audio."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import socket
import struct
import subprocess
import tempfile
import time

import firmware as runner
from support import ROOT, QEMU

CACHE = ROOT / ".cache/tests/behavior"
SRAM_BASE = 0x01c00000
SCAN_ADDRESS = 0x01c11ebc
CLOCK_ADDRESS = 0x01c11710
HOST_SECONDS = 600


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


class QMP:
    def __init__(self, path, deadline):
        self.deadline = deadline
        self.sock = socket.socket(socket.AF_UNIX)
        self.sock.settimeout(10)
        self.sock.connect(str(path))
        self.file = self.sock.makefile("rwb", buffering=0)
        self.read()
        self.request("qmp_capabilities")

    def read(self):
        remaining = self.deadline - time.monotonic()
        require(remaining > 0, "host observation deadline")
        self.sock.settimeout(min(10, remaining))
        line = self.file.readline()
        require(bool(line), "QEMU exited during QMP observation")
        return json.loads(line)

    def request(self, command, arguments=None):
        message = {"execute": command}
        if arguments is not None:
            message["arguments"] = arguments
        self.file.write((json.dumps(message) + "\n").encode())
        while True:
            response = self.read()
            if "event" in response:
                continue
            require("error" not in response, f"QMP error: {response.get('error')}")
            return response["return"]

    def words(self, address, count):
        text = self.request("human-monitor-command", {"command-line": f"xp /{count}wx 0x{address:x}"})
        values = [int(value, 16) for line in text.splitlines() if ":" in line
                  for value in re.findall(r"0x([0-9a-fA-F]+)", line.split(":", 1)[1])]
        require(len(values) == count, f"unexpected memory response: {text!r}")
        return values

    def key(self, name, down):
        self.request("input-send-event", {"events": [{"type": "key", "data": {
            "down": down, "key": {"type": "qcode", "data": name}}}]})

    def close(self):
        self.file.close()
        self.sock.close()


def signed_half(value):
    value &= 0xffff
    return value - 0x10000 if value & 0x8000 else value


class Behavior:
    def __init__(self, qmp, process, deadline, started, directory):
        self.qmp, self.process = qmp, process
        self.deadline, self.started = deadline, started
        self.directory = directory
        self.observations = []
        self.cadence = []

    def running(self):
        require(time.monotonic() < self.deadline, "host observation deadline")
        require(self.process.poll() is None, "guest exited; inspect state.json")

    def observe(self, label=None):
        self.running()
        require(self.qmp.request("query-status")["running"], "guest paused")
        debug = self.qmp.words(0x01c7c040, 19)
        inputs = self.qmp.words(0x01c11e40, 32)
        ui = self.qmp.words(0x01c12720, 1)[0]
        song = self.qmp.words(0x01c11a1c, 16)
        track = (self.qmp.words(0x01c11a54, 1)[0] >> 8) & 0xff
        # Encoder 3 on ENV controls DEC (parameter 2), a signed 16-bit song field.
        parameter = self.qmp.words(0x01c09228 + track * 0x598, 1)[0] & 0xffff
        result = {"label": label, "ms": self.qmp.words(CLOCK_ADDRESS, 1)[0],
                  "scan_frames": inputs[31], "notes": inputs[0], "buttons": inputs[1],
                  "bpm": song[0] & 0xffff, "octave": (song[14] >> 16) & 0xff,
                  "ui_frames": debug[7], "stage": debug[11], "home": ui & 0xff,
                  "page": (ui >> 8) & 0xff, "selected_track": track,
                  "env_decay": signed_half(parameter),
                  "bootguard": self.qmp.words(0x01c7c08c, 3),
                  "host_seconds": time.monotonic() - self.started}
        if label is not None:
            self.observations.append(result)
            print(json.dumps(result), flush=True)
        return result

    def wait_progress(self, scans, milliseconds=0, max_scans=None):
        self.running()
        start_scan = self.qmp.words(SCAN_ADDRESS, 1)[0]
        start_ms = self.qmp.words(CLOCK_ADDRESS, 1)[0]
        while True:
            self.running()
            scan_delta = self.qmp.words(SCAN_ADDRESS, 1)[0] - start_scan
            ms_delta = self.qmp.words(CLOCK_ADDRESS, 1)[0] - start_ms
            if max_scans is not None:
                require(scan_delta < max_scans, "encoder phase exceeded scan cadence bound")
            if scan_delta >= scans and ms_delta >= milliseconds:
                record = {"required_scans": scans, "required_ms": milliseconds,
                          "scans": scan_delta, "ms": ms_delta, "max_scans": max_scans}
                self.cadence.append(record)
                return
            time.sleep(0.005)

    def pcm_snapshot(self, label):
        words = self.qmp.words(0x01c08224, 1024)
        raw = struct.pack("<1024I", *words)
        samples = struct.unpack("<1024i", raw)
        energy = sum(sample * sample for sample in samples)
        name = label + ".pcm"
        (self.directory / name).write_bytes(raw)
        return {"pcm_file": name, "pcm_address": 0x01c08224,
                "pcm_nonzero_words": sum(sample != 0 for sample in samples), "pcm_words": len(samples),
                "pcm_energy_sum": energy, "pcm_mean_square": energy // len(samples),
                "pcm_peak_abs": max(abs(sample) for sample in samples)}

    def contact(self, key, hold_ms=100, release_ms=120):
        self.qmp.key(key, True)
        self.wait_progress(8, hold_ms)
        down = self.observe(key + "-down")
        require(down["buttons"] != 0, f"{key} button closure not scanned")
        self.qmp.key(key, False)
        self.wait_progress(8, release_ms)
        released = self.observe(key + "-released")
        require(released["buttons"] == 0, f"{key} button did not release")
        return released

    def detent(self, first, second):
        self.wait_progress(40, 100)
        for key, down in ((first, True), (second, True), (first, False), (second, False)):
            self.qmp.key(key, down)
            self.wait_progress(2, max_scans=40)
        self.wait_progress(40, 100)

    def exercise(self):
        while True:
            snapshot = self.observe()
            if snapshot["ms"] >= 30100 and snapshot["bootguard"] == [0x42475244, 0, 0]:
                break
            time.sleep(0.1)
        baseline = self.observe("bootguard-clear")
        require(baseline["home"] == 1, "complete boot did not reach HOME")
        for index, key in enumerate(("z", "c", "z"), 1):
            self.qmp.key(key, True)
            self.wait_progress(8, 150)
            down = self.observe(key + "-down")
            require(down["notes"] != 0, f"{key} note closure not scanned")
            down.update(self.pcm_snapshot(f"note-{index}-{key}-down"))
            require(down["pcm_nonzero_words"] > 0, f"{key} held note generated zero PCM")
            self.qmp.key(key, False)
            self.wait_progress(8, 500)
            released = self.observe(key + "-released")
            released.update(self.pcm_snapshot(f"note-{index}-{key}-released"))
            require(released["notes"] == 0, f"{key} note did not release")
            require(released["pcm_energy_sum"] < down["pcm_energy_sum"],
                    f"{key} released audio did not decay after 500 ms")
        before = self.observe()
        require(self.contact("x")["octave"] == (before["octave"] - 1) % 256, "octave minus failed")
        require(self.contact("v")["octave"] == before["octave"], "octave plus failed")
        before = self.observe()
        self.detent("s", "a")
        require(self.observe("bpm-detent")["bpm"] == before["bpm"] + 1, "BPM detent failed")
        for _ in range(2):
            self.contact("p")
            before = self.observe("parameter-page")
            require(before["home"] == 0, "parameter page did not open")
            self.detent("f", "d")
            after = self.observe("parameter-detent")
            require(after["selected_track"] == before["selected_track"], "selected track changed unexpectedly")
            require(after["env_decay"] == before["env_decay"] + 1, "ENV DEC detent did not increment song state")
            require(self.contact("h")["home"] == 1, "HOME did not return")
        # The second GLO press reaches text fitting's negative byte store.
        # Repeat the physical sequence to catch both a fault and lost release.
        for _ in range(2):
            before = self.observe()
            require(self.contact("f6")["home"] == 0, "GLO did not open")
            after = self.contact("f6")
            require(after["home"] == 0, "second GLO press left the page")
            require(after["ui_frames"] > before["ui_frames"], "GLO drawing stopped")
            require(self.contact("h")["home"] == 1, "HOME did not return from GLO")
        complete = self.observe("complete-interactions")
        require(complete["ui_frames"] > baseline["ui_frames"], "UI frames did not advance")
        return baseline, complete


def retain_state(directory, metadata, blobs):
    if not (directory / "state.json").exists():
        return None
    state = json.loads((directory / "state.json").read_text())
    metadata["state"] = state
    pc = state["pc"]
    ram = (directory / "state.sram").read_bytes()
    region, base = (ram, SRAM_BASE) if SRAM_BASE <= pc < SRAM_BASE + len(ram) else (blobs["felucca.bin"], runner.ENTRY)
    metadata["fault_opcode_bytes"] = region[pc - base:pc - base + 6].hex(" ") if base <= pc < base + len(region) else None
    lines = blobs["felucca.dis"].decode().splitlines()
    match = next((i for i, line in enumerate(lines) if re.match(rf"\s*{pc:x}:", line)), None)
    nearby = lines[max(0, match - 8):match + 9] if match is not None else ["No matching instruction in selected disassembly"]
    (directory / "nearby.dis").write_text("\n".join(nearby) + "\n")
    return state


def check_final(directory, state, complete):
    require(state["reason"] == "instruction limit reached", f"unexpected stop: {state['reason']}")
    require(state["watchdog_expirations"] == 0 and state["watchdog_feeds"] > 0, "watchdog service failed")
    require(state["guards"]["debug_message"] == 0 and state["guards"]["emu_message"] == 0, "hardware guard fault")
    require(state["irq11_entries"] > 0 and state["irq63_entries"] > 0, "audio/timer IRQ service missing")
    # The arbitrary instruction stop may land inside one current IRQ handler.
    require(state["irq_entries"] - state["rti_count"] == int(state["in_irq"]), "unaccounted IRQ returns")
    for source in (11, 63):
        debt = state[f"irq{source}_entries"] - state[f"irq{source}_rti_count"]
        expected = int(state["in_irq"] and state["last_irq_source"] == source)
        require(debt == expected, f"IRQ{source} return accounting failed")
    require("input queue overflow" not in (directory / "stderr.txt").read_text().lower(),
            "host input queue overflow invalidated input acceptance")
    require(state["alnk"]["completions"] > 0 and state["alnk"]["sample_words"] > 0
            and state["alnk"]["nonzero_words"] > 0, "DMA captured no nonzero guest audio")
    require(state["lcd"]["visible"] and state["lcd"]["completed_transfers"] > 0, "LCD service missing")
    ram = (directory / "state.sram").read_bytes()
    words = lambda address, count: struct.unpack_from(f"<{count}I", ram, address - SRAM_BASE)
    require(words(0x01c7c000, 16) == (0,) * 16, "guest crash block is nonzero")
    require(words(0x01c08220, 1)[0] == 0, "guest fault handler active")
    require(words(0x01c0d140, 2) == (0, 0), "LCD/P33 timeout recorded")
    require(words(0x01c7c08c, 3) == (0x42475244, 0, 0), "bootguard no longer clear")
    require(words(0x01c11e40, 1)[0] == 0, "note bitmap remained pressed")
    require(words(0x01c7c05c, 1)[0] > complete["ui_frames"], "UI stopped after interactions")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--label", required=True, help="new lowercase capture label; existing directories are refused")
    parser.add_argument("--firmware-dir", type=Path, required=True,
                        help="directory containing the pinned saved bin, ELF and disassembly")
    parser.add_argument("--guest-seconds", type=int, default=37)
    args = parser.parse_args()
    if not re.fullmatch(r"[a-z0-9-]+", args.label) or args.guest_seconds < 37:
        parser.error("use a simple lowercase label and at least 37 guest seconds")
    directory = CACHE / args.label
    if directory.exists():
        parser.error(f"capture label already exists: {directory}")
    blobs = runner.checked_inputs(args.firmware_dir)
    binary = QEMU
    metadata = {"binary_sha256": runner.HASHES["felucca.bin"], "input_hashes": runner.HASHES,
                "qemu_binary_sha256": hashlib.sha256(binary.read_bytes()).hexdigest(),
                "elf_load_segments_match_raw": True,
                "source_build_provenance": "artifact identity only; no rebuild attribution",
                "entry": runner.ENTRY, "functional_ns_per_instruction": 8,
                "flash_initialization": "1 MiB erased FF, unchanged app at physical offset 0x4120",
                "sram_initialization": "cold zero noinit/loader; A5 RAM text/data/BSS/pool/mailbox"}
    directory.mkdir(parents=True, exist_ok=False)
    settings = {"FM1_POC_STATE_DIR": str(directory),
                "FM1_POC_MAX_INSTRUCTIONS": str(args.guest_seconds * 125_000_000)}
    env = {key: value for key, value in os.environ.items() if not key.startswith("FM1_POC_")}
    env.update(settings)
    command = [str(binary), "--qemu", "-M", "fm1-poc", "-accel", "tcg,thread=single",
               "-icount", "shift=3,align=off,sleep=off", "-display", "none", "-serial", "none",
               "-monitor", "none", "-nodefaults", "-kernel", str(args.firmware_dir.resolve() / "felucca.bin"),
               "-append", "felucca"]
    metadata.update(command=command, environment=settings)
    report = {"guest_seconds": args.guest_seconds, "host_timeout_seconds": HOST_SECONDS,
              "interactions_passed": False, "passed": False}
    process = qmp = behavior = None
    started = time.monotonic()
    deadline = started + HOST_SECONDS
    try:
        with tempfile.TemporaryDirectory(prefix="fm1-behavior-") as temporary, (directory / "stdout.txt").open("w") as out, (directory / "stderr.txt").open("w") as err:
            endpoint = Path(temporary) / "qmp"
            command += ["-qmp", f"unix:{endpoint},server=on,wait=off"]
            process = subprocess.Popen(command, cwd=ROOT, env=env, stdout=out, stderr=err)
            while not endpoint.exists():
                require(process.poll() is None, "QEMU exited at startup")
                require(time.monotonic() - started < 10, "QMP startup timeout")
                time.sleep(0.01)
            qmp = QMP(endpoint, deadline)
            behavior = Behavior(qmp, process, deadline, started, directory)
            _, complete = behavior.exercise()
            report["interactions_passed"] = True
            process.wait(timeout=max(0.001, deadline - time.monotonic()))
            metadata["returncode"] = process.returncode
            state = retain_state(directory, metadata, blobs)
            require(state is not None, "no guest state captured")
            check_final(directory, state, complete)
            report["passed"] = True
    except Exception as error:
        report["error"] = repr(error)
        print(f"FIRST FAILURE: {error}", flush=True)
        if isinstance(error, (subprocess.TimeoutExpired, TimeoutError)) or time.monotonic() >= deadline:
            metadata.update(reason="host timeout", host_timeout_seconds=HOST_SECONDS)
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        metadata["returncode"] = process.returncode if process is not None else None
        retain_state(directory, metadata, blobs)
    finally:
        if qmp is not None:
            qmp.close()
        report["elapsed_host_seconds"] = time.monotonic() - started
        report["observations"] = behavior.observations if behavior is not None else []
        report["cadence"] = behavior.cadence if behavior is not None else []
        (directory / "run.json").write_text(json.dumps(metadata, indent=2) + "\n")
        (directory / "behavior.json").write_text(json.dumps(report, indent=2) + "\n")
    print(f"Evidence: {directory}")
    if not report["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
