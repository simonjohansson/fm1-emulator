#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Validate exact E1F4 signed mode1 classification in parallel bundles.

Pinned Apache arithops.sinc:332-335 executes numerator B / denominator C,
with A=destination bits12:15, B=bits4:7, C=bits8:11 and mode1 in bits0:3.
Its preceding signed prose reverses B/C; the executable constructor, vendor
F1F4/0011 + 2B81 at 0x0200e2fc, and separate reference probes agree on B/C.
The reached bundle divides incoming r1 by incoming r0 while storing incoming
r1 at SP+44. Quotients truncate toward zero; division preserves PSR/RETS.
Only signed mode1 is admitted to the existing classifier. Unsigned mode0 is
reference-valid but parallel-deferred; the scalar mode0 path stays supported.
Incoming snapshots, tail-first execution, helpers and predicate machinery stay
unchanged. Balanced final/nonfinal THEN/ELSE cases complete a following IF.
Zero denominator and INT_MIN/-1 remain explicit unsupported helper behavior.
A successful tail can retain stores, GPR updates and flags before that head
fault, without a head result or bundle retirement. Tail access faults precede
the head. Existing conflict prechecks reject reference-valid overlapping GPR
destinations before either half; this gate does not widen that model policy.
Fatal reference calls expose no CPU fault snapshot. QEMU fault expectations
establish modeled ordering only, not hardware validity, rollback or fault state.
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
CACHE = HERE / ".cache/parallel-signed-divide-validation"
ENTRY = 0x02000120
INSPECTION = 0x01C08000
STACK = INSPECTION - 44
PSR = 0x89ABCDE5
RETS = 0x12345678
GPRS = [0x10203040 + i * 0x01010101 for i in range(16)]
GPRS[0] = 0x01C7FE08
MARKERS = [0x12345678, 0x89ABCDEF, 0x76543210, 0x0BADF00D]
INSPECTION_WORDS = MARKERS + [0xA5A5A5A5] + [0] * 3 + [0xA5A5A5A5] * 4


def signed(value):
    return value - 0x100000000 if value & 0x80000000 else value


def quotient(numerator, denominator):
    numerator, denominator = signed(numerator), signed(denominator)
    assert denominator and (numerator, denominator) != (-0x80000000, -1)
    result = abs(numerator) // abs(denominator)
    return (-result if (numerator < 0) != (denominator < 0) else result) & 0xFFFFFFFF


def setup(registers, psr=PSR, stack=STACK, write_guard=False, pc_guard=False):
    guest = Guest()
    for index, value in enumerate(MARKERS):
        guest.write(INSPECTION + index * 4, value)
    if write_guard:
        # The tail word begins below but intersects this one-byte write window.
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
                   f"{name}: scalar/bundle sizing or single retirement differs")
    validate.check(state["registers"] == expected and state["specials"] == specials(psr),
                   f"{name}: signed quotient, incoming aliases, tail, PSR or RETS differs")
    validate.check(state["inspection"] == inspection,
                   f"{name}: incoming store source/address or neighboring memory differs")


