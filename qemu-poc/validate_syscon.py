#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Exercise the three existing shared words through disposable guest programs.

These checks preserve the current masks and functional ALNK clock. They do
not establish a clock tree, new USB behavior or device/machine reset support.
Fault snapshots use the existing ALNK capture keys to check canonical state.
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
    ALNK, DeviceGuest, FRAMES, HALF_BYTES, RATE, configuration, digest, samples,
)
from validate_peripherals import INSPECTION

HERE = Path(__file__).resolve().parent
CACHE = HERE / ".cache/syscon-validation"
WORDS = (("clk-con1", 0x10010, 3), ("clk-con2", 0x10014, 0xf00),
         ("iomap-con5", 0x51030, 0xc0))
RECORDS = []


@functools.cache
def qemu_sha256():
    return hashlib.sha256(validate.QEMU.read_bytes()).hexdigest()


def inspection(guest, address):
    guest.read_width(address, 4, 4)
    guest.store(4, 5)
    guest.add(5, 4)


def run(name, guest, *, profile="alnk-probe", error=None, fault_pc=None,
        expected=None, readbacks=()):
    directory = CACHE / name
    directory.mkdir(parents=True, exist_ok=True)
    for filename in ("state.json", "state.sram", "state.alnk"):
        (directory / filename).unlink(missing_ok=True)
    data = guest.bytes() + bytes(16)
    image = directory / "controller-input.bin"
    image.write_bytes(data)
    env = {key: value for key, value in os.environ.items()
           if not key.startswith("FM1_POC_")}
    env.update(FM1_POC_STOP_PC=hex(guest.pc), FM1_POC_MAX_INSTRUCTIONS="4000000",
               FM1_POC_STATE_DIR=str(directory), FM1_POC_FRAME_DIR=str(directory))
    command = [*validate.COMMAND, "-kernel", str(image), "-append", profile]
    result = subprocess.run(command, cwd=validate.ROOT, env=env,
                            capture_output=True, text=True, timeout=30)
    (directory / "stdout.txt").write_text(result.stdout)
    (directory / "stderr.txt").write_text(result.stderr)
    validate.check((directory / "state.json").exists(),
                   f"{name}: missing fault/stop snapshot: {result.stderr}")
    state = json.loads((directory / "state.json").read_text())
    validate.check(state["profile"] == profile, f"{name}: observation profile differs")
    if error is not None:
        validate.check(result.returncode != 0 and state["reason"] == error,
                       f"{name}: expected {error!r}, got {state['reason']!r}")
        validate.check(state["pc"] == fault_pc, f"{name}: fault at another instruction")
    else:
        validate.check(result.returncode == 0 and state["pc"] == guest.pc,
                       f"{name}: failed or stopped at another PC: {result.stderr}")
    # Also inspect rejected writes: getters must still expose old canonical
    # values, rather than a value committed before the active-state fault.
    for key, value in (expected or {}).items():
        validate.check(state["alnk"][key] == value,
                       f"{name}: alnk.{key} differs: {state['alnk'][key]!r}")
    ram = (directory / "state.sram").read_bytes()
    validate.check(len(ram) == 512 * 1024, f"{name}: incomplete SRAM capture")
    if readbacks:
        values = struct.unpack_from(f"<{len(readbacks)}I", ram,
                                    INSPECTION - 0x01c00000)
        validate.check(values == tuple(readbacks), f"{name}: shared-word readbacks differ")
    alnk = state["alnk"]
    if alnk["enabled"]:
        periods = alnk["completions"] + 1
        offset = (periods * FRAMES * 1000000000 + RATE - 1) // RATE
        validate.check(alnk["deadline"] == alnk["epoch"] + offset,
                       f"{name}: rational completion phase differs")
    captured = (directory / "state.alnk").read_bytes()
    validate.check(len(captured) == alnk["latest_half_bytes"],
                   f"{name}: sample capture length differs")
    record = {"fixture_sha256": hashlib.sha256(data).hexdigest(),
              "qemu_sha256": qemu_sha256(), "command": command,
              "returncode": result.returncode, "snapshot": state}
    (directory / "result.json").write_text(json.dumps(record, indent=2) + "\n")
    RECORDS.append(record)
    print(f"PASS {name}")
    return state


def idle_readbacks():
    for profile in ("alnk-probe", "application"):
        guest = DeviceGuest()
        guest.literal(5, INSPECTION)
        for _, address, _ in WORDS:
            inspection(guest, address)
        run(f"cold-zero-{profile}", guest, profile=profile, readbacks=(0, 0, 0),
            expected={"clock_control": 0, "iomap_control": 0, "enabled": False})

        guest = DeviceGuest()
        guest.literal(5, INSPECTION)
        current = [0, 0, 0]
        expected = []
        # Exercise every currently accepted field combination and read all
        # words after each write, so adjacent owners cannot alias one another.
        for index, values in enumerate((range(4), (n << 8 for n in range(16)),
                                        (n << 6 for n in range(4)))):
            for value in values:
                guest.write(WORDS[index][1], value)
                current[index] = value
                for _, address, _ in WORDS:
                    inspection(guest, address)
                expected.extend(current)
        run(f"idle-masks-independent-{profile}", guest, profile=profile,
            readbacks=expected, expected={"clock_control": 0xf00,
                                         "iomap_control": 0xc0, "enabled": False})


def active_guest():
    guest = DeviceGuest()
    configuration(guest)
    samples(guest)
    guest.write_width(ALNK, 0x980, 2)
    guest.wait_half(1)
    return guest


