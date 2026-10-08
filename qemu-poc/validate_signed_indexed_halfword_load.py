#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Validate exact EDD8 kind-A signed halfword loads using index << 1.

Vendor EDD8/302A and EDD8/002A plus the separate reference establish doubled
incoming index, modulo-32 address arithmetic, sign extension and no writeback.
Pinned Apache SLEIGH identifies kind A and register fields; its scaled-form
comment agrees with vendor assembly, but its body omits the shift. Distinct
scaled/unscaled halfwords below preserve that discrepancy as tested evidence.
Other unsupported kinds remain deferred. Fault
snapshots check existing model policy, not hardware state; the public reference
reports fault address/width but returns no architectural state. Write guard
windows permit reads, while PC guards check the complete instruction fetch.
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
CACHE = HERE / ".cache/signed-indexed-halfword-load-validation"
ENTRY = 0x02000120
INSPECTION = 0x01C08000
PSR = 0x89ABCDE5
GPRS = [0x10203040 + i * 0x01010101 for i in range(16)]
GPRS[0] = 0x01C7FE08
INITIAL_INSPECTION = [0xA5A5A5A5] * 5 + [0] * 3 + [0xA5A5A5A5] * 4
CONTINUATION = 0x33445566


def operand(dest, base, index, kind=10):
    return (dest << 12) | (index << 8) | (base << 4) | kind


def signed_halfword(value):
    assert 0 <= value <= 0xFFFF
    return value if value < 0x8000 else value | 0xFFFF0000


def setup(registers, value, address=INSPECTION, psr=PSR, guard=None):
    guest = Guest()
    word = (value << 16) | 0xA69B if address & 2 else (0xA69B << 16) | value
    # With index 2 and base INSPECTION-4, the doubled address contains value
    # while the unscaled address contains 7654. Negative index FFFFFFFE with
    # base INSPECTION+4 selects value rather than the unscaled halfword A69B.
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
    expected[5] = psr
    return expected


def save_image(name, guest):
    image = CACHE / f"{name}.bin"
    image.write_bytes(guest.bytes() + bytes(16))
    return image


def success_case(name, dest=3, base=2, index=0, index_value=2, registers=None,
                 value=0x8001, address=INSPECTION, psr=PSR, condition=None, guard=None):
    if base == index:
        # 0xAAAAAAAB is the inverse of 3 modulo 2^32; shared base/index uses
        # three times its incoming value, even when the pointer is unmapped.
        incoming = {base: (address * 0xAAAAAAAB) & 0xFFFFFFFF}
    else:
        incoming = {base: (address - 2 * index_value) & 0xFFFFFFFF, index: index_value}
    incoming.update(registers or {})
    if condition is not None:
        incoming[0] = condition
    guest, expected, markers = setup(incoming, value, address, psr, guard)
    effective = (expected[base] + 2 * expected[index]) & 0xFFFFFFFF
    validate.check(effective == address, f"{name}: fixture address differs")
    if condition is not None:
        guest.emit(0xEA20, 1)  # One THEN instruction, selected if r0 & 1 == 0.
    guest.emit(0xEDD8, operand(dest, base, index))
    guest.emit(0x0000)
    image = save_image(name, guest)
    state = isa.compare(name, image, guest.pc)
    selected = condition is None or condition == 0
    if selected:
        expected[dest] = signed_halfword(value)
    inspection = list(INITIAL_INSPECTION)
    inspection[:4] = markers[1:]
    retired = guest.instructions - (0 if selected else 1)
    validate.check(state["pc"] == guest.pc and state["instructions"] == retired,
                   f"{name}: four-byte load/continuation retirement differs")
    validate.check(state["registers"] == expected and state["specials"] == specials(psr),
                   f"{name}: doubled incoming index, aliases, low32 address, sign or PSR differs")
    validate.check(state["inspection"] == inspection,
                   f"{name}: signed load changed scaled/unscaled halfwords or neighboring memory")
    return image, state, markers


