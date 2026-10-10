# SPDX-License-Identifier: GPL-2.0-or-later
"""Two-core startup, shared locking and independent stack guards.

These guests exercise the experimental -smp 2 machine contract. The SRAM
entry vector and RTI handoff were exercised on hardware. The complete
boot-ROM reset sequence and retained registers remain unmodeled.
"""
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

from support import COMMAND, ENTRY, INSPECTION, Guest, environment

CORE1 = ENTRY + 0x1000
C1_CON = 0x01eee004
VECTOR = 0x01c7fff8
EMU0, EMU1 = 0x01eef0d0, 0x01eef2d0


def wait_word(guest, address):
    guest.literal(1, address)
    loop = guest.pc
    guest.load(0, 1)
    guest.branch_zero(0, loop)


def park(guest):
    guest.literal(7, 0)
    guest.branch_zero(7, guest.pc)


def secondary():
    guest = Guest(CORE1)
    guest.literal(12, 0x01c11000, special=True)  # USP
    guest.literal(14, 0x01c12000, special=True)  # startup SSP
    guest.literal(0, guest.pc + 8, special=True)  # RETI -> after RTI
    guest.emit(0x0081)  # RTI
    return guest


class DualCoreTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="fm1-dual-core-")
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)

    def run_pair(self, primary, peer, extra=()):
        bodies = [(0x1000, peer), *extra]
        image = bytearray(b"\xff" * (max(offset + len(body.bytes())
                                        for offset, body in bodies) + 16))
        image[:len(primary.bytes())] = primary.bytes()
        for offset, body in bodies:
            image[offset:offset + len(body.bytes())] = body.bytes()
        path = self.directory / "guest.bin"
        path.write_bytes(image)
        state_dir = self.directory / "state"
        state_dir.mkdir()
        result = subprocess.run(
            [*COMMAND, "-smp", "2", "-kernel", str(path), "-append", "application"],
            env=environment(FM1_POC_STATE_DIR=str(state_dir),
                            FM1_POC_STOP_PC=hex(primary.pc),
                            FM1_POC_MAX_INSTRUCTIONS="40000000"),  # above one 100 ms two-core slice
            capture_output=True, text=True, timeout=30)
        state = json.loads((state_dir / "state.json").read_text())
        ram = (state_dir / "state.sram").read_bytes()
        words = [int.from_bytes(ram[0x8000 + i:0x8004 + i], "little")
                 for i in range(0, 32, 4)]
        return result, state, words

    def start(self, primary):
        primary.write(VECTOR, CORE1)
        primary.write(C1_CON, 0x01000008)

    def test_release_executes_secondary_code_with_its_own_registers(self):
        primary = Guest()
        primary.write(VECTOR, CORE1)
        primary.write(C1_CON, 0x0100000a)  # enabled but still reset
        # Spend a whole scheduling slice with reset asserted. Core 1 must
        # not run, even though its entry is valid and clocks are enabled.
        primary.literal(2, 800000)
        loop = primary.pc
        primary.add(2, -1)
        primary.branch_zero(2, loop, nonzero=True)
        primary.literal(1, INSPECTION)
        primary.load(5, 1)
        primary.write(C1_CON, 0x01000008)
        wait_word(primary, INSPECTION)
        peer = secondary()
        peer.literal(1, INSPECTION)
        for offset, special in ((4, 14), (8, 13)):
            peer.emit(0xe064, (2 << 12) | (special << 8))
            peer.store(2, 1, offset)
        peer.emit(0xe064, 0x2600)  # r2 = cnum
        peer.store(2, 1)
        park(peer)
        result, state, words = self.run_pair(primary, peer)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(state["reason"], "checkpoint reached")
        self.assertEqual(state["registers"][5], 0)
        self.assertEqual(state["specials"][6], 0)
        self.assertEqual(words[:3], [1, 0x01c11000, 0x01c12000])

    def test_lock_blocks_peer_until_owner_releases(self):
        primary = Guest()
        primary.emit(0x0041)  # LOCKSET
        self.start(primary)
        wait_word(primary, INSPECTION)
        primary.literal(1, INSPECTION + 4)
        primary.load(5, 1)  # peer has arrived, but cannot write yet
        primary.emit(0x0040)  # LOCKCLR
        wait_word(primary, INSPECTION + 4)
        peer = secondary()
        peer.write(INSPECTION, 1)
        peer.emit(0x0041)
        peer.write(INSPECTION + 4, 0x12345678)
        peer.emit(0x0040)
        park(peer)
        result, state, words = self.run_pair(primary, peer)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(state["registers"][5], 0)
        self.assertEqual(words[:2], [1, 0x12345678])

    def test_pause_stops_peer_until_resume_without_resetting_registers(self):
        primary = Guest()
        primary.write(VECTOR, CORE1)
        primary.write(C1_CON, 0x0100000a)  # resume pulse while reset is asserted
        primary.literal(1, C1_CON)
        primary.load(5, 1)
        primary.write(C1_CON, 0x01000000)  # pulse is remembered across reset release
        wait_word(primary, INSPECTION)
        primary.write(C1_CON, 0x01000004)
        primary.literal(1, C1_CON)
        primary.load(6, 1)
        primary.literal(1, INSPECTION)
        primary.load(4, 1)
        primary.literal(2, 800000)
        loop = primary.pc
        primary.add(2, -1)
        primary.branch_zero(2, loop, nonzero=True)
        primary.literal(1, INSPECTION)
        primary.load(3, 1)
        primary.write(C1_CON, 0x01000019)  # read/modify/write of paused status + resume
        primary.literal(2, 800000)
        loop = primary.pc
        primary.add(2, -1)
        primary.branch_zero(2, loop, nonzero=True)
        primary.literal(1, INSPECTION)
        primary.load(2, 1)
        peer = secondary()
        peer.literal(1, INSPECTION)
        peer.literal(0, 0)
        loop = peer.pc
        peer.add(0, 1)
        peer.store(0, 1)
        peer.literal(7, 0)
        peer.branch_zero(7, loop)
        result, state, _ = self.run_pair(primary, peer)
        self.assertEqual(result.returncode, 0, result.stderr)
        regs = state["registers"]
        self.assertEqual(regs[5], 0x01000002)
        self.assertEqual(regs[6], 0x01000011)
        self.assertEqual(regs[3], regs[4])
        self.assertGreater(regs[2], regs[4])

    def test_secondary_stack_guard_is_independent(self):
        primary = Guest()
        primary.literal(14, 0x01c08000, special=True)
        primary.write(EMU0 + 20, 0x01c08000)
        primary.write(EMU0 + 16, 0x01c09000)
        primary.write(EMU0, 8)
        self.start(primary)
        wait_word(primary, INSPECTION)
        peer = secondary()
        peer.write(EMU1 + 20, 0x01c11000)
        peer.write(EMU1 + 16, 0x01c12000)
        peer.write(EMU1, 8)
        fault_pc = peer.pc
        peer.literal(14, 0x01c13000, special=True)
        park(peer)
        result, state, _ = self.run_pair(primary, peer)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(state["reason"],
                         "guest stack pointer lies outside its configured guard window")
        self.assertEqual(state["pc"], fault_pc)
        self.assertEqual(state["specials"][6], 1)
        self.assertEqual(state["guards"]["stack_windows"][1], [0x01c11000, 0x01c12000])

    def test_core0_write_guard_does_not_enable_core1_guard(self):
        primary = Guest()
        primary.write(0x01eee240, 0xe7)
        primary.write(0x01eee2c0, INSPECTION + 4)
        primary.write(0x01eee280, INSPECTION + 7)
        primary.write(0x01eee348, 1)  # C0_WR_LIMIT_EN; C1 remains disabled
        self.start(primary)
        wait_word(primary, INSPECTION)
        peer = secondary()
        peer.write(INSPECTION + 4, 0x12345678)
        peer.write(INSPECTION, 1)
        park(peer)
        result, _, words = self.run_pair(primary, peer)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(words[:2], [1, 0x12345678])

    def test_secondary_tick_irq_uses_its_own_timer_and_irq_bank(self):
        primary = Guest()
        primary.write(0x01eef100, 0x3000)  # IRQ3 enabled on both cores
        self.start(primary)
        wait_word(primary, INSPECTION)
        primary.literal(1, 0x01eef100)
        primary.load(5, 1)
        peer = secondary()
        handler = Guest(CORE1 + 0x200)
        handler.emit(0x0460)  # preserve r0-r3 around the interrupt
        handler.literal(1, INSPECTION + 4)
        handler.emit(0xe064, 0x2600)  # IRQ must execute on core 1
        handler.store(2, 1)
        handler.literal(1, 0x01eef2ec)
        handler.literal(0, 0x40)  # acknowledge and stop only core-1 TTMR
        handler.emit(0x4098)  # b[r1] = r0
        handler.emit(0x0440)
        handler.emit(0x0081)
        peer.write(0x01c7fe00 + 3 * 4, handler.base)
        peer.write(0x01eef300, 0x5000)
        peer.write(0x01eef2f4, 3600)
        peer.literal(1, 0x01eef2ec)
        peer.literal(0, 1)
        peer.emit(0x4098)
        wait_word(peer, INSPECTION + 4)
        peer.literal(1, 0x01eef300)
        peer.load(2, 1)
        peer.literal(1, INSPECTION + 8)
        peer.store(2, 1)
        peer.write(INSPECTION, 1)
        park(peer)
        result, state, words = self.run_pair(primary, peer, [(0x1200, handler)])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(state["registers"][5], 0x3000)
        self.assertEqual(state["irq_entries"], 0)
        self.assertEqual(words[:3], [1, 1, 0x5000])

    def test_bank0_software_requests_route_and_acknowledge_on_both_cores(self):
        primary = Guest()
        peer = secondary()
        handler1 = Guest(CORE1 + 0x200)
        handler0 = Guest(CORE1 + 0x400)
        for handler, source, result_offset in ((handler1, 124, 4),
                                                (handler0, 125, 12)):
            handler.emit(0x0460)  # preserve r0-r3
            handler.write(0x01eef1a4, 1 << (source - 120))
            handler.literal(1, INSPECTION)
            handler.emit(0xe064, 0x2600)  # r2 = cnum
            handler.store(2, 1, result_offset)
            handler.emit(0xe064, 0x2b00)  # r2 = icfg
            handler.store(2, 1, 16 if source == 124 else 20)
            if source == 125:
                handler.literal(2, 7)
                handler.store(2, 1, 8)
            handler.emit(0x0440)
            handler.emit(0x0081)
        primary.write(0x01c7fe00 + 124 * 4, handler1.base)
        primary.write(0x01c7fe00 + 125 * 4, handler0.base)
        primary.write(0x01eef13c, 0x00f00000)  # only source 125 on core 0
        primary.literal(12, 0x01c13000, special=True)
        primary.literal(13, 0x01c14000, special=True)
        primary.literal(14, 0x01c13000, special=True)
        primary.literal(0, 0x300)
        primary.emit(0xe064, 0x0b80)  # ICFG = r0: enable IRQs in supervisor mode
        self.start(primary)
        wait_word(primary, INSPECTION)
        primary.write(0x01eef1a0, 16)
        primary.write(0x01eef3a4, 16)  # wrong bank must leave the request pending
        primary.literal(1, 0x01eef38c)
        primary.load(5, 1)
        primary.literal(1, 0x01eef18c)
        primary.load(6, 1)
        primary.write(INSPECTION + 32, 1)
        wait_word(primary, INSPECTION + 8)
        for address, offset in ((0x01eef18c, 24), (0x01eef38c, 28)):
            primary.literal(1, address)
            primary.load(2, 1)
            primary.literal(1, INSPECTION)
            primary.store(2, 1, offset)
        peer.emit(0x0060)  # CLI: inspect pending before delivery
        peer.write(0x01eef33c, 0x000f0000)  # only source 124 on core 1
        peer.write(INSPECTION, 1)
        wait_word(peer, INSPECTION + 32)
        peer.emit(0x0061)
        wait_word(peer, INSPECTION + 4)
        peer.write(0x01eef1a0, 32)  # peer also requests through bank 0
        park(peer)
        result, state, words = self.run_pair(
            primary, peer, [(0x1200, handler1), (0x1400, handler0)])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(state["irq_entries"], 1)
        self.assertEqual(state["registers"][5], 0x10000000)
        self.assertEqual(state["registers"][6], 0)  # source 124 disabled on core 0
        self.assertEqual(words[:4], [1, 1, 7, 0])
        self.assertEqual((words[4] >> 16) & 127, 124)
        self.assertEqual((words[5] >> 16) & 127, 125)
        self.assertEqual(words[6:], [0, 0])


if __name__ == "__main__":
    unittest.main()
