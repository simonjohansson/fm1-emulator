#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Validate exact EDDC kind2 signed pre-index halfword loads.

Vendor EDDC/3312 and a separate executable reference establish an unscaled
incoming base+index, modulo32 addressing, signed16 result and base writeback.
The pinned Apache loadstore has no exact EDDC constructor; analogous word/byte
pre-index forms and register tokens are not direct opcode authority. All source
aliases are supported: base gets the address, then destination gets the loaded
value, so a destination/base alias ends with the signed result. The effective
address must use both incoming operands before either register is changed.
Faults retain existing modeled writeback-before-access/no-retirement policy;
reference access errors expose width/address but no CPU fault state. Hardware
ordering is unverified. PC guard and deferred-kind faults precede writeback.
Reference-valid unsigned kind0 stays deferred under this signed-only scope,
with its raw completion recorded. FDDC is an existing branch, not a negative.
Write guard windows permit reads. Helpers, neighbors and classifiers stay fixed.
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
CACHE = HERE / ".cache/signed-preindexed-halfword-load-validation"
ENTRY = 0x02000120
INSPECTION = 0x01C08000
PSR = 0x89ABCDE5
RETS = 0x12345678
STACK = INSPECTION - 16
GPRS = [0x10203040 + i * 0x01010101 for i in range(16)]
GPRS[0] = 0x01C7FE08
INITIAL_INSPECTION = [0xA5A5A5A5] * 5 + [0] * 3 + [0xA5A5A5A5] * 4
CONTINUATION = 0x33445566


def operand(dest, base, index, kind=2):
    return (dest << 12) | (index << 8) | (base << 4) | kind


def signed_halfword(value):
    assert 0 <= value <= 0xFFFF
    return value if value < 0x8000 else value | 0xFFFF0000


def setup(registers, value, address=INSPECTION, psr=PSR, guard=None):
    guest = Guest()
    word = (value << 16) | 0xA69B if address & 2 else (0xA69B << 16) | value
    # At index2 the unscaled target and doubled-index candidate occupy
    # distinct halfwords. Negative indices also distinguish the adjacent word.
    markers = [0x76547FFF, word, 0x43210001, 0x89ABCDEF, 0x0BADF00D]
    for index, marker in enumerate(markers):
        guest.write(INSPECTION - 4 + index * 4, marker)
    if address not in (INSPECTION, INSPECTION + 2) and address < 0x02000000:
        guest.write(address & ~3, word)
    if guard == "write":
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
    guest.literal(4, RETS)
    guest.emit(0xE064, 0x4380)
    guest.literal(14, STACK, special=True)
    expected = list(GPRS)
    for register, initial in registers.items():
        expected[register] = initial & 0xFFFFFFFF
    for register, initial in enumerate(expected):
        guest.literal(register, initial)
    if guard == "pc":
        # Inclusive PC range ends after these three setup instructions and
        # before the signed load, which is denied at its complete fetch.
        expected[0] = guest.pc + 14 - 1
        expected[1] = 0x01EEE380
        guest.literal(0, expected[0])
        guest.literal(1, expected[1])
        guest.store(0, 1)
    return guest, expected, markers


def specials(psr):
    expected = [0] * 16
    expected[3], expected[5], expected[14] = RETS, psr, STACK
    return expected


def save_image(name, guest):
    image = CACHE / f"{name}.bin"
    image.write_bytes(guest.bytes() + bytes(16))
    return image


