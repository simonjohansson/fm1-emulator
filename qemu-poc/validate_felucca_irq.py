#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Private nonnested IRQ11/63 gates with actual guest save/ack/RTI execution.

The wrapper opcodes match the selected unchanged Felucca artifact. Their CALLs
are relocated to disposable probe callees with the common RETS+r15..r4 save
frame, rather than the unchanged firmware C handlers or their local frames.
These gates use ALNK interface facts; the existing TIMER5/reference regression
remains a separate oracle and no Rust implementation is imported here.
"""
import hashlib
import json
import os
from pathlib import Path
import struct
import subprocess

import validate
from validate_felucca_devices import ALNK, DeviceGuest, configuration

HERE = Path(__file__).resolve().parent
CACHE = HERE / ".cache/felucca-irq-validation"
FELUCCA = Path("/Users/simonjohansson/src/Felucca/build/felucca.bin")
FELUCCA_SHA = "12a4b4ea47248467f566ec3b6984b08f2f89d6ef5a8e9e494cab5184e8fadb36"
ENTRY = 0x02000120
RAM = 0x01c00000
VECTOR = 0x01c7fe00
IRQ_CONFIG = 0x01eef100
PRIORITY_MASK = IRQ_CONFIG + 0xa8
TIMER5 = 0x10900
LOG = 0x01c06000
RECORD = {11: LOG + 0x40, 63: LOG + 0x80}
SP = 0x01c70000
SSP = 0x01c78000
PSR = 0x89abcde5
RETS = 0x12345678
GPRS = [0x81230000 + reg * 0x10101 for reg in range(16)]
STATUS_SPRS = [14, 13, 12, 3, 5, 0, 11]


def set_special(guest, special, value):
    guest.literal(0, value)
    guest.emit(0xe064, (special << 8) | 0x80)


def store_r0(guest, address):
    guest.literal(1, address)
    guest.store(0, 1)


def wait_timer(guest):
    guest.literal(1, TIMER5)
    guest.literal(2, 0x8000)
    loop = guest.pc
    guest.load(3, 1)
    guest.emit(0x19a3)  # r3 &= r2
    guest.branch_zero(3, loop)
    # Stop further expirations without acknowledging the existing latch.
    guest.write(TIMER5, 8)


def capture(guest, stage, enable):
    foreground_sp = SP - stage * 0x100
    guest.literal(14, foreground_sp, special=True)
    set_special(guest, 3, RETS)
    guest.literal(0, 0xaabbccdd, special=True)
    set_special(guest, 5, PSR)
    set_special(guest, 11, 0x100)
    for reg, value in enumerate(GPRS):
        guest.literal(reg, value)
    guest.emit(0x0020)  # CSYNC, followed by the real global STI gate.
    if enable:
        guest.emit(0x0061)
    resume = guest.pc
    # Both pending sources may run before this first resumed instruction.
    # Save/pop all registers on the foreground stack, leaving evidence in RAM.
    guest.emit(0xe8d8, 0xffff)
    guest.emit(0xe8d4, 0xffff)
    guest.emit(0x0060)
    status = LOG + 0x100 + stage * 0x40
    for index, special in enumerate(STATUS_SPRS):
        guest.emit(0xe064, special << 8)  # r0 = special
        store_r0(guest, status + index * 4)
    return {"sp": foreground_sp, "resume": resume, "status": status}


def call_at(guest, index, target):
    next_pc = guest.base + (index + 2) * 2
    displacement = (target - next_pc) // 2
    guest.words[index:index + 2] = [0xea80 | ((displacement >> 16) & 63),
                                   displacement & 0xffff]


def append_handler(guest, source):
    wrapper = guest.pc
    # Actual Felucca wrapper ABI: PSR/RETS/RETI, r3..r0, CALL, reverse restore.
    guest.emit(0x04e9)
    guest.emit(0x0460)
    call = len(guest.words)
    guest.emit(0, 0)
    guest.emit(0x0440)
    guest.emit(0x04a9)
    guest.emit(0x0020)
    guest.emit(0x0081)
    body = guest.pc
    call_at(guest, call, body)
    guest.emit(0x047f)  # Common C frame: RETS and r15..r4, 52 bytes.
    guest.literal(1, LOG)
    guest.load(0, 1)
    store_r0(guest, RECORD[source])
    guest.add(0, 1)
    store_r0(guest, LOG)
    for index, special in enumerate([11, 14, 0], start=1):
        guest.emit(0xe064, special << 8)
        store_r0(guest, RECORD[source] + index * 4)
    guest.read_width(ALNK + 8, 0, 1)
    store_r0(guest, RECORD[source] + 16)
    guest.read_width(TIMER5, 0, 4)
    store_r0(guest, RECORD[source] + 20)
    if source == 11:
        guest.write_width(ALNK + 8, 8, 1)
        guest.write_width(ALNK, 0x180, 2)
    else:
        guest.write(TIMER5, 0x4008)
    guest.read_width(ALNK + 8, 0, 1)
    store_r0(guest, RECORD[source] + 24)
    guest.read_width(TIMER5, 0, 4)
    store_r0(guest, RECORD[source] + 28)
    for reg in range(16):
        guest.literal(reg, 0xde000000 + reg)
    guest.literal(0, 0xdeadfa11, special=True)
    set_special(guest, 3, 0xdeadfa13)
    set_special(guest, 5, 0xdeadbeef)
    guest.emit(0x045f)  # Return through saved call link; wrapper restores RETS.
    return wrapper


def fixture(audio=True, timer=True, mask=0, audio_enabled=True,
            release=None, global_enable=True, equal_priority=False):
    guest = DeviceGuest()
    patches = {}
    for source in [11, 63]:
        patches[source] = len(guest.words) + 4
        guest.write(VECTOR + source * 4, 0)
    guest.literal(13, SSP, special=True)
    for address, value in [(SSP, 0xfeedface), (SSP - 84, 0xc001cafe),
                           (SP, 0xfacefeed), (SP - 68, 0xcafec001),
                           (SP - 0x100, 0xfacefeed), (SP - 0x100 - 68, 0xcafec001)]:
        guest.write(address, value)
    if audio:
        configuration(guest)
    audio_config = (3 if equal_priority else 7) if audio_enabled else 6
    guest.write(IRQ_CONFIG + 4, (audio_config << 12) if audio else 0)
    guest.write(IRQ_CONFIG + 0x1c, 0x30000000 if timer else 0)
    guest.write(PRIORITY_MASK, mask)
    if timer:
        guest.write(TIMER5, 8)
        guest.write(TIMER5 + 8, 24000)
        guest.write(TIMER5 + 4, 0)
    if audio:
        guest.write_width(ALNK, 0x980, 2)
    if timer:
        guest.write(TIMER5, 9)
        wait_timer(guest)
    if audio:
        guest.wait_half(1)
    stages = [capture(guest, 0, global_enable)]
    if release == "mask":
        guest.write(PRIORITY_MASK, 1)
        stages.append(capture(guest, 1, True))
    elif release == "source":
        guest.write(IRQ_CONFIG + 4, 7 << 12)
        stages.append(capture(guest, 1, True))
    stop = guest.pc
    guest.words.extend([0] * 8)
    wrappers = {source: append_handler(guest, source) for source in [11, 63]}
    for source, index in patches.items():
        address = wrappers[source]
        guest.words[index:index + 2] = [address & 0xffff, address >> 16]
    return guest, stop, stages, wrappers


def run(name, guest, stop, error=None, limit=4000000):
    directory = CACHE / name
    directory.mkdir(parents=True, exist_ok=True)
    for filename in ["state.json", "state.sram", "state.alnk"]:
        (directory / filename).unlink(missing_ok=True)
    data = guest.bytes() + bytes(16)
    image = directory / "fixture.bin"
    image.write_bytes(data)
    env = {key: value for key, value in os.environ.items() if not key.startswith("FM1_POC_")}
    env.update(FM1_POC_STOP_PC=hex(stop), FM1_POC_MAX_INSTRUCTIONS=str(limit),
               FM1_POC_STATE_DIR=str(directory), FM1_POC_FRAME_DIR=str(directory))
    command = [*validate.COMMAND, "-kernel", str(image), "-append", "alnk-probe"]
    result = subprocess.run(command, cwd=validate.ROOT, env=env, capture_output=True,
                            text=True, timeout=30)
    (directory / "stdout.txt").write_text(result.stdout)
    (directory / "stderr.txt").write_text(result.stderr)
    validate.check((directory / "state.json").exists(), f"{name}: missing snapshot: {result.stderr}")
    state = json.loads((directory / "state.json").read_text())
    validate.check(state["profile"] == "alnk-probe", f"{name}: wrong private profile")
    if error:
        validate.check(result.returncode != 0 and error in state["reason"],
                       f"{name}: expected {error!r}, got {state['reason']!r}")
    else:
        validate.check(result.returncode == 0 and state["pc"] == stop,
                       f"{name}: wrong stop or guest failure: {result.stderr}")
    record = {"fixture_sha256": hashlib.sha256(data).hexdigest(),
              "qemu_sha256": hashlib.sha256(Path(command[0]).read_bytes()).hexdigest(),
              "command": command, "returncode": result.returncode, "snapshot": state}
    (directory / "result.json").write_text(json.dumps(record, indent=2) + "\n")
    return state, (directory / "state.sram").read_bytes()


def words(sram, address, count=1):
    return list(struct.unpack_from("<" + "I" * count, sram, address - RAM))


def check_case(name, options, deliveries):
    guest, stop, stages, wrappers = fixture(**options)
    state, sram = run(name, guest, stop)
    sequence = [source for stage in deliveries for source in stage]
    validate.check(state["irq_entries"] == state["rti_count"] == len(sequence) and not state["in_irq"],
                   f"{name}: unbalanced entry/RTI")
    validate.check(words(sram, LOG)[0] == len(sequence), f"{name}: guest handler count differs")
    for source in [11, 63]:
        count = sequence.count(source)
        validate.check(state[f"irq{source}_entries"] == state[f"irq{source}_rti_count"] == count,
                       f"{name}: source {source} entry/RTI count differs")
        validate.check(words(sram, VECTOR + source * 4)[0] == wrappers[source],
                       f"{name}: guest vector RAM differs")
    validate.check(state["last_irq_source"] == (sequence[-1] if sequence else 0),
                   f"{name}: wrong last source")
    audio_pending = 0x80 if options.get("audio", True) else 0
    timer_pending = 0x8008 if options.get("timer", True) else 0
    order = 0
    for stage, sources in zip(stages, deliveries):
        validate.check(words(sram, stage["sp"] - 64, 16) == GPRS,
                       f"{name}: wrapper/callee failed to restore all GPRs")
        last = sources[-1] if sources else None
        icfg = (0x030b0500 if last == 11 else 0x013f0500) if last else 0x100
        expected = [stage["sp"], SSP, stage["sp"] if sources else 0,
                    RETS, PSR, stage["resume"] if sources else 0xaabbccdd, icfg]
        validate.check(words(sram, stage["status"], 7) == expected,
                       f"{name}: foreground SP/SSP/USP/RETS/PSR/RETI/ICFG differs")
        validate.check(words(sram, stage["sp"])[0] == 0xfacefeed and
                       words(sram, stage["sp"] - 68)[0] == 0xcafec001,
                       f"{name}: foreground stack canary changed")
        for source in sources:
            entry_icfg = 0x030b0308 if source == 11 else 0x013f0302
            before_audio, before_timer = audio_pending, timer_pending
            if source == 11:
                audio_pending = 0
            else:
                timer_pending = 8
            expected_record = [order, entry_icfg, SSP - 80, stage["resume"],
                               before_audio, before_timer, audio_pending, timer_pending]
            validate.check(words(sram, RECORD[source], 8) == expected_record,
                           f"{name}: source order, vector ICFG, frame, RETI or independent acknowledgment differs")
            order += 1
    validate.check(words(sram, SSP)[0] == 0xfeedface and words(sram, SSP - 84)[0] == 0xc001cafe,
                   f"{name}: common wrapper/callee stack exceeded its 80-byte frame")
    validate.check(state["specials"][14] == stages[-1]["sp"] and state["specials"][13] == SSP,
                   f"{name}: final foreground/supervisor stack balance differs")
    validate.check(state["alnk"]["pending"] == audio_pending and
                   state["pending"] == bool(timer_pending & 0x8000), f"{name}: final device latches differ")
    validate.check(state["alnk"]["acknowledgments"] == sequence.count(11) and
                   state["acknowledgments"] == sequence.count(63), f"{name}: device acknowledgment count differs")
    print(f"PASS {name}: guest wrapper, source selection, acknowledgment and balanced RTI")


def check_predicate_completion(selected_then):
    name = "pending-timer-then-completion" if selected_then else "pending-timer-else-completion"
    guest = DeviceGuest()
    vector_patch = len(guest.words) + 4
    guest.write(VECTOR + 63 * 4, 0)
    guest.literal(13, SSP, special=True)
    guest.literal(14, SP, special=True)
    set_special(guest, 11, 0x100)  # Keep the pending timer globally masked.
    guest.write(IRQ_CONFIG + 0x1c, 0x30000000)
    guest.write(PRIORITY_MASK, 0)
    guest.write(TIMER5, 8)
    guest.write(TIMER5 + 8, 24000)
    guest.write(TIMER5 + 4, 0)
    guest.write(TIMER5, 9)
    wait_timer(guest)
    guest.literal(1, LOG)
    guest.literal(4, int(selected_then))
    # IF r4 != 0: two THEN instructions and two ELSE instructions.
    # STI leaves an active predicate; the literal is the final fallthrough.
    guest.emit(0xe8a4, 0x6000)
    guest.emit(0x0061)
    guest.emit(0xe044, 0x1111)
    guest.emit(0x0061)
    guest.emit(0xe044, 0x2222)
    resume = guest.pc
    # The first resumed instruction must already see the handler's marker.
    guest.load(0, 1)
    store_r0(guest, LOG + 0x20)
    guest.literal(1, LOG + 0x24)
    guest.store(4, 1)
    stop = guest.pc
    guest.words.extend([0] * 8)
    wrapper = append_handler(guest, 63)
    guest.words[vector_patch:vector_patch + 2] = [wrapper & 0xffff, wrapper >> 16]
    state, sram = run(name, guest, stop)
    validate.check(state["irq_entries"] == state["rti_count"] ==
                   state["irq63_entries"] == state["irq63_rti_count"] == 1 and
                   state["irq11_entries"] == state["irq11_rti_count"] == 0 and not state["in_irq"],
                   f"{name}: unbalanced timer entry/RTI or unexpected source")
    validate.check(state["last_irq_source"] == 63 and
                   state["specials"][0] == resume and words(sram, RECORD[63] + 12)[0] == resume,
                   f"{name}: IRQ was not admitted at the exact completed-arm boundary")
    validate.check(words(sram, LOG)[0] == words(sram, LOG + 0x20)[0] == 1 and
                   words(sram, RECORD[63])[0] == 0,
                   f"{name}: first resumed instruction did not observe the handler marker")
    validate.check(words(sram, LOG + 0x24)[0] == (0x1111 if selected_then else 0x2222),
                   f"{name}: wrong selected arm or final instruction did not complete")
    validate.check(not state["pending"] and state["acknowledgments"] == 1,
                   f"{name}: timer latch was not acknowledged exactly once")
    print(f"PASS {name}: pending timer admitted after final predicate fallthrough")


def main():
    selected = FELUCCA.read_bytes()
    validate.check(hashlib.sha256(selected).hexdigest() == FELUCCA_SHA, "selected firmware changed")
    for address in [0x0200046e, 0x0200047e]:
        wrapper = list(struct.unpack_from("<8H", selected, address - ENTRY))
        validate.check(wrapper[:2] == [0x04e9, 0x0460] and (wrapper[2] & 0xffc0) == 0xea80 and
                       wrapper[4:] == [0x0440, 0x04a9, 0x0020, 0x0081], "Felucca wrapper ABI changed")
    cases = [
        ("irq11-wrapper", {"timer": False}, [[11]]),
        ("irq63-wrapper", {"audio": False}, [[63]]),
        ("simultaneous-audio-first", {}, [[11, 63]]),
        ("priority-mask-holds-timer", {"mask": 3}, [[11]]),
        ("priority-mask-release", {"mask": 3, "release": "mask"}, [[11], [63]]),
        ("source-disable-keeps-audio", {"audio_enabled": False}, [[63]]),
        ("source-enable-release", {"audio_enabled": False, "release": "source"}, [[63], [11]]),
        ("global-sti-gate", {"global_enable": False}, [[]]),
    ]
    for name, options, deliveries in cases:
        check_case(name, options, deliveries)
    check_predicate_completion(True)
    check_predicate_completion(False)
    guest, stop, _, _ = fixture(equal_priority=True)
    state, _ = run("equal-priority-explicit-fault", guest, stop,
                   error="equal-priority audio/timer arbitration is unsupported")
    validate.check(state["irq_entries"] == state["rti_count"] == 0 and not state["in_irq"],
                   "equal-priority fault accepted an interrupt")
    print("PASS equal-priority-explicit-fault")
    guest = DeviceGuest()
    fault_pc = guest.pc
    guest.emit(0x0081)
    state, _ = run("rti-outside-context", guest, guest.pc, error="rti outside interrupt context")
    validate.check(state["pc"] == fault_pc and state["irq_entries"] == state["rti_count"] == 0 and
                   all(state[field] == 0 for field in ["irq11_entries", "irq11_rti_count",
                                                      "irq63_entries", "irq63_rti_count"]),
                   "RTI outside context changed IRQ counters or fault location")
    print("PASS rti-outside-context")


if __name__ == "__main__":
    main()
