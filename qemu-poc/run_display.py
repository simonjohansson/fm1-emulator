#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Open the unchanged diagnostic in a native macOS window."""
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
    output = HERE / ".cache/live-display"
    output.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ, FM1_POC_KEEP_OPEN="1", FM1_POC_LOOP_IRQS="6000",
               FM1_POC_MAX_INSTRUCTIONS="200000000", FM1_POC_STATE_DIR=str(output))
    env.pop("FM1_POC_STOP_PC", None)
    print("Opening FM-1. Boot runs live, then pauses with the screen open. Close the window to quit.", flush=True)
    os.chdir(ROOT)
    # Keep the machine's diagnostic JSON available without filling the terminal.
    with (output / "state.json").open("w") as record:
        os.dup2(record.fileno(), sys.stdout.fileno())
    os.execve(qemu, [str(qemu), "-name", "FM-1 diagnostic", "-M", "fm1-poc",
                    "-accel", "tcg,thread=single", "-icount", "shift=3,align=off,sleep=off",
                    "-display", "cocoa,zoom-to-fit=on,zoom-interpolation=off",
                    "-serial", "none", "-monitor", "none", "-nodefaults",
                    "-kernel", str(ROOT / "build/fm1-diag.bin"), "-append", "diag"], env)


if __name__ == "__main__":
    main()
