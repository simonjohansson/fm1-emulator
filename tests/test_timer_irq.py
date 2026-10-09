# SPDX-License-Identifier: GPL-2.0-or-later
"""TIMER4/5 share timer semantics and retain distinct interrupt sources."""
import json
from pathlib import Path
import struct
import subprocess
import tempfile
import unittest

from support import COMMAND, ENTRY, INSPECTION, ROOT, Guest, environment
from test_dual_core import park, secondary, wait_word

TIMER4, TIMER5 = 0x10800, 0x10900
IRQ_CONFIG = 0x01eef11c
IRQ_PENDING = 0x01eef184


def record(guest, address, slot):
    guest.literal(1, address)
    guest.load(3, 1)
    guest.literal(1, INSPECTION + slot * 4)
    guest.store(3, 1)


def wait_pending(guest, timer):
    guest.literal(2, timer)
    guest.literal(5, 0x8000)
    poll = guest.pc
    guest.load(3, 2)
    guest.emit(0x1634)
    guest.emit(0x19d4)
    guest.branch_zero(4, poll)


class TimerIRQTests(unittest.TestCase):
    def setUp(self):
        directory = ROOT / ".cache/tests"
        directory.mkdir(parents=True, exist_ok=True)
        temporary = tempfile.TemporaryDirectory(dir=directory)
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)

    def run_timer(self, guest, stubs=(), cores=1):
        image = bytearray(guest.bytes() + bytes(16))
        for stub in stubs:
            offset = stub.base - ENTRY
            self.assertGreaterEqual(offset, len(image))
            image.extend(b"\xff" * (offset - len(image)))
            image.extend(stub.bytes() + bytes(16))
        path = self.directory / "guest.bin"
        path.write_bytes(image)
        result = subprocess.run(
            [*COMMAND, "-smp", str(cores), "-kernel", str(path), "-append", "application"],
            cwd=ROOT, env=environment(FM1_POC_STOP_PC=hex(guest.pc),
                                      FM1_POC_MAX_INSTRUCTIONS="10000000",
                                      FM1_POC_STATE_DIR=str(self.directory)),
            capture_output=True, text=True, timeout=30)
        state = json.loads((self.directory / "state.json").read_text())
        ram = (self.directory / "state.sram").read_bytes()
        words = struct.unpack_from("<8I", ram, INSPECTION - 0x01c00000)
        return result, state, words

    def test_timer4_and_timer5_periodic_irq_ack_and_return_preserve_cadence(self):
        for timer, source in ((TIMER4, 62), (TIMER5, 63)):
            with self.subTest(timer=hex(timer), source=source):
                guest = Guest()
                handler = Guest(ENTRY + 0x2000)
                handler.emit(0x0460)  # Preserve r0-r3 around the interrupt.
                record(handler, IRQ_PENDING, 0)
                handler.add(6, 1)
                handler.literal(0, 0x4009)  # ACK while leaving OSC24M enabled.
                handler.emit(0x1663)  # r3 = r6.
                handler.add(3, -3)
                handler.branch_zero(3, handler.pc + 8, nonzero=True)
                handler.literal(0, 0x4008)  # Third IRQ: ACK and stop.
                handler.literal(1, timer)
                handler.store(0, 1)
                record(handler, IRQ_PENDING, 1)
                handler.emit(0x0440)
                handler.emit(0x0081)
                guest.literal(14, 0x01c13000, special=True)
                guest.literal(13, 0x01c14000, special=True)
                guest.write(0x01c7fe00 + source * 4, handler.base)
                guest.write(IRQ_CONFIG, 3 << ((source % 8) * 4))
                guest.write(timer + 8, 2400)  # 100 us on the existing OSC24M model.
                guest.literal(6, 0)
                guest.literal(0, 0x300)
                guest.emit(0xe064, 0x0b80)
                result, state, _ = self.run_timer(guest, (handler,))
                self.assertEqual(result.returncode, 0, result.stderr)
                start_ns = state["virtual_ns"]
                guest.write(timer, 9)
                poll = guest.pc
                guest.emit(0x1663)
                guest.add(3, -3)
                guest.branch_zero(3, poll, nonzero=True)
                result, state, words = self.run_timer(guest, (handler,))
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(state["reason"], "checkpoint reached")
                self.assertEqual(state["irq_entries"], 3)
                self.assertEqual(state["rti_count"], 3)
                self.assertEqual(state["last_irq_source"], source)
                self.assertFalse(state["in_irq"])
                self.assertEqual(state["registers"][6], 3)
                self.assertEqual(words[:2], (1 << (source - 32), 0))
                self.assertEqual(state["specials"][14], 0x01c13000)
                self.assertEqual(state["specials"][13], 0x01c14000)
                elapsed = state["virtual_ns"] - start_ns
                self.assertGreaterEqual(elapsed, 300000)
                self.assertLess(elapsed, 301000)

    def test_pending_bits_and_device_acknowledgments_are_independent(self):
        guest = Guest()
        guest.write(TIMER4 + 8, 2400)
        guest.write(TIMER5 + 8, 4800)
        guest.write(TIMER4, 9)
        guest.write(TIMER5, 9)
        wait_pending(guest, TIMER5)
        record(guest, IRQ_PENDING, 0)
        guest.write(TIMER4, 0x4008)
        record(guest, IRQ_PENDING, 1)
        record(guest, TIMER4, 2)
        record(guest, TIMER5, 3)
        guest.write(TIMER5, 0x4008)
        record(guest, IRQ_PENDING, 4)
        result, state, words = self.run_timer(guest)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(words[:5], (0xc0000000, 0x80000000, 8, 0x8009, 0))
        self.assertEqual(state["irq_entries"], 0)

    def test_stopping_timer4_preserves_pending_until_device_ack(self):
        guest = Guest()
        guest.write(TIMER4 + 8, 2400)
        guest.write(TIMER4, 9)
        wait_pending(guest, TIMER4)
        guest.write(TIMER4, 8)
        record(guest, IRQ_PENDING, 0)
        record(guest, TIMER4, 1)
        fault_pc = guest.write(0x01eef1a4, 255)
        result, state, words = self.run_timer(guest)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(state["reason"], "IRQ clear requires a device acknowledgment")
        self.assertEqual(state["pc"], fault_pc)
        self.assertEqual(words[:2], (0x40000000, 0x8008))

    def test_timer4_shared_irq_uses_core1_configuration_and_vector(self):
        primary = Guest()
        peer = secondary()
        handler = Guest(ENTRY + 0x2000)
        handler.emit(0x0460)
        handler.literal(1, INSPECTION + 8)
        handler.emit(0xe064, 0x2600)  # r2 = CNUM.
        handler.store(2, 1)
        handler.emit(0xe064, 0x2b00)  # r2 = ICFG, including delivered source.
        handler.store(2, 1, 4)
        handler.write(TIMER4, 0x4008)
        handler.add(6, 1)
        handler.emit(0x0440)
        handler.emit(0x0081)
        primary.literal(14, 0x01c13000, special=True)
        primary.literal(13, 0x01c14000, special=True)
        primary.write(0x01c7fe00 + 62 * 4, handler.base)
        primary.write(IRQ_CONFIG, 0)  # Core0 remains enabled globally, but source62 masked.
        primary.literal(0, 0x300)
        primary.emit(0xe064, 0x0b80)
        primary.write(0x01c7fff8, peer.base)
        primary.write(0x01eee004, 0x01000008)
        wait_word(primary, INSPECTION + 4)
        peer.literal(6, 0)
        peer.write(0x01eef31c, 0x0b000000)  # Source62, priority5, on core1 only.
        peer.literal(0, 0x300)
        peer.emit(0xe064, 0x0b80)
        peer.write(TIMER4 + 8, 2400)
        peer.write(TIMER4, 9)
        poll = peer.pc
        peer.branch_zero(6, poll)
        peer.write(INSPECTION + 4, 1)  # Reached only after the ISR's RTI.
        park(peer)
        result, state, words = self.run_timer(primary, (peer, handler), cores=2)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(state["reason"], "checkpoint reached")
        self.assertEqual(state["irq_entries"], 0)
        self.assertFalse(state["in_irq"])
        self.assertFalse(state["peer"]["in_irq"])
        self.assertEqual(words[1:3], (1, 1))
        self.assertEqual((words[3] >> 16) & 127, 62)


if __name__ == "__main__":
    unittest.main()
