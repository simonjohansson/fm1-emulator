# SPDX-License-Identifier: GPL-2.0-or-later
"""Guest-configured bus guards: stack window, write windows, PC windows, XIP.

Each guest runs under the application profile with a state directory, so a
fault leaves the reason, PC, instruction count, guard registers and SRAM at
the moment the guard fired. Faults are reported at the instruction that
caused them.
"""
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

from support import COMMAND, ENTRY, ROOT, Guest, environment

SRAM = 0x01c00000
DEBUG = 0x01eee240        # unlock key, then write windows and PC windows
WRITE_HIGH = 0x01eee280
WRITE_LOW = 0x01eee2c0
WRITE_ENABLE = 0x01eee348
PC_WINDOWS = 0x01eee380   # per window: high, then low
EMU = 0x01eef0d0          # control, message, then IRQ window, normal window
TIMER5 = 0x10900
IRQ_TIMER5 = 0x01eef11c
VECTOR_TIMER5 = 0x01c7fe00 + 63 * 4
SP, SSP, ICFG = 14, 13, 11
STUB_OFFSET = 0x2000
SFC, IOMAP = 0x40200, 0x5101c
SRAM_CODE = 0x01c10000
BODY_OFFSET = 0x1000

STACK_FAULT = "guest stack pointer lies outside its configured guard window"
WRITE_FAULT = "CPU write intersects an enabled guest guard window"
PC_FAULT = "guest PC lies outside both configured guard windows"
XIP_FAULT = "XIP access while SFC is disabled or unrouted"


class GuardGuest(Guest):
    def unlock(self):
        self.write(DEBUG, 0xe7)

    def write_window(self, index, low, high, enable_mask):
        self.write(WRITE_LOW + index * 4, low)
        self.write(WRITE_HIGH + index * 4, high)
        self.write(WRITE_ENABLE, enable_mask)

    def pc_window(self, index, low, high):
        self.write(PC_WINDOWS + index * 8 + 4, low)
        self.write(PC_WINDOWS + index * 8, high)

    def stack_window(self, irq, low, high):
        base = EMU + (8 if irq else 16)
        self.write(base + 4, low)
        self.write(base, high)

    def stack_guard(self, on=True):
        return self.write(EMU, 8 if on else 0)

    def store_byte(self, reg, base, offset=0):
        self.emit(0x4088 | reg | (base << 4) | ((offset & 31) << 8))

    def call_register(self, reg):
        self.emit(0x00c0 | reg)

    def move_special(self, special, reg):
        pc = self.pc
        self.emit(0xe064, (reg << 12) | (special << 8) | 0x80)
        return pc


class GuardTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="fm1-guards-")
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)

    def run_image(self, image, stop_pc):
        (self.directory / "guest.bin").write_bytes(image)
        state_dir = self.directory / "state"
        state_dir.mkdir(exist_ok=True)
        for old in state_dir.iterdir():
            old.unlink()
        result = subprocess.run(
            [*COMMAND, "-kernel", str(self.directory / "guest.bin"), "-append", "application"],
            cwd=ROOT, env=environment(FM1_POC_STATE_DIR=str(state_dir),
                                      FM1_POC_STOP_PC=hex(stop_pc),
                                      FM1_POC_MAX_INSTRUCTIONS="100000"),
            capture_output=True, text=True, timeout=30)
        state = json.loads((state_dir / "state.json").read_text())
        sram = (state_dir / "state.sram").read_bytes()
        return result, state, sram

    def run_guest(self, guest, stubs=()):
        image = bytearray(guest.bytes() + bytes(16))
        for offset, stub in stubs:
            code = stub.bytes()
            if len(image) < offset + len(code) + 16:
                image.extend(b"\xff" * (offset + len(code) + 16 - len(image)))
            image[offset:offset + len(code)] = code
        return self.run_image(bytes(image), guest.pc)

    def assert_passes(self, result, state):
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(state["reason"], "checkpoint reached")

    def assert_fault(self, result, state, reason, pc, instructions):
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(f"{reason} at PC 0x{pc:08x} after {instructions} instructions",
                      result.stderr)
        self.assertEqual((state["reason"], state["pc"], state["instructions"]),
                         (reason, pc, instructions))

    @staticmethod
    def word(sram, address):
        return int.from_bytes(sram[address - SRAM:address - SRAM + 4], "little")

    # ---- write windows -------------------------------------------------

    LOW, HIGH = 0x01c09000, 0x01c090ff

    def window_guest(self, enable_mask=1, low=LOW, high=HIGH):
        guest = GuardGuest()
        guest.unlock()
        guest.write_window(0, low, high, enable_mask)
        guest.literal(2, 0x5a5a5a5a)
        return guest

    def test_store_into_write_window_faults_before_the_store(self):
        for address, byte in ((self.LOW, False), (self.HIGH - 3, False), (self.HIGH, True)):
            with self.subTest(address=hex(address), byte=byte):
                guest = self.window_guest()
                guest.literal(3, address)
                pc = guest.pc
                if byte:
                    guest.store_byte(2, 3)
                else:
                    guest.store(2, 3)
                result, state, sram = self.run_guest(guest)
                self.assert_fault(result, state, WRITE_FAULT, pc, guest.instructions - 1)
                self.assertEqual(sram[address - SRAM], 0)
                self.assertEqual(state["guards"]["debug_message"] & (1 << 13), 1 << 13)
                self.assertEqual(state["last_access"],
                                 {"address": address, "size": 1 if byte else 4, "flags": 1})

    def test_stores_beside_and_loads_inside_a_write_window_pass(self):
        guest = self.window_guest()
        for address in (self.LOW - 4, self.HIGH + 1):
            guest.literal(3, address)
            guest.store(2, 3)
        guest.literal(3, self.LOW - 1)
        guest.store_byte(2, 3)
        guest.literal(3, self.LOW)
        guest.load(4, 3)
        result, state, sram = self.run_guest(guest)
        self.assert_passes(result, state)
        self.assertEqual(self.word(sram, self.LOW - 4), 0x5a5a5a5a)
        self.assertEqual(self.word(sram, self.HIGH + 1), 0x5a5a5a5a)
        self.assertEqual(sram[self.LOW - 1 - SRAM], 0x5a)

    def test_disabled_and_reversed_write_windows_are_ignored(self):
        for mask, low, high in ((0, self.LOW, self.HIGH), (1, self.HIGH, self.LOW)):
            with self.subTest(mask=mask, low=hex(low)):
                guest = self.window_guest(mask, low, high)
                guest.literal(3, self.LOW + 0x10)
                guest.store(2, 3)
                result, state, sram = self.run_guest(guest)
                self.assert_passes(result, state)
                self.assertEqual(self.word(sram, self.LOW + 0x10), 0x5a5a5a5a)

    def test_push_into_write_window_faults(self):
        guest = self.window_guest()
        guest.literal(SP, self.HIGH + 1, special=True)
        pc = guest.pc
        guest.emit(0x0410)  # [--sp] = rets
        result, state, sram = self.run_guest(guest)
        self.assert_fault(result, state, WRITE_FAULT, pc, guest.instructions - 1)
        self.assertEqual(self.word(sram, self.HIGH - 3), 0)

    # ---- stack guard ---------------------------------------------------

    S_LOW, S_HIGH = 0x01c70000, 0x01c70100

    def stack_guest(self, sp):
        guest = GuardGuest()
        guest.stack_window(False, self.S_LOW, self.S_HIGH)
        guest.literal(SP, sp, special=True)
        guest.stack_guard()
        return guest

    def assert_stack_fault(self, result, state, pc, instructions):
        self.assert_fault(result, state, STACK_FAULT, pc, instructions)
        self.assertEqual(state["guards"]["emu_message"] & 8, 8)

    def test_push_below_stack_window_faults_before_the_store(self):
        guest = self.stack_guest(self.S_LOW)
        guest.literal(4, 0x11223344)
        guest.move_special(3, 4)  # rets = r4
        pc = guest.pc
        guest.emit(0x0410)
        result, state, sram = self.run_guest(guest)
        self.assert_stack_fault(result, state, pc, guest.instructions - 1)
        self.assertEqual(self.word(sram, self.S_LOW - 4), 0)

    def test_stack_adjustments_beyond_the_window_fault(self):
        forms = (
            ("pop", self.S_HIGH, (0x0488,)),           # rets = [sp++]
            ("range pop", self.S_HIGH - 4, (0x0445,)),  # r4, r5 = [sp++]
            ("sp += -128", self.S_LOW + 8, (0x80e2,)),
            ("sp += -128 long", self.S_LOW + 8, (0xe8f0, 0x1f80)),
        )
        for name, sp, words in forms:
            with self.subTest(form=name):
                guest = self.stack_guest(sp)
                pc = guest.pc
                guest.emit(*words)
                result, state, _ = self.run_guest(guest)
                self.assert_stack_fault(result, state, pc, guest.instructions - 1)

    def test_writing_sp_outside_the_window_faults_at_the_write(self):
        guest = self.stack_guest(self.S_LOW + 16)
        pc = guest.pc
        guest.literal(SP, self.S_HIGH + 4, special=True)
        guest.literal(5, 0)
        result, state, _ = self.run_guest(guest)
        self.assert_stack_fault(result, state, pc, guest.instructions - 2)

        guest = self.stack_guest(self.S_LOW + 16)
        guest.literal(4, self.S_LOW - 4)
        pc = guest.move_special(SP, 4)
        guest.literal(5, 0)
        result, state, _ = self.run_guest(guest)
        self.assert_stack_fault(result, state, pc, guest.instructions - 2)

    def test_enabling_the_stack_guard_outside_the_window_faults_at_the_enable(self):
        guest = GuardGuest()
        guest.stack_window(False, self.S_LOW, self.S_HIGH)
        guest.literal(SP, self.S_HIGH + 8, special=True)
        pc = guest.stack_guard()
        guest.literal(5, 0)
        result, state, _ = self.run_guest(guest)
        self.assert_stack_fault(result, state, pc, guest.instructions - 2)

    def test_stack_use_inside_the_window_passes(self):
        guest = self.stack_guest(self.S_LOW + 8)
        guest.literal(4, 0x11223344)
        guest.move_special(3, 4)
        guest.emit(0x0410, 0x0410)
        guest.emit(0x0488, 0x0488)
        guest.emit(0x0465)  # [--sp] = r5, r4
        guest.emit(0x0445)
        result, state, sram = self.run_guest(guest)
        self.assert_passes(result, state)
        self.assertEqual(self.word(sram, self.S_LOW), 0x11223344)
        self.assertEqual(state["specials"][SP], self.S_LOW + 8)

    def test_interrupt_entry_checks_the_interrupt_stack_window(self):
        for ssp, passes in ((self.S_LOW + 0x40, True), (self.S_HIGH + 0x40, False)):
            with self.subTest(passes=passes):
                guest = GuardGuest()
                guest.stack_window(False, self.S_LOW, self.S_HIGH)
                guest.stack_window(True, self.S_LOW, self.S_HIGH)
                guest.literal(SP, self.S_LOW + 0x80, special=True)
                guest.literal(SSP, ssp, special=True)
                guest.stack_guard()
                handler = ENTRY + STUB_OFFSET
                guest.write(VECTOR_TIMER5, handler)
                guest.write(IRQ_TIMER5, 0x30000000)  # enabled, level 1
                guest.write(TIMER5 + 8, 100)
                guest.write(TIMER5, 0x09)
                guest.literal(0, 0x300)
                guest.move_special(ICFG, 0)
                wait = guest.pc
                guest.branch_zero(6, wait)  # r6 == 0 until the handler runs
                stub = Guest(handler)
                stub.literal(6, 1)
                stub.branch_zero(7, stub.pc)  # r7 == 0: stay in the handler
                result, state, _ = self.run_guest(guest, [(STUB_OFFSET, stub)])
                if passes:
                    self.assertEqual((state["reason"], state["pc"]),
                                     ("instruction limit reached", handler + 6))
                    self.assertTrue(state["in_irq"])
                else:
                    self.assertEqual(state["reason"], STACK_FAULT)
                    self.assertEqual(state["pc"], handler)
                    self.assertTrue(state["in_irq"])

    # ---- PC windows ----------------------------------------------------

    def pc_guest(self):
        guest = GuardGuest()
        guest.literal(1, ENTRY + STUB_OFFSET)
        guest.literal(6, 0)
        return guest

    def stub(self):
        stub = Guest(ENTRY + STUB_OFFSET)
        stub.add(6, 1)
        stub.emit(0x0080)  # rts
        return stub

    def test_execution_outside_the_pc_windows_faults(self):
        guest = self.pc_guest()
        guest.unlock()
        guest.pc_window(0, ENTRY, ENTRY + 0x0fff)
        guest.literal(1, ENTRY + STUB_OFFSET)  # (window writes use r0/r1)
        guest.call_register(1)
        result, state, _ = self.run_guest(guest, [(STUB_OFFSET, self.stub())])
        self.assert_fault(result, state, PC_FAULT, ENTRY + STUB_OFFSET, guest.instructions)
        self.assertEqual(state["guards"]["debug_message"] & (1 << 12), 1 << 12)

    def test_pc_windows_apply_to_code_that_already_ran(self):
        guest = self.pc_guest()
        guest.call_register(1)             # the stub runs once, unguarded
        guest.unlock()
        guest.pc_window(0, ENTRY, ENTRY + 0x0fff)
        guest.literal(1, ENTRY + STUB_OFFSET)
        guest.call_register(1)
        result, state, _ = self.run_guest(guest, [(STUB_OFFSET, self.stub())])
        self.assert_fault(result, state, PC_FAULT, ENTRY + STUB_OFFSET, guest.instructions + 2)

        guest = self.pc_guest()
        guest.call_register(1)
        guest.unlock()
        guest.pc_window(0, ENTRY, ENTRY + 0x0fff)
        guest.pc_window(1, ENTRY + STUB_OFFSET, ENTRY + STUB_OFFSET + 0xff)
        guest.literal(1, ENTRY + STUB_OFFSET)
        guest.call_register(1)
        result, state, _ = self.run_guest(guest, [(STUB_OFFSET, self.stub())])
        self.assert_passes(result, state)
        self.assertEqual(state["registers"][6], 2)

    def test_an_instruction_must_lie_entirely_inside_a_pc_window(self):
        guest = GuardGuest()
        guest.unlock()
        pc = guest.pc + 28     # after the two window writes below (14 halfwords)
        guest.pc_window(0, ENTRY, pc + 3)
        self.assertEqual(guest.pc, pc)
        guest.literal(5, 0)    # six bytes: ends beyond the window
        result, state, _ = self.run_guest(guest)
        self.assert_fault(result, state, PC_FAULT, pc, guest.instructions - 1)

    # ---- XIP -----------------------------------------------------------

    def xip_image(self, body):
        """Run the stub from XIP once, copy body to SRAM and enter it."""
        payload = body.bytes()
        payload += bytes((-len(payload)) % 4)
        boot = Guest()
        boot.literal(1, ENTRY + STUB_OFFSET)
        boot.literal(6, 0)
        boot.emit(0x00c1)                   # call r1: the XIP stub is translated
        boot.literal(2, ENTRY + BODY_OFFSET)
        boot.literal(3, SRAM_CODE)
        boot.literal(4, len(payload) // 4)
        copy = boot.pc
        boot.load(0, 2)
        boot.store(0, 3)
        boot.add(2, 4)
        boot.add(3, 4)
        boot.add(4, -1)
        boot.branch_zero(4, copy, nonzero=True)
        boot.literal(1, SRAM_CODE)
        boot.emit(0xe064, 0x1380)           # rets = r1
        boot.emit(0x0080)
        image = bytearray(b"\xff" * (STUB_OFFSET + 64))
        image[:len(boot.bytes())] = boot.bytes()
        image[BODY_OFFSET:BODY_OFFSET + len(payload)] = payload
        stub = self.stub().bytes()
        image[STUB_OFFSET:STUB_OFFSET + len(stub)] = stub
        return bytes(image)

    def test_fetching_translated_xip_code_while_sfc_is_disabled_faults(self):
        body = GuardGuest(SRAM_CODE)
        body.write(SFC, 0)
        body.write(IOMAP, 0)
        body.literal(1, ENTRY + STUB_OFFSET)
        body.call_register(1)
        result, state, _ = self.run_image(self.xip_image(body), body.pc)
        self.assertEqual((state["reason"], state["pc"]), (XIP_FAULT, ENTRY + STUB_OFFSET))
        self.assertIn(XIP_FAULT, result.stderr)

    def test_xip_code_runs_again_after_sfc_is_restored(self):
        body = GuardGuest(SRAM_CODE)
        body.write(SFC, 0)
        body.write(IOMAP, 0)
        body.write(IOMAP, 0x20)
        body.write(SFC, 1)
        body.literal(1, ENTRY + STUB_OFFSET)
        body.call_register(1)
        result, state, _ = self.run_image(self.xip_image(body), body.pc)
        self.assert_passes(result, state)
        self.assertEqual(state["registers"][6], 2)

    def test_xip_reads_beyond_mapped_storage_fault(self):
        guest = GuardGuest()
        guest.literal(1, 0x020fc000)
        pc = guest.pc
        guest.load(0, 1)
        result, state, _ = self.run_guest(guest)
        self.assert_fault(result, state, "XIP access exceeds mapped NOR storage",
                          pc, guest.instructions - 1)


if __name__ == "__main__":
    unittest.main()
