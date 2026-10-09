# SPDX-License-Identifier: GPL-2.0-or-later
"""Reached signed IF against a packed literal (EEA0)."""
from pathlib import Path
import tempfile
import unittest

from support import ROOT, Guest, guest_state


class SignedPackedLeIfTests(unittest.TestCase):
    def setUp(self):
        directory = ROOT / ".cache/tests"
        directory.mkdir(parents=True, exist_ok=True)
        temporary = tempfile.TemporaryDirectory(dir=directory)
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)

    def check(self, value, operand, taken):
        guest = Guest()
        guest.literal(7, 0)
        guest.literal(6, value)
        guest.emit(0xeea6, operand)   # ifs (r6 <= packed) { one THEN instruction }
        guest.literal(7, 1)
        guest.literal(5, 2)
        state = guest_state(self.directory, guest)
        self.assertEqual(state["registers"][7], 1 if taken else 0)
        self.assertEqual(state["registers"][5], 2)
        self.assertEqual(state["instructions"], guest.instructions - (0 if taken else 1))

    def test_vendor_packed_literals_compare_signed(self):
        # Vendor EEA6/0B80 at Felucca 0x0202f6e2: ifs (r6 <= 65536).
        self.check(65536, 0x0b80, True)
        self.check(65537, 0x0b80, False)
        self.check(0xffffffff, 0x0b80, True)    # -1 is below a positive bound
        self.check(0x80000000, 0x0b80, True)
        # Vendor EEAF/2FBE encodes 380 with a zero arm-count field here.
        self.check(380, 0x0fbe, True)
        self.check(381, 0x0fbe, False)


if __name__ == "__main__":
    unittest.main()
