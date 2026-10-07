#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Validate compact immediate signed-right shifts, mask E088/value A088.

Pinned Apache shiftrot.sinc:163-166 and vendor AF88/BF98 agree on signed32
right shift, destination bits0..2, source bits4..6 and count bits8..12. The
separate reference confirms every count and low-GPR pair, including count0
as unchanged input and count31 as sign replication. No PSR bits change.
Arithmetic-left, register-count arithmetic-right and parallel-tail support
remain deferred; logical shifts/classifier stay unchanged. This scalar form
has no data access. Negative snapshots cover existing fetch guard/deferred
form policy only, and do not establish hardware fault state or priority.
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
CACHE = HERE / ".cache/arithmetic-shift-validation"
ENTRY = 0x02000120
INSPECTION = 0x01C08000
PSR = 0x89ABCDE5
GPRS = [0x10203040 + i * 0x01010101 for i in range(16)]
GPRS[0] = 0x01C7FE08
MARKERS = [0x12345678, 0x89ABCDEF, 0x76543210]
INITIAL_INSPECTION = [0xA5A5A5A5] * 5 + [0] * 3 + [0xA5A5A5A5] * 4
CONTINUATION = 0x33445566


def encoding(dest, source, count):
    assert 0 <= dest < 8 and 0 <= source < 8 and 0 <= count <= 31
    return 0xA088 | (count << 8) | (source << 4) | dest


def signed_shift(value, count):
    value &= 0xFFFFFFFF
    signed = value if value < 0x80000000 else value - 0x100000000
    return (signed >> count) & 0xFFFFFFFF


def setup(registers, psr=PSR, guard=False):
    guest = Guest()
    for index, marker in enumerate(MARKERS):
        guest.write(INSPECTION + index * 4, marker)
    if guard:
        guest.write(0x01EEE240, 0xE7)
        guest.write(0x01EEE380, 0xFFFFFFFF)  # PC window high, initially inclusive.
        guest.write(0x01EEE384, ENTRY)       # PC window low.
    guest.literal(4, psr)
    guest.emit(0xE064, 0x4580)
    expected = list(GPRS)
    for register, initial in registers.items():
        expected[register] = initial & 0xFFFFFFFF
    for register, initial in enumerate(expected):
        guest.literal(register, initial)
    if guard:
        # Inclusive window ends after these three setup instructions and
        # before the two-byte shift, which is denied at instruction fetch.
        expected[0] = guest.pc + 14 - 1
        expected[1] = 0x01EEE380
        guest.literal(0, expected[0])
        guest.literal(1, expected[1])
        guest.store(0, 1)
    return guest, expected


def specials(psr):
    expected = [0] * 16
    expected[5] = psr
    return expected


def save_image(name, guest):
    image = CACHE / f"{name}.bin"
    image.write_bytes(guest.bytes() + bytes(16))
    return image


def success_case(name, dest=0, source=0, count=15, value=0xF1234567,
                 psr=PSR, condition=None):
    incoming = {source: value}
    if condition is not None:
        incoming[0] = condition
    guest, expected = setup(incoming, psr)
    old_source = expected[source]
    if condition is not None:
        guest.emit(0xEA20, 1)  # One THEN instruction, selected if r0 & 1 == 0.
    guest.emit(encoding(dest, source, count))
    guest.emit(0x0000)
    image = save_image(name, guest)
    state = isa.compare(name, image, guest.pc)
    selected = condition is None or condition == 0
    if selected:
        expected[dest] = signed_shift(old_source, count)
    inspection = list(INITIAL_INSPECTION)
    inspection[:3] = MARKERS
    retired = guest.instructions - (0 if selected else 1)
    validate.check(state["pc"] == guest.pc and state["instructions"] == retired,
                   f"{name}: two-byte shift/continuation retirement differs")
    validate.check(state["registers"] == expected and state["specials"] == specials(psr),
                   f"{name}: signed shift, count, source/destination alias, fields or PSR differs")
    validate.check(state["inspection"] == inspection,
                   f"{name}: scalar shift changed memory or neighboring words")
    return image, state


