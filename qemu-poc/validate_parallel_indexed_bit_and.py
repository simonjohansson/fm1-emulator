#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Validate exact E194 mode2 indexed-bit AND classification in bundles.

Pinned Apache logicops:308-310 and vendor F194/1152 +6004 at0x020086c4
establish r1=incoming r5 & (1<<incoming r1), paired with r4=[incoming r0].
A=x12:15 is destination, B=x4:7 data, C=x8:11 bit index, low nibble2 AND.
Only this classifier entry changes. Scalar modes0..3, bit_operand, incoming
capture, tail-first ordering, sizing, helpers and predicate behavior stay fixed.
All32 supported index values and flags/aliases are independently exercised.
Primary gives no explicit out-of-range contract. The independent reference
wraps tested large/negative indices modulo32, while existing QEMU bit_operand
rejects unsigned index>=32. This inherited unsupported range remains model
policy, not hardware invalidity. A head index fault retains completed tail
stores/GPR/PSR without head result or bundle retirement, using normalized E194
reason. An E194 tail index fault precedes head effects and retirement. Modes
0/1/3 and destination conflicts are reference-valid but parallel model-deferred;
modes4..15 are rejected before tail. Tail access faults precede index checks.
Policy cases retain separate full wrapped/reference completions; reference fatal
calls expose no CPU fault snapshot. Hardware fault state/rollback remain unknown.
Balanced selected/skipped six/eight-byte arms complete a following independent
IF, including skipped large indices. No common predicate or schema is changed.
The initial exploratory E194/1002 tail had wrong fields; retained research names
its correction E194/0152 and does not count that faulty expectation as evidence.
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
CACHE = HERE / ".cache/parallel-indexed-bit-and-validation"
ENTRY = 0x02000120
INSPECTION = 0x01C08000
STACK = INSPECTION - 16
PSR = 0x89ABCDE5
RETS = 0x12345678
GPRS = [0x10203040 + i * 0x01010101 for i in range(16)]
GPRS[0] = 0x01C7FE08
MARKERS = [0x12345678, 0x89ABCDEF, 0x76543210, 0x0BADF00D]
INSPECTION_WORDS = MARKERS + [0xA5A5A5A5] + [0] * 3 + [0xA5A5A5A5] * 4


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
                   f"{name}: indexed mask, incoming aliases, tail, PSR or RETS differs")
    validate.check(state["inspection"] == inspection,
                   f"{name}: incoming store source/address or neighboring memory differs")



def bit_result(data, index, mode=2, wrapped=False):
    assert wrapped or index < 32
    mask = 1 << (index & 31)
    return (data | mask if mode == 0 else data ^ mask if mode == 1 else
            data & mask if mode == 2 else data & ~mask) & 0xFFFFFFFF


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


