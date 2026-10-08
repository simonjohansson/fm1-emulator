# SPDX-License-Identifier: GPL-2.0-or-later
"""Conditional arms ending in RTS complete before the caller resumes."""
from pathlib import Path
import tempfile
import unittest

from support import QEMU, ROOT, Guest, guest_state, run_guest


def conditional_return(selected_else=False, final_then_else=False):
    guest = Guest()
    guest.literal(0, int(selected_else))
    if selected_else:
        guest.literal(3, 0)
    call_index = len(guest.words)
    guest.emit(0xff80, 0, 0)
    guest.emit(0xea20, 0)  # Mask zero: always true, requires no scratch GPR.
    guest.literal(2, 0xabcdef01)
    skip_index = len(guest.words)
    guest.emit(0)  # Patched branch skips the callee after it returned.
    callee = guest.pc
    guest.emit(0xea20, 0x1001 if selected_else or final_then_else else 1)
    if selected_else:
        guest.literal(3, 0xbad)
    guest.emit(0x0080)
    if final_then_else:
        guest.literal(3, 0xbad)
    delta = callee - (guest.base + call_index * 2 + 6)
    guest.words[call_index + 1:call_index + 3] = [delta & 65535, delta >> 16]
    branch = Guest(guest.base + skip_index * 2)
    branch.branch_zero(0, guest.pc, nonzero=selected_else)
    guest.words[skip_index] = branch.words[0]
    return guest


class ConditionalReturnTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not QEMU.is_file():
            raise RuntimeError("Build ./emulator with mise run build before testing")
        cls.cache = ROOT / ".cache/tests"
        cls.cache.mkdir(parents=True, exist_ok=True)

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(dir=self.cache)
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)

    def test_final_then_return_closes_before_caller_condition(self):
        guest = conditional_return()
        state = guest_state(self.directory, guest)
        self.assertEqual(state["registers"][2], 0xabcdef01)
        self.assertEqual(state["pc"], guest.pc)
        self.assertEqual(state["instructions"], 7)

    def test_final_else_return_closes_before_caller_condition(self):
        guest = conditional_return(selected_else=True)
        state = guest_state(self.directory, guest)
        self.assertEqual(state["registers"][2:4], [0xabcdef01, 0])
        self.assertEqual(state["pc"], guest.pc)
        self.assertEqual(state["instructions"], 8)

    def test_final_then_return_with_else_remains_explicitly_unsupported(self):
        guest = conditional_return(final_then_else=True)
        result = run_guest(self.directory, guest)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("final THEN return with ELSE is unsupported", result.stderr)
        self.assertIn("after 3 instructions", result.stderr)

    def test_signed_register_greater_selects_then_or_else(self):
        for left, right, selected in ((1922, 1024, 1), (0, 0, 2),
                                     (0xffffffff, 0, 2), (0, 0xffffffff, 1),
                                     (0x80000000, 0, 2)):
            with self.subTest(left=left, right=right):
                guest = Guest()
                guest.literal(7, left)
                guest.literal(15, right)
                guest.emit(0xee17, 0x1f00)
                guest.literal(2, 1)
                guest.literal(2, 2)
                guest.literal(3, 0xabcdef01)
                state = guest_state(self.directory, guest)
                self.assertEqual(state["registers"][2], selected)
                self.assertEqual(state["registers"][3], 0xabcdef01)
                self.assertEqual(state["pc"], guest.pc)
                self.assertEqual(state["instructions"], 5)


if __name__ == "__main__":
    unittest.main()
