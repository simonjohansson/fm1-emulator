# SPDX-License-Identifier: GPL-2.0-or-later
"""Random panel stress: its input timeline, and optionally a real package."""

import argparse
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from support import ROOT
import stress

PACKAGES = [Path(p) for p in os.environ.get("FM1_STOCK_PACKAGES", "").split(os.pathsep) if p]


def options(**values):
    defaults = dict(seconds=2, boot_seconds=20, hold_ms=5, gap_ms=5, phase_ms=5,
                    encoder_share=0.4, seed=7)
    defaults.update(values)
    return argparse.Namespace(**defaults)


class StressTimelineTests(unittest.TestCase):
    def setUp(self):
        self.buttons, self.encoders = stress.panel_controls()

    def test_controls_cover_every_button_key_and_encoder(self):
        self.assertEqual(len(self.buttons), 41)
        self.assertEqual(len(self.encoders), 7)

    def test_same_seed_gives_the_same_events(self):
        first = stress.timeline(options(), self.buttons, self.encoders)
        second = stress.timeline(options(), self.buttons, self.encoders)
        other = stress.timeline(options(seed=8), self.buttons, self.encoders)
        self.assertEqual(first, second)
        self.assertNotEqual(first[0], other[0])

    def test_actions_start_after_boot_and_never_overlap(self):
        args = options(seconds=3)
        events, actions, end = stress.timeline(args, self.buttons, self.encoders)
        start = int(args.boot_seconds * stress.SECOND)
        self.assertTrue(all(start <= t < end for t, _, _ in events))
        down = set()
        for t, code, pressed in sorted(events):
            if pressed:
                self.assertNotIn(code, down)
                down.add(code)
            else:
                down.remove(code)
        self.assertFalse(down)
        times = [a["t"] for a in actions]
        self.assertTrue(all(b - a >= 10 * stress.MS for a, b in zip(times, times[1:])))
        self.assertTrue(any("encoder" in a for a in actions))
        self.assertTrue(any("button" in a for a in actions))


@unittest.skipUnless(PACKAGES, "set FM1_STOCK_PACKAGES to stress real packages")
class StressRunTests(unittest.TestCase):
    def test_short_burst_passes(self):
        for package in PACKAGES:
            with self.subTest(package=package.name), tempfile.TemporaryDirectory() as tmp:
                result = subprocess.run(
                    [sys.executable, str(ROOT / "tests/stress.py"), str(package),
                     "--seconds", "5", "--seed", "11", "--output", str(Path(tmp) / "run")],
                    cwd=ROOT / "tests", capture_output=True, text=True, timeout=600)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
