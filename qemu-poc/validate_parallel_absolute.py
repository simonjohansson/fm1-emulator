#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Validate exact E430 scalar ABS and canonical F430 parallel bundles.

Pinned Apache arithops.sinc:346-352 and vendor F430/1500 + 6100 at
0x02003992 establish r1=abs(incoming r5) paired with r0=[incoming r0+4].
The four-byte scalar uses destination bits12:15, source bits8:11 and a zero
low byte. Wrapping two's-complement ABS retains INT_MIN; PSR/RETS preservation
and incoming aliases are independently checked by executable reference.
An already-supported flag-writing tail changes PSR; ABS preserves those flags.
Classification validates the head before any tail effects. Existing bundle
capture, tail ordering, sizing, helpers and neighboring classifiers stay fixed.
Noncanonical operands were rejected by the independent executable, which did
not expose fault-state snapshots. Fault ordering here checks existing QEMU
policy only; hardware fault state and reserved-bit behavior remain unverified.
The signed-minimum gate's F435 deferred-parallel negative remains unchanged.
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
CACHE = HERE / ".cache/parallel-absolute-validation"
ENTRY = 0x02000120
INSPECTION = 0x01C08000
STACK = INSPECTION - 16
PSR = 0x89ABCDE5
RETS = 0x12345678
GPRS = [0x10203040 + i * 0x01010101 for i in range(16)]
GPRS[0] = 0x01C7FE08
MARKERS = [0x12345678, 0x89ABCDEF, 0x76543210, 0x0BADF00D]
INSPECTION_WORDS = MARKERS + [0xA5A5A5A5] + [0] * 3 + [0xA5A5A5A5] * 4


def absolute(value):
    return (-value if value & 0x80000000 else value) & 0xFFFFFFFF


def setup(registers, psr=PSR, stack=STACK, write_guard=False, pc_guard=False):
    guest = Guest()
    for index, value in enumerate(MARKERS):
        guest.write(INSPECTION + index * 4, value)
    if write_guard:
        # The tail word starts below but intersects this one-byte write window.
        guest.write(0x01EEE240, 0xE7)
        guest.write(0x01EEE2C0, INSPECTION + 2)
        guest.write(0x01EEE280, INSPECTION + 2)
        guest.write(0x01EEE348, 1)
    if pc_guard:
        guest.write(0x01EEE240, 0xE7)
        header = guest.pc + 28 + 122  # Two14-byte writes and full-state initializers.
        guest.write(0x01EEE380, header - 1)
        guest.write(0x01EEE384, ENTRY)
    guest.literal(4, psr)
    guest.emit(0xE064, 0x4580)
    guest.literal(4, RETS)
    guest.emit(0xE064, 0x4380)
    guest.literal(14, stack, special=True)
    expected = list(GPRS)
    for register, value in registers.items():
        expected[register] = value & 0xFFFFFFFF
    for register, value in enumerate(expected):
        guest.literal(register, value)
    return guest, expected


def specials(psr=PSR, stack=STACK):
    expected = [0] * 16
    expected[3], expected[5], expected[14] = RETS, psr, stack
    return expected


def save_image(name, guest):
    image = CACHE / f"{name}.bin"
    image.write_bytes(guest.bytes() + bytes(16))
    return image


def check_success(name, state, expected, inspection, pc, retired, psr):
    validate.check(state["pc"] == pc and state["instructions"] == retired,
                   f"{name}: four/six/eight-byte sizing or single retirement differs")
    validate.check(state["registers"] == expected and state["specials"] == specials(psr),
                   f"{name}: wrapping ABS, incoming operands, tail effects, PSR or RETS differ")
    validate.check(state["inspection"] == inspection,
                   f"{name}: incoming store source/address or neighboring memory differs")


