# SPDX-License-Identifier: GPL-2.0-or-later
"""Compare-with-immediate branches (F800 family): EQ sign-extends its imm10."""
from pathlib import Path
import tempfile
import unittest

from support import ROOT, Guest, guest_state


class CompareImmediateTests(unittest.TestCase):
    def setUp(self):
        directory = ROOT / ".cache/tests"
        directory.mkdir(parents=True, exist_ok=True)
        temporary = tempfile.TemporaryDirectory(dir=directory)
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)

    def test_eq_with_negative_immediate(self):
        # Stock FM-1 firmware: F874 FC04 is "if (r4 == -2) goto +8"; here the
        # displacement is 3 halfwords, skipping one literal.
        for value, taken in ((0xfffffffe, True), (0x3fe, False)):
            with self.subTest(value=hex(value)):
                guest = Guest()
                guest.literal(4, value)
                guest.literal(5, 0)
                guest.emit(0xf874, 0xfc03)
                guest.literal(5, 1)
                state = guest_state(self.directory, guest)
                self.assertEqual(state["registers"][5], 0 if taken else 1)


if __name__ == "__main__":
    unittest.main()
