# SPDX-License-Identifier: GPL-2.0-or-later
"""Synthetic SRAM guests exercise SPI NOR writes and coherent XIP reads.

Protocol expectations come from the saved P25Q80H datasheet sections 10.2,
10.3, 10.5, 10.20 and 10.24. The model uses its documented typical 2 ms
program and 8 ms sector-erase times, not calibrated board timing.
"""
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

from support import COMMAND, ENTRY, INSPECTION, QEMU, ROOT, Guest, environment

SRAM_CODE = 0x01c10000
BODY_OFFSET = 0x1000
RAW_FLASH_OFFSET = 0x4120
XIP_BASE = 0x02000000
XIP_FLASH_OFFSET = 0x4000
SPI = 0x11c00
SFC = 0x40200
PD_OUT = 0x500c0
IOMAP = 0x5101c
TX = 0x29
RX = 0x1029
ACK = 0x4000


class NORGuest(Guest):
    def __init__(self):
        super().__init__(SRAM_CODE)
        self.literal(2, SPI)
        self.literal(5, 0x8000)  # SPI byte completion mask.
        self.literal(6, 1)       # NOR WIP mask.
        self.literal(7, INSPECTION)
        self.write(SFC, 0)
        self.write(IOMAP, 0)

    def select(self):
        self.write(PD_OUT, 0)

    def release(self):
        self.write(PD_OUT, 1)

    def transfer(self, byte=0xff, receive=False):
        control = RX if receive else TX
        self.literal(0, control)
        self.store(0, 2)
        self.literal(0, byte)
        self.store(0, 2, 8)
        poll = self.pc
        self.load(3, 2)
        self.emit(0x1634)  # r4 = r3.
        self.emit(0x19d4)  # r4 &= r5 (SPI pending).
        self.branch_zero(4, poll)
        if receive:
            self.load(3, 2, 8)
        self.literal(0, control | ACK)
        self.store(0, 2)

    def command(self, opcode, address=None, data=b"", release=True):
        self.select()
        self.transfer(opcode)
        if address is not None:
            for byte in address.to_bytes(3, "big"):
                self.transfer(byte)
        for byte in data:
            self.transfer(byte)
        if release:
            self.release()

    def record(self, slot):
        self.store(3, 7, slot * 4)

    def status(self, slot=None):
        self.select()
        self.transfer(0x05)
        self.transfer(receive=True)
        if slot is not None:
            self.record(slot)
        self.release()

    def wait_idle(self):
        # Keep CS asserted while polling RDSR, just as the chip permits.
        self.select()
        self.transfer(0x05)
        poll = self.pc
        self.transfer(receive=True)
        self.emit(0x1634)  # r4 = received SR1.
        self.emit(0x19e4)  # r4 &= r6 (WIP).
        self.branch_zero(4, poll, nonzero=True)
        self.release()

    def spi_read(self, address, slots):
        self.command(0x0b, address, b"\xff", release=False)
        for slot in slots:
            self.transfer(receive=True)
            self.record(slot)
        self.release()

    def restore_xip(self):
        self.write(IOMAP, 0x20)
        self.write(SFC, 1)

    def xip_read(self, address, slot=None, register=3):
        self.literal(1, XIP_BASE + address - XIP_FLASH_OFFSET)
        self.load(register, 1)
        if slot is not None:
            self.record(slot)

    def delay(self, iterations):
        # With the shared shift=3 command, two instructions take 16 ns.
        self.literal(4, iterations)
        poll = self.pc
        self.add(4, -1)
        self.branch_zero(4, poll, nonzero=True)


