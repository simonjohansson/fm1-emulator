#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Random panel stress test: buttons, keys and encoders, scripted in guest time.

    python tests/stress.py FIRMWARE [--seconds 60] [--seed N] [--gap-ms 5] ...

One unpaced two-core run. After --boot-seconds of guest time, a seeded
random sequence of contact taps (every button and key in fm1-controls.h)
and encoder detents (either direction) is delivered through FM1_POC_INPUT
every few guest milliseconds for --seconds. Snapshots every two guest
seconds check that the display stays on and keeps updating and that audio
DMA keeps completing; after the burst a key is held and must sound. The
seed and every event are saved, so a failing run can be replayed exactly.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import random
import re
import subprocess

from support import QEMU, ROOT, environment

MS = 1_000_000
SECOND = 1000 * MS


def panel_controls():
    """Buttons/keys as (label, qcode); encoders as (label, phase A, phase B)."""
    source = (ROOT / "src/include/ui/fm1-controls.h").read_text()
    contacts = re.findall(r'APPLY\("([^"\n]+)",\s*(\w+),\s*(\d+),\s*(\d+)\)', source)
    encoders = re.findall(r'APPLY\("([^"\n]+)",\s*(\w+),\s*(\w+),\s*\d+,\s*\d+,\s*\d+,\s*\d+\)', source)
    if not contacts or not encoders:
        raise RuntimeError("panel control declarations not found")
    return ([{"label": label, "qcode": key.lower()} for label, key, _, _ in contacts],
            [{"label": label, "a": a.lower(), "b": b.lower()} for label, a, b in encoders])


