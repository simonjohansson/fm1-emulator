# SPDX-License-Identifier: GPL-2.0-or-later
"""Register-repeat memory initialization and explicit qualification limits."""

from pathlib import Path
import tempfile
import unittest

from support import INSPECTION, ROOT, Guest, guest_state, run_guest


class RepeatTests(unittest.TestCase):
    def test_immediate_repeat_accepts_stock_postincrement_word_store(self):
        guest = Guest()
        guest.literal(0, INSPECTION)
        guest.literal(11, 0)
        guest.emit(0x9d10)         # REP four-byte body, 30 iterations.
        guest.emit(0xecd8, 0xb005) # [r0++=4] = r11.
        state = guest_state(self.directory, guest)
        self.assertEqual(state["inspection"], [0] * 12)
        self.assertEqual(state["registers"][0], INSPECTION + 120)
        self.assertEqual(state["instructions"], guest.instructions + 29)

    def setUp(self):
        cache = ROOT / ".cache" / "tests"
        cache.mkdir(parents=True, exist_ok=True)
        temporary = tempfile.TemporaryDirectory(dir=cache)
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)

    def test_counter_decrements_after_each_store_and_preserves_flags(self):
        for count in (0, 1, 3):
            with self.subTest(count=count):
                guest = Guest()
                guest.literal(0, 0x89abcde5)
                guest.emit(0xe064, 0x0580)  # PSR = r0.
                guest.literal(2, count)
                guest.literal(3, INSPECTION if count else 0xffffffff)
                start = guest.pc
                guest.emit(0x0302)         # REP two bytes, r2 counter.
                guest.emit(0x05b2)         # [r3++=4] = r2.
                guest.branch_zero(2, start, nonzero=True)
                guest.literal(4, 0x12345678)  # Executes once after repeat ends.
                state = guest_state(self.directory, guest)
                self.assertEqual(state["registers"][2], 0)
                self.assertEqual(state["registers"][3],
                                 INSPECTION + 4 * count if count else 0xffffffff)
                self.assertEqual(state["registers"][4], 0x12345678)
                self.assertEqual(state["specials"][5], 0x89abcde5)
                self.assertEqual(state["inspection"][:count], list(range(count, 0, -1)))
                self.assertEqual(state["instructions"], guest.instructions + count - 1)

    def test_repeated_two_instruction_copy(self):
        guest = Guest()
        values = [0x01234567, 0x89abcdef, 0xfedcba98]
        for index, value in enumerate(values):
            guest.write(INSPECTION + index * 4, value)
        guest.literal(1, INSPECTION)
        guest.literal(4, INSPECTION + 16)
        guest.literal(2, len(values))
        guest.emit(0x0312)         # REP four bytes, r2 counter.
        guest.emit(0x0513)         # r3 = [r1++=4].
        guest.emit(0x05c3)         # [r4++=4] = r3.
        state = guest_state(self.directory, guest)
        self.assertEqual(state["inspection"][4:7], values)
        self.assertEqual(state["registers"][1], INSPECTION + 12)
        self.assertEqual(state["registers"][4], INSPECTION + 28)
        self.assertEqual(state["registers"][2], 0)
        self.assertEqual(state["instructions"], guest.instructions + 4)

    def test_high_register_counter_with_four_byte_body(self):
        guest = Guest()
        guest.literal(10, 3)
        guest.literal(3, 0)
        guest.emit(0x031a)         # REP four bytes, r10 counter.
        guest.emit(0xe0e3, 0x3001)  # r3 = r3 + packed 1.
        state = guest_state(self.directory, guest)
        self.assertEqual(state["registers"][10], 0)
        self.assertEqual(state["registers"][3], 3)
        self.assertEqual(state["instructions"], guest.instructions + 2)

    def test_immediate_count_leaves_registers_alone(self):
        # Stock FM-1 firmware: 8a00 is "rep 2 11 {"; 9310 is "rep 4 20 {".
        for op, count, body in ((0x8a00, 11, (0x05b2,)), (0x9310, 20, (0xe0e3, 0x3001))):
            with self.subTest(op=hex(op)):
                guest = Guest()
                guest.literal(2, 7)
                guest.literal(3, INSPECTION if len(body) == 1 else 0)
                guest.emit(op)
                guest.emit(*body)   # [r3++=4] = r2, or r3 = r3 + packed 1.
                state = guest_state(self.directory, guest)
                self.assertEqual(state["registers"][2], 7)
                if len(body) == 1:
                    self.assertEqual(state["registers"][3], INSPECTION + 4 * count)
                    self.assertEqual(state["inspection"][:count], [7] * count)
                else:
                    self.assertEqual(state["registers"][3], count)
                self.assertEqual(state["instructions"], guest.instructions + count - 1)

    def test_unqualified_bodies_fault_at_repeat_before_effects(self):
        bodies = [(0x0302,), (0x8000,), (0xe0e3, 0x3001)]
        for body in bodies:
            with self.subTest(body=body):
                guest = Guest()
                guest.literal(2, 3)
                fault_pc = guest.pc
                guest.emit(0x0302)  # Last case ends halfway through its body.
                guest.emit(*body)
                result = run_guest(self.directory, guest)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("unsupported instruction 0x0302", result.stderr)
                self.assertIn(f"at PC 0x{fault_pc:08x} after 1 instructions", result.stderr)

    def test_body_can_overwrite_counter_before_implicit_writeback(self):
        guest = Guest()
        guest.literal(2, 3)
        guest.literal(3, INSPECTION)
        guest.emit(0x0312)
        guest.emit(0x2142)  # r2 = 1, distinct from the snapshotted count.
        guest.emit(0x05b2)  # [r3++=4] = r2.
        guest.store(2, 3)
        state = guest_state(self.directory, guest)
        self.assertEqual(state["inspection"][:4], [1, 1, 1, 0])
        self.assertEqual(state["registers"][2], 0)
        self.assertEqual(state["instructions"], guest.instructions + 4)

    def test_zero_count_skips_unqualified_body(self):
        guest = Guest()
        guest.literal(2, 0)
        guest.emit(0x0302)
        guest.emit(0x0023)  # Unsupported instruction must not execute.
        guest.literal(4, 0x1234)
        state = guest_state(self.directory, guest)
        self.assertEqual(state["registers"][4], 0x1234)
        self.assertEqual(state["instructions"], guest.instructions - 1)

    def test_packed_body_reads_incoming_load_destination(self):
        guest = Guest()
        guest.write(INSPECTION, 0x00030201)
        guest.literal(1, INSPECTION)
        guest.literal(4, 0x55)
        guest.literal(5, 3)
        guest.emit(0x0315)
        guest.emit(0xd643, 0x0714)  # r3 = incoming r4 # r4 = b[r1++].
        state = guest_state(self.directory, guest)
        self.assertEqual(state["registers"][3:6], [2, 3, 0])
        self.assertEqual(state["registers"][1], INSPECTION + 3)
        self.assertEqual(state["instructions"], guest.instructions + 2)


if __name__ == "__main__":
    unittest.main()
