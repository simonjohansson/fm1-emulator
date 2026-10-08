# SPDX-License-Identifier: GPL-2.0-or-later
"""Instruction forms Felucca 1.0.3.1 and 1.1.5.1 execute; every encoding is the vendor assembler's
(JieLi clang 4.0.1 -target pi32v2, objdump) and each check reads back values and bases."""

from pathlib import Path
import tempfile
import unittest

from support import INSPECTION, QEMU, ROOT, Guest, guest_state

BASE = INSPECTION + 0x10                 # inside the reported inspection words
MARK = 0x0BADCAFE
MAX = 0xFFFFFFFF


class FeluccaFormTests(unittest.TestCase):
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

    def run_state(self, guest):
        return guest_state(self.directory, guest)

    def word(self, state, address):
        return state["inspection"][(address - INSPECTION) // 4]

    def read_back(self, guest, address, register=3):
        """Load a word anywhere in SRAM into a register after the instruction under test."""
        guest.literal(2, address)
        guest.load(register, 2)

    def test_byte_post_increment_signed_nine_bit_step(self):
        for h, x, step, store, signed in [
                (0xEED0, 0x30B1, 1, False, False), (0xEED0, 0x3FBF, 255, False, False),
                (0xEED1, 0x3FBF, -1, False, False), (0xEED1, 0x30B0, -256, False, False),
                (0xEED5, 0x3FBF, -1, False, True), (0xEED2, 0x30B1, 1, True, False),
                (0xEED3, 0x3FBF, -1, True, False), (0xEED3, 0x30B0, -256, True, False)]:
            with self.subTest(op=f"{h:04X} {x:04X}"):
                guest = Guest()
                guest.write(BASE, 0x55555581)
                guest.literal(11, BASE)
                guest.literal(3, 0x123456A7 if store else 0)
                guest.emit(h, x)
                state = self.run_state(guest)
                self.assertEqual(state["registers"][11], (BASE + step) & MAX)
                if store:
                    self.assertEqual(self.word(state, BASE), 0x555555A7)
                else:
                    self.assertEqual(state["registers"][3], 0xFFFFFF81 if signed else 0x81)

    def test_halfword_post_increment_signed_ten_bit_step(self):
        for h, x, step, store, signed in [
                (0xEDD0, 0x30B3, 2, True, False), (0xEDD1, 0x3FBF, 510, True, False),
                (0xEDD3, 0x3FBF, -2, True, False), (0xEDD2, 0x30B1, -512, True, False),
                (0xEDD3, 0x3FBE, -2, False, False), (0xEDD7, 0x3FBE, -2, False, True),
                (0xEDD5, 0x30B0, 256, False, True)]:
            with self.subTest(op=f"{h:04X} {x:04X}"):
                guest = Guest()
                guest.write(BASE, 0x55558001)
                guest.literal(11, BASE)
                guest.literal(3, 0x12348765 if store else 0)
                guest.emit(h, x)
                state = self.run_state(guest)
                self.assertEqual(state["registers"][11], (BASE + step) & MAX)
                if store:
                    self.assertEqual(self.word(state, BASE), 0x55558765)
                else:
                    self.assertEqual(state["registers"][3], 0xFFFF8001 if signed else 0x8001)

    def test_word_post_increment_signed_eleven_bit_step(self):
        for h, x, data, base, store, step in [
                (0xECD8, 0x30B4, 3, 11, False, 4), (0xECDB, 0x3FBC, 3, 11, False, 1020),
                (0xECDF, 0x3FBD, 3, 11, True, -4), (0xECDC, 0x30B0, 3, 11, False, -1024),
                (0xECDC, 0x30B1, 3, 11, True, -1024), (0xECDA, 0x5020, 5, 2, False, 512),
                (0xECDE, 0x0F9C, 0, 9, False, -260), (0xECDB, 0x70E5, 7, 14, True, 772),
                (0xECD8, 0xB005, 11, 0, True, 4)]:      # [r0++=4] = r11: Felucca 1.1.5 at boot
            with self.subTest(op=f"{h:04X} {x:04X}"):
                guest = Guest()
                guest.write(BASE, 0xFEEDBEEF)
                guest.literal(base, BASE)
                if store:
                    guest.literal(data, 0x5A5A1234)
                guest.emit(h, x)
                state = self.run_state(guest)
                self.assertEqual(state["registers"][base], (BASE + step) & MAX)
                if store:
                    self.assertEqual(self.word(state, BASE), 0x5A5A1234)
                else:
                    self.assertEqual(state["registers"][data], 0xFEEDBEEF)

    def test_sixteen_bit_post_increments_step_both_ways(self):
        # 0500 / 0600 / 0700: word, halfword, byte; 0x80 store; 0x08 a step back; base r6, data r0.
        for op, size, step, store in [
                (0x0760, 1, 1, False), (0x0768, 1, -1, False), (0x07E0, 1, 1, True), (0x07E8, 1, -1, True),
                (0x0660, 2, 2, False), (0x0668, 2, -2, False), (0x06E0, 2, 2, True), (0x06E8, 2, -2, True),
                (0x0560, 4, 4, False), (0x0568, 4, -4, False), (0x05E0, 4, 4, True), (0x05E8, 4, -4, True)]:
            with self.subTest(op=f"{op:04X}"):
                guest = Guest()
                guest.write(BASE, 0xA1B2C3D4)
                guest.literal(6, BASE)
                guest.literal(0, 0x11223344 if store else 0)
                guest.emit(op)
                state = self.run_state(guest)
                self.assertEqual(state["registers"][6], (BASE + step) & MAX)
                mask = (1 << (8 * size)) - 1
                if store:
                    self.assertEqual(self.word(state, BASE), (0xA1B2C3D4 & ~mask) | (0x11223344 & mask))
                else:
                    self.assertEqual(state["registers"][0], 0xA1B2C3D4 & mask)

    def test_halfword_at_signed_offset(self):
        for h, x, offset, store, signed in [
                (0xED57, 0x7FBC, -4, False, True), (0xED53, 0x7FBC, -4, False, False),
                (0xED53, 0x7FBD, -4, True, False), (0xED56, 0x70B0, -512, False, True),
                (0xED55, 0x70B0, 256, False, True), (0xED53, 0x70B1, -256, True, False)]:
            with self.subTest(op=f"{h:04X} {x:04X}"):
                address = BASE + 0x400 + offset
                guest = Guest()
                guest.write(address, 0x55558001)
                guest.literal(11, BASE + 0x400)
                guest.literal(7, 0x12348765 if store else 0)
                guest.emit(h, x)
                guest.literal(4, 0)
                guest.literal(2, address)
                guest.load(4, 2)
                state = self.run_state(guest)
                self.assertEqual(state["registers"][11], BASE + 0x400)
                if store:
                    self.assertEqual(state["registers"][4], 0x55558765)
                else:
                    self.assertEqual(state["registers"][7], 0xFFFF8001 if signed else 0x8001)

    def test_byte_pre_increment_and_negative_offsets(self):
        for h, x, data, base, offset, value in [
                (0xEE5C, 0x0B61, 0, 6, 177, 0xFFFFFF80), (0xEE5D, 0x0F6F, 0, 6, -1, 0xFFFFFF80),
                (0xEE59, 0x1F00, 1, 0, -16, 0x80)]:
            with self.subTest(op=f"{h:04X} {x:04X}"):
                address = BASE + 0x400 + offset
                guest = Guest()
                guest.write(address & ~3, 0x80 << (8 * (address & 3)))
                guest.literal(base, BASE + 0x400)
                guest.emit(h, x)
                state = self.run_state(guest)
                self.assertEqual(state["registers"][data], value)
                self.assertEqual(state["registers"][base], address)

    def test_doubleword_pre_and_post_increment(self):
        for words, base, start, address, after in [
                ((0xEC50, 0x2213), 1, INSPECTION, INSPECTION + 32, INSPECTION + 32),   # d[++r1=32] = r3_r2
                ((0xEC58, 0x2009), 0, BASE, BASE, BASE + 8),                           # d[r0++=8] = r3_r2
                ((0xEC57, 0x2A03), 0, BASE + 96, BASE, BASE)]:                         # d[++r0=-96] = r3_r2
            with self.subTest(op="%04X %04X" % words):
                guest = Guest()
                guest.literal(base, start)
                guest.literal(2, 0x11112222)
                guest.literal(3, 0x33334444)
                guest.emit(*words)
                state = self.run_state(guest)
                self.assertEqual([self.word(state, address), self.word(state, address + 4)], [0x11112222, 0x33334444])
                self.assertEqual(state["registers"][base], after)
        for words, base, pair, address, after in [
                ((0xEC50, 0x421A), 1, 4, BASE + 40, BASE + 40),     # r5_r4 = d[++r1=40]
                ((0xEC5F, 0x6F00), 0, 6, BASE, BASE - 16)]:         # r7_r6 = d[r0++=-16]
            with self.subTest(op="%04X %04X" % words):
                guest = Guest()
                guest.write(address, 0x55556666)
                guest.write(address + 4, 0x77778888)
                guest.literal(base, BASE)
                guest.emit(*words)
                state = self.run_state(guest)
                self.assertEqual(state["registers"][pair:pair + 2], [0x55556666, 0x77778888])
                self.assertEqual(state["registers"][base], after)

    def test_halfword_register_pre_increment(self):
        guest = Guest()                                       # h[++r2=r11] = r0 (Felucca gfx.c ramp)
        guest.write(BASE + 4, 0x55555555)
        guest.write(BASE + 8, 0x77777777)
        guest.literal(2, BASE)
        guest.literal(11, 6)
        guest.literal(0, 0x1234ABCD)
        guest.emit(0xEDDC, 0x0B21)
        state = self.run_state(guest)
        self.assertEqual(state["registers"][2], BASE + 6)
        self.assertEqual([self.word(state, BASE + 4), self.word(state, BASE + 8)], [0xABCD5555, 0x77777777])
        for x, value in [(0x3B20, 0x8001), (0x3B22, 0xFFFF8001)]:  # r3 = h[++r2=r11] (u) / (s)
            with self.subTest(x=f"{x:04X}"):
                guest = Guest()
                guest.write(BASE + 4, 0x00008001)
                guest.literal(2, BASE)
                guest.literal(11, 4)
                guest.emit(0xEDDC, x)
                state = self.run_state(guest)
                self.assertEqual(state["registers"][3], value)
                self.assertEqual(state["registers"][2], BASE + 4)

    def test_wide_divide(self):
        for x, dest, source, divisor, signed in [
                (0x1200, 0, 0, 2, True), (0x1020, 0, 2, 0, True), (0x3200, 2, 0, 2, True),
                (0xE0E0, 14, 14, 0, False), (0x0E40, 0, 4, 14, False)]:
            for dividend, by in [(-7, 2), (2**63 - 1, -3), (-(1 << 40) - 5, 1000), (5, -1)]:
                with self.subTest(op=f"E1F6 {x:04X}", dividend=dividend, by=by):
                    guest = Guest()
                    guest.literal(source, dividend & MAX)
                    guest.literal(source + 1, (dividend >> 32) & MAX)
                    guest.literal(divisor, by & MAX)
                    guest.emit(0xE1F6, x)
                    state = self.run_state(guest)
                    if signed:
                        quotient = abs(dividend) // abs(by)
                        quotient = quotient if (dividend < 0) == (by < 0) else -quotient
                    else:
                        quotient = (dividend & (2**64 - 1)) // (by & MAX)
                    got = state["registers"][dest + 1] << 32 | state["registers"][dest]
                    self.assertEqual(got, quotient & (2**64 - 1))

    def test_packed_immediate_ifs(self):
        for h, x, register, value, taken in [
                (0xE9A3, 0x0B80, 3, 65535, True), (0xE9A3, 0x0B80, 3, 65536, False), (0xE9A3, 0x0B80, 3, MAX, False),
                (0xEC23, 0x0BA0, 3, 81920, False), (0xEC23, 0x0BA0, 3, 81921, True), (0xEC23, 0x0BA0, 3, MAX, True),
                (0xEEA6, 0x0B80, 6, 65536, True), (0xEEA6, 0x0B80, 6, 65537, False), (0xEEA6, 0x0B80, 6, MAX, True),
                (0xEEA3, 0x0FFF, 3, 510, True), (0xEEA3, 0x0FFF, 3, 511, False)]:
            with self.subTest(op=f"{h:04X} {x:04X}", value=value):
                guest = Guest()
                guest.literal(register, value)
                guest.literal(2, 0)
                guest.emit(h, x)                 # a one-instruction THEN: the literal below
                guest.literal(2, MARK)
                guest.literal(5, 0x5555)
                state = self.run_state(guest)
                self.assertEqual(state["registers"][2] == MARK, taken)
                self.assertEqual(state["registers"][5], 0x5555)

    def test_signed_register_ifs(self):
        # ED97 0F00 ifs (r7 < r15) {, EE17 0F00 ifs (r7 > r15) {: -1 against 1 tells signed from unsigned.
        for h, left, right, taken in [(0xED97, MAX, 1, True), (0xED97, 1, MAX, False),
                                      (0xEE17, 1, MAX, True), (0xEE17, MAX, 1, False)]:
            with self.subTest(op=f"{h:04X}", left=left, right=right):
                guest = Guest()
                guest.literal(7, left)
                guest.literal(15, right)
                guest.literal(2, 0)
                guest.emit(h, 0x0F00)
                guest.literal(2, MARK)
                state = self.run_state(guest)
                self.assertEqual(state["registers"][2] == MARK, taken)

    def test_register_compare_branches(self):
        # FF49 / FF4B / FF4C / FF4D: if (r3 <= r5), ifs (r3 < r5), ifs (r3 > r5), ifs (r3 <= r5) goto;
        # a taken branch skips the six-byte literal after it.
        for op, left, right, taken in [
                (0xFF49, 1, MAX, True), (0xFF49, MAX, 1, False), (0xFF4B, MAX, 1, True), (0xFF4B, 1, MAX, False),
                (0xFF4C, 1, MAX, True), (0xFF4C, MAX, 1, False), (0xFF4D, MAX, 1, True), (0xFF4D, 1, MAX, False)]:
            with self.subTest(op=f"{op:04X}", left=left, right=right):
                guest = Guest()
                guest.literal(3, left)
                guest.literal(5, right)
                guest.literal(2, 0)
                guest.emit(op, 0x3500, 3)
                guest.literal(2, MARK)
                state = self.run_state(guest)
                self.assertEqual(state["registers"][2] == MARK, not taken)

    def test_false_block_skips_whole_six_byte_bodies(self):
        # ECA3 0B80 (if (r3 <= 65536) {, false for 70000) over a six-byte body whose last word is 2145
        # (r5 = 1): a body counted as four bytes would execute it.
        for body in [(0xFF4C, 0, 0x2145), (0xFFA0, 0, 0x2145), (0xFF00, 0, 0x2145), (0xFFC0, 0, 0x2145)]:
            with self.subTest(op=f"{body[0]:04X}"):
                guest = Guest()
                guest.literal(3, 70000)
                guest.literal(5, 0)
                guest.emit(0xECA3, 0x0B80)
                guest.emit(*body)
                guest.literal(6, 0x6666)
                state = self.run_state(guest)
                self.assertEqual(state["registers"][5:7], [0, 0x6666])

    def test_load_multiple_may_include_its_base(self):
        guest = Guest()                                       # {r2, r1} = [r2+]
        guest.write(BASE, 0x11111111)
        guest.write(BASE + 4, 0x22222222)
        guest.literal(2, BASE)
        guest.emit(0xEB02, 0x0006)
        state = self.run_state(guest)
        self.assertEqual(state["registers"][1:3], [0x11111111, 0x22222222])

    def test_memory_xor_with_a_register(self):
        guest = Guest()                                       # [r14+0] ^= r4
        guest.write(BASE, 0xF0F0F0F0)
        guest.literal(14, BASE)
        guest.literal(4, 0xFF00FF00)
        guest.emit(0xE864, 0xE401)
        state = self.run_state(guest)
        self.assertEqual(self.word(state, BASE), 0x0FF00FF0)

    def test_return_address_push_and_pop(self):
        guest = Guest()                                       # [--sp] = {rets}; {rets} = [sp++]
        guest.literal(3, INSPECTION + 0x30)
        guest.emit(0xE064, 0x3E80)                            # sp = r3: a stack in SRAM
        guest.literal(3, 0x02001234)
        guest.emit(0xE064, 0x3380)                            # rets = r3
        guest.emit(0x04C8)
        guest.literal(3, 0x02005678)
        guest.emit(0xE064, 0x3380)
        guest.emit(0x0488)
        state = self.run_state(guest)
        self.assertEqual(state["specials"][3], 0x02001234)
        self.assertEqual(state["specials"][14], INSPECTION + 0x30)
        self.assertEqual(self.word(state, INSPECTION + 0x2C), 0x02001234)

    def test_or_immediate_in_a_bundle(self):
        guest = Guest()                                       # r0 = r0 | 0xF0 # r6 = h[r4+0] (u)
        guest.write(BASE, 0x00001234)
        guest.literal(4, BASE)
        guest.literal(0, 0x100)
        guest.emit(0xF140, 0x00F0)
        guest.emit(0x604E)
        state = self.run_state(guest)
        self.assertEqual([state["registers"][0], state["registers"][6]], [0x1F0, 0x1234])


if __name__ == "__main__":
    unittest.main()
