#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Independently verify the unchanged Felucca application's startup copies.

This gate stops before guard configuration, persistence, LCD or audio startup.
It establishes application-entry initialization only, not a completed boot.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import struct
import subprocess
import sys

HERE = Path(__file__).resolve().parent
FIRMWARE = Path("/Users/simonjohansson/src/Felucca/build")
EVIDENCE = HERE / ".cache/felucca-validation/startup-copies"
ENTRY = 0x02000120
SRAM_BASE = 0x01C00000
CHECKPOINT = 0x0200CC64
HASHES = {
    "felucca.bin": "12a4b4ea47248467f566ec3b6984b08f2f89d6ef5a8e9e494cab5184e8fadb36",
    "felucca.elf": "9404c41dcd5c7516243e99cdbae1a4e731ea0c69e13dea397a8db343244b5deb",
    "felucca.dis": "0d992c3113a1765f21a10bfb233bef81ee908073f26a16ea1f9d13c6fe897618",
}
# These are selected ELF/disassembly interface facts, not guest source code.
SECTIONS = {
    ".ram_text": (0x01C00000, 0xB48, 0x02065344),
    ".data": (0x01C08000, 0x218, 0x02065E8C),
    ".bss": (0x01C08220, 0xA60C, None),
    ".pool": (0x01C20000, 0x3F920, None),
    ".noinit": (0x01C7C000, 0x2928, None),
}


def check(condition, message):
    if not condition:
        raise SystemExit(message)


def checked_inputs(directory):
    blobs = {name: (directory / name).read_bytes() for name in HASHES}
    for name, blob in blobs.items():
        check(hashlib.sha256(blob).hexdigest() == HASHES[name],
              f"immutable Felucca input differs: {name}")
    elf, binary = blobs["felucca.elf"], blobs["felucca.bin"]
    header = struct.unpack_from("<16sHHIIIIIHHHHHH", elf)
    check(header[0][:7] == b"\x7fELF\x01\x01\x01" and header[4] == ENTRY,
          "selected ELF is not the expected little-endian application")
    loads = []
    for index in range(header[10]):
        segment = struct.unpack_from("<8I", elf, header[5] + index * header[9])
        kind, offset, vma, lma, size, memory_size, flags, alignment = segment
        if kind != 1 or not size:
            continue
        raw_offset = lma - ENTRY
        check(raw_offset >= 0 and raw_offset + size <= len(binary) and
              elf[offset:offset + size] == binary[raw_offset:raw_offset + size],
              f"ELF file-backed load segment differs at LMA 0x{lma:08x}")
        loads.append(segment)
    sections = [struct.unpack_from("<10I", elf, header[6] + index * header[11])
                for index in range(header[12])]
    string_section = sections[header[13]]
    names = elf[string_section[4]:string_section[4] + string_section[5]]
    found = {}
    for section in sections:
        name = names[section[0]:names.index(0, section[0])].decode()
        if name not in SECTIONS:
            continue
        expected_vma, expected_size, expected_lma = SECTIONS[name]
        check((section[3], section[5]) == (expected_vma, expected_size),
              f"selected section bounds differ: {name}")
        if expected_lma is not None:
            segment = next((load for load in loads if
                            load[2] <= section[3] and
                            section[3] + section[5] <= load[2] + load[4]), None)
            check(segment is not None and
                  segment[3] + section[3] - segment[2] == expected_lma,
                  f"selected section load address differs: {name}")
        else:
            check(section[1] == 8, f"expected a NOLOAD section: {name}")
        found[name] = section
    check(found.keys() == SECTIONS.keys(), "missing selected ELF section")
    offset = CHECKPOINT - ENTRY
    check(binary[offset:offset + 4] == bytes.fromhex("d0 ec b0 00") and
          re.search(r"^\s*200cc64:\s+d0 ec b0 00\s", blobs["felucca.dis"].decode(), re.M),
          "startup checkpoint does not match selected binary/disassembly")
    return binary


