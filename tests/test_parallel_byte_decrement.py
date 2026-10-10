# SPDX-License-Identifier: GPL-2.0-or-later
"""Parallel byte post-decrement loads use the established incoming snapshot."""
from pathlib import Path
import tempfile
import unittest

from support import INSPECTION, ROOT, Guest, guest_state, run_guest


class ParallelByteDecrementTests(unittest.TestCase):
    def setUp(self):
        directory = ROOT / ".cache/tests"
        directory.mkdir(parents=True, exist_ok=True)
        temporary = tempfile.TemporaryDirectory(dir=directory)
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)

    def test_reached_bundle_and_incoming_loaded_register_as_alu_source(self):
        for head in (0xd823, 0xd813):
            with self.subTest(head=head):
                guest = Guest()
                guest.write(INSPECTION, 0x80abcdef)
                guest.literal(0, INSPECTION + 3)
                guest.literal(1, 12)
                guest.literal(2, 12)
                guest.literal(3, 100)
                guest.emit(head, 0x0709)
                state = guest_state(self.directory, guest)
                self.assertEqual(state["registers"][:4], [INSPECTION + 2, 0x80, 12, 112])
                self.assertEqual(state["inspection"][0], 0x80abcdef)
                self.assertEqual(state["instructions"], guest.instructions)

    def test_skipped_bundle_does_not_access_or_update_registers(self):
        guest = Guest()
        guest.literal(0, 0xdead0000)
        guest.literal(1, 12)
        guest.literal(3, 100)
        guest.emit(0xea21, 4)
        guest.emit(0xd813, 0x0709)
        state = guest_state(self.directory, guest)
        self.assertEqual(state["registers"][:2], [0xdead0000, 12])
        self.assertEqual(state["registers"][3], 100)
        self.assertEqual(state["instructions"], guest.instructions - 1)

    def test_overlapping_tail_runs_on_incoming_registers(self):
        # Head r0 *= r1 overlaps the tail's base writeback (r0). The tail
        # runs first on the incoming registers, so its byte load still
        # reads through the unmapped incoming base before the head's
        # write lands.
        guest = Guest()
        guest.literal(0, 0xdead0000)
        guest.literal(1, 12)
        guest.emit(0xd810, 0x0709)
        result = run_guest(self.directory, guest)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("unmapped access at 0xdead0000", result.stderr)
        self.assertNotIn("unsupported instruction", result.stderr)
        self.assertIn("after 2 instructions", result.stderr)


if __name__ == "__main__":
    unittest.main()
