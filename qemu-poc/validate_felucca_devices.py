#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Focused ALNK gates with disposable guests; unchanged Felucca is separate.

The private alnk-probe profile has cold SRAM and the dedicated ALNK model.
Only already accepted compact byte/half/word operations are emitted here.
No firmware or Rust device implementation is included in these tests.
Skipped callback capture accounting is guarded in the device but is not
exercised by these normal deterministic instruction-clock probes.
"""
import hashlib
import json
import os
from pathlib import Path
import struct
import subprocess

import validate
from validate_peripherals import Guest

HERE = Path(__file__).resolve().parent
CACHE = HERE / ".cache/felucca-device-validation"
ALNK = 0x12e00
DMA = 0x01c10000
HALF_BYTES = 2048
RATE = 44100
FRAMES = 256
SEED = 2166136261


class DeviceGuest(Guest):
    def access(self, reg, base, size, write=False):
        assert 0 <= reg < 8 and 0 <= base < 8
        if size == 4:
            self.emit((0x6080 if write else 0x6000) | reg | (base << 4))
        elif size == 2:
            self.emit((0x6088 if write else 0x6008) | reg | (base << 4))
        elif size == 1:
            self.emit((0x4088 if write else 0x4008) | reg | (base << 4))
        else:
            raise ValueError("use an accepted guest access width")

    def write_width(self, address, value, size):
        self.literal(1, address)
        self.literal(0, value)
        pc = self.pc
        self.access(0, 1, size, True)
        return pc

    def read_width(self, address, reg, size):
        self.literal(1, address)
        pc = self.pc
        self.access(reg, 1, size)
        return pc

    def wait_half(self, half):
        # Wait for actual active-half changes while leaving the pending latch
        # untouched. Interrupts stay disabled in this peripheral fixture.
        self.literal(1, ALNK)
        self.literal(2, 0x8000)
        loop = self.pc
        self.access(3, 1, 2)
        self.emit(0x19a3)  # r3 &= r2
        self.branch_zero(3, loop, nonzero=half == 0)

    def delay(self, loops=500000):
        self.literal(6, loops)
        loop = self.pc
        self.add(6, -1)
        self.branch_zero(6, loop, nonzero=True)


def configuration(guest, address=DMA):
    guest.write_width(ALNK, 0, 2)
    guest.write_width(ALNK + 4, 0, 2)
    guest.write_width(ALNK + 8, 0, 1)
    guest.write_width(ALNK + 12, 0, 1)
    guest.write(0x51030, 0)
    guest.write_width(ALNK + 12, 3, 1)
    guest.write_width(ALNK, 0x100, 2)
    guest.write_width(ALNK, 0x180, 2)
    guest.write_width(ALNK + 32, 512, 2)
    guest.write(0x10014, 0)
    guest.write_width(ALNK + 12, 0x83, 1)
    guest.write_width(ALNK + 4, 0x1000, 2)
    guest.write_width(ALNK + 4, 0x5000, 2)
    guest.write(ALNK + 28, address)
    guest.write_width(ALNK + 8, 15, 1)


def samples(guest):
    # Each half has one distinct nonzero stereo word amid cold zero SRAM.
    guest.write(DMA, 0x11223344)
    guest.write(DMA + HALF_BYTES, 0x55667788)


def digest(halves, words=(0x11223344, 0x55667788)):
    value = SEED
    for half in halves:
        data = struct.pack("<I", words[half])
        data += bytes(HALF_BYTES - len(data))
        for byte in data:
            value = ((value ^ byte) * 16777619) & 0xffffffff
    return value


def run(name, guest, error=None, fault_pc=None, expected=None, limit=4000000):
    directory = CACHE / name
    directory.mkdir(parents=True, exist_ok=True)
    for filename in ["state.json", "state.alnk"]:
        (directory / filename).unlink(missing_ok=True)
    data = guest.bytes() + bytes(16)
    image = directory / "fixture.bin"
    image.write_bytes(data)
    env = {key: value for key, value in os.environ.items()
           if not key.startswith("FM1_POC_")}
    env.update(FM1_POC_STOP_PC=hex(guest.pc),
               FM1_POC_MAX_INSTRUCTIONS=str(limit),
               FM1_POC_STATE_DIR=str(directory),
               FM1_POC_FRAME_DIR=str(directory))
    command = list(validate.COMMAND)
    command += ["-kernel", str(image), "-append", "alnk-probe"]
    result = subprocess.run(command, cwd=validate.ROOT, env=env,
                            capture_output=True, text=True, timeout=30)
    (directory / "stdout.txt").write_text(result.stdout)
    (directory / "stderr.txt").write_text(result.stderr)
    validate.check((directory / "state.json").exists(),
                   f"{name}: missing device snapshot: {result.stderr}")
    state = json.loads((directory / "state.json").read_text())
    validate.check(state["profile"] == "alnk-probe", f"{name}: wrong private profile")
    if error:
        validate.check(result.returncode != 0 and error in state["reason"],
                       f"{name}: expected {error!r}, got {state['reason']!r}")
        if fault_pc is not None:
            validate.check(state["pc"] == fault_pc, f"{name}: fault at another access")
    else:
        validate.check(result.returncode == 0 and state["pc"] == guest.pc,
                       f"{name}: failed or stopped at another PC: {result.stderr}")
        for key, value in (expected or {}).items():
            validate.check(state["alnk"][key] == value,
                           f"{name}: alnk.{key} differs: {state['alnk'][key]!r}")
        alnk = state["alnk"]
        validate.check(alnk["sample_frames"] * 2 == alnk["sample_words"] and
                       alnk["sample_frames"] == (
                           alnk["completions"] - alnk["skipped_captures"]) * FRAMES,
                       f"{name}: captured samples include skipped history")
        if alnk["enabled"]:
            periods = alnk["completions"] + 1
            offset = (periods * FRAMES * 1000000000 + RATE - 1) // RATE
            validate.check(alnk["deadline"] == alnk["epoch"] + offset,
                           f"{name}: cumulative rational completion phase differs")
        if alnk["latest_half_bytes"]:
            latest = (directory / "state.alnk").read_bytes()
            validate.check(len(latest) == HALF_BYTES,
                           f"{name}: latest sample capture is not bounded")
    record = {"fixture_sha256": hashlib.sha256(data).hexdigest(),
              "qemu_sha256": hashlib.sha256(Path(command[0]).read_bytes()).hexdigest(),
              "command": command, "returncode": result.returncode,
              "snapshot": state}
    (directory / "result.json").write_text(json.dumps(record, indent=2) + "\n")
    print(f"PASS {name}")
    return state


def positive_cases():
    guest = DeviceGuest()
    configuration(guest)
    guest.write_width(ALNK + 8, 0x80, 1)  # Status writes cannot manufacture a pending.
    guest.read_width(ALNK + 8, 4, 1)
    state = run("alnk-cold-configuration", guest, expected={
        "control0": 0x180, "control1": 0x5000, "control3": 0x83,
        "half_words": 512, "dma_address": DMA, "pending": 0,
        "enabled": False, "irq_level": False, "completions": 0,
        "sample_digest": SEED, "latest_half_bytes": 0})
    validate.check(state["registers"][4] == 0, "cold status write asserted pending")

    guest = DeviceGuest()
    configuration(guest)
    samples(guest)
    guest.write_width(ALNK, 0x980, 2)
    guest.wait_half(1)
    guest.write_width(ALNK + 8, 1, 1)  # Auxiliary ack leaves channel 3 pending.
    guest.read_width(ALNK + 8, 4, 1)
    guest.write_width(ALNK + 8, 0x88, 1)  # RMW acknowledgment includes old status.
    guest.read_width(ALNK + 8, 5, 1)
    guest.write(DMA + HALF_BYTES, 0x99aabbcc)  # DMA observes writes after enable.
    guest.wait_half(0)
    state = run("alnk-two-halves-ack-phase", guest, expected={
        "enabled": True, "irq_level": True, "pending": 0x80,
        "active_half": 0, "last_half": 1, "completions": 2,
        "acknowledgments": 1, "coalesced_completions": 0,
        "skipped_captures": 0, "sample_words": 1024,
        "nonzero_words": 2,
        "sample_digest": digest([0, 1], (0x11223344, 0x99aabbcc)),
        "latest_half_bytes": HALF_BYTES})
    validate.check(state["registers"][4] == 0x80 and state["registers"][5] == 0,
                   "auxiliary/half W1 acknowledgments or status readback differ")
    validate.check((CACHE / "alnk-two-halves-ack-phase/state.alnk").read_bytes() ==
                   struct.pack("<I", 0x99aabbcc) + bytes(HALF_BYTES - 4),
                   "DMA latest half does not contain actual guest samples")

    guest = DeviceGuest()
    configuration(guest)
    guest.write_width(ALNK, 0x980, 2)
    guest.wait_half(1)
    guest.write_width(ALNK + 8, 8, 1)
    run("alnk-half-ack-deasserts-level", guest, expected={
        "enabled": True, "irq_level": False, "pending": 0,
        "active_half": 1, "completions": 1, "acknowledgments": 1,
        "coalesced_completions": 0, "sample_words": 512})

    guest = DeviceGuest()
    configuration(guest)
    samples(guest)
    guest.write_width(ALNK, 0x980, 2)
    for half in [1, 0, 1]:
        guest.wait_half(half)
    run("alnk-pending-coalesces", guest, expected={
        "enabled": True, "irq_level": True, "pending": 0x80,
        "active_half": 1, "completions": 3, "acknowledgments": 0,
        "coalesced_completions": 2, "skipped_captures": 0,
        "sample_words": 1536, "nonzero_words": 3,
        "sample_digest": digest([0, 1, 0])})

    guest = DeviceGuest()
    configuration(guest)
    guest.write_width(ALNK, 0x980, 2)
    guest.wait_half(1)
    guest.write_width(ALNK, 0x180, 2)
    guest.delay()
    run("alnk-stop-cancels-dma", guest, expected={
        "enabled": False, "irq_level": False, "pending": 0x80,
        "completions": 1, "sample_words": 512, "deadline": 0})

    guest = DeviceGuest()
    configuration(guest)
    samples(guest)
    guest.write_width(ALNK, 0x980, 2)
    for half in [1, 0] * 6:
        guest.wait_half(half)
    run("alnk-twelve-halves-rational-phase", guest, limit=12000000,
        expected={"enabled": True, "irq_level": True, "pending": 0x80,
                  "active_half": 0, "last_half": 1, "completions": 12,
                  "acknowledgments": 0, "coalesced_completions": 11,
                  "skipped_captures": 0, "sample_words": 6144,
                  "nonzero_words": 12, "sample_digest": digest([0, 1] * 6),
                  "latest_half_bytes": HALF_BYTES})


def negative_cases():
    for name, address, value, size, error in [
        ("word-control1", ALNK + 4, 0, 4, "register write or width"),
        ("half-pending", ALNK + 8, 0, 2, "register write or width"),
        ("unknown-register", ALNK + 16, 0, 4, "register write or width"),
        ("unsupported-half-length", ALNK + 32, 256, 2, "DMA half length"),
        ("unsupported-control3", ALNK + 12, 4, 1, "CON3 configuration"),
        ("unsupported-clock", 0x10014, 1, 4, "clock configuration"),
        ("unsupported-route", 0x51030, 1, 4, "pin routing configuration"),
    ]:
        guest = DeviceGuest()
        pc = guest.write_width(address, value, size)
        run(f"alnk-{name}", guest, error=error, fault_pc=pc)

    for name, address in [("unaligned-dma", DMA + 1),
                          ("crossing-dma", 0x01c7f004)]:
        guest = DeviceGuest()
        configuration(guest, address)
        pc = guest.write_width(ALNK, 0x980, 2)
        run(f"alnk-{name}", guest, error="double buffer must be aligned",
            fault_pc=pc)

    for name, address, value in [("enabled-clock-mode", 0x10014, 0x100),
                                 ("enabled-route-mode", 0x51030, 0x40)]:
        guest = DeviceGuest()
        configuration(guest)
        guest.write(address, value)
        pc = guest.write_width(ALNK, 0x980, 2)
        run(f"alnk-{name}", guest, error="enabled configuration", fault_pc=pc)

    guest = DeviceGuest()
    configuration(guest)
    guest.write_width(ALNK, 0x980, 2)
    pc = guest.write(ALNK + 28, DMA + 4)
    run("alnk-enabled-address-change", guest, error="DMA address changed",
        fault_pc=pc)


def main():
    CACHE.mkdir(parents=True, exist_ok=True)
    positive_cases()
    negative_cases()
    print("PASS ALNK exact widths/configuration, rational DMA phase, real samples, IRQ latch and faults")


if __name__ == "__main__":
    main()
