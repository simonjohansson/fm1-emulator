#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Validate exact E434 scalar mode0 unsigned and mode1 signed maximum.

Vendor E434/1131 at0x0200399c and E434/0050,0100,7741 establish maximum
modes and destination12:15/left4:7/right8:11 fields. The pinned Apache SLEIGH
has no exact maximum constructor; its minimum layout is only analogous.
Authority for this admission is direct vendor witnesses plus a separate
executable oracle. Independent sign boundaries and incoming aliases distinguish
signed from unsigned maximum; PSR/RETS and retirement are checked explicitly.
Shared multiply/divide/min semantics, helpers and classifiers stay fixed.
F434 bundles remain model unsupported, as does F435 in its existing gate.
Reference rejects sampled modes2..15 without exposing fault snapshots. These
canonical admission and fault-state checks do not establish hardware validity
or fault behavior. The actual preceding ABS pair and scalar maximum are also
checked with independent nonzero values, stopping before the deferred EE80.
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
CACHE = HERE / ".cache/maximum-validation"
ENTRY = 0x02000120
INSPECTION = 0x01C08000
STACK = INSPECTION - 16
PSR = 0x89ABCDE5
RETS = 0x12345678
GPRS = [0x10203040 + i * 0x01010101 for i in range(16)]
GPRS[0] = 0x01C7FE08
MARKERS = [0x12345678, 0x89ABCDEF, 0x76543210]
INSPECTION_WORDS = MARKERS + [0xA5A5A5A5] * 2 + [0] * 3 + [0xA5A5A5A5] * 4


def signed(value):
    return value if value < 0x80000000 else value - 0x100000000


def setup(registers, psr=PSR, guard=False):
    guest = Guest()
    for index, value in enumerate(MARKERS):
        guest.write(INSPECTION + index * 4, value)
    if guard:
        guest.write(0x01EEE240, 0xE7)
        header = guest.pc + 28 + 122  # Two14-byte writes and fixed full-state initializers.
        guest.write(0x01EEE380, header - 1)
        guest.write(0x01EEE384, ENTRY)
    guest.literal(4, psr)
    guest.emit(0xE064, 0x4580)
    guest.literal(4, RETS)
    guest.emit(0xE064, 0x4380)
    guest.literal(14, STACK, special=True)
    expected = list(GPRS)
    for register, value in registers.items():
        expected[register] = value & 0xFFFFFFFF
    for register, value in enumerate(expected):
        guest.literal(register, value)
    return guest, expected


def specials(psr=PSR):
    expected = [0] * 16
    expected[3], expected[5], expected[14] = RETS, psr, STACK
    return expected


def save_image(name, guest):
    image = CACHE / f"{name}.bin"
    image.write_bytes(guest.bytes() + bytes(16))
    return image


def check_success(name, state, expected, pc, count, psr=PSR):
    validate.check(state["pc"] == pc and state["instructions"] == count,
                   f"{name}: four-byte sizing or retirement differs")
    validate.check(state["registers"] == expected and state["specials"] == specials(psr),
                   f"{name}: incoming maximum operands, PSR or RETS differ")
    validate.check(state["inspection"] == INSPECTION_WORDS,
                   f"{name}: scalar maximum altered memory or neighbors")


def success_case(name, dest=0, leftreg=3, rightreg=0, left=0xFFFFFFFF, right=32767,
                 mode=1, psr=PSR, condition=None):
    incoming = {leftreg: left, rightreg: right}
    if condition is not None:
        incoming[4] = condition
        incoming[12] = 0
    guest, expected = setup(incoming, psr)
    a, b = expected[leftreg], expected[rightreg]
    if condition is not None:
        guest.emit(0xEA24, 1)
    word = (dest << 12) | (leftreg << 4) | (rightreg << 8) | mode
    guest.emit(0xE434, word)
    if condition is not None:
        guest.emit(0xEA2C, 1)  # The first selected/skipped scalar arm must close.
        guest.emit(0xE04D, 0x7777)
        expected[13] = 0x7777
    guest.emit(0)
    selected = condition is None or condition == 0
    if selected:
        choose_left = signed(a) >= signed(b) if mode else a >= b
        expected[dest] = a if choose_left else b
    image = save_image(name, guest)
    state = isa.compare(name, image, guest.pc, limit=100)
    check_success(name, state, expected, guest.pc, guest.instructions - (not selected), psr)
    (CACHE / f"{name}-evidence.json").write_text(json.dumps({"opcode": 0xE434,
        "second_word": word, "incoming_left": a, "incoming_right": b,
        "signed_mode": mode == 1, "selected": selected}, indent=2) + "\n")
    return image, state


