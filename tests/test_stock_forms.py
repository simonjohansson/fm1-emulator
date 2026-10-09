# SPDX-License-Identifier: GPL-2.0-or-later
"""Instruction forms stock FM-1 firmware reaches, one compact check each."""
from pathlib import Path
import tempfile
import unittest

from support import INSPECTION, ROOT, Guest, guest_state


class StockFormTests(unittest.TestCase):
    def setUp(self):
        directory = ROOT / ".cache/tests"
        directory.mkdir(parents=True, exist_ok=True)
        temporary = tempfile.TemporaryDirectory(dir=directory)
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)

    def test_long_signed_register_branches(self):
        # FF4B/FF4C/FF4D: ifs (left < / > / <= right) goto, left in bits
        # 12-15, right in bits 8-11, displacement from the six-byte end.
        conditions = {0xff4b: lambda a, b: a < b, 0xff4c: lambda a, b: a > b,
                      0xff4d: lambda a, b: a <= b}
        for op, condition in conditions.items():
            for left, right in ((-2, 1), (1, -2), (5, 5)):
                with self.subTest(op=hex(op), left=left, right=right):
                    guest = Guest()
                    guest.literal(3, left)
                    guest.literal(5, right)
                    guest.literal(6, 0)
                    guest.emit(op, 3 << 12 | 5 << 8, 3)   # skip one literal
                    guest.literal(6, 1)
                    state = guest_state(self.directory, guest)
                    self.assertEqual(state["registers"][6], 0 if condition(left, right) else 1)

    def test_byte_load_advances_by_r13_or_r15(self):
        # 12x0/13x0: rD = b[rB++=r13 / r15] (u) loads at the old base.
        for op, stride in ((0x1280, 13), (0x1380, 15)):
            with self.subTest(stride=stride):
                guest = Guest()
                guest.write(INSPECTION, 0x44332211)
                guest.literal(4, INSPECTION + 1)
                guest.literal(stride, -1)
                guest.emit(op | 4 << 4)                # r0 = b[r4++=rS] (u)
                state = guest_state(self.directory, guest)
                self.assertEqual(state["registers"][0], 0x22)
                self.assertEqual(state["registers"][4], INSPECTION)

    def test_byte_post_decrement_store_in_a_parallel_bundle(self):
        # Stock D646/079B: r6 = r4 with b[r1++=-1] = r3, at the old address.
        guest = Guest()
        guest.write(INSPECTION, 0x44332211)
        guest.literal(1, INSPECTION + 2)
        guest.literal(3, 0x123456a5)
        guest.literal(4, 12)
        guest.emit(0xd646, 0x079b)
        state = guest_state(self.directory, guest)
        self.assertEqual(state["inspection"][0], 0x44a52211)
        self.assertEqual(state["registers"][1], INSPECTION + 1)
        self.assertEqual(state["registers"][6], 12)

    def test_negative_stride_and_pre_index_family_members(self):
        # Vendor readings: 0569 r1 = [r6++=-4]; 061B r3 = h[r1++=-2] (u);
        # EDD3 3F0D h[r0++=-4] = r3; EED1 3F28 r3 = b[r2++=-8] (u);
        # EE59 4F2F r4 = b[++r2=-1] (u); EC5C 8012 r9_r8 = d[++r1=r0].
        base = INSPECTION + 8
        cases = [
            ((0x0569,), {6: base}, {1: 0x44332211, 6: base - 4}),
            ((0x061b,), {1: base}, {3: 0x2211, 1: base - 2}),
            ((0xeed1, 0x3f28), {2: base}, {3: 0x11, 2: base - 8}),
            ((0xee59, 0x4f2f), {2: base + 1}, {4: 0x11, 2: base}),
            ((0xec5c, 0x8012), {1: INSPECTION, 0: 8}, {8: 0x44332211, 9: 0x88776655, 1: base}),
        ]
        for code, setup, expected in cases:
            with self.subTest(code=[hex(c) for c in code]):
                guest = Guest()
                guest.write(base, 0x44332211)
                guest.write(base + 4, 0x88776655)
                for reg, value in setup.items():
                    guest.literal(reg, value)
                guest.emit(*code)
                state = guest_state(self.directory, guest)
                for reg, value in expected.items():
                    self.assertEqual(state["registers"][reg], value, f"r{reg}")
        guest = Guest()
        guest.write(INSPECTION, 0x11111111)
        guest.literal(0, INSPECTION + 2)
        guest.literal(3, 0x1234abcd)
        guest.emit(0xedd3, 0x3f0d)
        state = guest_state(self.directory, guest)
        self.assertEqual(state["inspection"][0], 0xabcd1111)
        self.assertEqual(state["registers"][0], INSPECTION - 2)

    def test_halfword_store_at_unscaled_register_sum(self):
        # EDD8 kind 1, stock EDD8/5431: h[r3+r4] = r5 without writeback.
        guest = Guest()
        guest.write(INSPECTION, 0x1111beef)
        guest.literal(3, INSPECTION)
        guest.literal(4, 2)
        guest.literal(5, 0x12345678)
        guest.emit(0xedd8, 5 << 12 | 4 << 8 | 3 << 4 | 1)
        state = guest_state(self.directory, guest)
        self.assertEqual(state["inspection"][0], 0x5678beef)
        self.assertEqual(state["registers"][3], INSPECTION)

    def test_signed_register_less_than_if(self):
        # ED90|left with right in bits 8-11: ifs (left < right) { THEN } else { ELSE }.
        for left, right in ((-2, 1), (1, -2), (5, 5)):
            with self.subTest(left=left, right=right):
                guest = Guest()
                guest.literal(3, left)
                guest.literal(5, right)
                guest.emit(0xed90 | 3, 5 << 8 | 1 << 12)   # one THEN, one ELSE
                guest.literal(2, 1)
                guest.literal(2, 2)
                state = guest_state(self.directory, guest)
                self.assertEqual(state["registers"][2], 1 if left < right else 2)


if __name__ == "__main__":
    unittest.main()
