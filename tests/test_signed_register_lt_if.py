# SPDX-License-Identifier: GPL-2.0-or-later
"""Reached ED90 signed register IF selects established instruction arms."""
from pathlib import Path
import tempfile
import unittest

from support import INSPECTION, ROOT, Guest, guest_state, run_guest


def if_lt(guest, left=0, right=8, then_count=1, else_count=0):
    assert 0 <= then_count - 1 < 4 and 0 <= else_count < 4
    guest.emit(0xed90 | left, right << 8 | (then_count - 1) << 14 | else_count << 12)


class SignedRegisterLTIfTests(unittest.TestCase):
    def setUp(self):
        directory = ROOT / ".cache/tests"
        directory.mkdir(parents=True, exist_ok=True)
        temporary = tempfile.TemporaryDirectory(dir=directory)
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)

    def test_signed_order_equality_and_high_register_fields_preserve_flags(self):
        for left_reg, right_reg in ((0, 8), (3, 5), (15, 11)):
            for left, right in ((0, 0), (-1, 0), (0, -1), (-1, -1),
                                (-2147483648, -2147483648),
                                (2147483647, 2147483647),
                                (-2147483648, 2147483647),
                                (2147483647, -2147483648)):
                with self.subTest(registers=(left_reg, right_reg), left=left, right=right):
                    guest = Guest()
                    guest.literal(7, 0x89abcde5)
                    guest.emit(0xe064, 0x7580)
                    guest.literal(left_reg, left)
                    guest.literal(right_reg, right)
                    if_lt(guest, left_reg, right_reg, else_count=1)
                    guest.literal(2, 1)
                    guest.literal(2, 2)
                    guest.literal(6, 0xabcdef01)
                    state = guest_state(self.directory, guest)
                    self.assertEqual(state["registers"][2], 1 if left < right else 2)
                    self.assertEqual(state["registers"][6], 0xabcdef01)
                    self.assertEqual(state["registers"][left_reg], left & 0xffffffff)
                    self.assertEqual(state["registers"][right_reg], right & 0xffffffff)
                    self.assertEqual(state["specials"][5], 0x89abcde5)
                    self.assertEqual(state["pc"], guest.pc)
                    self.assertEqual(state["instructions"], guest.instructions - 1)

    def test_reached_if_selects_scaled_unsigned_halfword_load(self):
        for taken in (False, True):
            with self.subTest(taken=taken):
                guest = Guest()
                guest.write(INSPECTION, 0x8000beef)
                guest.write(INSPECTION + 4, 0xcafebabe)
                guest.literal(0, -1 if taken else 0)
                guest.literal(8, 0)
                guest.literal(9, INSPECTION if taken else 0xdead0000)
                guest.literal(2, 1)
                guest.literal(6, 0x12345678)
                # Stock ED90/0800 at 0x02015492 guards EDD8/6298:
                # ifs (r0 < r8) { r6 = h[r9 + (r2 << 1)] (u) }.
                if_lt(guest)
                guest.emit(0xedd8, 0x6298)
                guest.literal(7, 0xabcdef01)
                state = guest_state(self.directory, guest)
                self.assertEqual(state["registers"][6], 0x8000 if taken else 0x12345678)
                self.assertEqual(state["registers"][9], INSPECTION if taken else 0xdead0000)
                self.assertEqual(state["registers"][2], 1)
                self.assertEqual(state["registers"][7], 0xabcdef01)
                self.assertEqual(state["inspection"][:2], [0x8000beef, 0xcafebabe])
                self.assertEqual(state["instructions"], guest.instructions - int(not taken))

    def test_then_and_else_arm_counts_use_existing_boundaries(self):
        for then_count in range(1, 5):
            for else_count in range(4):
                for taken in (False, True):
                    with self.subTest(then_count=then_count, else_count=else_count, taken=taken):
                        guest = Guest()
                        guest.literal(0, -1 if taken else 1)
                        guest.literal(8, 0)
                        guest.literal(6, 0)
                        guest.literal(7, 0)
                        if_lt(guest, then_count=then_count, else_count=else_count)
                        for _ in range(then_count):
                            guest.add(6, 1)
                        for _ in range(else_count):
                            guest.add(7, 1)
                        guest.literal(5, 0xabcdef01)
                        state = guest_state(self.directory, guest)
                        self.assertEqual(state["registers"][6], then_count if taken else 0)
                        self.assertEqual(state["registers"][7], 0 if taken else else_count)
                        self.assertEqual(state["registers"][5], 0xabcdef01)
                        self.assertEqual(state["instructions"],
                                         guest.instructions - (else_count if taken else then_count))

    def test_selected_unmapped_arm_faults_before_load_retirement(self):
        guest = Guest()
        guest.literal(0, -1)
        guest.literal(8, 0)
        guest.literal(9, 0xdead0000)
        guest.literal(2, 1)
        if_lt(guest)
        fault_pc = guest.pc
        guest.emit(0xedd8, 0x6298)
        result = run_guest(self.directory, guest)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("unmapped access at 0xdead0002", result.stderr)
        self.assertIn(f"at PC 0x{fault_pc:08x} after 5 instructions", result.stderr)

    def test_reserved_operand_low_byte_remains_rejected(self):
        guest = Guest()
        guest.literal(0, -1)
        guest.literal(8, 0)
        fault_pc = guest.pc
        guest.emit(0xed90, 0x0801)
        guest.literal(6, 1)
        result = run_guest(self.directory, guest)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("unsupported instruction 0xed90", result.stderr)
        self.assertIn(f"at PC 0x{fault_pc:08x} after 2 instructions", result.stderr)


if __name__ == "__main__":
    unittest.main()
