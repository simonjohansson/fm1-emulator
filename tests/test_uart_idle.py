# SPDX-License-Identifier: GPL-2.0-or-later
"""UART receiver stays idle; measured TX DMA status and IRQ are independent."""
import json
from pathlib import Path
import subprocess
import struct
import tempfile
import unittest

from support import COMMAND, ENTRY, INSPECTION, ROOT, Guest, environment, guest_state, run_guest

UART = 0x12100
IRQ_PENDING = 0x01eef180
TX_SOURCE = INSPECTION + 0x100


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


class UARTTXGuest(Guest):
    def __init__(self, divisor_three=False):
        super().__init__()
        self.control = 0x15 if divisor_three else 5
        self.write(0x10010, 0x400)  # UART selector1: PLL48M.
        self.write(UART + 4, 0)
        self.write(UART + 8, 383)
        self.write(UART, self.control | 0x2000)  # Enable TX/IRQ, clear TPND.
        self.write(TX_SOURCE, 0x007f4090)
        self.write(UART + 20, TX_SOURCE)
        self.literal(2, UART)
        self.literal(5, 0x8000)

    def record(self, address, slot):
        self.literal(1, address)
        self.load(3, 1)
        self.literal(1, INSPECTION + slot * 4)
        self.store(3, 1)

    def wait_complete(self):
        poll = self.pc
        self.load(3, 2)
        self.emit(0x1634)  # r4 = r3.
        self.emit(0x19d4)  # r4 &= r5 (TPND).
        self.branch_zero(4, poll)

    def delay(self, iterations):
        self.literal(4, iterations)
        poll = self.pc
        self.add(4, -1)
        self.branch_zero(4, poll, nonzero=True)


