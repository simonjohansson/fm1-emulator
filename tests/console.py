#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Unchanged-firmware USB console regression; cache only.

Run with mise exec python@3.13.15 -- python tests/console.py
--label new-label --firmware-dir path/to/saved-artifacts. Add --long for
37 guest seconds and bootguard checks.
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

import firmware as runner
from support import ROOT, QEMU

CACHE = ROOT / ".cache/tests/console"
BASE = [str(QEMU), "--qemu", "-M", "fm1-poc", "-accel", "tcg,thread=single",
        "-icount", "shift=3,align=off,sleep=off", "-display", "none",
        "-monitor", "none", "-nodefaults"]


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def environment(**settings):
    clean = {key: value for key, value in os.environ.items()
             if not key.startswith("FM1_POC_")}
    clean.update(settings)
    return clean


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
    parser.add_argument("--firmware-dir", type=Path, required=True,
                        help="directory containing the pinned saved bin, ELF and disassembly")
    parser.add_argument("--long", action="store_true", help="37 guest seconds, including bootguard")
    args = parser.parse_args()
    if not re.fullmatch(r"[a-z0-9-]+", args.label):
        parser.error("use a simple lowercase label")
    directory = CACHE / args.label
    if directory.exists():
        parser.error(f"capture label already exists: {directory}")
    directory.mkdir(parents=True)
    try:
        check_console(directory, args.firmware_dir, args.long)
    except (RuntimeError, OSError, ValueError, subprocess.TimeoutExpired) as error:
        print(f"FAIL: {error}; evidence: {directory}", file=sys.stderr)
        return 1
    print(f"Evidence: {directory}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
