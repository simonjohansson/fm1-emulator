# SPDX-License-Identifier: GPL-2.0-or-later
"""Cache idle-status writeback, without accepting unknown cache commands."""

from pathlib import Path
import tempfile
import unittest

from support import ROOT, Guest, guest_state, run_guest

CACHE_CON = 0x01eee008


class CacheTests(unittest.TestCase):
    def setUp(self):
        cache = ROOT / ".cache" / "tests"
        cache.mkdir(parents=True, exist_ok=True)
        temporary = tempfile.TemporaryDirectory(dir=cache)
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)

    def test_idle_status_survives_zero_and_read_modify_write(self):
        guest = Guest()
        guest.write(CACHE_CON, 0)
        guest.literal(0, CACHE_CON)
        guest.load(3, 0)
        guest.emit(0xefc0, 0x0f40)  # [r0] &= ~0x300, preserving idle bit.
        guest.load(4, 0)
        state = guest_state(self.directory, guest)
        self.assertEqual(state["registers"][3:5], [0x4000, 0x4000])
        self.assertEqual(state["instructions"], guest.instructions)

    def test_unknown_cache_commands_remain_faults(self):
        for value in (1, 0x4100, 0x80000000):
            with self.subTest(value=value):
                guest = Guest()
                fault_pc = guest.write(CACHE_CON, value)
                result = run_guest(self.directory, guest)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("cache control writes are unsupported", result.stderr)
                self.assertIn(f"at PC 0x{fault_pc:08x}", result.stderr)


if __name__ == "__main__":
    unittest.main()
