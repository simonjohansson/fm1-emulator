#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Capture and compare the unchanged Felucca application's own splash screen.

The separate Rust process supplies an independent guest-generated framebuffer.
This gate establishes the splash milestone, not sustained home/audio operation.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import struct
import subprocess
import sys

import run_felucca
import validate_felucca

HERE = Path(__file__).resolve().parent
CHECKPOINT = 0x0200D0C2
EVIDENCE = HERE / ".cache/felucca-validation/splash"
PPM_HEADER = b"P6\n240 240\n255\n"


def check(condition, message):
    if not condition:
        raise SystemExit(message)


def pixels(path):
    blob = path.read_bytes()
    check(blob.startswith(PPM_HEADER) and len(blob) == len(PPM_HEADER) + 240 * 240 * 3,
          f"invalid full 240 by 240 RGB framebuffer: {path}")
    return blob[len(PPM_HEADER):]


def verify_qemu(blobs, executable_sha):
    state = json.loads((EVIDENCE / "state.json").read_text())
    run = json.loads((EVIDENCE / "run.json").read_text())
    check(run["input_hashes"] == run_felucca.HASHES and
          run["qemu_binary_sha256"] == executable_sha and run["returncode"] == 0,
          "splash capture identity or process outcome differs")
    check(state["profile"] == "felucca" and state["reason"] == "checkpoint reached" and
          state["pc"] == CHECKPOINT,
          "unchanged application did not finish its splash drawing")
    check(state["virtual_ns"] == (state["instructions"] + 1) * 8,
          "splash capture functional clock differs from documented 8 ns")
    lcd = state["lcd"]
    check(lcd["visible"] and not lcd["busy"] and lcd["pixels_written"] == 69120 and
          lcd["commands"] > 0 and lcd["dma_transfers"] > 0 and lcd["completed_transfers"] > 0,
          "LCD did not complete the guest's visible splash transfers")
    check(state["specials"][14] == 0x01C79EAC and state["specials"][13] == 0x01C7C000,
          "splash stack frame or supervisor stack differs")
    check(state["irq_entries"] == state["rti_count"] == state["timer_expirations"] ==
          state["acknowledgments"] == 0 and not state["pending"] and not state["in_irq"],
          "splash checkpoint is not before application interrupt activity")
    check(state["watchdog_arms"] == 1 and state["watchdog_feeds"] >= 1 and
          state["watchdog_expirations"] == 0 and state["p33_transfers"] > 0 and
          state["nor_transactions"] > 0 and state["nor_read_bytes"] > 0 and
          state["guard_checks"] > 0,
          "splash lacks real watchdog, persistence or guarded initialization evidence")
    expected_guards = {
        "emu_control": 12, "debug_enable": 0x3F0030,
        "debug_message": 0, "emu_message": 0, "debug_unlocked": False,
        "write_enable": 3,
        "write_windows": [[0x01C74000, 0x01C740FF], [0x01C7A000, 0x01C7A0FF], [0, 0]],
        "pc_windows": [[0x02000120, 0x02065344], [0x01C00000, 0x01C00B47]],
        "stack_windows": [[0x01C74100, 0x01C7C000], [0x01C74100, 0x01C7C000]],
    }
    check(state.get("guards") == expected_guards and state["write_enable"] == 3,
          "guest stack/write/PC guard configuration or error state differs")
    ram = (EVIDENCE / "state.sram").read_bytes()
    check(len(ram) == 0x80000, "splash SRAM capture is incomplete")
    check(ram[:0xB48] == blobs["felucca.bin"][0x65224:0x65D6C],
          "copied RAM flash-driver code changed before splash")
    for start, end in [(0x74000, 0x74100), (0x7A000, 0x7A100), (0x7FD80, 0x7FE00)]:
        check(ram[start:end] == bytes(end - start), "stack guard band or mailbox changed")
    check(struct.unpack_from("<5I", ram, 0x7F20) == (0, 0, 0x01C7FE08, 0, 0),
          "saved loader handoff changed before splash")
    check(ram[0x7C000:0x7C08C] == bytes(0x8C) and
          struct.unpack_from("<3I", ram, 0x7C08C) == (0x42475244, 0, 1),
          "splash crash/debug record or pending bootguard differs")
    vectors = b"".join(struct.pack("<I", 0x02000158 + 6 * index) for index in range(128))
    check(ram[0x7FE00:0x80000] == vectors, "pre-input splash fatal vectors differ")
    check(struct.unpack_from("<2I", ram, 0xD140) == (0, 0),
          "guest LCD or P33 polling timed out")
    return state, ram