def success_case(name, dest=1, datareg=5, indexreg=1, data=0xFFFFFFFF, index=8,
                 tail=(0,), registers=None, updates=None, writes=(), psr=PSR,
                 final_psr=None, scalar=False, mode=2):
    incoming = {0: INSPECTION, **(registers or {})}
    incoming[datareg], incoming[indexreg] = data, index
    guest, expected = setup(incoming, psr)
    old_data, old_index = expected[datareg], expected[indexreg]
    word = (dest << 12) | (indexreg << 8) | (datareg << 4) | mode
    guest.emit(0xE194 if scalar else 0xF194, word, *(() if scalar else tail))
    guest.emit(0)
    inspection = list(INSPECTION_WORDS)
    for register, value in (updates or {}).items():
        expected[register] = value & 0xFFFFFFFF
    expected[dest] = bit_result(old_data, old_index, mode)
    for address, value in writes:
        inspection[(address - INSPECTION) // 4] = value & 0xFFFFFFFF
    image = save_image(name, guest)
    state = isa.compare(name, image, guest.pc, limit=100)
    check_success(name, state, expected, inspection, guest.pc, guest.instructions,
                  psr if final_psr is None else final_psr)
    (CACHE / f"{name}-evidence.json").write_text(json.dumps({"second_word": word,
        "incoming_data": old_data, "incoming_index": old_index,
        "tail": tail, "scalar_control": scalar}, indent=2) + "\n")
    return image, state, writes


def actual_sequence():
    # Reached captured data0 yields r1=0; actual next opcode5201 takes its
    # zero branch, skipping a deliberately visible fallthrough marker.
    name = "actual-captured-zero-bundle-and-branch"
    guest, expected = setup({0: INSPECTION, 1: 8, 5: 0})
    before = guest.instructions
    guest.emit(0xF194, 0x1152, 0x6004)
    branch_pc = guest.pc
    guest.emit(0x5201)
    guest.literal(13, 0x33445566)
    target = branch_pc + 2 + 36
    guest.words.extend([0] * ((target - ENTRY) // 2 - len(guest.words)))
    guest.emit(0)
    expected[1], expected[4] = 0, MARKERS[0]
    image = save_image(name, guest)
    state = isa.compare(name, image, guest.pc, limit=100)
    check_success(name, state, expected, INSPECTION_WORDS, guest.pc, before + 3, PSR)
    return image, state, ()


def extended_tail_case(overwrites_head_source=False):
    name = "indexed-and-extended-tail" + ("-old-head-source" if overwrites_head_source else "-old-index")
    if overwrites_head_source:
        guest, expected = setup({0: 0xFFFFFFFF, 1: 8, 5: 0xFFFFFFFF})
        guest.emit(0xF0E2, 0x0001, 0xE194, 0x0152)
        expected[0], expected[2] = 0x100, 0
        psr = (PSR & ~15) | 6
    else:
        guest, expected = setup({0: 8, 5: 0xFFFFFFFF})
        guest.emit(0xF0E0, 0x0001, 0xE194, 0x1052)
        expected[0], expected[1] = 9, 0x100
        psr = PSR & ~15
    guest.emit(0)
    image = save_image(name, guest)
    state = isa.compare(name, image, guest.pc, limit=100)
    check_success(name, state, expected, INSPECTION_WORDS, guest.pc, guest.instructions, psr)


def conditional_case(width, arm, position, condition):
    name = f"conditional-{width}-{arm}-{position}-{condition}"
    guest, expected = setup({0: INSPECTION, 1: 8, 4: condition, 5: 0xFFFFFFFF, 6: 0})
    count = 1 if position == "final" else 2
    then_count, else_count = (count, 1) if arm == "then" else (1, count)
    guest.emit(0xEA24, ((then_count - 1) << 14) | (else_count << 12) | 1)
    tail = (0x6004,) if width == 6 else (0xE04F, 7)
    selected = condition == 0 if arm == "then" else condition != 0
    for block in ("then", "else"):
        if block == arm:
            guest.emit(0xF194, 0x1152, *tail)
            if position == "nonfinal":
                guest.emit(0)
        else:
            guest.emit(0xE04D, 0x1111)
    # The load tail can overwrite r4, so the next independent IF uses r6.
    guest.emit(0xEA26, 1)
    guest.emit(0xE04E, 0x2222)
    guest.emit(0)
    if selected:
        expected[1] = 0x100
        expected[4 if width == 6 else 15] = MARKERS[0] if width == 6 else 7
    else:
        expected[13] = 0x1111
    expected[14] = 0x2222
    image = save_image(name, guest)
    state = isa.compare(name, image, guest.pc, limit=100)
    check_success(name, state, expected, INSPECTION_WORDS, guest.pc,
                  guest.instructions - (1 if selected else count), PSR)


def skipped_large_index_case(index):
    name = f"skipped-large-index-{index:08x}"
    guest, expected = setup({0: INSPECTION, 1: index, 4: 1, 5: 0xFFFFFFFF, 6: 0})
    guest.emit(0xEA24, 1)
    guest.emit(0xF194, 0x1152, 0x6004)
    guest.emit(0xEA26, 1)
    guest.emit(0xE04E, 0x2222)
    guest.emit(0)
    expected[14] = 0x2222
    image = save_image(name, guest)
    state = isa.compare(name, image, guest.pc, limit=100)
    check_success(name, state, expected, INSPECTION_WORDS, guest.pc, guest.instructions - 1, PSR)

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
    return directory, env, record


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
    print("PASS generic-replay: default loader matches reached indexed-bit AND sequence")


def fault_case(name, tail=(0x2485,), mode=2, reason=None, dest=1, datareg=5, indexreg=1,
               data=0xFFFFFFFF, index=8, registers=None, updates=None, writes=(),
               expected_psr=PSR, stack=STACK, address=None, flags=1, write_guard=False,
               pc_guard=False, phase="before-tail", reference_completion=False,
               reference_writes=None):
    incoming = {0: INSPECTION, **(registers or {})}
    incoming[datareg], incoming[indexreg] = data, index
    guest, expected = setup(incoming, stack=stack, write_guard=write_guard, pc_guard=pc_guard)
    old_data, old_index = expected[datareg], expected[indexreg]
    fault_pc, before = guest.pc, guest.instructions
    word = (dest << 12) | (indexreg << 8) | (datareg << 4) | mode
    guest.emit(0xF194, word, *tail)
    for register, value in (updates or {}).items():
        expected[register] = value & 0xFFFFFFFF
    directory, env, record = run_model_fault(name, guest, expected, fault_pc, before,
        (2 + len(tail)) * 2, reason or "unsupported instruction 0xf194", stack=stack,
        address=address, flags=flags, expected_psr=expected_psr, writes=writes,
        pc_guard=pc_guard, phase=phase)
    record.update(second_word=word, incoming_data=old_data, incoming_index=old_index,
                  policy_scope="existing unsupported range or scoped parallel admission; not hardware invalidity")
    if reference_completion:
        image = CACHE / f"{name}.bin"
        record.update(reference_record(image, env))
        validate.check(record["reference_returncode"] == 0,
                       f"{name}: reference-valid policy form no longer completes: {record['reference_stderr']}")
        oracle_expected = list(expected)
        oracle_expected[dest] = bit_result(old_data, old_index, mode, wrapped=True)
        oracle_expected[13] = 0x33445566  # Reference retires the continuation; model fault does not.
        oracle_inspection = list(INSPECTION_WORDS)
        for destination, value in writes if reference_writes is None else reference_writes:
            oracle_inspection[(destination - INSPECTION) // 4] = value & 0xFFFFFFFF
        oracle = record["reference_state"]
        validate.check(oracle["pc"] == guest.pc and oracle["instructions"] == guest.instructions and
                       oracle["registers"] == oracle_expected and
                       oracle["specials"] == specials(expected_psr, stack) and
                       oracle["inspection"] == oracle_inspection,
                       f"{name}: full reference completion/wrapped mask or incoming state differs")
        record.update(reference_valid_model_rejected=True,
                      reference_index_wrap="tested modulo32" if old_index >= 32 else "supported index")
    (directory / "run.json").write_text(json.dumps(record, indent=2) + "\n")
    print(f"PASS {name}: model {phase} fault before head result and bundle retirement")


def extended_tail_index_fault(index):
    name = f"large-index-extended-tail-{index:08x}"
    guest, expected = setup({0: index, 5: 0xFFFFFFFF})
    fault_pc, before = guest.pc, guest.instructions
    guest.emit(0xF0E0, 0x0001, 0xE194, 0x1052)
    directory, env, record = run_model_fault(name, guest, expected, fault_pc, before, 8,
        "unsupported instruction 0xe194", phase="index-tail-before-packed-add-head")
    image = CACHE / f"{name}.bin"
    record.update(reference_record(image, env))
    validate.check(record["reference_returncode"] == 0,
                   f"{name}: wrapped independent tail no longer completes")
    oracle_expected = list(expected)
    oracle_expected[0], oracle_expected[1] = (index + 1) & 0xFFFFFFFF, 1 << (index & 31)
    oracle_expected[13] = 0x33445566
    oracle_psr = (PSR & ~15) | (6 if index == 0xFFFFFFFF else 0)
    check_success(name + "/reference", record["reference_state"], oracle_expected,
                  INSPECTION_WORDS, guest.pc, guest.instructions, oracle_psr)
    record.update(reference_valid_model_rejected=True, reference_index_wrap="tested modulo32",
                  policy_scope="inherited index<32 limit; fault before packed-add head")
    (directory / "run.json").write_text(json.dumps(record, indent=2) + "\n")
    print(f"PASS {name}: inherited index fault precedes head effects; wrapped reference retained")


def main():
    CACHE.mkdir(parents=True, exist_ok=True)
    isa.CACHE = CACHE
    cases = [
        dict(name="actual-nonzero-load", tail=(0x6004,), updates={4: MARKERS[0]}),
        dict(name="actual-captured-zero-load", data=0, tail=(0x6004,), updates={4: MARKERS[0]}),
        dict(name="destination-address-load", dest=0, tail=(0x6004,), updates={4: MARKERS[0]}),
        dict(name="overwritten-data-load", tail=(0x6005,), updates={5: MARKERS[0]}),
        dict(name="overwritten-index-load", dest=8, tail=(0x6001,), updates={1: MARKERS[0]}),
        dict(name="overwritten-data-extended", dest=8, datareg=15, indexreg=14,
             tail=(0xE04F, 7), updates={15: 7}),
        dict(name="overwritten-index-extended", dest=8, datareg=15, indexreg=14,
             tail=(0xE04E, 31), updates={14: 31}),
        dict(name="incoming-destination-store", tail=(0x2481,), writes=((INSPECTION, 8),)),
        dict(name="incoming-data-store", tail=(0x2485,), writes=((INSPECTION, 0xFFFFFFFF),)),
        dict(name="incoming-address-store", dest=0, tail=(0x6082,),
             registers={2: 0x87654321}, writes=((INSPECTION, 0x87654321),)),
        dict(name="flag-tail-overwrites-data", tail=(0x1805,), registers={0: 1}, updates={5: 0},
             final_psr=(PSR & ~15) | 6),
        dict(name="flag-tail-overwrites-index-to-large", dest=8, index=31, tail=(0x1801,),
             registers={0: 1}, updates={1: 32}, final_psr=PSR & ~15),
    ]
    for dest in range(16):
        datareg, indexreg = (dest + 7) % 16, (dest + 11) % 16
        for label, dreg, ireg in (("fields", datareg, indexreg), ("dest-data", dest, indexreg),
                                  ("dest-index", datareg, dest), ("same-sources", datareg, datareg),
                                  ("all-equal", dest, dest)):
            cases.append(dict(name=f"{label}-{dest}", dest=dest, datareg=dreg, indexreg=ireg))
    for index in range(32):
        for data in (0xFFFFFFFF, 0x55555555):
            cases.append(dict(name=f"index-{index}-data-{data:08x}", index=index, data=data))
    for index in (0, 31):
        for data in (0, 0x80000001):
            cases.append(dict(name=f"boundary-{index}-data-{data:08x}", index=index, data=data))
    for psr in (0, 0xFFFFFFFF):
        cases.append(dict(name=f"psr-{psr:08x}", psr=psr))
    for mode in range(4):
        cases.append(dict(name=f"unchanged-scalar-mode-{mode}", dest=8, datareg=15, indexreg=14,
                          data=0x81234567, index=31, mode=mode, scalar=True))
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
    for index in (32, 0xFFFFFFFF):
        skipped_large_index_case(index)
    # Three modes are primary/reference-valid, but only reached mode2 enters
    # the parallel classifier. All other low modes fault before the store tail.
    for mode in (0, 1, 3, *range(4, 16)):
        fault_case(f"deferred-or-malformed-mode-{mode}", mode=mode,
                   reference_completion=mode < 4, reference_writes=((INSPECTION, 0xFFFFFFFF),))
    fault_case("conflicting-load-destination", tail=(0x6001,), reference_completion=True)
    fault_case("conflicting-literal-destination", tail=(0xE041, 7), reference_completion=True)
    fault_case("deferred-tail", tail=(0xEED1, 0x1234))
    fault_case("unaligned-tail-read", tail=(0x6004,), registers={0: INSPECTION + 1},
               reason="unaligned access", address=INSPECTION + 1, flags=0, phase="tail-read-before-index")
    fault_case("unmapped-tail-read", tail=(0x6004,), registers={0: 0x18000000},
               reason="unmapped access at 0x18000000", address=0x18000000, flags=0, phase="tail-read-before-index")
    fault_case("unmapped-tail-read-before-large-index", tail=(0x6004,), registers={0: 0x18000000},
               index=32, reason="unmapped access at 0x18000000", address=0x18000000,
               flags=0, phase="tail-read-before-unsupported-index")
    fault_case("unaligned-tail-store", reason="unaligned access", stack=STACK + 1,
               address=INSPECTION + 1, phase="tail-write-before-index")
    fault_case("unmapped-tail-store", reason="unmapped access at 0x18000000",
               stack=0x18000000 - 16, address=0x18000000, phase="tail-write-before-index")
    fault_case("read-only-tail-store", reason="write to read-only XIP (NOR)",
               stack=ENTRY - 16, address=ENTRY, phase="tail-write-before-index")
    fault_case("guarded-tail-store", reason="CPU write intersects an enabled guest guard window",
               address=INSPECTION, write_guard=True, phase="tail-write-before-index")
    for width, tail in ((6, (0x2485,)), (8, (0xE04F, 7))):
        fault_case(f"pc-guard-{width}", tail=tail, pc_guard=True,
                   reason="guest PC lies outside both configured guard windows")
    for index in (32, 33, 63, 0x80000000, 0xFFFFFFFF):
        fault_case(f"large-head-index-after-store-{index:08x}", index=index,
                   reason="unsupported instruction 0xe194", address=INSPECTION,
                   writes=((INSPECTION, 0xFFFFFFFF),), phase="index-head-after-tail-store",
                   reference_completion=True)
    fault_case("large-head-index-after-repair-literal", dest=8, index=32, tail=(0xE041, 8),
               updates={1: 8}, reason="unsupported instruction 0xe194",
               phase="index-head-after-index-repair-literal", reference_completion=True)
    fault_case("large-head-index-after-repair-flags", dest=8, index=32, tail=(0x1801,),
               registers={0: -32}, updates={1: 0}, expected_psr=(PSR & ~15) | 6,
               reason="unsupported instruction 0xe194", phase="index-head-after-index-repair-flags",
               reference_completion=True)
    fault_case("large-head-index-after-data-literal", dest=8, index=0xFFFFFFFF, tail=(0xE045, 7),
               updates={5: 7}, reason="unsupported instruction 0xe194",
               phase="index-head-after-data-literal", reference_completion=True)
    fault_case("large-head-index-after-tail-load", index=32, tail=(0x6004,), updates={4: MARKERS[0]},
               reason="unsupported instruction 0xe194", address=INSPECTION, flags=0,
               phase="index-head-after-tail-load", reference_completion=True)
    for index in (32, 0xFFFFFFFF):
        extended_tail_index_fault(index)
    summary = {"passed": True, "instruction": "exact E194 mode2 indexed-bit AND parallel classification",
               "reference_positive_cases": len(cases) + 21, "ordinary_reference_cases": len(cases),
               "actual_sequence_cases": 1, "extended_bit_and_tail_cases": 2,
               "balanced_conditional_cases": 16, "skipped_large_index_cases": 2,
               "generic_replays": 1, "mode_precheck_faults": 15,
               "conflict_or_deferred_tail_faults": 3, "tail_access_faults": 7,
               "pc_guard_faults": 2, "head_index_faults_after_tail": 9,
               "extended_tail_index_faults_before_head": 2, "total_model_faults": 38,
               "reference_valid_model_fault_completions": 16,
               "primary_logic_blob": "6a2f65042279b0f09fb167250ec888d0f36f44ea",
               "index_policy": "QEMU preserves unsigned index<32 limit; reference wraps tested large indices modulo32",
               "parallel_modes_0_1_3_deferred": True, "conflicts_reference_valid_model_rejected": True,
               "initial_wrong_1002_fixture_retained_in_research": True,
               "qemu_sha256": hashlib.sha256(validate.QEMU.read_bytes()).hexdigest(),
               "hardware_validation": False, "hardware_fault_state_validation": False,
               "reference_fault_state_available": False}
    (CACHE / "validation.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(f"PASS indexed-bit AND: {len(cases) + 21} oracle positives, generic and 38 model faults")


if __name__ == "__main__":
    main()