def fault_case(name, opcode, reason, tail=(), guard=False):
    guest, expected = setup({0: 0xF1234567, 1: 0x89ABCDEF, 2: 3}, guard=guard)
    fault_pc = guest.pc
    before = guest.instructions
    span = (1 + len(tail)) * 2
    guest.emit(opcode, *tail)
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
    validate.check(state["last_access"] == {"address": fault_pc, "size": span, "flags": 2},
                   f"{name}: scalar/deferred-bundle fetch span differs")
    validate.check(state["registers"] == expected and state["specials"] == specials(PSR),
                   f"{name}: rejected form changed source, destination, continuation or PSR")
    memory = (directory / "state.sram").read_bytes()
    validate.check(len(memory) == 0x80000 and
                   memory[0x8000:0x800C] == struct.pack("<III", *MARKERS),
                   f"{name}: rejected form altered neighboring memory")
    if guard:
        validate.check(state["guards"]["debug_message"] == 1 << 12,
                       f"{name}: established PC guard fault status differs")
    (directory / "run.json").write_text(json.dumps(record, indent=2) + "\n")
    print(f"PASS {name}: fault before destination update/retirement; model state checked")


def generic_replay(image, expected):
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
                   memory[0x8000:0x800C] == struct.pack("<III", *MARKERS),
                   "generic-replay: signed shift altered neighboring memory")
    (directory / "run.json").write_text(json.dumps({"command": command,
        "environment": settings, "returncode": result.returncode,
        "stderr": result.stderr, "state": state}, indent=2) + "\n")
    print("PASS generic-replay: default application mode matches arithmetic shift architecture")


def main():
    CACHE.mkdir(parents=True, exist_ok=True)
    isa.CACHE = CACHE
    cases = []
    for count in range(32):
        cases.append(dict(name=f"count-{count}", dest=7, source=6,
                          count=count, value=0x81234567))
    for dest in range(8):
        for source in range(8):
            cases.append(dict(name=f"destination-{dest}-source-{source}", dest=dest,
                              source=source, count=15, value=0x81234567))
    for value in (0, 1, 0x7FFFFFFF, 0x80000000, 0x80000001, 0xFFFFFFFF, 0xAAAAAAAA, 0x55555555):
        for count in (0, 1, 31):
            cases.append(dict(name=f"sign-{value:08x}-count-{count}", dest=3,
                              source=3, count=count, value=value))
    cases.extend([
        dict(name="reached-negative"),
        dict(name="nearby-distinct-source-count31", dest=0, source=1,
             count=31, value=0x80000001),
    ])
    for psr in (0, 0xFFFFFFFF):
        cases.append(dict(name=f"psr-{psr:08x}", psr=psr))
    for condition in (0, 1):
        cases.append(dict(name=f"conditional-{condition}", dest=7, source=3, condition=condition))
    replay = None
    for case in cases:
        result = success_case(**case)
        if case["name"] == "reached-negative":
            replay = result
    generic_replay(*replay)
    for count in (0, 31):
        opcode = 0xA008 | (count << 8) | (2 << 4) | 1
        fault_case(f"deferred-arithmetic-left-{count}", opcode,
                   f"unsupported instruction 0x{opcode:04x}")
    fault_case("deferred-register-arithmetic-right", 0x1A89,
               "unsupported instruction 0x1a89")
    # Prefix6 encodes a parallel group0 NOP head; the scalar signed-right
    # tail remains unclassified by the unchanged parallel destination mask.
    fault_case("deferred-arithmetic-right-tail", 0xC000,
               "unsupported instruction 0xc000", tail=(0xAF88,))
    fault_case("pc-guard-rejects-shift-fetch", 0xAF88,
               "guest PC lies outside both configured guard windows", guard=True)
    summary = {"passed": True, "instruction": "compact immediate signed-right shift E088/A088",
               "positive_reference_cases": len(cases), "generic_replays": 1, "fault_cases": 5,
               "counts": "0..31, including unchanged input at0 and sign replication at31",
               "fields": "all8x8 low-GPR source/destination pairs",
               "primary_blob": "4f3786ff1d7603838cc0f5d1f03189a617ebb597",
               "unsupported_forms": "arithmetic-left immediate, register arithmetic-right, parallel tail",
               "hardware_fault_state_validation": False,
               "fault_limits": "no data access; existing instruction fetch/deferred form policy only"}
    (CACHE / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print("PASS arithmetic shift milestone: signed result, fields, aliases, sizing and generic replay")


if __name__ == "__main__":
    main()
