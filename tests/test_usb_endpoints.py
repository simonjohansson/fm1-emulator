# SPDX-License-Identifier: GPL-2.0-or-later
"""USB endpoint bounds and distinct pad configuration/sensing registers."""
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

from support import COMMAND, INSPECTION, ROOT, USB, Guest, environment, run_guest

PADS = 0x51000


class USBEndpointTests(unittest.TestCase):
    def setUp(self):
        directory = ROOT / ".cache/tests"
        directory.mkdir(parents=True, exist_ok=True)
        temporary = tempfile.TemporaryDirectory(dir=directory)
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)

    def test_endpoint_four_buffers_and_packet_length_readback(self):
        guest = Guest()
        guest.write(USB + 0x38, INSPECTION)
        guest.write(USB + 0x3c, INSPECTION + 128)
        guest.write(USB + 0x34, 64)
        guest.literal(2, USB + 0x34)
        for register, offset in ((3, 0), (4, 4), (5, 8)):
            guest.load(register, 2, offset)
        image = self.directory / "guest.bin"
        image.write_bytes(guest.bytes() + bytes(16))
        command = COMMAND.copy()
        command[command.index("-serial") + 1] = "null"
        result = subprocess.run(
            [*command, "-kernel", str(image), "-append", "diag"],
            cwd=ROOT, env=environment(FM1_POC_STOP_PC=hex(guest.pc),
                                      FM1_POC_MAX_INSTRUCTIONS="10000"),
            capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["registers"][3:6],
                         [64, INSPECTION, INSPECTION + 128])

    def test_endpoint_four_transmit_buffer_rejects_unmapped_address(self):
        guest = Guest()
        guest.write(USB + 0x38, 0)
        result = run_guest(self.directory, guest)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("USB endpoint buffer must be aligned and entirely in SRAM", result.stderr)

    def test_pad_sensing_is_distinct_from_stored_configuration(self):
        # Boot and attached-board CON0 values, plus the full accepted mask.
        # CON1 retains the captured attached-board input, rather than echoing
        # CON0 or inventing an unmeasured dependency on its configuration.
        for configuration in (0, 0xe0c, 0x164c, 0x7efc, 0x6636):
            with self.subTest(configuration=hex(configuration)):
                guest = Guest()
                guest.write(PADS, configuration)
                guest.literal(2, PADS)
                guest.load(3, 2)
                guest.load(4, 2, 4)
                result = run_guest(self.directory, guest)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(json.loads(result.stdout)["registers"][3:5],
                                 [configuration, 2])

    def test_dm_output_latch_set_and_clear_preserve_other_configuration(self):
        # Physical USB-mode capture: CON0 164c -> 164e -> 164c -> 164c.
        # Stock uses the same bit with CON0 6634 -> 6636. Live hardware
        # CON1 varies with USB traffic; the model retains its prior capture 2.
        for initial in (0x164c, 0x6634):
            with self.subTest(initial=hex(initial)):
                guest = Guest()
                guest.literal(2, PADS)
                guest.write(PADS, initial)
                guest.literal(4, 2)
                guest.load(3, 2)
                guest.literal(1, INSPECTION)
                guest.store(3, 1)
                guest.load(3, 2, 4)
                guest.store(3, 1, 4)
                # Set bit 1 by RMW, preserving the other accepted fields.
                guest.emit(0xe864, 0x2400)
                guest.load(3, 2)
                guest.store(3, 1, 8)
                guest.load(3, 2, 4)
                guest.store(3, 1, 12)
                # Clear bit 1 by RMW, then repeat the clear operation.
                for offset in (16, 24):
                    guest.load(3, 2)
                    guest.literal(4, 0xfffffffd)
                    guest.emit(0x19c3)  # r3 &= r4.
                    guest.store(3, 2)
                    guest.load(3, 2)
                    guest.store(3, 1, offset)
                    guest.load(3, 2, 4)
                    guest.store(3, 1, offset + 4)
                result = run_guest(self.directory, guest)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(json.loads(result.stdout)["inspection"][:8],
                                 [initial, 2, initial | 2, 2, initial, 2, initial, 2])

    def test_gpio_output_mode_and_dp_output_bit_remain_rejected(self):
        for configuration, reason in (
                (0x6636 | 0x800, "unsupported USB pad GPIO output configuration"),
                (0x6636 | 1, "unsupported USB pad configuration")):
            with self.subTest(configuration=hex(configuration)):
                guest = Guest()
                guest.write(PADS, 0x6634)
                fault_pc = guest.write(PADS, configuration)
                result = run_guest(self.directory, guest)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(reason, result.stderr)
                self.assertIn(f"at PC 0x{fault_pc:08x} after 5 instructions", result.stderr)

    def test_pad_sensing_write_is_rejected(self):
        guest = Guest()
        guest.write(PADS, 0x164c)
        guest.write(PADS + 4, 0)
        result = run_guest(self.directory, guest)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("unsupported USB pad sensing write", result.stderr)


if __name__ == "__main__":
    unittest.main()