def actual_absolute_sequence():
    name = "actual-absolute-and-maximum-sequence"
    guest, expected = setup({0: INSPECTION - 4, 2: 0x80000000, 5: 0x81234567})
    guest.emit(0xF430, 0x1500, 0x6100)
    guest.emit(0xE430, 0x3200)
    guest.emit(0xE434, 0x1131)
    guest.emit(0)
    # ABS(INT_MIN) wraps; signed maximum then selects the positive r1 operand.
    expected[0], expected[1], expected[3] = MARKERS[0], 0x7EDCBA99, 0x80000000
    image = save_image(name, guest)
    state = isa.compare(name, image, guest.pc, limit=100)
    check_success(name, state, expected, guest.pc, guest.instructions)
    return image, state


def fault_case(name, mode=1, parallel=False, guard=False):
    guest, expected = setup({0: 32767, 3: 0xFFFFFFFF}, guard=guard)
    pc, before = guest.pc, guest.instructions
    opcode = 0xF434 if parallel else 0xE434
    guest.emit(opcode, 0x0030 | mode, *([0x2480] if parallel else []))
    guest.emit(0xE04D, 0x3344)
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
    result = subprocess.run(command, cwd=validate.ROOT, env=env,
                            text=True, capture_output=True, timeout=15)
    reason = ("guest PC lies outside both configured guard windows" if guard else
              f"unsupported instruction 0x{opcode:04x}")
    state_path = directory / "state.json"
    validate.check(state_path.exists(), f"{name}: missing fault state: {result.stderr}")
    state = json.loads(state_path.read_text())
    validate.check(result.returncode != 0 and state["reason"] == reason and
                   state["pc"] == pc and state["instructions"] == before,
                   f"{name}: reason, PC or retirement stage differs: {result.stderr}")
    validate.check(state["last_access"] == {"address": pc, "size": 6 if parallel else 4, "flags": 2},
                   f"{name}: instruction/bundle span differs")
    validate.check(state["registers"] == expected and state["specials"] == specials(),
                   f"{name}: fault applied maximum, continuation, PSR or RETS effects")
    memory = (directory / "state.sram").read_bytes()
    validate.check(len(memory) == 0x80000 and memory[0x8000:0x800C] == struct.pack("<III", *MARKERS),
                   f"{name}: fault altered memory or neighbors")
    if guard:
        validate.check(state["guards"]["debug_message"] & (1 << 12), f"{name}: PC guard not latched")
    record = {"command": command, "environment": settings, "returncode": result.returncode,
              "stderr": result.stderr, "state": state, "operand_mode": mode,
              "fixture_sha256": hashlib.sha256(image.read_bytes()).hexdigest(),
              "deferred_parallel_policy": parallel, "hardware_fault_state_validation": False}
    (directory / "run.json").write_text(json.dumps(record, indent=2) + "\n")
    print(f"PASS {name}: model fault before maximum effects and retirement")


def generic_replay(image, expected):
    directory = CACHE / "generic-replay"
    directory.mkdir(exist_ok=True)
    for filename in ("state.json", "state.sram", "state.alnk"):
        (directory / filename).unlink(missing_ok=True)
    env = {key: value for key, value in os.environ.items() if not key.startswith("FM1_POC_")}
    settings = {"FM1_POC_STOP_PC": hex(expected["pc"]), "FM1_POC_MAX_INSTRUCTIONS": "100",
                "FM1_POC_STATE_DIR": str(directory)}
    env.update(settings)
    command = [*validate.COMMAND, "-kernel", str(image)]
    result = subprocess.run(command, cwd=validate.ROOT, env=env,
                            text=True, capture_output=True, timeout=15)
    validate.check(result.returncode == 0, f"generic-replay: {result.stderr}")
    state = json.loads((directory / "state.json").read_text())
    for field in ("pc", "instructions", "registers", "specials"):
        validate.check(state[field] == expected[field], f"generic-replay: {field} differs")
    memory = (directory / "state.sram").read_bytes()
    validate.check(memory[0x8000:0x800C] == struct.pack("<III", *MARKERS),
                   "generic-replay: initialized memory or neighbors differ")
    (directory / "run.json").write_text(json.dumps({"command": command,
        "environment": settings, "returncode": result.returncode, "stderr": result.stderr,
        "state": state}, indent=2) + "\n")
    print("PASS generic-replay: default loader matches scalar maximum")


