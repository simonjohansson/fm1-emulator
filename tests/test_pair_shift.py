# SPDX-License-Identifier: GPL-2.0-or-later
"""Reached 64-bit register-pair left shift and incoming count aliases."""
from pathlib import Path
import tempfile
import unittest

from support import ROOT, Guest, guest_state, run_guest


class PairShiftTests(unittest.TestCase):
    def setUp(self):
        directory = ROOT / ".cache/tests"
        directory.mkdir(parents=True, exist_ok=True)
        temporary = tempfile.TemporaryDirectory(dir=directory)
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)

    def check_shift(self, pair, index, low, high, shift):
        guest = Guest()
        guest.literal(7, 0x89abcde5)
        guest.emit(0xe064, 0x7580)
        guest.literal(pair, low)
        guest.literal(pair + 1, high)
        if index not in (pair, pair + 1):
            guest.literal(index, shift)
        guest.emit(0xe1d8, pair << 12 | index << 8)
        state = guest_state(self.directory, guest)
        expected = ((high << 32 | low) << shift) & 0xffffffffffffffff if shift < 64 else 0
        self.assertEqual(state["registers"][pair:pair + 2],
                         [expected & 0xffffffff, expected >> 32])
        if index not in (pair, pair + 1):
            self.assertEqual(state["registers"][index], shift)
        self.assertEqual(state["specials"][5], 0x89abcde5)
        self.assertEqual(state["instructions"], guest.instructions)

    def test_word_boundary_carry_and_large_counts_preserve_flags(self):
        for shift in (0, 1, 31, 32, 63, 64, 65, 0xffffffff):
            with self.subTest(shift=shift):
                self.check_shift(0, 6, 0x89abcdef, 0x12345678, shift)

    def test_count_alias_uses_incoming_low_or_high_register(self):
        self.check_shift(14, 14, 32, 0x12345678, 32)
        self.check_shift(14, 15, 0x89abcdef, 1, 1)

    def test_skipped_shift_leaves_pair_untouched(self):
        guest = Guest()
        guest.literal(0, 1)
        guest.literal(14, 0x89abcdef)
        guest.literal(15, 1)
        guest.emit(0xea20, 1)
        guest.emit(0xe1d8, 0xef00)
        state = guest_state(self.directory, guest)
        self.assertEqual(state["registers"][14:16], [0x89abcdef, 1])
        self.assertEqual(state["instructions"], guest.instructions - 1)

    def test_unqualified_direction_and_odd_pair_remain_rejected(self):
        for operand in (0x0602, 0x1600):
            with self.subTest(operand=operand):
                guest = Guest()
                guest.literal(6, 0)
                guest.emit(0xe1d8, operand)
                result = run_guest(self.directory, guest)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("unsupported instruction 0xe1d8", result.stderr)
                self.assertIn("after 1 instructions", result.stderr)


if __name__ == "__main__":
    unittest.main()
