# SPDX-License-Identifier: GPL-2.0-or-later
"""SPI1 reads bounded SRAM/XIP DMA sources through guest memory."""
import json
from pathlib import Path
import struct
import subprocess
import tempfile
import unittest

from support import COMMAND, ENTRY, INSPECTION, ROOT, Guest, environment
from test_nor_write import SRAM_CODE, raw_image

SPI = 0x11d00
PC_OUT = 0x50080
IOMAP = 0x51020
IRQ_PENDING = 0x01eef180
SRAM_BASE, SRAM_END = 0x01c00000, 0x01c80000
XIP_BASE, XIP_END = 0x02000000, 0x020fc000
PIXELS = bytes.fromhex("f800 07e0 001f 8410")
SOURCE_FAULT = "SPI1 DMA requires a nonempty source entirely in SRAM or XIP"
GAMMA = bytes.fromhex("d00d140b0b073a44500813132d32")
# Parameter bytes transcribed from the unchanged stock application's table.
STOCK_INIT = ((0x11, b""), (0x2a, bytes.fromhex("000000ef")),
              (0x2b, bytes.fromhex("00280117")),
              (0xb2, bytes.fromhex("0c0c0c0033")), (0x20, b""),
              (0xb7, b"\x56"), (0xbb, b"\x18"), (0xc0, b"\x2c"),
              (0xc2, b"\x01"), (0xc3, b"\x1f"), (0xc4, b"\x20"),
              (0xc6, b"\x0f"), (0xd0, b"\xa6\xa1"),
              (0xe0, GAMMA), (0xe1, GAMMA), (0x36, b"\x00"),
              (0x3a, b"\x55"), (0xe7, b"\x00"), (0x51, b"\xff"),
              (0x21, b""))


class LCDGuest(Guest):
    def __init__(self, base=ENTRY, stock=False):
        super().__init__(base)
        self.literal(2, SPI)
        self.literal(5, 0x8000)
        self.write(IOMAP, 0x10)
        self.write(0x50000, 0)  # Active-low panel backlight.
        self.write(SPI, 0x21)
        self.write(SPI + 4, 4)
        if stock:
            for command, parameters in STOCK_INIT:
                self.command(command, parameters)
            self.command(0x29)
        else:
            for command, parameters in ((0x3a, b"\x55"), (0x36, b"\x00"),
                                        (0x11, b""), (0x29, b"")):
                self.command(command, parameters)
        # Stock's initial rows 40..279 must be accepted before this reset.
        self.command(0x2a, b"\x00\x00\x00\x01")
        self.command(0x2b, b"\x00\x00\x00\x01")
        self.command(0x2c)

    def command(self, command, parameters=b""):
        self.write(PC_OUT, 0)  # CS asserted, D/C command.
        self.transfer(command)
        self.write(PC_OUT, 0x100)  # CS asserted, D/C data.
        for byte in parameters:
            self.transfer(byte)

    def wait_complete(self):
        poll = self.pc
        self.load(3, 2)
        self.emit(0x1634)  # r4 = r3.
        self.emit(0x19d4)  # r4 &= r5 (SPI completion).
        self.branch_zero(4, poll)

    def transfer(self, byte):
        self.write(SPI + 8, byte)
        self.wait_complete()
        self.write(SPI, 0x4021)

    def record_pending(self, slot):
        self.load(3, 2)
        self.emit(0x1634)
        self.emit(0x19d4)
        self.literal(1, INSPECTION + slot * 4)
        self.store(4, 1)

    def record_word(self, address, slot):
        self.literal(1, address)
        self.load(3, 1)
        self.literal(1, INSPECTION + slot * 4)
        self.store(3, 1)

    def dma(self, address, length):
        self.write(SPI + 12, address)
        fault_pc = self.write(SPI + 16, length)
        self.record_pending(0)
        self.wait_complete()
        self.record_pending(1)
        self.write(SPI, 0x4021)
        self.record_pending(2)
        return fault_pc