def main():
    CACHE.mkdir(parents=True, exist_ok=True)
    isa.CACHE = CACHE
    cases = [
        dict(name="vendor-signed-1131", dest=1, leftreg=3, rightreg=1,
             left=0x80000000, right=0x7FFFFFFF),
        dict(name="vendor-unsigned-0050", dest=0, leftreg=5, rightreg=0,
             left=1, right=0xFFFFFFFF, mode=0),
        dict(name="vendor-unsigned-0100", dest=0, leftreg=0, rightreg=1,
             left=0xFFFFFFFF, right=0, mode=0),
        dict(name="vendor-signed-7741", dest=7, leftreg=4, rightreg=7,
             left=0xFFFFFFFF, right=1),
    ]
    for mode in (0, 1):
        for dest in range(16):
            cases.append(dict(name=f"mode{mode}-fields-{dest}", dest=dest,
                              leftreg=(dest + 1) % 16, rightreg=(dest + 7) % 16,
                              left=0x7FFFFFFF, right=0x80000000, mode=mode))
            cases.append(dict(name=f"mode{mode}-dest-left-alias-{dest}", dest=dest,
                              leftreg=dest, rightreg=(dest + 1) % 16,
                              left=0xFFFFFFFF, right=1, mode=mode))
            cases.append(dict(name=f"mode{mode}-dest-right-alias-{dest}", dest=dest,
                              leftreg=(dest + 1) % 16, rightreg=dest,
                              left=0x80000000, right=0x7FFFFFFF, mode=mode))
            cases.append(dict(name=f"mode{mode}-all-alias-{dest}", dest=dest,
                              leftreg=dest, rightreg=dest,
                              left=0x80000001, right=0x80000001, mode=mode))
        values = (0, 1, 0x7FFFFFFF, 0x80000000, 0xFFFFFFFF)
        for left in values:
            for right in values:
                cases.append(dict(name=f"mode{mode}-boundary-{left:08x}-{right:08x}", dest=8,
                                  leftreg=15, rightreg=14, left=left, right=right, mode=mode))
        for psr in (0, 0xFFFFFFFF):
            cases.append(dict(name=f"mode{mode}-psr-{psr:08x}", psr=psr, mode=mode))
        for condition in (0, 1):
            cases.append(dict(name=f"mode{mode}-conditional-{condition}", dest=8,
                              leftreg=15, rightreg=14, left=0x80000000, right=0x7FFFFFFF,
                              mode=mode, condition=condition))
    for case in cases:
        success_case(**case)
    generic_replay(*actual_absolute_sequence())
    for mode in range(2, 16):
        fault_case(f"unsupported-mode-{mode}", mode=mode)
    for mode in (0, 1):
        fault_case(f"deferred-parallel-mode{mode}", mode=mode, parallel=True)
        fault_case(f"pc-guard-mode{mode}", mode=mode, guard=True)
    summary = {"passed": True, "instruction": "exact E434 scalar unsigned/signed maximum",
               "reference_compared_cases": len(cases) + 1, "actual_absolute_sequence_cases": 1,
               "generic_replays": 1, "total_model_faults": 18,
               "unsupported_modes": 14, "deferred_parallel_faults": 2, "pc_guard_faults": 2,
               "exact_primary_constructor_available": False,
               "encoding_authority": "direct vendor witnesses and separate executable reference",
               "analogous_primary_minimum_blob": "19b640bc036b14df78ee32ac45595d759b502317",
               "qemu_sha256": hashlib.sha256(validate.QEMU.read_bytes()).hexdigest(),
               "hardware_validation": False, "hardware_fault_state_validation": False}
    (CACHE / "validation.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(f"PASS E434: {len(cases) + 1} reference comparisons, generic replay and 18 model faults")


if __name__ == "__main__":
    main()
