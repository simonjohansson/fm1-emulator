#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Validate exact E86C kind0 word memory left shifts.

Vendor E86C/3704 at0x02003aac says [r3+4]<<=7. The pinned Apache
pi32v2.slaspec and all11 included instruction files have no exact memory-shift
constructor; their RMW and register-shift forms are analogues only. Vendor
and a separate executable establish base12:15, immediate count8:11 (0..15),
unsigned aligned byte offset2:7 and low2 mode0. Count0 is an identity transform
that still reads and writes. Results wrap32; registers, PSR and RETS are fixed.
The right modes2/3 are reference-valid but deferred, with full successful
reference completions saved. Mode1 reference rejection is not hardware proof.
F86C is an existing literal branch, not a deferred parallel-shift negative.

Fault checks retain the existing model's aligned word read-before-write and
no-retirement policy. Failed reads precede the store; write guards and XIP
reject the write after a permitted read. Reference fatal access errors expose
address/width/direction, not CPU fault state. Failed writes do not roll back
prior MMIO read side effects; no atomicity or hardware fault claim is made.
The branch-local I/O fence preserves the current instruction boundary without
changing helpers, earlier RMW forms, classifiers, devices or state schemas.
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
CACHE = HERE / ".cache/memory-left-shift-validation"
ENTRY = 0x02000120
INSPECTION = 0x01C08000
PSR = 0x89ABCDE5
RETS = 0x12345678
STACK = INSPECTION - 16
GPRS = [0x10203040 + i * 0x01010101 for i in range(16)]
GPRS[0] = 0x01C7FE08
INITIAL_INSPECTION = [0xA5A5A5A5] * 5 + [0] * 3 + [0xA5A5A5A5] * 4
CONTINUATION = 0x33445566


def operand(base, count, offset, mode=0):
    assert 0 <= base < 16 and 0 <= count < 16
    assert offset % 4 == 0 and 0 <= offset <= 252 and 0 <= mode < 4
    return (base << 12) | (count << 8) | offset | mode


def shifted(value, count, mode=0):
    if mode == 0:
        return (value << count) & 0xFFFFFFFF
    if mode == 2:
        return value >> count
    assert mode == 3
    signed = value if value < 0x80000000 else value - 0x100000000
    return (signed >> count) & 0xFFFFFFFF


def specials(psr=PSR):
    expected = [0] * 16
    expected[3], expected[5], expected[14] = RETS, psr, STACK
    return expected


def inspection(markers):
    result = list(INITIAL_INSPECTION)
    result[:4] = markers[1:]
    return result


