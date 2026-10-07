#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Validate existing E0E0 packed-immediate add semantics in parallel bundles.

Vendor F0E0/BC00 + 3580 adds 0x8000 to incoming r11 into r0 while storing
incoming r0 at SP+84. Only destination classification changes; scalar decoding,
packed_mask and tail-first/incoming-register bundle execution stay unchanged.
Pinned Apache arithops.sinc:119-122 states the result and register fields. It
does not establish flags: low-four-bit add flags and upper-PSR preservation
are existing scalar model policy, independently checked by executable oracle.
A disjoint ALU tail uses different flags to check the existing head-last order.
Literal constants here match primary encoding facts and independent probes;
inherited disputed repeated-byte modes remain outside this milestone.
Fault expectations preserve the existing model and do not prove hardware state.
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
CACHE = HERE / ".cache/parallel-packed-add-validation"
ENTRY = 0x02000120
INSPECTION = 0x01C08000
STACK = INSPECTION - 64
PSR = 0x89ABCDE5
GPRS = [0x10203040 + i * 0x01010101 for i in range(16)]
GPRS[0] = 0x01C7FE08
MARKERS = [0x12345678, 0x89ABCDEF, 0x76543210, 0x0BADF00D, 0x55667788, 0xCAFEBABE]
INITIAL_INSPECTION = [0xA5A5A5A5] * 5 + [0] * 3 + [0xA5A5A5A5] * 4


def setup(registers, psr=PSR, stack=STACK, seed=False, guard=False):
    guest = Guest()
    if seed:
        for index, value in enumerate(MARKERS):
            guest.write(INSPECTION + index * 4, value)
    if guard:
        guest.write(0x01EEE240, 0xE7)
        guest.write(0x01EEE2C0, INSPECTION + 20)
        guest.write(0x01EEE280, INSPECTION + 20)
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


def signed(value):
    return value if value < 0x80000000 else value - 0x100000000


def add_result(value, addend, psr):
    total = value + addend
    result = total & 0xFFFFFFFF
    signed_total = signed(value) + signed(addend)
    overflow = not -0x80000000 <= signed_total <= 0x7FFFFFFF
    flags = (overflow | ((total > 0xFFFFFFFF) << 1) |
             ((result == 0) << 2) | ((result >> 31) << 3))
    return result, (psr & 0xFFFFFFF0) | flags


