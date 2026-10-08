#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Targeted SSYNC and unchanged-firmware USB console regression; cache only.

Run with mise exec python@3.13.15 -- python qemu-poc/validate_usb_console.py
--label new-label. Add --long for 37 guest seconds and bootguard checks.
Stdout contains only firmware console bytes; validation messages use stderr.
No firmware rebuild, patch, physical device or reference emulator is used.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import selectors
import struct
import subprocess
import sys
import time

import run_felucca as runner
from validate_peripherals import Guest, INSPECTION

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
QEMU = HERE / ".cache/build/qemu-system-pi32v2"
CACHE = HERE / ".cache/usb-console-validation"
BASE = [str(QEMU), "-M", "fm1-poc", "-accel", "tcg,thread=single",
        "-icount", "shift=3,align=off,sleep=off", "-display", "none",
        "-monitor", "none", "-nodefaults"]
FLAGS = 0x89ABCDE5
SEEDS = [0x81230000 + index * 0x10101 for index in range(16)]


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def environment(**settings):
    clean = {key: value for key, value in os.environ.items()
             if not key.startswith("FM1_POC_")}
    clean.update(settings)
    return clean


def seeded_guest():
    guest = Guest()
    guest.literal(0, FLAGS)
    guest.emit(0xE064, 0x0580)  # PSR = r0, accepted special-register move.
    for register, value in enumerate(SEEDS):
        guest.literal(register, value)
    return guest


def check_barriers(directory):
    memory = seeded_guest()
    memory.literal(1, INSPECTION)
    memory.literal(0, 0x55667788)
    memory.store(0, 1)
    memory.emit(0x0022)  # SSYNC, evidenced by saved vendor disassembly.
    memory.load(2, 1)
    expected = list(SEEDS)
    expected[:3] = [0x55667788, INSPECTION, 0x55667788]

    conditional = seeded_guest()
    conditional.literal(0, 0)
    conditional.emit(0xEA20, 0x1001)  # IF r0 == 0, one THEN / one ELSE.
    conditional.emit(0x0022)  # Selected SSYNC.
    conditional.literal(3, 0xBAD)
    conditional.literal(0, 1)
    conditional.emit(0xEA20, 0x1001)
    conditional.emit(0x0022)  # Skipped SSYNC.
    conditional.literal(4, 0x4444)
    selected = list(SEEDS)
    selected[0], selected[4] = 1, 0x4444

    for name, guest, registers, count in (
            ("ssync-store-load", memory, expected, memory.instructions),
            ("ssync-conditional", conditional, selected, conditional.instructions - 2)):
        image = directory / f"{name}.bin"
        data = guest.bytes() + bytes(16)
        image.write_bytes(data)
        command = [*BASE, "-serial", "none", "-kernel", str(image), "-append", "diag"]
        result = subprocess.run(command, cwd=ROOT,
                                env=environment(FM1_POC_STOP_PC=hex(guest.pc),
                                                FM1_POC_MAX_INSTRUCTIONS="1000"),
                                capture_output=True, timeout=30)
        (directory / f"{name}.stdout").write_bytes(result.stdout)
        (directory / f"{name}.stderr").write_bytes(result.stderr)
        require(result.returncode == 0, f"{name}: {result.stderr.decode(errors='replace')}")
        state = json.loads(result.stdout)
        require(state["pc"] == guest.pc, f"{name}: wrong stop PC")
        require(state["instructions"] == count, f"{name}: wrong retirement count")
        require(state["registers"] == registers, f"{name}: register preservation failed")
        require(state["specials"][5] == FLAGS, f"{name}: PSR changed")
        require(state["irq_entries"] == 0, f"{name}: unexpected IRQ")
        if name == "ssync-store-load":
            require(state["inspection"][0] == 0x55667788, f"{name}: SRAM store missing")
        record = {"command": command, "fixture_sha256": hashlib.sha256(data).hexdigest(),
                  "returncode": result.returncode, "snapshot": state}
        (directory / f"{name}.json").write_text(json.dumps(record, indent=2) + "\n")
        print(f"PASS {name}", file=sys.stderr, flush=True)


