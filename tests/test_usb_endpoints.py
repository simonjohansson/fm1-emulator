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
        for configuration in (0, 0xe0c, 0x164c, 0x7efc):
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

    def test_pad_sensing_write_is_rejected(self):
        guest = Guest()
        guest.write(PADS, 0x164c)
        guest.write(PADS + 4, 0)
        result = run_guest(self.directory, guest)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("unsupported USB pad sensing write", result.stderr)


if __name__ == "__main__":
    unittest.main()
