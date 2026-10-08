#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Run the pinned Felucca application headlessly or in a native macOS window."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import struct
import subprocess
import sys

HERE = Path(__file__).resolve().parent
FIRMWARE = Path("/Users/simonjohansson/src/Felucca/build")
HASHES = {
    "felucca.bin": "12a4b4ea47248467f566ec3b6984b08f2f89d6ef5a8e9e494cab5184e8fadb36",
    "felucca.elf": "9404c41dcd5c7516243e99cdbae1a4e731ea0c69e13dea397a8db343244b5deb",
    "felucca.dis": "0d992c3113a1765f21a10bfb233bef81ee908073f26a16ea1f9d13c6fe897618",
}
ENTRY = 0x02000120


def checked_inputs(directory):
    blobs = {name: (directory / name).read_bytes() for name in HASHES}
    for name, data in blobs.items():
        if hashlib.sha256(data).hexdigest() != HASHES[name]:
            raise SystemExit(f"selected input differs: {name}")
    elf = blobs["felucca.elf"]
    header = struct.unpack_from("<16sHHIIIIIHHHHHH", elf)
    if header[0][:6] != b"\x7fELF\x01\x01" or header[4] != ENTRY:
        raise SystemExit("unexpected ELF format or application entry")
    for i in range(header[10]):
        kind, offset, vma, lma, size, _, _, _ = struct.unpack_from(
            "<8I", elf, header[5] + i * header[9])
        if kind == 1 and size:
            if lma < ENTRY or blobs["felucca.bin"][lma - ENTRY:lma - ENTRY + size] != elf[offset:offset + size]:
                raise SystemExit("ELF load segment differs from selected raw image")
    return blobs


