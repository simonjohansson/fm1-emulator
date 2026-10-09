# SPDX-License-Identifier: GPL-2.0-or-later
"""Stock FM-1 firmware scenarios driven by scripted panel input.

Set FM1_STOCK_PACKAGES to one or more .fwsc packages (os.pathsep-separated).
Each scenario boots the package unpaced, presses host keys at fixed guest
times (FM1_POC_INPUT) and captures state at a fixed guest time.
"""
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

from support import QEMU, ROOT, environment

PACKAGES = [Path(p) for p in os.environ.get("FM1_STOCK_PACKAGES", "").split(os.pathsep) if p]
SECOND = 1_000_000_000
READY = 20 * SECOND          # stock reaches HOME by about 17 guest seconds
CAPTURE = READY + 2 * SECOND


def press(code, at, hold=0.15):
    return [(at, code, 1), (at + int(hold * SECOND), code, 0)]


def turn(phase_a, phase_b, at, detents, clockwise=True):
    """Quadrature detents; clockwise closes phase B first."""
    first, second = (phase_b, phase_a) if clockwise else (phase_a, phase_b)
    events, step = [], 20_000_000
    for n in range(detents):
        t = at + n * 4 * step
        events += [(t, first, 1), (t + step, second, 1),
                   (t + 2 * step, first, 0), (t + 3 * step, second, 0)]
    return events


SCENARIOS = {
    "home": [],
    "env": press("p", READY),
    "note": press("z", READY, hold=3),
    "octave": press("v", READY) + press("z", READY + SECOND // 2, hold=0.3),
    "preset": turn("f13", "f14", READY, 2),
}


def run(package, name, directory):
    out = directory / name
    out.mkdir()
    script = ",".join(f"{t}:{code}:{down}" for t, code, down in SCENARIOS[name])
    settings = dict(FM1_POC_STATE_DIR=str(out), FM1_POC_CAPTURE_NS=str(CAPTURE),
                    FM1_POC_MAX_INSTRUCTIONS="100000000000",
                    FM1_POC_UART1_LOG=str(out / "uart1.bin"))
    if script:
        settings["FM1_POC_INPUT"] = script
    result = subprocess.run(
        [str(QEMU), "--qemu", "-M", "fm1-poc", "-smp", "2", "-accel", "tcg,thread=single",
         "-icount", "shift=3,align=off,sleep=off", "-display", "none",
         # As the launcher: a console attaches the USB host side.
         "-chardev", "null,id=console", "-serial", "chardev:console",
         "-monitor", "none", "-nodefaults", "-no-user-config",
         "-kernel", str(package), "-append", "application"],
        cwd=ROOT, env=environment(**settings), capture_output=True, text=True, timeout=600)
    uart = out / "uart1.bin"
    return {"stderr": result.stderr,
            "state": json.loads((out / "state.json").read_text()) if (out / "state.json").exists() else None,
            "lcd": (out / "lcd.ppm").read_bytes() if (out / "lcd.ppm").exists() else b"",
            "midi": uart.read_bytes() if uart.exists() else b""}


@unittest.skipUnless(PACKAGES, "set FM1_STOCK_PACKAGES to run stock firmware scenarios")
class StockScenarioTests(unittest.TestCase):
    def test_panel_scenarios(self):
        for package in PACKAGES:
            with self.subTest(package=package.name), tempfile.TemporaryDirectory() as tmp:
                with ThreadPoolExecutor(len(SCENARIOS)) as pool:
                    runs = dict(zip(SCENARIOS, pool.map(
                        lambda name: run(package, name, Path(tmp)), SCENARIOS)))
                for name, result in runs.items():
                    self.assertIn("requested capture", result["stderr"], f"{name}: {result['stderr']}")
                    self.assertTrue(result["state"]["lcd"]["visible"], f"{name}: display is on")
                home = runs["home"]
                self.assertTrue(home["state"]["lcd"]["visible"])
                self.assertGreater(home["state"]["lcd"]["commands"], 0)
                self.assertTrue(runs["env"]["lcd"] != home["lcd"], "ENV opens a page")
                self.assertTrue(runs["preset"]["lcd"] != home["lcd"], "PRESETS changes the preset")
                note = runs["note"]
                self.assertGreater(note["state"]["alnk"]["nonzero_words"],
                                   home["state"]["alnk"]["nonzero_words"] + 10_000, "a held key sounds")
                self.assertEqual(note["midi"][:1], b"\x90", "a key sends MIDI note-on")
                octave = runs["octave"]["midi"]
                self.assertEqual(octave[:1], b"\x90", "OCT+ then a key sends MIDI note-on")
                self.assertEqual(octave[1], note["midi"][1] + 12, "OCT+ raises the note an octave")


if __name__ == "__main__":
    unittest.main()