def fault_case(name, registers, reason, kind=10, guard=None,
               compare_reference=False):
    dest, base, index = 15, 15, 14
    guest, expected, markers = setup(registers, 0x8123, guard=guard)
    fault_pc = guest.pc
    before = guest.instructions
    guest.emit(0xEDD8, operand(dest, base, index, kind))
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
    address = (expected[base] + 2 * expected[index]) & 0xFFFFFFFF
    access = {"address": address, "size": 2, "flags": 0}
    if kind != 10 or guard == "pc":
        access = {"address": fault_pc, "size": 4, "flags": 2}
    validate.check(state["last_access"] == access,
                   f"{name}: attempted effective address, width or instruction fetch differs")
    validate.check(state["registers"] == expected and state["specials"] == specials(PSR),
                   f"{name}: rejected load changed destination, base, index, continuation or PSR")
    memory = (directory / "state.sram").read_bytes()
    validate.check(len(memory) == 0x80000 and
                   memory[0x7FFC:0x8010] == struct.pack("<IIIII", *markers),
                   f"{name}: rejected load altered scaled/unscaled halfwords or neighbors")
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
                   memory[0x7FFC:0x8010] == struct.pack("<IIIII", *markers),
                   "generic-replay: signed load altered scaled/unscaled halfwords or neighbors")
    (directory / "run.json").write_text(json.dumps({"command": command,
        "environment": settings, "returncode": result.returncode,
        "stderr": result.stderr, "state": state}, indent=2) + "\n")
    print("PASS generic-replay: default application mode matches indexed load architecture and memory")


def promoted_unscaled_control():
    # Exact bytes of the old deferred-kind-2 fixture are retained.
    name = "deferred-kind-2"
    guest, expected, markers = setup({15: INSPECTION - 4, 14: 2}, 0x8123)
    guest.emit(0xEDD8, operand(15, 15, 14, 2))
    guest.literal(13, CONTINUATION)
    image = save_image(name, guest)
    state = isa.compare(name, image, guest.pc, limit=100)
    expected[15], expected[13] = 0x7654, CONTINUATION
    inspection = list(INITIAL_INSPECTION)
    inspection[:4] = markers[1:]
    validate.check(state["registers"] == expected and state["specials"] == specials(PSR) and
                   state["pc"] == guest.pc and state["instructions"] == guest.instructions and
                   state["inspection"] == inspection,
                   "promoted kind2: unscaled address, full state, continuation or memory differs")


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
    for register in (0, 7, 8, 15):
        other = (register + 1) % 16
        cases.extend([
            dict(name=f"destination-base-alias-{register}", dest=register, base=register, index=other),
            dict(name=f"destination-index-alias-{register}", dest=register, base=other, index=register),
            dict(name=f"base-index-alias-{register}", dest=other, base=register, index=register),
            dict(name=f"all-equal-{register}", dest=register, base=register, index=register),
        ])
    for index_value in (15, 16, 127, 255, 0x80000001, 0xFFFFFFFF):
        cases.append(dict(name=f"index-boundary-{index_value:08x}", index_value=index_value))
    cases.extend([
        dict(name="reached-negative"),
        dict(name="next-destination-index-alias", dest=0),
        dict(name="incoming-base-below-sram", address=0x01C00000),
        dict(name="wrapping-base-sum", registers={2: 0xFFFFFFFE, 0: 0x00E04001}),
        dict(name="last-sram-halfword", dest=15, base=14, index=13,
             address=0x01C7FFFE, value=0xFFFF),
        # Immutable fixture begins with literal r1 (FFC1), an independent
        # negative signed halfword value read from ordinary mapped XIP.
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
    generic_replay(*replay)
    fault_case("unaligned-effective-address", {15: INSPECTION + 1, 14: 0},
               "unaligned access", compare_reference=True)
    fault_case("unmapped-effective-address", {15: 0x18000000, 14: 1},
               "unmapped access at 0x18000002", compare_reference=True)
    fault_case("wrapping-effective-address", {15: 0xFFFFFFFE, 14: 1},
               "unmapped access at 0x00000000", compare_reference=True)
    fault_case("pc-guard-rejects-load-fetch", {15: INSPECTION - 4, 14: 2},
               "guest PC lies outside both configured guard windows", guard="pc")
    promoted_unscaled_control()
    deferred = [kind for kind in range(16) if kind not in (2, 8, 9, 10)]
    for kind in deferred:
        fault_case(f"deferred-kind-{kind:x}", {15: INSPECTION - 4, 14: 2},
                   "unsupported instruction 0xedd8", kind=kind)
    summary = {"passed": True, "instruction": "EDD8 kind-A signed indexed halfword load",
               "positive_reference_cases": len(cases) + 1, "promoted_original_kind2_control": 1, "generic_replays": 1,
               "fault_cases": 16, "deferred_kinds": deferred,
               "reference_fault_state_available": False, "hardware_fault_state_validation": False,
               "primary_source_discrepancy": "kind-A SLEIGH body omits shift stated by comment/vendor",
               "address_evidence": "distinct scaled/unscaled halfwords, negative index and alias probes",
               "guard_scope": "write windows permit reads; PC window rejects instruction fetch"}
    (CACHE / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print("PASS signed indexed halfword milestone: scale, sign, aliases, faults and generic replay")


if __name__ == "__main__":
    main()
