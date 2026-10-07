#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Check common hardware through different optional observation profiles.

Each profile executes identical bytes. A second layout moves the MMIO code,
and renamed copies retain the same bytes. The guest initializes all state
whose readback is compared. These are synthetic controller regressions, not
evidence that additional production firmware boots.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import struct
import subprocess

import validate
from validate_felucca_devices import ALNK, DeviceGuest, configuration
from validate_peripherals import ENTRY, INSPECTION

HERE = Path(__file__).resolve().parent
CACHE = HERE / ".cache/machine-profile-validation"
PROFILES = ("probe", "diag", "alnk-probe", "application")
LAYOUTS = (("direct", 0), ("moved", 16))
READBACKS = (
    (0x50040, 4, 0x55AA),       # PB OUT
    (0x501C8, 4, 0x100),        # PH DIR
    (0x5101C, 4, 0x20),         # IOMAP CON0: flash/SFC routing
    (0x51020, 4, 0x10),         # IOMAP CON1: SPI1 routing
    (ALNK, 2, 0x180),
    (ALNK + 4, 2, 0x5000),
    (ALNK + 8, 1, 0),
    (ALNK + 12, 1, 0x83),
    (0x10014, 4, 0),            # ALNK clock word
    (0x51030, 4, 0),            # ALNK routing word
    (0x01EEF104, 4, 0x7000),    # Source 11 enabled at priority 3
    (0x01EEF11C, 4, 0x30000000),  # Source 63 enabled at priority 1
)
NEGATIVES = (
    ("alnk-width", ALNK + 4, 0, 4, "unsupported ALNK0 register write or width"),
    ("irq-source", 0x01EEF108, 1, 4, "unsupported IRQ source enable"),
)


def make_guest(prefix_words, negative=None):
    guest = DeviceGuest()
    for _ in range(prefix_words):
        guest.emit(0)  # Already accepted NOP; shifts the controller program.
    guest.literal(14, 0x01C7A000, special=True)
    guest.literal(13, 0x01C7C000, special=True)
    guest.literal(0, 0)
    guest.emit(0xe064, (11 << 8) | 0x80)  # ICFG = r0; no global IRQ admission.
    guest.write(0x10800, 0)
    guest.write(0x10900, 0)
    guest.write(0x01EEF104, 0)
    guest.write(0x01EEF11C, 0)
    guest.write(0x01EEF1A8, 0)
    guest.write(0x500C0, 1)  # NOR chip select released before routing writes.
    guest.write(0x50040, 0x55AA)
    guest.write(0x501C8, 0x100)
    guest.write(0x5101C, 0x20)
    guest.write(0x51020, 0x10)
    configuration(guest)
    guest.write(0x01EEF104, 0x7000)
    guest.write(0x01EEF11C, 0x30000000)
    guest.literal(5, INSPECTION)
    for index, (address, size, _) in enumerate(READBACKS):
        guest.read_width(address, 4, size)
        guest.store(4, 5, index * 4)
    fault_pc = None
    if negative is not None:
        _, address, value, size, _ = negative
        fault_pc = guest.write_width(address, value, size)
    return guest, fault_pc


