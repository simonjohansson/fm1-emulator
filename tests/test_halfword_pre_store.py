# SPDX-License-Identifier: GPL-2.0-or-later
"""Register pre-indexed halfword stores reached by package startup."""
from pathlib import Path
import tempfile
import unittest

from support import INSPECTION, QEMU, ROOT, Guest, guest_state, run_guest


class HalfwordPreStoreTests(unittest.TestCase):
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

    def test_store_updates_base_and_preserves_neighbor_and_flags(self):
        for stride in (2, -2):
            with self.subTest(stride=stride):
                guest = Guest()
                guest.write(INSPECTION, 0xdeadbeef)
                guest.literal(3, 0x89abcde5)
                guest.emit(0xe064, 0x3580)
                guest.literal(3, INSPECTION + 2 - stride)
                guest.literal(11, stride)
                guest.literal(0, 0x12345678)
                guest.emit(0xeddc, 0x0b31)
                state = guest_state(self.directory, guest)
                self.assertEqual(state["inspection"][0], 0x5678beef)
                self.assertEqual(state["registers"][3], INSPECTION + 2)
                self.assertEqual(state["registers"][0], 0x12345678)
                self.assertEqual(state["specials"][5], 0x89abcde5)
                self.assertEqual(state["instructions"], guest.instructions)

    def test_source_index_alias_uses_incoming_value(self):
        guest = Guest()
        guest.write(INSPECTION, 0xdeadbeef)
        guest.literal(3, INSPECTION - 2)
        guest.literal(11, 2)
        guest.emit(0xeddc, 0xbb31)
        state = guest_state(self.directory, guest)
        self.assertEqual(state["inspection"][0], 0xdead0002)
        self.assertEqual(state["registers"][3], INSPECTION)

    def test_skipped_store_does_not_touch_invalid_address(self):
        guest = Guest()
        guest.literal(0, 1)
        guest.literal(3, 0)
        guest.literal(11, 0)
        guest.emit(0xea20, 1)
        guest.emit(0xeddc, 0x0b31)
        state = guest_state(self.directory, guest)
        self.assertEqual(state["registers"][3], 0)
        self.assertEqual(state["instructions"], guest.instructions - 1)

    def test_source_base_alias_remains_rejected(self):
        guest = Guest()
        guest.literal(3, INSPECTION)
        guest.literal(11, 2)
        guest.emit(0xeddc, 0x3b31)
        result = run_guest(self.directory, guest)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("unsupported instruction 0xeddc", result.stderr)
        self.assertIn("after 2 instructions", result.stderr)

    def test_compact_decrement_stores_at_incoming_base(self):
        guest = Guest()
        guest.write(INSPECTION, 0xdeadbeef)
        guest.literal(6, INSPECTION + 2)
        guest.literal(0, 0x12345678)
        guest.emit(0x06e8)
        state = guest_state(self.directory, guest)
        self.assertEqual(state["inspection"][0], 0x5678beef)
        self.assertEqual(state["registers"][6], INSPECTION)
        self.assertEqual(state["registers"][0], 0x12345678)
        self.assertEqual(state["instructions"], guest.instructions)

    def test_compact_decrement_alias_stores_incoming_pointer(self):
        guest = Guest()
        guest.write(INSPECTION, 0xdeadbeef)
        guest.literal(6, INSPECTION + 2)
        guest.emit(0x06ee)
        state = guest_state(self.directory, guest)
        self.assertEqual(state["inspection"][0], 0x8002beef)
        self.assertEqual(state["registers"][6], INSPECTION)

    def test_signed_halfword_load_offsets_do_not_update_base(self):
        for offset in (-512, -4, 0, 300, 510):
            with self.subTest(offset=offset):
                guest = Guest()
                guest.write(INSPECTION, 0x8000beef)
                guest.literal(9, INSPECTION + 2 - offset)
                guest.emit(0xed54 | ((offset >> 8) & 3),
                           0xb090 | ((offset & 0xf0) << 4) | (offset & 14))
                state = guest_state(self.directory, guest)
                self.assertEqual(state["registers"][11], 0xffff8000)
                self.assertEqual(state["registers"][9], INSPECTION + 2 - offset)
                self.assertEqual(state["inspection"][0], 0x8000beef)
                self.assertEqual(state["instructions"], guest.instructions)


if __name__ == "__main__":
    unittest.main()
