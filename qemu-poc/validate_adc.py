#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Private SAR/WLA functional gates, using existing guest SRAM captures.

The 10us latency, command/readback/reset policies and raw600/512 board inputs
are declared model choices, not measured hardware behavior or resolution.
Fatal gates attest the saved guest prestate and fault PC/access/retirement;
ADC poststate/timer preservation is source-reviewed, not captured at failure.
Provider failure and deadline overflow guards are not forced by these normal
board inputs and deterministic virtual-clock probes. No firmware is modified.
"""
import argparse
import functools
import hashlib
import json
import os
from pathlib import Path
import re
import struct
import subprocess

import validate
from validate_felucca_devices import (
    ALNK, DeviceGuest, configuration, digest, samples,
)
from validate_peripherals import INSPECTION

HERE = Path(__file__).resolve().parent
CACHE = HERE / ".cache/adc-validation"
CON, RES, WLA = 0x13100, 0x13104, 0x11900
RESET_ENV = "FM1_POC_ADC_RESETS_NS"
INITIAL_ENV = "FM1_POC_ANALOG_INITIAL_WLA_CON0"
ICOUNT_NS = 8
ARTIFACTS = ("state.json", "state.sram", "state.alnk", "lcd.ppm")
RECORDS = []


class ADCGuest(DeviceGuest):
    def __init__(self, prefix=0):
        super().__init__()
        self.extra_retired = 0
        self.expected = []
        for _ in range(prefix):
            self.emit(0)

    @property
    def retired(self):
        return self.instructions + self.extra_retired

    def delay_ns(self, ns):
        # Two accepted loop instructions, plus one literal setup, per delay.
        # These deadlines concern shift3 functional time only.
        loops = max(1, (ns + 2 * ICOUNT_NS - 1) // (2 * ICOUNT_NS))
        self.delay(loops)
        self.extra_retired += 2 * (loops - 1)

    def take(self, address, value, label, size=4):
        self.read_width(address, 4, size)
        self.literal(5, INSPECTION + len(self.expected) * 4)
        self.store(4, 5)
        self.expected.append((label, value))

    def pair(self, control, result, label):
        self.take(CON, control, label + "/con")
        self.take(RES, result, label + "/res")

    def rmw(self, address, bits):
        self.literal(1, address)
        self.load(0, 1)
        self.literal(2, bits)
        self.emit(0x1920)  # r0 |= r2, accepted compact OR.
        self.store(0, 1)

    def stage(self, channel):
        self.write(CON, 0)
        self.write(CON, 0xf04e | (channel << 8))

    def enable(self):
        self.rmw(CON, 0x10)

    def kick(self):
        self.rmw(CON, 0x40)

    def start(self, channel=3):
        self.stage(channel)
        self.enable()
        self.kick()


@functools.cache
def qemu_sha256():
    return hashlib.sha256(Path(validate.COMMAND[0]).read_bytes()).hexdigest()


def run(name, guest, *, profile="application", image_name="controller-input",
        error=None, fault_pc=None, fault_access=None, schedule=(), initial=None,
        settings=None, process_error=None, missing_state=False, unobserved=False,
        expected_retired=None, alnk_expected=None, timer_expected=None):
    directory = CACHE / name
    directory.mkdir(parents=True, exist_ok=True)
    for filename in ARTIFACTS:
        (directory / filename).unlink(missing_ok=True)
    data = guest.bytes() + bytes(16)
    image = directory / f"{image_name}.bin"
    image.write_bytes(data)
    env = {key: value for key, value in os.environ.items()
           if not key.startswith("FM1_POC_")}
    controls = {}
    if not unobserved:
        controls.update(FM1_POC_STOP_PC=hex(guest.pc),
                        FM1_POC_MAX_INSTRUCTIONS="4000000",
                        FM1_POC_STATE_DIR=str(directory),
                        FM1_POC_FRAME_DIR=str(directory))
    if schedule:
        controls[RESET_ENV] = ",".join(str(ns) for ns in schedule)
    if initial is not None:
        controls[INITIAL_ENV] = hex(initial)
    controls.update(settings or {})
    if missing_state:
        controls.pop("FM1_POC_STATE_DIR", None)
    env.update(controls)
    command = [*validate.COMMAND, "-kernel", str(image)]
    if profile is not None:
        command += ["-append", profile]
    result = subprocess.run(command, cwd=directory, env=env,
                            capture_output=True, text=True, timeout=30)
    (directory / "stdout.txt").write_text(result.stdout)
    (directory / "stderr.txt").write_text(result.stderr)
    state = None
    observed = []
    retired = guest.retired if expected_retired is None else expected_retired
    if process_error is not None:
        validate.check(result.returncode != 0 and process_error in result.stderr,
                       f"{name}: expected setup rejection: {result.stderr}")
        validate.check(not any((directory / item).exists() for item in ARTIFACTS),
                       f"{name}: rejected setup produced guest evidence")
    elif unobserved:
        match = re.search(r"at PC 0x([0-9a-f]+) after (\d+) instructions", result.stderr)
        validate.check(result.returncode != 0 and error in result.stderr and match and
                       int(match[1], 16) == fault_pc and int(match[2]) == retired,
                       f"{name}: observer-disabled failure/count/PC differs: {result.stderr}")
        validate.check(not result.stdout.strip() and
                       not any((directory / item).exists() for item in ARTIFACTS),
                       f"{name}: observer-disabled execution created evidence")
    else:
        state_path = directory / "state.json"
        if state_path.exists():
            state = json.loads(state_path.read_text())
            ram = (directory / "state.sram").read_bytes()
            validate.check(len(ram) == 512 * 1024, f"{name}: incomplete SRAM capture")
            if guest.expected:
                observed = list(struct.unpack_from(
                    f"<{len(guest.expected)}I", ram, INSPECTION - 0x01c00000))
            validate.check(state["profile"] == (profile or "application"),
                           f"{name}: wrong capture profile")
        else:
            validate.check(not error and profile in ("probe", "diag"),
                           f"{name}: missing state capture: {result.stderr}")
            state = json.loads(result.stdout)
            observed = state["inspection"][:len(guest.expected)]
        validate.check(observed == [value for _, value in guest.expected],
                       f"{name}: readbacks differ: {list(zip(guest.expected, observed))}")
        if error:
            validate.check(result.returncode != 0 and error in state["reason"] and
                           state["pc"] == fault_pc,
                           f"{name}: fault or PC differs: {result.stderr}")
            validate.check(state["last_access"] == fault_access,
                           f"{name}: fault address/width/direction differs")
        else:
            validate.check(result.returncode == 0 and state["pc"] == guest.pc,
                           f"{name}: failed or stopped at another PC: {result.stderr}")
        validate.check(state["instructions"] == retired,
                       f"{name}: expected {retired} retired, got {state['instructions']}")
        validate.check(not state["in_irq"] and state["irq_entries"] == 0,
                       f"{name}: polling unexpectedly admitted an interrupt")
        for key, value in (alnk_expected or {}).items():
            validate.check(state["alnk"][key] == value,
                           f"{name}: unrelated alnk.{key} changed")
        for key, value in (timer_expected or {}).items():
            validate.check(state[key] == value, f"{name}: unrelated {key} differs")
    record = {"fixture_sha256": hashlib.sha256(data).hexdigest(),
              "qemu_sha256": qemu_sha256(), "command": command,
              "environment": controls, "returncode": result.returncode,
              "expected_readbacks": guest.expected, "readbacks": observed,
              "expected_retirement": retired, "expected_fault": error or process_error,
              "state": state, "diagnostic": result.stderr.strip(),
              "fatal_adc_poststate_attested": False}
    (directory / "result.json").write_text(json.dumps(record, indent=2) + "\n")
    RECORDS.append(name)
    print(f"PASS {name}")
    return state


def positive_cases():
    guest = ADCGuest()
    guest.pair(0, 0, "cold")
    guest.take(WLA, 0, "cold/wla")
    for channel, raw in ((3, 600), (4, 512)):
        base = 0xf00e | (channel << 8)
        guest.stage(channel)
        guest.pair(base, 0 if channel == 3 else 600, "disabled-kick")
        guest.enable()
        guest.delay_ns(12000)
        guest.pair(base | 0x10, 0 if channel == 3 else 600, "enable-alone")
        guest.kick()
        guest.pair(base | 0x10, 0 if channel == 3 else 600, "kick-before-deadline")
        guest.delay_ns(11000)
        guest.pair(base | 0x90, raw, "completion")
        guest.take(CON, base | 0x90, "read-preserves-pending")
        guest.write(CON, 0x40)
        guest.write(CON, 0)
        guest.pair(0, raw, "cleanup")
    run("driver-sequence-both-inputs", guest)

    guest = ADCGuest()
    guest.start()
    guest.delay_ns(11000)
    guest.pair(0xf39e, 600, "first-completion")
    guest.kick()  # RMW copies old pending, but its payload command is effective.
    guest.pair(0xf31e, 600, "repeated-rmw-kick")
    guest.delay_ns(11000)
    guest.pair(0xf39e, 600, "second-completion")
    guest.write(CON, 0xf39e)  # Copied pending has no acknowledgment side effect.
    guest.pair(0xf39e, 600, "pending-write-ignored")
    guest.write(CON, 0)
    guest.pair(0x80, 600, "disable-retains-pending")
    guest.write(CON, 0x40)
    guest.pair(0, 600, "disabled-kick-clears")
    run("repeat-kick-status-and-clear", guest)

    guest = ADCGuest()
    guest.start()
    guest.delay_ns(3000)
    guest.pair(0xf31e, 0, "busy-before-disable")
    guest.write(CON, 0)
    guest.delay_ns(12000)
    guest.pair(0, 0, "past-canceled-deadline")
    guest.write(CON, 0xf41e)
    guest.delay_ns(12000)
    guest.pair(0xf41e, 0, "reenable-without-kick")
    guest.kick()
    guest.delay_ns(11000)
    guest.pair(0xf49e, 512, "new-conversion")
    run("disable-cancels-old-deadline", guest)

    guest = ADCGuest()
    guest.start()
    guest.delay_ns(6000)
    guest.write(CON, 0xf45e)  # Busy command replaces both channel and deadline.
    guest.delay_ns(6000)
    guest.pair(0xf41e, 0, "past-old-before-new-deadline")
    guest.delay_ns(5000)
    guest.pair(0xf49e, 512, "new-deadline")
    run("busy-kick-new-input-fresh-phase", guest)

    guest = ADCGuest()
    guest.start()
    guest.delay_ns(6000)
    guest.write(CON, 0xf31e)
    guest.write(WLA, 0)
    guest.delay_ns(5000)
    guest.pair(0xf39e, 600, "same-writes-preserve-phase")
    guest.write(CON, 0xf41e)  # Enabled, idle reconfiguration requires no kick.
    guest.pair(0xf49e, 600, "idle-reconfiguration-retains-result-pending")
    guest.kick()
    guest.delay_ns(11000)
    guest.pair(0xf49e, 512, "idle-reconfigured-kick")
    run("same-writes-and-idle-reconfiguration", guest)

    guest = ADCGuest()
    guest.start()
    guest.delay_ns(9000)
    guest.pair(0xf31e, 0, "before-functional-deadline")
    guest.delay_ns(2000)
    guest.pair(0xf39e, 600, "after-functional-deadline")
    run("functional-ten-us-deadline", guest)

    guest = ADCGuest()
    guest.take(WLA, 0x24005, "initial-unowned-fields")
    guest.write(WLA, 0x20005)
    guest.start()
    guest.delay_ns(11000)
    guest.pair(0xf39e, 600, "gpio-with-unowned-fields")
    guest.take(WLA, 0x20005, "preserved-fields")
    guest.write(WLA, 0x24005)
    guest.take(WLA, 0x24005, "idle-route-set")
    guest.write(WLA, 0x20005)
    guest.take(WLA, 0x20005, "idle-route-clear")
    run("shared-analog-field-preservation", guest, initial=0x24005)


def fault_cases():
    negatives = (
        ("control-high-bit", "busy", CON, 0x1f35e, 4, True, "unsupported SAR ADC control fields"),
        ("irq-disabled", "cold", CON, 0x20, 4, True, "SAR ADC IRQ24 is unimplemented"),
        ("irq-busy", "busy", CON, 0xf37e, 4, True, "SAR ADC IRQ24 is unimplemented"),
        ("divider", "busy", CON, 0xf35f, 4, True, "unsupported SAR ADC enabled timing configuration"),
        ("bit3", "busy", CON, 0xf356, 4, True, "unsupported SAR ADC enabled timing configuration"),
        ("startup-delay", "busy", CON, 0xe35e, 4, True, "unsupported SAR ADC enabled timing configuration"),
        ("unbound-channel", "busy", CON, 0xf25e, 4, True, "SAR ADC channel has no board raw input"),
        ("busy-channel-without-kick", "busy", CON, 0xf41e, 4, True, "SAR ADC configuration changed while busy without a kick"),
        ("wla-unowned-change", "complete", WLA, 1, 4, True, "unsupported WLA_CON0 field change"),
        ("wla-busy-route", "busy", WLA, 0x4000, 4, True, "WLA_CON0 ADC routing changed while SAR ADC is busy"),
        ("result-readonly", "complete", RES, 0, 4, True, "write to read-only SAR ADC result"),
        ("unaligned-con", "cold", CON + 1, 0, 4, True, "unaligned access"),
        ("sar-range-end", "cold", CON + 8, 0, 4, False, "unmapped access at 0x00013108"),
        ("analog-range-end", "cold", WLA + 4, 0, 4, False, "unmapped access at 0x00011904"),
    )
    for name, phase, address, value, size, write, error in negatives:
        guest = ADCGuest()
        if phase != "cold":
            guest.start()
            if phase == "complete":
                guest.delay_ns(11000)
        guest.pair(0 if phase == "cold" else 0xf39e if phase == "complete" else 0xf31e,
                   600 if phase == "complete" else 0, "pre-fault")
        guest.take(WLA, 0, "pre-fault/wla")
        pc = (guest.write_width(address, value, size) if write else
              guest.read_width(address, 4, size))
        run(name, guest, error=error, fault_pc=pc,
            fault_access={"address": address, "size": size, "flags": int(write)},
            expected_retired=guest.retired - 1)
    for address, register in ((CON, "con"), (RES, "res"), (WLA, "wla")):
        for size in (1, 2):
            for write in (False, True):
                guest = ADCGuest()
                guest.pair(0, 0, "pre-fault")
                pc = (guest.write_width(address, 0, size) if write else
                      guest.read_width(address, 4, size))
                owner = "WLA_CON0" if address == WLA else "SAR ADC"
                direction = "write" if write else "read"
                run(f"{register}-{direction}-width{size}", guest,
                    error=f"unsupported {owner} register {direction} or width", fault_pc=pc,
                    fault_access={"address": address, "size": size, "flags": int(write)},
                    expected_retired=guest.retired - 1)
    guest = ADCGuest()
    guest.take(WLA, 0x20005, "initial-unowned-fields")
    pc = guest.write(WLA, 0)
    run("wla-whole-zero-loses-unowned-fields", guest, initial=0x20005,
        error="unsupported WLA_CON0 field change", fault_pc=pc,
        fault_access={"address": WLA, "size": 4, "flags": 1},
        expected_retired=guest.retired - 1)
    guest = ADCGuest()
    guest.write(WLA, 0x4000)
    guest.pair(0, 0, "pre-fault")
    guest.take(WLA, 0x4000, "pre-fault/wla")
    pc = guest.write(CON, 0xf35e)
    run("unsupported-analog-test-kick", guest,
        error="unsupported SAR ADC analog-test input routing", fault_pc=pc,
        fault_access={"address": CON, "size": 4, "flags": 1},
        expected_retired=guest.retired - 1)


def reset_cases():
    guest = ADCGuest()
    guest.delay_ns(12000)
    guest.pair(0, 0, "cold-reset")
    run("reset-cold", guest, schedule=(8,))
    for name, schedule in (("reset-configured", (5000,)),
                           ("reset-repeated", (5000, 5000, 8000)),
                           ("reset-maximum-schedule", tuple(5000 + 32 * n for n in range(16)))):
        guest = ADCGuest()
        guest.stage(3)
        guest.enable()
        guest.pair(0xf31e, 0, "before-idle-reset")
        guest.delay_ns(12000)
        guest.pair(0, 0, "after-idle-reset")
        run(name, guest, schedule=schedule)
    guest = ADCGuest()
    guest.start()
    guest.pair(0xf31e, 0, "before-busy-reset")
    guest.delay_ns(12000)
    guest.pair(0, 0, "past-reset-and-canceled-deadline")
    run("reset-cancels-busy-conversion", guest, schedule=(5000,))
    guest = ADCGuest()
    guest.start()
    guest.delay_ns(11000)
    guest.pair(0xf39e, 600, "before-pending-reset")
    guest.delay_ns(12000)
    guest.pair(0, 0, "after-pending-reset")
    run("reset-clears-result-and-pending", guest, schedule=(20000,))

    guest = ADCGuest()
    configuration(guest)
    samples(guest)
    guest.write_width(ALNK, 0x980, 2)
    guest.write(0x10908, 240000)  # TIMER5: 24 MHz clock, 10 ms; CPU IRQ admission disabled.
    guest.write(0x10900, 9)
    guest.take(WLA, 0x24005, "initial-shared-analog")
    guest.write(WLA, 0x20005)
    guest.start()
    guest.pair(0xf31e, 0, "before-local-reset")
    validate.check(guest.retired * ICOUNT_NS < 5000,
                   "reset fixture no longer establishes the intended busy prestate")
    guest.delay_ns(12000000)
    guest.pair(0, 0, "after-local-reset-past-old-deadline")
    guest.take(WLA, 0x20005, "reset-preserves-shared-analog")
    guest.start(4)
    guest.delay_ns(11000)
    guest.pair(0xf49e, 512, "reset-preserves-provider-bindings")
    guest.take(ALNK + 4, 0x5000, "reset-preserves-alnk", size=2)
    guest.take(0x10900, 0x8009, "reset-preserves-timer5")
    run("reset-shared-bindings-and-unrelated-controllers", guest,
        schedule=(5000,), initial=0x24005,
        alnk_expected={"enabled": True, "completions": 2, "pending": 0x80,
                       "sample_words": 1024, "sample_digest": digest([0, 1])},
        timer_expected={"timer_expirations": 1, "pending": True, "acknowledgments": 0})


def profile_cases():
    for prefix, layout in ((0, "direct"), (16, "moved")):
        for profile in ("probe", "diag", "alnk-probe", "application", None):
            guest = ADCGuest(prefix)
            guest.pair(0, 0, "cold")
            guest.take(WLA, 0, "cold/wla")
            guest.start(4)
            guest.delay_ns(11000)
            guest.pair(0xf49e, 512, "complete")
            for image_name in ("controller-input", "renamed-input"):
                run(f"profiles/{layout}/{profile or 'default'}/{image_name}", guest,
                    profile=profile, image_name=image_name)
    guest = ADCGuest()
    guest.start()
    guest.delay_ns(11000)
    guest.read_width(RES, 0, 4)
    guest.literal(1, 600)
    branch = len(guest.words)
    guest.emit(0xe881, 0)  # If r0!=r1, use a distinct failure PC/opcode.
    success = guest.pc
    retired = guest.retired
    guest.emit(0x0001)
    failure = guest.pc
    guest.emit(0x0003)
    guest.words[branch + 1] = (failure - (guest.base + (branch + 2) * 2)) // 2
    run("observers-disabled-conversion", guest, profile=None, unobserved=True,
        error="unsupported instruction 0x0001", fault_pc=success,
        expected_retired=retired)


def control_cases():
    bad_schedule = ("", "0", "-1", "2,1", "1,", "garbage", "9223372036854775808",
                    ",".join(str(n + 1) for n in range(17)))
    for index, value in enumerate(bad_schedule):
        run(f"invalid-reset-schedule-{index}", ADCGuest(),
            settings={RESET_ENV: value}, process_error="FM1_POC_ADC_RESETS_NS requires")
    run("reset-schedule-needs-existing-capture", ADCGuest(), schedule=(8,),
        missing_state=True, process_error="FM1_POC_ADC_RESETS_NS requires FM1_POC_STATE_DIR")
    for index, value in enumerate(("", "-1", "4294967296", "garbage")):
        run(f"invalid-initial-analog-{index}", ADCGuest(), settings={INITIAL_ENV: value},
            process_error="FM1_POC_ANALOG_INITIAL_WLA_CON0 requires a 32-bit unsigned integer")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("group", nargs="?", default="all",
                        choices=("all", "positive", "faults", "reset", "profiles", "controls"))
    args = parser.parse_args()
    groups = {"positive": positive_cases, "faults": fault_cases, "reset": reset_cases,
              "profiles": profile_cases, "controls": control_cases}
    for name, function in groups.items():
        if args.group in ("all", name):
            function()
    print(f"PASS {len(RECORDS)} SAR/WLA functional probes; hardware timing/resolution uncalibrated")


if __name__ == "__main__":
    main()
