#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Validate existing compact 1B00 register multiplication in parallel bundles.

Pinned Apache SLEIGH and vendor DB01 + 3083 establish r1*=r0 with a parallel
word store of incoming r3 at SP+64. Only destination classification is proposed;
scalar multiplication, incoming-register capture, tail ordering and instruction
sizes stay unchanged. Independent nonzero products and store/address values
exercise behavior hidden by the actual checkpoint's zero operands. Fault-state
expectations preserve existing model policy; hardware faults are unverified.
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
CACHE = HERE / ".cache/parallel-register-multiply-validation"
ENTRY = 0x02000120
INSPECTION = 0x01C08000
STACK = INSPECTION - 64
PSR = 0x89ABCDE5
GPRS = [0x10203040 + i * 0x01010101 for i in range(16)]
GPRS[0] = 0x01C7FE08
MARKERS = [0x12345678, 0x89ABCDEF, 0x76543210, 0x0BADF00D]
INITIAL_INSPECTION = [0xA5A5A5A5] * 5 + [0] * 3 + [0xA5A5A5A5] * 4


def setup(registers, psr=PSR, stack=STACK, seed=False, guard=False):
    guest = Guest()
    if seed:
        for index, value in enumerate(MARKERS):
            guest.write(INSPECTION + index * 4, value)
    if guard:
        # The tail word starts below but intersects a one-byte write window.
        guest.write(0x01EEE240, 0xE7)
        guest.write(0x01EEE2C0, INSPECTION + 2)
        guest.write(0x01EEE280, INSPECTION + 2)
        guest.write(0x01EEE348, 1)
    guest.literal(4, psr)
    guest.emit(0xE064, 0x4580)
    guest.literal(14, stack, special=True)
    expected = list(GPRS)
    for register, value in registers.items():
        expected[register] = value & 0xFFFFFFFF
    for register, value in enumerate(expected):
        guest.literal(register, value)
    return guest, expected


def specials(psr, stack=STACK):
    expected = [0] * 16
    expected[5] = psr
    expected[14] = stack
    return expected


def save_image(name, guest):
    image = CACHE / f"{name}.bin"
    image.write_bytes(guest.bytes() + bytes(16))
    return image


