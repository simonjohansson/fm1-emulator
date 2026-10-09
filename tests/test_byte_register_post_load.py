# SPDX-License-Identifier: GPL-2.0-or-later
"""Compact unsigned byte loads advance the old base by incoming r13/r15."""
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

from support import COMMAND, INSPECTION, ROOT, Guest, environment, guest_state


def post_load(guest, destination, base, stride_register=15):
    assert 0 <= destination < 8 and 0 <= base < 8
    assert stride_register in (13, 15)
    guest.emit((0x1280 if stride_register == 13 else 0x1380) | base << 4 | destination)


class ByteRegisterPostLoadTests(unittest.TestCase):
    def setUp(self):
        directory = ROOT / ".cache/tests"
        directory.mkdir(parents=True, exist_ok=True)
        temporary = tempfile.TemporaryDirectory(dir=directory)
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)

    def fault_state(self, guest):
        image = self.directory / "guest.bin"
        image.write_bytes(guest.bytes() + bytes(16))
        result = subprocess.run(
            [*COMMAND, "-kernel", str(image), "-append", "application"],
            cwd=ROOT, env=environment(FM1_POC_STOP_PC=hex(guest.pc),
                                      FM1_POC_MAX_INSTRUCTIONS="10000",
                                      FM1_POC_STATE_DIR=str(self.directory)),
            capture_output=True, text=True, timeout=30)
        state = json.loads((self.directory / "state.json").read_text())
        return result, state

    def test_unsigned_old_base_load_register_stride_and_flags(self):
        # Includes reached 13C0/13D1/13B0/13A4 and 12F0/12F1/12C6.
        for stride_register, destination, base in (
                (15, 0, 4), (15, 1, 5), (15, 0, 3), (15, 4, 2), (15, 7, 6),
                (13, 0, 7), (13, 1, 7), (13, 6, 4)):
            other_stride = 13 if stride_register == 15 else 15
            for stride in (0, 2, -2):
                for offset, byte in ((2, 0x80), (3, 0xff)):
                    with self.subTest(stride_register=stride_register, destination=destination, base=base,
                                      stride=stride, byte=byte):
                        guest = Guest()
                        guest.write(INSPECTION, 0xff80beef)
                        guest.write(INSPECTION + 4, 0xcafebabe)
                        guest.literal(6, 0x89abcde5)
                        guest.emit(0xe064, 0x6580)  # PSR = r6.
                        guest.literal(destination, 0x12345678)
                        guest.literal(base, INSPECTION + offset)
                        guest.literal(other_stride, 23)
                        guest.literal(stride_register, stride)
                        post_load(guest, destination, base, stride_register)
                        state = guest_state(self.directory, guest)
                        self.assertEqual(state["registers"][destination], byte)
                        self.assertEqual(state["registers"][base],
                                         INSPECTION + offset + stride)
                        self.assertEqual(state["registers"][stride_register], stride & 0xffffffff)
                        self.assertEqual(state["registers"][other_stride], 23)
                        self.assertEqual(state["inspection"][:2], [0xff80beef, 0xcafebabe])
                        self.assertEqual(state["specials"][5], 0x89abcde5)
                        self.assertEqual(state["instructions"], guest.instructions)

    def test_false_if_skips_two_byte_load_without_access_or_writeback(self):
        for stride_register in (13, 15):
            with self.subTest(stride_register=stride_register):
                guest = Guest()
                guest.literal(0, 1)
                guest.literal(4, 0xdead0000)
                guest.literal(stride_register, -2)
                guest.emit(0xea20, 1)  # False IF r0 == 0, one compact arm.
                post_load(guest, 0, 4, stride_register)
                guest.literal(7, 0xabcdef01)
                state = guest_state(self.directory, guest)
                self.assertEqual(state["registers"][0], 1)
                self.assertEqual(state["registers"][4], 0xdead0000)
                self.assertEqual(state["registers"][stride_register], 0xfffffffe)
                self.assertEqual(state["registers"][7], 0xabcdef01)
                self.assertEqual(state["instructions"], guest.instructions - 1)

    def test_unmapped_load_faults_before_writeback_or_retirement(self):
        for stride_register, stride in ((13, 0), (13, 2), (13, -2), (15, 0), (15, 2), (15, -2)):
            with self.subTest(stride_register=stride_register, stride=stride):
                guest = Guest()
                guest.literal(6, 0x89abcde5)
                guest.emit(0xe064, 0x6580)
                guest.literal(0, 0x12345678)
                guest.literal(4, 0xdead0000)
                guest.literal(stride_register, stride)
                fault_pc = guest.pc
                post_load(guest, 0, 4, stride_register)
                result, state = self.fault_state(guest)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("unmapped access at 0xdead0000", result.stderr)
                self.assertEqual(state["pc"], fault_pc)
                self.assertEqual(state["instructions"], guest.instructions - 1)
                self.assertEqual(state["registers"][0], 0x12345678)
                self.assertEqual(state["registers"][4], 0xdead0000)
                self.assertEqual(state["registers"][stride_register], stride & 0xffffffff)
                self.assertEqual(state["specials"][5], 0x89abcde5)

    def test_destination_base_alias_remains_rejected_before_effects(self):
        for stride_register, base, opcode in ((13, 7, 0x12f7), (15, 4, 0x13c4)):
            with self.subTest(stride_register=stride_register):
                guest = Guest()
                guest.literal(base, 0xdead0000)
                guest.literal(stride_register, 2)
                fault_pc = guest.pc
                post_load(guest, base, base, stride_register)
                result, state = self.fault_state(guest)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(f"unsupported instruction 0x{opcode:04x}", result.stderr)
                self.assertNotIn("unmapped access", result.stderr)
                self.assertEqual(state["pc"], fault_pc)
                self.assertEqual(state["instructions"], guest.instructions - 1)
                self.assertEqual(state["registers"][base], 0xdead0000)
                self.assertEqual(state["registers"][stride_register], 2)

    def test_unqualified_r11_stride_family_remains_rejected(self):
        guest = Guest()
        guest.literal(2, 0x12345678)
        guest.literal(3, 0xdead0000)
        guest.literal(11, 2)
        fault_pc = guest.pc
        # Vendor 11B2 names r2 = b[r3++=r11] (u), outside these two families.
        guest.emit(0x11b2)
        result, state = self.fault_state(guest)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("unsupported instruction 0x11b2", result.stderr)
        self.assertNotIn("unmapped access", result.stderr)
        self.assertEqual(state["pc"], fault_pc)
        self.assertEqual(state["instructions"], guest.instructions - 1)
        self.assertEqual(state["registers"][2], 0x12345678)
        self.assertEqual(state["registers"][3], 0xdead0000)
        self.assertEqual(state["registers"][11], 2)


if __name__ == "__main__":
    unittest.main()