def check_console(directory, firmware_dir, long_run):
    runner.checked_inputs(firmware_dir)
    limit = 4_625_000_000 if long_run else 500_000_000
    seconds = 600 if long_run else 180
    settings = {"FM1_POC_MAX_INSTRUCTIONS": str(limit), "FM1_POC_STATE_DIR": str(directory)}
    command = [*BASE, "-chardev", "stdio,id=fm1-console,signal=off",
               "-serial", "chardev:fm1-console", "-kernel",
               str(firmware_dir.resolve() / "felucca.bin"), "-append", "application"]
    metadata = {"command": command, "environment": settings, "input_hashes": runner.HASHES,
                "qemu_binary_sha256": hashlib.sha256(QEMU.read_bytes()).hexdigest(),
                "instruction_limit": limit, "host_timeout_seconds": seconds,
                "passed": False}
    output = bytearray()
    phase = 0
    response_start = 0
    responses = []
    commands = [b"help\r", b"status\r", b"help\r"]
    started = time.monotonic()
    process = None
    try:
        with (directory / "stdout.txt").open("wb") as out, (directory / "stderr.txt").open("wb") as err:
            process = subprocess.Popen(command, cwd=ROOT, env=environment(**settings),
                                       stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=err)
            with selectors.DefaultSelector() as selector:
                selector.register(process.stdout, selectors.EVENT_READ)
                while selector.get_map():
                    require(time.monotonic() - started < seconds, "host timeout")
                    for key, _ in selector.select(timeout=0.1):
                        chunk = os.read(key.fd, 65536)
                        if not chunk:
                            selector.unregister(key.fileobj)
                            continue
                        output.extend(chunk)
                        out.write(chunk)
                        out.flush()
                        sys.stdout.buffer.write(chunk)
                        sys.stdout.buffer.flush()
                        if phase > len(commands):
                            continue
                        current = bytes(output[response_start:])
                        if not current.endswith(b"> "):
                            continue
                        if phase == 0:
                            require(re.search(rb"\r\nFelucca [^\r\n]+ console - 'help'\r\n> $", current),
                                    "greeting differs or stdout contains host diagnostics")
                        else:
                            sent = commands[phase - 1].rstrip(b"\r")
                            require(current.startswith(sent + b"\r\n"), "command echo missing")
                            if sent == b"help":
                                require(b"status  dbg  crash  params  memr ADDR [LEN]  flr OFF [LEN]  uboot yes\r\n" in current,
                                        "actual firmware help response missing")
                            else:
                                require(b"felucca " in current and b"uptime_ms " in current,
                                        "actual firmware status response missing")
                            require(b"? (help)" not in current, "firmware rejected command")
                            responses.append(current.decode(errors="replace"))
                        if phase < len(commands):
                            response_start = len(output)
                            process.stdin.write(commands[phase])
                            process.stdin.flush()
                            phase += 1
                        else:
                            phase = len(commands) + 1
            process.wait(timeout=max(0.001, seconds - (time.monotonic() - started)))
        require(phase == len(commands) + 1, "console stopped before repeated commands completed")
        state = json.loads((directory / "state.json").read_text())
        require(state["reason"] == "instruction limit reached", f"unexpected stop: {state['reason']}")
        require(state["instructions"] == limit, "instruction budget differs")
        require(state["watchdog_expirations"] == 0, "watchdog expired")
        require(state["guards"]["debug_message"] == state["guards"]["emu_message"] == 0,
                "hardware guard fault")
        diagnostics = (directory / "stderr.txt").read_text(errors="replace")
        require("FM-1 USB console:" not in diagnostics, "USB host reported a failure")
        if long_run:
            ram = (directory / "state.sram").read_bytes()
            require(struct.unpack_from("<3I", ram, 0x01C7C08C - 0x01C00000) ==
                    (0x42475244, 0, 0), "bootguard failed or remains pending")
            require(struct.unpack_from("<16I", ram, 0x01C7C000 - 0x01C00000) == (0,) * 16,
                    "guest crash block is nonzero")
        metadata.update(passed=True, state=state, responses=responses)
    finally:
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        metadata.update(returncode=process.returncode if process else None,
                        elapsed_host_seconds=time.monotonic() - started)
        (directory / "run.json").write_text(json.dumps(metadata, indent=2) + "\n")
    print("PASS USB console greeting, help, status and repeated prompt" +
          ("; 37-second bootguard" if long_run else ""), file=sys.stderr, flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--label", required=True, help="new lowercase capture label")
    parser.add_argument("--firmware-dir", type=Path, default=runner.FIRMWARE)
    parser.add_argument("--long", action="store_true", help="37 guest seconds, including bootguard")
    args = parser.parse_args()
    if not re.fullmatch(r"[a-z0-9-]+", args.label):
        parser.error("use a simple lowercase label")
    directory = CACHE / args.label
    if directory.exists():
        parser.error(f"capture label already exists: {directory}")
    directory.mkdir(parents=True)
    try:
        check_barriers(directory)
        check_console(directory, args.firmware_dir, args.long)
    except (RuntimeError, OSError, ValueError, subprocess.TimeoutExpired) as error:
        print(f"FAIL: {error}; evidence: {directory}", file=sys.stderr)
        return 1
    print(f"Evidence: {directory}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
