# SPDX-License-Identifier: GPL-2.0-or-later
"""64-bit divide (E1F6) and multiply-accumulate (E1FC), CLZ (E180) and rotate
(E1C4). Expected values were measured on an FM-1."""
from pathlib import Path
import tempfile
import unittest

from support import ROOT, Guest, guest_state

FLAGS = 0x89abcde5


class PairArithmeticTests(unittest.TestCase):
    def setUp(self):
        directory = ROOT / ".cache/tests"
        directory.mkdir(parents=True, exist_ok=True)
        temporary = tempfile.TemporaryDirectory(dir=directory)
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)

    def run_form(self, flags, values, *words):
        guest = Guest()
        guest.literal(7, flags)
        guest.emit(0xe064, 0x7580)  # PSR = r7.
        for reg, value in values.items():
            guest.literal(reg, value)
        guest.emit(*words)
        state = guest_state(self.directory, guest)
        self.assertEqual(state["instructions"], guest.instructions)
        return state

    def test_unsigned_divide_gives_a_64_bit_quotient(self):
        cases = [(0x100000005, 3, 0x55555557), (100, 7, 14),
                 (0xffffffffffffffff, 0xffffffff, 0x100000001),
                 (0x200000001, 0, 0), (0x1000000007, 0x10, 0x100000000)]
        for dividend, divisor, quotient in cases:
            with self.subTest(dividend=dividend, divisor=divisor):
                # r3_r2 = r1_r0 / r4 (u), as stock FM-1 firmware uses it.
                state = self.run_form(FLAGS, {0: dividend & 0xffffffff, 1: dividend >> 32,
                                              4: divisor}, 0xe1f6, 0x2400)
                self.assertEqual(state["registers"][2:4],
                                 [quotient & 0xffffffff, quotient >> 32])
                self.assertEqual(state["specials"][5], FLAGS)

    def test_signed_multiply_accumulate_matches_hardware(self):
        # Felucca's rD+1_rD += rA * rB (s), captured on an FM-1 with PSR flags
        # preset to 0xf: only C changes, the 64-bit carry out.
        M = (1 << 64) - 1
        cases = [(0, -1, 1, M, False), ((1 << 63) - 1, 1, 1, 1 << 63, False),
                 (M, 1, 1, 0, True), (5, -2, 3, M, False),
                 (0, -(1 << 31), -(1 << 31), 1 << 62, False),
                 (1 << 63, -1, 1, (1 << 63) - 1, True), (1, -1, -1, 2, False)]
        for accumulator, left, right, result, carry in cases:
            with self.subTest(accumulator=accumulator, left=left, right=right):
                # r11_r10 += r6 * r14 (s): x = 0xbe60 | signed bit 12
                state = self.run_form(0x8000000f, {10: accumulator & 0xffffffff,
                                                   11: accumulator >> 32,
                                                   6: left & 0xffffffff, 14: right & 0xffffffff},
                                      0xe1fc, 0xbe60)
                self.assertEqual(state["registers"][10:12],
                                 [result & 0xffffffff, result >> 32])
                self.assertEqual(state["specials"][5], 0x8000000d | (2 if carry else 0))

    def test_signed_divide_matches_hardware(self):
        # Felucca's r1_r0 = r1_r0 / r2 (s), captured on an FM-1: truncation
        # toward zero, zero divisor gives 0, INT64_MIN / -1 wraps.
        M = (1 << 64) - 1
        cases = [(-7, 2, -3), (7, -2, -3), (-7, -2, 3), (7, 0, 0),
                 (1000000000000, 3, 333333333333), (-1000000000000, 7, -142857142857),
                 (-(1 << 63), -1, -(1 << 63))]
        for dividend, divisor, quotient in cases:
            with self.subTest(dividend=dividend, divisor=divisor):
                state = self.run_form(FLAGS, {0: dividend & 0xffffffff, 1: (dividend & M) >> 32,
                                              2: divisor & 0xffffffff}, 0xe1f6, 0x1200)
                self.assertEqual(state["registers"][0:2],
                                 [quotient & 0xffffffff, (quotient & M) >> 32])

    def test_multiply_accumulate_sets_only_carry(self):
        cases = [(0x200000001, 3, 4, 0x20000000d, False),
                 (0xffffffffffffffff, 1, 1, 0, True),
                 (0, 0xffffffff, 0xffffffff, 0xfffffffe00000001, False),
                 (0xffffffff, 1, 1, 0x100000000, False)]
        for flags in (0x80000000, 0x8000000f):
            for accumulator, left, right, result, carry in cases:
                with self.subTest(flags=flags, accumulator=accumulator):
                    # r11_r10 += r6 * r14 (u)
                    state = self.run_form(flags, {10: accumulator & 0xffffffff,
                                                  11: accumulator >> 32, 6: left, 14: right},
                                          0xe1fc, 0xae60)
                    self.assertEqual(state["registers"][10:12],
                                     [result & 0xffffffff, result >> 32])
                    self.assertEqual(state["specials"][5], (flags & ~2) | (2 if carry else 0))

    def test_count_leading_zeros(self):
        for value, count in ((0, 32), (1, 31), (0x80000000, 0), (0xffff, 16)):
            with self.subTest(value=value):
                state = self.run_form(FLAGS, {2: value}, 0xe180, 0x1200)  # r1 = clz(r2)
                self.assertEqual(state["registers"][1], count)

    def test_rotate_right(self):
        # r7 = r4 <> 18, from the stock firmware's SHA-256 message schedule.
        state = self.run_form(FLAGS, {4: 0x12345678}, 0xe1c4, 0x7142)
        self.assertEqual(state["registers"][7], 0x159e048d)
        self.assertEqual(state["registers"][4], 0x12345678)


if __name__ == "__main__":
    unittest.main()
