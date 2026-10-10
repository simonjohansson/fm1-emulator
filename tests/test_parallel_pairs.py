# SPDX-License-Identifier: GPL-2.0-or-later
"""Parallel pairs: special-register heads, umax heads and overlap order."""
from pathlib import Path
import tempfile
import unittest

from support import INSPECTION, ROOT, Guest, guest_state


class ParallelPairTests(unittest.TestCase):
    def setUp(self):
        directory = ROOT / ".cache/tests"
        directory.mkdir(parents=True, exist_ok=True)
        temporary = tempfile.TemporaryDirectory(dir=directory)
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)

    def test_special_register_head_writes_gpr(self):
        # Vendor F064 3E00 pairs r3 = sp with a plain store. The head
        # reads a special register into a GPR while the tail stores the
        # incoming r2.
        guest = Guest()
        guest.literal(2, 0x1234)
        guest.literal(1, INSPECTION)
        guest.emit(0xf064, 0x3e00, 0x6092)
        guest.load(4, 1)
        state = guest_state(self.directory, guest)
        self.assertEqual(state["registers"][2], 0x1234)
        self.assertEqual(state["registers"][3], state["specials"][14])
        self.assertEqual(state["registers"][4], 0x1234)
        self.assertEqual(state["instructions"], 4)

    def test_overlapping_writes_resolve_with_the_head(self):
        # Vendor D603 / 3BDB: r3 = r0, in parallel with r3 += 123. The tail
        # runs first on the incoming registers and the head's write lands
        # last, so the head's value remains.
        guest = Guest()
        guest.literal(0, 0x1234)
        guest.literal(3, 0x555)
        guest.emit(0xd603, 0x3bdb)
        state = guest_state(self.directory, guest)
        self.assertEqual(state["registers"][3], 0x1234)
        self.assertEqual(state["instructions"], 3)

    def test_umax_head_runs_against_a_store_tail(self):
        # Vendor F434 with the extension's low bits clear is umax; stock
        # Bubba pairs it with plain stores. r0 = umax(r2, r0) while
        # [r1] = r2 stores the incoming register.
        guest = Guest()
        guest.literal(1, INSPECTION)
        guest.literal(2, 5)
        guest.literal(0, 3)
        guest.emit(0xf434, 0x0020, 0x6092)
        guest.load(4, 1)
        state = guest_state(self.directory, guest)
        self.assertEqual(state["registers"][0], 5)
        self.assertEqual(state["registers"][4], 5)
        self.assertEqual(state["instructions"], 5)


if __name__ == "__main__":
    unittest.main()
