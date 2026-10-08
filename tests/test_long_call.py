# SPDX-License-Identifier: GPL-2.0-or-later
"""Synthetic six-byte PC-relative CALL and return regressions."""

from pathlib import Path
import tempfile
import unittest

from support import QEMU, ROOT, Guest, guest_state


def long_call(guest, target):
    # FF80 carries a signed byte displacement from its six-byte boundary.
    delta = (target - (guest.pc + 6)) & 0xffffffff
    guest.emit(0xff80, delta & 0xffff, delta >> 16)


class LongCallTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not QEMU.is_file():
            raise RuntimeError("Build ./emulator with mise run build before running the tests")
        cls.cache = ROOT / ".cache" / "tests"
        cls.cache.mkdir(parents=True, exist_ok=True)

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(dir=self.cache)
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)

    def assert_finished(self, guest, count):
        state = guest_state(self.directory, guest)
        self.assertEqual(state["pc"], guest.pc)
        self.assertEqual(state["instructions"], count)
        self.assertEqual(state["irq_entries"], 0)
        return state

    def test_positive_displacement_and_return(self):
        guest = Guest()
        guest.literal(0, 0)
        return_pc = guest.pc + 6
        long_call(guest, guest.pc + 14)
        guest.literal(1, 0x12345678)
        # Skip the callee after it has returned; r0 remains zero.
        guest.branch_zero(0, guest.pc + 10)
        guest.literal(2, 0xabcdef01)
        guest.emit(0x0080)  # RTS uses the sequential CALL boundary.
        state = self.assert_finished(guest, 6)
        self.assertEqual(state["registers"][1:3], [0x12345678, 0xabcdef01])
        self.assertEqual(state["specials"][3], return_pc)

    def test_negative_displacement_and_return(self):
        guest = Guest()
        guest.literal(0, 1)
        guest.branch_zero(0, guest.pc + 10, nonzero=True)
        callee = guest.pc
        guest.literal(2, 0xabcdef01)
        guest.emit(0x0080)
        return_pc = guest.pc + 6
        long_call(guest, callee)
        guest.literal(1, 0x12345678)
        state = self.assert_finished(guest, 6)
        self.assertEqual(state["registers"][1:3], [0x12345678, 0xabcdef01])
        self.assertEqual(state["specials"][3], return_pc)

    def test_skipped_call_preserves_rets(self):
        guest = Guest()
        guest.literal(0, 1)
        guest.literal(1, 0x02000300)
        guest.emit(0xe064, 0x1380)  # RETS = r1.
        guest.emit(0xea20, 0x0001)  # IF r0 == 0, one THEN / no ELSE.
        long_call(guest, guest.pc)  # Would loop if the predicate selected it.
        guest.literal(2, 0xabcdef01)
        state = self.assert_finished(guest, 5)
        self.assertEqual(state["registers"][2], 0xabcdef01)
        self.assertEqual(state["specials"][3], 0x02000300)


if __name__ == "__main__":
    unittest.main()
