# SPDX-License-Identifier: GPL-2.0-or-later
"""Reached EE53 negative-displacement byte stores, without firmware data."""
from pathlib import Path
import tempfile
import unittest

from support import INSPECTION, QEMU, ROOT, Guest, guest_state, run_guest

FLAGS = 0x89abcde5


def store_negative(guest, base, source, offset):
    assert -256 <= offset <= -1
    immediate = offset + 256
    guest.emit(0xee53, (source << 12) | (immediate & 0xf0) << 4 |
               (base << 4) | (immediate & 15))


class ByteStoreTests(unittest.TestCase):
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

    def test_negative_offsets_preserve_neighbors_registers_and_flags(self):
        for offset in (-256, -255, -128, -16, -1):
            with self.subTest(offset=offset):
                guest = Guest()
                guest.write(INSPECTION + 12, 0xdeadbeef)
                guest.write(INSPECTION + 16, 0x44332211)
                guest.write(INSPECTION + 20, 0xcafebabe)
                guest.literal(3, FLAGS)
                guest.emit(0xe064, 0x3580)  # PSR = r3.
                guest.literal(0, INSPECTION + 16 - offset)
                guest.literal(8, 0x123456a5)
                store_negative(guest, 0, 8, offset)
                state = guest_state(self.directory, guest)
                self.assertEqual(state["inspection"][3:6],
                                 [0xdeadbeef, 0x443322a5, 0xcafebabe])
                self.assertEqual(state["registers"][0], INSPECTION + 16 - offset)
                self.assertEqual(state["registers"][8], 0x123456a5)
                self.assertEqual(state["specials"][5], FLAGS)
                self.assertEqual(state["instructions"], guest.instructions)

    def test_source_aliases_base_without_writeback(self):
        guest = Guest()
        guest.write(INSPECTION + 4, 0x44332211)
        guest.literal(9, INSPECTION + 8)
        store_negative(guest, 9, 9, -1)
        state = guest_state(self.directory, guest)
        self.assertEqual(state["inspection"][1], 0x08332211)
        self.assertEqual(state["registers"][9], INSPECTION + 8)
        self.assertEqual(state["instructions"], guest.instructions)

    def test_skipped_store_does_not_access_unmapped_memory(self):
        guest = Guest()
        guest.literal(0, 1)
        guest.literal(8, 0)
        guest.emit(0xea20, 0x0001)  # False IF r0 == 0, one THEN.
        store_negative(guest, 8, 0, -1)
        guest.literal(2, 0xabcdef01)
        state = guest_state(self.directory, guest)
        self.assertEqual(state["registers"][2], 0xabcdef01)
        self.assertEqual(state["instructions"], guest.instructions - 1)

    def test_selected_store_faults_at_effective_address(self):
        guest = Guest()
        guest.literal(0, 0)
        guest.literal(8, 0x123456a5)
        fault_pc = guest.pc
        store_negative(guest, 0, 8, -1)
        result = run_guest(self.directory, guest)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("unmapped access at 0xffffffff", result.stderr)
        self.assertIn(f"at PC 0x{fault_pc:08x} after 2 instructions", result.stderr)


if __name__ == "__main__":
    unittest.main()
