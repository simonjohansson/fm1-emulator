#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Validate the exact EED2 byte store with unsigned immediate post-increment.

Pinned Apache SLEIGH and vendor EED2/2510 agree: store the incoming source's
low byte at the old base, then advance by an unsigned eight-bit byte stride.
The public reference agrees for all source/base pairs. Fault snapshots below
verify the model's store-before-writeback policy, not hardware fault state.
Pointers near 0xffffffff fault before writeback in the available memory map;
a successful wrapping post-increment cannot be observed through this setup.
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
CACHE = HERE / ".cache/postincrement-store-validation"
ENTRY = 0x02000120
INSPECTION = 0x01C08000
PSR = 0x89ABCDE5
GPRS = [0x10203040 + i * 0x01010101 for i in range(16)]
GPRS[0] = 0x01C7FE08
MARKERS = [0x12345678, 0x89ABCDEF, 0x76543210]
INITIAL_INSPECTION = [0xA5A5A5A5] * 5 + [0] * 3 + [0xA5A5A5A5] * 4


def setup(base, source, pointer, value, psr=PSR, condition=None, seed=False, guard=False):
    guest = Guest()
    if seed:
        for index, marker in enumerate(MARKERS):
            guest.write(INSPECTION + index * 4, marker)
    if guard:
        guest.write(0x01EEE240, 0xE7)
        guest.write(0x01EEE2C0, pointer)
        guest.write(0x01EEE280, pointer)
        guest.write(0x01EEE348, 1)
    guest.literal(4, psr)
    guest.emit(0xE064, 0x4580)
    expected = list(GPRS)
    if condition is not None:
        expected[0] = condition
    expected[source] = value & 0xFFFFFFFF
    expected[base] = pointer
    # Seed every GPR explicitly; generic and fault replays share the same
    # guest architecture independently of optional fixture reset values.
    for register, register_value in enumerate(expected):
        guest.literal(register, register_value)
    return guest, expected


def operand(base, source, stride):
    assert 0 <= stride <= 255
    return (source << 12) | ((stride >> 4) << 8) | (base << 4) | (stride & 15)


def save_image(name, guest):
    image = CACHE / f"{name}.bin"
    image.write_bytes(guest.bytes() + bytes(16))
    return image


def specials(psr):
    expected = [0] * 16
    expected[5] = psr
    return expected


def success_case(name, base, source, pointer, stride, value, psr=PSR, condition=None):
    guest, expected = setup(base, source, pointer, value, psr, condition)
    incoming_byte = expected[source] & 255
    if condition is not None:
        guest.emit(0xEA20, 1)  # One THEN instruction, selected if r0 & 1 == 0.
    guest.emit(0xEED2, operand(base, source, stride))
    guest.emit(0x0000)
    image = save_image(name, guest)
    state = isa.compare(name, image, guest.pc)
    selected = condition is None or condition == 0
    inspection = list(INITIAL_INSPECTION)
    if selected:
        offset = pointer - INSPECTION
        shift = (offset & 3) * 8
        inspection[offset // 4] = (inspection[offset // 4] & ~(255 << shift)) | (incoming_byte << shift)
        expected[base] = (pointer + stride) & 0xFFFFFFFF
    retired = guest.instructions - (0 if selected else 1)
    validate.check(state["pc"] == guest.pc and state["instructions"] == retired,
                   f"{name}: four-byte instruction/continuation retirement differs")
    validate.check(state["registers"] == expected and state["specials"] == specials(psr),
                   f"{name}: unsigned post-increment, source/base alias or PSR differs")
    validate.check(state["inspection"] == inspection,
                   f"{name}: old-address byte store, truncation or neighboring memory differs")
    return image, state, pointer, incoming_byte


def mapped_boundary_case():
    # The last SRAM byte is writable. Advancing beyond mapped SRAM is valid
    # until the resulting pointer is used by a later memory instruction.
    name = "mapped-sram-boundary"
    pointer = 0x01C7FFFF
    guest, expected = setup(15, 14, pointer, 0x123456FF)
    guest.emit(0xEED2, operand(15, 14, 255))
    guest.literal(7, pointer & ~3)
    guest.load(6, 7)
    guest.emit(0x0000)
    image = save_image(name, guest)
    state = isa.compare(name, image, guest.pc)
    expected[15] = pointer + 255
    expected[7] = pointer & ~3
    expected[6] = 0xFF000000
    validate.check(state["registers"] == expected and state["specials"] == specials(PSR) and
                   state["instructions"] == guest.instructions and state["pc"] == guest.pc,
                   "mapped-sram-boundary: byte destination, unchecked next pointer or retirement differs")


def fault_case(name, pointer, stride, reason, opcode=0xEED2, alias=False,
               guard=False, compare_reference=False):
    base, source = 15, 15 if alias else 14
    guest, expected = setup(base, source, pointer, 0x81234567, seed=True, guard=guard)
    fault_pc = guest.pc
    before = guest.instructions
    guest.emit(opcode, operand(base, source, stride))
    guest.literal(13, 0x33445566)
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
    if opcode == 0xEED2:
        validate.check(state["last_access"] == {"address": pointer, "size": 1, "flags": 1},
                       f"{name}: attempted old-address byte access differs")
    validate.check(state["registers"] == expected and state["specials"] == specials(PSR),
                   f"{name}: rejected store changed base, source, continuation or PSR")
    sram = (directory / "state.sram").read_bytes()
    validate.check(len(sram) == 0x80000 and
                   sram[0x8000:0x800C] == struct.pack("<III", *MARKERS),
                   f"{name}: rejected store altered destination or neighbors")
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
                       f"address: {pointer}, size: 1, operation: \"write\"" in reference.stderr,
                       f"{name}: separate reference fault address/width differs: {reference.stderr}")
        record["reference_fault_state_available"] = False
    (directory / "run.json").write_text(json.dumps(record, indent=2) + "\n")
    print(f"PASS {name}: fault before writeback/retirement; model state checked")


