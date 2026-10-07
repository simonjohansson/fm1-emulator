#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Validate canonical EE80/FFF0 signed less-or-equal register branches.

Pinned Apache progflow:286-290/slaspec:364 and vendor EE80/1004 establish
signed32 x[15:12] <= op[3:0], with signed9 word displacement from PC+4.
Second-word bits11:9 are unconstrained by primary evidence; rejecting them
preserves the existing conservative four-byte decoder admission policy, not
an ISA-invalid or hardware claim. Reference raw disagreements are retained.
Reference accepts sampled unused pattern4/xbit11; six other patterns fault.
Canonical conditional arm completion agrees for selected/skipped final and
nonfinal branches with displacement0. Taken exits beyond an arm retain the
existing model predicate state: the following IF faults after branch retirement,
while the reference completes it. Source inspection also shows retained state
blocks IRQ entry; this gate does not verify IRQ/completion behavior for exits.
No predicate instrumentation or FF0C helper changes are required. True32-bit
PC wrapping cannot be executed at the available mapped instruction addresses;
signed9 displacement encoding boundaries are exercised at mapped XIP targets.
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
CACHE = HERE / ".cache/signed-register-le-branch-validation"
ENTRY = 0x02000120
BRANCH = ENTRY + 0x500
INSPECTION = 0x01C08000
PSR = 0x89ABCDE5
RETS = 0x12345678
GPRS = [0x10203040 + i * 0x01010101 for i in range(16)]
GPRS[0] = 0x01C7FE08
MARKERS = [0x12345678, 0x89ABCDEF, 0x76543210]
INITIAL_INSPECTION = [0xA5A5A5A5] * 5 + [0] * 3 + [0xA5A5A5A5] * 4


def signed(value):
    value &= 0xFFFFFFFF
    return value if value < 0x80000000 else value - 0x100000000


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
    for register, initial in registers.items():
        expected[register] = initial & 0xFFFFFFFF
    for register, initial in enumerate(expected):
        guest.literal(register, initial)
    return guest, expected


def specials(psr):
    expected = [0] * 16
    expected[3] = RETS
    expected[5] = psr
    return expected


def inspection():
    expected = list(INITIAL_INSPECTION)
    expected[:3] = MARKERS
    return expected


def save_image(name, guest):
    image = CACHE / f"{name}.bin"
    image.write_bytes(guest.bytes() + bytes(16))
    return image


