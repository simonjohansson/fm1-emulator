# SPDX-License-Identifier: GPL-2.0-or-later
"""Reached EF81 packed-mask word AND, including memory failure boundaries."""

from pathlib import Path
import tempfile
import unittest

from support import INSPECTION, ROOT, Guest, guest_state, run_guest


class WordAndTests(unittest.TestCase):
    def setUp(self):
        cache = ROOT / ".cache" / "tests"
        cache.mkdir(parents=True, exist_ok=True)
        temporary = tempfile.TemporaryDirectory(dir=cache)
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)

    def test_stock_mask_preserves_only_top_byte(self):
        guest = Guest()
        guest.write(INSPECTION + 4, 0x12345678)
        guest.literal(3, INSPECTION)
        guest.emit(0xef81, 0x347f)  # [r3+4] &= 0xff000000.
        state = guest_state(self.directory, guest)
        self.assertEqual(state["inspection"][1], 0x12000000)
        self.assertEqual(state["registers"][3], INSPECTION)
        self.assertEqual(state["instructions"], guest.instructions)

    def test_mask_modes_high_base_and_maximum_displacement(self):
        # Independent finite probes pinned these values for incoming FEDCBA98.
        for mode, expected in enumerate((0, 0xa2000000, 0x00800000, 0x0000a200)):
            with self.subTest(mode=mode):
                guest = Guest()
                guest.write(INSPECTION + 124, 0xfedcba98)
                guest.literal(12, INSPECTION)
                guest.literal(0, 0x89abcde5)
                guest.emit(0xe064, 0x0580)  # PSR = r0.
                guest.emit(0xef9f, 0xc023 | (mode << 10))
                guest.literal(1, INSPECTION + 124)
                guest.load(2, 1)
                state = guest_state(self.directory, guest)
                self.assertEqual(state["registers"][2], expected)
                self.assertEqual(state["registers"][12], INSPECTION)
                self.assertEqual(state["specials"][5], 0x89abcde5)
                self.assertEqual(state["instructions"], guest.instructions)

    def test_zero_mask_still_requires_valid_memory(self):
        guest = Guest()
        guest.literal(3, 0xffffffff)
        fault_pc = guest.pc
        guest.emit(0xef80, 0x3000)
        result = run_guest(self.directory, guest)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(f"at PC 0x{fault_pc:08x} after 1 instructions", result.stderr)


if __name__ == "__main__":
    unittest.main()
