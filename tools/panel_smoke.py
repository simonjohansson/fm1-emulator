#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Drive a firmware package through every preset and panel button, headless.

    python tools/panel_smoke.py FIRMWARE.fwsc [--presets N] [--out DIR] [--keep]

One unpaced run with scripted panel input (FM1_POC_INPUT) and LCD/audio
snapshots (FM1_POC_SNAPSHOT_NS):

1. step PRESETS one detent at a time through N presets (default 128), and
   play a note on each: the screen must change and the note must sound;
2. from HOME, press each function button up to PAGES times, paging through
   its screens: report whether the screen changed;
3. play a final note and stop with a capture: the run must not have faulted.

Failing steps are written as PPM images under --out.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parent.parent
SECOND = 1_000_000_000
MS = 1_000_000
READY = 20 * SECOND            # stock reaches HOME by about 17 guest seconds
PAGES = 6


def controls():
    """(label, qcode) for buttons and (label, phase A, phase B) for encoders."""
    text = (ROOT / "src/include/ui/fm1-controls.h").read_text()
    buttons_block = text.split("FM1_PANEL_BUTTON_CONTACTS(APPLY)", 1)[1].split("\n\n", 1)[0]
    encoders_block = text.split("FM1_PANEL_ENCODER_CONTACTS(APPLY)", 1)[1].split("\n\n", 1)[0]
    buttons = re.findall(r'APPLY\("([^"]+)",\s*(\w+),', buttons_block)
    encoders = re.findall(r'APPLY\("([^"]+)",\s*(\w+),\s*(\w+),', encoders_block)
    return ([(label, code.lower()) for label, code in buttons],
            {label: (a.lower(), b.lower()) for label, a, b in encoders})


class Timeline:
    def __init__(self):
        self.events, self.snapshots, self.labels = [], [], []
        self.t = READY

    def key(self, code, at, hold):
        self.events += [(at, code, 1), (at + hold, code, 0)]

    def turn(self, phases, at, clockwise=True):
        a, b = phases
        first, second = (b, a) if clockwise else (a, b)
        step = 20 * MS
        self.events += [(at, first, 1), (at + step, second, 1),
                        (at + 2 * step, first, 0), (at + 3 * step, second, 0)]

    def snap(self, at, label):
        self.snapshots.append(at)
        self.labels.append(label)
        return len(self.snapshots) - 1


def build(presets, buttons, encoders):
    tl = Timeline()
    tl.snap(tl.t - 100 * MS, "home")
    note = "z"
    preset_steps = []
    for i in range(presets):
        t = tl.t
        tl.turn(encoders["PRESETS"], t)
        loaded = tl.snap(t + 450 * MS, f"preset step {i + 1}")
        tl.key(note, t + 500 * MS, 300 * MS)
        sounding = tl.snap(t + 750 * MS, f"preset step {i + 1} note")
        preset_steps.append((loaded, sounding))
        tl.t += 1200 * MS
    home = dict(buttons)["HOME"]
    button_steps = []
    for label, code in buttons:
        tl.key(home, tl.t, 150 * MS)
        before = tl.snap(tl.t + 500 * MS, f"{label}: HOME before")
        tl.t += 600 * MS
        presses = []
        for page in range(PAGES):
            tl.key(code, tl.t, 150 * MS)
            presses.append(tl.snap(tl.t + 500 * MS, f"{label} press {page + 1}"))
            tl.t += 600 * MS
        button_steps.append((label, before, presses))
    tl.key(home, tl.t, 150 * MS)
    tl.t += 600 * MS
    quiet = tl.snap(tl.t, "final: before note")
    tl.key(note, tl.t + 50 * MS, 300 * MS)
    final = tl.snap(tl.t + 300 * MS, "final: note")
    tl.t += SECOND
    return tl, preset_steps, button_steps, (quiet, final)


