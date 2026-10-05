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


def compare(name, image, stop, exact_count=True, limit=1000000, polling_registers=()):
    env = dict(os.environ, FM1_POC_STOP_PC=hex(stop), FM1_POC_MAX_INSTRUCTIONS=str(limit))
    commands = {
        "qemu": [*validate.COMMAND, "-kernel", str(image), "-append", "diag"],
        "rust": ["mise", "exec", "--", "cargo", "run", "--manifest-path",
                 str(HERE / "reference/Cargo.toml"), "--locked", "--offline", "--", "snapshot", str(image)],
    }
    states = {}
    for label, command in commands.items():
        result = subprocess.run(command, cwd=validate.ROOT, env=env, capture_output=True, text=True, timeout=60)
        validate.check(result.returncode == 0, f"{name}/{label}: {result.stderr}")
        states[label] = json.loads(result.stdout)
    (CACHE / f"{name}.json").write_text(json.dumps(states, indent=2) + "\n")
    fields = ["pc", "specials", "inspection"]
    if exact_count:
        fields.append("instructions")
    for field in fields:
        validate.check(states["qemu"][field] == states["rust"][field], f"{name}: {field} differs")
    for index in range(16):
        if index not in polling_registers:
            validate.check(states["qemu"]["registers"][index] == states["rust"]["registers"][index],
                           f"{name}: r{index} differs")
    scope = "full snapshot" if not polling_registers else f"snapshot except timed poll registers {polling_registers}"
    print(f"PASS {name}: QEMU/Rust {scope}, {states['qemu']['instructions']} instructions")
    return states["qemu"]


def fixture(name, words):
    image = CACHE / f"{name}.bin"
    image.write_bytes(struct.pack("<" + "H" * len(words), *words) + b"\0" * 16)
    return compare(name, image, ENTRY + len(words) * 2)


