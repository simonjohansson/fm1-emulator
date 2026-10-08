# SPDX-License-Identifier: GPL-2.0-or-later
"""Device registers Felucca 1.0 on configures: 128-frame audio halves, UART1 (TRS MIDI IN, no input
modeled), its routing and clock fields, and USB endpoint 4's own count and DMA address."""

from pathlib import Path
import tempfile
import unittest

from support import INSPECTION, QEMU, ROOT, Guest, guest_state, run_guest

UART1 = 0x12100
ALNK0 = 0x12E00
USB = 0x11800
IOMAP = 0x5101C
CLK_CON1 = 0x10010


def halfword_store(guest, source, base):
    """h[rB+0] = rA (vendor ED50 with operand bit 0: an unsigned-offset halfword store)."""
    guest.emit(0xED50, (source << 12) | (base << 4) | 1)


class FeluccaDeviceTests(unittest.TestCase):
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

    def test_uart1_as_felucca_configures_it(self):
        # hal/fm1_uart.h fm1_uart1_midi_init, then fm1_uart1_rx_take: nothing has been received.
        ring = INSPECTION + 0x100
        guest = Guest()
        for offset, value in [(0, 0x3400), (4, 0), (0x1C, ring), (0x20, ring + 256), (0x24, 256),
                              (8, 383), (0x10, 60000), (0, 0x14C0), (0, 0x14C1), (0, 0x14C1 | 0x80)]:
            guest.write(UART1 + offset, value)
        guest.literal(5, UART1)
        guest.load(6, 5, 0x28)                  # HRXCNT
        guest.load(7, 5, 0x1C)                  # RXSADR
        guest.write(IOMAP + 12, 0x50)           # IOMAP_CON3: UT1 RX = input channel 1
        guest.write(IOMAP + 8, 49 << 8)         # IOMAP_CON2: channel 1 = PH8
        guest.literal(5, IOMAP)
        guest.load(4, 5, 8)
        guest.load(3, 5, 12)
        state = guest_state(self.directory, guest)
        self.assertEqual(state["registers"][6:8], [0, ring])
        self.assertEqual(state["registers"][3:5], [0x50, 49 << 8])

    def test_uart1_rejects_unknown_control_bits(self):
        guest = Guest()
        guest.write(UART1, 0x0002)
        result = run_guest(self.directory, guest)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("unsupported UART1 CON0 bits", result.stderr)

    def test_uart_clock_selector_field(self):
        guest = Guest()
        guest.write(CLK_CON1, 1 << 10)          # [11:10] = 1: the UART clock from PLL48M
        guest.literal(5, CLK_CON1)
        guest.load(6, 5)
        state = guest_state(self.directory, guest)
        self.assertEqual(state["registers"][6], 1 << 10)

    def test_usb_endpoint4_registers(self):
        buffer = INSPECTION + 0x200
        guest = Guest()
        guest.write(USB + 0x38, buffer)         # EP4 transmit DMA address
        guest.write(USB + 0x34, 0)              # EP4 count
        guest.literal(5, USB)
        guest.load(6, 5, 0x38)
        guest.load(7, 5, 0x34)
        state = guest_state(self.directory, guest)
        self.assertEqual(state["registers"][6:8], [buffer, 0])
        guest = Guest()
        guest.write(USB + 0x38, buffer + 2)
        result = run_guest(self.directory, guest)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("USB endpoint buffer must be aligned", result.stderr)

    def test_audio_half_lengths(self):
        for words, accepted in [(256, True), (512, True), (300, False)]:
            with self.subTest(words=words):
                guest = Guest()
                guest.literal(5, ALNK0 + 0x20)
                guest.literal(6, words)
                halfword_store(guest, 6, 5)
                result = run_guest(self.directory, guest)
                self.assertEqual(result.returncode == 0, accepted, result.stderr)
                if not accepted:
                    self.assertIn("unsupported ALNK0 DMA half length", result.stderr)


if __name__ == "__main__":
    unittest.main()
