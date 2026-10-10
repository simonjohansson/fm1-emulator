# SPDX-License-Identifier: GPL-2.0-or-later
"""Panel LEDs light where a selected 595 column meets a driven LED line."""
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

from support import COMMAND, ROOT, Guest, environment

PA_OUT, PH_OUT = 0x50000, 0x501c0
LINES = {1: (PA_OUT, 1 << 9), 2: (PA_OUT, 1 << 10), 3: (PH_OUT, 1 << 6), 4: (PH_OUT, 1 << 9)}


def lit_guest(column, row):
    """Select one column on the 595 chain, drive one LED line, then spin."""
    guest = Guest()
    word = 0xffff ^ (1 << column)
    for bit in range(15, -1, -1):
        data = ((word >> bit) & 1) << 4
        guest.write(PA_OUT, data)
        guest.write(PA_OUT, data | 8)
        guest.write(PA_OUT, data)
    guest.write(PA_OUT, 2)
    guest.write(PA_OUT, 0)
    address, mask = LINES[row]
    guest.write(address, mask)
    start = guest.pc
    displacement = ((start - (guest.pc + 4)) // 2) & 0x3fffff
    guest.emit(0xeac0 | (displacement >> 16), displacement & 0xffff)
    return guest


class LEDTests(unittest.TestCase):
    def snapshot_leds(self, guest):
        with tempfile.TemporaryDirectory(dir=ROOT / ".cache") as work:
            work = Path(work)
            (work / "guest.bin").write_bytes(guest.bytes() + bytes(16))
            subprocess.run(
                [*COMMAND, "-kernel", str(work / "guest.bin")], cwd=ROOT, timeout=60,
                env=environment(FM1_POC_STATE_DIR=str(work),
                                FM1_POC_SNAPSHOT_NS="100000000",
                                FM1_POC_CAPTURE_NS="150000000",
                                FM1_POC_MAX_INSTRUCTIONS="100000000"),
                capture_output=True, text=True)
            return json.loads((work / "snapshots.jsonl").read_text())["leds"]

    def test_driven_line_lights_only_the_selected_column(self):
        for column, row in ((7, 1), (10, 2), (8, 3), (0, 4)):
            with self.subTest(column=column, row=row):
                leds = self.snapshot_leds(lit_guest(column, row))
                self.assertGreater(leds.pop(f"{column},{row}"), 240)
                self.assertEqual(leds, {})


if __name__ == "__main__":
    unittest.main()
