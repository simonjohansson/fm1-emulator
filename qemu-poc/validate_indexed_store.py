#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Validate the evidenced ECDC kind-3 pre-index word store.

Vendor ECDC/5013 and the pinned Apache SLEIGH establish the unscaled register
sum, word width and base writeback. Supported aliases agree with the separate
public reference. Source==base disagrees between SLEIGH and that reference,
so it remains explicitly unsupported. Fault writeback is the existing QEMU
pre-index policy; the public snapshot reference returns no state on a fault,
and these tests do not establish hardware fault-state or priority behavior.
"""
import hashlib
import json
import os
from pathlib import Path
import struct
import subprocess

import validate
import validate_isa as isa
from validate_peripherals import Guest

HERE = Path(__file__).resolve().parent
CACHE = HERE / ".cache/indexed-store-validation"
ENTRY = 0x02000120
INSPECTION = 0x01C08000
PSR = 0x89ABCDE5
GPRS = [0x10203040 + i * 0x01010101 for i in range(16)]
GPRS[0] = 0x01C7FE08
CONTINUATION = 0x33445566
MARKERS = [0x12345678, 0x89ABCDEF, 0x76543210]
INITIAL_INSPECTION = [0xA5A5A5A5] * 5 + [0] * 3 + [0xA5A5A5A5] * 4


def setup(registers, psr=PSR, seed=False, guard=False):
    guest = Guest()
    if seed:
        for index, value in enumerate(MARKERS):
            guest.write(INSPECTION + index * 4, value)
    if guard:
        # The computed word starts below a protected one-byte range but
        # intersects it. These are the established guest guard registers.
        guest.write(0x01EEE240, 0xE7)
        guest.write(0x01EEE2C0, INSPECTION + 6)
        guest.write(0x01EEE280, INSPECTION + 6)
        guest.write(0x01EEE348, 1)
    guest.literal(4, psr)
    guest.emit(0xE064, 0x4580)
    expected = list(GPRS)
    for register, value in registers.items():
        expected[register] = value & 0xFFFFFFFF
    # Initialize every GPR in the guest so fault assertions and generic-mode
    # replays do not depend on profile-specific reset registers.
    for register, value in enumerate(expected):
        guest.literal(register, value)
    return guest, expected


def save_image(name, guest):
    image = CACHE / f"{name}.bin"
    image.write_bytes(guest.bytes() + bytes(16))
    return image


def specials(psr):
    expected = [0] * 16
    expected[5] = psr
    return expected


def success_case(name, base, index, source, registers, psr=PSR, condition=None):
    guest, expected = setup(registers, psr)
    operand = (source << 12) | (index << 8) | (base << 4) | 3
    address = (expected[base] + expected[index]) & 0xFFFFFFFF
    value = expected[source]
    selected = condition is None or condition == 0
    if condition is not None:
        guest.emit(0xEA20, 1)  # One THEN instruction, selected if r0 & 1 == 0.
    guest.emit(0xECDC, operand)
    guest.literal(13, CONTINUATION)
    image = save_image(name, guest)
    state = isa.compare(name, image, guest.pc)
    inspection = list(INITIAL_INSPECTION)
    if selected:
        validate.check(address == INSPECTION + 4, f"{name}: invalid test destination")
        inspection[1] = value
        expected[base] = address
    expected[13] = CONTINUATION
    retired = guest.instructions - (0 if selected else 1)
    validate.check(state["pc"] == guest.pc and state["instructions"] == retired,
                   f"{name}: four-byte store/continuation retirement differs")
    validate.check(state["registers"] == expected and state["specials"] == specials(psr),
                   f"{name}: base writeback, incoming aliases or PSR preservation differs")
    validate.check(state["inspection"] == inspection,
                   f"{name}: unscaled destination, word value or neighboring memory differs")
    return image, state, expected


def fault_case(name, base, index, source, registers, reason, kind=3,
               access_fault=True, guard=False, compare_reference=False):
    guest, expected = setup(registers, seed=True, guard=guard)
    fault_pc = guest.pc
    before = guest.instructions
    address = (expected[base] + expected[index]) & 0xFFFFFFFF
    guest.emit(0xECDC, (source << 12) | (index << 8) | (base << 4) | kind)
    guest.literal(13, CONTINUATION)
    image = save_image(name, guest)
    directory = CACHE / name
    directory.mkdir(exist_ok=True)
    for filename in ("state.json", "state.sram", "state.alnk"):
        (directory / filename).unlink(missing_ok=True)
    env = {key: value for key, value in os.environ.items() if not key.startswith("FM1_POC_")}
    settings = {"FM1_POC_STOP_PC": hex(guest.pc), "FM1_POC_MAX_INSTRUCTIONS": "100",
                "FM1_POC_STATE_DIR": str(directory)}
    env.update(settings)
    command = [*validate.COMMAND, "-kernel", str(image), "-append", "alnk-probe"]
    result = subprocess.run(command, env=env, cwd=validate.ROOT,
                            capture_output=True, text=True, timeout=15)
    state_path = directory / "state.json"
    validate.check(state_path.exists(), f"{name}: missing fault snapshot: {result.stderr}")
    state = json.loads(state_path.read_text())
    record = {"command": command, "environment": settings,
              "fixture_sha256": hashlib.sha256(image.read_bytes()).hexdigest(),
              "returncode": result.returncode, "stderr": result.stderr, "state": state,
              "hardware_fault_state_validation": False}
    validate.check(result.returncode != 0 and state["reason"] == reason and
                   state["pc"] == fault_pc and state["instructions"] == before,
                   f"{name}: fault reason, instruction PC or retirement differs: {result.stderr}")
    if access_fault:
        expected[base] = address
        validate.check(state["last_access"] == {"address": address, "size": 4, "flags": 1},
                       f"{name}: attempted store address/width differs")
    validate.check(state["registers"] == expected and state["specials"] == specials(PSR),
                   f"{name}: modeled fault writeback, untouched continuation or PSR differs")
    sram = (directory / "state.sram").read_bytes()
    validate.check(len(sram) == 0x80000 and
                   sram[0x8000:0x800C] == struct.pack("<III", *MARKERS),
                   f"{name}: rejected store altered destination or its neighbors")
    if compare_reference:
        reference_command = ["mise", "exec", "--", "cargo", "run", "--manifest-path",
                             str(HERE / "reference/Cargo.toml"), "--locked", "--offline",
                             "--", "snapshot", str(image)]
        reference = subprocess.run(reference_command, env=env, cwd=validate.ROOT,
                                   capture_output=True, text=True, timeout=60)
        record.update(reference_command=reference_command,
                      reference_returncode=reference.returncode,
                      reference_stdout=reference.stdout, reference_stderr=reference.stderr)
        validate.check(reference.returncode != 0 and not reference.stdout.strip() and
                       f"pc: {fault_pc}" in reference.stderr and
                       f"address: {address}, size: 4, operation: \"write\"" in reference.stderr,
                       f"{name}: separate reference fault address/width differs: {reference.stderr}")
        record["reference_fault_state_available"] = False
    (directory / "run.json").write_text(json.dumps(record, indent=2) + "\n")
    print(f"PASS {name}: explicit fault before retirement; modeled writeback checked")


def generic_replay(image, expected):
    directory = CACHE / "generic-replay"
    directory.mkdir(exist_ok=True)
    for filename in ("state.json", "state.sram", "state.alnk"):
        (directory / filename).unlink(missing_ok=True)
    stop = ENTRY + len(image.read_bytes()) - 16
    env = {key: value for key, value in os.environ.items() if not key.startswith("FM1_POC_")}
    settings = {"FM1_POC_STOP_PC": hex(stop), "FM1_POC_MAX_INSTRUCTIONS": "100",
                "FM1_POC_STATE_DIR": str(directory)}
    env.update(settings)
    # Omit -append: the default application loader uses the same CPU path.
    command = [*validate.COMMAND, "-kernel", str(image)]
    result = subprocess.run(command, env=env, cwd=validate.ROOT,
                            capture_output=True, text=True, timeout=15)
    validate.check(result.returncode == 0, f"generic-replay: {result.stderr}")
    state = json.loads((directory / "state.json").read_text())
    for field in ("pc", "instructions", "registers", "specials"):
        validate.check(state[field] == expected[field], f"generic-replay: {field} differs")
    memory = (directory / "state.sram").read_bytes()
    validate.check(struct.unpack_from("<I", memory, 0x8004)[0] == 0x81234567,
                   "generic-replay: store value/destination differs")
    (directory / "run.json").write_text(json.dumps({"command": command,
        "environment": settings, "returncode": result.returncode,
        "stderr": result.stderr, "state": state}, indent=2) + "\n")
    print("PASS generic-replay: default application mode matches CPU state and stored word")


def main():
    CACHE.mkdir(parents=True, exist_ok=True)
    isa.CACHE = CACHE
    cases = [
        ("reached-zero-offset", 1, 0, 5, {1: INSPECTION + 4, 0: 0, 5: 0}),
        ("positive", 1, 0, 5, {1: INSPECTION, 0: 4, 5: 0x81234567}),
        ("negative", 1, 0, 5, {1: INSPECTION + 8, 0: -4, 5: 0x89ABCDEF}),
        ("high-registers", 14, 8, 15, {14: INSPECTION + 12, 8: -8, 15: 0xFEDCBA98}),
        ("positive-wrap", 1, 0, 5, {1: 0xFFFFFFF0, 0: INSPECTION + 20, 5: 0x12345678}),
        ("negative-wrap", 1, 0, 5, {1: 0x02000000, 0: 0xFFC08004, 5: 0x76543210}),
        ("source-index-positive", 1, 0, 0, {1: INSPECTION, 0: 4}),
        ("source-index-negative", 1, 0, 0, {1: INSPECTION + 8, 0: -4}),
        ("base-index", 1, 1, 5, {1: (INSPECTION + 4) // 2, 5: 0xFFFFFFFF}),
        ("base-index-wrap", 1, 1, 5, {1: 0x80E04002, 5: 0x12345678}),
    ]
    replay = None
    for name, base, index, source, registers in cases:
        image, state, _ = success_case(name, base, index, source, registers)
        if name == "positive":
            replay = (image, state)
    for psr in (0, 0xFFFFFFFF):
        success_case(f"psr-{psr:08x}", 1, 0, 5,
                     {1: INSPECTION, 0: 4, 5: 0x81234567}, psr=psr)
    for condition in (0, 1):
        success_case(f"conditional-{condition}", 1, 0, 5,
                     {1: INSPECTION + 4, 0: condition, 5: 0x81234567}, condition=condition)
    generic_replay(*replay)
    for name, base, increment, reason in [
        ("unaligned", INSPECTION, 5, "unaligned access"),
        ("unmapped", 0x18000000, 4, "unmapped access at 0x18000004"),
        ("read-only-xip", ENTRY, 4, "write to read-only XIP (NOR)"),
    ]:
        fault_case(name, 1, 0, 5, {1: base, 0: increment, 5: 0x81234567},
                   reason, compare_reference=True)
    fault_case("guard-intersection", 1, 0, 5,
               {1: INSPECTION, 0: 4, 5: 0x81234567},
               "CPU write intersects an enabled guest guard window", guard=True)
    for name, index, registers in [
        ("source-base-unresolved", 0, {1: INSPECTION, 0: 4}),
        ("all-equal-unresolved", 1, {1: (INSPECTION + 4) // 2}),
    ]:
        fault_case(name, 1, index, 1, registers, "unsupported instruction 0xecdc",
                   access_fault=False)
    for kind in (0, 1, 4, 6, 7, 10, 11, 14, 15):
        fault_case(f"unsupported-kind-{kind:x}", 1, 0, 5,
                   {1: INSPECTION, 0: 4, 5: 0x81234567},
                   "unsupported instruction 0xecdc", kind=kind, access_fault=False)
    summary = {"passed": True, "instruction": "ECDC kind-3 pre-index word store",
               "reference_compared_cases": 14, "generic_replay_cases": 1,
               "access_fault_cases": 4, "reference_fault_address_cases": 3,
               "unresolved_alias_fault_cases": 2, "unsupported_kind_cases": 9,
               "qemu_sha256": hashlib.sha256(validate.QEMU.read_bytes()).hexdigest(),
               "hardware_validation": False, "hardware_fault_state_validation": False,
               "unresolved_behavior": "source==base value and hardware access-fault state/priority"}
    (CACHE / "validation.json").write_text(json.dumps(summary, indent=2) + "\n")
    print("PASS ECDC: 14 reference comparisons, generic replay and 15 explicit faults")


if __name__ == "__main__":
    main()