def success_case(name, dest=3, base=1, index=3, index_value=2, registers=None,
                 value=0x8001, address=INSPECTION, psr=PSR, condition=None, guard=None):
    if base == index:
        incoming = {base: address // 2}
    else:
        incoming = {base: (address - index_value) & 0xFFFFFFFF, index: index_value}
    incoming.update(registers or {})
    if condition is not None:
        incoming[4] = condition
    guest, expected, markers = setup(incoming, value, address, psr, guard)
    effective = (expected[base] + expected[index]) & 0xFFFFFFFF
    validate.check(effective == address, f"{name}: fixture address differs")
    if condition is not None:
        guest.emit(0xEA24, 1)  # One THEN instruction, selected if r4 & 1 == 0.
    guest.emit(0xEDDC, operand(dest, base, index))
    guest.emit(0x0000)
    image = save_image(name, guest)
    state = isa.compare(name, image, guest.pc)
    selected = condition is None or condition == 0
    if selected:
        expected[base] = effective
        expected[dest] = signed_halfword(value)
    inspection = list(INITIAL_INSPECTION)
    inspection[:4] = markers[1:]
    retired = guest.instructions - (0 if selected else 1)
    validate.check(state["pc"] == guest.pc and state["instructions"] == retired,
                   f"{name}: four-byte load/continuation retirement differs")
    validate.check(state["registers"] == expected and state["specials"] == specials(psr),
                   f"{name}: unscaled incoming sum, load-wins aliases, writeback, sign, PSR or RETS differs")
    validate.check(state["inspection"] == inspection,
                   f"{name}: signed load changed unscaled/scaled halfwords or neighboring memory")
    return image, state, markers


def fault_case(name, registers, reason, kind=2, guard=None,
               compare_reference=False, reference_valid=False, dest=15, base=15, index=14):
    guest, expected, markers = setup(registers, 0x8123, guard=guard)
    fault_pc = guest.pc
    before = guest.instructions
    guest.emit(0xEDDC, operand(dest, base, index, kind))
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
    address = (expected[base] + expected[index]) & 0xFFFFFFFF
    access = {"address": address, "size": 2, "flags": 0}
    access_fault = kind == 2 and guard != "pc"
    if not access_fault:
        access = {"address": fault_pc, "size": 4, "flags": 2}
    validate.check(state["last_access"] == access,
                   f"{name}: attempted effective address, width or instruction fetch differs")
    if access_fault:
        expected[base] = address  # Partial modeled writeback; no loaded value.
    validate.check(state["registers"] == expected and state["specials"] == specials(PSR),
                   f"{name}: fault-stage loaded result, modeled base writeback, index, continuation, PSR or RETS differs")
    memory = (directory / "state.sram").read_bytes()
    validate.check(len(memory) == 0x80000 and
                   memory[0x7FFC:0x8010] == struct.pack("<IIIII", *markers),
                   f"{name}: rejected load altered unscaled/scaled halfwords or neighbors")
    if guard == "pc":
        validate.check(state["guards"]["debug_message"] == 1 << 12,
                       f"{name}: established PC guard fault status differs")
    if compare_reference or reference_valid:
        reference_command = ["mise", "exec", "--", "cargo", "run", "--manifest-path",
                             str(HERE / "reference/Cargo.toml"), "--locked", "--offline",
                             "--", "snapshot", str(image)]
        reference = subprocess.run(reference_command, env=env, cwd=validate.ROOT,
                                   capture_output=True, text=True, timeout=60)
        record.update(reference_command=reference_command,
                      reference_returncode=reference.returncode,
                      reference_stdout=reference.stdout, reference_stderr=reference.stderr)
        if reference_valid:
            validate.check(kind == 0 and reference.returncode == 0,
                           f"{name}: deferred unsigned reference completion failed: {reference.stderr}")
            oracle = json.loads(reference.stdout)
            oracle_expected = list(expected)
            oracle_expected[base] = address
            oracle_expected[dest] = 0x8123
            oracle_expected[13] = CONTINUATION
            validate.check(oracle["pc"] == guest.pc and oracle["instructions"] == before + 2 and
                           oracle["registers"] == oracle_expected and oracle["specials"] == specials(PSR),
                           f"{name}: reference unsigned load/alias completion differs")
            oracle_inspection = list(INITIAL_INSPECTION)
            oracle_inspection[:4] = markers[1:]
            validate.check(oracle["inspection"] == oracle_inspection,
                           f"{name}: reference unsigned load changed memory")
            record.update(reference_state=oracle, deferred_reference_valid_kind=0)
        else:
            validate.check(reference.returncode != 0 and not reference.stdout.strip() and
                           f"pc: {fault_pc}" in reference.stderr and
                           f'address: {address}, size: 2, operation: "read"' in reference.stderr,
                           f"{name}: separate reference fault address/width differs: {reference.stderr}")
            record["reference_fault_state_available"] = False
    (directory / "run.json").write_text(json.dumps(record, indent=2) + "\n")
    print(f"PASS {name}: fault before load result/retirement; partial modeled writeback checked")


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
                   memory[0x7FFC:0x8010] == struct.pack("<IIIII", *markers),
                   "generic-replay: signed load altered unscaled/scaled halfwords or neighbors")
    (directory / "run.json").write_text(json.dumps({"command": command,
        "environment": settings, "returncode": result.returncode,
        "stderr": result.stderr, "state": state}, indent=2) + "\n")
    print("PASS generic-replay: default application mode matches indexed load architecture and memory")


def actual_load_pair():
    name = "actual-preindex-and-following-immediate-load"
    guest, expected, markers = setup({1: 0, 3: INSPECTION}, 0x8001)
    guest.emit(0xEDDC, 0x3312)
    guest.emit(0xED54, 0x1012)
    guest.emit(0)
    expected[3], expected[1] = 0xFFFF8001, 0xFFFFA69B
    image = save_image(name, guest)
    state = isa.compare(name, image, guest.pc, limit=100)
    validate.check(state["pc"] == guest.pc and state["instructions"] == guest.instructions and
                   state["registers"] == expected and state["specials"] == specials(PSR),
                   f"{name}: actual alias load, base writeback, next load, PSR or retirement differs")
    inspection = list(INITIAL_INSPECTION)
    inspection[:4] = markers[1:]
    validate.check(state["inspection"] == inspection, f"{name}: adjacent memory differs")


