#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Re-run immutable guest fixtures in QEMU and the separate Rust process."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import struct
import subprocess

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
QEMU = HERE / ".cache/build/qemu-system-pi32v2"
RESULTS = HERE / "results"
COMMAND = [str(QEMU), "-M", "fm1-poc", "-accel", "tcg,thread=single",
           "-icount", "shift=3,align=off,sleep=off", "-display", "none",
           "-serial", "none", "-monitor", "none", "-nodefaults"]


def check(test, message):
    if not test:
        raise SystemExit(message)


def save(name, value):
    (RESULTS / f"{name}.json").write_text(json.dumps(value, indent=2) + "\n")


def json_run(command):
    result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, timeout=15)
    check(result.returncode == 0, result.stderr)
    return json.loads(result.stdout)


def rust(fixture, image):
    return json_run(["mise", "exec", "--", "cargo", "run", "--manifest-path",
                     str(HERE / "reference/Cargo.toml"), "--locked", "--offline",
                     "--", fixture, image])


def infrastructure_checks():
    requests = "\n".join(json.dumps({"execute": command}) for command in
                         ["qmp_capabilities", "query-target", "quit"]) + "\n"
    result = subprocess.run([str(QEMU), "-M", "none", "-display", "none",
                             "-nodefaults", "-monitor", "none", "-qmp", "stdio"],
                            input=requests, capture_output=True, text=True, timeout=15)
    check(result.returncode == 0, result.stderr)
    messages = [json.loads(line) for line in result.stdout.splitlines()]
    check(any(message.get("return") == {"arch": "pi32v2"} for message in messages),
          "QMP target architecture differs")
    save("qmp-target", messages)

    logfile = HERE / ".cache/probe-tcg.log"
    probe = json_run([*COMMAND, "-kernel", "build/probe.bin", "-append", "probe",
                      "-d", "op", "-D", str(logfile)])
    check(probe["instructions"] == 75, "TCG logging changed probe execution")
    blocks = [len(re.findall(r"^ ---- [0-9a-f]+$", block, re.MULTILINE))
              for block in logfile.read_text().split("OP:\n")[1:]]
    # Conditional completion currently needs a boundary after each guest
    # instruction. This is a common CPU correctness policy, not throughput.
    check(blocks and max(blocks) == 1, "CPU conditional-completion TB boundary differs")
    save("tcg-translation", {
        "qemu_revision": "7c949c53e936aa3a658d84ab53bae5cadaa5d59c",
        "fixture": "build/probe.bin", "translation_block_guest_markers": blocks,
        "maximum_guest_markers_per_block": max(blocks), "performance_comparison": False,
    })
    print(f"PASS QEMU infrastructure: pi32v2 target; up to {max(blocks)} guest markers per block")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("fixture", choices=["probe", "timer", "all"], default="all", nargs="?")
    args = parser.parse_args()
    manifest = json.loads((HERE / "fixtures.json").read_text())
    RESULTS.mkdir(exist_ok=True)
    for filename, expected in manifest["files"].items():
        check(hashlib.sha256((ROOT / filename).read_bytes()).hexdigest() == expected,
              f"fixture hash differs: {filename}")
    capture = (ROOT / "build/hardware-initial.txt").read_text()
    hardware = {}
    for line in capture.splitlines():
        if line.startswith("W "):
            _, i, value = line.split()
            check(int(i) not in hardware, "duplicate hardware word")
            hardware[int(i)] = int(value, 16)
    check(set(hardware) == set(range(12)), "incomplete hardware capture")
    expected_probe = [int(x, 16) for x in manifest["probe"]["expected_hex"]]
    check([hardware[i] for i in range(12)] == expected_probe, "hardware values differ")
    check(manifest["probe"]["function_sha256"] in capture, "hardware probe identity differs")
    for filename, offset in [("build/probe.bin", 0x1c),
                             ("build/foundation/firmware.bin", 0x1d0),
                             ("build/fm1-diag.bin", 0x2aa2)]:
        function = (ROOT / filename).read_bytes()[offset:offset + 114]
        check(hashlib.sha256(function).hexdigest() == manifest["probe"]["function_sha256"],
              f"embedded probe bytes differ: {filename}")

    summary = {"hardware_probe_words": 12, "performance_comparison": False, "fixtures": {}}
    for fixture in ["probe", "timer"]:
        if args.fixture not in ["all", fixture]:
            continue
        image = "build/probe.bin" if fixture == "probe" else "build/foundation/firmware.bin"
        reference = rust(fixture, image)
        qemu = json_run([*COMMAND, "-kernel", image, "-append", fixture])
        expected = [int(x, 16) for x in manifest[fixture]["expected_hex"]]
        for field in ["inspection", "registers", "pc", "irq_entries", "pending"]:
            check(qemu[field] == reference[field], f"{fixture}: QEMU/Rust {field} differs")
        check(qemu["inspection"] == expected, f"{fixture}: fixture expectation differs")
        # The host stop breakpoint adds one QEMU icount slot, not a guest retirement.
        check(qemu["virtual_ns"] == (qemu["instructions"] + 1) * 8, "unexpected icount mapping")
        if fixture == "probe":
            check(qemu["specials"] == reference["specials"], "probe special registers differ")
            check(qemu["instructions"] == reference["instructions"] == 75, "probe instruction count differs")
            initial = [0x10203040 + i * 0x01010101 for i in range(16)]
            check(qemu["registers"][1:] == initial[1:], "probe register preservation failed")
            check(qemu["specials"][14] == 0x01c7a000, "probe stack preservation failed")
            direct = rust("direct-probe", "build/fm1-diag.elf")
            check(direct["inspection"] == expected and direct["instructions"] == 70,
                  "unchanged diagnostic ELF direct probe differs")
            save("rust-direct-probe", direct)
        else:
            # Functional clocks differ, so IRQ can interrupt either polling slot.
            check(qemu["specials"][1:] == reference["specials"][1:], "timer special registers differ")
            for state in [qemu, reference]:
                check(state["specials"][0] in [0x02000280, 0x02000282], "unexpected interrupt return PC")
            check(not qemu["in_irq"] and not qemu["pending"], "IRQ did not complete")
            for field in ["irq_entries", "rti_count", "timer_expirations", "acknowledgments"]:
                check(qemu[field] == 1, f"timer: expected exactly one {field}")
            check(qemu["last_irq_handler"] == 0x020002bc, "wrong guest vector handler")
            check(qemu["entry_icfg"] == 0x013f0302 and qemu["return_icfg"] == 0x013f0700,
                  "IRQ63 priority/source entry or return state differs")
        save(f"qemu-{fixture}", qemu)
        save(f"rust-{fixture}", reference)
        summary["fixtures"][fixture] = {
            "passed": True, "qemu_guest_instructions": qemu["instructions"],
            "rust_guest_instructions": reference["instructions"],
        }
        print(f"PASS {fixture}: QEMU {qemu['instructions']} / Rust {reference['instructions']} guest instructions")

    negatives = []
    if args.fixture in ["all", "probe"]:
        baseline = (ROOT / "build/probe.bin").read_bytes()
        cases = [("unsupported-opcode", 0, b"\xff\xff", "unsupported instruction"),
                 ("unmapped-memory", 18, struct.pack("<I", 0xdead0000), "unmapped access"),
                 ("unaligned-memory", 18, struct.pack("<I", 0x01c08001), "unaligned access"),
                 ("read-only-xip", 18, struct.pack("<I", 0x02000300), "read-only XIP")]
        directory = HERE / ".cache/negative-fixtures"
        directory.mkdir(exist_ok=True)
        for name, offset, patch, expected in cases:
            image = bytearray(baseline)
            image[offset:offset + len(patch)] = patch
            file = directory / f"{name}.bin"
            file.write_bytes(image)
            result = subprocess.run([*COMMAND, "-kernel", str(file), "-append", "probe"],
                                    capture_output=True, text=True, timeout=15)
            check(result.returncode != 0 and expected in result.stderr, f"{name} did not fail explicitly")
            negatives.append({"case": name, "returncode": result.returncode, "diagnostic": result.stderr.strip()})
            print(f"PASS explicit fault: {name}")
        save("negative-checks", negatives)
        infrastructure_checks()
    save("validation", summary)


if __name__ == "__main__":
    main()