def run(firmware, tl, directory):
    env = {k: v for k, v in os.environ.items() if not k.startswith("FM1_POC_")}
    env.update(FM1_POC_STATE_DIR=str(directory),
               FM1_POC_CAPTURE_NS=str(tl.t),
               FM1_POC_MAX_INSTRUCTIONS=str(10 ** 13),
               FM1_POC_UART1_LOG=str(directory / "uart1.bin"),
               FM1_POC_INPUT=",".join(f"{t}:{c}:{d}" for t, c, d in sorted(tl.events)),
               FM1_POC_SNAPSHOT_NS=",".join(str(t) for t in tl.snapshots))
    return subprocess.run(
        [str(ROOT / "emulator"), "--qemu", "-M", "fm1-poc", "-smp", "2",
         "-accel", "tcg,thread=single", "-icount", "shift=3,align=off,sleep=off",
         "-display", "none", "-chardev", "null,id=console", "-serial", "chardev:console",
         "-monitor", "none", "-nodefaults", "-no-user-config",
         "-kernel", str(firmware), "-append", "application"],
        cwd=ROOT, env=env, capture_output=True, text=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("firmware", type=Path)
    parser.add_argument("--presets", type=int, default=128)
    parser.add_argument("--out", type=Path, default=ROOT / ".cache/panel-smoke")
    parser.add_argument("--keep", action="store_true", help="keep the run's state directory")
    args = parser.parse_args()
    buttons, encoders = controls()
    tl, preset_steps, button_steps, final = build(args.presets, buttons, encoders)
    args.out.mkdir(parents=True, exist_ok=True)
    for stale in args.out.glob("*.ppm"):
        stale.unlink()
    with tempfile.TemporaryDirectory(dir=args.out, delete=not args.keep) as tmp:
        directory = Path(tmp)
        if args.keep:
            print(f"run directory: {directory}")
        result = run(args.firmware, tl, directory)
        lines = (directory / "snapshots.jsonl").read_text().splitlines() \
            if (directory / "snapshots.jsonl").exists() else []
        snaps = {s["index"]: s for s in map(json.loads, lines)}
        lcd = {i: (directory / f"snapshot-{i}.ppm").read_bytes() for i in snaps}
        digest = {i: hashlib.sha1(b).hexdigest()[:10] for i, b in lcd.items()}
        failures = []

        def fail(index, message):
            failures.append(message)
            if index in lcd:
                name = re.sub(r"[^A-Za-z0-9]+", "-", tl.labels[index]).strip("-")
                (args.out / f"{index:04d}-{name}.ppm").write_bytes(lcd[index])

        fault = "requested capture" not in result.stderr
        if fault:
            last = max(snaps) if snaps else -1
            failures.append(f"emulator stopped: {result.stderr.strip()} "
                            f"(last snapshot: {tl.labels[last] if last >= 0 else 'none'})")
        print(f"{args.firmware.name}: {len(snaps)}/{len(tl.snapshots)} snapshots, "
              f"{tl.t / SECOND:.0f} guest seconds")

        previous = 0
        silent, unchanged, screens = [], [], set()
        for step, (loaded, sounding) in enumerate(preset_steps, 1):
            if loaded not in snaps or sounding not in snaps:
                break
            screens.add(digest[loaded])
            if digest[loaded] == digest[previous]:
                unchanged.append(step)
                fail(loaded, f"preset step {step}: screen did not change")
            gained = snaps[sounding]["audio_nonzero_words"] - snaps[loaded]["audio_nonzero_words"]
            if gained < 1000 or not snaps[sounding]["visible"]:
                silent.append(step)
                fail(sounding, f"preset step {step}: no sound ({gained} non-zero words)")
            previous = loaded
        print(f"presets: {len(screens)} distinct screens over {len(preset_steps)} steps; "
              f"unchanged {len(unchanged)}, silent {len(silent)}")

        for label, before, presses in button_steps:
            if before not in snaps or presses[-1] not in snaps:
                break
            seen = [digest[before]]
            changes = []
            for index in presses:
                changes.append("new" if digest[index] not in seen else
                               "same" if digest[index] == seen[-1] else "back")
                seen.append(digest[index])
            pages = len(set(seen)) - 1
            print(f"  {label:10s} {pages} new screen(s): {' '.join(changes)}")
            if pages == 0:
                fail(presses[0], f"{label}: no visible change")

        quiet, note = final
        if note in snaps:
            gained = snaps[note]["audio_nonzero_words"] - snaps[quiet]["audio_nonzero_words"]
            midi = snaps[note]["uart1_bytes"] - snaps[quiet]["uart1_bytes"]
            print(f"final note: {gained} non-zero audio words, {midi} MIDI bytes")
            if gained < 1000:
                fail(note, f"final note: no sound ({gained} non-zero words)")
        print(f"{len(failures)} failure(s)")
        for message in failures[:40]:
            print(f"  {message}")
        if failures:
            print(f"failing screens: {args.out}")
    return 1 if fault or failures else 0


if __name__ == "__main__":
    sys.exit(main())