def generic_replay(image, expected, pointer, byte):
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
    word = byte << ((pointer & 3) * 8)
    validate.check(struct.unpack_from("<I", memory, (pointer & ~3) - 0x01C00000)[0] == word,
                   "generic-replay: byte destination, truncation or neighbors differ")
    (directory / "run.json").write_text(json.dumps({"command": command,
        "environment": settings, "returncode": result.returncode,
        "stderr": result.stderr, "state": state}, indent=2) + "\n")
    print("PASS generic-replay: default application mode matches CPU state and byte store")


def main():
    CACHE.mkdir(parents=True, exist_ok=True)
    isa.CACHE = CACHE
    replay = None
    for stride in (0, 1, 15, 16, 80, 127, 128, 255):
        result = success_case(f"stride-{stride}", 1, 2, INSPECTION + 1, stride, 0x81234567)
        if stride == 80:
            replay = result
    for position, value in enumerate((0x12345600, 0xABCDE07F, 0xFFFF1280, 0xDEADBEFF)):
        success_case(f"byte-position-{position}", 15, 14, INSPECTION + position, 80, value)
    for register in range(16):
        stride = (0, 1, 80, 255)[register % 4]
        success_case(f"source-base-alias-{register}", register, register,
                     INSPECTION + 1, stride, 0)
        success_case(f"distinct-registers-{register}", register, (register + 7) % 16,
                     INSPECTION + 2, 255, 0x123456FE)
    for psr in (0, 0xFFFFFFFF):
        success_case(f"psr-{psr:08x}", 1, 2, INSPECTION + 1, 80, 0x81234567, psr=psr)
    for condition in (0, 1):
        success_case(f"conditional-{condition}", 1, 2, INSPECTION + 1, 80,
                     0x81234567, condition=condition)
    mapped_boundary_case()
    generic_replay(*replay)
    fault_case("unmapped", 0x18000001, 80, "unmapped access at 0x18000001", compare_reference=True)
    fault_case("read-only-xip", ENTRY + 1, 255, "write to read-only XIP (NOR)", compare_reference=True)
    fault_case("guarded-byte", INSPECTION + 1, 80,
               "CPU write intersects an enabled guest guard window", guard=True)
    fault_case("alias-wrap-unmapped", 0xFFFFFFFF, 255, "unmapped access at 0xffffffff",
               alias=True, compare_reference=True)
    for opcode in (0xEED1, 0xEED3, 0xEED5, 0xEED6, 0xEED7):
        fault_case(f"unsupported-{opcode:04x}", INSPECTION + 1, 80,
                   f"unsupported instruction 0x{opcode:04x}", opcode=opcode)
    summary = {"passed": True, "instruction": "EED2 byte store with unsigned immediate post-increment",
               "reference_compared_cases": 49, "generic_replay_cases": 1,
               "access_fault_cases": 4, "reference_fault_address_cases": 3,
               "unsupported_neighbor_cases": 5,
               "qemu_sha256": hashlib.sha256(validate.QEMU.read_bytes()).hexdigest(),
               "hardware_validation": False, "hardware_fault_state_validation": False,
               "successful_wrapping_writeback_validation": False,
               "limitations": "available map faults before 32-bit wrapping writeback; reference has no fault-state snapshot"}
    (CACHE / "validation.json").write_text(json.dumps(summary, indent=2) + "\n")
    print("PASS EED2: 49 reference comparisons, generic replay and nine explicit faults")


if __name__ == "__main__":
    main()