def success_case(name, dest=0, left=1, right=0, numerator=8192, denominator=16,
                 tail=(0x2B81,), registers=None, updates=None, writes=(), psr=PSR,
                 final_psr=None, scalar=False, mode=1):
    incoming = dict(registers or {})
    incoming[left], incoming[right] = numerator, denominator
    guest, expected = setup(incoming, psr)
    old_left, old_right = expected[left], expected[right]
    word = (dest << 12) | (right << 8) | (left << 4) | mode
    guest.emit(0xE1F4 if scalar else 0xF1F4, word, *(() if scalar else tail))
    guest.emit(0)
    inspection = list(INSPECTION_WORDS)
    for register, value in (updates or {}).items():
        expected[register] = value & 0xFFFFFFFF
    expected[dest] = quotient(old_left, old_right) if mode == 1 else old_left // old_right
    for address, stored_value in writes:
        inspection[(address - INSPECTION) // 4] = stored_value & 0xFFFFFFFF
    image = save_image(name, guest)
    state = isa.compare(name, image, guest.pc, limit=100)
    check_success(name, state, expected, inspection, guest.pc, guest.instructions,
                  psr if final_psr is None else final_psr)
    (CACHE / f"{name}-evidence.json").write_text(json.dumps({
        "second_word": word, "incoming_numerator": old_left,
        "incoming_denominator": old_right, "tail": tail, "scalar_control": scalar,
        "initial_psr": psr, "final_psr": psr if final_psr is None else final_psr}, indent=2) + "\n")
    return image, state, writes


def actual_sequence():
    name = "actual-bundle-square-and-shift"
    guest, expected = setup({0: 16, 1: 8192})
    guest.emit(0xF1F4, 0x0011, 0x2B81)
    guest.emit(0x1B00)
    guest.emit(0xA880)
    guest.emit(0)
    expected[0] = 1024  # (8192 / 16)^2 >> 8.
    inspection = [8192, *INSPECTION_WORDS[1:]]
    image = save_image(name, guest)
    state = isa.compare(name, image, guest.pc, limit=100)
    check_success(name, state, expected, inspection, guest.pc, guest.instructions, PSR)
    return image, state, ((INSPECTION, 8192),)


def extended_tail_case(overwrites_head_source=False):
    # Classifier admission also admits E1F4 as an eight-byte bundle's tail.
    # The first case divides old r1 / old r0; the second tail replaces r0 but
    # disjoint packed-add r1=oldr0+1 still uses the incoming r0 snapshot.
    name = "divide-extended-tail" + ("-head-source" if overwrites_head_source else "")
    if overwrites_head_source:
        guest, expected = setup({0: 7, 1: 0xFFFFFFF9, 2: 3})
        guest.emit(0xF0E1, 0x0001, 0xE1F4, 0x0211)
        expected[0], expected[1] = 0xFFFFFFFE, 8
    else:
        guest, expected = setup({0: 7, 1: 3})
        guest.emit(0xF0E0, 0x0001, 0xE1F4, 0x2011)
        expected[0], expected[2] = 8, 0
    guest.emit(0)
    image = save_image(name, guest)
    state = isa.compare(name, image, guest.pc, limit=100)
    check_success(name, state, expected, INSPECTION_WORDS, guest.pc,
                  guest.instructions, PSR & 0xFFFFFFF0)


def conditional_case(width, arm, position, condition):
    name = f"conditional-{width}-{arm}-{position}-{condition}"
    guest, expected = setup({0: 16, 1: 8192, 4: condition, 6: 0})
    count = 1 if position == "final" else 2
    then_count = count if arm == "then" else 1
    else_count = count if arm == "else" else 1
    guest.emit(0xEA24, ((then_count - 1) << 14) | (else_count << 12) | 1)
    tail = (0x2B81,) if width == 6 else (0xE04F, 7)
    selected = condition == 0 if arm == "then" else condition != 0
    for block in ("then", "else"):
        if block == arm:
            guest.emit(0xF1F4, 0x0011, *tail)
            if position == "nonfinal":
                guest.emit(0)
        else:
            guest.emit(0xE04D, 0x1111)
    # This IF must start after the entire first block has completed.
    guest.emit(0xEA26, 1)
    guest.emit(0xE04E, 0x2222)
    guest.emit(0)
    inspection = list(INSPECTION_WORDS)
    if selected:
        expected[0] = 512
        if width == 6:
            inspection[0] = 8192
        else:
            expected[15] = 7
    else:
        expected[13] = 0x1111
    expected[14] = 0x2222
    retired = guest.instructions - (1 if selected else count)
    image = save_image(name, guest)
    state = isa.compare(name, image, guest.pc, limit=100)
    check_success(name, state, expected, inspection, guest.pc, retired, PSR)


def skipped_edge_case(overflow=False):
    name = "skipped-overflow" if overflow else "skipped-zero"
    numerator, denominator = (0x80000000, 0xFFFFFFFF) if overflow else (7, 0)
    guest, expected = setup({0: denominator, 1: numerator, 4: 1, 6: 0})
    guest.emit(0xEA24, 1)
    guest.emit(0xF1F4, 0x0011, 0x2B81)
    guest.emit(0xEA26, 1)
    guest.emit(0xE04E, 0x2222)
    guest.emit(0)
    expected[14] = 0x2222
    image = save_image(name, guest)
    state = isa.compare(name, image, guest.pc, limit=100)
    check_success(name, state, expected, INSPECTION_WORDS, guest.pc,
                  guest.instructions - 1, PSR)


def run_model_fault(name, guest, expected, fault_pc, before, span, reason,
                    stack=STACK, address=None, flags=1, expected_psr=PSR,
                    writes=(), pc_guard=False, phase="before-tail"):
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
                   f"{name}: reason, bundle PC or pre-retirement count differs: {result.stderr}")
    access = ({"address": address, "size": 4, "flags": flags} if address is not None
              else {"address": fault_pc, "size": span, "flags": 2})
    validate.check(state["last_access"] == access, f"{name}: fault stage/access span differs")
    validate.check(state["registers"] == expected and
                   state["specials"] == specials(expected_psr, stack),
                   f"{name}: retained tail, head result, continuation, PSR or RETS differs")
    words = list(MARKERS)
    for destination, value in writes:
        words[(destination - INSPECTION) // 4] = value & 0xFFFFFFFF
    memory = (directory / "state.sram").read_bytes()
    validate.check(len(memory) == 0x80000 and
                   memory[0x8000:0x8010] == struct.pack("<IIII", *words),
                   f"{name}: retained tail store or neighboring words differ")
    if pc_guard:
        validate.check(state["guards"]["debug_message"] & (1 << 12), f"{name}: PC guard not latched")
    record = {"command": command, "environment": settings, "returncode": result.returncode,
              "stderr": result.stderr, "state": state, "modeled_fault_phase": phase,
              "expected_retained_tail_writes": writes,
              "fixture_sha256": hashlib.sha256(image.read_bytes()).hexdigest(),
              "hardware_fault_state_validation": False, "reference_fault_state_available": False}
    (directory / "run.json").write_text(json.dumps(record, indent=2) + "\n")
    print(f"PASS {name}: model {phase} fault before head result and bundle retirement")


def fault_case(name, tail=(0x2B81,), mode=1, reason=None, dest=0, left=1, right=0,
               numerator=8192, denominator=16, registers=None, updates=None,
               writes=(), expected_psr=PSR, stack=STACK, address=None, flags=1,
               write_guard=False, pc_guard=False, phase="before-tail"):
    incoming = dict(registers or {})
    incoming[left], incoming[right] = numerator, denominator
    guest, expected = setup(incoming, stack=stack, write_guard=write_guard, pc_guard=pc_guard)
    fault_pc, before = guest.pc, guest.instructions
    guest.emit(0xF1F4, (dest << 12) | (right << 8) | (left << 4) | mode, *tail)
    for register, value in (updates or {}).items():
        expected[register] = value & 0xFFFFFFFF
    run_model_fault(name, guest, expected, fault_pc, before, (2 + len(tail)) * 2,
                    reason or "unsupported instruction 0xf1f4", stack=stack,
                    address=address, flags=flags, expected_psr=expected_psr,
                    writes=writes, pc_guard=pc_guard, phase=phase)


def extended_tail_fault_case(overflow=False):
    name = "extended-divide-tail-overflow" if overflow else "extended-divide-tail-zero"
    numerator, denominator = (0x80000000, 0xFFFFFFFF) if overflow else (7, 0)
    guest, expected = setup({0: 7, 1: numerator, 3: denominator})
    fault_pc, before = guest.pc, guest.instructions
    guest.emit(0xF0E0, 0x0001, 0xE1F4, 0x2311)
    reason = ("signed-division-overflow behavior is unsupported" if overflow
              else "divide-by-zero behavior is unsupported")
    run_model_fault(name, guest, expected, fault_pc, before, 8, reason,
                    phase="divide-tail-before-packed-add-head")


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
                   "generic-replay: incoming store or owned neighboring memory differs")
    (directory / "run.json").write_text(json.dumps({"command": command,
        "environment": settings, "returncode": result.returncode,
        "stderr": result.stderr, "state": state}, indent=2) + "\n")
    print("PASS generic-replay: default loader matches reached signed-divide sequence")


