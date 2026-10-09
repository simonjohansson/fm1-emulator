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

    def check_immediate(self, operand, low, high):
        guest = Guest()
        guest.literal(7, 0x89abcde5)
        guest.emit(0xe064, 0x7580)
        pair = operand >> 12
        guest.literal(pair, low)
        guest.literal(pair + 1, high)
        guest.emit(0xe1d0, operand)
        state = guest_state(self.directory, guest)
        mode, shift = (operand >> 10) & 3, (operand & 15) | ((operand >> 8) & 3) * 16
        value = high << 32 | low
        if mode == 0:
            value = (value << shift) & 0xffffffffffffffff
        elif mode == 2:
            value >>= shift
        else:
            value = ((value - (1 << 64) if value >> 63 else value) >> shift) & 0xffffffffffffffff
        self.assertEqual(state["registers"][pair:pair + 2], [value & 0xffffffff, value >> 32])
        self.assertEqual(state["specials"][5], 0x89abcde5)
        self.assertEqual(state["instructions"], guest.instructions)

    def test_immediate_pair_shifts_follow_vendor_forms(self):
        # E1D0/6804 (r7_r6 >>= 4) was reached in Felucca after a preset change.
        for operand in (0x6804, 0x0100, 0x0004, 0x0801, 0x0a0f, 0x0d0e, 0x0e00,
                        0xa100, 0xc00c, 0xc90f, 0x0000):
            with self.subTest(operand=hex(operand)):
                self.check_immediate(operand, 0x89abcdef, 0xf2345678)

    def test_unqualified_immediate_pair_shifts_remain_rejected(self):
        for operand in (0x6814, 0x0404, 0x1004):
            with self.subTest(operand=hex(operand)):
                guest = Guest()
                guest.literal(6, 0)
                guest.emit(0xe1d0, operand)
                result = run_guest(self.directory, guest)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("unsupported instruction 0xe1d0", result.stderr)
                self.assertIn("after 1 instructions", result.stderr)

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
