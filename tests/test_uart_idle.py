# SPDX-License-Identifier: GPL-2.0-or-later
"""UART receiver startup stays idle without external serial input."""
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

from support import COMMAND, INSPECTION, ROOT, Guest, environment, guest_state, run_guest

UART = 0x12100


class UARTIdleTests(unittest.TestCase):
    def setUp(self):
        directory = ROOT / ".cache/tests"
        directory.mkdir(parents=True, exist_ok=True)
        temporary = tempfile.TemporaryDirectory(dir=directory)
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)

    def test_clear_reload_and_enabled_receiver_readback(self):
        guest = Guest()
        for offset, value in ((0, 0x3400), (4, 0), (28, INSPECTION),
                              (32, INSPECTION), (36, 128), (8, 383),
                              (16, 768), (0, 0x14c0), (0, 0x14c1)):
            guest.write(UART + offset, value)
        for n, offset in enumerate((0, 4, 36, 40)):
            guest.literal(2, UART + offset)
            guest.load(3, 2)
            guest.literal(4, INSPECTION + n * 4)
            guest.store(3, 4)
        state = guest_state(self.directory, guest)
        self.assertEqual(state["inspection"][:4], [0x41, 0, 128, 0])
        self.assertEqual(state["irq_entries"], 0)

    def test_idle_does_not_receive_write_dma_or_raise_irq(self):
        guest = Guest()
        guest.write(INSPECTION, 0xdeadbeef)
        guest.write(UART + 28, INSPECTION)
        guest.write(UART + 32, INSPECTION + 4)
        guest.write(UART + 36, 4)
        guest.write(UART + 16, 1)
        guest.write(UART, 0x14c1)
        guest.literal(0, 1_000_000)
        loop = guest.pc
        guest.add(0, -1)
        guest.branch_zero(0, loop, nonzero=True)
        guest.literal(2, UART)
        guest.load(3, 2)
        guest.load(4, 2, 40)
        image = self.directory / "guest.bin"
        image.write_bytes(guest.bytes() + bytes(16))
        result = subprocess.run(
            [*COMMAND, "-kernel", str(image), "-append", "diag"],
            cwd=ROOT, env=environment(FM1_POC_STOP_PC=hex(guest.pc),
                                      FM1_POC_MAX_INSTRUCTIONS="3000000"),
            capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        state = json.loads(result.stdout)
        self.assertGreaterEqual(state["virtual_ns"], 16_000_000)
        self.assertEqual(state["inspection"][0], 0xdeadbeef)
        self.assertEqual(state["registers"][3:5], [0x41, 0])
        self.assertEqual(state["irq_entries"], 0)

    def test_transmission_remains_explicitly_unsupported(self):
        guest = Guest()
        guest.write(UART + 24, 1)
        result = run_guest(self.directory, guest)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("UART1 transmission is unimplemented", result.stderr)

    def test_uart_clock_field_preserves_usb_selector(self):
        guest = Guest()
        guest.write(0x10010, 3)
        guest.literal(2, 0x10010)
        guest.literal(4, 0x400)
        guest.emit(0xe864, 0x2400)  # Set UART selector bit 10 by ordinary RMW.
        guest.load(3, 2)
        state = guest_state(self.directory, guest)
        self.assertEqual(state["registers"][3], 0x403)

    def test_uart_pin_routes_preserve_nor_and_lcd_routes(self):
        guest = Guest()
        guest.write(0x51020, 0x10)
        guest.write(0x51024, 0x3100)
        guest.write(0x51028, 0x50)
        guest.literal(2, 0x5101c)
        for register, offset in ((3, 0), (4, 4), (5, 8), (6, 12)):
            guest.load(register, 2, offset)
        state = guest_state(self.directory, guest)
        self.assertEqual(state["registers"][3:7], [0x20, 0x10, 0x3100, 0x50])


if __name__ == "__main__":
    unittest.main()
