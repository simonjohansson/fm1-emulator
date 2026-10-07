#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Check local ALNK cold reset using opt-in virtual-time test injection.

Disposable guest programs and alnk-reset.jsonl attest the actual prestate.
These tests do not claim whole-machine, watchdog or physical reset support.
The injection has no firmware identity or guest-PC trigger.
"""
import argparse
import functools
import hashlib
import json
import os
from pathlib import Path
import struct
import subprocess

import validate
from validate_felucca_devices import (
    ALNK, DMA, DeviceGuest, FRAMES, HALF_BYTES, RATE, SEED,
    configuration, digest, samples,
)
from validate_felucca_irq import (
    IRQ_CONFIG, LOG, SP, SSP, TIMER5, VECTOR, append_handler, set_special, wait_timer,
)

HERE = Path(__file__).resolve().parent
CACHE = HERE / ".cache/alnk-reset-validation"
RESET_ENV = "FM1_POC_ALNK_RESETS_NS"
EMPTY_SHA = hashlib.sha256(b"").hexdigest()
PERIOD = (FRAMES * 1000000000 + RATE - 1) // RATE
ZERO_FIELDS = (
    "control0", "control1", "control3", "pending", "dma_address", "half_words",
    "active_half", "last_half", "epoch", "deadline", "completions", "acknowledgments",
    "coalesced_completions", "skipped_captures", "sample_words", "sample_frames",
    "nonzero_words", "latest_half_bytes",
)
RECORDS = []


@functools.cache
def qemu_sha256():
    return hashlib.sha256(validate.QEMU.read_bytes()).hexdigest()


def check_cold(alnk, label, *, sidecar=False):
    for key in ZERO_FIELDS:
        validate.check(alnk[key] == 0, f"{label}: reset left alnk.{key}={alnk[key]!r}")
    validate.check(not alnk["enabled"] and not alnk["irq_level"] and
                   alnk["sample_digest"] == SEED, f"{label}: ALNK cold state differs")
    if sidecar:
        validate.check(alnk["scheduled_halves"] == 0 and
                       alnk["latest_half_sha256"] == EMPTY_SHA and
                       alnk["realized"] and alnk["validators_registered"],
                       f"{label}: reset damaged capture or lifecycle state")


def check_events(name, events, schedule):
    validate.check(len(events) == len(schedule),
                   f"{name}: only {len(events)} of {len(schedule)} requested resets occurred")
    for index, (event, ns) in enumerate(zip(events, schedule)):
        label = f"{name}/reset-{index}"
        validate.check(event["index"] == index and event["total"] == len(schedule) and
                       event["scheduled_ns"] == ns and event["actual_ns"] >= ns,
                       f"{label}: reset schedule/evidence differs")
        before, after = event["before"], event["after"]
        for key in ("syscon", "sram_sha256", "cpu", "timer5", "unrelated"):
            validate.check(after[key] == before[key], f"{label}: reset changed {key}")
        check_cold(after["alnk"], label, sidecar=True)
        validate.check(not after["alnk_timer_pending"],
                       f"{label}: reset left the completion timer scheduled")
        validate.check(before["cpu_hard_irq"] ==
                       (before["alnk"]["irq_level"] or before["timer5"]["pending"]),
                       f"{label}: pre-reset aggregate interrupt disagrees with device levels")
        validate.check(after["cpu_hard_irq"] == after["timer5"]["pending"],
                       f"{label}: reset lost TIMER5 IRQ or retained ALNK IRQ")


def run(name, guest, *, schedule=(), stop=None, profile="alnk-probe",
        bad_schedule=None, missing_state=False, process_error=None):
    directory = CACHE / name
    directory.mkdir(parents=True, exist_ok=True)
    for filename in ("state.json", "state.sram", "state.alnk", "alnk-reset.jsonl"):
        (directory / filename).unlink(missing_ok=True)
    data = guest.bytes() + bytes(16)
    image = directory / "controller-input.bin"
    image.write_bytes(data)
    env = {key: value for key, value in os.environ.items()
           if not key.startswith("FM1_POC_")}
    stop = guest.pc if stop is None else stop
    env.update(FM1_POC_STOP_PC=hex(stop), FM1_POC_MAX_INSTRUCTIONS="4000000",
               FM1_POC_STATE_DIR=str(directory), FM1_POC_FRAME_DIR=str(directory))
    if schedule:
        env[RESET_ENV] = ",".join(str(ns) for ns in schedule)
    if bad_schedule is not None:
        env[RESET_ENV] = bad_schedule
    if missing_state:
        env.pop("FM1_POC_STATE_DIR")
    command = [*validate.COMMAND, "-kernel", str(image), "-append", profile]
    result = subprocess.run(command, cwd=validate.ROOT, env=env,
                            capture_output=True, text=True, timeout=30)
    (directory / "stdout.txt").write_text(result.stdout)
    (directory / "stderr.txt").write_text(result.stderr)
    record = {"fixture_sha256": hashlib.sha256(data).hexdigest(),
              "qemu_sha256": qemu_sha256(), "command": command,
              "schedule_ns": list(schedule), "returncode": result.returncode}
    if process_error is not None:
        validate.check(result.returncode != 0 and process_error in result.stderr,
                       f"{name}: schedule should fail explicitly: {result.stderr}")
        validate.check(not (directory / "alnk-reset.jsonl").exists(),
                       f"{name}: invalid schedule started reset injection")
        state, events = None, []
        record["diagnostic"] = result.stderr.strip()
    else:
        validate.check(result.returncode == 0, f"{name}: {result.stderr}")
        state = json.loads((directory / "state.json").read_text())
        validate.check(state["profile"] == profile and state["pc"] == stop,
                       f"{name}: profile or final checkpoint differs")
        path = directory / "alnk-reset.jsonl"
        if schedule:
            validate.check(path.exists(), f"{name}: missing reset attestation")
            events = [json.loads(line) for line in path.read_text().splitlines()]
        else:
            validate.check(not path.exists(), f"{name}: injection ran while unset")
            events = []
        check_events(name, events, schedule)
        record.update(snapshot=state, resets=events)
    (directory / "result.json").write_text(json.dumps(record, indent=2) + "\n")
    RECORDS.append(record)
    print(f"PASS {name}")
    return state, events


def prepared_guest():
    guest = DeviceGuest()
    guest.write(0x10010, 3)
    configuration(guest)
    samples(guest)
    guest.write(0x01c12000, 0x13579bdf)
    guest.write(0x01c12004, 0x2468ace0)
    return guest


def baseline():
    guest = prepared_guest()
    guest.write_width(ALNK, 0x980, 2)
    guest.wait_half(1)
    state, _ = run("injection-unset-baseline", guest)
    a = state["alnk"]
    validate.check(a["completions"] == 1 and a["pending"] == 0x80 and
                   a["enabled"] and a["sample_digest"] == digest([0]),
                   "baseline did not reach the expected real completion")
    return a["epoch"]


def idle_resets():
    guest = DeviceGuest()
    guest.delay(2000)
    state, events = run("cold-reset", guest, schedule=(8,))
    validate.check(events[0]["before"]["alnk"]["control0"] == 0 and
                   not events[0]["before"]["alnk_timer_pending"],
                   "cold-reset: injection did not observe cold idle state")
    check_cold(state["alnk"], "cold-reset/final")

    for name, schedule in (("idle-configured-reset", (2000,)),
                            ("idle-repeated-reset", (2000, 2000, 4000)),
                            ("maximum-reset-schedule", tuple(2000 + 32 * n for n in range(16)))):
        guest = prepared_guest()
        guest.write(0x10014, 0xf00)
        guest.write(0x51030, 0xc0)
        guest.delay(4000)
        state, events = run(name, guest, schedule=schedule)
        validate.check(events[0]["before"]["alnk"]["control0"] == 0x180 and
                       not events[0]["before"]["alnk"]["enabled"],
                       f"{name}: configured idle state was not reached before reset")
        for event in events:
            validate.check(event["before"]["syscon"] == [3, 0xf00, 0xc0],
                           f"{name}: nonzero shared words were not preserved")
        check_cold(state["alnk"], f"{name}/final")
        validate.check(state["alnk"]["clock_control"] == 0xf00 and
                       state["alnk"]["iomap_control"] == 0xc0,
                       f"{name}: canonical capture getters changed after reset")


def completion_resets(epoch):
    for name, ns, completed in (("cancel-before-completion", epoch + PERIOD // 2, 0),
                               ("clear-pending-completion", epoch + PERIOD + 800, 1)):
        guest = prepared_guest()
        guest.write_width(ALNK, 0x980, 2)
        guest.delay(1000000)
        state, events = run(name, guest, schedule=(ns,))
        event = events[0]
        before = event["before"]
        a = before["alnk"]
        validate.check(a["enabled"] and before["alnk_timer_pending"] and
                       a["epoch"] == epoch and a["completions"] == completed and
                       a["pending"] == (0x80 if completed else 0),
                       f"{name}: requested reset did not observe the intended completion state")
        validate.check(event["actual_ns"] < a["deadline"],
                       f"{name}: reset arrived after the next deadline")
        if completed:
            validate.check(event["actual_ns"] > epoch + PERIOD and
                           a["sample_digest"] == digest([0]),
                           f"{name}: first completion was not attested before reset")
        check_cold(state["alnk"], f"{name}/final")
        validate.check(state["virtual_ns"] > a["deadline"],
                       f"{name}: guest did not advance past the canceled old deadline")
        validate.check((CACHE / name / "state.alnk").read_bytes() == b"",
                       f"{name}: canceled completion produced sample evidence")

    guest = prepared_guest()
    guest.write_width(ALNK, 0x980, 2)
    guest.delay(500000)
    configuration(guest)
    guest.write_width(ALNK, 0x980, 2)
    guest.wait_half(1)
    state, events = run("fresh-phase-after-reconfigure", guest,
                        schedule=(epoch + PERIOD // 2,))
    a = state["alnk"]
    validate.check(a["epoch"] > events[0]["actual_ns"] and a["epoch"] > epoch + PERIOD and
                   a["completions"] == 1 and a["active_half"] == 1 and a["last_half"] == 0 and
                   a["sample_words"] == 512 and a["sample_digest"] == digest([0]),
                   "reconfiguration did not start a fresh phase/capture history")
    next_offset = (2 * FRAMES * 1000000000 + RATE - 1) // RATE
    validate.check(a["deadline"] == a["epoch"] + next_offset,
                   "fresh phase has the wrong rational next deadline")
    validate.check((CACHE / "fresh-phase-after-reconfigure/state.alnk").read_bytes() ==
                   struct.pack("<I", 0x11223344) + bytes(HALF_BYTES - 4),
                   "fresh phase did not consume the preserved first SRAM half")


def independent_timer_irq():
    guest = prepared_guest()
    set_special(guest, 11, 0x100)  # Global admission disabled until after reset.
    guest.literal(14, SP, special=True)
    guest.literal(13, SSP, special=True)
    patch = len(guest.words) + 4
    guest.write(VECTOR + 63 * 4, 0)
    guest.write(IRQ_CONFIG + 4, 0x7000)
    guest.write(IRQ_CONFIG + 0x1c, 0x30000000)
    guest.write(IRQ_CONFIG + 0xa8, 0)
    guest.write(0x11800, 0x3d)
    guest.write(0x11804, 0x160)  # Existing cold pending SIE request, no host traffic.
    guest.write(TIMER5, 8)
    guest.write(TIMER5 + 8, 24000)
    guest.write(TIMER5 + 4, 0)
    guest.write(TIMER5, 9)
    wait_timer(guest)  # Leave TIMER5 pending, with further expirations disabled.
    guest.write_width(ALNK, 0x980, 2)
    guest.wait_half(1)
    guest.delay(500000)
    guest.emit(0x0061)  # Actual STI admits the surviving TIMER5 source.
    for _ in range(8):
        guest.emit(0)
    stop = guest.pc
    guest.words.extend([0] * 8)
    wrapper = append_handler(guest, 63)
    guest.words[patch] = wrapper & 0xffff
    guest.words[patch + 1] = wrapper >> 16
    state, events = run("timer5-pending-survives", guest, stop=stop, schedule=(8000000,))
    before = events[0]["before"]
    validate.check(before["alnk"]["pending"] == 0x80 and before["timer5"]["pending"] and
                   before["unrelated"]["usb_requests"] == 1 and
                   before["unrelated"]["usb_bridge"] == 0x160,
                   "independence case did not attest both IRQs and unrelated USB state")
    validate.check(state["irq63_entries"] == state["irq63_rti_count"] == 1 and
                   state["irq11_entries"] == 0 and not state["in_irq"] and
                   state["irq_entries"] == state["rti_count"] == 1 and
                   state["acknowledgments"] == 1 and not state["pending"],
                   "ALNK reset prevented real TIMER5 delivery/acknowledgment/return")
    check_cold(state["alnk"], "timer5-pending-survives/final")
    ram = (CACHE / "timer5-pending-survives/state.sram").read_bytes()
    validate.check(struct.unpack_from("<I", ram, LOG - 0x01c00000)[0] == 1,
                   "surviving TIMER5 guest handler did not run")


def invalid_schedules():
    guest = DeviceGuest()
    guest.delay(2000)
    message = "FM1_POC_ALNK_RESETS_NS requires 1 to 16 sorted positive nanoseconds"
    for name, value in (("empty", ""), ("zero", "0"), ("negative", "-1"),
                        ("unsorted", "2000,1000"), ("empty-entry", "1000,,2000"),
                        ("noninteger", "abc"), ("overflow", str(1 << 63)),
                        ("too-many", ",".join(str(1000 + n) for n in range(17)))):
        run(f"invalid-schedule-{name}", guest, bad_schedule=value, process_error=message)
    run("schedule-requires-state-dir", guest, bad_schedule="1000", missing_state=True,
        process_error="FM1_POC_ALNK_RESETS_NS requires FM1_POC_STATE_DIR")


def main():
    global CACHE
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence-dir", type=Path, default=CACHE)
    args = parser.parse_args()
    CACHE = args.evidence_dir.resolve()
    epoch = baseline()
    idle_resets()
    completion_resets(epoch)
    independent_timer_irq()
    invalid_schedules()
    summary = {"qemu_sha256": qemu_sha256(), "local_alnk_cold_reset_only": True,
               "whole_machine_reset_validated": False, "cases": RECORDS}
    (CACHE / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(f"PASS {len(RECORDS)} ALNK lifecycle cases: attested local reset, cancellation, "
          "fresh phase and independent TIMER5/SRAM/syscon/controller state")


if __name__ == "__main__":
    main()
