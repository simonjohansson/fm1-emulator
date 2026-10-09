# SPDX-License-Identifier: GPL-2.0-or-later
"""Unscaled indexed halfword stores use incoming registers without writeback."""
from pathlib import Path
import tempfile
import unittest

from support import INSPECTION, ROOT, Guest, guest_state, run_guest


def indexed_store(guest, source, base, index, kind=1):
    guest.emit(0xedd8, source << 12 | index << 8 | base << 4 | kind)


class HalfwordIndexStoreTests(unittest.TestCase):
    def setUp(self):
        directory = ROOT / ".cache/tests"
        directory.mkdir(parents=True, exist_ok=True)
        temporary = tempfile.TemporaryDirectory(dir=directory)
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)

    def test_unscaled_store_preserves_neighbors_registers_and_flags(self):
        # First two operand sets are the stock EDD8/5431 and EDD8/3201.
        for source, base, index in ((5, 3, 4), (3, 0, 2), (13, 9, 11)):
            for stride in (0, 2, -2):
                with self.subTest(source=source, base=base, index=index, stride=stride):
                    guest = Guest()
                    guest.write(INSPECTION, 0xdeadbeef)
                    guest.write(INSPECTION + 4, 0xcafebabe)
                    guest.literal(6, 0x89abcde5)
                    guest.emit(0xe064, 0x6580)  # PSR = r6.
                    guest.literal(base, INSPECTION + 2 - stride)
                    guest.literal(index, stride)
                    guest.literal(source, 0x12345678)
                    indexed_store(guest, source, base, index)
                    state = guest_state(self.directory, guest)
                    self.assertEqual(state["inspection"][:2], [0x5678beef, 0xcafebabe])
                    self.assertEqual(state["registers"][base], INSPECTION + 2 - stride)
                    self.assertEqual(state["registers"][index], stride & 0xffffffff)
                    self.assertEqual(state["registers"][source], 0x12345678)
                    self.assertEqual(state["specials"][5], 0x89abcde5)
                    self.assertEqual(state["instructions"], guest.instructions)

    def test_aliases_store_incoming_values_without_writeback(self):
        cases = (("source/base", 3, 3, 11,
                  {3: INSPECTION, 11: 2}, 0x8000),
                 ("source/index", 11, 3, 11,
                  {3: INSPECTION, 11: 2}, 2),
                 ("base/index", 5, 3, 3,
                  {3: INSPECTION // 2 + 1, 5: 0x12345678}, 0x5678),
                 ("all operands", 3, 3, 3,
                  {3: INSPECTION // 2 + 1}, 0x4001))
        for name, source, base, index, registers, half in cases:
            with self.subTest(alias=name):
                guest = Guest()
                guest.write(INSPECTION, 0xdeadbeef)
                guest.literal(6, 0x89abcde5)
                guest.emit(0xe064, 0x6580)
                for register, value in registers.items():
                    guest.literal(register, value)
                indexed_store(guest, source, base, index)
                state = guest_state(self.directory, guest)
                self.assertEqual(state["inspection"][0], half << 16 | 0xbeef)
                for register, value in registers.items():
                    self.assertEqual(state["registers"][register], value)
                self.assertEqual(state["specials"][5], 0x89abcde5)
                self.assertEqual(state["instructions"], guest.instructions)

    def test_skipped_four_byte_store_does_not_access_or_write_back(self):
        for address, stride in ((0xdead0000, 2), (INSPECTION, 1)):
            with self.subTest(address=hex(address), stride=stride):
                guest = Guest()
                guest.literal(0, 1)
                guest.literal(9, address)
                guest.literal(11, stride)
                guest.emit(0xea20, 1)  # False IF r0 == 0, one four-byte arm.
                indexed_store(guest, 9, 9, 11)
                guest.write(INSPECTION, 0x12345678)
                state = guest_state(self.directory, guest)
                self.assertEqual(state["registers"][9], address)
                self.assertEqual(state["registers"][11], stride)
                self.assertEqual(state["inspection"][0], 0x12345678)
                self.assertEqual(state["instructions"], guest.instructions - 1)

    def test_selected_faults_are_precise_and_do_not_retire(self):
        for address, stride, reason in (
                (0xdead0000, 2, "unmapped access at 0xdead0002"),
                (INSPECTION, 1, "unaligned access")):
            with self.subTest(reason=reason):
                guest = Guest()
                guest.literal(3, address)
                guest.literal(11, stride)
                guest.literal(5, 0x12345678)
                fault_pc = guest.pc
                indexed_store(guest, 5, 3, 11)
                result = run_guest(self.directory, guest)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(reason, result.stderr)
                self.assertIn(f"at PC 0x{fault_pc:08x} after 3 instructions", result.stderr)

    def test_neighboring_reserved_kind_remains_rejected(self):
        guest = Guest()
        guest.literal(3, INSPECTION)
        guest.literal(11, 2)
        guest.literal(5, 0x12345678)
        fault_pc = guest.pc
        indexed_store(guest, 5, 3, 11, kind=3)
        result = run_guest(self.directory, guest)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("unsupported instruction 0xedd8", result.stderr)
        self.assertIn(f"at PC 0x{fault_pc:08x} after 3 instructions", result.stderr)


if __name__ == "__main__":
    unittest.main()
