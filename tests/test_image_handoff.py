# SPDX-License-Identifier: GPL-2.0-or-later
"""Execute the packaged application's resource-directory handoff."""
from pathlib import Path
import subprocess
import tempfile
import unittest

from support import COMMAND, INSPECTION, Guest, environment
from test_images import package


class ImageHandoffTests(unittest.TestCase):
    def test_package_directory_is_readable_and_raw_handoff_stays_zero(self):
        guest = Guest()
        guest.load(2, 0, 8)  # SPL param+8, before guest vector setup
        guest.literal(1, INSPECTION)
        guest.store(2, 1)
        guest.branch_zero(2, guest.pc + 6)
        guest.load(3, 2, 4)  # validated area's application entry
        guest.store(3, 1, 4)
        with tempfile.TemporaryDirectory(prefix="fm1-image-handoff-") as temporary:
            for kind in ("bin", "fwsc", "ufw"):
                with self.subTest(kind=kind):
                    directory = Path(temporary) / kind
                    directory.mkdir()
                    image = directory / ("guest." + kind)
                    payload = guest.bytes() + bytes(16)
                    if kind != "bin":
                        payload, _, _ = package(fwsc=kind == "fwsc", app=payload)
                    image.write_bytes(payload)
                    result = subprocess.run(
                        [*COMMAND, "-kernel", str(image), "-append", "application"],
                        env=environment(FM1_POC_STOP_PC=hex(guest.pc),
                                        FM1_POC_MAX_INSTRUCTIONS="1000",
                                        FM1_POC_STATE_DIR=str(directory)),
                        capture_output=True, text=True, timeout=30)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    ram = (directory / "state.sram").read_bytes()
                    words = [int.from_bytes(ram[0x8000 + i:0x8004 + i], "little")
                             for i in (0, 4)]
                    self.assertEqual(words,
                                     [0, 0] if kind == "bin" else
                                     [0x02000000, 0x02000120])
