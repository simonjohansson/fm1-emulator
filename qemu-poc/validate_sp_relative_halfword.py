#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Validate exact E9D8 SP-relative unsigned halfword load/store.

Pinned Apache stack:410-415/463-468 and vendor E9D8/8149 agree: bit0
selects low16 store versus zero-extending load, offset=x&4094 is unsigned
and unscaled, high4 select an ordinary GPR independently of special SP.
All low12 bits are offset/direction data; no malformed operand mask is added.
SP/PSR/RETS remain unchanged, and loads update only the selected destination.
Both directions require aligned two-byte accesses. The actual prefix stores
capturedzero with existing E9DE/814A, then reached E9D8/8149, preserving bytes.

Reference fatal probes expose category/address/width/direction, no CPU fault
snapshot. QEMU fault-state/retirement checks are model policy, not hardware
ordering/rollback. Reference completes both configured PCguard fixtures; raw
full completions are retained separately. Protected reads are permitted, and
stores intersecting a protected second byte fault; an adjacent window remains
untouched. Wrap-to-zero probes establish address arithmetic through fatal EA;
successful wrap into mapped storage is not observed. Incoming unmapped SP
with mapped EA is tested with the existing stack guard disabled. E9D9/8000
reference completes but its exact primary constructor and further semantics
are unverified; it stays deferred, without signedness inference. E9DA/DB
rejection does not establish hardware invalidity. No helper/classifier/schema
or common predicate changes; balanced four-byte arms finish a following IF.
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
CACHE = HERE / ".cache/sp-relative-halfword-validation"
ENTRY = 0x02000120
INSPECTION = 0x01C08000
PSR = 0x89ABCDE5
RETS = 0x12345678
GPRS = [0x10203040 + i * 0x01010101 for i in range(16)]
GPRS[0] = 0x01C7FE08
MARKERS = [0x12345678, 0x89ABCDEF, 0x76543210, 0x0BADF00D]
INSPECTION_WORDS = MARKERS + [0xA5A5A5A5] + [0] * 3 + [0xA5A5A5A5] * 4
CONTINUATION = 0x33445566


def setup(registers, stack, psr=PSR, guard=None, extra_word=None, window=INSPECTION + 1):
    guest = Guest()
    for index, value in enumerate(MARKERS):
        guest.write(INSPECTION + index * 4, value)
    if extra_word is not None:
        guest.write(*extra_word)
    if guard == "write":
        # Exactly one protected byte, optionally the second byte of a halfword.
        guest.write(0x01EEE240, 0xE7)
        guest.write(0x01EEE2C0, window)
        guest.write(0x01EEE280, window)
        guest.write(0x01EEE348, 1)
    elif guard == "pc":
        guest.write(0x01EEE240, 0xE7)
        header = guest.pc + 28 + 122  # Two writes, then full-state initializers.
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

def specials(stack, psr=PSR):
    expected = [0] * 16
    expected[3], expected[5], expected[14] = RETS, psr, stack
    return expected

def save_image(name, guest):
    image = CACHE / f"{name}.bin"
    image.write_bytes(guest.bytes() + bytes(16))
    return image

def check_success(name, state, expected, inspection, guest, stack, psr=PSR, retired=None):
    validate.check(state["pc"] == guest.pc and
                   state["instructions"] == (guest.instructions if retired is None else retired),
                   f"{name}: four-byte sizing or retirement differs")
    validate.check(state["registers"] == expected and state["specials"] == specials(stack, psr),
                   f"{name}: source, special SP versus GPR14, PSR or RETS differs")
    validate.check(state["inspection"] == inspection,
                   f"{name}: unsigned halfword offset, extension/truncation or neighboring memory differs")

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

def model_fault(name, guest, expected, stack, fault_pc, before, reason, access, guard=None):
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
    validate.check(state_path.exists(), f"{name}: missing fault capture: {result.stderr}")
    state = json.loads(state_path.read_text())
    validate.check(result.returncode != 0 and state["reason"] == reason and
                   state["pc"] == fault_pc and state["instructions"] == before,
                   f"{name}: fault reason, PC or pre-access retirement differs: {result.stderr}")
    validate.check(state["last_access"] == access, f"{name}: halfword EA/direction or fetch span differs")
    validate.check(state["registers"] == expected and state["specials"] == specials(stack),
                   f"{name}: rejected instruction changed GPR/SP/PSR/RETS or continuation")
    memory = (directory / "state.sram").read_bytes()
    validate.check(len(memory) == 0x80000 and
                   memory[0x8000:0x8010] == struct.pack("<4I", *MARKERS),
                   f"{name}: rejected instruction altered halfword destination or neighbors")
    if guard == "pc":
        validate.check(state["guards"]["debug_message"] & (1 << 12), f"{name}: PC guard not latched")
    record = {"command": command, "environment": settings, "returncode": result.returncode,
              "stderr": result.stderr, "state": state,
              "fixture_sha256": hashlib.sha256(image.read_bytes()).hexdigest(),
              "hardware_fault_state_validation": False, "reference_fault_state_available": False}
    (directory / "run.json").write_text(json.dumps(record, indent=2) + "\n")
    return directory, image, env, record

