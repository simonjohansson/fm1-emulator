# SPDX-License-Identifier: GPL-2.0-or-later
"""Reached signed byte load reads the offset address and updates its base."""
from pathlib import Path
import tempfile
import unittest

from support import INSPECTION, ROOT, Guest, guest_state, run_guest


class BytePreLoadTests(unittest.TestCase):
    def setUp(self):
        directory = ROOT / ".cache/tests"
        directory.mkdir(parents=True, exist_ok=True)
        temporary = tempfile.TemporaryDirectory(dir=directory)
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)

    def test_sign_extension_offset_and_writeback_preserve_flags(self):
        for offset, byte in ((0, 0), (1, 127), (225, 128), (255, 255)):
            with self.subTest(offset=offset, byte=byte):
                guest = Guest()
                guest.write(INSPECTION, 0x12345600 | byte)
                guest.literal(2, 0x89abcde5)
                guest.emit(0xe064, 0x2580)
                guest.literal(6, INSPECTION - offset)
                guest.emit(0xee5c, 0x1060 | (offset & 15) | (offset & 0xf0) << 4)
                state = guest_state(self.directory, guest)
                self.assertEqual(state["registers"][1], byte if byte < 128 else byte | 0xffffff00)
                self.assertEqual(state["registers"][6], INSPECTION)
                self.assertEqual(state["inspection"][0], 0x12345600 | byte)
                self.assertEqual(state["specials"][5], 0x89abcde5)
                self.assertEqual(state["instructions"], guest.instructions)

    def test_skipped_load_preserves_base_without_access(self):
        guest = Guest()
        guest.literal(0, 1)
        guest.literal(6, 0xdead0000)
        guest.emit(0xea20, 1)
        guest.emit(0xee5c, 0x1e61)
        state = guest_state(self.directory, guest)
        self.assertEqual(state["registers"][6], 0xdead0000)
        self.assertEqual(state["instructions"], guest.instructions - 1)

    def test_destination_base_alias_remains_unsupported(self):
        guest = Guest()
        guest.literal(6, INSPECTION)
        guest.emit(0xee5c, 0x6e61)
        result = run_guest(self.directory, guest)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("unsupported instruction 0xee5c", result.stderr)
        self.assertIn("after 1 instructions", result.stderr)


if __name__ == "__main__":
    unittest.main()