class UARTTXTests(unittest.TestCase):
    def setUp(self):
        directory = ROOT / ".cache/tests"
        directory.mkdir(parents=True, exist_ok=True)
        temporary = tempfile.TemporaryDirectory(dir=directory)
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)

    def run_tx(self, guest, handler=None):
        image = bytearray(guest.bytes() + bytes(16))
        if handler is not None:
            offset = handler.base - ENTRY
            self.assertGreaterEqual(offset, len(image))
            image.extend(b"\xff" * (offset - len(image)))
            image.extend(handler.bytes() + bytes(16))
        path = self.directory / "guest.bin"
        path.write_bytes(image)
        result = subprocess.run(
            [*COMMAND, "-kernel", str(path), "-append", "application"],
            cwd=ROOT, env=environment(FM1_POC_STOP_PC=hex(guest.pc),
                                      FM1_POC_MAX_INSTRUCTIONS="2000000",
                                      FM1_POC_STATE_DIR=str(self.directory)),
            capture_output=True, text=True, timeout=30)
        state = json.loads((self.directory / "state.json").read_text())
        ram = (self.directory / "state.sram").read_bytes()
        words = struct.unpack_from("<12I", ram, INSPECTION - 0x01c00000)
        return result, state, words

    def test_tx_completion_count_and_sticky_pending_clear(self):
        for divisor_three in (False, True):
            with self.subTest(divisor_three=divisor_three):
                guest = UARTTXGuest(divisor_three)
                guest.write(UART + 24, 3)
                guest.record(UART, 0)
                guest.record(IRQ_PENDING, 1)
                guest.wait_complete()
                guest.record(UART, 2)
                guest.record(UART + 24, 3)
                guest.record(IRQ_PENDING, 4)
                guest.delay(6250)  # 100 us with the shared shift=3 command.
                guest.record(UART, 5)
                guest.record(IRQ_PENDING, 6)
                guest.write(UART, guest.control | 0x2000)
                guest.record(UART, 7)
                guest.record(IRQ_PENDING, 8)
                guest.record(UART + 40, 9)
                guest.record(TX_SOURCE, 10)
                result, state, words = self.run_tx(guest)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(state["reason"], "checkpoint reached")
                self.assertEqual(words[:11], (
                    guest.control, 0, guest.control | 0x8000, 0, 1 << 20,
                    guest.control | 0x8000, 1 << 20, guest.control, 0, 0,
                    0x007f4090))
                self.assertEqual(state["irq_entries"], 0)

    def test_tx_duration_matches_independent_hardware_baud_sweep(self):
        # Physical TIMER4 counts at OSC24M for TX lengths1,3,8 with PLL48M
        # selected. Bounds include the measured hardware polling overhead
        # and this guest's short completion poll; no implementation formula
        # supplies the expected durations.
        captures = ((False, 95, (1978, 5918, 15772)),
                    (False, 191, (3944, 11820, 31516)),
                    (False, 383, (7881, 23628, 63004)),
                    (False, 767, (15751, 47246, 125980)),
                    (True, 383, (5960, 17869, 47643)))
        for divisor_three, baud, hardware_ticks in captures:
            baseline = UARTTXGuest(divisor_three)
            baseline.write(UART + 8, baud)
            result, state, _ = self.run_tx(baseline)
            self.assertEqual(result.returncode, 0, result.stderr)
            start_ns = state["virtual_ns"]
            durations = {}
            for count, ticks in zip((1, 3, 8), hardware_ticks):
                with self.subTest(divisor_three=divisor_three, baud=baud, count=count):
                    guest = UARTTXGuest(divisor_three)
                    guest.write(UART + 8, baud)
                    guest.write(UART + 24, count)
                    guest.wait_complete()
                    result, state, _ = self.run_tx(guest)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(state["reason"], "checkpoint reached")
                    elapsed = state["virtual_ns"] - start_ns
                    measured = ticks * 1_000_000_000 // 24_000_000
                    self.assertGreaterEqual(elapsed, measured - 1000)
                    self.assertLessEqual(elapsed, measured + 1000)
                    durations[count] = elapsed
            # The increment from one byte to three bytes excludes fixed
            # startup/poll cost and verifies length changes actual cadence.
            measured_delta = ((hardware_ticks[1] - hardware_ticks[0]) *
                              1_000_000_000 // 24_000_000)
            self.assertGreaterEqual(durations[3] - durations[1], measured_delta - 500)
            self.assertLessEqual(durations[3] - durations[1], measured_delta + 500)

    def test_tx_irq_enable_masks_without_clearing_completion(self):
        guest = UARTTXGuest()
        guest.write(UART, 1)
        guest.write(UART + 24, 1)
        guest.wait_complete()
        guest.record(UART, 0)
        guest.record(IRQ_PENDING, 1)
        guest.write(UART, 5)
        guest.record(IRQ_PENDING, 2)
        guest.write(UART, 1)
        guest.record(IRQ_PENDING, 3)
        guest.record(UART, 4)
        guest.write(UART, 5)
        guest.record(IRQ_PENDING, 5)
        guest.write(UART, 0x2005)
        guest.record(UART, 6)
        guest.record(IRQ_PENDING, 7)
        result, state, words = self.run_tx(guest)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(words[:8], (0x8001, 0, 1 << 20, 0, 0x8001, 1 << 20, 5, 0))
        self.assertEqual(state["irq_entries"], 0)

    def test_tx_interrupt_enters_once_and_returns_after_ack(self):
        guest = UARTTXGuest()
        handler = Guest(ENTRY + 0x2000)
        handler.emit(0x0460)  # Preserve r0-r3 around the interrupt.
        handler.write(UART, 0x2005)
        handler.add(6, 1)
        handler.emit(0x0440)
        handler.emit(0x0081)  # RTI.
        guest.literal(14, 0x01c13000, special=True)
        guest.literal(13, 0x01c14000, special=True)
        guest.write(0x01c7fe00 + 20 * 4, handler.base)
        guest.write(0x01eef108, 0x30000)  # Source20, priority1.
        guest.literal(6, 0)
        guest.literal(0, 0x300)
        guest.emit(0xe064, 0x0b80)  # ICFG: enable IRQs in supervisor mode.
        guest.write(UART + 24, 3)
        poll = guest.pc
        guest.branch_zero(6, poll)
        guest.record(UART, 0)
        guest.record(UART + 24, 1)
        guest.record(IRQ_PENDING, 2)
        result, state, words = self.run_tx(guest, handler)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(state["reason"], "checkpoint reached")
        self.assertEqual(state["irq_entries"], 1)
        self.assertEqual(state["rti_count"], 1)
        self.assertEqual(state["last_irq_source"], 20)
        self.assertFalse(state["in_irq"])
        self.assertEqual(state["registers"][6], 1)
        self.assertEqual(words[:3], (5, 0, 0))
        self.assertEqual(state["specials"][14], 0x01c13000)
        self.assertEqual(state["specials"][13], 0x01c14000)

    def test_dma_rejects_invalid_source_count_enable_and_clock(self):
        cases = (("MMIO", UART + 20, UART, 3,
                  "unsupported UART1 DMA source or count"),
                 ("empty address", UART + 20, 0, 3,
                  "unsupported UART1 DMA source or count"),
                 ("cross SRAM end", UART + 20, 0x01c7ffff, 2,
                  "unsupported UART1 DMA source or count"),
                 ("wrapped address", UART + 20, 0xfffffffc, 8,
                  "unsupported UART1 DMA source or count"),
                 ("oversized count", UART + 20, TX_SOURCE, 0x10000,
                  "unsupported UART1 DMA source or count"),
                 ("disabled UART", UART, 0, 3,
                  "unsupported UART1 DMA mode or clock"),
                 ("unsupported clock", 0x10010, 0, 3,
                  "unsupported UART1 DMA mode or clock"))
        for name, address, value, count, reason in cases:
            with self.subTest(case=name):
                guest = UARTTXGuest()
                guest.write(address, value)
                fault_pc = guest.write(UART + 24, count)
                result, state, _ = self.run_tx(guest)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(reason, result.stderr)
                self.assertEqual(state["reason"], reason)
                self.assertEqual(state["pc"], fault_pc)
                self.assertEqual(state["irq_entries"], 0)

    def test_active_transfer_changes_fault_before_completion(self):
        cases = (("restart", UART + 24, 3, "unsupported UART1 DMA restart"),
                 ("cancel by zero", UART + 24, 0, "unsupported UART1 DMA restart"),
                 ("acknowledge before completion", UART, 0x2005,
                  "unsupported UART1 active acknowledgment"),
                 ("source", UART + 20, TX_SOURCE + 4,
                  "unsupported UART1 active DMA source change"),
                 ("baud", UART + 8, 384, "unsupported UART1 active baud change"),
                 ("disable", UART, 4, "unsupported UART1 active control change"),
                 ("divider", UART, 0x15, "unsupported UART1 active control change"),
                 ("clock", 0x10010, 0, "unsupported UART1 active clock change"))
        for name, address, value, reason in cases:
            with self.subTest(case=name):
                guest = UARTTXGuest()
                guest.write(UART + 24, 3)
                fault_pc = guest.write(address, value)
                result, state, _ = self.run_tx(guest)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(reason, result.stderr)
                self.assertEqual(state["reason"], reason)
                self.assertEqual(state["pc"], fault_pc)
                self.assertEqual(state["irq_entries"], 0)

    def test_pending_completion_requires_ack_before_restart(self):
        guest = UARTTXGuest()
        guest.write(UART + 24, 1)
        guest.wait_complete()
        fault_pc = guest.write(UART + 24, 1)
        result, state, _ = self.run_tx(guest)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(state["reason"], "unsupported UART1 DMA restart")
        self.assertEqual(state["pc"], fault_pc)

    def test_exact_sram_end_source_and_zero_idle_count(self):
        guest = UARTTXGuest()
        guest.write(UART + 20, 0)
        guest.write(UART + 24, 0)  # Existing idle-zero behavior is retained.
        guest.record(UART + 24, 0)
        guest.record(IRQ_PENDING, 1)
        guest.write(UART + 20, 0x01c7ffff)
        guest.write(UART + 24, 1)
        guest.wait_complete()
        guest.record(UART + 24, 2)
        result, state, words = self.run_tx(guest)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(state["reason"], "checkpoint reached")
        self.assertEqual(words[:3], (0, 0, 0))

    def test_byte_buffer_transmit_stays_explicitly_unsupported(self):
        guest = UARTTXGuest()
        fault_pc = guest.write(UART + 12, 0x90)
        result, state, _ = self.run_tx(guest)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(state["reason"], "unsupported UART1 transmit or read-only write")
        self.assertEqual(state["pc"], fault_pc)


if __name__ == "__main__":
    unittest.main()