def literal(register, value):
    return [0xffc0 | register, value & 0xffff, value >> 16]


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
    # The count is unsigned; shifts above the word width saturate rather than
    # wrapping modulo 32. Aliasing checks preserve the incoming source/count.
    for mode in [0, 2, 3]:
        for count in [0, 31, 32, 63, 0xffffffff]:
            dest = 1 if count == 31 else 0 if count == 32 else 2
            fixture(f"shift-register-{mode}-{count:08x}", [*literal(0, 0x81234567),
                    *literal(1, count), 0xe1c8, (dest << 12) | 0x100 | mode])
        for count in [0, 31, 32, 63]:
            fixture(f"shift-immediate-{mode}-{count}", [*literal(0, 0x71234567),
                    0xe1c0, 0x2000 | (mode << 10) | ((count >> 4) << 8) | (count & 15)])
    fixture("new-logic-arithmetic", [*literal(0, 0x12345678), *literal(1, 0x89abcdef),
            0xe142, 0x0045, 0xe153, 0x1055, 0xe190, 0x4100,
            0xe190, 0x5101, 0xe190, 0x6102, 0xe190, 0x7103,
            0xe0b4, 0x8100, 0xe0b4, 0x9102, 0xe070, 0xa000, 0x1801, 0x1b01])
    fixture("byte-addressing-memory-add", [*literal(0, 0x01c08000), *literal(1, 0x1234abcd),
            0x4089, 0x400a, *literal(2, 1), 0xeed8, 0x3020,
            0xeed8, 0x1021, 0xeed8, 0x4022, 0xeedc, 0x5020,
            *literal(0, 0x01c08000), 0xe041, 7, 0x6081, 0xebc0, 0x0fff,
            0x6002, 0xffee, 0x9ef0, 0x01c7, 0x94e9])
    fixture("zero-register-pairs", [0x1480, 0x1482, 0x14c0, 0x14c7])
    for name, op in [("ge", 0xfd00), ("lt", 0xfd80), ("gt", 0xfe00), ("le", 0xfe80)]:
        for value in [0, 0xffffffff]:
            fixture(f"signed-{name}-{value:08x}", [*literal(0, value), op | 0x70, 0xfe01, 0x2241])
    for name, op in [("eq", 0xff00), ("ne", 0xff01), ("ge", 0xff02),
                     ("lt", 0xff03), ("gt", 0xff08), ("le", 0xff09)]:
        for value in [0, 1, 0xffffffff]:
            fixture(f"long-branch-{name}-{value:08x}", [*literal(0, value), op, 1, 1, 0x2241])
    fixture("lcd-packed-sub", [*literal(0, 240), 0xe0a2, 0x00f0])
    fixture("lcd-multiply-flags-alias", [*literal(0, 11), 0xe064, 0x0580,
            *literal(0, 0x81234567), *literal(1, 0x7fffffff),
            0xe1e0, 0x0005, 0xe1f0, 0x1100])
    fixture("lcd-unsigned-div-min", [*literal(0, 0x80000000), *literal(1, 7),
            0xe1f4, 0x2100, 0xe435, 0x3020])
    fixture("lcd-post-half-store", [*literal(0, 0x01c08000), *literal(1, 0x12345678), 0x0681])
    fixture("lcd-pre-byte-store", [*literal(0, 0x01c08000), *literal(1, 0x12345678), 0xee5a, 0x1002])
    for value in [239, 240, 241, 0xffffffff, 520, 1099, 4095]:
        # Vendor literal forms ECB* differ from the pinned SLEIGH packed label.
        threshold = value if value in [520, 1099, 4095] else 240
        fixture(f"conditional-unsigned-le-{value:08x}", [*literal(0, value),
                0xecb0, threshold, 0xe041, 7])
    for value in [0, 1, 0x80000000]:
        fixture(f"conditional-register-mask-{value:08x}", [*literal(0, value), *literal(1, 1),
                0xea10, 0x0100, 0xe042, 7])
        fixture(f"conditional-register-ge-{value:08x}", [*literal(0, value), *literal(1, 1),
                0xe910, 0x0100, 0xe042, 7])
        for op in [0xff60, 0xff61]:
            fixture(f"long-mask-{op:04x}-{value:08x}", [*literal(0, value), op, 1, 1, 0x2241])
    state = fixture("gpio-mask-register", [*literal(3, 11), 0xe064, 0x3580,
            *literal(0, 0x01c08000), *literal(1, 9), *literal(2, 0xffffffff), 0x6282,
            0xe866, 0x0100, 0xe866, 0x0105, 0xe866, 0x010b])
    validate.check(state["inspection"][:3] == [0xa5a5a7a5, 0xa5a5a7a5, 0xfffffdff] and
                   state["specials"][5] == 11, "register-bit RMW effects/PSR differ")
    fixture("gpio-extended-mask-offset", [*literal(0, 0x01c08000), 0xef13, 0x0001])
    for value in [1, 2]:
        fixture(f"decrement-branch-{value}", [*literal(0, value), 0xea00, 1, 0x2241])
    for offset in [232, 2048, 4095]:
        fixture(f"stack-wide-address-{offset}", [0xffee, 0x9ef0, 0x01c7, 0xe8f8, offset])
    fixture("stack-double-word", [0xffee, 0x8000, 0x01c0, *literal(0, 0x12345678),
            *literal(1, 0x87654321), 0xe9d0, 0x0029, 0xe9d0, 0x2028])
    fixture("packed-add", [*literal(0, 0xffffffff), 0xe0e1, 0x0001])
    for index in [0, 31]:
        fixture(f"bit-register-{index}", [*literal(0, 0x81234567), *literal(1, index),
                0xe194, 0x2100, 0xe194, 0x3101, 0xe194, 0x4102, 0xe194, 0x5103])
    for op in [0x1a10, 0x1a90]:
        for count in [0, 31, 32, 0xffffffff]:
            fixture(f"low-shift-{op:04x}-{count:08x}", [*literal(0, 0x81234567), *literal(1, count), op])
    fixture("byte-post-signed", [*literal(0, 0x01c08000), 0xe041, 0x12,
            0xee52, 0x100b, 0xeed4, 0x200b])
    fixture("byte-pre-unsigned", [*literal(0, 0x01c08000), 0xe041, 0x12,
            0xee52, 0x100b, 0xee58, 0x200b])
    for value in [0, 1, 0x80000000]:
        fixture(f"register-mask-branch-{value:08x}", [*literal(0, value), *literal(1, 1),
                0xfb10, 1, 0x2242])
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
