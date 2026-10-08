#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Validate exact canonical FF41 six-byte register inequality branches.

Pinned Apache progflow:504-507/slaspec:383-385 and vendor FF41/0100/FCF0 at
0x0200db62 establish GPR[x12:15] != GPR[x8:11], with signed16 third-word
word displacement from PC+6. Primary requires low-byte zero; nine nonzero
bytes complete the independent reference, so rejection remains canonical model
admission policy, with hardware reserved-byte behavior unverified.
The reference scans FF41 as four bytes inside IF arms: skipped displacement4
faults at the third word, and several THEN/ELSE markers or counts disagree.
Oracle-comparable positives and independent primary six-byte model positives
are counted separately, retaining each raw reference contradiction. Selected
final THEN+ELSE has a dedicated FF41 pre-retirement guard; the existing FF0C
helper is unchanged. Taken exits retain existing predicate state: nonfinal THEN
and ELSE exits fault at the following IF after branch retirement. Retained
predicate also blocks IRQ entry by source inspection, not IRQ validation here.
All modeled fault stages assert exact PC/count/full GPR/specials/owned memory.
No schema, common predicate, classifier or neighboring opcode is widened.
True32-bit PC wrapping cannot be executed at mapped instruction addresses with
this displacement range; full signed16 boundaries are checked at mapped XIP.
Fatal reference results provide no CPU fault snapshot or hardware fault proof.
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
CACHE = HERE / ".cache/long-register-ne-branch-validation"
ENTRY = 0x02000120
BRANCH = ENTRY + 0x10200
INSPECTION = 0x01C08000
PSR = 0x89ABCDE5
RETS = 0x12345678
GPRS = [0x10203040 + i * 0x01010101 for i in range(16)]
GPRS[0] = 0x01C7FE08
MARKERS = [0x12345678, 0x89ABCDEF, 0x76543210]
INITIAL_INSPECTION = [0xA5A5A5A5] * 5 + [0] * 3 + [0xA5A5A5A5] * 4
FINAL_THEN_REASON = "final THEN FF41 register branch with ELSE is unsupported"


def setup(registers, psr=PSR, guard_high=None):
    guest = Guest()
    for index, marker in enumerate(MARKERS):
        guest.write(INSPECTION + index * 4, marker)
    if guard_high is not None:
        guest.write(0x01EEE240, 0xE7)
        guest.write(0x01EEE380, guard_high)
        guest.write(0x01EEE384, ENTRY)
    guest.literal(4, psr)
    guest.emit(0xE064, 0x4580)
    guest.literal(4, RETS)
    guest.emit(0xE064, 0x4380)
    expected = list(GPRS)
    for register, value in registers.items():
        expected[register] = value & 0xFFFFFFFF
    for register, value in enumerate(expected):
        guest.literal(register, value)
    return guest, expected


def specials(psr=PSR):
    expected = [0] * 16
    expected[3], expected[5] = RETS, psr
    return expected


def inspection():
    expected = list(INITIAL_INSPECTION)
    expected[:3] = MARKERS
    return expected


def save_image(name, guest):
    image = CACHE / f"{name}.bin"
    image.write_bytes(guest.bytes() + bytes(16))
    return image


def check_success(name, state, registers, stop, count, psr=PSR):
    validate.check(state["pc"] == stop and state["instructions"] == count,
                   f"{name}: six-byte target, signed displacement or retirement differs")
    validate.check(state["registers"] == registers and state["specials"] == specials(psr),
                   f"{name}: inequality, initialized aliases, GPR, PSR or RETS differs")
    validate.check(state["inspection"] == inspection(), f"{name}: branch changed owned memory")


