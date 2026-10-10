#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Measure how fast a firmware runs, headless and windowed.

    python tools/speed.py FIRMWARE [--headless-seconds 50] [--ui-seconds 40] [--skip-ui]

Both runs use the launcher's two-core command line and end with a capture
(FM1_POC_CAPTURE_NS) at a fixed guest time:

- headless: unpaced, no display or audio. Speed is guest time divided by
  host wall time, the most the emulator can do;
- ui: the SDL window with SDL audio, paced to real time, so it cannot
  exceed 1x. Reported are the speed, the host CPU the process used (100%
  is one core) and icount's "guest is late" warnings, which mean the host
  could not keep up.
"""
import argparse
import os
from pathlib import Path
import platform
import re
import subprocess
import tempfile
import time

ROOT = Path(__file__).resolve().parent.parent
QEMU = ROOT / "emulator"
SECOND = 1_000_000_000
COMMON = ["-name", "FM-1", "-M", "fm1-poc", "-smp", "2", "-accel", "tcg,thread=single"]
TAIL = ["-chardev", "null,id=console", "-serial", "chardev:console",
        "-monitor", "none", "-nodefaults", "-no-user-config"]
MODES = {
    "headless": ["-icount", "shift=3,align=off,sleep=off", "-display", "none"],
    "ui": ["-icount", "shift=3,align=on,sleep=on",
           "-display", "sdl,show-cursor=on",
           "-audiodev", "sdl,id=fm1,out.frequency=44100,out.channels=2",
           "-global", "fm1-alnk.audiodev=fm1"],
}


def measure(mode, firmware, seconds):
    with tempfile.TemporaryDirectory(prefix=f"fm1-speed-{mode}-") as directory:
        env = {key: value for key, value in os.environ.items()
               if not key.startswith("FM1_POC_")}
        env.update(FM1_POC_STATE_DIR=directory,
                   FM1_POC_CAPTURE_NS=str(int(seconds * SECOND)),
                   FM1_POC_MAX_INSTRUCTIONS=str(10 ** 13))
        command = [str(QEMU), "--qemu", *COMMON, *MODES[mode], *TAIL,
                   "-kernel", str(firmware), "-append", "application"]
        start = time.monotonic()
        process = subprocess.Popen(command, cwd=ROOT, env=env, text=True,
                                   stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        with process.stderr:
            stderr = process.stderr.read()
        # wait4 instead of wait, for the child's CPU time.
        _, status, usage = os.wait4(process.pid, 0)
        process.returncode = os.waitstatus_to_exitcode(status)
        host = time.monotonic() - start
    if "requested capture" not in stderr:
        raise SystemExit(f"{mode} run stopped early: {stderr.strip()}")
    late = len(re.findall(r"guest is now late", stderr))
    cpu = usage.ru_utime + usage.ru_stime
    return {"guest": seconds, "host": host, "speed": seconds / host,
            "cpu": 100 * cpu / host, "late": late}


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("firmware", type=Path, help=".bin, .fwsc or .ufw application")
    parser.add_argument("--headless-seconds", type=float, default=50,
                        help="guest seconds of the headless run (default 50)")
    parser.add_argument("--ui-seconds", type=float, default=40,
                        help="guest seconds of the windowed run (default 40)")
    parser.add_argument("--skip-ui", action="store_true", help="measure headless only")
    args = parser.parse_args()
    firmware = args.firmware.resolve()
    if not firmware.is_file() or not QEMU.is_file():
        parser.error("firmware and built ./emulator must exist")

    print(f"{firmware.name} on {platform.machine()} {platform.platform(terse=True)}", flush=True)
    modes = [("headless", args.headless_seconds)]
    if not args.skip_ui:
        modes.append(("ui", args.ui_seconds))
    for mode, seconds in modes:
        print(f"{mode}: running {seconds:g} guest seconds...", flush=True)
        r = measure(mode, firmware, seconds)
        line = (f"{mode}: {r['speed']:.2f}x ({r['guest']:g} guest s in {r['host']:.1f} host s), "
                f"{r['cpu']:.0f}% of a core")
        if mode == "ui":
            line += f", {r['late']} 'guest is late' warnings"
        print(line, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