def setup(registers, value=0x89ABCDEF, address=INSPECTION + 4, psr=PSR,
          guard=None, initialize_target=True, usb=False):
    guest = Guest()
    markers = [0x12345678, 0x81234567, 0x89ABCDEF, 0x7FFFFFFF, 0x55AA55AA]
    if initialize_target and INSPECTION - 4 <= address <= INSPECTION + 12:
        markers[(address - INSPECTION + 4) // 4] = value
    for index, marker in enumerate(markers):
        guest.write(INSPECTION - 4 + index * 4, marker)
    if initialize_target and not INSPECTION - 4 <= address <= INSPECTION + 12:
        guest.write(address, value)
    if usb:
        guest.write(0x011800, 4)
        guest.write(0x011804, 0x160)
    if guard == "write":
        guest.write(0x01EEE240, 0xE7)
        # A one-byte guard strictly inside the target tests word intersection.
        guest.write(0x01EEE2C0, address + 1)
        guest.write(0x01EEE280, address + 1)
        guest.write(0x01EEE348, 1)
    elif guard == "pc":
        guest.write(0x01EEE240, 0xE7)
        guest.write(0x01EEE380, 0xFFFFFFFF)
        guest.write(0x01EEE384, ENTRY)
    guest.literal(4, psr)
    guest.emit(0xE064, 0x4580)
    guest.literal(4, RETS)
    guest.emit(0xE064, 0x4380)
    guest.literal(14, STACK, special=True)
    expected = list(GPRS)
    for register, value in registers.items():
        expected[register] = value & 0xFFFFFFFF
    for register, value in enumerate(expected):
        guest.literal(register, value)
    if guard == "pc":
        # The three following setup instructions fit inside this inclusive
        # window; the complete four-byte shift fetch lies outside it.
        expected[0] = guest.pc + 14 - 1
        expected[1] = 0x01EEE380
        guest.literal(0, expected[0])
        guest.literal(1, expected[1])
        guest.store(0, 1)
    return guest, expected, markers


def save_image(name, guest):
    image = CACHE / f"{name}.bin"
    image.write_bytes(guest.bytes() + bytes(16))
    return image


def check_success(name, state, expected, pc, retired, markers, psr=PSR):
    validate.check(state["pc"] == pc and state["instructions"] == retired,
                   f"{name}: four-byte sizing or retirement differs")
    validate.check(state["registers"] == expected and state["specials"] == specials(psr),
                   f"{name}: base, immediate-count register, continuation, PSR or RETS differs")
    validate.check(state["inspection"] == inspection(markers),
                   f"{name}: shifted word, unsigned offset, truncation or neighbors differ")


def success_case(name, base=3, count=7, offset=4, value=0x89ABCDEF,
                 address=INSPECTION + 4, psr=PSR, condition=None,
                 initialize_target=True, capture=False):
    incoming = {base: (address - offset) & 0xFFFFFFFF}
    if condition is not None:
        incoming[4], incoming[12] = condition, 0
    if capture:
        incoming[12] = address
    guest, expected, markers = setup(incoming, value, address, psr,
                                    initialize_target=initialize_target)
    validate.check((expected[base] + offset) & 0xFFFFFFFF == address,
                   f"{name}: initialized incoming base differs")
    if condition is not None:
        guest.emit(0xEA24, 1)
    guest.emit(0xE86C, operand(base, count, offset))
    selected = condition is None or condition == 0
    result = shifted(value, count)
    if selected and INSPECTION - 4 <= address <= INSPECTION + 12:
        markers[(address - INSPECTION + 4) // 4] = result
    if capture:
        guest.emit(0xECD0, 0xE0C0)  # r14=[incoming r12], accepted word load.
        expected[14] = result
    if condition is not None:
        # A second independent IF verifies the first selected/skipped arm
        # closes at the shift's four-byte sequential boundary.
        guest.emit(0xEA2C, 1)
        guest.literal(13, CONTINUATION)
        expected[13] = CONTINUATION
    guest.emit(0)
    image = save_image(name, guest)
    state = isa.compare(name, image, guest.pc, limit=100)
    check_success(name, state, expected, guest.pc,
                  guest.instructions - (not selected), markers, psr)
    return image, state, markers


def actual_store_and_shift():
    name = "actual-adjacent-store-and-reached-shift"
    guest, expected, markers = setup({2: 0x76543210, 3: INSPECTION})
    guest.emit(0x60B2)  # [r3+0]=incoming r2, immediately before the shift.
    guest.emit(0xE86C, 0x3704)
    guest.emit(0)
    markers[1], markers[2] = expected[2], shifted(markers[2], 7)
    image = save_image(name, guest)
    state = isa.compare(name, image, guest.pc, limit=100)
    check_success(name, state, expected, guest.pc, guest.instructions, markers)


def fault_fixture(name, address=INSPECTION + 4, count=7, offset=4, mode=0, guard=None):
    # Seed only mapped SRAM markers; invalid data addresses and immutable XIP
    # are never written by setup. Guard initialization happens after seeding.
    guest, expected, markers = setup({15: (address - offset) & 0xFFFFFFFF},
                                    address=INSPECTION + 4, guard=guard)
    fault_pc, before = guest.pc, guest.instructions
    guest.emit(0xE86C, operand(15, count, offset, mode))
    guest.literal(13, CONTINUATION)
    image = save_image(name, guest)
    return guest, expected, markers, image, fault_pc, before


def fault_case(name, reason, address=INSPECTION + 4, count=7, offset=4,
               mode=0, guard=None, stage="read", reference="access"):
    guest, expected, markers, image, fault_pc, before = fault_fixture(
        name, address, count, offset, mode, guard)
    directory = CACHE / name
    directory.mkdir(exist_ok=True)
    for filename in ("state.json", "state.sram", "state.alnk"):
        (directory / filename).unlink(missing_ok=True)
    settings = {"FM1_POC_STOP_PC": hex(guest.pc), "FM1_POC_MAX_INSTRUCTIONS": "100",
                "FM1_POC_STATE_DIR": str(directory)}
    env = {key: value for key, value in os.environ.items() if not key.startswith("FM1_POC_")}
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
              "model_fault_stage": stage, "hardware_fault_state_validation": False}
    validate.check(result.returncode != 0 and state["reason"] == reason and
                   state["pc"] == fault_pc and state["instructions"] == before,
                   f"{name}: fault reason, PC or retirement stage differs: {result.stderr}")
    access = {"address": address, "size": 4, "flags": int(stage == "write")}
    if stage == "fetch":
        access = {"address": fault_pc, "size": 4, "flags": 2}
    validate.check(state["last_access"] == access,
                   f"{name}: rejected read/write address, width or pre-access fetch differs")
    validate.check(state["registers"] == expected and state["specials"] == specials(),
                   f"{name}: failed RMW changed GPRs, PSR, RETS or continuation")
    memory = (directory / "state.sram").read_bytes()
    validate.check(len(memory) == 0x80000 and
                   memory[0x7FFC:0x8010] == struct.pack("<IIIII", *markers),
                   f"{name}: failed RMW changed target or neighboring SRAM words")
    if guard:
        bit = 12 if guard == "pc" else 13
        validate.check(state["guards"]["debug_message"] == 1 << bit,
                       f"{name}: established PC/write guard fault status differs")
    if reference:
        reference_command = ["mise", "exec", "--", "cargo", "run", "--manifest-path",
                             str(HERE / "reference/Cargo.toml"), "--locked", "--offline",
                             "--", "snapshot", str(image)]
        oracle_result = subprocess.run(reference_command, env=env, cwd=validate.ROOT,
                                       capture_output=True, text=True, timeout=60)
        record.update(reference_command=reference_command,
                      reference_returncode=oracle_result.returncode,
                      reference_stdout=oracle_result.stdout, reference_stderr=oracle_result.stderr)
        if reference == "valid":
            validate.check(mode in (2, 3) and oracle_result.returncode == 0,
                           f"{name}: deferred right-shift reference completion failed: {oracle_result.stderr}")
            oracle = json.loads(oracle_result.stdout)
            oracle_expected = list(expected)
            oracle_expected[13] = CONTINUATION
            oracle_markers = list(markers)
            index = (address - INSPECTION + 4) // 4
            oracle_markers[index] = shifted(oracle_markers[index], count, mode)
            check_success(name + "/reference", oracle, oracle_expected, guest.pc,
                          before + 2, oracle_markers)
            record.update(reference_state=oracle, deferred_reference_valid_mode=mode)
        else:
            validate.check(oracle_result.returncode != 0 and not oracle_result.stdout.strip() and
                           f"pc: {fault_pc}" in oracle_result.stderr,
                           f"{name}: reference fatal error PC differs: {oracle_result.stderr}")
            if reference == "access":
                direction = "write" if stage == "write" else "read"
                validate.check(f'address: {address}, size: 4, operation: "{direction}"' in oracle_result.stderr,
                               f"{name}: reference fatal error address/width/direction differs")
            else:
                validate.check("Unsupported" in oracle_result.stderr and
                               "word: 59500" in oracle_result.stderr,
                               f"{name}: reference mode1 rejection differs")
            record["reference_fault_state_available"] = False
    (directory / "run.json").write_text(json.dumps(record, indent=2) + "\n")
    print(f"PASS {name}: modeled {stage} stage, no GPR/PSR/retirement effect")


def modeled_mmio_identity():
    name = "modeled-mmio-identity-read-and-repost-once"
    guest, expected, markers = setup({15: 0x011804}, usb=True)
    guest.emit(0xE86C, operand(15, 0, 0))
    guest.literal(13, CONTINUATION)
    guest.emit(0)
    expected[13] = CONTINUATION
    image = save_image(name, guest)
    settings = {"FM1_POC_STOP_PC": hex(guest.pc), "FM1_POC_MAX_INSTRUCTIONS": "100"}
    env = {key: value for key, value in os.environ.items() if not key.startswith("FM1_POC_")}
    env.update(settings)
    command = [*validate.COMMAND, "-kernel", str(image), "-append", "diag"]
    result = subprocess.run(command, env=env, cwd=validate.ROOT,
                            capture_output=True, text=True, timeout=15)
    validate.check(result.returncode == 0, f"{name}: {result.stderr}")
    state = json.loads(result.stdout)
    check_success(name, state, expected, guest.pc, guest.instructions, markers)
    # Existing cold USB model counters make a duplicated RMW read observable:
    # one poll, then the unchanged request is abandoned and reposted once.
    # This is a QEMU device-model check, without reference/hardware equivalence.
    expected_usb = {"host_connected": False, "sie_clock_available": False,
                    "control": 4, "pads": 0, "requests": 2, "poll_reads": 1,
                    "abandoned_requests": 1, "dma_packets": 0,
                    "recent_requests": [0x160, 0x160, 0, 0, 0, 0],
                    "recent_polls": [1, 0, 0, 0, 0, 0]}
    validate.check(state["usb"] == expected_usb,
                   f"{name}: missing/replayed MMIO read or identity write differs")
    (CACHE / f"{name}.json").write_text(json.dumps({"command": command,
        "environment": settings, "fixture_sha256": hashlib.sha256(image.read_bytes()).hexdigest(),
        "returncode": result.returncode, "stderr": result.stderr, "state": state,
        "model_only": True, "hardware_or_reference_equivalence": False}, indent=2) + "\n")
    print(f"PASS {name}: existing USB model observes one read and one identity repost")


def generic_replay(image, expected, markers):
    directory = CACHE / "generic-replay"
    directory.mkdir(exist_ok=True)
    for filename in ("state.json", "state.sram", "state.alnk"):
        (directory / filename).unlink(missing_ok=True)
    settings = {"FM1_POC_STOP_PC": hex(expected["pc"]), "FM1_POC_MAX_INSTRUCTIONS": "100",
                "FM1_POC_STATE_DIR": str(directory)}
    env = {key: value for key, value in os.environ.items() if not key.startswith("FM1_POC_")}
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
                   memory[0x7FFC:0x8010] == struct.pack("<IIIII", *markers),
                   "generic-replay: shifted word or neighboring SRAM differs")
    (directory / "run.json").write_text(json.dumps({"command": command,
        "environment": settings, "returncode": result.returncode,
        "stderr": result.stderr, "state": state}, indent=2) + "\n")
    print("PASS generic-replay: default loader matches RMW shift architecture and memory")


