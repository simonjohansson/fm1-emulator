# SPDX-License-Identifier: GPL-2.0-or-later
"""A conditional block whose selected arm ends in a return: the block is over when the return retires."""

from pathlib import Path
import tempfile
import unittest

from support import QEMU, ROOT, Guest, guest_state

# Vendor-assembled (JieLi clang 4.0.1 -target pi32v2), as Felucca's st_sector does it:
#         goto main
# sub:    if (r0 >= 5) {
#           r0 = 1
#           rts
#         }
#         r0 = 2
#         rts
# main:   call sub
#         if (r1 == r2) {
#           r3 = 7
#         }
#         r4 = 9
PROGRAM = [0x8604, 0xE920, 0x4005, 0x2140, 0x0080, 0x2240, 0x0080, 0x9971, 0xE811, 0x0200, 0x2743, 0x2944]


class BlockExitTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not QEMU.is_file():
            raise RuntimeError("Build ./emulator with mise run build before running the tests")
        cls.cache = ROOT / ".cache" / "tests"
        cls.cache.mkdir(parents=True, exist_ok=True)

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(dir=self.cache)
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)

    def run_program(self, r0, r1, r2):
        guest = Guest()
        guest.literal(0, r0)
        guest.literal(1, r1)
        guest.literal(2, r2)
        guest.literal(3, 0)
        guest.literal(4, 0)
        for word in PROGRAM:                 # (emit counts instructions; the state is all these tests read)
            guest.emit(word)
        return guest_state(self.directory, guest)

    def test_return_from_a_selected_arm_ends_the_block(self):
        # r0 >= 5: the THEN returns from inside the block; the next IF is not nested in it.
        state = self.run_program(9, 4, 4)
        self.assertEqual(state["registers"][0:5], [1, 4, 4, 7, 9])

    def test_unselected_arm_and_the_next_if(self):
        state = self.run_program(2, 4, 5)
        self.assertEqual(state["registers"][0:5], [2, 4, 5, 0, 9])


if __name__ == "__main__":
    unittest.main()
