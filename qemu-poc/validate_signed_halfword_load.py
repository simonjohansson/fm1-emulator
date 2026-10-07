#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Validate exact ED54/55 signed halfword loads with even unsigned offsets.

Pinned Apache SLEIGH and vendor ED54/63BC, ED55/52FC establish the split
immediate, sign extension and absence of base writeback. All destination/base
pairs agree with the separate public reference for even offsets. Odd operands
and ED56/57 differ from the SLEIGH interpretation in that reference, so remain
explicitly unsupported. Fault snapshots verify existing QEMU policy only;
the public reference provides fault address/width but no architectural state.
Write guard windows permit reads; the negative guard case rejects instruction
fetch through the established PC guard policy. Hardware faults are unverified.
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
CACHE = HERE / ".cache/signed-halfword-load-validation"
ENTRY = 0x02000120
INSPECTION = 0x01C08000
PSR = 0x89ABCDE5
GPRS = [0x10203040 + i * 0x01010101 for i in range(16)]
GPRS[0] = 0x01C7FE08
INITIAL_INSPECTION = [0xA5A5A5A5] * 5 + [0] * 3 + [0xA5A5A5A5] * 4
CONTINUATION = 0x33445566


def encoding(dest, base, offset):
    assert 0 <= offset <= 510 and offset % 2 == 0
    return (0xED54 | (offset >> 8), (dest << 12) | (((offset >> 4) & 15) << 8) |
            (base << 4) | (offset & 14))


def signed_halfword(value):
    assert 0 <= value <= 0xFFFF
    return value if value < 0x8000 else value | 0xFFFF0000


def setup(base, pointer, value, address=INSPECTION, psr=PSR, condition=None, guard=None):
    guest = Guest()
    # A distinct other halfword and two neighboring words catch wide loads
    # and accidental writes, without depending on optional reset poisoning.
    word = (value << 16) | 0xA69B if address & 2 else (0xA69B << 16) | value
    markers = [word, 0x89ABCDEF, 0x76543210]
    for index, marker in enumerate(markers):
        guest.write(INSPECTION + index * 4, marker)
    if address not in (INSPECTION, INSPECTION + 2) and address < 0x02000000:
        guest.write(address & ~3, word)
    if guard == "write":
        # The halfword intersects a protected one-byte write range. Reads
        # remain permitted by the existing model, independently of stores.
        guest.write(0x01EEE240, 0xE7)
        guest.write(0x01EEE2C0, address + 1)
        guest.write(0x01EEE280, address + 1)
        guest.write(0x01EEE348, 1)
    elif guard == "pc":
        guest.write(0x01EEE240, 0xE7)
        guest.write(0x01EEE380, 0xFFFFFFFF)  # PC window high, initially inclusive.
        guest.write(0x01EEE384, ENTRY)       # PC window low.
    guest.literal(4, psr)
    guest.emit(0xE064, 0x4580)
    expected = list(GPRS)
    if condition is not None:
        expected[0] = condition
    expected[base] = pointer
    for register, initial in enumerate(expected):
        guest.literal(register, initial)
    if guard == "pc":
        # Three accepted setup instructions configure an inclusive PC range
        # ending just before the signed load; its whole fetch span is denied.
        expected[0] = guest.pc + 14 - 1
        expected[1] = 0x01EEE380
        guest.literal(0, expected[0])
        guest.literal(1, expected[1])
        guest.store(0, 1)
    return guest, expected, markers


def specials(psr):
    expected = [0] * 16
    expected[5] = psr
    return expected


def save_image(name, guest):
    image = CACHE / f"{name}.bin"
    image.write_bytes(guest.bytes() + bytes(16))
    return image