def operand(register, offset, store):
    assert 0 <= register < 16 and 0 <= offset <= 4094 and offset % 2 == 0
    return (register << 12) | offset | int(store)


def changed_halfword(words, address, value):
    words = list(words)
    offset = address - INSPECTION
    shift = (offset & 2) * 8
    words[offset // 4] = (words[offset // 4] & ~(65535 << shift)) | ((value & 65535) << shift)
    return words


def success_case(name, store=True, register=8, offset=328, value=0x812345AB,
                 half=0, psr=PSR, guard=None, window=INSPECTION + 1):
    address = INSPECTION + half
    stack = (address - offset) & 0xFFFFFFFF
    inspection = list(INSPECTION_WORDS)
    extra_word = None
    if not store:
        word = ((value & 65535) << 16) | 0xA69B if half else (0xA69B << 16) | (value & 65535)
        inspection[0] = word
        extra_word = (INSPECTION, word)
    guest, expected = setup({register: value if store else 0xDEADBEEF}, stack, psr,
                            guard, extra_word, window)
    guest.emit(0xE9D8, operand(register, offset, store))
    guest.emit(0)
    if store:
        inspection = changed_halfword(inspection, address, expected[register])
    else:
        expected[register] = value & 65535
    image = save_image(name, guest)
    state = isa.compare(name, image, guest.pc, limit=100)
    check_success(name, state, expected, inspection, guest, stack, psr)


def actual_prefix():
    name = "actual-byte-halfword-prefix"
    stack = INSPECTION - 328
    guest, expected = setup({8: 0}, stack)
    guest.emit(0xE9DE, 0x814A)  # Existing byte+330 store from captured zero.
    guest.emit(0xE9D8, 0x8149)  # Reached halfword+328 store from the same GPR8.
    guest.emit(0)
    inspection = list(INSPECTION_WORDS)
    inspection[0] &= 0xFF000000  # Low halfword and following byte, high byte preserved.
    image = save_image(name, guest)
    state = isa.compare(name, image, guest.pc, limit=100)
    check_success(name, state, expected, inspection, guest, stack)
    return image, state


def boundary_case(name, address, offset, store):
    stack = (address - offset) & 0xFFFFFFFF
    word = 0x812345AB
    guest, expected = setup({8: 0xCDEF9876}, stack, extra_word=(address & ~3, word))
    guest.emit(0xE9D8, operand(8, offset, store))
    shift = (address & 2) * 8
    if store:
        word = (word & ~(65535 << shift)) | (0x9876 << shift)
    else:
        expected[8] = (word >> shift) & 65535
    # Load the whole boundary word to prove the opposite halfword is intact.
    guest.literal(7, address & ~3)
    guest.load(6, 7)
    guest.emit(0)
    expected[6], expected[7] = word, address & ~3
    image = save_image(name, guest)
    state = isa.compare(name, image, guest.pc, limit=100)
    check_success(name, state, expected, INSPECTION_WORDS, guest, stack)


def xip_read_case():
    name = "XIP-unsigned-load"
    guest, expected = setup({}, ENTRY)
    expected[8] = int.from_bytes(guest.bytes()[:2], "little")  # Own image's first halfword.
    guest.emit(0xE9D8, 0x8000)
    guest.emit(0)
    image = save_image(name, guest)
    state = isa.compare(name, image, guest.pc, limit=100)
    check_success(name, state, expected, INSPECTION_WORDS, guest, ENTRY)


def conditional_case(store, arm, position, condition):
    name = f"conditional-{int(store)}-{arm}-{position}-{condition}"
    stack = INSPECTION - 328
    guest, expected = setup({8: 0x812345EF, 4: condition, 6: 0}, stack)
    count = 1 if position == "final" else 2
    then_count, else_count = (count, 1) if arm == "then" else (1, count)
    guest.emit(0xEA24, ((then_count - 1) << 14) | (else_count << 12) | 1)
    selected = condition == 0 if arm == "then" else condition != 0
    for block in ("then", "else"):
        if block == arm:
            guest.emit(0xE9D8, operand(8, 328, store))
            if position == "nonfinal":
                guest.emit(0)
        else:
            guest.emit(0xE04D, 0x1111)
    guest.emit(0xEA26, 1)
    guest.emit(0xE04E, 0x2222)
    guest.emit(0)
    inspection = list(INSPECTION_WORDS)
    if selected:
        if store:
            inspection = changed_halfword(inspection, INSPECTION, 0x45EF)
        else:
            expected[8] = 0x5678
    else:
        expected[13] = 0x1111
    expected[14] = 0x2222  # Ordinary GPR14; special SP remains fixed.
    image = save_image(name, guest)
    state = isa.compare(name, image, guest.pc, limit=100)
    check_success(name, state, expected, inspection, guest, stack,
                  retired=guest.instructions - (1 if selected else count))


def skipped_access_case(store, stack):
    name = f"skipped-{int(store)}-{stack:08x}"
    guest, expected = setup({8: 0x812345EF, 4: 1, 6: 0}, stack)
    guest.emit(0xEA24, 1)
    guest.emit(0xE9D8, operand(8, 0, store))
    guest.emit(0xEA26, 1)
    guest.emit(0xE04E, 0x2222)
    guest.emit(0)
    expected[14] = 0x2222
    image = save_image(name, guest)
    state = isa.compare(name, image, guest.pc, limit=100)
    check_success(name, state, expected, INSPECTION_WORDS, guest, stack,
                  retired=guest.instructions - 1)


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
    inspection = MARKERS + [0] * 8  # Default loader's unowned neighbors remain zero.
    inspection[0] &= 0xFF000000
    memory = (directory / "state.sram").read_bytes()
    validate.check(struct.unpack_from("<12I", memory, 0x8000) == tuple(inspection),
                   "generic-replay: actual byte/halfword prefix or owned neighbors differ")
    (directory / "run.json").write_text(json.dumps({"command": command,
        "environment": settings, "returncode": result.returncode,
        "stderr": result.stderr, "state": state}, indent=2) + "\n")
    print("PASS generic-replay: default loader matches actual byte/halfword prefix")


def fault_case(name, store=False, stack=INSPECTION, offset=0, opcode=0xE9D8,
               guard=None, reason=None, reference_kind="access"):
    guest, expected = setup({8: 0x812345EF}, stack, guard=guard)
    fault_pc, before = guest.pc, guest.instructions
    guest.emit(opcode, operand(8, offset, store))
    address = (stack + offset) & 0xFFFFFFFF
    access = ({"address": address, "size": 2, "flags": int(store)}
              if opcode == 0xE9D8 and guard != "pc"
              else {"address": fault_pc, "size": 4, "flags": 2})
    directory, image, env, record = model_fault(name, guest, expected, stack,
        fault_pc, before, reason or f"unsupported instruction 0x{opcode:04x}", access, guard)
    record.update(reference_record(image, env))
    record.update(second_word=operand(8, offset, store), special_SP=stack,
                  effective_address=address, model_policy_only=True)
    if reference_kind in ("neighbor", "pc-policy"):
        validate.check(record["reference_returncode"] == 0,
                       f"{name}: reference-valid deferred/policy form no longer completes")
        oracle_expected = list(expected)
        oracle_expected[13] = CONTINUATION
        inspection = list(INSPECTION_WORDS)
        if reference_kind == "neighbor":
            oracle_expected[8] = 0x5678  # Only raw canonical E9D9/8000 result, no signed inference.
        elif store:
            inspection = changed_halfword(inspection, address, 0x45EF)
        else:
            oracle_expected[8] = 0x5678
        check_success(name + "/reference", record["reference_state"], oracle_expected,
                      inspection, guest, stack)
        record.update(reference_valid_model_rejected=True,
                      rejection_scope="unverified separate E9D9 opcode" if reference_kind == "neighbor"
                      else "existing QEMU instruction-fetch guard policy; reference completes")
    else:
        stderr = record["reference_stderr"]
        validate.check(record["reference_returncode"] != 0,
                       f"{name}: reference category probe unexpectedly completed")
        if reference_kind == "access":
            operation = "write" if store else "read"
            validate.check(f'address: {address}, size: 2, operation: "{operation}"' in stderr,
                           f"{name}: reference does not report computed halfword EA/width/direction")
        else:
            validate.check(f"Unsupported {{ pc: {fault_pc}, word: {opcode} }}" in stderr,
                           f"{name}: reference unsupported category differs")
    (directory / "run.json").write_text(json.dumps(record, indent=2) + "\n")
    print(f"PASS {name}: unchanged state before modeled halfword/decode fault")


def main():
    CACHE.mkdir(parents=True, exist_ok=True)
    isa.CACHE = CACHE
    cases = []
    for store in (False, True):
        for register in range(16):
            cases.append(dict(name=f"fields-{int(store)}-{register}", store=store,
                              register=register, value=0x81234500 | register))
        for offset in (0, 2, 4, 14, 16, 254, 256, 328, 510, 512, 1022, 2046, 2048, 4092, 4094):
            cases.append(dict(name=f"offset-{int(store)}-{offset}", store=store, offset=offset))
    for value in (0, 0x7FFF, 0x8000, 0xFFFF, 0x12345678, 0x812345AB):
        for half in (0, 2):
            cases.append(dict(name=f"store-{value:08x}-half{half}", value=value, half=half))
    for value in (0, 0x7FFF, 0x8000, 0xFFFF):
        for half in (0, 2):
            cases.append(dict(name=f"load-{value:04x}-half{half}", store=False, value=value, half=half))
    for store in (False, True):
        for psr in (0, 0xFFFFFFFF):
            cases.append(dict(name=f"PSR-{int(store)}-{psr:08x}", store=store, psr=psr))
    cases.append(dict(name="protected-halfword-read", store=False, guard="write", value=0x8001))
    cases.append(dict(name="adjacent-protected-byte-store", guard="write", window=INSPECTION + 2))
    for case in cases:
        success_case(**case)
    generic_replay(*actual_prefix())
    for store in (False, True):
        for name, address, offset in (("first-sram-from-unmappedSP", 0x01C00000, 4094),
                                      ("last-sram-halfword", 0x01C7FFFE, 0)):
            boundary_case(f"{name}-{int(store)}", address, offset, store)
    xip_read_case()
    for store in (False, True):
        for arm in ("then", "else"):
            for position in ("final", "nonfinal"):
                for condition in (0, 1):
                    conditional_case(store, arm, position, condition)
    for store, stack in ((False, 0x18000000), (True, 0x18000000), (True, ENTRY)):
        skipped_access_case(store, stack)
    for store in (False, True):
        for name, stack, offset, reason in (("unaligned", INSPECTION + 1, 0, "unaligned access"),
              ("unmapped", 0x18000000, 0, "unmapped access at 0x18000000"),
              ("wrapped-zero", 0xFFFFFFFE, 2, "unmapped access at 0x00000000"),
              ("wrapped-maximum-offset-zero", 0xFFFFF002, 4094, "unmapped access at 0x00000000"),
              ("past-SRAM-boundary", 0x01C80000, 0, "unmapped access at 0x01c80000")):
            fault_case(f"{name}-{int(store)}", store, stack, offset, reason=reason)
    fault_case("XIP-store", store=True, stack=ENTRY, reason="write to read-only XIP (NOR)")
    fault_case("second-byte-write-guard", store=True, guard="write",
               reason="CPU write intersects an enabled guest guard window")
    for store in (False, True):
        fault_case(f"PC-guard-{int(store)}", store, guard="pc",
                   reason="guest PC lies outside both configured guard windows", reference_kind="pc-policy")
    fault_case("unverified-reference-valid-e9d9", opcode=0xE9D9, reference_kind="neighbor")
    for opcode in (0xE9DA, 0xE9DB):
        fault_case(f"unverified-{opcode:04x}", opcode=opcode, reference_kind="unsupported")
    summary = {"passed": True, "instruction": "exact E9D8 SP-relative unsigned halfword load/store",
               "reference_positive_cases": len(cases) + 25, "ordinary_reference_cases": len(cases),
               "actual_prefix_cases": 1, "boundary_cases": 4, "XIP_read_cases": 1,
               "balanced_conditional_cases": 16, "skipped_access_cases": 3,
               "generic_replays": 1, "data_access_faults": 12, "pc_guard_faults": 2,
               "unverified_reference_valid_neighbor_faults": 1, "unverified_neighbor_faults": 2,
               "total_model_faults": 17, "reference_valid_model_fault_completions": 3,
               "reference_fatal_category_comparisons": 14,
               "primary_stack_blob": "6efa433503fa134bac981a20ebd33cdc09cfe82e",
               "all_low12_operand_bits_offset_or_direction": True,
               "load_zero_extends_store_truncates_low16": True,
               "SP_PSR_RETS_unchanged": True, "E9D9_reference_valid_unverified_deferred": True,
               "E9D9_signedness_not_established": True,
               "pc_guard_reference_completes_model_policy_difference": True,
               "successful_address_wrap_not_observed": True,
               "qemu_sha256": hashlib.sha256(validate.QEMU.read_bytes()).hexdigest(),
               "hardware_validation": False, "hardware_fault_state_validation": False,
               "reference_fault_state_available": False}
    (CACHE / "validation.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(f"PASS SP-relative halfword: {len(cases) + 25} oracle positives, generic and 17 model faults")


if __name__ == "__main__":
    main()