def branch_fixture(leftreg=1, rightreg=0, left=0xFFFFFFFF, right=32766,
                   displacement=4, psr=PSR, unused=0, opcode=None, guard=None):
    assert 0 <= leftreg < 16 and 0 <= rightreg < 16 and -256 <= displacement <= 255
    guard_high = BRANCH - 1 if guard == "branch" else BRANCH + 3 if guard == "target" else None
    guest, expected = setup({leftreg: left, rightreg: right}, psr, guard_high)
    # An accepted long GOTO skips padding. All signed9 destinations, including
    # -256 words, lie inside the mapped NOP padding rather than setup data.
    delta = (BRANCH - (guest.pc + 4)) // 2
    guest.emit(0xEAC0 | ((delta >> 16) & 63), delta & 0xFFFF)
    guest.words.extend([0] * ((BRANCH - ENTRY) // 2 - len(guest.words)))
    before = guest.instructions
    op = 0xEE80 | rightreg if opcode is None else opcode
    guest.emit(op, (leftreg << 12) | (unused << 9) | (displacement & 511))
    taken = signed(expected[leftreg]) <= signed(expected[rightreg])
    stop = (BRANCH + 4 + (displacement * 2 if taken else 0)) & 0xFFFFFFFF
    guest.words.extend([0] * max(0, (stop - ENTRY) // 2 + 4 - len(guest.words)))
    return guest, expected, stop, before, op


def check_success(name, state, registers, stop, count, psr=PSR):
    validate.check(state["pc"] == stop and state["instructions"] == count,
                   f"{name}: four-byte target, signed displacement or retirement differs")
    validate.check(state["registers"] == registers and state["specials"] == specials(psr),
                   f"{name}: signed comparison, fields, aliases, GPR, PSR or RETS differs")
    validate.check(state["inspection"] == inspection(), f"{name}: branch changed memory")


def success_case(name, **settings):
    guest, expected, stop, _, _ = branch_fixture(**settings)
    image = save_image(name, guest)
    state = isa.compare(name, image, stop, limit=100)
    check_success(name, state, expected, stop, guest.instructions, settings.get("psr", PSR))
    return image, state


def conditional_fixture(side, position, condition, taken, outside=False):
    left, right = (4, 5) if taken else (5, 4)
    guest, expected = setup({0: condition, 4: left, 5: right})
    header_count = guest.instructions
    marker = ("marker", 6 if side == "then" else 7, 0x6666 if side == "then" else 0x7777)
    arm = [marker, ("branch",)] if position == "final" else [("branch",), marker]
    then = arm if side == "then" else [("marker", 6, 0x6666)]
    otherwise = arm if side == "else" else [("marker", 7, 0x7777)]
    guest.emit(0xEA20, ((len(then) - 1) << 14) | (len(otherwise) << 12) | 1)
    branch_index = branch_pc = None
    for sequence in (then, otherwise):
        for item in sequence:
            if item[0] == "marker":
                guest.literal(item[1], item[2])
            else:
                branch_index, branch_pc = len(guest.words), guest.pc
                guest.emit(0xEE85, 0x4000)
    guest.literal(8, 0x8888)
    next_if = guest.pc
    guest.emit(0xEA20, 1)
    guest.literal(9, 0x9999)
    guest.emit(0x0000)
    if outside:
        displacement = (next_if - (branch_pc + 4)) // 2
        guest.words[branch_index + 1] |= displacement & 511
        # These fixtures select the branch arm and take its exit, so the
        # continuation and nonfinal marker are skipped before the next IF.
        assert taken and condition == (0 if side == "then" else 1)
        if position == "final":
            expected[marker[1]] = marker[2]
        count = header_count + 2 + (position == "final")
    else:
        selected_arm = then if condition == 0 else otherwise
        for item in selected_arm:
            if item[0] == "marker":
                expected[item[1]] = item[2]
        expected[8] = 0x8888
        count = header_count + 1 + len(selected_arm) + 3 + (condition == 0)
        if condition == 0:
            expected[9] = 0x9999
    return guest, expected, count, branch_pc, next_if


def conditional_case(side, position, condition, taken):
    name = f"conditional-{side}-{position}-{condition}-{int(taken)}"
    guest, expected, count, _, _ = conditional_fixture(side, position, condition, taken)
    image = save_image(name, guest)
    state = isa.compare(name, image, guest.pc, limit=100)
    check_success(name, state, expected, guest.pc, count)


def fault_snapshot(name, image, stop, pc, count, registers, reason, size, psr=PSR):
    directory = CACHE / name
    directory.mkdir(exist_ok=True)
    for filename in ("state.json", "state.sram", "state.alnk"):
        (directory / filename).unlink(missing_ok=True)
    env = {key: value for key, value in os.environ.items() if not key.startswith("FM1_POC_")}
    settings = {"FM1_POC_STOP_PC": hex(stop), "FM1_POC_MAX_INSTRUCTIONS": "100",
                "FM1_POC_STATE_DIR": str(directory)}
    env.update(settings)
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
                   f"{name}: instruction fetch address/span differs")
    validate.check(state["registers"] == registers and state["specials"] == specials(psr),
                   f"{name}: fault-stage GPR, PSR, RETS or continuation differs")
    memory = (directory / "state.sram").read_bytes()
    validate.check(len(memory) == 0x80000 and
                   memory[0x8000:0x800C] == struct.pack("<III", *MARKERS),
                   f"{name}: model fault altered memory")
    if "guard window" in reason:
        validate.check(state["guards"]["debug_message"] == 1 << 12,
                       f"{name}: established PC guard status differs")
    record = {"command": command, "environment": settings,
              "fixture_sha256": hashlib.sha256(image.read_bytes()).hexdigest(),
              "returncode": result.returncode, "stderr": result.stderr, "state": state,
              "hardware_fault_state_validation": False}
    return directory, env, record


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


def policy_fault(name, unused=0, opcode=None):
    guest, expected, stop, before, op = branch_fixture(leftreg=15, rightreg=14,
        left=0, right=1, unused=unused, opcode=opcode)
    image = save_image(name, guest)
    directory, env, record = fault_snapshot(name, image, stop, BRANCH, before, expected,
                                           f"unsupported instruction 0x{op:04x}", 4)
    record["policy_scope"] = "canonical decoder admission, not a hardware invalid-form claim"
    if unused:
        # Primary leaves these bits unused. Preserve raw oracle behavior
        # without treating its acceptance/rejection as a hardware contract.
        record.update(reference_record(image, env))
        record["unused_second_word_bits_11_9"] = unused
    (directory / "run.json").write_text(json.dumps(record, indent=2) + "\n")
    print(f"PASS {name}: conservative admission fault before branch retirement")


def guard_fault(stage):
    name = f"pc-guard-{stage}"
    guest, expected, target, before, _ = branch_fixture(leftreg=15, rightreg=14,
                                                       left=0, right=1, guard=stage)
    image = save_image(name, guest)
    pc, count, size = (BRANCH, before, 4) if stage == "branch" else (target, before + 1, 2)
    # Target fetch must execute its guard check rather than the checkpoint
    # helper; the mapped target and following NOP are both outside the window.
    stop = target + 2
    directory, _, record = fault_snapshot(name, image, stop, pc, count, expected,
        "guest PC lies outside both configured guard windows", size)
    record["branch_already_retired"] = stage == "target"
    (directory / "run.json").write_text(json.dumps(record, indent=2) + "\n")
    print(f"PASS {name}: instruction guard at the asserted retirement stage")


def exit_followup_fault(side, position):
    condition = 0 if side == "then" else 1
    name = f"taken-exit-{side}-{position}-followup-if"
    guest, expected, count, branch_pc, next_if = conditional_fixture(
        side, position, condition, True, outside=True)
    image = save_image(name, guest)
    directory, env, record = fault_snapshot(name, image, guest.pc, next_if, count, expected,
                                           "nested conditional block is unsupported", 4)
    record.update(reference_record(image, env))
    validate.check(record["reference_returncode"] == 0,
                   f"{name}: independent reference completion failed: {record['reference_stderr']}")
    oracle_expected = list(expected)
    if condition == 0:
        oracle_expected[9] = 0x9999
    oracle_count = count + 2 + (condition == 0)  # Follow-up IF, selected body and NOP.
    check_success(name + "/reference", record["reference_state"], oracle_expected, guest.pc, oracle_count)
    record.update(branch_pc=branch_pc, branch_already_retired=True,
                  limitation="taken exit retains model predicate; fault belongs to following IF",
                  reference_model_completion_disagreement=True,
                  retained_predicate_irq_blocking="source inspected; not IRQ validation")
    (directory / "run.json").write_text(json.dumps(record, indent=2) + "\n")
    print(f"PASS {name}: inherited follow-up IF fault after branch; reference disagreement retained")


def generic_replay(image, expected):
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
    memory = (directory / "state.sram").read_bytes()
    validate.check(len(memory) == 0x80000 and
                   memory[0x8000:0x800C] == struct.pack("<III", *MARKERS),
                   "generic-replay: branch altered neighboring memory")
    (directory / "run.json").write_text(json.dumps({"command": command,
        "environment": settings, "returncode": result.returncode,
        "stderr": result.stderr, "state": state}, indent=2) + "\n")
    print("PASS generic-replay: default application mode matches signed register branch")


def main():
    CACHE.mkdir(parents=True, exist_ok=True)
    isa.CACHE = CACHE
    cases = []
    for register in range(16):
        cases.extend([
            dict(name=f"left-{register}-right-{(register + 1) % 16}", leftreg=register,
                 rightreg=(register + 1) % 16, left=0x80000000, right=0x7FFFFFFF),
            dict(name=f"equal-alias-{register}", leftreg=register, rightreg=register,
                 left=0x80000000, right=0x80000000),
        ])
    for left, right in ((0, 0), (1, 0), (0, 1), (0xFFFFFFFF, 0), (0, 0xFFFFFFFF),
                        (0x7FFFFFFF, 0x80000000), (0x80000000, 0x7FFFFFFF),
                        (0x80000000, 0xFFFFFFFF), (0xFFFFFFFF, 0x80000000),
                        (0x80000000, 0x80000000), (0x7FFFFFFF, 0x7FFFFFFF)):
        cases.append(dict(name=f"signed-{left:08x}-{right:08x}", leftreg=15, rightreg=14,
                          left=left, right=right))
    for displacement in (-256, -255, -1, 0, 1, 25, 254, 255):
        cases.append(dict(name=f"displacement-{displacement}", leftreg=15, rightreg=14,
                          left=0, right=1, displacement=displacement))
    for psr in (0, 0xFFFFFFFF):
        cases.append(dict(name=f"psr-{psr:08x}", psr=psr))
    cases.append(dict(name="reached-fields-signed-1004"))
    replay = None
    for case in cases:
        result = success_case(**case)
        if case["name"] == "reached-fields-signed-1004":
            replay = result
    for side in ("then", "else"):
        for position in ("final", "nonfinal"):
            for condition in (0, 1):
                for taken in (False, True):
                    conditional_case(side, position, condition, taken)
    generic_replay(*replay)
    for unused in range(1, 8):
        policy_fault(f"canonical-admission-unused-{unused}", unused=unused)
    for opcode in (0xED0E, 0xED8E):
        policy_fault(f"deferred-family-{opcode:04x}", opcode=opcode)
    for stage in ("branch", "target"):
        guard_fault(stage)
    for side in ("then", "else"):
        for position in ("final", "nonfinal"):
            exit_followup_fault(side, position)
    summary = {"passed": True, "instruction": "canonical EE80/FFF0 signed register less-or-equal",
               "ordinary_reference_cases": len(cases), "conditional_reference_cases": 16,
               "generic_replays": 1, "canonical_admission_policy_faults": 7,
               "deferred_family_faults": 2, "pc_guard_faults": 2,
               "inherited_followup_if_faults": 4, "total_model_faults": 15,
               "primary_blob": "622d767fceb3ad46972ae821394226ff1e6117b2",
               "unused_bits_policy": "bits11:9 unconstrained by primary; canonical decoder requires zero",
               "reference_unused_pattern4": "one accepted pattern value4/xbit11; six other sampled patterns rejected",
               "conditional_limit": "taken exits followed by IF retain model predicate; oracle differs",
               "irq_limit": "retained predicate blocks IRQ entry by source inspection; not validated here",
               "pc32_wrap_validation": False, "hardware_fault_state_validation": False}
    (CACHE / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print("PASS signed LE register branch: canonical semantics and inherited model limits recorded")


if __name__ == "__main__":
    main()
