#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Validate three unchanged display guest frames against a separate oracle."""
import hashlib
import json
import os
from pathlib import Path
import subprocess

import validate

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
CACHE = HERE / ".cache/display-validation"
QEMU_FRAMES = HERE / ".cache/display-frames"
RUST_FRAMES = CACHE / "rust-frames"
FILES = {
    "tests/fixtures/display/firmware.bin": "3bc59ff7d09de123174b49ea786582a1e137b2e1609074b5189c9fb024f19ba2",
    "tests/fixtures/display/firmware.elf": "929077b8a5e30e0ea19bbc3f800ac857906265acefc10f9f7ce42a366b525cbb",
    "tests/fixtures/display/firmware.dis": "ce82aa5c8e459761019a4782510ee74711d8246c51541d2299ab489f7ad5875f",
}


def run(command, directory):
    result = subprocess.run(command, cwd=ROOT,
                            env=dict(os.environ, FM1_POC_FRAME_DIR=str(directory)),
                            capture_output=True, text=True, timeout=45)
    validate.check(result.returncode == 0, result.stderr)
    return json.loads(result.stdout)


def pixels(directory, frame):
    header = b"P6\n240 240\n255\n"
    image = (directory / f"frame-{frame}.ppm").read_bytes()
    validate.check(image.startswith(header) and len(image) == len(header) + 240 * 240 * 3,
                   "invalid frame PPM")
    return image[len(header):]


def pixel(image, x, y):
    offset = (y * 240 + x) * 3
    return int.from_bytes(image[offset:offset + 3], "big")


def without_counter(image):
    # The guest samples different functional clocks. Keep labels, background
    # and all 44 key tiles in the exact comparison, excluding only rows 80..109.
    return image[:80 * 240 * 3] + image[110 * 240 * 3:]


