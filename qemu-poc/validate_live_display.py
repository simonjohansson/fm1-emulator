#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Observe an uninterrupted display loop, timer pixels and physical keys."""
import hashlib
import json
import os
from pathlib import Path
import select
import socket
import subprocess
import tempfile
import time

import validate
from validate_display import FILES, pixel

HERE = Path(__file__).resolve().parent
CACHE = HERE / ".cache/live-display-validation"
HEADER = b"P6\n240 240\n255\n"


def main():
    CACHE.mkdir(parents=True, exist_ok=True)
    for filename, digest in FILES.items():
        validate.check(hashlib.sha256((validate.ROOT / filename).read_bytes()).hexdigest() == digest,
                       f"display fixture hash differs: {filename}")
    command = [*validate.COMMAND, "-kernel", str(validate.ROOT / "tests/fixtures/display/firmware.bin"),
               "-append", "display"]
    command[command.index("-icount") + 1] = "shift=6,align=on,sleep=on"
    env = dict(os.environ, FM1_POC_DISPLAY_LIVE="1")
    for name in ["FM1_POC_KEEP_OPEN", "FM1_POC_STOP_PC", "FM1_POC_FRAME_DIR"]:
        env.pop(name, None)
    samples, transitions, events = [], [], []
    # No -S, stop or cont: the guest runs uninterrupted throughout this test.
    with tempfile.TemporaryDirectory(prefix="fm1-live-", dir="/private/tmp") as directory:
        frames = Path(directory)
        env["FM1_POC_FRAME_DIR"] = directory
        with (CACHE / "stderr.txt").open("w") as err, (CACHE / "stdout.txt").open("w") as out:
            endpoint = frames / "qmp.sock"
            process = subprocess.Popen([*command, "-qmp", f"unix:{endpoint},server=on,wait=off"],
                                       cwd=validate.ROOT, env=env, stdout=out, stderr=err)
            connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            connection.settimeout(10)
            stream = None
            def read_message():
                validate.check(select.select([connection], [], [], 10)[0], "QMP response timeout")
                line = stream.readline()
                validate.check(line, "QEMU exited before QMP response")
                return json.loads(line)

            def request(name):
                stream.write((json.dumps({"execute": name}) + "\n").encode())
                while True:
                    message = read_message()
                    if "event" in message:
                        events.append(message)
                    validate.check("error" not in message, f"QMP error: {message}")
                    if "return" in message:
                        return message["return"]

            try:
                deadline = time.monotonic() + 10
                while not endpoint.exists():
                    validate.check(process.poll() is None, "QEMU exited before QMP startup; inspect stderr.txt")
                    validate.check(time.monotonic() < deadline, "QMP startup timeout")
                    time.sleep(0.01)
                connection.connect(str(endpoint))
                stream = connection.makefile("rwb", buffering=0)
                validate.check("QMP" in read_message(), "missing QMP greeting")
                request("qmp_capabilities")
                started = time.monotonic()
                deadline = started + 30
                counters = set()
                while len(transitions) < 5:
                    validate.check(process.poll() is None, "live guest exited; inspect stderr.txt")
                    validate.check(time.monotonic() < deadline, "live timer/key cycle timeout")
                    validate.check(request("query-status")["running"], "live guest paused")
                    try:
                        before = (frames / "frame-live.json").read_bytes()
                        image = (frames / "frame-live.ppm").read_bytes()
                        after = (frames / "frame-live.json").read_bytes()
                    except FileNotFoundError:
                        time.sleep(0.02)
                        continue
                    if before != after:
                        continue
                    state = json.loads(before)
                    if samples and state["frame"] <= samples[-1]["frame"]:
                        time.sleep(0.02)
                        continue
                    validate.check(image.startswith(HEADER) and len(image) == len(HEADER) + 240 * 240 * 3,
                                   "invalid live framebuffer")
                    image = image[len(HEADER):]
                    pressed = state["matrix"][0] == 0xe1
                    validate.check(state["matrix"] == [0xe1 if pressed else 0x1e1] + [0x1e1] * 10,
                                   "guest GPIO matrix sample differs")
                    # The two atomic files can straddle publication of a frame.
                    # Retry that sample rather than stopping guest execution.
                    if pixel(image, 24, 202) != (0xf7cb00 if pressed else 0x313031):
                        continue
                    validate.check(state["visible"] and state["sp"] == 0x01c7a000 and
                                   state["ssp"] == 0x01c7c000, "live guest display/stack differs")
                    validate.check(pixel(image, 0, 0) == 0x101010 and pixel(image, 24, 14) == 0xffffff,
                                   "live title/background differs")
                    if samples:
                        validate.check(state["instructions"] > samples[-1]["instructions"] and
                                       state["display_ticks"] > samples[-1]["display_ticks"],
                                       "live instructions/timer stopped advancing")
                    state["wall_seconds"] = time.monotonic() - started
                    samples.append(state)
                    counters.add(hashlib.sha256(image[80 * 240 * 3:110 * 240 * 3]).hexdigest())
                    if not transitions or pressed != transitions[-1]:
                        transitions.append(pressed)
                        (CACHE / f"transition-{len(transitions)}.ppm").write_bytes(HEADER + image)
                    time.sleep(0.02)
                validate.check(transitions == [False, True, False, True, False], "key cycle differs")
                validate.check(samples[-1]["frame"] > 3 and len(counters) > 3,
                               "display did not keep repainting its timer after frame three")
                guest_seconds = (samples[-1]["virtual_ns"] - samples[0]["virtual_ns"]) / 1e9
                wall_seconds = samples[-1]["wall_seconds"] - samples[0]["wall_seconds"]
                validate.check(guest_seconds <= wall_seconds + 0.15, "guest clock ran ahead of real time")
                validate.check(all(p.name in {"frame-live.ppm", "frame-live.json",
                                             "frame-live.ppm.tmp", "frame-live.json.tmp", "qmp.sock"}
                                   for p in frames.iterdir()), "live capture created unbounded frame files")
                validate.check(request("query-status")["running"], "live guest stopped at end of observation")
                request("quit")
                process.wait(timeout=10)
                validate.check(process.returncode == 0, "QMP quit did not exit cleanly")
                validate.check(not any(e["event"] == "STOP" for e in events), "live guest emitted a pause event")
            finally:
                if stream is not None:
                    stream.close()
                connection.close()
                if process.poll() is None:
                    process.terminate()
                    process.wait(timeout=10)
    (CACHE / "validation.json").write_text(json.dumps({
        "passed": True, "firmware_hashes": FILES, "transitions_pressed": transitions,
        "guest_seconds": guest_seconds, "wall_seconds": wall_seconds,
        "distinct_timer_images": len(counters), "samples": samples, "events": events,
    }, indent=2) + "\n")
    print(f"PASS uninterrupted display: {samples[-1]['frame']} frames, two physical key cycles, "
          f"{len(counters)} changing timer images")
    print(f"Observed {guest_seconds:.3f} guest seconds in {wall_seconds:.3f} wall seconds; no pause")
    print(f"Evidence: {CACHE}")


if __name__ == "__main__":
    main()