def success_case(name, dest=6, base=11, offset=60, value=0x8001,
                 address=INSPECTION, psr=PSR, condition=None, guard=None):
    pointer = (address - offset) & 0xFFFFFFFF
    guest, expected, markers = setup(base, pointer, value, address, psr, condition, guard)
    if condition is not None:
        guest.emit(0xEA20, 1)  # One THEN instruction, selected if r0 & 1 == 0.
    guest.emit(*encoding(dest, base, offset))
    guest.emit(0x0000)
    image = save_image(name, guest)
    state = isa.compare(name, image, guest.pc)
    selected = condition is None or condition == 0
    if selected:
        expected[dest] = signed_halfword(value)
    inspection = list(INITIAL_INSPECTION)
    inspection[:3] = markers
    retired = guest.instructions - (0 if selected else 1)
    validate.check(state["pc"] == guest.pc and state["instructions"] == retired,
                   f"{name}: four-byte load/continuation retirement differs")
    validate.check(state["registers"] == expected and state["specials"] == specials(psr),
                   f"{name}: sign extension, offset, incoming-base alias, writeback or PSR differs")
    validate.check(state["inspection"] == inspection,
                   f"{name}: signed load changed memory or neighboring halfwords")
    return image, state, markers


def fault_case(name, pointer, offset, reason, opcode=None, odd=False, alias=False,
               guard=None, compare_reference=False):
    base, dest = 15, 15 if alias else 14
    guest, expected, markers = setup(base, pointer, 0x8123, guard=guard)
    fault_pc = guest.pc
    before = guest.instructions
    op, x = encoding(dest, base, offset)
    if opcode is not None:
        op = opcode
    if odd:
        x |= 1
    guest.emit(op, x)
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
                   f"{name}: fault reason, PC or retirement differs: {result.stderr}")
    address = (pointer + offset) & 0xFFFFFFFF
    access = {"address": address, "size": 2, "flags": 0}
    if odd or opcode is not None or guard == "pc":
        access = {"address": fault_pc, "size": 4, "flags": 2}
    validate.check(state["last_access"] == access,
                   f"{name}: attempted effective address, width or instruction fetch differs")
    validate.check(state["registers"] == expected and state["specials"] == specials(PSR),
                   f"{name}: rejected load changed destination, base, continuation or PSR")
    memory = (directory / "state.sram").read_bytes()
    validate.check(len(memory) == 0x80000 and
                   memory[0x8000:0x800C] == struct.pack("<III", *markers),
                   f"{name}: rejected load altered memory or neighboring halfwords")
    if guard == "pc":
        validate.check(state["guards"]["debug_message"] == 1 << 12,
                       f"{name}: established PC guard fault status differs")
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
                       f'address: {address}, size: 2, operation: "read"' in reference.stderr,
                       f"{name}: separate reference fault address/width differs: {reference.stderr}")
        record["reference_fault_state_available"] = False
    (directory / "run.json").write_text(json.dumps(record, indent=2) + "\n")
    print(f"PASS {name}: fault before destination update/retirement; model state checked")


def generic_replay(image, expected, markers):
    directory = CACHE / "generic-replay"
    directory.mkdir(exist_ok=True)
    for filename in ("state.json", "state.sram", "state.alnk"):
        (directory / filename).unlink(missing_ok=True)
    env = {key: value for key, value in os.environ.items() if not key.startswith("FM1_POC_")}
    settings = {"FM1_POC_STOP_PC": hex(expected["pc"]), "FM1_POC_MAX_INSTRUCTIONS": "100",
                "FM1_POC_STATE_DIR": str(directory)}
    env.update(settings)
    command = [*validate.COMMAND, "-kernel", str(image)]  # Default application loader.
    result = subprocess.run(command, env=env, cwd=validate.ROOT,
                            capture_output=True, text=True, timeout=15)
    validate.check(result.returncode == 0, f"generic-replay: {result.stderr}")
    state = json.loads((directory / "state.json").read_text())
    for field in ("pc", "instructions", "registers", "specials"):
        validate.check(state[field] == expected[field], f"generic-replay: {field} differs")
    memory = (directory / "state.sram").read_bytes()
    validate.check(len(memory) == 0x80000 and
                   memory[0x8000:0x800C] == struct.pack("<III", *markers),
                   "generic-replay: signed load altered seeded memory or neighboring halfwords")
    (directory / "run.json").write_text(json.dumps({"command": command,
        "environment": settings, "returncode": result.returncode,
        "stderr": result.stderr, "state": state}, indent=2) + "\n")
    print("PASS generic-replay: default application mode matches load architecture and memory")