class LCDDMATests(unittest.TestCase):
    def setUp(self):
        directory = ROOT / ".cache/tests"
        directory.mkdir(parents=True, exist_ok=True)
        temporary = tempfile.TemporaryDirectory(dir=directory)
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)

    def run_lcd(self, guest, image=None):
        path = self.directory / "guest.bin"
        path.write_bytes(image if image is not None else guest.bytes() + bytes(16))
        result = subprocess.run(
            [*COMMAND, "-kernel", str(path), "-append", "application"],
            cwd=ROOT, env=environment(FM1_POC_STOP_PC=hex(guest.pc),
                                      FM1_POC_MAX_INSTRUCTIONS="2000000",
                                      FM1_POC_STATE_DIR=str(self.directory)),
            capture_output=True, text=True, timeout=30)
        state = json.loads((self.directory / "state.json").read_text())
        ram = (self.directory / "state.sram").read_bytes()
        pending = struct.unpack_from("<III", ram, INSPECTION - SRAM_BASE)
        return result, state, pending

    def test_sram_and_xip_dma_render_the_same_pixels_and_complete(self):
        expected = bytearray(240 * 240 * 3)
        for (x, y), rgb in zip(((0, 0), (1, 0), (0, 1), (1, 1)),
                              (b"\xff\x00\x00", b"\x00\xff\x00",
                               b"\x00\x00\xff", b"\x84\x82\x84")):
            offset = (y * 240 + x) * 3
            expected[offset:offset + 3] = rgb
        expected = b"P6\n240 240\n255\n" + expected
        # Exact upper boundaries are accepted; a crossing is checked below.
        for source in (SRAM_END - len(PIXELS), XIP_END - len(PIXELS)):
            with self.subTest(source=hex(source)):
                guest = LCDGuest(stock=True)
                if source < XIP_BASE:
                    for offset in range(0, len(PIXELS), 4):
                        guest.write(source + offset,
                                    int.from_bytes(PIXELS[offset:offset + 4], "little"))
                guest.dma(source, len(PIXELS))
                image = bytearray(guest.bytes() + bytes(16))
                if source >= XIP_BASE:
                    offset = source - ENTRY
                    self.assertGreaterEqual(offset, len(image))
                    image.extend(b"\xff" * (offset + len(PIXELS) - len(image)))
                    image[offset:offset + len(PIXELS)] = PIXELS
                result, state, pending = self.run_lcd(guest, image)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(state["reason"], "checkpoint reached")
                self.assertEqual(state["pc"], guest.pc)
                self.assertEqual(pending, (0, 0x8000, 0))
                self.assertEqual(state["lcd"], {
                    "visible": True, "busy": False, "pixels_written": 4,
                    "commands": 24, "dma_transfers": 1, "completed_transfers": 87})
                self.assertEqual((self.directory / "lcd.ppm").read_bytes(), expected)

    def test_invalid_sources_fault_before_dma_or_completion(self):
        cases = (("empty", INSPECTION, 0), ("mmio", SPI, 8),
                 ("below SRAM", SRAM_BASE - 4, 8),
                 ("cross SRAM end", SRAM_END - 4, 8),
                 ("below XIP", XIP_BASE - 4, 8),
                 ("cross XIP end", XIP_END - 4, 8),
                 ("unmapped XIP end", XIP_END, 8),
                 ("wrapped address", 0xfffffffc, 8),
                 ("wrapped length", XIP_BASE, 0xffffffff))
        for name, source, length in cases:
            with self.subTest(case=name):
                guest = LCDGuest()
                fault_pc = guest.dma(source, length)
                result, state, _ = self.run_lcd(guest)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(SOURCE_FAULT, result.stderr)
                self.assertEqual(state["reason"], SOURCE_FAULT)
                self.assertEqual(state["pc"], fault_pc)
                self.assertEqual(state["lcd"], {
                    "visible": True, "busy": False, "pixels_written": 0,
                    "commands": 7, "dma_transfers": 0, "completed_transfers": 17})

    def test_inclusive_window_skips_column_240_and_accepts_hidden_row_240(self):
        guest = LCDGuest()
        guest.command(0x2a, bytes.fromhex("00ef00f0"))
        guest.command(0x2b, bytes.fromhex("00ef00f0"))
        guest.command(0x2c)
        source = INSPECTION + 0x100
        for offset in range(0, len(PIXELS), 4):
            guest.write(source + offset,
                        int.from_bytes(PIXELS[offset:offset + 4], "little"))
        guest.dma(source, len(PIXELS))
        result, state, pending = self.run_lcd(guest)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(state["reason"], "checkpoint reached")
        self.assertEqual(pending, (0, 0x8000, 0))
        self.assertEqual(state["lcd"], {
            "visible": True, "busy": False, "pixels_written": 2,
            "commands": 10, "dma_transfers": 1, "completed_transfers": 29})
        # Red lands at (239,239), green at the ignored column240, blue at
        # controller row240 (below the viewport), and gray at ignored column240.
        expected = b"P6\n240 240\n255\n" + bytes(240 * 240 * 3 - 3) + b"\xff\x00\x00"
        self.assertEqual((self.directory / "lcd.ppm").read_bytes(), expected)

    def test_xip_dma_obeys_disabled_sfc_at_completion(self):
        guest = LCDGuest(SRAM_CODE)
        guest.write(0x40200, 0)
        guest.dma(XIP_BASE + 0x8c000, len(PIXELS))
        # The existing NOR bootstrap copies code to SRAM while XIP is enabled.
        # Only the DMA, not instruction fetch, then touches disabled XIP.
        result, state, pending = self.run_lcd(guest, raw_image(guest))
        reason = "XIP access while SFC is disabled or unrouted"
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(reason, result.stderr)
        self.assertEqual(state["reason"], reason)
        self.assertEqual(pending, (0, 0, 0))
        self.assertEqual(state["lcd"], {
            "visible": True, "busy": True, "pixels_written": 0,
            "commands": 7, "dma_transfers": 1, "completed_transfers": 17})

    def test_command_after_incomplete_gamma_is_rejected(self):
        guest = LCDGuest()
        guest.command(0xe0, GAMMA[:-1])
        guest.write(PC_OUT, 0)
        guest.write(SPI + 8, 0x29)
        guest.wait_complete()
        result, state, _ = self.run_lcd(guest)
        reason = "LCD command interrupts an incomplete parameter/pixel"
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(reason, result.stderr)
        self.assertEqual(state["reason"], reason)
        self.assertEqual(state["lcd"]["commands"], 8)
        self.assertEqual(state["lcd"]["completed_transfers"], 31)
        self.assertEqual(state["lcd"]["pixels_written"], 0)

    def test_irq_enable_masks_completed_pending_until_acknowledged(self):
        guest = LCDGuest()
        # A single data byte is enough to complete a transfer; it leaves an
        # incomplete pixel, which remains legal until a new command arrives.
        guest.write(SPI + 8, 0xf8)
        guest.wait_complete()
        guest.write(SPI, 0x2021)
        guest.record_word(IRQ_PENDING, 0)
        guest.write(SPI, 0x21)
        guest.record_word(IRQ_PENDING, 1)
        guest.record_word(SPI, 2)
        guest.write(SPI, 0x2021)
        guest.record_word(IRQ_PENDING, 3)
        guest.write(SPI, 0x6021)
        guest.record_word(IRQ_PENDING, 4)
        guest.record_word(SPI, 5)
        result, state, _ = self.run_lcd(guest)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(state["reason"], "checkpoint reached")
        ram = (self.directory / "state.sram").read_bytes()
        words = struct.unpack_from("<6I", ram, INSPECTION - SRAM_BASE)
        self.assertEqual(words, (1 << 16, 0, 0x8021, 1 << 16, 0, 0x2021))
        self.assertEqual(state["irq_entries"], 0)
        self.assertEqual(state["lcd"]["completed_transfers"], 18)

    def test_spi1_dma_interrupt_enters_once_and_returns_after_ack(self):
        guest = LCDGuest()
        source = INSPECTION + 0x100
        for offset in range(0, len(PIXELS), 4):
            guest.write(source + offset,
                        int.from_bytes(PIXELS[offset:offset + 4], "little"))
        handler_offset = 0x2000
        handler = Guest(ENTRY + handler_offset)
        handler.emit(0x0460)  # Preserve r0-r3 around the interrupt.
        handler.write(SPI, 0x6021)  # ACK, leaving SPI1 IRQ enabled.
        handler.add(6, 1)
        handler.emit(0x0440)
        handler.emit(0x0081)  # RTI.
        guest.literal(14, 0x01c13000, special=True)  # SP.
        guest.literal(13, 0x01c14000, special=True)  # Interrupt SSP.
        guest.write(0x01c7fe00 + 16 * 4, handler.base)
        guest.write(0x01eef108, 3)  # Source16 enabled at priority1.
        guest.literal(6, 0)
        guest.literal(0, 0x300)
        guest.emit(0xe064, 0x0b80)  # ICFG enables interrupts in supervisor mode.
        guest.write(SPI, 0x2021)
        guest.write(SPI + 12, source)
        guest.write(SPI + 16, len(PIXELS))
        poll = guest.pc
        guest.branch_zero(6, poll)
        guest.literal(1, IRQ_PENDING)
        guest.load(7, 1)
        image = bytearray(guest.bytes())
        self.assertLess(len(image), handler_offset)
        image.extend(b"\xff" * (handler_offset - len(image)))
        image.extend(handler.bytes() + bytes(16))
        result, state, _ = self.run_lcd(guest, image)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(state["reason"], "checkpoint reached")
        self.assertEqual(state["irq_entries"], 1)
        self.assertEqual(state["rti_count"], 1)
        self.assertEqual(state["last_irq_source"], 16)
        self.assertFalse(state["in_irq"])
        self.assertEqual(state["registers"][6:8], [1, 0])
        self.assertEqual(state["specials"][14], 0x01c13000)
        self.assertEqual(state["specials"][13], 0x01c14000)
        self.assertEqual(state["lcd"], {
            "visible": True, "busy": False, "pixels_written": 4,
            "commands": 7, "dma_transfers": 1, "completed_transfers": 18})


if __name__ == "__main__":
    unittest.main()