def main():
    for directory in [CACHE, QEMU_FRAMES, RUST_FRAMES]:
        directory.mkdir(parents=True, exist_ok=True)
    for filename, digest in FILES.items():
        validate.check(hashlib.sha256((ROOT / filename).read_bytes()).hexdigest() == digest,
                       f"display fixture hash differs: {filename}")
    qemu = run([*validate.COMMAND, "-kernel", "tests/fixtures/display/firmware.bin",
                "-append", "display"], QEMU_FRAMES)
    oracle = run(["mise", "exec", "--", "cargo", "run", "--manifest-path",
                  str(HERE / "reference/Cargo.toml"), "--locked", "--offline", "--",
                  "display", "tests/fixtures/display/firmware.elf"], RUST_FRAMES)
    for field in ["pc", "irq_entries", "probe"]:
        validate.check(qemu[field] == oracle[field], f"display {field} differs")
    expected_probe = [int(word, 16) for word in
                      json.loads((HERE / "fixtures.json").read_text())["probe"]["expected_hex"]]
    validate.check(qemu["probe"] == expected_probe, "display startup probe differs from hardware")
    validate.check(qemu["pc"] == 0x020004fa and qemu["frames"] == 3 and qemu["display_visible"],
                   "guest did not complete three visible frames")
    for i in [0, 1, 2, 5, 6, 7, 8, 9]:
        validate.check(qemu["inspection"][i] == oracle["inspection"][i],
                       f"display foundation word {i} differs")
    validate.check(qemu["inspection"][:3] == [0xc001cafe, 0, 0x5a17] and
                   qemu["inspection"][9] == 0x50f00d, "display startup milestone failed")
    for field in ["irq_entries", "rti_count", "timer_expirations", "acknowledgments"]:
        validate.check(qemu[field] == 1, f"display: expected one {field}")
    validate.check(not qemu["pending"] and not qemu["in_irq"] and
                   qemu["last_irq_handler"] == 0x020002c0, "display startup IRQ did not complete")
    validate.check(qemu["shift_edges"] == 176 * 4 and qemu["latch_edges"] == 11 * 4,
                   "startup plus three guest matrix scans did not run")
    validate.check(qemu["virtual_ns"] == (qemu["instructions"] + 1) * 8,
                   "display checkpoint changed guest clock accounting")
    frames, images = [], []
    for frame in range(1, 4):
        qstate = json.loads((QEMU_FRAMES / f"frame-{frame}.json").read_text())
        rstate = json.loads((RUST_FRAMES / f"frame-{frame}.json").read_text())
        image, reference_image = pixels(QEMU_FRAMES, frame), pixels(RUST_FRAMES, frame)
        validate.check(without_counter(image) == without_counter(reference_image),
                       f"frame {frame}: pixels outside sampled-timer digits differ")
        expected_matrix = [0xe1 if frame == 2 else 0x1e1] + [0x1e1] * 10
        validate.check(qstate["matrix"] == rstate["matrix"] == expected_matrix,
                       f"frame {frame}: guest key closure did not scan correctly")
        for field in ["frame", "pc", "sp", "ssp", "visible", "pixels_written", "registers"]:
            validate.check(qstate[field] == rstate[field], f"frame {frame}: {field} differs")
        validate.check(qstate["specials"][1:] == rstate["specials"][1:],
                       f"frame {frame}: saved/restored guest state differs")
        for state in [qstate, rstate]:
            validate.check(state["specials"][0] in [0x02000280, 0x02000282],
                           f"frame {frame}: interrupt return PC left the guest polling loop")
        validate.check(qstate["sp"] == 0x01c7a000 and qstate["ssp"] == 0x01c7c000,
                       "display helper stack was not restored")
        validate.check(qstate["visible"] and qstate["pixels_written"] > 240 * 240 and
                       qstate["dma_transfers"] > 0, "frame bypassed guest SPI/DMA")
        validate.check(pixel(image, 0, 0) == 0x101010 and pixel(image, 24, 14) == 0xffffff,
                       "background or title pixel differs")
        validate.check(pixel(image, 24, 202) == (0xf7cb00 if frame == 2 else 0x313031),
                       f"frame {frame}: OCT-minus tile differs")
        frames.append({"frame": frame, "qemu_guest_instructions": qstate["instructions"],
                       "rust_guest_instructions": rstate["instructions"],
                       "qemu_display_ticks": qstate["display_ticks"],
                       "rust_display_ticks": rstate["display_ticks"],
                       "pixels_sha256": hashlib.sha256(image).hexdigest(),
                       "static_pixels_sha256": hashlib.sha256(without_counter(image)).hexdigest(),
                       "pixels_outside_counter_match": True,
                       "physical_input": "0:4 pressed" if frame == 2 else "released"})
        images.append(image)
    for i in range(1, 3):
        validate.check(frames[i]["qemu_display_ticks"] > frames[i - 1]["qemu_display_ticks"],
                       "guest timer sample did not advance between frames")
        validate.check(images[i][80 * 240 * 3:110 * 240 * 3] !=
                       images[i - 1][80 * 240 * 3:110 * 240 * 3],
                       "guest sampled timer pixels did not change")
    validate.check(without_counter(images[0]) == without_counter(images[2]) and
                   without_counter(images[0]) != without_counter(images[1]),
                   "guest press/release did not repaint the key tile")
    (CACHE / "qemu-display.json").write_text(json.dumps(qemu, indent=2) + "\n")
    (CACHE / "rust-display.json").write_text(json.dumps(oracle, indent=2) + "\n")
    (CACHE / "validation.json").write_text(json.dumps({"passed": True, "files": FILES,
        "entry": 0x02000120, "frame_checkpoint": 0x020004fa,
        "guest_patched": False, "host_drawing": False, "clock_comparison": False,
        "frames": frames}, indent=2) + "\n")
    print(f"PASS display: unchanged startup, three guest SPI/DMA frames, "
          f"{qemu['instructions']} QEMU / {oracle['instructions']} Rust guest instructions")
    print("PASS pixels: title/background/all 44 key tiles match; OCT-minus released/pressed/released")
    print(f"Frames: {QEMU_FRAMES}")


if __name__ == "__main__":
    main()