def raw_image(body, patches=()):
    """Bootstrap copies the embedded body from XIP to SRAM before disabling SFC."""
    payload = body.bytes()
    payload += bytes((-len(payload)) % 4)
    bootstrap = Guest()
    bootstrap.literal(2, ENTRY + BODY_OFFSET)
    bootstrap.literal(3, SRAM_CODE)
    bootstrap.literal(4, len(payload) // 4)
    copy = bootstrap.pc
    bootstrap.load(0, 2)
    bootstrap.store(0, 3)
    bootstrap.add(2, 4)
    bootstrap.add(3, 4)
    bootstrap.add(4, -1)
    bootstrap.branch_zero(4, copy, nonzero=True)
    bootstrap.literal(1, SRAM_CODE)
    bootstrap.emit(0xe064, 0x1380)  # RETS = r1.
    bootstrap.emit(0x0080)          # RTS enters the SRAM body.
    image = bytearray(b"\xff" * (BODY_OFFSET + len(payload) + 16))
    image[:len(bootstrap.bytes())] = bootstrap.bytes()
    image[BODY_OFFSET:BODY_OFFSET + len(payload)] = payload
    for address, data in patches:
        offset = address - RAW_FLASH_OFFSET
        assert offset >= len(image), "fixture must not overwrite guest code"
        image.extend(b"\xff" * max(0, offset + len(data) - len(image)))
        image[offset:offset + len(data)] = data
    return bytes(image)


class NORWriteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not QEMU.is_file():
            raise RuntimeError("Build ./emulator with mise run build before testing")
        cls.cache = ROOT / ".cache/tests"
        cls.cache.mkdir(parents=True, exist_ok=True)

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(dir=self.cache)
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)

    def run_nor(self, guest, patches=()):
        image = self.directory / "guest.bin"
        initial = raw_image(guest, patches)
        image.write_bytes(initial)
        result = subprocess.run(
            [*COMMAND, "-kernel", str(image), "-append", "diag"],
            cwd=ROOT, env=environment(FM1_POC_STOP_PC=hex(guest.pc),
                                      FM1_POC_MAX_INSTRUCTIONS="5000000"),
            capture_output=True, text=True, timeout=30)
        self.assertEqual(image.read_bytes(), initial, "NOR writes changed the host input")
        return result

    def state(self, guest, patches=()):
        result = self.run_nor(guest, patches)
        self.assertEqual(result.returncode, 0, result.stderr)
        state = json.loads(result.stdout)
        self.assertEqual(state["pc"], guest.pc)
        self.assertEqual(state["irq_entries"], 0)
        return state

    def test_wren_wrdi_status_and_read_only_xip(self):
        guest = NORGuest()
        guest.status(0)
        guest.command(0x06)
        guest.status(1)
        guest.command(0x04)
        guest.status(2)
        guest.restore_xip()
        self.assertEqual(self.state(guest)["inspection"][:3], [0, 2, 0])

        guest.write(XIP_BASE + 0x8c000, 0)
        result = self.run_nor(guest)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("write to read-only XIP (NOR)", result.stderr)

    def test_program_and_erase_without_wel_leave_storage_unchanged(self):
        guest = NORGuest()
        guest.command(0x02, 0x90000, b"\x00\x00\x00\x00")
        guest.status(0)
        guest.command(0x20, 0x900a5)
        guest.status(1)
        guest.spi_read(0x90000, range(2, 6))
        guest.restore_xip()
        guest.xip_read(0x90000, 6)
        state = self.state(guest, [(0x90000, b"\x12\x34\x56\x78")])
        self.assertEqual(state["inspection"][:7],
                         [0, 0, 0x12, 0x34, 0x56, 0x78, 0x78563412])

    def test_xip_flush_while_sfc_disabled_does_not_read_memory(self):
        address = XIP_BASE + 0x8c000
        guest = NORGuest()
        guest.literal(2, address)
        guest.emit(0x0232)  # FLUSH r2 orders an XIP cache line, without a read.
        guest.restore_xip()
        self.assertEqual(self.state(guest)["registers"][2], address)

        guest = NORGuest()
        guest.literal(2, address)
        guest.load(0, 2)
        result = self.run_nor(guest)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("XIP access while SFC is disabled or unrouted", result.stderr)

    def test_program_cs_commit_and_wrap_last_page_and_bit_clearing(self):
        guest = NORGuest()
        guest.command(0x06)
        guest.command(0x02, 0x900fe, b"\x0f\xf0\x55\xaa", release=False)
        # A delay longer than tPP while CS is low must not start programming.
        guest.delay(140000)
        guest.release()
        guest.status(0)
        guest.wait_idle()
        guest.status(1)
        guest.spi_read(0x900fe, [2, 3])
        guest.spi_read(0x90000, [4, 5])
        guest.command(0x06)
        guest.command(0x02, 0x90000, b"\x0f\xff")
        guest.wait_idle()
        guest.spi_read(0x90000, [6, 7])
        guest.command(0x06)
        # The first zero is discarded when the final byte wraps over it.
        guest.command(0x02, 0x901ff, b"\x00" + b"\x5a" * 256)
        guest.wait_idle()
        guest.restore_xip()
        for address, slot in ((0x90000, 8), (0x900fc, 9),
                              (0x90100, 10), (0x901fc, 11)):
            guest.xip_read(address, slot)
        guest.xip_read(0x90200, register=6)
        state = self.state(guest)
        self.assertEqual(state["inspection"],
                         [3, 0, 0x0f, 0xf0, 0x55, 0xaa, 5, 0xaa,
                          0xffffaa05, 0xf00fffff, 0x5a5a5a5a, 0x5a5a5a5a])
        self.assertEqual(state["registers"][6], 0xffffffff)

    def test_sector_boundaries_and_program_erase_virtual_cadence(self):
        patches = [(0x8fffc, b"\x11\x22\x33\x44"),
                   (0x90000, b"\xa5" * 4096),
                   (0x91000, b"\x55\x66\x77\x88")]
        states = {}
        for opcode in (0x02, 0x20):
            with self.subTest(opcode=opcode):
                guest = NORGuest()
                guest.command(0x06)
                guest.command(opcode, 0x907fe, b"\x04" if opcode == 0x02 else b"")
                guest.status(0)
                guest.wait_idle()
                guest.status(1)
                guest.spi_read(0x907fe, [2])
                guest.restore_xip()
                for address, slot in ((0x8fffc, 3), (0x90000, 4),
                                      (0x90ffc, 5), (0x91000, 6), (0x907fc, 7)):
                    guest.xip_read(address, slot)
                state = self.state(guest, patches)
                states[opcode] = state
                erased = opcode == 0x20
                self.assertEqual(state["inspection"][:8],
                                 [3, 0, 0xff if erased else 4, 0x44332211,
                                  0xffffffff if erased else 0xa5a5a5a5,
                                  0xffffffff if erased else 0xa5a5a5a5,
                                  0x88776655, 0xffffffff if erased else 0xa504a5a5])
                duration = 8_000_000 if erased else 2_000_000
                self.assertGreaterEqual(state["virtual_ns"], duration)
                self.assertLess(state["virtual_ns"], duration + 100_000)
        difference = states[0x20]["virtual_ns"] - states[0x02]["virtual_ns"]
        self.assertGreater(difference, 5_980_000)
        self.assertLess(difference, 6_020_000)


if __name__ == "__main__":
    unittest.main()
