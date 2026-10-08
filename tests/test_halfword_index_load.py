# SPDX-License-Identifier: GPL-2.0-or-later
"""Unscaled unsigned indexed halfword loads, including incoming aliases."""
from pathlib import Path
import tempfile
import unittest

from support import INSPECTION, ROOT, Guest, guest_state


class HalfwordIndexLoadTests(unittest.TestCase):
    def setUp(self):
        directory = ROOT / ".cache/tests"
        directory.mkdir(parents=True, exist_ok=True)
        temporary = tempfile.TemporaryDirectory(dir=directory)
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)

    def test_unsigned_load_uses_incoming_address_without_writeback(self):
        for dest, base, index in ((0, 0, 1), (11, 9, 11), (3, 9, 11)):
            for stride, half in ((2, 0x8000), (-2, 0xffff), (0, 0x0068)):
                with self.subTest(dest=dest, base=base, index=index, stride=stride, half=half):
                    guest = Guest()
                    guest.write(INSPECTION, (half << 16) | 0xbeef)
                    guest.literal(6, 0x89abcde5)
                    guest.emit(0xe064, 0x6580)
                    guest.literal(base, INSPECTION + 2 - stride)
                    guest.literal(index, stride)
                    guest.emit(0xedd8, dest << 12 | index << 8 | base << 4)
                    state = guest_state(self.directory, guest)
                    self.assertEqual(state["registers"][dest], half)
                    if base != dest:
                        self.assertEqual(state["registers"][base], INSPECTION + 2 - stride)
                    if index != dest:
                        self.assertEqual(state["registers"][index], stride & 0xffffffff)
                    self.assertEqual(state["specials"][5], 0x89abcde5)
                    self.assertEqual(state["inspection"][0], (half << 16) | 0xbeef)
                    self.assertEqual(state["instructions"], guest.instructions)

    def test_skipped_load_does_not_access_or_modify_destination(self):
        guest = Guest()
        guest.literal(0, 1)
        guest.literal(9, 0xdead0000)
        guest.literal(11, 2)
        guest.emit(0xea20, 1)
        guest.emit(0xedd8, 0xbb90)  # destination/index alias
        state = guest_state(self.directory, guest)
        self.assertEqual(state["registers"][9], 0xdead0000)
        self.assertEqual(state["registers"][11], 2)
        self.assertEqual(state["instructions"], guest.instructions - 1)


if __name__ == "__main__":
    unittest.main()
