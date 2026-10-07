#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Validate FF0C against explicit expectations and the separate public oracle.

The unchanged vendor disassembly identifies FF0C as signed greater-than with
a signed twelve-bit literal and a signed word displacement from PC+6. No
reference decoder implementation is imported or copied into QEMU.
"""
import hashlib
import json
import os
from pathlib import Path
import struct
import subprocess

import validate
import validate_isa as isa

HERE = Path(__file__).resolve().parent
CACHE = HERE / ".cache/long-signed-branch-validation"
ENTRY = 0x02000120
PSR = 0x89ABCDE5
RETS = 0x12345678


def header(register, value):
    return [*isa.literal(4, PSR), 0xE064, 0x4580,
            *isa.literal(4, RETS), 0xE064, 0x4380,
            *isa.literal(register, value & 0xFFFFFFFF)]


def save_image(name, words):
    image = CACHE / f"{name}.bin"
    image.write_bytes(struct.pack("<" + "H" * len(words), *words) + bytes(16))
    return image


def compare(name, image, stop, count):
    state = isa.compare(name, image, stop)
    validate.check(state["pc"] == stop, f"{name}: wrong destination")
    validate.check(state["instructions"] == count, f"{name}: wrong retirement count")
    validate.check(state["specials"][5] == PSR and state["specials"][3] == RETS,
                   f"{name}: changed PSR or RETS")
    return state


def branch_case(name, register, value, immediate, displacement, opcode=0xFF0C):
    # A long GOTO permits a negative displacement of -32768 words without
    # branching below mapped XIP. Padding is never executed.
    setup = header(register, value)
    jump_pc = ENTRY + len(setup) * 2
    branch_pc = ENTRY + 0x10040
    delta = (branch_pc - (jump_pc + 4)) // 2
    words = [*setup, 0xEAC0 | ((delta >> 16) & 63), delta & 0xFFFF]
    words.extend([0] * ((branch_pc - ENTRY) // 2 - len(words)))
    words.extend([opcode, (register << 12) | (immediate & 0xFFF), displacement & 0xFFFF])
    taken = value > immediate if opcode == 0xFF0C else (value & 0xFFFFFFFF) > (immediate & 0xFFF)
    stop = branch_pc + 6 + (displacement * 2 if taken else 0)
    words.extend([0] * max(0, (stop - ENTRY) // 2 + 4 - len(words)))
    image = save_image(name, words)
    state = compare(name, image, stop, 7)
    expected = [0x10203040 + i * 0x01010101 for i in range(16)]
    expected[0] = 0x01C7FE08
    expected[4] = RETS
    expected[register] = value & 0xFFFFFFFF
    validate.check(state["registers"] == expected, f"{name}: branch changed a register")


def conditional_case(condition, has_else):
    name = f"if-{condition}-else-{int(has_else)}"
    words = [*header(1, -1), *isa.literal(0, condition),
             0xEA20, 0x1001 if has_else else 1, 0xFF0C, 0x1FFF, 4]
    if has_else:
        words.extend([0xE042, 0x4444])
    words.extend([0xE043, 0x7777])
    stop = ENTRY + len(words) * 2
    image = save_image(name, words)
    count = 9 if has_else or condition == 0 else 8
    if condition == 0 and not has_else:
        state = compare(name, image, stop, count)
    else:
        # Black-box reference execution shows that its IF scanner lands on
        # FF0C's displacement word when skipping it. Do not use that scanner
        # as the six-byte sizing oracle or copy its implementation.
        directory = CACHE / name
        directory.mkdir(exist_ok=True)
        for filename in ("state.json", "state.sram", "state.alnk"):
            (directory / filename).unlink(missing_ok=True)
        env = {k: v for k, v in os.environ.items() if not k.startswith("FM1_POC_")}
        settings = {"FM1_POC_STOP_PC": hex(stop), "FM1_POC_MAX_INSTRUCTIONS": "40",
                    "FM1_POC_STATE_DIR": str(directory)}
        env.update(settings)
        command = [*validate.COMMAND, "-kernel", str(image), "-append", "alnk-probe"]
        result = subprocess.run(command, env=env, cwd=validate.ROOT,
                                capture_output=True, text=True, timeout=15)
        state = json.loads((directory / "state.json").read_text())
        reference_command = ["mise", "exec", "--", "cargo", "run", "--manifest-path",
                             str(HERE / "reference/Cargo.toml"), "--locked", "--offline",
                             "--", "snapshot", str(image)]
        reference = subprocess.run(reference_command, env=env, cwd=validate.ROOT,
                                   capture_output=True, text=True, timeout=60)
        record = {"command": command, "environment": settings,
                  "returncode": result.returncode, "stderr": result.stderr,
                  "reference_command": reference_command,
                  "reference_returncode": reference.returncode,
                  "reference_stdout": reference.stdout, "reference_stderr": reference.stderr}
        (directory / "run.json").write_text(json.dumps(record, indent=2) + "\n")
        validate.check(state["specials"][5] == PSR and state["specials"][3] == RETS,
                       f"{name}: altered PSR/RETS")
        if condition == 0:
            branch_pc = ENTRY + (len(header(1, -1)) + 3 + 2) * 2
            validate.check(result.returncode != 0 and state["pc"] == branch_pc and
                           state["instructions"] == 7 and state["registers"][2] == 0x12223242 and
                           state["reason"] == "final THEN signed-literal branch with ELSE is unsupported",
                           f"{name}: unresolved combination did not fail before retirement")
            print(f"PASS {name}: explicit unresolved conditional form; reference discrepancy recorded")
            return
        validate.check(result.returncode == 0 and state["pc"] == stop and
                       state["instructions"] == count,
                       f"{name}: incorrect independent six-byte skip/retirement")
        if reference.returncode == 0:
            oracle = json.loads(reference.stdout)
            validate.check(all(state[key] == oracle[key] for key in
                               ("pc", "instructions", "registers", "specials")),
                           f"{name}: available reference result differs")
        else:
            displacement_pc = ENTRY + (len(header(1, -1)) + 3 + 2 + 2) * 2
            validate.check(f"Unsupported {{ pc: {displacement_pc}, word: 4 }}" in reference.stderr,
                           f"{name}: unexpected reference failure: {reference.stderr}")
        print(f"PASS {name}: independent six-byte skip; reference limitation recorded")
    expected_r2 = 0x4444 if has_else and condition else 0x12223242
    validate.check(state["registers"][2:4] == [expected_r2, 0x7777],
                   f"{name}: six-byte arm sizing/completion differs")


def unsupported_case(opcode):
    name = f"unsupported-{opcode:04x}"
    words = header(1, -1)
    pc = ENTRY + len(words) * 2
    image = save_image(name, [*words, opcode, 0x1FFF, 1])
    env = {k: v for k, v in os.environ.items() if not k.startswith("FM1_POC_")}
    env.update(FM1_POC_STOP_PC=hex(pc + 6), FM1_POC_MAX_INSTRUCTIONS="20")
    command = [*validate.COMMAND, "-kernel", str(image), "-append", "diag"]
    result = subprocess.run(command, env=env, cwd=validate.ROOT,
                            capture_output=True, text=True, timeout=15)
    validate.check(result.returncode != 0 and f"unsupported instruction 0x{opcode:04x}" in result.stderr
                   and f"PC 0x{pc:08x} after 5 instructions" in result.stderr,
                   f"{name}: unsupported neighbor or retirement changed: {result.stderr}")
    (CACHE / f"{name}.json").write_text(json.dumps({"command": command,
        "returncode": result.returncode, "stderr": result.stderr}, indent=2) + "\n")
    print(f"PASS {name}: explicit fault before retirement")


def main():
    CACHE.mkdir(parents=True, exist_ok=True)
    isa.CACHE = CACHE
    values = [(-1, value) for value in (-2147483648, -128, -2, -1, 0, 1, 2147483647)]
    values += [(-2048, value) for value in (-2049, -2048, -2047)]
    values += [(2047, value) for value in (2046, 2047, 2048)]
    values += [(0, value) for value in (-1, 0, 1)]
    for index, (immediate, value) in enumerate(values):
        branch_case(f"signed-value-{index}", 1, value, immediate, 7)
    for register in (0, 7, 8, 15):
        branch_case(f"register-{register}", register, 0, -1, 7)
    for displacement in (-32768, -1, 0, 1, 32767):
        branch_case(f"displacement-{displacement}", 1, 0, -1, displacement)
    branch_case("unsigned-ff08-preserved", 1, 4096, 4095, 7, opcode=0xFF08)
    for condition in (0, 1):
        for has_else in (False, True):
            conditional_case(condition, has_else)
    for opcode in (0xFF0A, 0xFF0B, 0xFF0D, 0xFF0E, 0xFF0F):
        unsupported_case(opcode)
    summary = {"passed": True, "reference_compared_cases": 27,
               "independent_sizing_cases": 2, "unsupported_neighbor_cases": 5,
               "unsupported_conditional_cases": 1,
               "qemu_sha256": hashlib.sha256(validate.QEMU.read_bytes()).hexdigest(),
               "instruction": "FF0C signed greater-than literal branch",
               "hardware_validation": False}
    (CACHE / "validation.json").write_text(json.dumps(summary, indent=2) + "\n")
    print("PASS FF0C: 27 oracle comparisons, two independent sizing checks and six explicit faults")


if __name__ == "__main__":
    main()
