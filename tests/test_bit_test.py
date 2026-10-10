# SPDX-License-Identifier: GPL-2.0-or-later
"""Bit-test ALU (e194) runs alone, in parallel pairs, and guards its index."""
from pathlib import Path
import tempfile
import unittest

from support import INSPECTION, ROOT, Guest, guest_state, run_guest

SUBOPS = (
    (0, lambda value, bit: value | bit),
    (1, lambda value, bit: value ^ bit),
    (2, lambda value, bit: value & bit),
    (3, lambda value, bit: value & ~bit & 0xffffffff),
)


class BitTestTests(unittest.TestCase):
    def setUp(self):
        directory = ROOT / ".cache/tests"
        directory.mkdir(parents=True, exist_ok=True)
        temporary = tempfile.TemporaryDirectory(dir=directory)
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)

    def extension(self, subop, dest=2, left=2, right=3):
        return dest << 12 | right << 8 | left << 4 | subop

    def test_serial_bit_test_alu(self):
        for subop, operation in SUBOPS:
            with self.subTest(subop=subop):
                guest = Guest()
                guest.literal(2, 0xf0f0f0f0)
                guest.literal(3, 5)
                guest.emit(0xe194, self.extension(subop))
                state = guest_state(self.directory, guest)
                self.assertEqual(state["registers"][2],
                                 operation(0xf0f0f0f0, 1 << 5))
                self.assertEqual(state["instructions"], 3)

    def test_parallel_bit_test_pair_executes_both_halves(self):
        for subop, operation in SUBOPS:
            with self.subTest(subop=subop):
                guest = Guest()
                guest.literal(0, INSPECTION)
                guest.literal(2, 0xf0f0f0f0)
                guest.literal(3, 5)
                # r2 = r2 <op> (1 << r3), in parallel with [r0] = r3.
                guest.emit(0xf194, self.extension(subop), 0x6083)
                guest.load(4, 0)
                state = guest_state(self.directory, guest)
                self.assertEqual(state["registers"][2],
                                 operation(0xf0f0f0f0, 1 << 5))
                self.assertEqual(state["registers"][4], 5)
                self.assertEqual(state["instructions"], 5)

    def test_parallel_bit_test_with_offset_tail(self):
        # The bubba firmware pairs the bit-test with a store that has an
        # offset and a different base register.
        guest = Guest()
        guest.literal(0, INSPECTION)
        guest.literal(2, 0xf0f0f0f0)
        guest.literal(3, 5)
        guest.emit(0xf194, self.extension(subop=1), 0x6080 | 3 | (0 << 4) | (1 << 8))
        guest.load(4, 0, 4)
        state = guest_state(self.directory, guest)
        self.assertEqual(state["registers"][2], 0xf0f0f0f0 ^ (1 << 5))
        self.assertEqual(state["registers"][4], 5)
        self.assertEqual(state["instructions"], 5)

    def test_bit_index_of_32_faults_before_retirement(self):
        for parallel in (False, True):
            with self.subTest(parallel=parallel):
                guest = Guest()
                guest.literal(2, 0xf0f0f0f0)
                guest.literal(3, 32)
                fault_pc = guest.pc
                if parallel:
                    # The tail store would observe r3; the pair must not run.
                    guest.emit(0xf194, self.extension(subop=1), 0x6083)
                else:
                    guest.emit(0xe194, self.extension(subop=1))
                result = run_guest(self.directory, guest)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(f"at PC 0x{fault_pc:08x} after 2 instructions",
                              result.stderr)
                self.assertIn("unsupported instruction 0xe194", result.stderr)