def success_case(name, dest=0, source=11, value=0x12345678, encoded=0xC00, addend=0x8000,
                 tail=(0x3580,), registers=None, updates=None, writes=(),
                 psr=PSR, condition=None):
    incoming = dict(registers or {})
    incoming[source] = value
    if condition is not None:
        incoming[0] = condition
    guest, expected = setup(incoming, psr)
    old_source = expected[source]
    if condition is not None:
        guest.emit(0xEA20, 1)  # One THEN instruction, selected if r0 & 1 == 0.
    guest.emit(0xF0E0 | dest, (source << 12) | encoded, *tail)
    guest.emit(0x0000)
    image = save_image(name, guest)
    state = isa.compare(name, image, guest.pc)
    selected = condition is None or condition == 0
    inspection = list(INITIAL_INSPECTION)
    final_psr = psr
    if selected:
        for register, new_value in (updates or {}).items():
            expected[register] = new_value & 0xFFFFFFFF
        expected[dest], final_psr = add_result(old_source, addend, psr)
        for address, stored_value in writes:
            inspection[(address - INSPECTION) // 4] = stored_value & 0xFFFFFFFF
    retired = guest.instructions - (0 if selected else 1)
    validate.check(state["pc"] == guest.pc and state["instructions"] == retired,
                   f"{name}: six/eight-byte sizing or one-bundle retirement differs")
    validate.check(state["registers"] == expected and state["specials"] == specials(final_psr),
                   f"{name}: incoming source, modulo32 sum, tail or add flags/upper PSR differs")
    validate.check(state["inspection"] == inspection,
                   f"{name}: incoming store source/address or neighboring memory differs")
    return image, state, writes


def fault_case(name, tail, reason, stack=STACK, address=None, flags=1, guard=False, dest=0, source=11):
    guest, expected = setup({0: 0x89ABCDEF, source: 0x7FFFFFFF}, stack=stack, seed=True, guard=guard)
    fault_pc = guest.pc
    before = guest.instructions
    span = (2 + len(tail)) * 2
    guest.emit(0xF0E0 | dest, (source << 12) | 0xC00, *tail)
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
                   f"{name}: fault applied add, tail register, continuation or PSR effects")
    sram = (directory / "state.sram").read_bytes()
    validate.check(len(sram) == 0x80000 and
                   sram[0x8000:0x8018] == struct.pack("<IIIIII", *MARKERS),
                   f"{name}: fault altered destination or neighboring memory")
    record = {"command": command, "environment": settings,
              "fixture_sha256": hashlib.sha256(image.read_bytes()).hexdigest(),
              "returncode": result.returncode, "stderr": result.stderr, "state": state,
              "hardware_fault_state_validation": False}
    (directory / "run.json").write_text(json.dumps(record, indent=2) + "\n")
    print(f"PASS {name}: explicit bundle fault before add effects and retirement")


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
    expected_words = [0] * 6
    for address, value in writes:
        expected_words[(address - INSPECTION) // 4] = value
    validate.check(struct.unpack_from("<IIIIII", memory, 0x8000) == tuple(expected_words),
                   "generic-replay: incoming store value/address or neighboring words differ")
    (directory / "run.json").write_text(json.dumps({"command": command,
        "environment": settings, "returncode": result.returncode,
        "stderr": result.stderr, "state": state}, indent=2) + "\n")
    print("PASS generic-replay: default application mode matches bundle architecture and memory")


def main():
    CACHE.mkdir(parents=True, exist_ok=True)
    isa.CACHE = CACHE
    cases = [
        dict(name="reached-nonzero", registers={0: 0x89ABCDEF},
             writes=((INSPECTION + 20, 0x89ABCDEF),)),
        dict(name="incoming-source-store", source=3, value=0x89ABCDEF,
             tail=(0x3583,), writes=((INSPECTION + 20, 0x89ABCDEF),)),
        dict(name="incoming-address", registers={0: INSPECTION + 4, 2: 0x87654321},
             tail=(0x6082,), writes=((INSPECTION + 4, 0x87654321),)),
        dict(name="six-byte-loaded-source", source=1, value=0x7FFFFFFF,
             encoded=1, addend=1, tail=(0x3501,), updates={1: 0}),
        dict(name="eight-byte-overwritten-source", tail=(0xE04B, 7), updates={11: 7}),
        dict(name="high-destination-14", dest=14, source=15, value=0xFFFFFFFF,
             encoded=1, addend=1, tail=(0xE04F, 0x1234), updates={15: 0x1234}),
        dict(name="high-destination-15", dest=15, source=14, value=0x80000000,
             encoded=0x400, addend=0x80000000, tail=(0xE04E, 0x5678), updates={14: 0x5678}),
        dict(name="six-byte-high-source-overwritten", dest=8, source=15,
             tail=(0x160F,), updates={15: GPRS[0]}),
        # Tail r1 += incoming r0 produces zero/carry (6); head produces sign/
        # overflow (9). These differ, discriminating existing head-last flags.
        dict(name="disjoint-alu-tail-distinct-flags", source=1, value=0x7FFFFFFF,
             encoded=1, addend=1, registers={0: 0x80000001},
             tail=(0x1801,), updates={1: 0}),
    ]
    for register in range(16):
        cases.append(dict(name=f"source-destination-alias-{register}", dest=register,
                          source=register, value=0xFFFF8000,
                          tail=(0x3580 | register,) if register < 8 else (0x0000,),
                          writes=((INSPECTION + 20, 0xFFFF8000),) if register < 8 else ()))
    for dest in range(16):
        cases.append(dict(name=f"destination-{dest}", dest=dest, source=(dest + 1) % 16,
                          value=0x7FFFFFFF, encoded=1, addend=1, tail=(0x0000,)))
    literals = [(0, 0), (1, 1), (255, 255), (0x300, 0), (0x301, 0x01010101),
                (0x3FF, 0xFFFFFFFF), (0x400, 0x80000000), (0x47F, 0xFF000000),
                (0x800, 0x00800000), (0xC00, 0x8000), (0xC7F, 0xFF00),
                (0xC80, 0x4000), (0xEB3, 0x598)]
    for encoded, addend in literals:
        cases.append(dict(name=f"literal-{encoded:03x}", encoded=encoded,
                          addend=addend, tail=(0x0000,)))
    boundaries = [(0, 0, 0), (1, 0x3FF, 0xFFFFFFFF), (0x7FFFFFFF, 1, 1),
                  (0x80000000, 0x400, 0x80000000), (0xFFFFFFFF, 1, 1),
                  (0x80000000, 0x3FF, 0xFFFFFFFF), (0x7FFF8000, 0xC00, 0x8000),
                  (0xFFFF8000, 0xC00, 0x8000), (0xFFFFFFFF, 0xC00, 0x8000),
                  (0x80000000, 0xC00, 0x8000), (0, 0xC00, 0x8000)]
    for index, (value, encoded, addend) in enumerate(boundaries):
        cases.append(dict(name=f"flags-boundary-{index}", value=value, encoded=encoded,
                          addend=addend, tail=(0x0000,)))
    for psr in (0, 0xFFFFFFFF):
        cases.append(dict(name=f"psr-{psr:08x}", psr=psr, value=0x80000000,
                          encoded=0x400, addend=0x80000000, tail=(0x0000,)))
    for width in (6, 8):
        for condition in (0, 1):
            cases.append(dict(name=f"conditional-{width}-{condition}", dest=2, condition=condition,
                              tail=(0x3580,) if width == 6 else (0xE04B, 7),
                              writes=((INSPECTION + 20, condition),) if width == 6 else (),
                              updates={} if width == 6 else {11: 7}))
    replay = None
    for case in cases:
        result = success_case(**case)
        if case["name"] == "reached-nonzero":
            replay = result
    generic_replay(*replay)
    unsupported = "unsupported instruction 0xf0e0"
    fault_case("overlapping-literal-destination", (0xE040, 0x1234), unsupported)
    fault_case("overlapping-load-destination", (0x3500,), unsupported)
    fault_case("overlapping-high-destination", (0xE04F, 0x1234),
               "unsupported instruction 0xf0ef", dest=15, source=14)
    fault_case("deferred-tail", (0xEED1, 0x1234), unsupported)
    fault_case("unaligned-tail-store", (0x3580,), "unaligned access",
               stack=STACK + 1, address=INSPECTION + 21)
    fault_case("unmapped-tail-store", (0x3580,), "unmapped access at 0x18000000",
               stack=0x18000000 - 84, address=0x18000000)
    fault_case("unmapped-tail-load", (0x3503,), "unmapped access at 0x18000000",
               stack=0x18000000 - 84, address=0x18000000, flags=0)
    fault_case("read-only-tail-store", (0x3580,), "write to read-only XIP (NOR)",
               stack=ENTRY - 84, address=ENTRY)
    fault_case("guarded-tail-store", (0x3580,),
               "CPU write intersects an enabled guest guard window", address=INSPECTION + 20, guard=True)
    summary = {"passed": True, "instruction": "E0E0 packed-immediate add in parallel bundles",
               "reference_compared_cases": len(cases), "generic_replay_cases": 1,
               "precheck_fault_cases": 4, "tail_access_fault_cases": 5,
               "qemu_sha256": hashlib.sha256(validate.QEMU.read_bytes()).hexdigest(),
               "primary_arithmetic_blob": "19b640bc036b14df78ee32ac45595d759b502317",
               "flag_authority": "existing scalar model policy plus separate executable oracle; primary states result only",
               "hardware_validation": False, "hardware_fault_state_validation": False,
               "inherited_packed_literal_discrepancy": "disputed repeated-byte modes are outside this milestone"}
    (CACHE / "validation.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(f"PASS F0E0: {len(cases)} reference comparisons, generic replay and nine explicit faults")


if __name__ == "__main__":
    main()
