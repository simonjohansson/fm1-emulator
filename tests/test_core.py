# SPDX-License-Identifier: GPL-2.0-or-later
"""Compact native CLI, CPU and cold USB regressions; no external firmware."""

import json
from pathlib import Path
import subprocess
import tempfile
import unittest

from support import (INSPECTION, QEMU, ROOT, USB, Guest, environment,
                     guest_state, run_guest)

FLAGS = 0x89ABCDE5
SEEDS = [0x81230000 + index * 0x10101 for index in range(16)]


def seeded_guest():
    guest = Guest()
    guest.literal(0, FLAGS)
    guest.emit(0xE064, 0x0580)  # PSR = r0.
    for register, value in enumerate(SEEDS):
        guest.literal(register, value)
    return guest


class EmulatorTests(unittest.TestCase):
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

    def cli(self, *arguments, **kwargs):
        return subprocess.run([str(QEMU), *arguments], cwd=ROOT,
                              env=environment(), capture_output=True,
                              text=True, timeout=30, **kwargs)

    def assert_guest(self, guest, registers, count=None):
        state = guest_state(self.directory, guest)
        self.assertEqual(state["pc"], guest.pc)
        self.assertEqual(state["instructions"], guest.instructions if count is None else count)
        self.assertEqual(state["registers"], registers)
        self.assertEqual(state["specials"][5], FLAGS)
        self.assertEqual(state["irq_entries"], 0)
        return state

    def test_cli_help(self):
        result = self.cli("--help")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Usage:", result.stdout)
        self.assertIn("FIRMWARE", result.stdout)
        self.assertIn("--headless", result.stdout)

    def test_cli_version(self):
        result = self.cli("--version")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertRegex(result.stdout, r"FM-1 emulator \(QEMU \d+\.\d+")

    def test_cli_missing_firmware(self):
        missing = self.directory / "missing.bin"
        result = self.cli("--headless", str(missing))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("cannot read firmware", result.stderr)
        self.assertIn(str(missing), result.stderr)

    def test_qmp_target(self):
        commands = [{"execute": "qmp_capabilities", "id": "capabilities"},
                    {"execute": "query-target", "id": "target"},
                    {"execute": "quit", "id": "quit"}]
        result = self.cli("--qemu", "-M", "none", "-S", "-display", "none",
                          "-serial", "none", "-monitor", "none", "-nodefaults",
                          "-no-user-config", "-qmp", "stdio",
                          input="".join(json.dumps(command) + "\n" for command in commands))
        self.assertEqual(result.returncode, 0, result.stderr)
        messages = [json.loads(line) for line in result.stdout.splitlines()]
        self.assertTrue(messages and "QMP" in messages[0])
        replies = {message["id"]: message for message in messages if "id" in message}
        for command in commands:
            self.assertIn("return", replies.get(command["id"], {}), messages)
        self.assertEqual(replies["target"]["return"]["arch"], "pi32v2")

    def test_ssync_store_load(self):
        guest = seeded_guest()
        guest.literal(1, INSPECTION)
        guest.literal(0, 0x55667788)
        guest.store(0, 1)
        guest.emit(0x0022)  # SSYNC orders the store before the load.
        guest.load(2, 1)
        registers = list(SEEDS)
        registers[:3] = [0x55667788, INSPECTION, 0x55667788]
        state = self.assert_guest(guest, registers)
        self.assertEqual(state["inspection"][0], 0x55667788)

    def test_ssync_selected_and_skipped(self):
        guest = seeded_guest()
        guest.literal(0, 0)
        guest.emit(0xEA20, 0x1001)  # IF r0 == 0, one THEN / one ELSE.
        guest.emit(0x0022)  # Selected SSYNC.
        guest.literal(3, 0xBAD)  # Skipped ELSE.
        guest.literal(0, 1)
        guest.emit(0xEA20, 0x1001)
        guest.emit(0x0022)  # Skipped SSYNC.
        guest.literal(4, 0x4444)  # Selected ELSE.
        registers = list(SEEDS)
        registers[0], registers[4] = 1, 0x4444
        self.assert_guest(guest, registers, guest.instructions - 2)

    def test_unsupported_instruction_fault(self):
        guest = Guest()
        guest.literal(0, 0x12345678)
        fault_pc = guest.pc
        guest.emit(0x0023)
        result = run_guest(self.directory, guest)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("unsupported instruction 0x0023", result.stderr)
        self.assertIn(f"at PC 0x{fault_pc:08x} after 1 instructions", result.stderr)

    def test_ssync_does_not_complete_cold_usb_request(self):
        guest = Guest()
        request = 0x160
        polls = 64
        guest.write(USB, 0x3d)
        guest.write(USB + 4, request)
        guest.literal(2, USB + 4)
        guest.literal(5, 0x8000)  # SIE DONE bit.
        guest.literal(6, 0)
        guest.literal(7, INSPECTION)
        guest.literal(1, -polls)
        loop = guest.pc
        guest.load(3, 2)
        guest.emit(0x1634)  # r4 = r3.
        guest.emit(0x19d4)  # r4 &= r5.
        guest.emit(0x1946)  # r6 |= r4, retaining DONE from every poll.
        guest.emit(0x0022)
        guest.add(1, 1)
        guest.branch_zero(1, loop, nonzero=True)
        guest.store(3, 7)
        guest.store(6, 7, 4)
        state = guest_state(self.directory, guest)
        self.assertEqual(state["pc"], guest.pc)
        self.assertEqual(state["instructions"], guest.instructions + 7 * (polls - 1))
        self.assertEqual(state["inspection"][:2], [request, 0])
        self.assertEqual(state["irq_entries"], 0)


if __name__ == "__main__":
    unittest.main()