def main():
    CACHE.mkdir(parents=True, exist_ok=True)
    isa.CACHE = CACHE
    cases = []
    for index_value in (0, 1, 2, 0x7FFFFFFF, 0x80000000, 0xFFFFFFFE):
        for value in (0, 0x7FFF, 0x8000, 0xFFFF):
            cases.append(dict(name=f"index-{index_value:08x}-sign-{value:04x}",
                              index_value=index_value, value=value))
    for register in range(16):
        cases.append(dict(name=f"fields-destination-{register}", dest=register,
                          base=(register + 1) % 16, index=(register + 2) % 16))
        other = (register + 1) % 16
        cases.extend([
            dict(name=f"destination-base-alias-{register}", dest=register, base=register, index=other),
            dict(name=f"destination-index-alias-{register}", dest=register, base=other, index=register),
            dict(name=f"base-index-alias-{register}", dest=other, base=register, index=register),
            dict(name=f"all-equal-{register}", dest=register, base=register, index=register),
        ])
    cases.extend([
        dict(name="reached-negative"),
        dict(name="incoming-table-pointer", registers={1: 0, 3: INSPECTION}),
        dict(name="incoming-base-below-sram", address=0x01C00000),
        dict(name="wrapping-base-sum", registers={1: 0xFFFFFFFE, 3: INSPECTION + 2}),
        dict(name="shared-base-index-wrap", base=1, index=1,
             registers={1: 0x80000000 + INSPECTION // 2}),
        dict(name="last-sram-halfword", dest=15, base=14, index=13,
             address=0x01C7FFFE, value=0xFFFF),
        # The immutable fixture starts with literal r1 (FFC1), a signed
        # negative halfword read from mapped read-only XIP rather than SRAM.
        dict(name="read-only-xip-load", address=ENTRY, value=0xFFC1),
        dict(name="write-guard-permits-read", guard="write", address=INSPECTION + 2),
    ])
    for psr in (0, 0xFFFFFFFF):
        cases.append(dict(name=f"psr-{psr:08x}", psr=psr))
    for condition in (0, 1):
        cases.append(dict(name=f"conditional-{condition}", dest=6, index=3, condition=condition))
    replay = None
    for case in cases:
        result = success_case(**case)
        if case["name"] == "reached-negative":
            replay = result
    actual_load_pair()
    generic_replay(*replay)
    fault_case("unaligned-destination-base-alias", {15: INSPECTION - 2, 14: 3},
               "unaligned access", compare_reference=True)
    fault_case("unaligned-destination-index-alias", {15: INSPECTION - 2, 14: 3},
               "unaligned access", dest=14, compare_reference=True)
    fault_case("unmapped-separate-destination", {15: 0x18000000 - 2, 14: 2},
               "unmapped access at 0x18000000", dest=12, compare_reference=True)
    fault_case("unmapped-destination-index-alias", {15: 0x18000000 - 2, 14: 2},
               "unmapped access at 0x18000000", dest=14, compare_reference=True)
    fault_case("wrapping-base-index-alias", {15: 0x80000000},
               "unmapped access at 0x00000000", dest=14, base=15, index=15, compare_reference=True)
    fault_case("wrapping-all-equal", {15: 0x80000000},
               "unmapped access at 0x00000000", dest=15, base=15, index=15, compare_reference=True)
    fault_case("pc-guard-rejects-before-writeback", {15: INSPECTION - 2, 14: 2},
               "guest PC lies outside both configured guard windows", guard="pc")
    deferred = [kind for kind in range(16) if kind != 2]
    for kind in deferred:
        fault_case(f"deferred-kind-{kind:x}", {15: INSPECTION - 2, 14: 2},
                   "unsupported instruction 0xeddc", kind=kind, reference_valid=kind == 0)
    summary = {"passed": True, "instruction": "EDDC kind2 signed pre-index halfword load",
               "positive_reference_cases": len(cases) + 1, "actual_adjacent_load_pair_cases": 1,
               "generic_replays": 1, "fault_cases": 22, "data_access_faults": 6,
               "pc_guard_faults": 1, "deferred_kinds": deferred,
               "reference_valid_deferred_unsigned_kind": 0,
               "reference_fault_state_available": False, "hardware_fault_state_validation": False,
               "exact_primary_constructor_available": False,
               "encoding_authority": "direct vendor EDDC3312 and independent executable probes",
               "fault_policy": "modeled base writeback before access; PC/deferred faults before writeback",
               "guard_scope": "write windows permit reads; PC window rejects complete instruction fetch"}
    (CACHE / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print("PASS signed pre-index halfword: unscaled sum, load-wins aliases, model faults and generic replay")


if __name__ == "__main__":
    main()
