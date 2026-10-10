# SPDX-License-Identifier: GPL-2.0-or-later
"""Multiple-register loads and stores over a base register bitmap."""
from pathlib import Path
import tempfile
import unittest

from support import INSPECTION, ROOT, Guest, guest_state


class MultiRegisterTests(unittest.TestCase):
    def setUp(self):
        directory = ROOT / ".cache/tests"
        directory.mkdir(parents=True, exist_ok=True)
        temporary = tempfile.TemporaryDirectory(dir=directory)
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)

    def marker_words(self, guest):
        # Four marker words at INSPECTION (via r6), read back later.
        guest.literal(6, INSPECTION)
        guest.literal(0, 0x11111111)
        guest.store(0, 6)
        guest.literal(0, 0x22222222)
        guest.store(0, 6, 4)
        guest.literal(0, 0x33333333)
        guest.store(0, 6, 8)
        guest.literal(0, 0x44444444)
        guest.store(0, 6, 12)

    def run_marker_readback(self, guest):
        guest.literal(6, INSPECTION)
        guest.load(4, 6)
        guest.load(5, 6, 4)
        guest.load(7, 6, 8)

    def test_load_may_include_the_base_register(self):
        # Vendor EB02 0006: {r2, r1} = [r2+]. The ascending order reads
        # every word from the incoming base before later loads overwrite
        # it, and the single plus leaves no writeback.
        guest = Guest()
        self.marker_words(guest)
        guest.literal(2, INSPECTION)
        guest.emit(0xeb02, 0x0006)
        state = guest_state(self.directory, guest)
        self.assertEqual(state["registers"][1], 0x11111111)
        self.assertEqual(state["registers"][2], 0x22222222)

    def test_load_post_increment_charges_the_count(self):
        guest = Guest()
        self.marker_words(guest)
        guest.literal(0, INSPECTION)
        guest.emit(0xeb10, 0x0110)
        state = guest_state(self.directory, guest)
        self.assertEqual(state["registers"][4], 0x11111111)
        self.assertEqual(state["registers"][8], 0x22222222)
        self.assertEqual(state["registers"][0], INSPECTION + 8)

    def test_store_post_increment_charges_the_count(self):
        guest = Guest()
        guest.literal(1, INSPECTION)
        guest.literal(2, 0xaabbccdd)
        guest.literal(3, 0x55667788)
        guest.emit(0xeb31, 0x000c)
        self.run_marker_readback(guest)
        state = guest_state(self.directory, guest)
        self.assertEqual(state["registers"][4], 0xaabbccdd)
        self.assertEqual(state["registers"][5], 0x55667788)
        self.assertEqual(state["registers"][1], INSPECTION + 8)

    def test_store_pre_decrement_captures_incoming_registers(self):
        # Vendor EB7F FFE8 in Bubba pushes the incoming frame pointer
        # among the set: [r1-8] = r1's incoming value, [r1-4] = r2, and
        # r1 ends one block below its start.
        guest = Guest()
        guest.literal(1, INSPECTION + 16)
        guest.literal(2, 0xaabbccdd)
        guest.emit(0xeb71, 0x0006)
        guest.literal(6, INSPECTION + 8)
        guest.load(4, 6)
        guest.load(5, 6, 4)
        state = guest_state(self.directory, guest)
        self.assertEqual(state["registers"][4], INSPECTION + 16)
        self.assertEqual(state["registers"][5], 0xaabbccdd)
        self.assertEqual(state["registers"][1], INSPECTION + 8)

    def test_load_pre_decrement_reads_the_block_below(self):
        # The block sits below the incoming base: [r1-8] and [r1-4], and
        # the base ends at the block's low end.
        guest = Guest()
        self.marker_words(guest)
        guest.literal(1, INSPECTION + 8)
        guest.emit(0xeb51, 0x000c)
        state = guest_state(self.directory, guest)
        self.assertEqual(state["registers"][2], 0x11111111)
        self.assertEqual(state["registers"][3], 0x22222222)
        self.assertEqual(state["registers"][1], INSPECTION)

    def test_store_below_base_without_writeback(self):
        guest = Guest()
        guest.literal(1, INSPECTION + 8)
        guest.literal(2, 0xaabbccdd)
        guest.literal(3, 0x55667788)
        guest.emit(0xeb61, 0x000c)
        guest.literal(6, INSPECTION)
        guest.load(4, 6)
        guest.load(5, 6, 4)
        state = guest_state(self.directory, guest)
        self.assertEqual(state["registers"][4], 0xaabbccdd)
        self.assertEqual(state["registers"][5], 0x55667788)
        self.assertEqual(state["registers"][1], INSPECTION + 8)

    def test_load_below_base_without_writeback(self):
        # Same block below the base, with the base left intact.
        guest = Guest()
        self.marker_words(guest)
        guest.literal(1, INSPECTION + 8)
        guest.emit(0xeb41, 0x000c)
        state = guest_state(self.directory, guest)
        self.assertEqual(state["registers"][2], 0x11111111)
        self.assertEqual(state["registers"][3], 0x22222222)
        self.assertEqual(state["registers"][1], INSPECTION + 8)


if __name__ == "__main__":
    unittest.main()