def success_case(name, dest=1, source=5, value=0x81234567, tail=(0x6100,),
                 registers=None, updates=None, writes=(), psr=PSR, condition=None,
                 final_psr=None):
    incoming = {0: INSPECTION - 4, **(registers or {})}
    incoming[source] = value
    if condition is not None:
        incoming[4] = condition
    guest, expected = setup(incoming, psr)
    old_source = expected[source]
    if condition is not None:
        guest.emit(0xEA24, 1)  # One THEN instruction, selected if r4 & 1 == 0.
    opcode = 0xF430 if tail else 0xE430
    word = (dest << 12) | (source << 8)
    guest.emit(opcode, word, *tail)
    guest.emit(0)
    selected = condition is None or condition == 0
    inspection = list(INSPECTION_WORDS)
    expected_psr = psr
    if selected:
        for register, new_value in (updates or {}).items():
            expected[register] = new_value & 0xFFFFFFFF
        expected[dest] = absolute(old_source)
        for address, stored_value in writes:
            inspection[(address - INSPECTION) // 4] = stored_value & 0xFFFFFFFF
        if final_psr is not None:
            expected_psr = final_psr
    image = save_image(name, guest)
    state = isa.compare(name, image, guest.pc, limit=100)
    check_success(name, state, expected, inspection, guest.pc,
                  guest.instructions - (not selected), expected_psr)
    (CACHE / f"{name}-evidence.json").write_text(json.dumps({"opcode": opcode,
        "second_word": word, "incoming_source": old_source, "selected": selected,
        "tail": tail, "initial_psr": psr, "final_psr": expected_psr}, indent=2) + "\n")
    return image, state, writes


def actual_sequence():
    name = "actual-bundle-and-following-scalar"
    guest, expected = setup({0: INSPECTION - 4, 2: 0x80000001, 5: 0x81234567})
    guest.emit(0xF430, 0x1500, 0x6100)
    guest.emit(0xE430, 0x3200)
    guest.emit(0)
    expected[0], expected[1], expected[3] = MARKERS[0], absolute(0x81234567), absolute(0x80000001)
    image = save_image(name, guest)
    state = isa.compare(name, image, guest.pc, limit=100)
    check_success(name, state, expected, INSPECTION_WORDS, guest.pc, guest.instructions, PSR)
    return image, state, ()


def absolute_tail_case():
    # The checked classifier also admits E430 as an extended tail. Existing
    # packed-add r0=oldr0+1 writes flags6; the disjoint tail reads oldr0 first.
    name = "absolute-extended-tail-incoming-source"
    guest, expected = setup({0: 0xFFFFFFFF})
    guest.emit(0xF0E0, 0x0001, 0xE430, 0x1000)
    guest.emit(0)
    expected[0], expected[1] = 0, 1
    image = save_image(name, guest)
    state = isa.compare(name, image, guest.pc, limit=100)
    check_success(name, state, expected, INSPECTION_WORDS, guest.pc,
                  guest.instructions, (PSR & 0xFFFFFFF0) | 6)


def fault_case(name, tail=(), low=0, reason=None, stack=STACK, address=None, flags=1,
               write_guard=False, pc_guard=False, registers=None):
    incoming = {0: INSPECTION - 4, 1: 0x87654321, 5: 0x81234567, **(registers or {})}
    guest, expected = setup(incoming, stack=stack, write_guard=write_guard, pc_guard=pc_guard)
    fault_pc, before = guest.pc, guest.instructions
    opcode = 0xF430 if tail else 0xE430
    span = (2 + len(tail)) * 2
    guest.emit(opcode, 0x1500 | low, *tail)
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
    expected_reason = reason or f"unsupported instruction 0x{opcode:04x}"
    validate.check(result.returncode != 0 and state["reason"] == expected_reason and
                   state["pc"] == fault_pc and state["instructions"] == before,
                   f"{name}: fault reason, PC or retirement differs: {result.stderr}")
    access = ({"address": address, "size": 4, "flags": flags} if address is not None
              else {"address": fault_pc, "size": span, "flags": 2})
    validate.check(state["last_access"] == access, f"{name}: precheck or tail access span differs")
    validate.check(state["registers"] == expected and state["specials"] == specials(PSR, stack),
                   f"{name}: fault applied ABS, tail register, continuation, PSR or RETS effects")
    memory = (directory / "state.sram").read_bytes()
    validate.check(len(memory) == 0x80000 and
                   memory[0x8000:0x8010] == struct.pack("<IIII", *MARKERS),
                   f"{name}: fault altered destination or neighboring memory")
    if pc_guard:
        validate.check(state["guards"]["debug_message"] & (1 << 12), f"{name}: PC guard not latched")
    record = {"command": command, "environment": settings, "returncode": result.returncode,
              "stderr": result.stderr, "state": state, "operand_low_byte": low,
              "fixture_sha256": hashlib.sha256(image.read_bytes()).hexdigest(),
              "hardware_fault_state_validation": False}
    (directory / "run.json").write_text(json.dumps(record, indent=2) + "\n")
    print(f"PASS {name}: model fault before ABS/tail effects and retirement")


def generic_replay(image, expected, writes):
    directory = CACHE / "generic-replay"
    directory.mkdir(exist_ok=True)
    for filename in ("state.json", "state.sram", "state.alnk"):
        (directory / filename).unlink(missing_ok=True)
    env = {key: value for key, value in os.environ.items() if not key.startswith("FM1_POC_")}
    settings = {"FM1_POC_STOP_PC": hex(expected["pc"]), "FM1_POC_MAX_INSTRUCTIONS": "100",
                "FM1_POC_STATE_DIR": str(directory)}
    env.update(settings)
    command = [*validate.COMMAND, "-kernel", str(image)]
    result = subprocess.run(command, env=env, cwd=validate.ROOT,
                            capture_output=True, text=True, timeout=15)
    validate.check(result.returncode == 0, f"generic-replay: {result.stderr}")
    state = json.loads((directory / "state.json").read_text())
    for field in ("pc", "instructions", "registers", "specials"):
        validate.check(state[field] == expected[field], f"generic-replay: {field} differs")
    inspection = MARKERS + [0] * 8  # Default loader leaves unowned neighbors zero.
    for address, value in writes:
        inspection[(address - INSPECTION) // 4] = value
    memory = (directory / "state.sram").read_bytes()
    validate.check(struct.unpack_from("<12I", memory, 0x8000) == tuple(inspection),
                   "generic-replay: owned memory or neighbors differ")
    (directory / "run.json").write_text(json.dumps({"command": command,
        "environment": settings, "returncode": result.returncode,
        "stderr": result.stderr, "state": state}, indent=2) + "\n")
    print("PASS generic-replay: default loader matches scalar and bundled ABS")


def main():
    CACHE.mkdir(parents=True, exist_ok=True)
    isa.CACHE = CACHE
    cases = [
        dict(name="reached-nonzero", updates={0: MARKERS[0]}),
        dict(name="incoming-source-load-six", source=0, value=INSPECTION - 4,
             updates={0: MARKERS[0]}),
        dict(name="incoming-address-load-six", dest=0, tail=(0x6102,), updates={2: MARKERS[0]}),
        dict(name="incoming-source-literal-eight", dest=8, source=15,
             tail=(0xE04F, 7), updates={15: 7}),
        dict(name="incoming-high-source-move-six", dest=8, source=15,
             tail=(0x160F,), updates={15: INSPECTION - 4}),
        dict(name="incoming-destination-store", tail=(0x2481,), registers={1: 0x87654321},
             writes=((INSPECTION, 0x87654321),)),
        dict(name="source-destination-store-alias", dest=5, source=5, tail=(0x2485,),
             writes=((INSPECTION, 0x81234567),)),
        dict(name="incoming-address-store", dest=0, tail=(0x6082,),
             registers={0: INSPECTION + 4, 2: 0x87654321},
             writes=((INSPECTION + 4, 0x87654321),)),
        dict(name="flagging-tail-overwrites-source", value=0xFFFFFFFF, tail=(0x1805,),
             registers={0: 1}, updates={5: 0}, final_psr=(PSR & 0xFFFFFFF0) | 6),
    ]
    for parallel in (False, True):
        label, tail = ("bundle", (0,)) if parallel else ("scalar", ())
        for dest in range(16):
            cases.append(dict(name=f"{label}-fields-{dest}", dest=dest,
                              source=(dest + 7) % 16, tail=tail))
            cases.append(dict(name=f"{label}-source-destination-alias-{dest}",
                              dest=dest, source=dest, value=0x80000000, tail=tail))
        for value in (0, 1, 0x7FFFFFFF, 0x80000000, 0x80000001, 0xFFFFFFFF, 0x81234567):
            cases.append(dict(name=f"{label}-boundary-{value:08x}", value=value, tail=tail))
        for psr in (0, 0xFFFFFFFF):
            cases.append(dict(name=f"{label}-psr-{psr:08x}", value=0x80000000, tail=tail, psr=psr))
    for tail in ((), (0x6100,), (0xE04F, 7)):
        for condition in (0, 1):
            cases.append(dict(name=f"conditional-{4 + 2 * len(tail)}-{condition}",
                              dest=8, source=15, tail=tail, condition=condition,
                              updates={0: MARKERS[0]} if tail == (0x6100,) else ({15: 7} if tail else {})))
    for case in cases:
        success_case(**case)
    generic_replay(*actual_sequence())
    absolute_tail_case()
    for low in (1, 0x10, 0x7F, 0x80, 0xFF):
        fault_case(f"noncanonical-scalar-{low:02x}", low=low)
        fault_case(f"noncanonical-bundle-{low:02x}", low=low, tail=(0x2481,))
    fault_case("overlapping-load-destination", tail=(0x2401,))
    fault_case("overlapping-literal-destination", tail=(0xE041, 0x1234))
    fault_case("deferred-tail", tail=(0xEED1, 0x1234))
    fault_case("unaligned-tail-load", tail=(0x6100,), reason="unaligned access",
               registers={0: INSPECTION - 3}, address=INSPECTION + 1, flags=0)
    fault_case("unmapped-tail-load", tail=(0x6100,), reason="unmapped access at 0x18000000",
               registers={0: 0x18000000 - 4}, address=0x18000000, flags=0)
    fault_case("unaligned-tail-store", tail=(0x2481,), reason="unaligned access",
               stack=STACK + 1, address=INSPECTION + 1)
    fault_case("unmapped-tail-store", tail=(0x2481,), reason="unmapped access at 0x18000000",
               stack=0x18000000 - 16, address=0x18000000)
    fault_case("read-only-tail-store", tail=(0x2481,), reason="write to read-only XIP (NOR)",
               stack=ENTRY - 16, address=ENTRY)
    fault_case("guarded-tail-store", tail=(0x2481,),
               reason="CPU write intersects an enabled guest guard window", address=INSPECTION, write_guard=True)
    for tail in ((), (0x2481,)):
        fault_case(f"pc-guard-{4 + 2 * len(tail)}", tail=tail, pc_guard=True,
                   reason="guest PC lies outside both configured guard windows")
    summary = {"passed": True, "instruction": "exact E430 scalar ABS and canonical F430 bundles",
               "reference_compared_cases": len(cases) + 2, "actual_sequence_cases": 1,
               "absolute_extended_tail_cases": 1,
               "generic_replays": 1, "noncanonical_operand_faults": 10,
               "precheck_conflict_or_tail_faults": 3, "tail_access_faults": 6,
               "pc_guard_faults": 2, "total_model_faults": 21,
               "primary_arithmetic_blob": "19b640bc036b14df78ee32ac45595d759b502317",
               "qemu_sha256": hashlib.sha256(validate.QEMU.read_bytes()).hexdigest(),
               "hardware_validation": False, "hardware_fault_state_validation": False}
    (CACHE / "validation.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(f"PASS ABS: {len(cases) + 2} reference comparisons, generic replay and 21 model faults")


if __name__ == "__main__":
    main()
