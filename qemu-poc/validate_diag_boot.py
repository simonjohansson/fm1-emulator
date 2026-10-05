#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Boot the unchanged FM1_980 diagnostic to its visible, running main loop."""
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import struct
import subprocess

import validate
import validate_diag_startup
import validate_isa

HERE = Path(__file__).resolve().parent
CACHE = HERE / ".cache/diag-boot-validation"
STATUS_SHA = "cc00d897e166126d034a174776615fcdac85e473892ea3a3a2eef33db25de6b0"
SYMBOL_SHA = "db623d6156d18117e351a3856135be15f17874ccbc2ea4b561ee3edb8c3d43de"


def capture(name, expected_retries=0, **parameters):
    directory = CACHE / name
    directory.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ, FM1_POC_STATE_DIR=str(directory), FM1_POC_MAX_INSTRUCTIONS="100000000")
    env.update(parameters)
    result = subprocess.run([*validate.COMMAND, "-kernel", str(validate.ROOT / "build/fm1-diag.bin"),
                             "-append", "diag"], cwd=validate.ROOT, env=env,
                            capture_output=True, text=True, timeout=90)
    (directory / "stderr.txt").write_text(result.stderr)
    validate.check(result.returncode == 0, f"{name}: {result.stderr}")
    state = json.loads(result.stdout)
    (directory / "state.json").write_text(json.dumps(state, indent=2) + "\n")
    ram = (directory / f"state-{state['pc']:08x}.sram").read_bytes()
    ppm = (directory / f"state-{state['pc']:08x}.ppm").read_bytes()
    validate.check(ppm.startswith(b"P6\n240 240\n255\n"), "LCD PPM header differs")
    rgb = ppm[len(b"P6\n240 240\n255\n"):]
    validate.check(len(rgb) == 240 * 240 * 3 and hashlib.sha256(rgb).hexdigest() == STATUS_SHA,
                   f"{name}: actual guest status pixels differ from the independent analytical oracle")
    validate.check(Counter(rgb[i:i + 3] for i in range(0, len(rgb), 3)) ==
                   {b"\0\0\0": 50976, b"\xff\xff\xff": 3744, b"\0\xff\0": 2880},
                   "status colors/counts differ")
    validate.check(state["lcd"] == {"visible": True, "busy": False, "pixels_written": 124480,
                   "commands": 1216, "dma_transfers": 1700, "completed_transfers": 2916},
                   "LCD wire activity/visibility differs")
    validate.check(state["lcd_timeouts"] == state["p33_timeouts"] == 0 and
                   state["watchdog_expirations"] == 0, "guest LCD/P33 wait or watchdog expired")
    validate.check(state["specials"][14] == 0x01c79ef0 and state["specials"][13] == 0x01c7c000,
                   "guest stacks were not restored")
    validate.check(state["virtual_ns"] == (state["instructions"] + 1) * 8, "guest icount differs")
    nor = state["nor"]
    validate.check(nor["xip_enabled"] and not nor["busy"] and not nor["selected"] and
                   nor["transactions"] == 3 and nor["read_commands"] == 2 and nor["read_bytes"] == 12 and
                   nor["transfers"] == nor["completed_transfers"] == nor["acknowledgments"] == 26,
                   "read-only NOR boot evidence differs")
    usb = state["usb"]
    validate.check(not usb["host_connected"] and not usb["sie_clock_available"] and
                   usb["control"] == 0x3d and usb["pads"] == 0x164c and usb["dma_packets"] == 0 and
                   usb["requests"] == 6 * (expected_retries + 1) and
                   usb["poll_reads"] == 120000 * (expected_retries + 1) and
                   usb["recent_requests"] == [0x160, 0xb07, 0x701, 0x800, 0x900, 0xa00] and
                   usb["recent_polls"] == [20000] * 6 and state["usb_up"] == 0 and
                   state["usb_timeouts"] == 6 and state["usb_retries"] == expected_retries,
                   "disconnected USB initialization/retry differs")
    return state, ram


