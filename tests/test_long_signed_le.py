# SPDX-License-Identifier: GPL-2.0-or-later
"""Reached FF4D signed register comparison with signed branch distance."""
from pathlib import Path
import tempfile
import unittest

from support import ROOT, Guest, guest_state, run_guest


def branch_le(guest, target, left=3, right=5):
    delta = target - (guest.pc + 6)
    assert delta % 2 == 0 and -65536 <= delta <= 65534
    # Vendor objdump: stock FF4D/0100/FD6C at 0x0200ac02 is
    # ifs (r0 <= r1) goto -1320; left is bits 12:15, right bits 8:11.
    guest.emit(0xff4d, (left << 12) | (right << 8), (delta // 2) & 0xffff)


class LongSignedLETests(unittest.TestCase):
    def setUp(self):
        directory = ROOT / ".cache/tests"
        directory.mkdir(parents=True, exist_ok=True)
        temporary = tempfile.TemporaryDirectory(dir=directory)
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)

    def test_signed_order_equality_and_register_fields(self):
        for left_reg, right_reg in ((3, 5), (0, 1), (15, 8)):
            for left, right in ((0, 0), (-1, 0), (0, -1), (-1, -1),
                                (-2147483648, -2147483648),
                                (2147483647, 2147483647),
                                (-2147483648, 2147483647),
                                (2147483647, -2147483648)):
                with self.subTest(registers=(left_reg, right_reg), left=left, right=right):
                    guest = Guest()
                    guest.literal(left_reg, left)
                    guest.literal(right_reg, right)
                    guest.literal(6, 0)
                    branch_le(guest, guest.pc + 12, left_reg, right_reg)
                    guest.literal(6, 0xabcdef01)
                    state = guest_state(self.directory, guest)
                    self.assertEqual(state["registers"][6],
                                     0 if left <= right else 0xabcdef01)
                    self.assertEqual(state["pc"], guest.pc)
                    self.assertEqual(state["instructions"], 4 if left <= right else 5)

    def test_negative_displacement_includes_equality_in_loop(self):
        guest = Guest()
        guest.literal(3, -3)
        guest.literal(5, 0)
        guest.literal(6, 0)
        loop = guest.pc
        guest.add(6, 1)
        guest.add(3, 1)
        branch_le(guest, loop)
        state = guest_state(self.directory, guest)
        self.assertEqual(state["registers"][3], 1)
        self.assertEqual(state["registers"][6], 4)
        self.assertEqual(state["instructions"], 15)

    def test_skipped_long_branch_uses_six_byte_boundary(self):
        guest = Guest()
        guest.literal(0, 1)
        guest.emit(0xea20, 1)
        branch_le(guest, guest.pc)
        guest.literal(6, 0xabcdef01)
        state = guest_state(self.directory, guest)
        self.assertEqual(state["registers"][6], 0xabcdef01)
        self.assertEqual(state["instructions"], 3)

    def test_reserved_operand_low_byte_remains_rejected(self):
        guest = Guest()
        guest.literal(0, 0)
        guest.literal(1, 1)
        fault_pc = guest.pc
        guest.emit(0xff4d, 0x0101, 0)
        result = run_guest(self.directory, guest)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("unsupported instruction 0xff4d", result.stderr)
        self.assertIn(f"at PC 0x{fault_pc:08x} after 2 instructions", result.stderr)


if __name__ == "__main__":
    unittest.main()
