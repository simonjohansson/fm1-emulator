# SPDX-License-Identifier: GPL-2.0-or-later
"""Doubleword indexed access: offset, pre-writeback, post-index and sums."""
from pathlib import Path
import tempfile
import unittest

from support import INSPECTION, ROOT, Guest, guest_state


class DoublewordIndexedTests(unittest.TestCase):
    def setUp(self):
        directory = ROOT / ".cache/tests"
        directory.mkdir(parents=True, exist_ok=True)
        temporary = tempfile.TemporaryDirectory(dir=directory)
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)

    def prepare(self):
        guest = Guest()
        guest.literal(6, INSPECTION)
        guest.literal(0, 0x11111111)
        guest.store(0, 6)
        guest.literal(0, 0x22222222)
        guest.store(0, 6, 4)
        guest.literal(0, 0x33333333)
        guest.store(0, 6, 8)
        guest.literal(0, 0x44444444)
        guest.store(0, 6, 12)
        return guest

    def test_register_sum_store_without_writeback(self):
        # Vendor EC58 0233: d[r3+r2] = r1_r0, neither base nor index
        # written back. Bubba executes this form 175 million
        # instructions in.
        guest = self.prepare()
        guest.literal(3, INSPECTION)
        guest.literal(2, 8)
        guest.literal(0, 0xaabbccdd)
        guest.literal(1, 0x55667788)
        guest.emit(0xec58, 0x0233)
        guest.load(4, 6, 8)
        guest.load(5, 6, 12)
        state = guest_state(self.directory, guest)
        self.assertEqual(state["registers"][4], 0xaabbccdd)
        self.assertEqual(state["registers"][5], 0x55667788)
        self.assertEqual(state["registers"][3], INSPECTION)
        self.assertEqual(state["registers"][2], 8)

    def test_register_sum_store_scales_the_index_by_eight(self):
        # Vendor EC58 3B02: d[r3+r2<<3] = r1_r0.
        guest = self.prepare()
        guest.literal(3, INSPECTION)
        guest.literal(2, 1)
        guest.literal(0, 0xaabbccdd)
        guest.literal(1, 0x55667788)
        guest.emit(0xec58, 0x023b)
        guest.load(4, 6, 8)
        guest.load(5, 6, 12)
        state = guest_state(self.directory, guest)
        self.assertEqual(state["registers"][4], 0xaabbccdd)
        self.assertEqual(state["registers"][5], 0x55667788)

    def test_register_sum_load_without_writeback(self):
        # Vendor EC58 3202 reads the pair at the unscaled incoming sum.
        guest = self.prepare()
        guest.literal(3, INSPECTION)
        guest.literal(2, 8)
        guest.emit(0xec58, 0x0232)
        state = guest_state(self.directory, guest)
        self.assertEqual(state["registers"][0], 0x33333333)
        self.assertEqual(state["registers"][1], 0x44444444)
        self.assertEqual(state["registers"][3], INSPECTION)
        self.assertEqual(state["registers"][2], 8)

    def test_offset_store_with_pre_index_writeback(self):
        # Vendor EC50 0723: d[++r0=52] = r3_r2. Melodee executes this
        # with the writeback before the store.
        guest = self.prepare()
        guest.literal(0, INSPECTION)
        guest.literal(2, 0xaabbccdd)
        guest.literal(3, 0x55667788)
        guest.emit(0xec50, 0x2307)
        guest.load(4, 6, 52)
        guest.load(5, 6, 56)
        state = guest_state(self.directory, guest)
        self.assertEqual(state["registers"][4], 0xaabbccdd)
        self.assertEqual(state["registers"][5], 0x55667788)
        self.assertEqual(state["registers"][0], INSPECTION + 52)

    def test_offset_store_without_writeback(self):
        # Vendor EC50 0121: d[r0+16] = r3_r2 leaves r0 intact.
        guest = self.prepare()
        guest.literal(0, INSPECTION)
        guest.literal(2, 0xaabbccdd)
        guest.literal(3, 0x55667788)
        guest.emit(0xec50, 0x2101)
        guest.load(4, 6, 16)
        guest.load(5, 6, 20)
        state = guest_state(self.directory, guest)
        self.assertEqual(state["registers"][4], 0xaabbccdd)
        self.assertEqual(state["registers"][5], 0x55667788)
        self.assertEqual(state["registers"][0], INSPECTION)

    def test_post_index_load(self):
        # EC58 0224: r1_r0 = d[r2++=36]. Measured on an FM-1: the access
        # uses the incoming base and the stride charges it afterwards.
        guest = self.prepare()
        guest.literal(2, INSPECTION)
        guest.emit(0xec58, 0x0224)
        state = guest_state(self.directory, guest)
        self.assertEqual(state["registers"][0], 0x11111111)
        self.assertEqual(state["registers"][1], 0x22222222)
        self.assertEqual(state["registers"][2], INSPECTION + 36)

    def test_post_index_load_with_zero_stride(self):
        # Vendor EC58 00F0: r1_r0 = d[r15++=0].
        guest = self.prepare()
        guest.literal(15, INSPECTION)
        guest.emit(0xec58, 0x00f0)
        state = guest_state(self.directory, guest)
        self.assertEqual(state["registers"][0], 0x11111111)
        self.assertEqual(state["registers"][1], 0x22222222)
        self.assertEqual(state["registers"][15], INSPECTION)


if __name__ == "__main__":
    unittest.main()