def active_unchanged():
    states = []
    actions = ((0x10014, 0), (0x51030, 0), (0x10010, 3), (0x10010, 1))
    for name, selected, clock1 in (("active-baseline", (), 0),
                                    ("active-same-values", (0, 1), 0),
                                    ("active-clk-con1", (0, 1, 2, 3), 1)):
        guest = active_guest()
        for index, (address, value) in enumerate(actions):
            if index in selected:
                guest.write(address, value)
            else:
                # Same retirement/time budget as literal/literal/store. The
                # baseline therefore compares absolute epoch and deadline.
                for _ in range(3):
                    guest.emit(0)
        guest.literal(5, INSPECTION)
        inspection(guest, 0x10010)
        inspection(guest, 0x10014)
        inspection(guest, 0x51030)
        guest.read_width(ALNK + 8, 4, 1)
        guest.store(4, 5)
        guest.add(5, 4)
        guest.read_width(ALNK, 4, 2)
        guest.store(4, 5)
        guest.wait_half(0)
        state = run(name, guest, readbacks=(clock1, 0, 0, 0x80, 0x8980),
                    expected={"enabled": True, "irq_level": True, "pending": 0x80,
                              "active_half": 0, "last_half": 1, "completions": 2,
                              "acknowledgments": 0, "coalesced_completions": 1,
                              "skipped_captures": 0, "sample_words": 1024,
                              "sample_frames": 512, "nonzero_words": 2,
                              "sample_digest": digest([0, 1]),
                              "latest_half_bytes": HALF_BYTES,
                              "clock_control": 0, "iomap_control": 0})
        captured = (CACHE / name / "state.alnk").read_bytes()
        validate.check(captured == struct.pack("<I", 0x55667788) + bytes(HALF_BYTES - 4),
                       f"{name}: shared-word write changed captured SRAM samples")
        states.append(state)
    for state in states[1:]:
        validate.check(state["alnk"] == states[0]["alnk"],
                       "active shared-word writes changed ALNK phase/counters/state")
        validate.check(state["instructions"] == states[0]["instructions"] and
                       state["virtual_ns"] == states[0]["virtual_ns"],
                       "active comparison guests did not retain equal clock/retirement budgets")
    return states[0]["alnk"]["epoch"]


def active_rejections(epoch):
    for name, address, value, error in (
        ("active-clock-change", 0x10014, 0x100,
         "ALNK0 clock changed while DMA is enabled"),
        ("active-route-change", 0x51030, 0x40,
         "ALNK0 routing changed while DMA is enabled"),
    ):
        guest = active_guest()
        pc = guest.write(address, value)
        run(name, guest, error=error, fault_pc=pc,
            expected={"clock_control": 0, "iomap_control": 0, "epoch": epoch,
                      "enabled": True, "pending": 0x80, "irq_level": True,
                      "active_half": 1, "last_half": 0, "completions": 1,
                      "acknowledgments": 0, "coalesced_completions": 0,
                      "skipped_captures": 0, "sample_words": 512,
                      "nonzero_words": 1, "sample_digest": digest([0])})
        validate.check((CACHE / name / "state.alnk").read_bytes() ==
                       struct.pack("<I", 0x11223344) + bytes(HALF_BYTES - 4),
                       f"{name}: rejected write changed captured samples")


def invalid_accesses():
    errors = ("unsupported USB clock selector fields",
              "unsupported ALNK0 clock configuration",
              "unsupported ALNK0 pin routing configuration")
    for (name, address, _), error in zip(WORDS, errors):
        guest = DeviceGuest()
        guest.write(0x10010, 3)
        guest.write(0x10014, 0xf00)
        guest.write(0x51030, 0xc0)
        pc = guest.write(address, 4 if address == 0x10010 else 1)
        run(f"invalid-bits-{name}", guest, error=error, fault_pc=pc,
            expected={"clock_control": 0xf00, "iomap_control": 0xc0, "enabled": False})

    for name, address, _ in WORDS:
        for size in (1, 2):
            for write in (False, True):
                guest = DeviceGuest()
                guest.write(0x10014, 0xf00)
                guest.write(0x51030, 0xc0)
                pc = (guest.write_width(address, 0, size) if write else
                      guest.read_width(address, 4, size))
                run(f"{name}-{'write' if write else 'read'}-width-{size}", guest,
                    error=f"unmapped access at 0x{address:08x}", fault_pc=pc,
                    expected={"clock_control": 0xf00, "iomap_control": 0xc0,
                              "enabled": False})

    for name, address in (("clock-gap", 0x10018), ("iomap-gap", 0x51034)):
        guest = DeviceGuest()
        pc = guest.read_width(address, 4, 4)
        run(name, guest, error=f"unmapped access at 0x{address:08x}", fault_pc=pc)

    for name, address, value, key in (
        ("enable-nonzero-clock", 0x10014, 0x100, "clock_control"),
        ("enable-nonzero-route", 0x51030, 0x40, "iomap_control"),
    ):
        guest = DeviceGuest()
        configuration(guest)
        guest.write(address, value)
        pc = guest.write_width(ALNK, 0x980, 2)
        run(name, guest, error="unsupported ALNK0 enabled configuration", fault_pc=pc,
            expected={key: value, "enabled": False, "completions": 0})


def main():
    global CACHE
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence-dir", type=Path, default=CACHE)
    args = parser.parse_args()
    CACHE = args.evidence_dir.resolve()
    idle_readbacks()
    epoch = active_unchanged()
    active_rejections(epoch)
    invalid_accesses()
    summary = {"qemu_sha256": qemu_sha256(), "words": WORDS,
               "clock_tree_validated": False, "reset_validated": False,
               "cases": RECORDS}
    (CACHE / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(f"PASS {len(RECORDS)} syscon cases: canonical readbacks, widths/masks, "
          "precommit active faults and unchanged ALNK phase/pending/samples")


if __name__ == "__main__":
    main()