def run_cocoa(firmware_dir):
    if sys.platform != "darwin":
        raise SystemExit("This launcher uses the native macOS Cocoa display.")
    qemu = HERE / ".cache/build/qemu-system-pi32v2"
    if not qemu.is_file():
        raise SystemExit("Build first: mise exec python@3.13.15 -- python qemu-poc/build.py")
    backends = subprocess.run([str(qemu), "-display", "help"], capture_output=True, text=True, check=True)
    if "cocoa" not in backends.stdout.split():
        raise SystemExit("Enable the display: mise exec python@3.13.15 -- python qemu-poc/build.py --reconfigure")
    audio = subprocess.run([str(qemu), "-audiodev", "help"], capture_output=True, text=True, check=True)
    if "coreaudio" not in audio.stdout.split():
        raise SystemExit("Enable macOS sound: mise exec python@3.13.15 -- python qemu-poc/build.py")
    env = {key: value for key, value in os.environ.items() if not key.startswith("FM1_POC_")}
    print("Opening the Felucca display with macOS sound. Close the window to quit.", flush=True)
    os.chdir(HERE.parent)
    # Preserve 8 ns/instruction; sleep when ahead without requiring real-time catch-up.
    # Ordinary application handoff has no validation observer or instruction limit.
    os.execve(qemu, [str(qemu), "-name", "FM-1 Felucca", "-M", "fm1-poc",
                    "-accel", "tcg,thread=single", "-icount", "shift=3,align=off,sleep=on",
                    "-display", "cocoa,zoom-to-fit=on,zoom-interpolation=off",
                    "-audiodev", "coreaudio,id=fm1,out.frequency=44100,out.channels=2",
                    "-global", "fm1-alnk.audiodev=fm1",
                    "-serial", "none", "-monitor", "none", "-nodefaults",
                    "-kernel", str(firmware_dir / "felucca.bin"), "-append", "application"], env)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--firmware-dir", type=Path, default=FIRMWARE)
    parser.add_argument("--label", default="first-attempt")
    parser.add_argument("--display", choices=("none", "cocoa"), default="none",
                        help="Cocoa runs continuously until its window closes")
    parser.add_argument("--max-instructions", type=int,
                        help="headless instruction limit (default: 100000000)")
    parser.add_argument("--stop-pc", type=lambda value: int(value, 0))
    parser.add_argument("--expect-reason")
    args = parser.parse_args()
    if args.display == "cocoa" and any(value is not None for value in
                                       (args.max_instructions, args.stop_pc, args.expect_reason)):
        parser.error("Cocoa mode cannot use --max-instructions, --stop-pc, or --expect-reason")
    if args.max_instructions is None:
        args.max_instructions = 100_000_000
    if not re.fullmatch(r"[a-z0-9-]+", args.label) or args.max_instructions <= 0:
        parser.error("use a simple lowercase label and a positive instruction limit")
    blobs = checked_inputs(args.firmware_dir)
    if args.display == "cocoa":
        run_cocoa(args.firmware_dir.resolve())
        return
    directory = HERE / ".cache/felucca-validation" / args.label
    directory.mkdir(parents=True, exist_ok=True)
    for name in ("state.json", "state.sram", "state.alnk", "lcd.ppm"):
        (directory / name).unlink(missing_ok=True)
    env = {key: value for key, value in os.environ.items() if not key.startswith("FM1_POC_")}
    settings = {"FM1_POC_STATE_DIR": str(directory),
                "FM1_POC_MAX_INSTRUCTIONS": str(args.max_instructions)}
    if args.stop_pc is not None:
        settings["FM1_POC_STOP_PC"] = hex(args.stop_pc)
    env.update(settings)
    command = [str(HERE / ".cache/build/qemu-system-pi32v2"), "-M", "fm1-poc",
               "-accel", "tcg,thread=single", "-icount", "shift=3,align=off,sleep=off",
               "-display", "none", "-serial", "none", "-monitor", "none", "-nodefaults",
               "-kernel", str(args.firmware_dir / "felucca.bin"), "-append", "felucca"]
    metadata = {"binary_sha256": HASHES["felucca.bin"], "input_hashes": HASHES,
                "qemu_binary_sha256": hashlib.sha256(Path(command[0]).read_bytes()).hexdigest(),
                "elf_load_segments_match_raw": True,
                "source_build_provenance": "artifact identity only; no rebuild attribution",
                "entry": ENTRY, "functional_ns_per_instruction": 8,
                "flash_initialization": "1 MiB erased FF, unchanged app at physical offset 0x4120",
                "sram_initialization": "cold zero noinit/loader; A5 RAM text/data/BSS/pool/mailbox",
                "command": command, "environment": settings}
    try:
        result = subprocess.run(command, env=env, capture_output=True, text=True, timeout=180)
    except subprocess.TimeoutExpired as error:
        for name, output in (("stdout.txt", error.stdout), ("stderr.txt", error.stderr)):
            text = output.decode(errors="replace") if isinstance(output, bytes) else output or ""
            (directory / name).write_text(text)
        metadata.update(returncode=None, reason="host timeout", host_timeout_seconds=180)
        (directory / "run.json").write_text(json.dumps(metadata, indent=2) + "\n")
        raise SystemExit(f"host timeout; partial evidence: {directory}") from error
    (directory / "stdout.txt").write_text(result.stdout)
    (directory / "stderr.txt").write_text(result.stderr)
    metadata["returncode"] = result.returncode
    if not (directory / "state.json").exists():
        (directory / "run.json").write_text(json.dumps(metadata, indent=2) + "\n")
        raise SystemExit(f"no guest state captured: {result.stderr.strip()}")
    state = json.loads((directory / "state.json").read_text())
    pc = state["pc"]
    ram = (directory / "state.sram").read_bytes()
    region, base = (ram, 0x01c00000) if 0x01c00000 <= pc < 0x01c80000 else (blobs["felucca.bin"], ENTRY)
    metadata["fault_opcode_bytes"] = region[pc - base:pc - base + 6].hex(" ") if base <= pc < base + len(region) else None
    lines = blobs["felucca.dis"].decode().splitlines()
    match = next((i for i, line in enumerate(lines) if re.match(rf"\s*{pc:x}:", line)), None)
    nearby = lines[max(0, match - 8):match + 9] if match is not None else ["No matching instruction in selected disassembly"]
    (directory / "nearby.dis").write_text("\n".join(nearby) + "\n")
    metadata["state"] = state
    (directory / "run.json").write_text(json.dumps(metadata, indent=2) + "\n")
    print(f"{state['reason']} at 0x{pc:08x}, {state['instructions']:,} instructions")
    print(f"Evidence: {directory}")
    if args.expect_reason is not None:
        if state["reason"] != args.expect_reason or (result.returncode == 0) != (state["reason"] == "checkpoint reached"):
            raise SystemExit("observed stop differs from required outcome")


if __name__ == "__main__":
    main()
