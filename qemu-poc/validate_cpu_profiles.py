#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Check image-independent CPU boundaries across optional observer profiles.

Disposable programs initialize their own registers. Different SRAM startup
contents and profile-specific evidence fields are deliberately not compared.
The callee's encodings follow the established focused ISA gates; no reference
implementation is imported or linked into the QEMU target.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import struct
import subprocess

import validate
from validate_peripherals import Guest as PeripheralGuest, INSPECTION, SFC

HERE = Path(__file__).resolve().parent
CACHE = HERE / ".cache/cpu-profile-validation"
ENTRY = 0x02000120
PROFILES = ("probe", "diag", "application", "alnk-probe")
GPRS = [0x81230000 + register * 0x10101 for register in range(16)]
SPRS = [0] * 16
SPRS[0] = 0xaabbccdd
SPRS[3] = 0x12345678
SPRS[5] = 0x89abcde5
SPRS[12:15] = [0x01c07900, 0x01c07800, 0x01c07000]
FAULT = "final THEN call with ELSE is unsupported"


class Guest:
    def __init__(self, prefix):
        self.words = [0x0000] * prefix
        # E064 supports special registers 0..14. Register 15 remains its
        # cleared CPU-reset value in every profile.
        for register, value in enumerate(SPRS[:15]):
            self.literal(0, value)
            self.words.extend((0xe064, (register << 8) | 0x80))
        for register, value in enumerate(GPRS):
            self.literal(register, value)
        self.setup_count = prefix + 30 + 16

    @property
    def pc(self):
        return ENTRY + len(self.words) * 2

    def literal(self, register, value):
        self.words.extend((0xffc0 | register, value & 0xffff, value >> 16))

    def bytes(self):
        return struct.pack("<" + "H" * len(self.words), *self.words) + bytes(16)


def case(prefix, gap, condition, kind=None, unsupported=False):
    guest = Guest(prefix)
    expected_gprs = list(GPRS)
    expected_sprs = list(SPRS)
    guest.literal(0, condition)
    expected_gprs[0] = condition
    if kind is None:
        guest.words.extend((0xea20, 0x1001, 0xe041, 0x1111, 0xe041, 0x2222,
                            0xe042, 0x3333))
        expected_gprs[1] = 0x1111 if condition == 0 else 0x2222
        expected_gprs[2] = 0x3333
        return guest, {"pc": guest.pc, "instructions": guest.setup_count + 4,
                       "registers": expected_gprs, "specials": expected_sprs}, None

    callee_literal = len(guest.words)
    guest.literal(3, 0)
    guest.words.extend((0xea20, 0x1001 if unsupported else 1))
    call_index = len(guest.words)
    width = 2 if kind == "direct" else 1
    guest.words.extend([0] * width)
    next_pc = guest.pc
    if unsupported:
        guest.words.extend((0xe042, 0x2222))
    guest.words.extend((0xe042, 0x3333))
    stop = guest.pc
    guest.words.extend([0x0000] * gap)
    callee = guest.pc
    guest.words.extend((0xe045, 0x55, 0xea25, 2, 0xe046, 0x66, 0x0080))
    guest.words[callee_literal + 1:callee_literal + 3] = [callee & 0xffff, callee >> 16]
    expected_gprs[3] = callee
    if kind == "direct":
        delta = (callee - next_pc) // 2
        guest.words[call_index:call_index + width] = [0xea80 | ((delta >> 16) & 63),
                                                    delta & 0xffff]
    elif kind == "short":
        delta = callee - next_pc
        guest.words[call_index] = (0x8001 | (((delta >> 6) & 7) << 4) |
                                   (((delta >> 1) & 31) << 8))
    else:
        guest.words[call_index] = 0x00c3

    if unsupported:
        # The unsupported CALL faults before retiring or overwriting RETS.
        expected = {"pc": ENTRY + call_index * 2,
                    "instructions": guest.setup_count + 3,
                    "registers": expected_gprs, "specials": expected_sprs}
    else:
        called = condition == 0
        expected_gprs[2] = 0x3333
        if called:
            expected_gprs[5:7] = [0x55, 0x66]
            expected_sprs[3] = next_pc
        expected = {"pc": stop, "instructions": guest.setup_count + (9 if called else 4),
                    "registers": expected_gprs, "specials": expected_sprs}
    return guest, expected, FAULT if unsupported else None