def run(profile, layout, image_name, guest, fault_pc=None, error=None):
    directory = CACHE / layout / profile / image_name
    directory.mkdir(parents=True, exist_ok=True)
    for filename in ("state.json", "state.sram", "state.alnk"):
        (directory / filename).unlink(missing_ok=True)
    data = guest.bytes() + bytes(16)
    image = directory / f"{image_name}.bin"
    image.write_bytes(data)
    env = {key: value for key, value in os.environ.items()
           if not key.startswith("FM1_POC_")}
    env.update(FM1_POC_STOP_PC=hex(guest.pc), FM1_POC_MAX_INSTRUCTIONS="10000",
               FM1_POC_STATE_DIR=str(directory), FM1_POC_FRAME_DIR=str(directory))
    command = [*validate.COMMAND, "-kernel", str(image), "-append", profile]
    result = subprocess.run(command, cwd=validate.ROOT, env=env,
                            capture_output=True, text=True, timeout=30)
    (directory / "stdout.txt").write_text(result.stdout)
    (directory / "stderr.txt").write_text(result.stderr)
    record = {"profile": profile, "layout": layout,
              "fixture_sha256": hashlib.sha256(data).hexdigest(),
              "command": command, "returncode": result.returncode,
              "stop_pc": guest.pc}
    label = f"{layout}/{profile}/{image_name}"
    if error is not None:
        validate.check(result.returncode != 0 and error in result.stderr,
                       f"{label}: expected explicit fault {error!r}: {result.stderr}")
        match = re.search(r"at PC 0x([0-9a-f]+) after (\d+) instructions", result.stderr)
        validate.check(match is not None and int(match[1], 16) == fault_pc,
                       f"{label}: missing or wrong guest fault location: {result.stderr}")
        record.update(reason=error, fault_pc=int(match[1], 16),
                      instructions=int(match[2]), diagnostic=result.stderr.strip())
    else:
        validate.check(result.returncode == 0, f"{label}: {result.stderr}")
        if profile in ("application", "alnk-probe"):
            state_path = directory / "state.json"
            ram_path = directory / "state.sram"
            validate.check(state_path.exists() and ram_path.exists(),
                           f"{label}: missing generic state/SRAM capture")
            state = json.loads(state_path.read_text())
            validate.check(state["profile"] == profile,
                           f"{label}: wrong observation profile")
            ram = ram_path.read_bytes()
            validate.check(len(ram) == 512 * 1024, f"{label}: incomplete SRAM capture")
            values = list(struct.unpack_from("<12I", ram, INSPECTION - 0x01C00000))
        else:
            state = json.loads(result.stdout)
            values = state["inspection"]
        validate.check(state["pc"] == guest.pc, f"{label}: wrong stop PC")
        validate.check(values == [item[2] for item in READBACKS],
                       f"{label}: common MMIO readbacks differ: {values}")
        validate.check(state["instructions"] == guest.instructions,
                       f"{label}: retirement count differs")
        validate.check(not state["in_irq"] and state["irq_entries"] == 0,
                       f"{label}: idle configuration unexpectedly admitted an IRQ")
        record.update(readbacks=values, instructions=state["instructions"],
                      virtual_ns=state["virtual_ns"])
    (directory / "result.json").write_text(json.dumps(record, indent=2) + "\n")
    return record


def main():
    CACHE.mkdir(parents=True, exist_ok=True)
    records = []
    layout_hashes = []
    for layout, prefix_words in LAYOUTS:
        guest, _ = make_guest(prefix_words)
        same_layout = []
        for profile in PROFILES:
            for image_name in ("controller-input", "renamed-input"):
                record = run(profile, layout, image_name, guest)
                records.append(record)
                same_layout.append(record)
            for negative in NEGATIVES:
                failed, fault_pc = make_guest(prefix_words, negative)
                records.append(run(profile, layout, negative[0], failed,
                                   fault_pc=fault_pc, error=negative[4]))
        validate.check(len({item["fixture_sha256"] for item in same_layout}) == 1,
                       f"{layout}: profile/name cases did not use identical bytes")
        layout_hashes.append(same_layout[0]["fixture_sha256"])
        print(f"PASS {layout}: common hardware under four profiles and renamed images")
    validate.check(len(set(layout_hashes)) == 2, "layout cases did not move the guest code")
    summary = {"qemu_sha256": hashlib.sha256(validate.QEMU.read_bytes()).hexdigest(),
               "profiles": list(PROFILES), "readbacks": list(READBACKS),
               "production_firmware_compatibility": False, "cases": records}
    (CACHE / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print("PASS identical MMIO readbacks and explicit unsupported faults across profiles/layouts")


if __name__ == "__main__":
    main()
