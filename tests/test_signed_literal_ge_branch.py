# SPDX-License-Identifier: GPL-2.0-or-later
"""Reached six-byte signed-literal GE branch (FF0A)."""
from pathlib import Path
import tempfile
import unittest

from support import ROOT, Guest, guest_state


class SignedLiteralGeBranchTests(unittest.TestCase):
    def setUp(self):
        directory = ROOT / ".cache/tests"
        directory.mkdir(parents=True, exist_ok=True)
        temporary = tempfile.TemporaryDirectory(dir=directory)
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)

    def check(self, reg, value, literal, taken):
        guest = Guest()
        guest.literal(6, 0)
        guest.literal(reg, value)
        # ifs (reg >= literal) skip the following six-byte literal.
        guest.emit(0xff0a, reg << 12 | (literal & 0xfff), 3)
        guest.literal(6, 0xbad)
        guest.literal(7, 1)
        state = guest_state(self.directory, guest)
        self.assertEqual(state["registers"][6], 0 if taken else 0xbad)
        self.assertEqual(state["registers"][7], 1)
        self.assertEqual(state["instructions"], guest.instructions - (1 if taken else 0))

    def test_vendor_literals_compare_signed(self):
        # Vendor FF0A/5460 0007 at Felucca 0x020329f2: ifs (r5 >= 1120).
        self.check(5, 1120, 1120, True)
        self.check(5, 1119, 1120, False)
        # Vendor FF0A/8D05: ifs (r8 >= -763).
        self.check(8, -763 & 0xffffffff, -763, True)
        self.check(8, -764 & 0xffffffff, -763, False)
        # Vendor FF0A/FFFF: ifs (r15 >= -1); 0x80000000 is negative.
        self.check(15, 0x7fffffff, -1, True)
        self.check(15, 0x80000000, -1, False)


if __name__ == "__main__":
    unittest.main()