def main():
    CACHE.mkdir(parents=True, exist_ok=True)
    isa.CACHE = CACHE
    cases = []
    for offset in (0, 2, 254, 256, 300, 510):
        for value in (0, 0x7FFF, 0x8000, 0xFFFF):
            cases.append(dict(name=f"offset-{offset}-sign-{value:04x}", offset=offset,
                              value=value, address=INSPECTION + (offset & 2)))
    for register in range(16):
        cases.append(dict(name=f"destination-base-alias-{register}", dest=register,
                          base=register, offset=300 if register & 1 else 60,
                          value=0x8000 | register))
        cases.append(dict(name=f"destination-{register}-base-{(register + 1) % 16}",
                          dest=register, base=(register + 1) % 16,
                          offset=300 if register & 1 else 60, value=0x8001 + register))
    cases.extend([
        dict(name="reached-fields-negative", dest=6, base=11, offset=60),
        dict(name="next-fields-positive", dest=5, base=15, offset=300, value=0x7FFF),
        dict(name="low-offset-field-14", offset=14, address=INSPECTION + 2),
        dict(name="high-offset-field-16", offset=16),
        dict(name="incoming-base-below-sram", offset=2, address=0x01C00000),
        dict(name="last-sram-halfword", dest=15, base=15, offset=510,
             address=0x01C7FFFE, value=0xFFFF),
        # The immutable fixture starts with literal r1 (FFC1), whose low
        # halfword supplies an independent negative signed XIP read value.
        dict(name="read-only-xip-load", dest=15, base=14, offset=0,
             address=ENTRY, value=0xFFC1),
        dict(name="write-guard-permits-read", guard="write", address=INSPECTION + 2),
    ])
    for psr in (0, 0xFFFFFFFF):
        cases.append(dict(name=f"psr-{psr:08x}", psr=psr))
    for condition in (0, 1):
        cases.append(dict(name=f"conditional-{condition}", condition=condition))
    replay = None
    for case in cases:
        result = success_case(**case)
        if case["name"] == "reached-fields-negative":
            replay = result
    generic_replay(*replay)
    fault_case("unaligned-effective-address", INSPECTION + 1 - 300, 300,
               "unaligned access", alias=True, compare_reference=True)
    fault_case("unmapped-effective-address", 0x18000000 - 510, 510,
               "unmapped access at 0x18000000", compare_reference=True)
    fault_case("wrapping-effective-address", 0xFFFFFFFE, 2,
               "unmapped access at 0x00000000", alias=True, compare_reference=True)
    fault_case("pc-guard-rejects-load-fetch", INSPECTION - 60, 60,
               "guest PC lies outside both configured guard windows", guard="pc")
    for offset in (0, 256):
        opcode = 0xED54 | (offset >> 8)
        fault_case(f"odd-operand-{opcode:04x}", INSPECTION - offset, offset,
                   f"unsupported instruction 0x{opcode:04x}", odd=True, alias=True)
    for opcode in (0xED56, 0xED57):
        fault_case(f"deferred-{opcode:04x}", INSPECTION - 60, 60,
                   f"unsupported instruction 0x{opcode:04x}", opcode=opcode)
    summary = {"passed": True, "instruction": "ED54/55 signed halfword immediate loads",
               "positive_reference_cases": len(cases), "generic_replays": 1,
               "fault_cases": 8, "reference_fault_state_available": False,
               "hardware_fault_state_validation": False,
               "unsupported_forms": "odd operand bit 0 and ED56/57",
               "guard_scope": "write windows permit reads; PC window rejects instruction fetch"}
    (CACHE / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print("PASS signed halfword load milestone: sign, fields, aliases, faults and generic replay")


if __name__ == "__main__":
    main()