def compare(blobs, qemu, qemu_ram, executable_sha, reference_run):
    directory = Path(reference_run["evidence_dir"])
    reference = json.loads((directory / "state.json").read_text())
    check(reference["profile"] == "felucca-reference" and
          reference["reason"] == "checkpoint reached" and reference["pc"] == CHECKPOINT and
          reference["lcd"]["visible"] and reference["lcd"]["pixels_written"] == 69120,
          "separate reference did not produce the selected splash")
    check(reference["irq_entries"] == reference["rti_count_observed"] ==
          reference["audio"]["frames"] == 0,
          "reference splash unexpectedly passed interrupt/audio startup")
    rgb = pixels(EVIDENCE / "lcd.ppm")
    expected = pixels(directory / "lcd.ppm")
    check(rgb == expected and any(rgb), "actual guest splash pixels differ from the independent reference")
    reference_ram = (directory / "state.sram").read_bytes()
    check(len(reference_ram) == 0x80000, "reference splash SRAM is incomplete")
    # Device timings leave polling registers and stack temporaries different.
    # Compare immutable code, loader/vector state and guest boot/crash records.
    for start, end in [(0, 0xB48), (0x7F20, 0x7F34), (0x74000, 0x74100),
                       (0x7A000, 0x7A100), (0x7C000, 0x7C098), (0x7FD80, 0x80000)]:
        check(reference_ram[start:end] == qemu_ram[start:end],
              f"meaningful splash memory differs at SRAM offset 0x{start:x}")
    check(reference["specials"][13:15] == qemu["specials"][13:15],
          "splash application/supervisor stacks differ from reference")
    for address, text in [(0x02065148, b"FELUCCA\0"), (0x0206494B, b"MULTI-ENGINE SYNTH\0")]:
        offset = address - run_felucca.ENTRY
        check(blobs["felucca.bin"][offset:offset + len(text)] == text,
              "selected splash string identity differs")
    return {
        "passed": True, "milestone": "unchanged guest FELUCCA splash", "completed_boot": False,
        "checkpoint": CHECKPOINT, "input_hashes": run_felucca.HASHES,
        "source_build_provenance": "selected artifacts identified; no rebuild/source-HEAD attribution",
        "qemu_executable_sha256": executable_sha,
        "reference_executable_sha256": reference_run["executable_sha256"],
        "rgb_sha256": hashlib.sha256(rgb).hexdigest(), "width": 240, "height": 240,
        "pixels_written": qemu["lcd"]["pixels_written"],
        "qemu_instructions": qemu["instructions"], "reference_instructions": reference["instructions"],
        "qemu_virtual_ns": qemu["virtual_ns"], "guards": qemu["guards"],
        "comparison": "exact independent guest RGB pixels and meaningful initialized memory",
        "clock_comparison": "functional clocks differ; polling registers, counts and time not required equal",
        "flash_comparison": "reached pre-splash persistence areas erased FF in both; physical application region differs",
        "remaining": "input, ADC, audio, USB, timer interrupts and sustained home screen follow splash",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--firmware-dir", type=Path, default=run_felucca.FIRMWARE)
    args = parser.parse_args()
    blobs = run_felucca.checked_inputs(args.firmware_dir)
    offset = CHECKPOINT - run_felucca.ENTRY
    check(blobs["felucca.bin"][offset:offset + 6] == bytes.fromhex("c4 ff 40 c0 c7 01") and
          re.search(r"^\s*200d0c2:\s+c4 ff 40 c0 c7 01\s", blobs["felucca.dis"].decode(), re.M),
          "splash checkpoint differs from the selected ELF/disassembly mapping")
    executable = HERE / ".cache/build/qemu-system-pi32v2"
    executable_sha = validate_felucca.sha256(executable)
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    (EVIDENCE / "validation.json").unlink(missing_ok=True)
    subprocess.run([sys.executable, str(HERE / "run_felucca.py"),
                    "--firmware-dir", str(args.firmware_dir), "--label", "splash",
                    "--stop-pc", hex(CHECKPOINT), "--expect-reason", "checkpoint reached"], check=True)
    check(validate_felucca.sha256(executable) == executable_sha,
          "QEMU executable changed during splash capture")
    qemu, ram = verify_qemu(blobs, executable_sha)
    reference_run = validate_felucca.capture_reference(args.firmware_dir, checkpoint=CHECKPOINT,
                                                     label="splash-reference", limit=100000000)
    check(validate_felucca.sha256(executable) == executable_sha,
          "QEMU executable changed during the independent reference capture")
    summary = compare(blobs, qemu, ram, executable_sha, reference_run)
    (EVIDENCE / "validation.json").write_text(json.dumps(summary, indent=2) + "\n")
    print("PASS unchanged Felucca splash: exact independent 240 by 240 guest pixels,")
    print("completed LCD transfers, intact guards/stacks/boot state and no watchdog or LCD/P33 timeout")
    print(f"Evidence: {EVIDENCE}")


if __name__ == "__main__":
    main()
