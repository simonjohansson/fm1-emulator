#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Validate exact E435 operand mode1 signed minimum, preserving mode0 umin.

Pinned Apache arithops.sinc:374-380 and vendor E435/0031 at0x02003686
establish signed minimum with destination bits12:15, left bits4:7 and right
bits8:11. Results use actual incoming GPR values, including all source aliases.
PSR/RETS preservation is checked against the independent executable reference.
Scalar decoding changes only mode1; multiply/divide and unsigned minimum,
helpers and parallel classification stay fixed. F435 bundles remain model
unsupported. Modes2..15 and instruction guard faults preserve existing policy;
these checks do not establish hardware fault state or invalid-ISA behavior.
The actual preceding ED13 IF/literal/min body must complete before another IF.
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
CACHE = HERE / ".cache/signed-minimum-validation"
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
                   f"{name}: incoming min operands, PSR or RETS differ")
    validate.check(state["inspection"] == INSPECTION_WORDS,
                   f"{name}: scalar min altered memory or neighbors")


def success_case(name, dest=0, leftreg=3, rightreg=0, left=0xFFFFFFFF, right=32767,
                 mode=1, psr=PSR, condition=None):
    incoming = {leftreg: left, rightreg: right}
    if condition is not None:
        incoming[0] = condition
    guest, expected = setup(incoming, psr)
    a, b = expected[leftreg], expected[rightreg]
    if condition is not None:
        guest.emit(0xEA20, 1)
    word = (dest << 12) | (leftreg << 4) | (rightreg << 8) | mode
    guest.emit(0xE435, word)
    guest.emit(0)
    selected = condition is None or condition == 0
    if selected:
        choose_left = signed(a) <= signed(b) if mode else a <= b
        expected[dest] = a if choose_left else b
    image = save_image(name, guest)
    state = isa.compare(name, image, guest.pc, limit=100)
    check_success(name, state, expected, guest.pc, guest.instructions - (not selected), psr)
    (CACHE / f"{name}-evidence.json").write_text(json.dumps({"opcode": 0xE435,
        "second_word": word, "incoming_left": a, "incoming_right": b,
        "signed_mode": mode == 1, "selected": selected}, indent=2) + "\n")
    return image, state


def actual_if_case(value):
    name = f"actual-if-body-{value:08x}"
    guest, expected = setup({0: 0x89ABCDEF, 1: 0xFFFF8000, 3: value, 4: 0})
    guest.emit(0xED13, 0x4100)
    guest.emit(0xE040, 0x7FFF)
    guest.emit(0xE435, 0x0031)
    selected = signed(value) >= -32768
    if selected:
        expected[0] = value if signed(value) <= 32767 else 32767
    guest.emit(0xEA24, 1)  # Following IF must see the preceding block closed.
    guest.emit(0xE04C, 0x7777)
    guest.emit(0)
    expected[12] = 0x7777
    image = save_image(name, guest)
    state = isa.compare(name, image, guest.pc, limit=100)
    check_success(name, state, expected, guest.pc, guest.instructions - (0 if selected else 2))
    print(f"PASS {name}: actual IF/literal/min body and subsequent IF complete")


def fault_case(name, mode=1, parallel=False, guard=False):
    guest, expected = setup({0: 32767, 3: 0xFFFFFFFF}, guard=guard)
    pc, before = guest.pc, guest.instructions
    opcode = 0xF435 if parallel else 0xE435
    guest.emit(opcode, 0x0030 | mode, *([0] if parallel else []))
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
                   f"{name}: fault applied min, continuation, PSR or RETS effects")
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
    print(f"PASS {name}: model fault before min effects and retirement")


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
    print("PASS generic-replay: default loader matches signed minimum")


def main():
    CACHE.mkdir(parents=True, exist_ok=True)
    isa.CACHE = CACHE
    cases = [dict(name="reached-alias-nonzero")]
    for dest in range(16):
        cases.append(dict(name=f"fields-{dest}", dest=dest, leftreg=(dest + 1) % 16,
                          rightreg=(dest + 7) % 16, left=0x7FFFFFFF, right=0x80000000))
        cases.append(dict(name=f"dest-left-alias-{dest}", dest=dest, leftreg=dest,
                          rightreg=(dest + 1) % 16, left=0xFFFFFFFF, right=1))
        cases.append(dict(name=f"dest-right-alias-{dest}", dest=dest, leftreg=(dest + 1) % 16,
                          rightreg=dest, left=0x80000000, right=0x7FFFFFFF))
        cases.append(dict(name=f"all-alias-{dest}", dest=dest, leftreg=dest, rightreg=dest,
                          left=0x80000000, right=0x80000000))
        cases.append(dict(name=f"source-alias-{dest}", dest=(dest + 1) % 16,
                          leftreg=dest, rightreg=dest, left=0xFFFFFFFF, right=0xFFFFFFFF))
    values = (0, 1, 0x7FFFFFFF, 0x80000000, 0xFFFFFFFF)
    for mode in (0, 1):
        for left in values:
            for right in values:
                cases.append(dict(name=f"boundary-{mode}-{left:08x}-{right:08x}", dest=8,
                                  leftreg=15, rightreg=14, left=left, right=right, mode=mode))
    for psr in (0, 0xFFFFFFFF):
        cases.append(dict(name=f"psr-{psr:08x}", psr=psr))
    for condition in (0, 1):
        cases.append(dict(name=f"conditional-{condition}", dest=8, leftreg=15, rightreg=14,
                          left=0x80000000, right=0x7FFFFFFF, condition=condition))
    replay = None
    for case in cases:
        result = success_case(**case)
        if case["name"] == "reached-alias-nonzero":
            replay = result
    for value in (0, 32767, 0x7FFFFFFF, 0xFFFFFFFE, 0x80000000):
        actual_if_case(value)
    generic_replay(*replay)
    for mode in range(2, 16):
        fault_case(f"unsupported-mode-{mode}", mode=mode)
    fault_case("deferred-parallel-head", parallel=True)
    fault_case("pc-guard", guard=True)
    summary = {"passed": True, "instruction": "exact E435 mode1 signed minimum",
               "reference_compared_cases": len(cases) + 5, "unsigned_mode0_controls": 25,
               "actual_if_body_cases": 5, "generic_replays": 1, "total_model_faults": 16,
               "unsupported_modes": 14, "deferred_parallel_faults": 1, "pc_guard_faults": 1,
               "primary_arithmetic_blob": "19b640bc036b14df78ee32ac45595d759b502317",
               "qemu_sha256": hashlib.sha256(validate.QEMU.read_bytes()).hexdigest(),
               "hardware_validation": False, "hardware_fault_state_validation": False}
    (CACHE / "validation.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(f"PASS E435: {len(cases) + 5} reference comparisons, generic replay and sixteen model faults")


if __name__ == "__main__":
    main()
