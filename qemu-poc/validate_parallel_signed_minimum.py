#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Validate exact E435 signed mode1 classification in parallel bundles.

Pinned Apache arithops:374-380 and vendor F435/2621 +2603 at0x0200998a
establish signed minimum of incoming B=x4:7 and C=x8:11 into A=x12:15.
The reached bundle writes r2=min(oldr2,oldr6) and loads r3=[specialSP+24].
Batch A admits modes0/1 to the classifier; scalar signed/unsigned minimum,
helpers, incoming capture, tail-first execution, sizing and predicates stay
fixed. Both head and extended-tail roles are checked. Minimum does not write
PSR; flags written by a disjoint tail survive. All source aliases use actual
initialized GPR values. Mode0 is admitted by Batch A; reference-valid
destination conflicts remain model rejected. Modes2..15 reject before effects/count.
Tail accesses fault before any minimum result or bundle retirement. Fatal
reference calls expose no CPU fault snapshot; fault state/order are modeled
policy, not hardware validity or rollback claims. Balanced selected/skipped
six/eight-byte arms complete a following independent IF. Default-loader replay
uses requested observers, not an observer-free equivalence claim.
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
CACHE = HERE / ".cache/parallel-signed-minimum-validation"
ENTRY = 0x02000120
INSPECTION = 0x01C08000
STACK = INSPECTION - 24
PSR = 0x89ABCDE5
RETS = 0x12345678
GPRS = [0x10203040 + i * 0x01010101 for i in range(16)]
GPRS[0] = 0x01C7FE08
MARKERS = [0x12345678, 0x89ABCDEF, 0x76543210, 0x0BADF00D]
INSPECTION_WORDS = MARKERS + [0xA5A5A5A5] + [0] * 3 + [0xA5A5A5A5] * 4



def signed(value):
    return value - 0x100000000 if value & 0x80000000 else value


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
                   f"{name}: signed minimum, incoming aliases, tail, PSR or RETS differs")
    validate.check(state["inspection"] == inspection,
                   f"{name}: incoming store source/address or neighboring memory differs")


def minimum(left, right, mode=1):
    return left if (signed(left) <= signed(right) if mode == 1 else left <= right) else right