def success_case(name, dest=1, source=0, left=0x81234567, right=3,
                 tail=(0x3083,), registers=None, updates=None, writes=(),
                 psr=PSR, condition=None):
    assert dest != source or left == right
    incoming = dict(registers or {})
    incoming[dest] = left
    incoming[source] = right
    if condition is not None:
        incoming[0] = condition
    guest, expected = setup(incoming, psr)
    old_left, old_right = expected[dest], expected[source]
    if condition is not None:
        guest.emit(0xEA20, 1)  # One THEN instruction, selected if r0 & 1 == 0.
    guest.emit(0xDB00 | (source << 4) | dest, *tail)
    guest.emit(0x0000)
    image = save_image(name, guest)
    state = isa.compare(name, image, guest.pc)
    selected = condition is None or condition == 0
    inspection = list(INITIAL_INSPECTION)
    if selected:
        for register, new_value in (updates or {}).items():
            expected[register] = new_value & 0xFFFFFFFF
        expected[dest] = (old_left * old_right) & 0xFFFFFFFF
        for address, stored_value in writes:
            inspection[(address - INSPECTION) // 4] = stored_value & 0xFFFFFFFF
    retired = guest.instructions - (0 if selected else 1)
    validate.check(state["pc"] == guest.pc and state["instructions"] == retired,
                   f"{name}: four/six-byte sizing or one-bundle retirement differs")
    validate.check(state["registers"] == expected and state["specials"] == specials(psr),
                   f"{name}: incoming operands, low-word product, disjoint tail or PSR differs")
    validate.check(state["inspection"] == inspection,
                   f"{name}: incoming store source/address or neighboring memory differs")
    return image, state, writes


def fault_case(name, tail, reason, stack=STACK, address=None, flags=1, guard=False):
    guest, expected = setup({0: 3, 1: 0x81234567, 3: 0x89ABCDEF},
                            stack=stack, seed=True, guard=guard)
    fault_pc = guest.pc
    before = guest.instructions
    span = (1 + len(tail)) * 2
    guest.emit(0xDB01, *tail)
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
    validate.check(result.returncode != 0 and state["reason"] == reason and
                   state["pc"] == fault_pc and state["instructions"] == before,
                   f"{name}: bundle fault reason, PC or retirement differs: {result.stderr}")
    access = ({"address": address, "size": 4, "flags": flags} if address is not None
              else {"address": fault_pc, "size": span, "flags": 2})
    validate.check(state["last_access"] == access,
                   f"{name}: precheck or tail access address/width differs")
    validate.check(state["registers"] == expected and state["specials"] == specials(PSR, stack),
                   f"{name}: fault applied multiply, tail register, continuation or PSR effects")
    memory = (directory / "state.sram").read_bytes()
    validate.check(len(memory) == 0x80000 and
                   memory[0x8000:0x8010] == struct.pack("<IIII", *MARKERS),
                   f"{name}: fault altered destination or neighboring memory")
    record = {"command": command, "environment": settings,
              "fixture_sha256": hashlib.sha256(image.read_bytes()).hexdigest(),
              "returncode": result.returncode, "stderr": result.stderr, "state": state,
              "hardware_fault_state_validation": False}
    (directory / "run.json").write_text(json.dumps(record, indent=2) + "\n")
    print(f"PASS {name}: explicit bundle fault before multiply effects and retirement")


def generic_replay(image, expected, writes):
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
    expected_words = [0] * 5
    for address, value in writes:
        expected_words[(address - INSPECTION) // 4] = value
    validate.check(struct.unpack_from("<IIIII", memory, 0x8000) == tuple(expected_words),
                   "generic-replay: incoming store value/address or neighboring words differ")
    (directory / "run.json").write_text(json.dumps({"command": command,
        "environment": settings, "returncode": result.returncode,
        "stderr": result.stderr, "state": state}, indent=2) + "\n")
    print("PASS generic-replay: default application mode matches bundle architecture and memory")


def main():
    CACHE.mkdir(parents=True, exist_ok=True)
    isa.CACHE = CACHE
    cases = [
        dict(name="reached-nonzero", registers={3: 0x89ABCDEF},
             writes=((INSPECTION, 0x89ABCDEF),)),
        dict(name="incoming-destination-store", tail=(0x3081,),
             writes=((INSPECTION, 0x81234567),)),
        dict(name="incoming-source-store", tail=(0x3080,), writes=((INSPECTION, 3),)),
        dict(name="incoming-address-store", left=INSPECTION + 4, tail=(0x6092,),
             registers={2: 0x87654321}, writes=((INSPECTION + 4, 0x87654321),)),
        dict(name="four-byte-overwritten-source", tail=(0x3000,), updates={0: 0xA5A5A5A5}),
        dict(name="six-byte-overwritten-source", tail=(0xE040, 7), updates={0: 7}),
        dict(name="six-byte-high-source-overwritten", dest=8, source=15,
             left=0x80000001, tail=(0xE04F, 0x1234), updates={15: 0x1234}),
        dict(name="four-byte-high-source-overwritten", dest=8, source=15,
             left=0x80000001, tail=(0x160F,), updates={15: GPRS[0]}),
    ]
    for register in range(16):
        cases.append(dict(name=f"destination-{register}-source-{(register + 9) % 16}",
                          dest=register, source=(register + 9) % 16,
                          left=0x81234567, right=0x7FFFFFFF, tail=(0x0000,)))
    for register in (0, 7, 8, 15):
        cases.append(dict(name=f"source-destination-square-{register}", dest=register,
                          source=register, left=0xFFFFFFFF, right=0xFFFFFFFF, tail=(0x0000,)))
    for left, right in ((0, 0xFFFFFFFF), (1, 0xFFFFFFFF), (0x7FFFFFFF, 3),
                        (0x80000000, 3), (0xFFFFFFFF, 3), (0x80000001, 2),
                        (0x12345678, 0x87654321)):
        cases.append(dict(name=f"product-{left:08x}-{right:08x}", left=left,
                          right=right, tail=(0x0000,)))
    for psr in (0, 0xFFFFFFFF):
        cases.append(dict(name=f"psr-{psr:08x}", psr=psr, registers={3: 0x89ABCDEF},
                          writes=((INSPECTION, 0x89ABCDEF),)))
    for width in (4, 6):
        for condition in (0, 1):
            cases.append(dict(name=f"conditional-{width}-{condition}", dest=2, source=3,
                              condition=condition, tail=(0x3083,) if width == 4 else (0xE043, 7),
                              writes=((INSPECTION, 3),) if width == 4 else (),
                              updates={} if width == 4 else {3: 7}))
    replay = None
    for case in cases:
        result = success_case(**case)
        if case["name"] == "reached-nonzero":
            replay = result
    generic_replay(*replay)
    unsupported = "unsupported instruction 0xdb01"
    fault_case("overlapping-literal-destination", (0xE041, 0x1234), unsupported)
    fault_case("overlapping-load-destination", (0x3001,), unsupported)
    fault_case("deferred-tail", (0xEED1, 0x1234), unsupported)
    fault_case("unaligned-tail-store", (0x3083,), "unaligned access",
               stack=STACK + 1, address=INSPECTION + 1)
    fault_case("unmapped-tail-store", (0x3083,), "unmapped access at 0x18000000",
               stack=0x18000000 - 64, address=0x18000000)
    fault_case("unmapped-tail-load", (0x3003,), "unmapped access at 0x18000000",
               stack=0x18000000 - 64, address=0x18000000, flags=0)
    fault_case("read-only-tail-store", (0x3083,), "write to read-only XIP (NOR)",
               stack=ENTRY - 64, address=ENTRY)
    fault_case("guarded-tail-store", (0x3083,),
               "CPU write intersects an enabled guest guard window", address=INSPECTION, guard=True)
    summary = {"passed": True, "instruction": "compact 1B00 register multiply in parallel bundles",
               "reference_compared_cases": len(cases), "generic_replay_cases": 1,
               "precheck_fault_cases": 3, "tail_access_fault_cases": 5,
               "qemu_sha256": hashlib.sha256(validate.QEMU.read_bytes()).hexdigest(),
               "hardware_validation": False, "hardware_fault_state_validation": False}
    (CACHE / "validation.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(f"PASS DB01: {len(cases)} reference comparisons, generic replay and eight explicit faults")


if __name__ == "__main__":
    main()