def main():
    CACHE.mkdir(parents=True, exist_ok=True)
    for path, expected in [("build/fm1-diag.bin", validate_isa.DIAG_SHA),
                           ("build/fm1-diag.dis", validate_diag_startup.DIS_SHA),
                           ("build/fm1-diag.symbols", SYMBOL_SHA)]:
        validate.check(hashlib.sha256((validate.ROOT / path).read_bytes()).hexdigest() == expected,
                       f"immutable diagnostic input differs: {path}")
    before, _ = capture("usb-initialized", FM1_POC_STOP_PC="0x0200225e")
    validate.check(before["irq_entries"] == before["rti_count"] == before["milliseconds"] == 0,
                   "USB startup checkpoint is not before TIMER5 enable")
    # 640 ticks include 58 complete eleven-column scans and exceed the
    # forty-frame encoder-rest learning interval. The stop also requires
    # three real foreground visits, after all selected conditional arms.
    running, ram = capture("main-loop", FM1_POC_LOOP_IRQS="640")
    # Startup takes about 412 ms of the functional clock; 6000 further timer
    # periods pass the guest's 1000 ms retry deadline. The blocking retry
    # naturally adds more periods before the next foreground checkpoint.
    retry, retry_ram = capture("usb-retry", expected_retries=1, FM1_POC_LOOP_IRQS="6000",
                               FM1_POC_MAX_INSTRUCTIONS="200000000")
    for state, memory, ticks in [(running, ram, 640), (retry, retry_ram, 6000)]:
        validate.check(state["pc"] == 0x02002632 and state["loop_visits"] >= 3 and
                       state["irq_entries"] == state["rti_count"] == state["timer_expirations"] ==
                       state["acknowledgments"] >= ticks and not state["pending"] and not state["in_irq"],
                       "main loop did not complete balanced TIMER5 interrupt cycles")
        validate.check(state["last_irq_handler"] == 0x0200046e and
                       state["watchdog_feeds"] > 3 and state["milliseconds"] > before["milliseconds"] and
                       state["write_enable"] == 7, "foreground watchdog/time/guard progress differs")
        validate.check(struct.unpack_from("<3I", memory, 0x7c040) == (0x42475244, 0, 1),
                       "clean boot guard state differs")
        validate.check(struct.unpack_from("<I", memory, 0x991c)[0] >= ticks // 11 and
                       memory[0x98b4:0x98bf] == b"\0" * 11 and memory[0x98bf:0x98e8] == b"\0" * 41,
                       "guest matrix scan/debounce does not reflect released inputs")
        validate.check(memory[0x98e8:0x98f6] == b"\0" * 14 and memory[0x98f6:0x98fd] == b"\1" * 7 and
                       min(memory[0x98fd:0x9904]) >= 40 and memory[0x9904:0x991a] == b"\0" * 22,
                       "guest encoder rest learning differs")
    retry_time = struct.unpack_from("<I", retry_ram, 0x96e0)[0]
    validate.check(1000 <= retry_time <= retry["milliseconds"] and
                   struct.unpack_from("<I", retry_ram, 0x9804)[0] == 1 and
                   struct.unpack_from("<I", retry_ram, 0x9810)[0] == 0 and
                   retry["milliseconds"] >= 1000, "guest timed retry/detach state differs")
    summary = {"entry": 0x02000120, "binary_sha256": validate_isa.DIAG_SHA,
               "status_rgb_sha256": STATUS_SHA, "usb_state": "cold host absent; six guest timeouts",
               "checkpoints": {"usb_initialized": before, "main_loop": running, "usb_retry": retry}}
    (CACHE / "boot.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(f"PASS unchanged FM1_980: visible exact status pixels, {running['loop_visits']} main-loop visits, "
          f"{running['rti_count']} TIMER5 IRQ/rti cycles, {running['milliseconds']} ms")
    print("PASS guest matrix frames and encoder rest learning, watchdog feeds, guards and disconnected USB timeouts")
    print(f"PASS disconnected USB retry: {retry['rti_count']} balanced IRQ/rti cycles, "
          f"{retry['loop_visits']} loop visits, {retry['milliseconds']} ms; "
          "12 real SIE requests and 240000 guest polls")
    print(f"Evidence: {CACHE}")


if __name__ == "__main__":
    main()
