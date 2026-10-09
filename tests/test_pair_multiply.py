# SPDX-License-Identifier: GPL-2.0-or-later
"""Reached 32x32 -> 64-bit register-pair multiply (E1F8), alone and parallel."""
from pathlib import Path
import tempfile
import unittest

from support import INSPECTION, ROOT, Guest, guest_state, run_guest

FLAGS = 0x89abcde5


def signed(value):
    return value - (1 << 32) if value & 0x80000000 else value


class PairMultiplyTests(unittest.TestCase):
    def setUp(self):
        directory = ROOT / ".cache/tests"
        directory.mkdir(parents=True, exist_ok=True)
        temporary = tempfile.TemporaryDirectory(dir=directory)
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)

    def seeded(self):
        guest = Guest()
        guest.literal(7, FLAGS)
        guest.emit(0xe064, 0x7580)  # PSR = r7.
        return guest

    def check(self, operand, values):
        guest = self.seeded()
        for reg, value in values.items():
            guest.literal(reg, value)
        guest.emit(0xe1f8, operand)
        state = guest_state(self.directory, guest)
        pair, left, right = (operand >> 13) * 2, (operand >> 4) & 15, (operand >> 8) & 15
        a, b = values[left], values[right]
        product = (signed(a) * signed(b)) if operand & 0x1000 else a * b
        product &= 0xffffffffffffffff
        self.assertEqual(state["registers"][pair:pair + 2],
                         [product & 0xffffffff, product >> 32])
        self.assertEqual(state["specials"][5], FLAGS)
        self.assertEqual(state["instructions"], guest.instructions)

    def test_unsigned_product_fills_the_pair(self):
        # Reached in Felucca after a preset change: r13_r12 = r0 * r8 (u).
        self.check(0xc800, {0: 0xfffffffe, 8: 0x89abcdef})

    def test_signed_products(self):
        self.check(0x5220, {2: 0x80000001})                 # r5_r4 = r2 * r2 (s)
        self.check(0x1400, {0: 0xffffff85, 4: 0x12345678})  # r1_r0 = r0 * r4 (s)

    def test_pair_may_alias_an_incoming_operand(self):
        self.check(0x32c0, {2: 0xfffffff0, 12: 0x7fffffff})  # r3_r2 = r12 * r2 (s)
        self.check(0x2e20, {2: 0xdeadbeef, 14: 0xcafef00d})  # r3_r2 = r2 * r14 (u)

    def test_parallel_with_word_load(self):
        guest = self.seeded()
        guest.write(INSPECTION + 4, 0x13572468)
        guest.literal(2, INSPECTION)
        guest.literal(0, 0xfffffffe)
        guest.literal(8, 0x89abcdef)
        guest.emit(0xf1f8, 0xc800, 0x6127)  # r13_r12 = r0 * r8 (u) || r7 = [r2+4]
        state = guest_state(self.directory, guest)
        product = 0xfffffffe * 0x89abcdef
        self.assertEqual(state["registers"][12:14], [product & 0xffffffff, product >> 32])
        self.assertEqual(state["registers"][7], 0x13572468)
        self.assertEqual(state["instructions"], guest.instructions)

    def test_noncanonical_low_nibble_remains_rejected(self):
        guest = Guest()
        guest.literal(0, 3)
        guest.emit(0xe1f8, 0xc801)
        result = run_guest(self.directory, guest)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("unsupported instruction 0xe1f8", result.stderr)
        self.assertIn("after 1 instructions", result.stderr)


if __name__ == "__main__":
    unittest.main()