def run(name, profile, guest, expected, fault, qemu_sha):
    directory = CACHE / name / profile
    directory.mkdir(parents=True, exist_ok=True)
    for filename in ("state.json", "state.sram", "state.alnk"):
        (directory / filename).unlink(missing_ok=True)
    image = directory / "fixture.bin"
    data = guest.bytes()
    image.write_bytes(data)
    # Fault cases use a later checkpoint so the unsupported CALL is executed.
    stop = expected["pc"] + 0x100 if fault else expected["pc"]
    env = {key: value for key, value in os.environ.items() if not key.startswith("FM1_POC_")}
    settings = {"FM1_POC_STOP_PC": hex(stop), "FM1_POC_MAX_INSTRUCTIONS": "200",
                "FM1_POC_STATE_DIR": str(directory), "FM1_POC_FRAME_DIR": str(directory)}
    env.update(settings)
    command = [*validate.COMMAND, "-kernel", str(image), "-append", profile]
    result = subprocess.run(command, cwd=validate.ROOT, env=env, capture_output=True,
                            text=True, timeout=15)
    (directory / "stdout.txt").write_text(result.stdout)
    (directory / "stderr.txt").write_text(result.stderr)
    state_path = directory / "state.json"
    state = json.loads(state_path.read_text()) if state_path.exists() else None
    if profile in ("application", "alnk-probe"):
        validate.check(state is not None, f"{name}/{profile}: missing configured state observer")
    if fault:
        validate.check(result.returncode != 0 and fault in result.stderr and
                       f"PC 0x{expected['pc']:08x}" in result.stderr and
                       f"after {expected['instructions']} instructions" in result.stderr,
                       f"{name}/{profile}: unsupported CALL fault or retirement differs: {result.stderr}")
    else:
        validate.check(result.returncode == 0, f"{name}/{profile}: {result.stderr}")
        if state is None:
            state = json.loads(result.stdout)
    if state is not None:
        for field, value in expected.items():
            validate.check(state[field] == value, f"{name}/{profile}: {field} differs")
        if fault:
            validate.check(state["reason"] == fault, f"{name}/{profile}: captured fault differs")
    record = {"fixture_sha256": hashlib.sha256(data).hexdigest(), "qemu_sha256": qemu_sha,
              "command": command, "environment": settings, "returncode": result.returncode,
              "expected_architecture": expected, "expected_fault": fault, "state": state}
    (directory / "result.json").write_text(json.dumps(record, indent=2) + "\n")
    return {field: state[field] for field in expected} if state is not None else None


def run_unobserved(name, words, fault_pc, instructions, fault, qemu_sha):
    directory = CACHE / "observers-disabled" / name
    directory.mkdir(parents=True, exist_ok=True)
    artifacts = ("state.json", "state.sram", "state.alnk", "lcd.ppm")
    for filename in artifacts:
        (directory / filename).unlink(missing_ok=True)
    data = struct.pack("<" + "H" * len(words), *words) + bytes(16)
    image = directory / "fixture.bin"
    image.write_bytes(data)
    env = {key: value for key, value in os.environ.items() if not key.startswith("FM1_POC_")}
    # No profile, checkpoint, guest budget or capture request: default
    # application execution must retain CPU and hardware semantics on its own.
    command = [*validate.COMMAND, "-kernel", str(image)]
    result = subprocess.run(command, cwd=directory, env=env, capture_output=True,
                            text=True, timeout=15)
    (directory / "stdout.txt").write_text(result.stdout)
    (directory / "stderr.txt").write_text(result.stderr)
    match = re.search(r"at PC 0x([0-9a-f]+) after (\d+) instructions", result.stderr)
    validate.check(result.returncode != 0 and fault in result.stderr and match is not None and
                   int(match[1], 16) == fault_pc and int(match[2]) == instructions,
                   f"{name}/observers-disabled: fault, PC or retirement differs: {result.stderr}")
    validate.check(not result.stdout.strip() and not any((directory / item).exists() for item in artifacts),
                   f"{name}/observers-disabled: unexpected observer output")
    record = {"fixture_sha256": hashlib.sha256(data).hexdigest(), "qemu_sha256": qemu_sha,
              "command": command, "observer_configuration": "none", "returncode": result.returncode,
              "expected_fault": fault, "fault_pc": fault_pc, "instructions": instructions,
              "diagnostic": result.stderr.strip(), "host_timeout_seconds": 15}
    (directory / "result.json").write_text(json.dumps(record, indent=2) + "\n")
    print(f"PASS {name}: default application, observers disabled, exact fault/PC/retirement")


