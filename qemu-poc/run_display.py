#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Run the unchanged timer-and-key example in a native macOS window."""
import os
from pathlib import Path
import subprocess
import sys

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent


def main():
    if sys.platform != "darwin":
        raise SystemExit("This launcher uses the native macOS Cocoa display.")
    qemu = HERE / ".cache/build/qemu-system-pi32v2"
    if not qemu.is_file():
        raise SystemExit("Build first: mise exec python@3.13.15 -- python qemu-poc/build.py")
    backends = subprocess.run([str(qemu), "-display", "help"], capture_output=True, text=True, check=True)
    if "cocoa" not in backends.stdout.split():
        raise SystemExit("Enable the display: mise exec python@3.13.15 -- python qemu-poc/build.py --reconfigure")
    env = dict(os.environ, FM1_POC_DISPLAY_LIVE="1")
    for name in ["FM1_POC_KEEP_OPEN", "FM1_POC_LOOP_IRQS", "FM1_POC_MAX_INSTRUCTIONS",
                 "FM1_POC_STATE_DIR", "FM1_POC_STOP_PC"]:
        env.pop(name, None)
    print("Opening the FM-1 timer and key display. OCT-minus alternates automatically. Close the window to quit.",
          flush=True)
    os.chdir(ROOT)
    os.execve(qemu, [str(qemu), "-name", "FM-1 timer and keys", "-M", "fm1-poc",
                    "-accel", "tcg,thread=single", "-icount", "shift=8,align=on,sleep=on",
                    "-display", "cocoa,zoom-to-fit=on,zoom-interpolation=off",
                    "-serial", "none", "-monitor", "none", "-nodefaults",
                    "-kernel", str(ROOT / "tests/fixtures/display/firmware.bin"), "-append", "display"], env)


if __name__ == "__main__":
    main()
