# SPDX-License-Identifier: GPL-2.0-or-later
"""Programmed DMA half sizes control capture boundaries and virtual cadence."""
import json
from pathlib import Path
import struct
import subprocess
import tempfile
import unittest

from support import COMMAND, INSPECTION, ROOT, Guest, environment

ALNK = 0x12e00


def narrow_write(guest, address, value, byte=False):
    guest.literal(1, address)
    guest.literal(0, value)
    guest.emit(0x0790 if byte else 0x0690)


def configured_guest(words):
    guest = Guest()
    guest.write(INSPECTION, 0x12345678)
    guest.write(INSPECTION + words * 4, 0xabcdef01)
    narrow_write(guest, ALNK, 0x180)
    narrow_write(guest, ALNK + 4, 0x5000)
    narrow_write(guest, ALNK + 12, 0x83, byte=True)
    narrow_write(guest, ALNK + 32, words)
    guest.write(ALNK + 28, INSPECTION)
    narrow_write(guest, ALNK, 0x980)
    return guest


class AudioDMATests(unittest.TestCase):
    def setUp(self):
        directory = ROOT / ".cache/tests"
        directory.mkdir(parents=True, exist_ok=True)
        temporary = tempfile.TemporaryDirectory(dir=directory)
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)

    def run_dma(self, guest):
        image = self.directory / "guest.bin"
        image.write_bytes(guest.bytes() + bytes(16))
        return subprocess.run(
            [*COMMAND, "-kernel", str(image), "-append", "alnk-probe"],
            cwd=ROOT, env=environment(FM1_POC_STOP_PC=hex(guest.pc),
                                      FM1_POC_MAX_INSTRUCTIONS="2000000",
                                      FM1_POC_STATE_DIR=str(self.directory)),
            capture_output=True, text=True, timeout=30)

    def test_both_sizes_switch_halves_at_programmed_cadence(self):
        for words in (256, 512):
            with self.subTest(words=words):
                guest = configured_guest(words)
                for half in range(2):
                    guest.literal(2, ALNK + 8)
                    poll = guest.pc
                    guest.emit(0xee50, 0x3020)  # r3 = pending byte, no writeback.
                    guest.branch_zero(3, poll)
                    narrow_write(guest, ALNK + 8, 8, byte=True)
                narrow_write(guest, ALNK, 0x180)
                result = self.run_dma(guest)
                self.assertEqual(result.returncode, 0, result.stderr)
                state = json.loads((self.directory / "state.json").read_text())
                audio = state["alnk"]
                self.assertEqual(audio["completions"], 2)
                self.assertEqual(audio["acknowledgments"], 2)
                self.assertEqual(audio["sample_words"], words * 2)
                self.assertEqual(audio["sample_frames"], words)
                self.assertEqual(audio["latest_half_bytes"], words * 4)
                self.assertEqual(audio["last_half"], 1)
                self.assertEqual(audio["nonzero_words"], 2)
                self.assertEqual(audio["skipped_captures"], 0)
                self.assertEqual((self.directory / "state.alnk").read_bytes(),
                                 struct.pack("<I", 0xabcdef01) + bytes(words * 4 - 4))
                elapsed = state["virtual_ns"] - audio["epoch"]
                expected = (words * 1_000_000_000 + 44099) // 44100
                self.assertGreaterEqual(elapsed, expected)
                self.assertLess(elapsed, expected + 10000)

    def test_length_change_while_enabled_is_rejected(self):
        guest = configured_guest(256)
        narrow_write(guest, ALNK + 32, 512)
        result = self.run_dma(guest)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("ALNK0 DMA length changed while enabled", result.stderr)


if __name__ == "__main__":
    unittest.main()