def main():
    CACHE.mkdir(parents=True, exist_ok=True)
    isa.CACHE = CACHE
    cases = []
    for value in (0, 1, 0x7FFFFFFF, 0x80000000, 0xFFFFFFFF, 0x89ABCDEF):
        for count in range(16):
            cases.append(dict(name=f"value-{value:08x}-count-{count}", value=value, count=count))
    for base in range(16):
        # The encoded count equals the base-register number while that GPR
        # contains a pointer; this distinguishes immediate from register count.
        cases.append(dict(name=f"base-field-{base}", base=base, count=base))
    for offset in range(0, 253, 4):
        cases.append(dict(name=f"offset-{offset}", offset=offset))
    for psr in (0, 0xFFFFFFFF):
        cases.append(dict(name=f"psr-{psr:08x}", psr=psr))
    for condition in (0, 1):
        for count in (0, 7, 15):
            cases.append(dict(name=f"conditional-{condition}-count-{count}",
                              condition=condition, count=count))
    cases.extend([
        dict(name="skipped-unmapped-address", address=0, condition=1, initialize_target=False),
        dict(name="incoming-base-outside-sram", base=15, address=0x01C00000,
             offset=4, capture=True),
        dict(name="last-sram-word", base=15, address=0x01C7FFFC,
             offset=252, count=15, value=0xFFFFFFFF, capture=True),
        dict(name="last-sram-word-identity", base=15, address=0x01C7FFFC,
             offset=0, count=0, capture=True),
    ])
    replay = None
    for case in cases:
        result = success_case(**case)
        if case["name"] == "value-89abcdef-count-7":
            replay = result
    actual_store_and_shift()
    generic_replay(*replay)
    modeled_mmio_identity()
    for count in (0, 7):
        fault_case(f"unaligned-read-count-{count}", "unaligned access",
                   address=INSPECTION + 1, count=count)
    fault_case("unmapped-read", "unmapped access at 0x18000000", address=0x18000000)
    fault_case("wrapping-base-read", "unmapped access at 0x00000000", address=0)
    for count in (0, 7):
        fault_case(f"read-only-xip-write-count-{count}", "write to read-only XIP (NOR)",
                   address=ENTRY, count=count, stage="write")
        fault_case(f"write-guard-count-{count}", "CPU write intersects an enabled guest guard window",
                   count=count, guard="write", stage="write")
    fault_case("pc-guard-before-read", "guest PC lies outside both configured guard windows",
               guard="pc", stage="fetch", reference=None)
    for mode in (1, 2, 3):
        fault_case(f"deferred-mode-{mode}", "unsupported instruction 0xe86c", mode=mode,
                   count=15, stage="fetch", reference="valid" if mode in (2, 3) else "unsupported")
    fault_case("mode1-precheck-before-unmapped-read", "unsupported instruction 0xe86c",
               address=0x18000000, mode=1, stage="fetch", reference="unsupported")
    summary = {"passed": True, "instruction": "E86C kind0 word memory left shift",
               "positive_reference_cases": len(cases) + 1, "actual_adjacent_store_cases": 1,
               "generic_replays": 1, "modeled_mmio_cases": 1, "fault_cases": 13, "data_read_faults": 4,
               "data_write_faults": 4, "pc_guard_faults": 1, "mode_precheck_faults": 4,
               "reference_valid_deferred_modes": [2, 3], "reference_fault_state_available": False,
               "hardware_fault_state_validation": False, "exact_primary_constructor_available": False,
               "encoding_authority": "direct vendor E86C3704 and separate executable probes",
               "fault_policy": "aligned read before write; zero count still reads and writes; no retirement on fault",
               "mmio_scope": "failed write does not roll back earlier MMIO read side effects; no hardware atomicity claim",
               "preserved": "all GPRs, PSR, RETS, older RMW modes, helpers and classifiers"}
    (CACHE / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print("PASS memory left shift: immediate counts, offsets, identity access, model fault stages and generic replay")


if __name__ == "__main__":
    main()
