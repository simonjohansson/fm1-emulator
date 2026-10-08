# SPDX-License-Identifier: GPL-2.0-or-later
"""Word stores at the incoming base with an immediate pointer advance."""
from pathlib import Path
import tempfile
import unittest

from support import INSPECTION, QEMU, ROOT, Guest, guest_state, run_guest


def post_store(guest, base, source, stride):
    assert -1024 <= stride <= 1020 and stride % 4 == 0
    guest.emit(0xecd8 | ((stride >> 8) & 7), (source << 12) | ((stride & 0xf0) << 4) |
               (base << 4) | (stride & 12) | 1)


class WordPostStoreTests(unittest.TestCase):
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

    def test_store_uses_old_pointer_and_preserves_flags(self):
        for stride in (-1024, -4, 0, 4, 16, 252, 516, 1020):
            with self.subTest(stride=stride):
                guest = Guest()
                guest.write(INSPECTION, 0xdeadbeef)
                guest.write(INSPECTION + 4, 0xcafebabe)
                guest.literal(3, 0x89abcde5)
                guest.emit(0xe064, 0x3580)  # PSR = r3.
                guest.literal(0, INSPECTION)
                guest.literal(11, 0x12345678)
                post_store(guest, 0, 11, stride)
                state = guest_state(self.directory, guest)
                self.assertEqual(state["inspection"][:2], [0x12345678, 0xcafebabe])
                self.assertEqual(state["registers"][0], INSPECTION + stride)
                self.assertEqual(state["registers"][11], 0x12345678)
                self.assertEqual(state["specials"][5], 0x89abcde5)
                self.assertEqual(state["instructions"], guest.instructions)

    def test_alias_stores_incoming_base(self):
        guest = Guest()
        guest.literal(9, INSPECTION)
        post_store(guest, 9, 9, 4)
        state = guest_state(self.directory, guest)
        self.assertEqual(state["inspection"][0], INSPECTION)
        self.assertEqual(state["registers"][9], INSPECTION + 4)

    def test_large_signed_load_strides_read_incoming_base(self):
        for stride in (-1024, -4, 516, 1020):
            with self.subTest(stride=stride):
                guest = Guest()
                guest.write(INSPECTION, 0x12345678)
                guest.literal(1, INSPECTION)
                guest.emit(0xecd8 | ((stride >> 8) & 7),
                           ((stride & 0xf0) << 4) | 0x10 | (stride & 12))
                state = guest_state(self.directory, guest)
                self.assertEqual(state["registers"][0], 0x12345678)
                self.assertEqual(state["registers"][1], INSPECTION + stride)
                self.assertEqual(state["instructions"], guest.instructions)

    def test_skipped_store_does_not_access_or_advance(self):
        guest = Guest()
        guest.literal(0, 1)
        guest.literal(8, 0)
        guest.emit(0xea20, 0x0001)  # False IF r0 == 0, one THEN.
        post_store(guest, 8, 0, 4)
        state = guest_state(self.directory, guest)
        self.assertEqual(state["registers"][8], 0)
        self.assertEqual(state["instructions"], guest.instructions - 1)

    def test_unmapped_store_faults_before_retirement(self):
        guest = Guest()
        guest.literal(0, 0)
        guest.literal(11, 0x12345678)
        fault_pc = guest.pc
        post_store(guest, 0, 11, 4)
        result = run_guest(self.directory, guest)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("unmapped access at 0x00000000", result.stderr)
        self.assertIn(f"at PC 0x{fault_pc:08x} after 2 instructions", result.stderr)


if __name__ == "__main__":
    unittest.main()
