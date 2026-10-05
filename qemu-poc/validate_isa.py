#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Focused encoding gates and real diagnostic entry snapshot; cache only."""
import hashlib
import json
import os
from pathlib import Path
import struct
import subprocess

import validate

HERE = Path(__file__).resolve().parent
CACHE = HERE / ".cache/isa-validation"
ENTRY = 0x02000120
DIAG_SHA = "781005cfcc4e0b562291fa747a0fa956204ee97c396c39214df04feaf86cc7e2"


def compare(name, image, stop, exact_count=True):
    env = dict(os.environ, FM1_POC_STOP_PC=hex(stop), FM1_POC_MAX_INSTRUCTIONS="1000000")
    commands = {
        "qemu": [*validate.COMMAND, "-kernel", str(image), "-append", "diag"],
        "rust": ["mise", "exec", "--", "cargo", "run", "--manifest-path",
                 str(HERE / "reference/Cargo.toml"), "--locked", "--offline", "--", "snapshot", str(image)],
    }
    states = {}
    for label, command in commands.items():
        result = subprocess.run(command, cwd=validate.ROOT, env=env, capture_output=True, text=True, timeout=30)
        validate.check(result.returncode == 0, f"{name}/{label}: {result.stderr}")
        states[label] = json.loads(result.stdout)
    fields = ["pc", "registers", "specials", "inspection"]
    if exact_count:
        fields.append("instructions")
    for field in fields:
        validate.check(states["qemu"][field] == states["rust"][field], f"{name}: {field} differs")
    (CACHE / f"{name}.json").write_text(json.dumps(states, indent=2) + "\n")
    print(f"PASS {name}: QEMU/Rust full snapshot, {states['qemu']['instructions']} instructions")
    return states["qemu"]


def fixture(name, words):
    image = CACHE / f"{name}.bin"
    image.write_bytes(struct.pack("<" + "H" * len(words), *words) + b"\0" * 16)
    return compare(name, image, ENTRY + len(words) * 2)


def main():
    CACHE.mkdir(parents=True, exist_ok=True)
    # Vendor E04A FFFF is rendered as r10=-1 at diagnostic PC 0x02001b50.
    # This and the external oracle contradict pinned SLEIGH's movz label.
    state = fixture("literal-boundaries", [0xe040, 0x8000, 0xe041, 0xffff])
    validate.check(state["registers"][:2] == [0xffff8000, 0xffffffff], "16-bit literals are not sign extended")
    for name, op, delta in [("minimum", 0x8082, -512), ("minus-four", 0x9fe2, -4),
                            ("zero", 0x8002, 0), ("maximum", 0x9f62, 508)]:
        state = fixture(f"stack-{name}", [0xffee, 0xa000, 0x01c7, op])
        validate.check(state["specials"][14] == 0x01c7a000 + delta, "SP adjustment differs")
    state = fixture("bundle-old-store", [0xe040, 0, 0xffc1, 0x8000, 0x01c0,
                                          0xf040, 0x4009, 0x6190])
    validate.check(state["inspection"][1] == 0 and state["registers"][0] == 0x4009,
                   "parallel store did not consume incoming r0")
    state = fixture("bundle-old-source", [0xe040, 0x1234, 0xd601, 0xe040, 0x8000])
    validate.check(state["registers"][:2] == [0xffff8000, 0x1234], "parallel ALU input was overwritten")
    state = fixture("bundle-head-old-store", [0xffc0, 0x8000, 0x01c0, 0xe041, 0x2222,
                                               0xc581, 0xe041, 0x3333])
    validate.check(state["inspection"][0] == 0x2222 and state["registers"][:2] == [0x01c08004, 0x3333],
                   "parallel head store did not consume incoming source")
    state = fixture("mask-and-store", [0xe040, 0x07ff, 0xe162, 0x0123,
                                       0xffc1, 0x8000, 0x01c0, 0xea41, 0x1005])
    validate.check(state["registers"][2] == 0x123 and state["inspection"][1] == 5,
                   "packed immediate mask/store differs")
    fixture("compare-branch", [0xe040, 1, 0xf800, 0x0201, 0x0000])
    for value in [0, 1]:
        state = fixture(f"conditional-else-{value}", [0xe040, value, 0xea20, 0x1001,
                                                     0xe041, 2, 0xe041, 3])
        validate.check(state["registers"][1] == (2 if value == 0 else 3), "wrong conditional arm")
        fixture(f"conditional-no-else-{value}", [0xe040, value, 0xea20, 1, 0xe041, 2])
    fixture("conditional-long-skip", [0xe040, 1, 0xea20, 0x1001, 0xff80, 0, 0, 0xe041, 3])
    fixture("conditional-bundle-skip", [0xe040, 1, 0xea20, 0x1001, 0xd601, 0xe042, 4, 0xe041, 3])
    fixture("conditional-signed-sentinel", [0xe040, 0xfb95, 0xe8b0, 0x0b95, 0xe041, 2])
    for value in [0, 1]:
        fixture(f"conditional-irq-mask-{value}", [0xe040, value, 0xea20, 0x1001, 0x0061, 0xe041, 3])
    fixture("bit-extract-insert", [0xe040, 0x1234, 0xe1b1, 0x0408, 0xe1a1, 0x02ec])
    fixture("wide-stack-offset", [0xffee, 0x9ef0, 0x01c7, 0xe040, 0x1234,
                                  0xe9d4, 0x010d, 0xe9d4, 0x210c])
    for condition, op in [("ge", 0xf900), ("lt", 0xf980), ("gt", 0xfc00), ("le", 0xfc80)]:
        for value in [0, 1, 2, 0x80000000]:
            fixture(f"unsigned-{condition}-{value:08x}", [0xffc0, value & 0xffff, value >> 16,
                                                         op, 0x0201, 0x2241])
    diag = validate.ROOT / "build/fm1-diag.bin"
    validate.check(hashlib.sha256(diag.read_bytes()).hexdigest() == DIAG_SHA, "diagnostic binary differs")
    compare("diagnostic-before-p33", diag, 0x020015f8)
    env = dict(os.environ, FM1_POC_MAX_INSTRUCTIONS="1")
    result = subprocess.run([*validate.COMMAND, "-kernel", str(diag), "-append", "diag"],
                            cwd=validate.ROOT, env=env, capture_output=True, text=True, timeout=15)
    validate.check(result.returncode != 0 and "instruction limit reached" in result.stderr and
                   "after 1 instructions" in result.stderr, "diagnostic bound did not fault")
    print("PASS explicit diagnostic instruction bound")


if __name__ == "__main__":
    main()
