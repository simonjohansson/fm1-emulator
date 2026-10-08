# SPDX-License-Identifier: GPL-2.0-or-later
"""Word XOR read/modify/write preserves operands, neighbors and flags."""
from pathlib import Path
import tempfile
import unittest

from support import INSPECTION, ROOT, Guest, guest_state, run_guest


class WordXORTests(unittest.TestCase):
    def setUp(self):
        directory = ROOT / ".cache/tests"
        directory.mkdir(parents=True, exist_ok=True)
        temporary = tempfile.TemporaryDirectory(dir=directory)
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)

    def test_offsets_and_operand_alias_preserve_flags_and_neighbors(self):
        for offset, alias in ((0, False), (4, False), (252, False), (4, True)):
            with self.subTest(offset=offset, alias=alias):
                guest = Guest()
                guest.write(INSPECTION + offset, 0x8000a55a)
                guest.write(INSPECTION + offset + 4, 0x12345678)
                guest.literal(14, INSPECTION)
                guest.literal(4, 0xf00f00f0)
                guest.literal(6, 0xa5a5000f)
                guest.emit(0xe064, 0x6580)  # PSR = r6.
                source = 14 if alias else 4
                guest.emit(0xe864, 0xe000 | source << 8 | offset | 1)
                guest.literal(2, INSPECTION + offset)
                guest.load(3, 2)
                guest.load(5, 2, 4)
                state = guest_state(self.directory, guest)
                self.assertEqual(state["registers"][3],
                                 0x8000a55a ^ (INSPECTION if alias else 0xf00f00f0))
                self.assertEqual(state["registers"][5], 0x12345678)
                self.assertEqual(state["registers"][4], 0xf00f00f0)
                self.assertEqual(state["registers"][14], INSPECTION)
                self.assertEqual(state["specials"][5], 0xa5a5000f)
                self.assertEqual(state["instructions"], guest.instructions)

    def test_skipped_xor_does_not_access_unmapped_memory(self):
        guest = Guest()
        guest.literal(0, 1)
        guest.literal(14, 0xdead0000)
        guest.emit(0xea20, 1)
        guest.emit(0xe864, 0xe401)
        state = guest_state(self.directory, guest)
        self.assertEqual(state["instructions"], 3)
        self.assertEqual(state["registers"][14], 0xdead0000)

    def test_selected_xor_faults_before_retirement(self):
        guest = Guest()
        guest.literal(14, 0xdead0000)
        fault_pc = guest.pc
        guest.emit(0xe864, 0xe405)
        result = run_guest(self.directory, guest)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(f"at PC 0x{fault_pc:08x} after 1 instructions", result.stderr)
        self.assertNotIn("unsupported instruction", result.stderr)


if __name__ == "__main__":
    unittest.main()
