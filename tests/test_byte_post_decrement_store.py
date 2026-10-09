# SPDX-License-Identifier: GPL-2.0-or-later
"""Byte post-decrement stores, including the reached D646/079B bundle."""
from pathlib import Path
import tempfile
import unittest

from support import INSPECTION, ROOT, Guest, guest_state, run_guest


class BytePostDecrementStoreTests(unittest.TestCase):
    def setUp(self):
        directory = ROOT / ".cache/tests"
        directory.mkdir(parents=True, exist_ok=True)
        temporary = tempfile.TemporaryDirectory(dir=directory)
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)

    def test_store_uses_old_address_and_preserves_neighbors(self):
        guest = Guest()
        guest.write(INSPECTION, 0x44332211)
        guest.literal(1, INSPECTION + 2)
        guest.literal(3, 0x123456a5)
        guest.emit(0x079b)  # Vendor: b[r1++=-1] = r3.
        state = guest_state(self.directory, guest)
        self.assertEqual(state["inspection"][0], 0x44a52211)
        self.assertEqual(state["registers"][1], INSPECTION + 1)
        self.assertEqual(state["registers"][3], 0x123456a5)
        self.assertEqual(state["instructions"], guest.instructions)

    def test_source_aliases_base_and_stores_incoming_low_byte(self):
        guest = Guest()
        guest.write(INSPECTION, 0x44332211)
        guest.literal(0, INSPECTION + 2)
        guest.emit(0x0788)  # Vendor stock 0x02053506: b[r0++=-1] = r0.
        state = guest_state(self.directory, guest)
        self.assertEqual(state["inspection"][0], 0x44022211)
        self.assertEqual(state["registers"][0], INSPECTION + 1)

    def test_parallel_move_and_store_use_incoming_snapshot(self):
        # Stock D646 moves r4 into r6. D643 changes the stored source r3;
        # D616 copies the base r1, which the store simultaneously decrements.
        for head, dest, source in ((0xd646, 6, 4), (0xd643, 3, 4), (0xd616, 6, 1)):
            with self.subTest(head=hex(head)):
                guest = Guest()
                guest.write(INSPECTION, 0x44332211)
                guest.literal(1, INSPECTION + 2)
                guest.literal(3, 0x123456a5)
                guest.literal(4, 0xabcdef01)
                guest.emit(head, 0x079b)
                state = guest_state(self.directory, guest)
                self.assertEqual(state["inspection"][0], 0x44a52211)
                self.assertEqual(state["registers"][1], INSPECTION + 1)
                self.assertEqual(state["registers"][dest],
                                 INSPECTION + 2 if source == 1 else 0xabcdef01)
                self.assertEqual(state["instructions"], guest.instructions)

    def test_skipped_bundle_does_not_access_or_update_registers(self):
        guest = Guest()
        guest.literal(0, 1)
        guest.literal(1, 0xdead0000)
        guest.literal(3, 0xa5)
        guest.literal(4, 12)
        guest.literal(6, 100)
        guest.emit(0xea20, 1)
        guest.emit(0xd646, 0x079b)
        state = guest_state(self.directory, guest)
        self.assertEqual(state["registers"][1], 0xdead0000)
        self.assertEqual(state["registers"][6], 100)
        self.assertEqual(state["instructions"], guest.instructions - 1)

    def test_overlapping_bundle_destination_rejected_before_store(self):
        guest = Guest()
        guest.literal(1, 0xdead0000)
        guest.literal(3, 0xa5)
        guest.literal(4, 12)
        guest.emit(0xd641, 0x079b)  # Both halves would write r1.
        result = run_guest(self.directory, guest)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("unsupported instruction 0xd641", result.stderr)
        self.assertNotIn("unmapped access", result.stderr)
        self.assertIn("after 3 instructions", result.stderr)

    def test_selected_store_faults_at_old_address(self):
        guest = Guest()
        guest.literal(1, 0xdead0000)
        guest.literal(3, 0xa5)
        fault_pc = guest.pc
        guest.emit(0x079b)
        result = run_guest(self.directory, guest)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("unmapped access at 0xdead0000", result.stderr)
        self.assertIn(f"at PC 0x{fault_pc:08x} after 2 instructions", result.stderr)


if __name__ == "__main__":
    unittest.main()