def branch_fixture(leftreg=0, rightreg=1, left=1, right=0, displacement=25,
                   low=0, psr=PSR, opcode=0xFF41, guard=None):
    assert -32768 <= displacement <= 32767
    # One guard permits the old four-byte span but rejects the actual third
    # word. The other permits all six branch bytes, rejecting the target's NOP.
    high = BRANCH + 3 if guard == "branch" else BRANCH + 5 if guard == "target" else None
    guest, expected = setup({leftreg: left, rightreg: right}, psr, high)
    delta = (BRANCH - (guest.pc + 4)) // 2
    guest.emit(0xEAC0 | ((delta >> 16) & 63), delta & 0xFFFF)
    guest.words.extend([0] * ((BRANCH - ENTRY) // 2 - len(guest.words)))
    before = guest.instructions
    guest.emit(opcode, (leftreg << 12) | (rightreg << 8) | low, displacement & 0xFFFF)
    a, b = expected[leftreg], expected[rightreg]
    taken = {0xFF40: a == b, 0xFF41: a != b, 0xFF42: a >= b,
             0xFF43: a < b, 0xFF48: a > b, 0xFF49: a <= b}[opcode]
    stop = (BRANCH + 6 + (displacement * 2 if taken else 0)) & 0xFFFFFFFF
    guest.words.extend([0] * max(0, (stop - ENTRY) // 2 + 4 - len(guest.words)))
    return guest, expected, stop, before


def ordinary_case(name, **settings):
    guest, expected, stop, _ = branch_fixture(**settings)
    image = save_image(name, guest)
    state = isa.compare(name, image, stop, limit=100)
    check_success(name, state, expected, stop, guest.instructions, settings.get("psr", PSR))
    return image, state


def conditional_fixture(side="then", position="final", condition=0, taken=False,
                        has_else=True, outside=False, displacement=0):
    guest, initial = setup({0: condition, 4: 5, 5: 4 if taken else 5})
    before = guest.instructions
    marker = ("marker", 6 if side == "then" else 7, 0x6666 if side == "then" else 0x7777)
    arm = [marker, ("branch",)] if position == "final" else [("branch",), marker]
    if not has_else:
        arm = [("branch",)]
    then = arm if side == "then" else [("marker", 6, 0x6666)]
    otherwise = (arm if side == "else" else [("marker", 7, 0x7777)]) if has_else else []
    guest.emit(0xEA20, ((len(then) - 1) << 14) | (len(otherwise) << 12) | 1)
    for sequence in (then, otherwise):
        for item in sequence:
            if item[0] == "marker":
                guest.literal(item[1], item[2])
            else:
                branch_index, branch_pc = len(guest.words), guest.pc
                guest.emit(0xFF41, 0x4500, displacement & 0xFFFF)
    guest.literal(8, 0x8888)
    next_if = guest.pc
    guest.emit(0xEA20, 1)
    guest.literal(9, 0x9999)
    guest.emit(0)
    expected = list(initial)
    if outside:
        assert taken and condition == (0 if side == "then" else 1)
        guest.words[branch_index + 2] = (next_if - (branch_pc + 6)) // 2
        if position == "final":
            expected[marker[1]] = marker[2]
        count = before + 2 + (position == "final")
    else:
        selected = then if condition == 0 else otherwise
        for item in selected:
            if item[0] == "marker":
                expected[item[1]] = item[2]
        expected[8] = 0x8888
        if condition == 0:
            expected[9] = 0x9999
        count = before + 1 + len(selected) + 3 + (condition == 0)
    return guest, expected, count, branch_pc, next_if, initial, before


def conditional_oracle_case(side, position, taken, has_else=True):
    condition = 1 if side == "else" else 0
    name = f"oracle-conditional-{side}-{position}-{int(taken)}-else-{int(has_else)}"
    guest, expected, count, _, _, _, _ = conditional_fixture(
        side, position, condition, taken, has_else)
    image = save_image(name, guest)
    state = isa.compare(name, image, guest.pc, limit=100)
    check_success(name, state, expected, guest.pc, count)


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


def run_settings(stop, directory=None):
    env = {key: value for key, value in os.environ.items() if not key.startswith("FM1_POC_")}
    settings = {"FM1_POC_STOP_PC": hex(stop), "FM1_POC_MAX_INSTRUCTIONS": "100"}
    if directory is not None:
        settings["FM1_POC_STATE_DIR"] = str(directory)
    env.update(settings)
    return env, settings


def independent_conditional_case(side, position, condition, taken, has_else=True, displacement=0):
    name = f"model-conditional-{side}-{position}-{condition}-{int(taken)}-else-{int(has_else)}-delta-{displacement}"
    guest, expected, count, branch_pc, _, _, _ = conditional_fixture(
        side, position, condition, taken, has_else, displacement=displacement)
    image = save_image(name, guest)
    env, settings = run_settings(guest.pc)
    command = [*validate.COMMAND, "-kernel", str(image), "-append", "diag"]
    result = subprocess.run(command, env=env, cwd=validate.ROOT,
                            capture_output=True, text=True, timeout=15)
    validate.check(result.returncode == 0, f"{name}: primary sizing model failed: {result.stderr}")
    state = json.loads(result.stdout)
    check_success(name, state, expected, guest.pc, count)
    record = {"command": command, "environment": settings, "returncode": result.returncode,
              "stderr": result.stderr, "state": state, "primary_six_byte_expectation": True}
    record.update(reference_record(image, env))
    if displacement:
        validate.check(record["reference_returncode"] != 0 and
                       f"Unsupported {{ pc: {branch_pc + 4}, word: {displacement} }}" in record["reference_stderr"],
                       f"{name}: expected independent reference four-byte skip discrepancy changed")
    else:
        validate.check(record["reference_returncode"] == 0, f"{name}: unexpected reference fatal result")
        oracle = record["reference_state"]
        fields = ("pc", "instructions", "registers", "specials", "inspection")
        record["disagreeing_fields"] = [field for field in fields if oracle[field] != state[field]]
        validate.check(record["disagreeing_fields"] and oracle["pc"] == guest.pc and
                       oracle["specials"] == specials() and oracle["inspection"] == inspection(),
                       f"{name}: recorded scanner/completion disagreement changed unexpectedly")
    record["reference_model_completion_disagreement"] = True
    record["fixture_sha256"] = hashlib.sha256(image.read_bytes()).hexdigest()
    (CACHE / f"{name}-run.json").write_text(json.dumps(record, indent=2) + "\n")
    print(f"PASS {name}: independent primary six-byte state; reference contradiction retained")


def fault_snapshot(name, image, stop, pc, count, registers, reason, size, psr=PSR):
    directory = CACHE / name
    directory.mkdir(exist_ok=True)
    for filename in ("state.json", "state.sram", "state.alnk"):
        (directory / filename).unlink(missing_ok=True)
    env, settings = run_settings(stop, directory)
    command = [*validate.COMMAND, "-kernel", str(image), "-append", "alnk-probe"]
    result = subprocess.run(command, env=env, cwd=validate.ROOT,
                            capture_output=True, text=True, timeout=15)
    state_path = directory / "state.json"
    validate.check(state_path.exists(), f"{name}: missing fault snapshot: {result.stderr}")
    state = json.loads(state_path.read_text())
    validate.check(result.returncode != 0 and state["reason"] == reason and
                   state["pc"] == pc and state["instructions"] == count,
                   f"{name}: exact fault stage, PC or retirement differs: {result.stderr}")
    validate.check(state["last_access"] == {"address": pc, "size": size, "flags": 2},
                   f"{name}: fetch address or admitted six-byte span differs")
    validate.check(state["registers"] == registers and state["specials"] == specials(psr),
                   f"{name}: fault-stage GPR, PSR, RETS or continuation differs")
    memory = (directory / "state.sram").read_bytes()
    validate.check(len(memory) == 0x80000 and
                   memory[0x8000:0x800C] == struct.pack("<III", *MARKERS),
                   f"{name}: model fault changed owned neighboring memory")
    if "guard windows" in reason:
        validate.check(state["guards"]["debug_message"] & (1 << 12), f"{name}: PC guard not latched")
    record = {"command": command, "environment": settings, "returncode": result.returncode,
              "stderr": result.stderr, "state": state,
              "fixture_sha256": hashlib.sha256(image.read_bytes()).hexdigest(),
              "hardware_fault_state_validation": False}
    return directory, env, record


def policy_fault(name, low=0, opcode=0xFF41):
    guest, expected, stop, before = branch_fixture(leftreg=15, rightreg=14,
        left=1, right=0, low=low, opcode=opcode)
    image = save_image(name, guest)
    size = 6 if opcode == 0xFF41 else 4  # Deferred neighbors keep existing scanner policy.
    directory, env, record = fault_snapshot(name, image, stop, BRANCH, before,
        expected, f"unsupported instruction 0x{opcode:04x}", size)
    record.update(reference_record(image, env))
    validate.check(record["reference_returncode"] == 0, f"{name}: expected reference-valid form did not complete")
    check_success(name + "/reference", record["reference_state"], expected, stop, guest.instructions)
    record.update(policy_scope="canonical admission or deferred exact opcode, not hardware invalidity",
                  nonzero_low_byte=low, primary_requires_zero_low_byte=True,
                  reference_valid_model_rejected=True)
    (directory / "run.json").write_text(json.dumps(record, indent=2) + "\n")
    print(f"PASS {name}: scoped pre-retirement fault; reference-valid policy difference retained")


def guard_fault(stage):
    name = f"pc-guard-{stage}"
    guest, expected, target, before = branch_fixture(guard=stage)
    image = save_image(name, guest)
    pc, count, size = (BRANCH, before, 6) if stage == "branch" else (target, before + 1, 2)
    directory, _, record = fault_snapshot(name, image, target + 2, pc, count, expected,
        "guest PC lies outside both configured guard windows", size)
    record["branch_already_retired"] = stage == "target"
    (directory / "run.json").write_text(json.dumps(record, indent=2) + "\n")
    print(f"PASS {name}: precise six-byte branch/next-fetch guard stage")


def final_then_fault(taken, outside=False):
    name = f"final-then-else-{int(taken)}-outside-{int(outside)}"
    guest, _, _, branch_pc, _, initial, before = conditional_fixture(
        "then", "final", 0, taken, outside=outside)
    expected = list(initial)
    expected[6] = 0x6666  # The preceding arm marker retired; FF41 has not.
    image = save_image(name, guest)
    directory, env, record = fault_snapshot(name, image, guest.pc, branch_pc,
        before + 2, expected, FINAL_THEN_REASON, 6)
    record.update(reference_record(image, env))
    validate.check(record["reference_returncode"] == 0 and record["reference_state"]["pc"] == guest.pc,
                   f"{name}: raw independent completion changed")
    record.update(branch_already_retired=False, final_then_family_guard=True,
                  reference_model_completion_disagreement=True,
                  limitation="final selected THEN plus ELSE rejected before FF41 retirement/branch")
    (directory / "run.json").write_text(json.dumps(record, indent=2) + "\n")
    print(f"PASS {name}: dedicated FF41 guard before branch effects; reference result retained")


def exit_followup_fault(side, position):
    condition = 0 if side == "then" else 1
    name = f"taken-exit-{side}-{position}-followup-if"
    guest, expected, count, branch_pc, next_if, _, _ = conditional_fixture(
        side, position, condition, True, outside=True)
    image = save_image(name, guest)
    directory, env, record = fault_snapshot(name, image, guest.pc, next_if, count,
        expected, "nested conditional block is unsupported", 4)
    record.update(reference_record(image, env))
    validate.check(record["reference_returncode"] == 0, f"{name}: raw reference completion failed")
    oracle_expected = list(expected)
    if condition == 0:
        oracle_expected[9] = 0x9999
    check_success(name + "/reference", record["reference_state"], oracle_expected,
                  guest.pc, count + 2 + (condition == 0))
    record.update(branch_pc=branch_pc, branch_already_retired=True,
                  limitation="taken exit retains model predicate; fault belongs to next IF",
                  reference_model_completion_disagreement=True,
                  retained_predicate_irq_blocking="source inspected; not IRQ validation")
    (directory / "run.json").write_text(json.dumps(record, indent=2) + "\n")
    print(f"PASS {name}: inherited next-IF fault after FF41 retirement; raw reference differs")


def generic_replay(image, expected):
    directory = CACHE / "generic-replay"
    directory.mkdir(exist_ok=True)
    for filename in ("state.json", "state.sram", "state.alnk"):
        (directory / filename).unlink(missing_ok=True)
    env, settings = run_settings(expected["pc"], directory)
    command = [*validate.COMMAND, "-kernel", str(image)]
    result = subprocess.run(command, env=env, cwd=validate.ROOT,
                            capture_output=True, text=True, timeout=15)
    validate.check(result.returncode == 0, f"generic-replay: {result.stderr}")
    state = json.loads((directory / "state.json").read_text())
    for field in ("pc", "instructions", "registers", "specials"):
        validate.check(state[field] == expected[field], f"generic-replay: {field} differs")
    memory = (directory / "state.sram").read_bytes()
    validate.check(memory[0x8000:0x800C] == struct.pack("<III", *MARKERS),
                   "generic-replay: branch changed owned memory")
    (directory / "run.json").write_text(json.dumps({"command": command,
        "environment": settings, "returncode": result.returncode,
        "stderr": result.stderr, "state": state}, indent=2) + "\n")
    print("PASS generic-replay: default loader matches reached FF41 register branch")


def main():
    CACHE.mkdir(parents=True, exist_ok=True)
    isa.CACHE = CACHE
    cases = []
    for reg in range(16):
        cases.extend([
            dict(name=f"fields-{reg}-{(reg + 7) % 16}", leftreg=reg, rightreg=(reg + 7) % 16,
                 left=0x80000000, right=0x7FFFFFFF),
            dict(name=f"same-register-alias-{reg}", leftreg=reg, rightreg=reg, left=0xFFFFFFFF, right=0),
        ])
    for left, right in [(0, 0), (0, 1), (1, 0), (0xFFFFFFFF, 0xFFFFFFFF),
                        (0xFFFFFFFF, 0), (0xFFFFFFFF, 0x7FFFFFFF), (0x80000000, 0x80000000),
                        (0x80000000, 0x7FFFFFFF), (0x80000001, 0x80000000), (0x12345678, 0x12345679)]:
        cases.append(dict(name=f"boundary-{left:08x}-{right:08x}", left=left, right=right))
    for displacement in (-32768, -32767, -784, -256, -1, 0, 1, 255, 256, 32766, 32767):
        for taken in (False, True):
            cases.append(dict(name=f"displacement-{displacement}-{int(taken)}", left=5,
                              right=4 if taken else 5, displacement=displacement))
    for psr in (0, 0xFFFFFFFF):
        cases.append(dict(name=f"psr-{psr:08x}", psr=psr))
    cases.append(dict(name="reached-untaken-negative-displacement", left=0, right=0, displacement=-784))
    replay = None
    for case in cases:
        result = ordinary_case(**case)
        if case["name"] == "reached-untaken-negative-displacement":
            replay = result
    generic_replay(*replay)
    for position in ("final", "nonfinal"):
        for taken in (False, True):
            conditional_oracle_case("else", position, taken)
    for taken in (False, True):
        conditional_oracle_case("then", "final", taken, has_else=False)
    # Ten balanced cases have raw scanner/completion disagreements; selected
    # final THEN+ELSE is handled separately by the exact family guard below.
    for side in ("then", "else"):
        for position in ("final", "nonfinal"):
            for condition in (0, 1):
                if (side == "then" and position == "final" and condition == 0) or (side == "else" and condition == 1):
                    continue
                for taken in (False, True):
                    independent_conditional_case(side, position, condition, taken)
    for taken in (False, True):
        independent_conditional_case("then", "final", 1, taken, has_else=False)
    independent_conditional_case("then", "final", 1, False, has_else=False, displacement=4)
    for low in (1, 2, 4, 8, 16, 32, 64, 128, 255):
        policy_fault(f"canonical-low-byte-{low:02x}", low=low)
    for opcode in (0xFF40, 0xFF42, 0xFF43, 0xFF48, 0xFF49):
        policy_fault(f"deferred-neighbor-{opcode:04x}", opcode=opcode)
    for stage in ("branch", "target"):
        guard_fault(stage)
    final_then_fault(False)
    final_then_fault(True)
    final_then_fault(True, outside=True)
    for side, position in (("then", "nonfinal"), ("else", "final"), ("else", "nonfinal")):
        exit_followup_fault(side, position)
    summary = {"passed": True, "instruction": "exact canonical FF41 six-byte register inequality",
               "ordinary_reference_cases": len(cases), "conditional_reference_cases": 6,
               "total_reference_positive_cases": len(cases) + 6,
               "independent_primary_model_positive_cases": 13, "generic_replays": 1,
               "canonical_admission_policy_faults": 9, "deferred_neighbor_faults": 5,
               "pc_guard_faults": 2, "final_then_family_guard_faults": 3,
               "inherited_followup_if_faults": 3, "total_model_faults": 22,
               "primary_blob": "622d767fceb3ad46972ae821394226ff1e6117b2",
               "low_byte_policy": "primary requires zero; reference accepts all nine tested nonzero bytes",
               "reference_scanner_limitation": "FF41 scanned as4; skipped displacement word and arm completion differ",
               "conditional_limit": "final selected THEN+ELSE guarded; other taken exits retain next-IF model fault",
               "irq_limit": "retained predicate blocks IRQ entry by source inspection; not IRQ validation",
               "pc32_wrap_validation": False, "hardware_fault_state_validation": False,
               "qemu_sha256": hashlib.sha256(validate.QEMU.read_bytes()).hexdigest()}
    (CACHE / "validation.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(f"PASS FF41: {len(cases) + 6} oracle positives, 13 independent model positives, generic and 22 faults")


if __name__ == "__main__":
    main()
