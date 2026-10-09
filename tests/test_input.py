# SPDX-License-Identifier: GPL-2.0-or-later
"""Real QMP input through board GPIO/ADC, using a small synthetic scanner."""

import re
import subprocess
import tempfile
import time
import unittest

from behavior import QMP
from support import COMMAND, INSPECTION, ROOT, Guest, environment

HEADER = ROOT / "src/include/ui/fm1-controls.h"


def panel_contacts():
    """Read only transport qcodes; expected wiring below is independent."""
    text = HEADER.read_text()
    keys = re.findall(r'APPLY\("[^"\n]+",\s*(\w+),\s*(\d+),\s*(\d+)\)', text)
    encoders = re.findall(
        r'APPLY\("[^"\n]+",\s*(\w+),\s*(\w+),\s*(\d+),\s*(\d+),\s*(\d+),\s*(\d+)\)', text)
    result = [(q.lower(), int(c), int(r)) for q, c, r in keys]
    for a, b, ac, ar, bc, br in encoders:
        result.extend(((a.lower(), int(ac), int(ar)),
                       (b.lower(), int(bc), int(br))))
    return result


def scanner():
    guest = Guest()
    # PA0/5/6/7/8 and PB7 are matrix returns; PA1/3/4 drive the
    # latch/clock/data lines of the actual board's shift-register chain.
    for address, value in ((0x50008, 0x1e1), (0x5000c, 0x1fb),
                           (0x50010, 0x1e1), (0x50014, 0),
                           (0x50048, 0x80), (0x5004c, 0x80),
                           (0x50050, 0x80), (0x50054, 0)):
        guest.write(address, value)
    guest.literal(6, 0)
    start = guest.pc
    for column in range(11):
        word = 0xffff ^ (1 << column)
        for bit in range(15, -1, -1):
            data = ((word >> bit) & 1) << 4
            guest.write(0x50000, data)
            guest.write(0x50000, data | 8)
            guest.write(0x50000, data)
        guest.write(0x50000, 2)
        guest.write(0x50000, 0)
        for port, offset in ((0x50004, 0), (0x50044, 4)):
            guest.literal(2, port)
            guest.load(3, 2)
            guest.literal(4, INSPECTION + column * 8 + offset)
            guest.store(3, 4)
    # Each conversion uses the existing channel-4 provider and virtual
    # completion timer. The guest waits on SAR pending before reading RES.
    guest.write(0x13100, 0xf45e)
    guest.literal(2, 0x13100)
    guest.literal(5, 0x80)
    polling = guest.pc
    guest.load(3, 2)
    guest.emit(0x1634)  # r4 = r3.
    guest.emit(0x19d4)  # r4 &= r5.
    guest.branch_zero(4, polling)
    guest.load(3, 2, 4)
    guest.literal(4, INSPECTION + 88)
    guest.store(3, 4)
    guest.add(6, 1)
    guest.store(6, 4, 4)
    displacement = ((start - (guest.pc + 4)) // 2) & 0x3fffff
    guest.emit(0xeac0 | (displacement >> 16), displacement & 0xffff)
    return guest


class BoardInputTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="fm1-input-")
        self.addCleanup(self.temporary.cleanup)
        from pathlib import Path
        directory = Path(self.temporary.name)
        image = directory / "scanner.bin"
        image.write_bytes(scanner().bytes())
        self.stderr = (directory / "stderr").open("w+")
        self.addCleanup(self.stderr.close)
        self.process = subprocess.Popen(
            [*COMMAND, "-kernel", str(image), "-qmp",
             f"unix:{directory / 'qmp'},server=on,wait=off"],
            cwd=ROOT, env=environment(), stdout=subprocess.DEVNULL,
            stderr=self.stderr)
        self.addCleanup(self.stop)
        deadline = time.monotonic() + 15
        while not (directory / "qmp").exists():
            self.alive()
            if time.monotonic() >= deadline:
                self.fail("QMP did not start")
            time.sleep(0.005)
        self.qmp = QMP(directory / "qmp", time.monotonic() + 120)
        self.addCleanup(self.qmp.close)
        self.wait(lambda state: state[23] > 0)

    def stop(self):
        if self.process.poll() is None:
            self.process.terminate()
            self.process.wait(timeout=5)

    def alive(self):
        if self.process.poll() is not None:
            self.stderr.seek(0)
            self.fail(self.stderr.read())

    def wait(self, predicate):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            self.alive()
            state = self.qmp.words(INSPECTION, 24)
            if predicate(state):
                return state
            time.sleep(0.005)
        self.fail(f"scanner condition not reached: {state}")

    @staticmethod
    def closures(state):
        result = []
        for column in range(11):
            pa, pb = state[column * 2:column * 2 + 2]
            rows = int(not (pa & 1))
            for row in range(1, 5):
                rows |= int(not (pa & (1 << (row + 4)))) << row
            rows |= int(not (pb & 0x80)) << 5
            result.append(rows)
        return result

    def matrix(self, *contacts):
        expected = [0] * 11
        for column, row in contacts:
            expected[column] |= 1 << row
        self.wait(lambda state: self.closures(state) == expected)

    def test_all_contacts_and_legacy_coordinates(self):
        contacts = panel_contacts()
        self.assertEqual(len(contacts), 55)
        self.assertEqual(len({q for q, _, _ in contacts}), 55)
        self.assertEqual(len({(c, r) for _, c, r in contacts}), 55)
        # Independently transcribed FM1_KEYMAP key IDs 14..40, then 2..13,0,1.
        positions = [47, 46, 49, 48, 51, 50, 52, 53, 54, 33, 34, 35,
                     36, 37, 38, 39, 40, 41, 42, 43, 22, 23, 24, 25,
                     26, 28, 27, 17, 15, 13, 11, 31, 30, 18, 16,
                     14, 12, 32, 29, 44, 45]
        # Encoders: SELECT, PRESETS, ALGORITHM (stock firmware swaps the
        # transcription's PRESETS/ALGORITHM pair), KNOB 1-4.
        positions.extend([0, 1, 55, 56, 2, 3, 19, 20, 8, 9, 6, 7, 4, 5])
        self.assertEqual([(c, r) for _, c, r in contacts],
                         [(position % 11, position // 11) for position in positions])
        legacy = {"z": (3, 4), "c": (2, 4), "x": (0, 4), "v": (1, 4),
                  "h": (7, 1), "p": (2, 1), "o": (0, 1), "a": (0, 0),
                  "s": (1, 0), "d": (8, 0), "f": (9, 0)}
        self.assertEqual({q: (c, r) for q, c, r in contacts if q in legacy}, legacy)
        for qcode, column, row in contacts:
            with self.subTest(qcode=qcode):
                self.qmp.key(qcode, True)
                self.matrix((column, row))
                self.qmp.key(qcode, False)
                self.matrix()
        self.qmp.key("z", True)
        self.qmp.key("c", True)
        self.matrix((3, 4), (2, 4))
        self.qmp.key("z", False)
        self.matrix((2, 4))
        self.qmp.key("c", False)
        self.matrix()

    def test_master_adc_and_pause_retention(self):
        self.wait(lambda state: state[22] == 512)
        for value, raw in ((0, 0), (32767, 1023), (16384, 511)):
            self.qmp.request("input-send-event", {"events": [{"type": "abs", "data": {
                "axis": "x", "value": value}}]})
            self.wait(lambda state: state[22] == raw)
        self.qmp.request("stop")
        self.qmp.request("cont")
        self.wait(lambda state: state[22] == 511)

    def test_pause_lost_keyup_rearms_without_press(self):
        self.qmp.key("z", True)
        self.matrix((3, 4))
        self.qmp.request("stop")
        with self.assertRaises(RuntimeError):
            self.qmp.key("z", False)
        self.qmp.request("cont")
        self.matrix()
        self.qmp.key("z", True)
        self.matrix()
        self.qmp.key("z", False)
        self.matrix()
        self.qmp.key("z", True)
        self.matrix((3, 4))
        self.qmp.key("z", False)
        self.matrix()

    def test_queue_overflow_releases_and_quarantines(self):
        # A single QMP batch holds BQL while all 65 changes arrive, so the
        # bounded FIFO overflows before its CPU worker can drain it.
        events = [{"type": "key", "data": {"down": index % 2 == 0,
                  "key": {"type": "qcode", "data": "z"}}} for index in range(65)]
        self.qmp.request("input-send-event", {"events": events})
        self.matrix()
        self.qmp.key("z", False)
        self.qmp.key("z", True)
        self.matrix((3, 4))
        self.qmp.key("z", False)
        self.matrix()


if __name__ == "__main__":
    unittest.main()
