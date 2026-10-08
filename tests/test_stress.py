# SPDX-License-Identifier: GPL-2.0-or-later
"""Stress CLI readiness, physical input cadence, and exact fault retention."""

import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest

from support import Guest, INSPECTION, ROOT, environment


def delayed_ready():
    guest = Guest()
    guest.write(INSPECTION, 1)
    iterations = 2_500_000
    guest.literal(2, iterations)
    loop = guest.pc
    guest.add(2, -1)
    guest.branch_zero(2, loop, nonzero=True)
    guest.write(INSPECTION + 4, 2)
    ready_instructions = guest.instructions + 2 * (iterations - 1)
    guest.branch_zero(2, guest.pc)
    return guest, ready_instructions


class StressTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="fm1-stress-")
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)

    def run_stress(self, guest, *arguments):
        image = self.directory / "guest.bin"
        image.write_bytes(guest.bytes() + bytes(16))
        output = self.directory / "capture"
        result = subprocess.run(
            [sys.executable, str(ROOT / "tests/stress.py"), str(image),
             "--seconds", "2", "--boot-timeout", "5", "--seed", "73",
             "--output", str(output), *arguments],
            cwd=ROOT, env=environment(), capture_output=True, text=True, timeout=20)
        self.assertTrue((output / "stress.json").exists(), result.stderr)
        report = json.loads((output / "stress.json").read_text())
        events = [json.loads(line) for line in (output / "inputs.jsonl").read_text().splitlines()]
        self.assertTrue((output / "stdout.txt").exists())
        self.assertTrue((output / "stderr.txt").exists())
        return result, report, events, output

    def test_ready_gate_and_host_and_guest_contact_spacing(self):
        guest, ready = delayed_ready()
        result, report, events, output = self.run_stress(
            guest, "--ready-memory", f"{INSPECTION:#x}:1",
            "--ready-memory", f"{INSPECTION + 4:#x}:2",
            "--hold-ms", "20", "--gap-ms", "20")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(report["status"], "completed")
        self.assertGreaterEqual(len(events), 4, report)
        self.assertFalse((output / "state.json").exists())
        controls = re.findall(r'APPLY\("([^"\n]+)",\s*(\w+),\s*(\d+),\s*(\d+)\)',
                              (ROOT / "src/include/ui/fm1-controls.h").read_text())
        expected = {q.lower(): (label, int(column), int(row))
                    for label, q, column, row in controls}
        self.assertEqual(len(expected), 41)
        cycle = [event["qcode"] for event in events if event["action"] == "press"][:41]
        self.assertEqual(len(cycle), len(set(cycle)), "a shuffle cycle repeated a contact")
        for index, event in enumerate(events):
            self.assertGreaterEqual(event["guest_instructions"], ready)
            self.assertEqual(event["index"], index)
            self.assertEqual(event["action"], "press" if index % 2 == 0 else "release")
            self.assertEqual((event["label"], event["column"], event["row"]),
                             expected[event["qcode"]])
            if index % 2:
                self.assertEqual(event["qcode"], events[index - 1]["qcode"])
            if index:
                previous = events[index - 1]
                self.assertGreaterEqual(event["host_seconds"] - previous["host_seconds"], .02)
                self.assertGreaterEqual(event["guest_instructions"] - previous["guest_instructions"],
                                        2_500_000)

    def test_default_starts_at_ui_ready_without_waiting_for_bootguard(self):
        guest = Guest()
        guest.write(0x01c7c040, 0x44424731)
        guest.write(0x01c7c06c, 9)
        ready = guest.instructions
        guest.branch_zero(2, guest.pc)
        result, report, events, _ = self.run_stress(guest, "--seconds", ".1")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(events)
        self.assertEqual(report["ready_memory"], [
            {"address": 0x01c7c040, "values": [0x44424731]},
            {"address": 0x01c7c06c, "values": [9]}])
        self.assertGreaterEqual(events[0]["guest_instructions"], ready)
        self.assertLess(report["boot_host_seconds"], 5)

    def test_readiness_timeout_never_sends_input(self):
        guest = Guest()
        guest.literal(0, 0)
        guest.branch_zero(0, guest.pc)
        result, report, events, output = self.run_stress(
            guest, "--ready-memory", f"{INSPECTION:#x}:1", "--boot-timeout", ".2")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(report["status"], "boot-timeout")
        self.assertEqual(events, [])
        self.assertFalse((output / "state.json").exists())

    def test_fault_preserves_exact_guest_state_before_readiness(self):
        guest = Guest()
        guest.literal(0, 0x12345678)
        fault_pc = guest.pc
        guest.emit(0x0023)
        result, report, events, output = self.run_stress(
            guest, "--ready-memory", f"{INSPECTION:#x}:1")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(report["status"], "guest-fault")
        self.assertEqual(events, [])
        state = json.loads((output / "state.json").read_text())
        self.assertEqual(state["pc"], fault_pc)
        self.assertEqual(state["instructions"], 1)
        self.assertEqual(report["failure"]["pc"], fault_pc)
        self.assertEqual(report["failure"]["instructions"], 1)
        self.assertIn("unsupported instruction 0x0023", report["failure"]["reason"])


if __name__ == "__main__":
    unittest.main()
