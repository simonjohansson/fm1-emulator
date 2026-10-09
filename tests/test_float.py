# SPDX-License-Identifier: GPL-2.0-or-later
"""Single-precision FPU (E53F) against results captured on a real FM-1."""
from pathlib import Path
import tempfile
import unittest

from support import ROOT, Guest, guest_state, run_guest


class FloatTests(unittest.TestCase):
    def setUp(self):
        directory = ROOT / ".cache/tests"
        directory.mkdir(parents=True, exist_ok=True)
        temporary = tempfile.TemporaryDirectory(dir=directory)
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)

    def run_op(self, operand, a, b=0, d=0, psr=0):
        """Execute E53F operand with r1 = a, r2 = b, r3 = d; return r3 and PSR."""
        guest = Guest()
        guest.literal(1, a)
        guest.literal(2, b)
        guest.literal(3, d)
        guest.literal(7, psr)
        guest.emit(0xe064, 0x7580)          # psr = r7
        guest.emit(0xe53f, operand)
        state = guest_state(self.directory, guest)
        return state["registers"][3], state["specials"][5]

    def test_arithmetic_rounds_to_nearest_even(self):
        cases = [
            (0x3210, 0x3fc00000, 0x40100000, 0x40700000),  # 1.5 + 2.25
            (0x3211, 0x3f800001, 0x3f7ffffe, 0x34800000),  # exact difference
            (0x3212, 0x3f800001, 0x3f7ffffe, 0x3f800000),  # product rounds to 1
            (0x3213, 0x3fc00000, 0x40100000, 0x3f2aaaab),  # 1.5 / 2.25
            (0x3211, 0x80000000, 0x00000000, 0x80000000),  # -0 - 0
            (0x3212, 0x00000000, 0x80000000, 0x80000000),  # 0 * -0
        ]
        for operand, a, b, expected in cases:
            with self.subTest(operand=hex(operand), a=hex(a), b=hex(b)):
                self.assertEqual(self.run_op(operand, a, b)[0], expected)

    def test_multiply_accumulate_rounds_the_product_first(self):
        # -1 + 1.0000001 * 0.99999988: a fused result would be nonzero.
        self.assertEqual(self.run_op(0x3217, 0x3f800001, 0x3f7ffffe, 0xbf800000)[0], 0)
        self.assertEqual(self.run_op(0x3218, 0x3fc00000, 0x40100000, 0x3f800000)[0], 0xc0180000)

    def test_min_max_set_compare_flags_and_order_signed_zeros(self):
        cases = [
            (0x3215, 0x3fc00000, 0x40100000, 0x3fc00000, 8),
            (0x3216, 0x3f800001, 0x3f7ffffe, 0x3f800001, 2),
            (0x3215, 0x00000000, 0x80000000, 0x80000000, 6),
            (0x3216, 0x80000000, 0x00000000, 0x00000000, 6),
        ]
        for operand, a, b, expected, flags in cases:
            with self.subTest(operand=hex(operand), a=hex(a), b=hex(b)):
                result, psr = self.run_op(operand, a, b, psr=0xf0)
                self.assertEqual(result, expected)
                self.assertEqual(psr, 0xf0 | flags)

    def test_conversions(self):
        cases = [
            (0x328f, 0x01000001, 0x4b800000),  # itof rounds to nearest even
            (0x328f, 0x80000000, 0xcf000000),
            (0x329f, 0xffffffff, 0x4f800000),  # unsigned itof
            (0x321f, 0x40f00000, 7),           # 7.5 truncates
            (0x321f, 0xbfc00000, 0xffffffff),  # -1.5 truncates toward zero
            (0x321f, 0xcf000000, 0x80000000),
        ]
        for operand, source, expected in cases:
            with self.subTest(operand=hex(operand), source=hex(source)):
                self.assertEqual(self.run_op(operand, 0, source)[0], expected)

    def test_unmeasured_values_fault(self):
        for operand, a, b in ((0x3213, 0x3f800000, 0), (0x3210, 0x7f800000, 0),
                              (0x321f, 0, 0x4f000000)):
            with self.subTest(operand=hex(operand), a=hex(a), b=hex(b)):
                guest = Guest()
                guest.literal(1, a)
                guest.literal(2, b)
                guest.emit(0xe53f, operand)
                result = run_guest(self.directory, guest)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("unmeasured", result.stderr)


if __name__ == "__main__":
    unittest.main()
