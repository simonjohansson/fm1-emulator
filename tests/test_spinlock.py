# SPDX-License-Identifier: GPL-2.0-or-later
"""SDK spinlock forms: TESTSET, IFEQ, LOCKSET and LOCKCLR, as measured on an FM-1."""
from pathlib import Path
import tempfile
import unittest

from support import INSPECTION, ROOT, Guest, guest_state

FLAGS = 0x89abcdea


class SpinlockTests(unittest.TestCase):
    def setUp(self):
        directory = ROOT / ".cache/tests"
        directory.mkdir(parents=True, exist_ok=True)
        temporary = tempfile.TemporaryDirectory(dir=directory)
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)

    def locked(self, byte):
        guest = Guest()
        guest.write(INSPECTION, 0x12345600 | byte)
        guest.literal(0, FLAGS)
        guest.emit(0xe064, 0x0580)  # PSR = r0.
        guest.literal(2, INSPECTION)
        guest.emit(0x00b2)          # testset b[r2]
        return guest

    def test_testset_writes_ff_and_copies_the_low_nibble_into_flags(self):
        for byte in (0x00, 0x01, 0x05, 0x7e, 0x80, 0xff):
            with self.subTest(byte=byte):
                guest = self.locked(byte)
                state = guest_state(self.directory, guest)
                self.assertEqual(state["inspection"][0], 0x123456ff)
                self.assertEqual(state["specials"][5], (FLAGS & ~15) | (byte & 15))
                self.assertEqual(state["instructions"], guest.instructions)

    def test_ifeq_branches_on_z(self):
        for byte, taken in ((0x00, False), (0x04, True), (0x7f, True), (0x0b, False)):
            with self.subTest(byte=byte):
                guest = self.locked(byte)
                guest.literal(4, 0)
                guest.emit(0xe840, 3)       # ifeq: skip the next literal
                guest.literal(4, 1)
                state = guest_state(self.directory, guest)
                self.assertEqual(state["registers"][4], 0 if taken else 1)
                self.assertEqual(state["instructions"],
                                 guest.instructions - (1 if taken else 0))

    def test_lockset_and_lockclr_leave_state_alone(self):
        guest = Guest()
        guest.literal(0, FLAGS)
        guest.emit(0xe064, 0x0580)
        guest.emit(0x0041)
        guest.emit(0x0040)
        state = guest_state(self.directory, guest)
        self.assertEqual(state["specials"][5], FLAGS)
        self.assertEqual(state["instructions"], guest.instructions)


if __name__ == "__main__":
    unittest.main()