def success_case(name, dest=2, left=2, right=6, left_value=12, right_value=240,
                 tail=(0,), registers=None, updates=None, writes=(), psr=PSR,
                 final_psr=None, scalar=False, mode=1):
    incoming = {0: INSPECTION, **(registers or {})}
    incoming[left], incoming[right] = left_value, right_value
    guest, expected = setup(incoming, psr)
    old_left, old_right = expected[left], expected[right]
    word = (dest << 12) | (right << 8) | (left << 4) | mode
    guest.emit(0xE435 if scalar else 0xF435, word, *(() if scalar else tail))
    guest.emit(0)
    inspection = list(INSPECTION_WORDS)
    for register, value in (updates or {}).items():
        expected[register] = value & 0xFFFFFFFF
    expected[dest] = minimum(old_left, old_right, mode)
    for address, stored_value in writes:
        inspection[(address - INSPECTION) // 4] = stored_value & 0xFFFFFFFF
    image = save_image(name, guest)
    state = isa.compare(name, image, guest.pc, limit=100)
    check_success(name, state, expected, inspection, guest.pc, guest.instructions,
                  psr if final_psr is None else final_psr)
    (CACHE / f"{name}-evidence.json").write_text(json.dumps({
        "second_word": word, "incoming_left": old_left, "incoming_right": old_right,
        "tail": tail, "scalar_control": scalar, "operand_mode": mode,
        "initial_psr": psr, "final_psr": psr if final_psr is None else final_psr}, indent=2) + "\n")
    return image, state, writes


def actual_sequence():
    # Same reached words and captured minimum inputs, with independently
    # seeded nonzero stack data relocated into the inspected SRAM region.
    name = "actual-fields-bundle-rev8-shift"
    guest, expected = setup({2: 12, 3: 13, 6: 240})
    guest.emit(0xF435, 0x2621, 0x2603)
    guest.emit(0xE070, 0x3300)
    guest.emit(0xB0B3)
    guest.emit(0)
    expected[2], expected[3] = 12, 0x7856
    image = save_image(name, guest)
    state = isa.compare(name, image, guest.pc, limit=100)
    check_success(name, state, expected, INSPECTION_WORDS, guest.pc, guest.instructions, PSR)
    return image, state, ()


def extended_tail_case(overwrites_head_source=False):
    name = "minimum-extended-tail" + ("-old-head-source" if overwrites_head_source else "-old-tail-source")
    if overwrites_head_source:
        guest, expected = setup({0: 7, 1: 0xFFFFFFF9, 2: 3})
        guest.emit(0xF0E1, 0x0001, 0xE435, 0x0211)
        expected[0], expected[1] = 0xFFFFFFF9, 8
    else:
        guest, expected = setup({0: 7, 1: 8})
        guest.emit(0xF0E0, 0x0001, 0xE435, 0x2011)
        expected[0], expected[2] = 8, 7
    guest.emit(0)
    image = save_image(name, guest)
    state = isa.compare(name, image, guest.pc, limit=100)
    check_success(name, state, expected, INSPECTION_WORDS, guest.pc,
                  guest.instructions, PSR & ~15)


def conditional_case(width, arm, position, condition):
    name = f"conditional-{width}-{arm}-{position}-{condition}"
    guest, expected = setup({2: 0x7FFFFFFF, 4: condition, 6: 0x80000000, 7: 0})
    count = 1 if position == "final" else 2
    then_count, else_count = (count, 1) if arm == "then" else (1, count)
    guest.emit(0xEA24, ((then_count - 1) << 14) | (else_count << 12) | 1)
    tail = (0x2603,) if width == 6 else (0xE04F, 7)
    selected = condition == 0 if arm == "then" else condition != 0
    for block in ("then", "else"):
        if block == arm:
            guest.emit(0xF435, 0x2621, *tail)
            if position == "nonfinal":
                guest.emit(0)
        else:
            guest.emit(0xE04D, 0x1111)
    guest.emit(0xEA27, 1)
    guest.emit(0xE04E, 0x2222)
    guest.emit(0)
    if selected:
        expected[2] = 0x80000000
        expected[3 if width == 6 else 15] = MARKERS[0] if width == 6 else 7
    else:
        expected[13] = 0x1111
    expected[14] = 0x2222
    image = save_image(name, guest)
    state = isa.compare(name, image, guest.pc, limit=100)
    check_success(name, state, expected, INSPECTION_WORDS, guest.pc,
                  guest.instructions - (1 if selected else count), PSR)



def run_model_fault(name, guest, expected, fault_pc, before, span, reason,
                    stack=STACK, address=None, flags=1, expected_psr=PSR,
                    writes=(), pc_guard=False, phase="before-tail", reference_expected=None,
                    reference_inspection=None, reference_category=None,
                    reference_access=None, reference_unsupported=False):
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
    words = MARKERS + [0] * 8  # alnk-probe cold-zero neighbors; reference uses poison.
    for destination, value in writes:
        words[(destination - INSPECTION) // 4] = value & 0xFFFFFFFF
    memory = (directory / "state.sram").read_bytes()
    validate.check(len(memory) == 0x80000 and
                   struct.unpack_from("<12I", memory, 0x8000) == tuple(words),
                   f"{name}: retained tail store or neighboring words differ")
    if pc_guard:
        validate.check(state["guards"]["debug_message"] & (1 << 12), f"{name}: PC guard not latched")
    record = {"command": command, "environment": settings, "returncode": result.returncode,
              "stderr": result.stderr, "state": state, "modeled_fault_phase": phase,
              "expected_retained_tail_writes": writes,
              "fixture_sha256": hashlib.sha256(image.read_bytes()).hexdigest(),
              "hardware_fault_state_validation": False, "reference_fault_state_available": False}
    record.update(reference_record(image, env))
    if reference_expected is not None:
        reference_expected = list(reference_expected)
        reference_expected[13] = 0x33445566  # Reference executes the continuation.
        validate.check(record["reference_returncode"] == 0,
                       f"{name}: separate reference completion failed: {record['reference_stderr']}")
        reference_state = record["reference_state"]
        validate.check(reference_state["pc"] == guest.pc and
                       reference_state["instructions"] == guest.instructions and
                       reference_state["registers"] == reference_expected and
                       reference_state["specials"] == specials(expected_psr, stack) and
                       reference_state["inspection"] == reference_inspection,
                       f"{name}: separate sampled reference completion differs")
        record["reference_full_sampled_completion_checked"] = True
        record["reference_model_policy_disagreement"] = True
    else:
        validate.check(record["reference_returncode"] != 0,
                       f"{name}: expected fatal reference category completed")
        if reference_unsupported:
            validate.check(f"Unsupported {{ pc: {fault_pc}, word: {0xE435} }}" in
                           record["reference_stderr"],
                           f"{name}: malformed normalized E435 reference category differs")
            record["reference_expected_category"] = "Unsupported E435"
        else:
            validate.check(reference_category in record["reference_stderr"] and
                           f"Access {{ pc: {fault_pc + 4}," in record["reference_stderr"] and
                           reference_access in record["reference_stderr"],
                           f"{name}: reference access category, tail PC, address, width or direction differs")
            record["reference_expected_category"] = reference_category
            record["reference_expected_access"] = reference_access
        record["reference_fatal_category_checked_without_fault_snapshot"] = True
    (directory / "run.json").write_text(json.dumps(record, indent=2) + "\n")
    print(f"PASS {name}: model {phase} fault before head result and bundle retirement")


def reference_record(image, env):
    command = ["mise", "exec", "--", "cargo", "run", "--manifest-path",
               str(HERE / "reference/Cargo.toml"), "--locked", "--offline",
               "--", "snapshot", str(image)]
    result = subprocess.run(command, env=env, cwd=validate.ROOT,
                            capture_output=True, text=True, timeout=60)
    record = {"reference_command": command, "reference_returncode": result.returncode,
              "reference_stdout": result.stdout, "reference_stderr": result.stderr}
    if result.returncode == 0:
        record["reference_state"] = json.loads(result.stdout)
    return record


def fault_case(name, tail=(0x2682,), mode=1, reason=None, dest=2, left=2, right=6,
               left_value=0x7FFFFFFF, right_value=0x80000000, registers=None,
               stack=STACK, address=None, flags=1, write_guard=False, pc_guard=False):
    incoming = {0: INSPECTION, **(registers or {})}
    incoming[left], incoming[right] = left_value, right_value
    guest, expected = setup(incoming, stack=stack, write_guard=write_guard, pc_guard=pc_guard)
    fault_pc, before = guest.pc, guest.instructions
    guest.emit(0xF435, (dest << 12) | (right << 8) | (left << 4) | mode, *tail)
    reference_expected = None
    reference_inspection = list(INSPECTION_WORDS)
    reference_category = reference_access = None
    if mode == 0 or (address is None and mode == 1):
        # Reference policy completions are checked separately from model faults.
        reference_expected = list(expected)
        reference_expected[dest] = minimum(expected[left], expected[right], mode)
        if tail == (0x2682,):
            reference_inspection[0] = expected[2]
        elif tail == (0xE04F, 7):
            reference_expected[15] = 7
        elif tail == (0xE435, 0xF620):
            reference_expected[15] = minimum(expected[2], expected[6], 0)
        elif tail == (0xE434, 0xF621):
            reference_expected[15] = max((expected[2], expected[6]), key=signed)
        else:
            # Conflict tails both target the head destination; the separate
            # reference ends with the incoming signed-minimum head result.
            validate.check(tail in ((0x2602,), (0xE042, 7)), f"{name}: unknown policy fixture")
    elif address is not None:
        reference_category = ("unaligned access" if reason == "unaligned access" else
                              "read-only XIP" if reason == "write to read-only XIP (NOR)" else
                              "CPU write protection violation" if write_guard else "unmapped")
        reference_access = f'address: {address}, size: 4, operation: "{"write" if flags else "read"}"'
    run_model_fault(name, guest, expected, fault_pc, before, (2 + len(tail)) * 2,
                    reason or "unsupported instruction 0xf435", stack=stack,
                    address=address, flags=flags, pc_guard=pc_guard,
                    phase="tail-access-before-minimum" if address is not None else "precheck-before-tail",
                    reference_expected=reference_expected, reference_inspection=reference_inspection,
                    reference_category=reference_category, reference_access=reference_access,
                    reference_unsupported=mode > 1)



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
    print("PASS generic-replay: default loader matches reached signed-minimum sequence")


def main():
    CACHE.mkdir(parents=True, exist_ok=True)
    isa.CACHE = CACHE
    cases = [
        dict(name="reached-incoming-stack-load", tail=(0x2603,), updates={3: MARKERS[0]}),
        dict(name="left-overwritten-compact", dest=8, left_value=0xFFFFFFFF, right_value=7,
             tail=(0x1662,), updates={2: 7}),
        dict(name="right-overwritten-compact", dest=8, left_value=1, right_value=0xFFFFFFFF,
             tail=(0x1626,), updates={6: 1}),
        dict(name="left-overwritten-extended", dest=8, left=15, right=14,
             left_value=0x80000000, right_value=0x7FFFFFFF, tail=(0xE04F, 7), updates={15: 7}),
        dict(name="right-overwritten-extended", dest=8, left=15, right=14,
             left_value=0x7FFFFFFF, right_value=0x80000000, tail=(0xE04E, 7), updates={14: 7}),
        dict(name="old-destination-store", dest=2, left=1, right=6, left_value=7, right_value=0xFFFFFFFD,
             registers={2: 0x87654321}, tail=(0x2682,), writes=((INSPECTION, 0x87654321),)),
        dict(name="old-left-destination-store", left_value=0x7FFFFFFF, right_value=0x80000000,
             tail=(0x2682,), writes=((INSPECTION, 0x7FFFFFFF),)),
        dict(name="old-address-store", dest=0, left=0, right=1, left_value=INSPECTION, right_value=0xFFFFFFFF,
             tail=(0x6082,), registers={2: 0x87654321}, writes=((INSPECTION, 0x87654321),)),
        dict(name="old-left-load", dest=8, left=0, right=6, left_value=INSPECTION, right_value=0x7FFFFFFF,
             tail=(0x6000,), updates={0: MARKERS[0]}),
        dict(name="zero-flag-tail-overwrites-left", dest=8, left=1, right=0,
             left_value=0xFFFFFFFF, right_value=1, tail=(0x1801,), updates={1: 0}, final_psr=(PSR & ~15) | 6),
        dict(name="overflow-flag-tail-overwrites-right", dest=8, left=0, right=1,
             left_value=1, right_value=0x80000000, registers={2: 0x80000000}, tail=(0x1821,),
             updates={1: 0}, final_psr=(PSR & ~15) | 7),
    ]
    for dest in range(16):
        left, right = (dest + 7) % 16, (dest + 11) % 16
        for label, lreg, rreg in (("fields", left, right), ("dest-left", dest, right),
                                  ("dest-right", left, dest), ("same-sources", left, left),
                                  ("all-equal", dest, dest)):
            cases.append(dict(name=f"{label}-{dest}", dest=dest, left=lreg, right=rreg,
                              left_value=0x7FFFFFFF, right_value=0x80000000))
    boundaries = [(0, 0), (0, 1), (1, 0), (0, 0xFFFFFFFF), (0xFFFFFFFF, 0),
                  (0x7FFFFFFF, 0x80000000), (0x80000000, 0x7FFFFFFF),
                  (0xFFFFFFFF, 0x80000000), (0x80000000, 0xFFFFFFFF),
                  (0x7FFFFFFF, 0x7FFFFFFF), (0x80000000, 0x80000000)]
    for a, b in boundaries:
        cases.append(dict(name=f"boundary-{a:08x}-{b:08x}", dest=8, left=15, right=14,
                          left_value=a, right_value=b))
    for psr in (0, 0xFFFFFFFF):
        cases.append(dict(name=f"psr-{psr:08x}", psr=psr))
    cases.extend([
        dict(name="unsigned-scalar-control", dest=8, left=15, right=14, left_value=0xFFFFFFFF,
             right_value=1, scalar=True, mode=0),
        dict(name="signed-scalar-control", dest=8, left=15, right=14, left_value=0xFFFFFFFF,
             right_value=1, scalar=True),
        dict(name="signed-high-scalar-alias-control", dest=15, left=15, right=14,
             left_value=0x80000000, right_value=0x7FFFFFFF, scalar=True),
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
    success_case("unsigned-parallel-mode0-control", dest=8, left=15, right=14,
                 left_value=0xFFFFFFFF, right_value=1, mode=0)
    for mode in range(2, 16):
        fault_case(f"deferred-or-malformed-head-mode-{mode}", mode=mode)
    fault_case("conflicting-load-destination", tail=(0x2602,))
    fault_case("conflicting-literal-destination", tail=(0xE042, 7))
    success_case("unsigned-minimum-tail-control", tail=(0xE435, 0xF620),
                 updates={15: 12})
    success_case("signed-maximum-tail-control", tail=(0xE434, 0xF621),
                 updates={15: 240})
    for name, base, reason in [("unaligned", INSPECTION + 1, "unaligned access"),
                              ("unmapped", 0x18000000, "unmapped access at 0x18000000")]:
        fault_case(f"{name}-tail-read", tail=(0x6003,), registers={0: base},
                   reason=reason, address=base, flags=0)
    fault_case("unaligned-tail-store", reason="unaligned access", stack=STACK + 1,
               address=INSPECTION + 1)
    fault_case("unmapped-tail-store", reason="unmapped access at 0x18000000",
               stack=0x18000000 - 24, address=0x18000000)
    fault_case("read-only-tail-store", reason="write to read-only XIP (NOR)",
               stack=ENTRY - 24, address=ENTRY)
    fault_case("guarded-tail-store", reason="CPU write intersects an enabled guest guard window",
               address=INSPECTION, write_guard=True)
    for width, tail in [(6, (0x2682,)), (8, (0xE04F, 7))]:
        fault_case(f"pc-guard-{width}", tail=tail, pc_guard=True,
                   reason="guest PC lies outside both configured guard windows")
    summary = {"passed": True, "instruction": "exact E435 signed mode1 parallel classification",
               "reference_compared_cases": len(cases) + 22, "ordinary_reference_cases": len(cases),
               "actual_sequence_cases": 1, "extended_minimum_tail_cases": 2,
               "balanced_conditional_cases": 16, "generic_replays": 1,
               "mode_precheck_faults": 14, "conflict_or_deferred_tail_faults": 2,
               "tail_access_faults": 6, "pc_guard_faults": 2, "total_model_faults": 24,
               "separate_reference_policy_completions": 2, "reference_pc_guard_completions": 2,
               "reference_fatal_categories_without_fault_snapshot": 20,
               "primary_arithmetic_blob": "19b640bc036b14df78ee32ac45595d759b502317",
               "unsigned_parallel_mode0_deferred": False, "conflicts_reference_valid_model_rejected": True,
               "qemu_sha256": hashlib.sha256(validate.QEMU.read_bytes()).hexdigest(),
               "hardware_validation": False, "hardware_fault_state_validation": False,
               "reference_fault_state_available": False}
    (CACHE / "validation.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(f"PASS signed minimum: {len(cases) + 22} reference comparisons, generic replay and 24 model faults")


if __name__ == "__main__":
    main()

