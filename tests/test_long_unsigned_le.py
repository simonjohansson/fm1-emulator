# SPDX-License-Identifier: GPL-2.0-or-later
"""Reached FF49 unsigned register comparison with signed branch distance."""
from pathlib import Path
import tempfile
import unittest

from support import ROOT, Guest, guest_state, run_guest


def branch_le(guest, target):
    delta = target - (guest.pc + 6)
    assert delta % 2 == 0
    guest.emit(0xff49, 0x3500, (delta // 2) & 0xffff)


class LongUnsignedLETests(unittest.TestCase):
    def setUp(self):
        directory = ROOT / ".cache/tests"
        directory.mkdir(parents=True, exist_ok=True)
        temporary = tempfile.TemporaryDirectory(dir=directory)
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)

    def test_unsigned_order_equality_and_untaken_fallthrough(self):
        for left, right in ((0, 0), (0, 1), (1, 0),
                            (0x80000000, 1), (1, 0x80000000),
                            (0xffffffff, 0xffffffff)):
            with self.subTest(left=left, right=right):
                guest = Guest()
                guest.literal(3, left)
                guest.literal(5, right)
                guest.literal(6, 0)
                branch_le(guest, guest.pc + 12)
                guest.literal(6, 0xabcdef01)
                state = guest_state(self.directory, guest)
                self.assertEqual(state["registers"][6], 0 if left <= right else 0xabcdef01)
                self.assertEqual(state["pc"], guest.pc)
                self.assertEqual(state["instructions"], 4 if left <= right else 5)

    def test_negative_displacement_returns_to_loop(self):
        guest = Guest()
        guest.literal(3, 0)
        guest.literal(5, 3)
        guest.literal(6, 0)
        loop = guest.pc
        guest.add(6, 1)
        guest.add(3, 1)
        branch_le(guest, loop)
        state = guest_state(self.directory, guest)
        self.assertEqual(state["registers"][3], 4)
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

    def test_final_selected_branch_closes_before_destination_condition(self):
        for selected_else in (False, True):
            for taken in (False, True):
                with self.subTest(selected_else=selected_else, taken=taken):
                    guest = Guest()
                    guest.literal(0, int(selected_else))
                    guest.literal(3, 0 if taken else 1)
                    guest.literal(5, 0)
                    guest.emit(0xea20, 0x1001 if selected_else else 1)
                    if selected_else:
                        guest.emit(0)  # Skipped THEN.
                    branch_le(guest, guest.pc + 8)
                    guest.emit(0)  # Untaken fallthrough.
                    guest.emit(0xea20, 0)
                    guest.literal(6, 0xabcdef01)
                    state = guest_state(self.directory, guest)
                    self.assertEqual(state["pc"], guest.pc)
                    self.assertEqual(state["registers"][6], 0xabcdef01)
                    self.assertEqual(state["instructions"], 7 if taken else 8)

    def test_final_then_branch_with_else_remains_explicitly_unsupported(self):
        guest = Guest()
        guest.emit(0xea20, 0x1000)
        branch_le(guest, guest.pc + 8)
        guest.emit(0)
        result = run_guest(self.directory, guest)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("final THEN FF49 branch with ELSE is unsupported", result.stderr)
        self.assertIn("after 1 instructions", result.stderr)


if __name__ == "__main__":
    unittest.main()