def unobserved_cases(qemu_sha):
    # Branch to a distinct failure opcode if any untouched GPR is nonzero.
    # This checks the actual loader handoff without capturing guest state.
    guest = PeripheralGuest()
    zero_checks = []
    for register in range(1, 16):
        zero_checks.append(len(guest.words))
        guest.emit(0xff01, register << 12, 0)  # Long-literal NE register, 0.
    guest.literal(1, 0x01c7fe08)
    pointer_check = len(guest.words)
    guest.emit(0xe881, 0)  # Register NE: r0 versus r1.
    success = guest.pc
    retired = guest.instructions
    guest.emit(0x0001)  # Expected unsupported opcode marks all checks passed.
    failure = guest.pc
    guest.emit(0x0003)
    for index in zero_checks:
        guest.words[index + 2] = (failure - (ENTRY + (index + 3) * 2)) // 2
    guest.words[pointer_check + 1] = (failure - (ENTRY + (pointer_check + 2) * 2)) // 2
    run_unobserved("zero-register-and-r0-handoff", guest.words, success, retired,
                   "unsupported instruction 0x0001", qemu_sha)

    for condition in (0, 1):
        guest = PeripheralGuest()
        guest.literal(0, condition)
        guest.emit(0xea20, 0x1001)
        guest.emit(0xe041, 2)
        guest.emit(0xe041, 3)
        guest.add(1, -(2 if condition == 0 else 3))
        success = guest.pc + 4
        guest.branch_zero(1, success)
        guest.emit(0x0003)  # Wrong arm is a different fault at a different PC.
        guest.emit(0x0001)
        run_unobserved(f"conditional-arm-{condition}", guest.words, success, 5,
                       "unsupported instruction 0x0001", qemu_sha)

    for kind in ("direct", "short", "indirect"):
        guest, expected, _ = case(0, 1, 0, kind)
        # Replace the gap's first NOP, retaining the assembled callee address.
        # The final CALL must close its IF before the callee starts another IF.
        guest.words[(expected["pc"] - ENTRY) // 2] = 0x0001
        run_unobserved(f"conditional-call-{kind}", guest.words, expected["pc"],
                       expected["instructions"], "unsupported instruction 0x0001", qemu_sha)
    guest, expected, fault = case(0, 1, 0, "direct", unsupported=True)
    run_unobserved("unsupported-final-then-else-call", guest.words, expected["pc"],
                   expected["instructions"], fault, qemu_sha)

    guest = PeripheralGuest()
    guest.write(0x01eee240, 0xe7)
    guest.write(0x01eee2c0, INSPECTION + 2)
    guest.write(0x01eee280, INSPECTION + 2)
    guest.write(0x01eee348, 1)
    fault_pc = guest.write(INSPECTION, 0x55667788)
    run_unobserved("write-guard-crossing", guest.words, fault_pc, guest.instructions - 1,
                   "CPU write intersects an enabled guest guard window", qemu_sha)

    guest = PeripheralGuest()
    fault_pc = guest.write(ENTRY, 0x55667788)
    run_unobserved("nor-xip-store", guest.words, fault_pc, guest.instructions - 1,
                   "write to read-only XIP", qemu_sha)

    guest = PeripheralGuest()
    guest.write(SFC, 0)
    fault_pc = guest.pc
    retired = guest.instructions
    guest.emit(0x0000)
    run_unobserved("sfc-disabled-fetch", guest.words, fault_pc, retired,
                   "XIP access while SFC is disabled or unrouted", qemu_sha)
    return 10


def main():
    qemu_sha = hashlib.sha256(validate.QEMU.read_bytes()).hexdigest()
    count = 0
    for layout, prefix, gap in (("compact", 0, 1), ("relocated", 7, 16)):
        cases = [(f"else-{condition}", condition, None, False) for condition in (0, 1)]
        for kind in ("direct", "short", "indirect"):
            cases.extend((f"call-{kind}-{condition}", condition, kind, False)
                         for condition in (0, 1))
            cases.append((f"unsupported-call-{kind}", 0, kind, True))
        for label, condition, kind, unsupported in cases:
            name = f"{layout}-{label}"
            guest, expected, fault = case(prefix, gap, condition, kind, unsupported)
            snapshots = [run(name, profile, guest, expected, fault, qemu_sha)
                         for profile in PROFILES]
            available = [snapshot for snapshot in snapshots if snapshot is not None]
            validate.check(all(snapshot == available[0] for snapshot in available),
                           f"{name}: observer profile changed architectural state")
            count += len(PROFILES)
            print(f"PASS {name}: same initialized CPU result across {', '.join(PROFILES)}")
    print(f"PASS {count} bounded CPU/profile runs")
    count += unobserved_cases(qemu_sha)
    print(f"PASS {count} bounded CPU runs including observer-disabled execution; "
          "no firmware compatibility or throughput claim")


if __name__ == "__main__":
    main()