def main():
    CACHE.mkdir(parents=True, exist_ok=True)
    isa.CACHE = CACHE
    cases = [
        dict(name="reached-incoming-stack-source", writes=((INSPECTION, 8192),)),
        dict(name="old-destination-stack-source", tail=(0x2B80,), writes=((INSPECTION, 16),)),
        dict(name="numerator-destination-stack-source", dest=1, writes=((INSPECTION, 8192),)),
        dict(name="old-address-store", denominator=INSPECTION, tail=(0x6082,),
             registers={2: 0x76543210}, writes=((INSPECTION, 0x76543210),)),
        dict(name="numerator-overwritten-compact", dest=8, tail=(0x1601,), updates={1: 16}),
        dict(name="denominator-overwritten-compact", dest=8, tail=(0x1610,), updates={0: 8192}),
        dict(name="numerator-overwritten-extended", dest=8, left=15, right=14,
             numerator=-987654321, denominator=17, tail=(0xE04F, 7), updates={15: 7}),
        dict(name="denominator-overwritten-extended", dest=8, left=15, right=14,
             numerator=-987654321, denominator=17, tail=(0xE04E, 7), updates={14: 7}),
        dict(name="tail-flags-overwrite-numerator", dest=8, numerator=0xFFFFFFFF,
             denominator=1, tail=(0x1801,), updates={1: 0}, final_psr=(PSR & ~15) | 6),
        dict(name="tail-flags-overwrite-denominator", dest=8, numerator=1,
             denominator=0xFFFFFFFF, tail=(0x1810,), updates={0: 0}, final_psr=(PSR & ~15) | 6),
        dict(name="tail-overflow-flags-before-signed-divide", dest=8,
             numerator=0x80000000, denominator=1, tail=(0x1821,),
             registers={2: 0x80000000}, updates={1: 0}, final_psr=(PSR & ~15) | 7),
    ]
    for dest in range(16):
        left, right = (dest + 7) % 16, (dest + 11) % 16
        for label, lreg, rreg in (("fields", left, right), ("dest-left", dest, right),
                                  ("dest-right", left, dest), ("same-sources", left, left),
                                  ("all-equal", dest, dest)):
            cases.append(dict(name=f"{label}-{dest}", dest=dest, left=lreg, right=rreg,
                              numerator=-987654321, denominator=17, tail=(0,)))
    boundaries = [(0, -1), (1, 2), (-1, 2), (7, 3), (-7, 3), (7, -3), (-7, -3),
                  (-0x80000000, 1), (-0x80000000, 2), (-0x80000000, -0x80000000),
                  (0x7FFFFFFF, -1), (0x7FFFFFFF, -0x80000000), (-0x80000000, 0x7FFFFFFF),
                  (-0x7FFFFFFF, -1), (-0x7FFFFFFF, 3), (0x7FFFFFFF, 3), (0x12345678, -17)]
    for numerator, denominator in boundaries:
        cases.append(dict(name=f"boundary-{numerator & 0xFFFFFFFF:08x}-{denominator & 0xFFFFFFFF:08x}",
                          dest=2, left=0, right=1, numerator=numerator,
                          denominator=denominator, tail=(0,)))
    for psr in (0, 0xFFFFFFFF):
        cases.append(dict(name=f"psr-{psr:08x}", psr=psr, writes=((INSPECTION, 8192),)))
    cases.extend([
        dict(name="unsigned-scalar-control", dest=2, left=0, right=1, numerator=0x80000000,
             denominator=7, scalar=True, mode=0),
        dict(name="signed-scalar-control", dest=0, left=0, right=1, numerator=-7,
             denominator=3, scalar=True),
        dict(name="signed-high-scalar-control", dest=15, left=14, right=13,
             numerator=-0x80000000, denominator=2, scalar=True),
    ])
    for case in cases:
        success_case(**case)
    generic_replay(*actual_sequence())
    extended_tail_case()
    extended_tail_case(True)
    for width in (6, 8):
        for arm in ("then", "else"):
            for position in ("final", "nonfinal"):
                for condition in (0, 1):
                    conditional_case(width, arm, position, condition)
    skipped_edge_case()
    skipped_edge_case(True)
    # Mode0 is existing scalar/reference-valid unsigned division, but this
    # reached classifier scope admits only signed mode1. Other low modes were
    # rejected by the reference; hardware invalidity is not established.
    for mode in (0, *range(2, 16)):
        fault_case(f"deferred-or-malformed-head-mode-{mode}", mode=mode)
    fault_case("conflicting-load-destination", tail=(0x2B00,))
    fault_case("conflicting-literal-destination", tail=(0xE040, 7))
    fault_case("deferred-tail", tail=(0xEED1, 0x1234))
    for name, base, reason in [("unaligned", INSPECTION - 3, "unaligned access"),
                              ("unmapped", 0x17FFFFFC, "unmapped access at 0x18000000")]:
        fault_case(f"{name}-tail-read", tail=(0x6102,), left=1, right=3,
                   registers={0: base}, reason=reason, address=(base + 4) & 0xFFFFFFFF,
                   flags=0, phase="tail-read-before-divide")
    fault_case("unmapped-tail-read-before-zero", tail=(0x6102,), right=3, denominator=0,
               registers={0: 0x17FFFFFC}, reason="unmapped access at 0x18000000",
               address=0x18000000, flags=0, phase="tail-read-before-divide-zero")
    fault_case("unaligned-tail-store", reason="unaligned access", stack=STACK + 1,
               address=INSPECTION + 1, phase="tail-write-before-divide")
    fault_case("unmapped-tail-store", reason="unmapped access at 0x18000000",
               stack=0x18000000 - 44, address=0x18000000, phase="tail-write-before-divide")
    fault_case("read-only-tail-store", reason="write to read-only XIP (NOR)",
               stack=ENTRY - 44, address=ENTRY, phase="tail-write-before-divide")
    fault_case("guarded-tail-store", reason="CPU write intersects an enabled guest guard window",
               address=INSPECTION, write_guard=True, phase="tail-write-before-divide")
    for width, tail in [(6, (0x2B81,)), (8, (0xE04F, 7))]:
        fault_case(f"pc-guard-{width}", tail=tail, pc_guard=True,
                   reason="guest PC lies outside both configured guard windows")
    for overflow in (False, True):
        label = "overflow" if overflow else "zero"
        numerator, denominator = (0x80000000, 0xFFFFFFFF) if overflow else (7, 0)
        reason = ("signed-division-overflow behavior is unsupported" if overflow
                  else "divide-by-zero behavior is unsupported")
        fault_case(f"head-{label}-after-tail-store", numerator=numerator, denominator=denominator,
                   reason=reason, address=INSPECTION, writes=((INSPECTION, numerator),),
                   phase="divide-head-after-tail-store")
        fault_case(f"head-{label}-after-denominator-literal", dest=8, tail=(0xE040, 7),
                   numerator=numerator, denominator=denominator, reason=reason, updates={0: 7},
                   phase="divide-head-after-denominator-literal")
        fault_case(f"head-{label}-after-flagging-tail", dest=8, tail=(0x1821,),
                   numerator=numerator, denominator=denominator, registers={2: -numerator},
                   updates={1: 0}, expected_psr=(PSR & ~15) | (7 if overflow else 6), reason=reason,
                   phase="divide-head-after-flagging-tail")
        extended_tail_fault_case(overflow)
    summary = {"passed": True, "instruction": "exact E1F4 signed mode1 parallel classification",
               "reference_compared_cases": len(cases) + 21, "ordinary_reference_cases": len(cases),
               "actual_sequence_cases": 1, "extended_divide_tail_cases": 2,
               "balanced_conditional_cases": 16, "skipped_exception_cases": 2,
               "generic_replays": 1, "mode_precheck_faults": 15, "conflict_or_deferred_tail_faults": 3,
               "tail_access_faults": 7, "pc_guard_faults": 2, "after_tail_head_helper_faults": 6,
               "divide_tail_helper_faults": 2, "total_model_faults": 35,
               "primary_arithmetic_blob": "19b640bc036b14df78ee32ac45595d759b502317",
               "primary_prose_disagreement": "Signed comment reverses C/B; constructor/vendor/reference establish B/C.",
               "unsigned_parallel_mode0_deferred": True, "conflicts_reference_valid_model_rejected": True,
               "qemu_sha256": hashlib.sha256(validate.QEMU.read_bytes()).hexdigest(),
               "hardware_validation": False, "hardware_fault_state_validation": False,
               "reference_fault_state_available": False}
    (CACHE / "validation.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(f"PASS signed divide: {len(cases) + 21} reference comparisons, generic replay and 35 model faults")


if __name__ == "__main__":
    main()