def timeline(args, buttons, encoders):
    """Sequential random actions; no two touch a contact at once."""
    rng = random.Random(args.seed)
    t = int(args.boot_seconds * SECOND)
    end = t + int(args.seconds * SECOND)
    hold, gap, phase = (int(v * MS) for v in (args.hold_ms, args.gap_ms, args.phase_ms))
    events, actions = [], []
    while t < end:
        if rng.random() < args.encoder_share:
            enc = rng.choice(encoders)
            clockwise = rng.random() < 0.5
            first, second = (enc["b"], enc["a"]) if clockwise else (enc["a"], enc["b"])
            for i, (code, down) in enumerate(((first, 1), (second, 1), (first, 0), (second, 0))):
                events.append((t + i * phase, code, down))
            actions.append({"t": t, "encoder": enc["label"], "clockwise": clockwise})
            t += 4 * phase + gap
        else:
            button = rng.choice(buttons)
            events += [(t, button["qcode"], 1), (t + hold, button["qcode"], 0)]
            actions.append({"t": t, "button": button["label"], "qcode": button["qcode"]})
            t += hold + gap
    return events, actions, t


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("firmware", type=Path, help=".bin, .fwsc or .ufw application")
    parser.add_argument("--seconds", type=float, default=60, help="guest seconds of input (default 60)")
    parser.add_argument("--boot-seconds", type=float, default=20, help="guest seconds before input (default 20)")
    parser.add_argument("--hold-ms", type=float, default=5, help="button/key hold, guest ms (default 5)")
    parser.add_argument("--gap-ms", type=float, default=5, help="gap after each action, guest ms (default 5)")
    parser.add_argument("--phase-ms", type=float, default=5, help="encoder quadrature phase, guest ms (default 5)")
    parser.add_argument("--encoder-share", type=float, default=0.4, help="fraction of actions that turn an encoder")
    parser.add_argument("--probe-key", default="z", help="qcode held after the burst; it must sound")
    parser.add_argument("--seed", type=int, help="random seed (saved with the run)")
    parser.add_argument("--output", type=Path, help="capture directory (default .cache/tests/stress/<time>-<seed>)")
    args = parser.parse_args()
    for name in ("seconds", "boot_seconds", "hold_ms", "gap_ms", "phase_ms"):
        if not math.isfinite(getattr(args, name)) or getattr(args, name) <= 0:
            parser.error(f"--{name.replace('_', '-')} must be positive")
    args.firmware = args.firmware.resolve()
    if not args.firmware.is_file() or not QEMU.is_file():
        parser.error("firmware and built ./emulator must exist")
    if args.seed is None:
        args.seed = random.SystemRandom().getrandbits(64)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    directory = (args.output or ROOT / ".cache/tests/stress" / f"{stamp}-{args.seed}").resolve()
    if directory.exists():
        parser.error(f"capture directory already exists: {directory}")
    directory.mkdir(parents=True)

    buttons, encoders = panel_controls()
    events, actions, t = timeline(args, buttons, encoders)
    burst_end = t
    probe_down, probe_up = t + SECOND, t + 2 * SECOND
    events += [(probe_down, args.probe_key, 1), (probe_up, args.probe_key, 0)]
    snaps = list(range(int(args.boot_seconds * SECOND), burst_end, 2 * SECOND))
    snaps += [probe_down - 50 * MS, probe_down + 800 * MS]
    capture = probe_up + SECOND
    with (directory / "inputs.jsonl").open("w") as log:
        for action in actions:
            log.write(json.dumps(action) + "\n")
    # A file, as the script outgrows one environment string on Linux.
    script = directory / "input.txt"
    script.write_text(",".join(f"{at}:{code}:{down}" for at, code, down in sorted(events)))
    settings = {"FM1_POC_STATE_DIR": str(directory), "FM1_POC_CAPTURE_NS": str(capture),
                "FM1_POC_MAX_INSTRUCTIONS": str(10 ** 13),
                "FM1_POC_INPUT": f"@{script}",
                "FM1_POC_SNAPSHOT_NS": ",".join(map(str, snaps))}
    command = [str(QEMU), "--qemu", "-M", "fm1-poc", "-smp", "2", "-accel", "tcg,thread=single",
               "-icount", "shift=3,align=off,sleep=off", "-display", "none",
               "-chardev", "null,id=console", "-serial", "chardev:console",
               "-monitor", "none", "-nodefaults", "-no-user-config",
               "-kernel", str(args.firmware), "-append", "application"]
    print(f"{len(actions)} actions over {args.seconds:g} guest seconds, seed {args.seed}. "
          f"Evidence: {directory}", flush=True)
    result = subprocess.run(command, cwd=ROOT, env=environment(**settings),
                            capture_output=True, text=True)
    (directory / "stderr.txt").write_text(result.stderr)

    lines = (directory / "snapshots.jsonl").read_text().splitlines() \
        if (directory / "snapshots.jsonl").exists() else []
    seen = [json.loads(line) for line in lines]
    failures = []
    if "requested capture" not in result.stderr:
        last = seen[-1]["virtual_ns"] / SECOND if seen else 0
        failures.append(f"emulator stopped: {result.stderr.strip()} (last snapshot {last:.1f} s)")
    burst = [s for s in seen if s["index"] < len(snaps) - 2]
    for before, after in zip(burst, burst[1:]):
        when = f"{after['virtual_ns'] / SECOND:.1f} s"
        if not after["visible"]:
            failures.append(f"display off at {when}")
        if after["audio_completions"] <= before["audio_completions"]:
            failures.append(f"audio DMA stopped before {when}")
    updates = sum(b["lcd_commands"] > a["lcd_commands"] for a, b in zip(burst, burst[1:]))
    if len(burst) > 2 and updates == 0:
        failures.append("the display never updated during the burst")
    probe = [s for s in seen if s["index"] >= len(snaps) - 2]
    sounded = len(probe) == 2 and probe[1]["audio_nonzero_words"] - probe[0]["audio_nonzero_words"] >= 1000
    if len(probe) == 2 and not sounded:
        failures.append("the probe key did not sound after the burst")
    report = {"passed": not failures, "failures": failures, "seed": args.seed,
              "firmware": str(args.firmware),
              "firmware_sha256": hashlib.sha256(args.firmware.read_bytes()).hexdigest(),
              "emulator_sha256": hashlib.sha256(QEMU.read_bytes()).hexdigest(),
              "seconds": args.seconds, "boot_seconds": args.boot_seconds,
              "hold_ms": args.hold_ms, "gap_ms": args.gap_ms, "phase_ms": args.phase_ms,
              "actions": len(actions), "display_updates": updates,
              "returncode": result.returncode, "command": command}
    (directory / "stress.json").write_text(json.dumps(report, indent=2) + "\n")
    if failures:
        print("FAIL: " + "; ".join(failures[:5]) + f". Evidence: {directory}")
        return 1
    print(f"PASS: {len(actions)} actions, display updated in {updates}/{max(len(burst) - 1, 0)} "
          f"intervals, probe key sounded. Evidence: {directory}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
