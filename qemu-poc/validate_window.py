#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Check display-hold guest state and pause lifecycle through QMP.

The minimal Cocoa build has no Pixman/QMP screendump. Pixel checks use the
guest LCD capture; native window rendering is checked separately by a person.
"""
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import tempfile
import time

import validate
from validate_diag_boot import STATUS_SHA

HERE = Path(__file__).resolve().parent
CACHE = HERE / ".cache/window-validation"


def rgb(path):
    ppm = path.read_bytes()
    header = b"P6\n240 240\n255\n"
    validate.check(ppm.startswith(header), "console dimensions/format differ")
    return ppm[len(header):]


def main():
    CACHE.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ, FM1_POC_LOOP_IRQS="640", FM1_POC_MAX_INSTRUCTIONS="100000000")
    for name in ["FM1_POC_KEEP_OPEN", "FM1_POC_STOP_PC", "FM1_POC_STATE_DIR"]:
        env.pop(name, None)
    command = [*validate.COMMAND, "-kernel", str(validate.ROOT / "build/fm1-diag.bin"), "-append", "diag"]
    control = subprocess.run(command, env=env, capture_output=True, text=True, timeout=60)
    validate.check(control.returncode == 0, control.stderr)
    expected = json.loads(control.stdout)
    (CACHE / "exit-state.json").write_text(json.dumps(expected, indent=2) + "\n")
    env.update(FM1_POC_KEEP_OPEN="1", FM1_POC_STATE_DIR=str(CACHE))
    # Use a short temporary socket path, independent of checkout path length.
    with tempfile.TemporaryDirectory(prefix="fm1-window-", dir="/private/tmp") as temporary:
        endpoint = Path(temporary) / "qmp.sock"
        with (CACHE / "pause-state.json").open("w") as out, (CACHE / "stderr.txt").open("w") as err:
            process = subprocess.Popen([*command, "-S", "-qmp", f"unix:{endpoint},server=on,wait=off"],
                                       env=env, stdout=out, stderr=err)
            try:
                deadline = time.monotonic() + 10
                while not endpoint.exists():
                    validate.check(process.poll() is None, "QEMU exited before QMP startup; inspect stderr.txt")
                    validate.check(time.monotonic() < deadline, "QMP startup timeout")
                    time.sleep(0.02)
                with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
                    connection.settimeout(60)
                    connection.connect(str(endpoint))
                    stream = connection.makefile("rwb", buffering=0)
                    validate.check("QMP" in json.loads(stream.readline()), "missing QMP greeting")
                    messages = []
                    def request(name, arguments=None):
                        stream.write((json.dumps({"execute": name, "arguments": arguments or {}}) + "\n").encode())
                        while True:
                            item = json.loads(stream.readline())
                            messages.append(item)
                            validate.check("error" not in item, f"QMP {name}: {item}")
                            if "return" in item:
                                return item["return"]
                    request("qmp_capabilities")
                    validate.check(not request("query-status")["running"], "QEMU ignored paused startup")
                    request("cont")
                    deadline = time.monotonic() + 60
                    while request("query-status")["status"] != "paused":
                        validate.check(time.monotonic() < deadline, "checkpoint pause timeout")
                        time.sleep(0.1)
                    actual = json.loads((CACHE / "pause-state.json").read_text())
                    validate.check(actual == expected, "display hold changed guest checkpoint state")
                    pixels = rgb(CACHE / "state-02002632.ppm")
                    validate.check(hashlib.sha256(pixels).hexdigest() == STATUS_SHA,
                                   "held guest LCD pixels differ from verified capture")
                    registers = request("human-monitor-command", {"command-line": "info registers"})
                    # A resume request at the completed checkpoint must re-pause,
                    # without executing guest instructions or emitting another result.
                    request("cont")
                    deadline = time.monotonic() + 10
                    while request("query-status")["status"] != "paused":
                        validate.check(time.monotonic() < deadline, "resume did not return to held checkpoint")
                        time.sleep(0.05)
                    after = request("human-monitor-command", {"command-line": "info registers"})
                    validate.check(after == registers,
                                   f"held checkpoint executed guest instructions:\nBefore: {registers}\nAfter: {after}")
                    validate.check(json.loads((CACHE / "pause-state.json").read_text()) == expected,
                                   "held checkpoint emitted an extra result")
                    request("quit")
                    process.wait(timeout=10)
                    validate.check(process.returncode == 0, "QMP close did not exit cleanly")
                    (CACHE / "qmp.json").write_text(json.dumps(messages, indent=2) + "\n")
            finally:
                if process.poll() is None:
                    process.terminate()
                    process.wait(timeout=10)
    print("PASS display hold: exact guest LCD capture, unchanged guest state, pause/resume and clean QMP close")
    print("Native Cocoa window rendering requires separate visual confirmation; QMP screendump is not enabled.")
    print(f"Evidence: {CACHE}")


if __name__ == "__main__":
    main()