def verify(directory, binary):
    state = json.loads((directory / "state.json").read_text())
    run = json.loads((directory / "run.json").read_text())
    check(run["input_hashes"] == HASHES and run["binary_sha256"] == HASHES["felucca.bin"],
          "capture is not bound to the selected immutable inputs")
    check(run["returncode"] == 0 and state["profile"] == "felucca" and
          state["reason"] == "checkpoint reached" and state["pc"] == CHECKPOINT,
          "capture did not reach the startup-copy checkpoint")
    check(state["instructions"] > 0 and
          state["virtual_ns"] == (state["instructions"] + 1) * 8,
          "capture functional clock differs from the documented 8 ns setting")
    ram = (directory / "state.sram").read_bytes()
    check(len(ram) == 0x80000, "captured SRAM must contain the whole 512 KiB")
    for name, (vma, size, lma) in SECTIONS.items():
        actual = ram[vma - SRAM_BASE:vma - SRAM_BASE + size]
        if lma is not None:
            expected = binary[lma - ENTRY:lma - ENTRY + size]
            check(actual == expected, f"guest startup did not copy {name} exactly")
        elif name != ".noinit":
            check(actual == bytes(size), f"guest startup did not zero all of {name}")
    check(ram[0x7FD80:0x7FE00] == bytes(0x80), "guest mailbox was not cleared")
    noinit = bytearray(SECTIONS[".noinit"][1])
    struct.pack_into("<3I", noinit, 0x8C, 0x42475244, 0, 1)
    check(ram[0x7C000:0x7E928] == noinit,
          "cold noinit differs from the guest's only expected bootguard update")
    check(struct.unpack_from("<5I", ram, 0x7F20) == (0, 0, 0x01C7FE08, 0, 0),
          "guest entry did not save the documented cold loader handoff")
    expected_vectors = b"".join(struct.pack("<I", 0x02000158 + 6 * index)
                                for index in range(128))
    check(ram[0x7FE00:0x80000] == expected_vectors,
          "guest did not initialize every fatal interrupt vector")
    check(state["specials"][14] == 0x01C79EAC and
          state["specials"][13] == 0x01C7C000,
          "startup stack frame or supervisor stack differs")
    check(state["irq_entries"] == state["rti_count"] == state["timer_expirations"] ==
          state["acknowledgments"] == 0 and not state["pending"] and not state["in_irq"],
          "startup-copy checkpoint unexpectedly serviced a timer interrupt")
    check(state["watchdog_arms"] == 1 and state["watchdog_feeds"] >= 1 and
          state["watchdog_expirations"] == 0 and state["guard_checks"] > 0 and
          state["write_enable"] == 0,
          "watchdog or pre-guard startup state differs")
    check(state["nor_transactions"] == state["nor_read_bytes"] == 0 and
          not state["lcd"]["visible"] and not state["lcd"]["busy"] and
          all(state["lcd"][key] == 0 for key in
              ["pixels_written", "commands", "dma_transfers"]),
          "capture is not before persistence and LCD startup")
    return {
        "passed": True, "milestone": "application-entry startup copies",
        "completed_boot": False, "checkpoint": CHECKPOINT,
        "input_hashes": HASHES, "instructions": state["instructions"],
        "virtual_ns": state["virtual_ns"],
        "checks": ["ELF loads match raw binary", "exact RAM code and data copies",
                   "complete BSS and pool zeroing", "mailbox cleared",
                   "cold loader handoff", "all fatal vectors initialized",
                   "cold noinit and guest bootguard", "stack frame and watchdog"],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--firmware-dir", type=Path, default=FIRMWARE)
    parser.add_argument("--evidence-dir", type=Path, default=EVIDENCE)
    parser.add_argument("--capture", action="store_true",
                        help="run the bounded application before checking its evidence")
    args = parser.parse_args()
    binary = checked_inputs(args.firmware_dir)
    if args.capture:
        if args.evidence_dir != EVIDENCE:
            parser.error("--capture uses the default startup-copies evidence directory")
        subprocess.run([sys.executable, str(HERE / "run_felucca.py"),
                        "--firmware-dir", str(args.firmware_dir),
                        "--label", "startup-copies", "--stop-pc", hex(CHECKPOINT),
                        "--expect-reason", "checkpoint reached"], check=True)
    summary = verify(args.evidence_dir, binary)
    (args.evidence_dir / "validation.json").write_text(json.dumps(summary, indent=2) + "\n")
    print("PASS unchanged Felucca startup: exact RAM code/data, zero BSS/pool/mailbox,")
    print("cold loader/noinit, bootguard, vectors, stacks and watchdog")
    print(f"Evidence: {args.evidence_dir}")


if __name__ == "__main__":
    main()
