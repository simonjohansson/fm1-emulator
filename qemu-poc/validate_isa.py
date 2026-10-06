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


def conditional_call(kind, arm, condition, has_else=True):
    # The selected arm ends in CALL, whose callee starts a separate IF. The
    # actual retry uses no ELSE; selected ELSE calls also match the reference.
    # A final THEN call with ELSE has an unresolved reference discrepancy
    # (the reference returns into ELSE), so QEMU explicitly rejects that form.
    width = 2 if kind == "direct" else 1
    header = [0xffee, 0x9ef0, 0x01c7, *literal(0, condition), *literal(2, 0), *literal(3, 0)]
    selected = [0] * width
    other = [0xe042, 0x1111]
    then = selected if arm == "then" else other
    otherwise = (other if arm == "then" else selected) if has_else else []
    main = [*header, 0xea20, 0x1001 if has_else else 1, *then, *otherwise, 0xe044, 0x4444]
    stop = ENTRY + len(main) * 2
    callee = stop + 2
    header[-3:] = literal(3, callee)
    call_index = len(header) + 2 + (len(then) if arm == "else" else 0)
    next_pc = ENTRY + (call_index + width) * 2
    if kind == "direct":
        delta = (callee - next_pc) // 2
        selected[:] = [0xea80 | ((delta >> 16) & 63), delta & 0xffff]
    elif kind == "short":
        delta = callee - next_pc
        selected[:] = [0x8001 | (((delta >> 6) & 7) << 4) | (((delta >> 1) & 31) << 8)]
    else:
        selected[:] = [0x00c3]
    main = [*header, 0xea20, 0x1001 if has_else else 1, *then, *otherwise, 0xe044, 0x4444]
    body = [0xe045, 0x55, 0xea25, 2, 0xe046, 0x66, 0x0080]
    image = CACHE / f"conditional-call-{kind}-{arm}-{condition}-{has_else}.bin"
    words = [*main, 0x0000, *body]
    image.write_bytes(struct.pack("<" + "H" * len(words), *words) + b"\0" * 16)
    if arm == "then" and has_else and condition == 0:
        env = dict(os.environ, FM1_POC_STOP_PC=hex(stop), FM1_POC_MAX_INSTRUCTIONS="1000000")
        result = subprocess.run([*validate.COMMAND, "-kernel", str(image), "-append", "diag"],
                                cwd=validate.ROOT, env=env, capture_output=True, text=True, timeout=15)
        call_pc = ENTRY + call_index * 2
        validate.check(result.returncode != 0 and "final THEN call with ELSE is unsupported" in result.stderr and
                       f"PC 0x{call_pc:08x}" in result.stderr,
                       "unresolved final THEN call with ELSE did not fault at its call")
        (CACHE / f"{image.stem}-negative.json").write_text(json.dumps(
            {"call_pc": call_pc, "returncode": result.returncode, "stderr": result.stderr}, indent=2) + "\n")
        print(f"PASS {image.stem}: explicit unsupported final THEN+ELSE call")
        return
    state = compare(image.stem, image, stop)
    called = condition == (0 if arm == "then" else 1)
    validate.check(state["registers"][6] == (0x66 if called else 0x16263646),
                   "conditional call chose the wrong arm/callee")
    if arm == "then":
        validate.check(state["registers"][2] == (0 if called or not has_else else 0x1111),
                       "final THEN call returned into skipped ELSE")


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
    for hi in [4, 8, 15]:
        words = [0xffee, 0x8080, 0x01c0, *literal(0, 0x11223344), 0xe064, 0x0380]
        expected = [0x81000000 + reg * 0x01010101 for reg in range(4, hi + 1)]
        for reg, value in zip(range(4, hi + 1), expected):
            words += literal(reg, value)
        words += [0x0470 | hi]
        for reg in range(4, hi + 1):
            words += [0xe040 | reg, 0]
        words += [*literal(0, 0xdeadbeef), 0xe064, 0x0380, 0x0430 | hi,
                  *literal(2, 0x22334455)]
        state = fixture(f"stack-pop-rets-range-{hi}", words)
        validate.check(state["registers"][4:hi + 1] == expected and
                       state["specials"][3] == 0x11223344 and
                       state["specials"][14] == 0x01c08080 and
                       state["registers"][2] == 0x22334455,
                       "RETS/range pop order, restored stack or sequential continuation differs")
    state = fixture("stack-push-rets-single", [0xffee, 0x8008, 0x01c0,
            *literal(0, 0x81234567), 0xe064, 0x0380, *literal(1, 0x89abcde5), 0xe064, 0x1580,
            0x0410, 0x2002, *literal(3, 0x01c08000), 0x6034])
    validate.check(state["specials"][14] == 0x01c08004 and state["specials"][3] == 0x81234567 and
                   state["specials"][5] == 0x89abcde5 and state["registers"][2] == 0x81234567 and
                   state["registers"][4] == 0xa5a5a5a5 and
                   state["inspection"][:3] == [0xa5a5a5a5, 0x81234567, 0xa5a5a5a5],
                   "standalone RETS push did not predecrement, preserve link/PSR or store exactly one word")
    state = fixture("stack-push-rets-two-links", [0xffee, 0x800c, 0x01c0,
            *literal(0, 0x01234567), 0xe064, 0x0380, 0x0410,
            *literal(0, 0x89abcdef), 0xe064, 0x0380, 0x0410, 0x2002, 0x2103])
    validate.check(state["specials"][14] == 0x01c08004 and state["specials"][3] == 0x89abcdef and
                   state["registers"][2:4] == [0x89abcdef, 0x01234567] and
                   state["inspection"][:4] == [0xa5a5a5a5, 0x89abcdef, 0x01234567, 0xa5a5a5a5],
                   "successive standalone RETS pushes lost word width, link order or stack movement")
    main_words = [0xffee, 0x8010, 0x01c0, *literal(4, 0x81234567),
                  *literal(0, 0x89abcde5), 0xe064, 0x0580, *literal(1, 0)]
    outer_call = len(main_words)
    main_words += [0, 0, *literal(2, 0x22334455)]
    stop = ENTRY + len(main_words) * 2
    outer_pc = stop + 2
    outer_words = [0x0410, 0xe8d8, 0x0010, *literal(4, 0xdeadbeef)]
    inner_call = len(outer_words)
    outer_words += [0, 0, *literal(3, 0x33445566), 0x0454]
    inner_pc = outer_pc + len(outer_words) * 2
    outer_return = ENTRY + (outer_call + 2) * 2
    inner_return = outer_pc + (inner_call + 2) * 2
    for words, index, target, return_pc in [(main_words, outer_call, outer_pc, outer_return),
                                           (outer_words, inner_call, inner_pc, inner_return)]:
        displacement = (target - return_pc) // 2
        words[index:index + 2] = [0xea80 | ((displacement >> 16) & 63), displacement & 0xffff]
    image = CACHE / "stack-push-rets-nested-call.bin"
    words = [*main_words, 0, *outer_words, *literal(5, 0x55667788), 0x0080]
    image.write_bytes(struct.pack("<" + "H" * len(words), *words) + b"\0" * 16)
    state = compare(image.stem, image, stop)
    validate.check(state["registers"][2:6] == [0x22334455, 0x33445566, 0x81234567, 0x55667788] and
                   state["specials"][14] == 0x01c08010 and state["specials"][3] == inner_return and
                   state["specials"][5] == 0x89abcde5 and state["instructions"] == 15 and
                   state["inspection"][:4] == [0xa5a5a5a5, 0xa5a5a5a5, 0x81234567, outer_return],
                   "nested CALL did not return through saved RETS, restore stack/register or preserve PSR")
    state = fixture("bundle-old-store", [0xe040, 0, 0xffc1, 0x8000, 0x01c0,
                                          0xf040, 0x4009, 0x6190])
    validate.check(state["inspection"][1] == 0 and state["registers"][0] == 0x4009,
                   "parallel store did not consume incoming r0")
    state = fixture("bundle-old-source", [0xe040, 0x1234, 0xd601, 0xe040, 0x8000])
    validate.check(state["registers"][:2] == [0xffff8000, 0x1234], "parallel ALU input was overwritten")
    for source, dest in [(6, 0), (0, 14), (14, 6), (6, 6)]:
        state = fixture(f"pair-move-{source}-{dest}", [*literal(source, 0x81234567),
                *literal(source + 1, 0xfedcba98), 0x1500 | (source << 4) | dest])
        validate.check(state["registers"][dest:dest + 2] == [0x81234567, 0xfedcba98] and
                       state["registers"][source:source + 2] == [0x81234567, 0xfedcba98],
                       "register-pair move order, word width or source preservation differs")
    state = fixture("pair-move-bundle-incoming-source", [*literal(6, 0x81234567),
            *literal(7, 0xfedcba98), 0xd562, 0xe046, 0x6666])
    validate.check(state["registers"][2:4] == [0x81234567, 0xfedcba98] and
                   state["registers"][6] == 0x6666,
                   "parallel pair move did not consume its incoming source")
    state = fixture("pair-move-bundle-incoming-store", [*literal(0, 0x01c08000),
            *literal(2, 0x22334455), *literal(6, 0x81234567), *literal(7, 0xfedcba98),
            0xd562, 0x6082])
    validate.check(state["registers"][2:4] == [0x81234567, 0xfedcba98] and
                   state["inspection"][0] == 0x22334455,
                   "parallel store did not consume the incoming pair-move destination")
    state = fixture("bundle-head-old-store", [0xffc0, 0x8000, 0x01c0, 0xe041, 0x2222,
                                               0xc581, 0xe041, 0x3333])
    validate.check(state["inspection"][0] == 0x2222 and state["registers"][:2] == [0x01c08004, 0x3333],
                   "parallel head store did not consume incoming source")
    state = fixture("mask-and-store", [0xe040, 0x07ff, 0xe162, 0x0123,
                                       0xffc1, 0x8000, 0x01c0, 0xea41, 0x1005])
    validate.check(state["registers"][2] == 0x123 and state["inspection"][1] == 5,
                   "packed immediate mask/store differs")
    fixture("compare-branch", [0xe040, 1, 0xf800, 0x0201, 0x0000])
    # Cross the displacement's low-word boundary in both directions. The
    # backward fixture jumps over its target, back to it, then forward to stop.
    header = [*literal(0, 0x11223344), 0xe064, 0x0380]
    forward = [*header, 0xeac1, 0, *([0] * 0x10000), *literal(2, 0x22334455)]
    backward = [*header, 0xeac1, 0, *literal(2, 0x22334455), 0xeac0, 0xfffd,
                *([0] * 0xfffb), 0xeafe, 0xfffe]
    for name, words, count in [("forward", forward, 4), ("backward", backward, 6)]:
        state = fixture(f"long-goto-{name}", words)
        validate.check(state["specials"][3] == 0x11223344 and
                       state["registers"][2] == 0x22334455 and
                       state["instructions"] == count,
                       "long GOTO signed target, RETS preservation or retired count differs")
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
    # Vendor 21A0/2120 at Felucca 0x0200cd2a/2c use SP+132; compact
    # offsets include bit 5 and extend through 252, in unsigned word units.
    for offset in [124, 128, 132, 252]:
        compact = 0x2000 | (((offset // 4) & 31) << 8) | ((offset // 4) & 32)
        state = fixture(f"stack-compact-offset-{offset}", [0xffee, 0x8000, 0x01c0,
                *literal(0, 0x81234567), compact | 0x80, compact | 2,
                *literal(1, 0x01c08000 + offset), 0x6015, 0x7f13, 0x6114])
        validate.check(state["registers"][0] == state["registers"][2] ==
                       state["registers"][5] == 0x81234567 and
                       state["registers"][3:5] == [0xa5a5a5a5] * 2 and
                       state["specials"][14] == 0x01c08000,
                       "compact stack load/store address, source, neighbors or SP differ")
    state = fixture("stack-compact-bundle-old-source", [0xffee, 0x8000, 0x01c0,
            *literal(0, 0x1234), 0xf040, 0x5678, 0x21a0,
            0xf041, 0x1111, 0x2122, *literal(1, 0x01c08084), 0x6013])
    validate.check(state["registers"][0] == 0x5678 and
                   state["registers"][2] == state["registers"][3] == 0x1234,
                   "compact stack bundle did not preserve incoming store source")
    # Reached ECDC 1162 uses r1 as both incoming increment and load destination.
    for name, base, increment, dest in [("separate", 0x01c08000, 4, 2),
                                      ("increment-alias", 0x01c08008, 0xfffffffc, 1)]:
        state = fixture(f"word-pre-register-{name}", [*literal(0, 0x01c08000),
                *literal(3, 0x81234567), 0x6183, *literal(6, base),
                *literal(1, increment), 0xecdc, (dest << 12) | 0x162])
        validate.check(state["registers"][6] == 0x01c08004 and
                       state["registers"][dest] == 0x81234567 and
                       state["inspection"][1] == 0x81234567,
                       "pre-increment word load address, writeback or alias result differs")
    state = fixture("word-pre-register-base-increment-alias", [*literal(0, 0x01c08000),
            *literal(3, 0x81234567), 0x6183, *literal(6, 0x00e04002), 0xecdc, 0x2662])
    validate.check(state["registers"][6] == 0x01c08004 and
                   state["registers"][2] == 0x81234567,
                   "pre-increment word load did not use incoming base/increment")
    values = [0x81234567, 0x89abcdef, 0x76543210]
    for base, mask, registers in [(4, 0x0104, [2, 8]), (6, 0x0012, [1, 4]),
                                  (0, 0xa100, [8, 13, 15])]:
        words = literal(0, 0x01c08000)
        for index, value in enumerate(values):
            words += [*literal(1, value), 0x6081 | (index << 8)]
        words += [*literal(base, 0x01c08000), 0xeb00 | base, mask]
        state = fixture(f"word-bitmap-load-{base}-{mask:04x}", words)
        validate.check([state["registers"][reg] for reg in registers] == values[:len(registers)] and
                       state["registers"][base] == 0x01c08000 and
                       state["inspection"][:3] == values,
                       "bitmap word loads differ in order, width, base preservation or memory")
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
    state = fixture("multiply-parallel-stack-source", [0xffee, 0x8000, 0x01c0,
            *literal(0, 12), *literal(10, 7), *literal(11, 0x11223344),
            *literal(2, 0x87654321), 0x2782, *literal(4, 0x89abcde5), 0xe064, 0x4580,
            0xf1f0, 0xb0a0, 0x2700, *literal(13, 0x33445566)])
    validate.check(state["registers"][11] == 84 and state["registers"][0] == 0x87654321 and
                   state["registers"][10] == 7 and state["inspection"][7] == 0x87654321 and
                   state["specials"][5] == 0x89abcde5 and state["instructions"] == 10 and
                   state["registers"][13] == 0x33445566,
                   "six-byte parallel multiply used loaded source, altered PSR or retired incorrectly")
    state = fixture("multiply-parallel-high-bank-source", [*literal(4, 0x89abcde5), 0xe064, 0x4580,
            *literal(14, 0x80000001), *literal(15, 3), 0xf1f0, 0xafe0, 0xe04e, 0x1234,
            *literal(13, 0x33445566)])
    validate.check(state["registers"][10] == 0x80000003 and state["registers"][14] == 0x1234 and
                   state["registers"][15] == 3 and state["specials"][5] == 0x89abcde5 and
                   state["instructions"] == 6 and state["registers"][13] == 0x33445566,
                   "eight-byte parallel multiply lost word width, incoming high-bank source or retirement")
    state = fixture("multiply-parallel-incoming-store", [*literal(4, 0x89abcde5), 0xe064, 0x4580,
            *literal(0, 0x01c08000), *literal(2, 0x89abcdef), *literal(5, 0xffffffff),
            *literal(6, 3), 0xf1f0, 0x2650, 0x6082, *literal(13, 0x33445566)])
    validate.check(state["registers"][2] == 0xfffffffd and state["inspection"][0] == 0x89abcdef and
                   state["registers"][5:7] == [0xffffffff, 3] and state["registers"][0] == 0x01c08000 and
                   state["specials"][5] == 0x89abcde5 and state["instructions"] == 8 and
                   state["registers"][13] == 0x33445566,
                   "parallel store lost incoming multiply destination, word width, PSR or retirement")
    fixture("lcd-unsigned-div-min", [*literal(0, 0x80000000), *literal(1, 7),
            0xe1f4, 0x2100, 0xe435, 0x3020])
    # E1F4/0101 at Felucca 0x02000848 divides the signed width difference
    # by two. Sign combinations distinguish truncation toward zero from floor.
    for name, left, right, dest, numerator, denominator, quotient in [
        ("reached", 0, 1, 0, 128, 2, 64),
        ("positive", 0, 1, 2, 7, 3, 2),
        ("negative-numerator", 0, 1, 0, -7, 3, -2),
        ("negative-denominator", 0, 1, 1, 7, -3, -2),
        ("both-negative", 0, 1, 2, -7, -3, 2),
        ("minimum", 0, 1, 2, -0x80000000, 1, -0x80000000),
        ("minimum-half", 0, 1, 1, -0x80000000, 2, -0x40000000),
        ("maximum-negative", 0, 1, 0, 0x7fffffff, -1, -0x7fffffff),
        ("truncate-negative-zero", 0, 1, 2, -1, 2, 0),
        ("zero", 0, 1, 1, 0, -1, 0),
        ("high-bank", 14, 13, 15, -0x80000000, -0x80000000, 1),
        ("high-bank-denominator-alias", 15, 14, 14, -0x7fffffff, 3, -715827882),
        ("same-source", 3, 3, 3, -7, -7, 1),
    ]:
        state = fixture(f"signed-div-{name}", [*literal(4, 0x89abcde5), 0xe064, 0x4580,
                *literal(left, numerator & 0xffffffff), *literal(right, denominator & 0xffffffff),
                0xe1f4, (dest << 12) | (right << 8) | (left << 4) | 1])
        expected = {left: numerator & 0xffffffff, right: denominator & 0xffffffff}
        expected[dest] = quotient & 0xffffffff
        validate.check(all(state["registers"][reg] == value for reg, value in expected.items()) and
                       state["specials"][5] == 0x89abcde5,
                       "signed division quotient, source/destination aliases or preserved PSR differ")
    for name, numerator, denominator, reason in [
        ("zero-denominator", 7, 0, "divide-by-zero behavior is unsupported"),
        ("overflow", 0x80000000, 0xffffffff, "signed-division-overflow behavior is unsupported"),
    ]:
        image = CACHE / f"signed-div-{name}.bin"
        words = [*literal(0, numerator), *literal(1, denominator), 0xe1f4, 0x0101]
        image.write_bytes(struct.pack("<" + "H" * len(words), *words) + b"\0" * 16)
        fault_pc = ENTRY + (len(words) - 2) * 2
        env = dict(os.environ, FM1_POC_STOP_PC=hex(ENTRY + len(words) * 2),
                   FM1_POC_MAX_INSTRUCTIONS="1000000")
        result = subprocess.run([*validate.COMMAND, "-kernel", str(image), "-append", "diag"],
                                cwd=validate.ROOT, env=env, capture_output=True, text=True, timeout=15)
        validate.check(result.returncode != 0 and reason in result.stderr and
                       f"PC 0x{fault_pc:08x}" in result.stderr,
                       "unresolved signed division edge did not fault at its instruction")
        (CACHE / f"{image.stem}-negative.json").write_text(json.dumps(
            {"fault_pc": fault_pc, "returncode": result.returncode, "stderr": result.stderr}, indent=2) + "\n")
        print(f"PASS {image.stem}: explicit unsupported division edge")
    fixture("lcd-post-half-store", [*literal(0, 0x01c08000), *literal(1, 0x12345678), 0x0681])
    for index, dest in [(0, 3), (1, 0), (4, 1)]:
        state = fixture(f"halfword-index-{index}-dest-{dest}", [*literal(0, 0x01c08000),
                *literal(1, index), *literal(2, 0x89abcdef), 0xedd8, 0x2109,
                0xedd8, (dest << 12) | 0x108])
        expected = [0xa5a5a5a5] * 3
        expected[index // 2] = 0xcdefa5a5 if index & 1 else 0xa5a5cdef
        validate.check(state["registers"][dest] == 0xcdef and
                       state["registers"][2] == 0x89abcdef and
                       state["inspection"][:3] == expected,
                       "indexed halfword load/store scale, alias, extension or neighboring bytes differ")
    for source, index, expected in [(0, 2, 0x8000), (1, 0x8001, 0x8001)]:
        state = fixture(f"halfword-index-store-source-{source}", [*literal(0, 0x01c08000),
                *literal(1, index), 0xedd8, (source << 12) | 0x109, 0xedd8, 0x3108])
        validate.check(state["registers"][3] == expected and
                       state["registers"][:2] == [0x01c08000, index],
                       "indexed halfword store did not use incoming base/index source")
    for kind in [8, 9]:
        image = CACHE / f"halfword-index-unaligned-{kind}.bin"
        words = [*literal(0, 0x01c08001), *literal(1, 2), *literal(2, 0x12345678),
                 0xedd8, 0x2100 | kind]
        image.write_bytes(struct.pack("<" + "H" * len(words), *words) + b"\0" * 16)
        fault_pc = ENTRY + (len(words) - 2) * 2
        env = dict(os.environ, FM1_POC_STOP_PC=hex(ENTRY + len(words) * 2),
                   FM1_POC_MAX_INSTRUCTIONS="1000000")
        result = subprocess.run([*validate.COMMAND, "-kernel", str(image), "-append", "diag"],
                                cwd=validate.ROOT, env=env, capture_output=True, text=True, timeout=15)
        validate.check(result.returncode != 0 and "unaligned access" in result.stderr and
                       f"PC 0x{fault_pc:08x}" in result.stderr,
                       "unaligned indexed halfword operation did not fault at its instruction")
        (CACHE / f"{image.stem}-negative.json").write_text(json.dumps(
            {"fault_pc": fault_pc, "returncode": result.returncode, "stderr": result.stderr}, indent=2) + "\n")
        print(f"PASS {image.stem}: explicit unaligned halfword access")
    for stride, base, dest in [(0, 0, 2), (2, 0, 2), (20, 0, 2), (128, 14, 15), (254, 0, 2)]:
        words = [*literal(0, 0x01c08000), *literal(2, 0xabcd8001), 0x6082]
        if stride:
            words += [*literal(1, stride // 2), *literal(2, 0x76547f02), 0xedd8, 0x2109]
        words += [*literal(4, 0x89abcde5), 0xe064, 0x4580, *literal(base, 0x01c08000),
                  0xedd0, (dest << 12) | ((stride >> 4) << 8) | (base << 4) | (stride & 14),
                  0xedd0, 0x3000 | (base << 4)]
        state = fixture(f"halfword-post-stride-{stride}-base-{base}", words)
        validate.check(state["registers"][dest] == 0x8001 and
                       state["registers"][3] == (0x7f02 if stride else 0x8001) and
                       state["registers"][base] == 0x01c08000 + stride and
                       state["inspection"][0] == (0x7f028001 if stride == 2 else 0xabcd8001) and
                       state["specials"][5] == 0x89abcde5,
                       "post-increment halfword old-base load, stride, zero extension, memory or PSR differ")
    for name, base, operand, reason in [
        ("unaligned", 0x01c08001, 0x2104, "unaligned access"),
        ("same-base-destination", 0x01c08000, 0x0104, "unsupported instruction 0xedd0"),
        ("store", 0x01c08000, 0x2105, "unsupported instruction 0xedd0"),
    ]:
        image = CACHE / f"halfword-post-{name}.bin"
        words = [*literal(0, base), 0xedd0, operand]
        image.write_bytes(struct.pack("<" + "H" * len(words), *words) + b"\0" * 16)
        fault_pc = ENTRY + (len(words) - 2) * 2
        env = dict(os.environ, FM1_POC_STOP_PC=hex(ENTRY + len(words) * 2),
                   FM1_POC_MAX_INSTRUCTIONS="1000000")
        result = subprocess.run([*validate.COMMAND, "-kernel", str(image), "-append", "diag"],
                                cwd=validate.ROOT, env=env, capture_output=True, text=True, timeout=15)
        validate.check(result.returncode != 0 and reason in result.stderr and
                       f"PC 0x{fault_pc:08x}" in result.stderr,
                       "unaligned or unsupported post-increment halfword form did not fault at its instruction")
        (CACHE / f"{image.stem}-negative.json").write_text(json.dumps(
            {"fault_pc": fault_pc, "returncode": result.returncode, "stderr": result.stderr}, indent=2) + "\n")
        print(f"PASS {image.stem}: explicit unaligned or unsupported halfword form")
    for offset, dest in [(0, 3), (232, 0), (254, 3), (256, 3), (510, 3)]:
        operand = (dest << 12) | (((offset & 255) >> 4) << 8) | (offset & 14)
        state = fixture(f"halfword-immediate-{offset}-dest-{dest}", [*literal(0, 0x01c08000),
                *literal(1, offset // 2), *literal(2, 0xabcd8001), 0xedd8, 0x2109,
                0xed50 | (offset >> 8), operand, *literal(4, 0x01c08000 + (offset & ~3)), 0x6045])
        validate.check(state["registers"][dest] == 0x8001 and
                       state["registers"][2] == 0xabcd8001 and
                       state["registers"][5] == (0x8001a5a5 if offset & 2 else 0xa5a58001),
                       "immediate halfword offset, zero extension, base alias or neighbor bytes differ")
    image = CACHE / "halfword-immediate-unaligned.bin"
    words = [*literal(0, 0x01c08001), 0xed51, 0x2f0e]
    image.write_bytes(struct.pack("<" + "H" * len(words), *words) + b"\0" * 16)
    fault_pc = ENTRY + (len(words) - 2) * 2
    env = dict(os.environ, FM1_POC_STOP_PC=hex(ENTRY + len(words) * 2),
               FM1_POC_MAX_INSTRUCTIONS="1000000")
    result = subprocess.run([*validate.COMMAND, "-kernel", str(image), "-append", "diag"],
                            cwd=validate.ROOT, env=env, capture_output=True, text=True, timeout=15)
    validate.check(result.returncode != 0 and "unaligned access" in result.stderr and
                   f"PC 0x{fault_pc:08x}" in result.stderr,
                   "unaligned immediate halfword load did not fault at its instruction")
    (CACHE / f"{image.stem}-negative.json").write_text(json.dumps(
        {"fault_pc": fault_pc, "returncode": result.returncode, "stderr": result.stderr}, indent=2) + "\n")
    print(f"PASS {image.stem}: explicit unaligned halfword access")
    fixture("lcd-pre-byte-store", [*literal(0, 0x01c08000), *literal(1, 0x12345678), 0xee5a, 0x1002])
    for value in [239, 240, 241, 0xffffffff, 520, 1099, 4095]:
        # Vendor literal forms ECB* differ from the pinned SLEIGH packed label.
        threshold = value if value in [520, 1099, 4095] else 240
        fixture(f"conditional-unsigned-le-{value:08x}", [*literal(0, value),
                0xecb0, threshold, 0xe041, 7])
    for threshold, values in [(5, [0, 4, 5, 6, 0x80000000, 0xffffffff]),
                              (4095, [4094, 4095])]:
        for value in values:
            state = fixture(f"conditional-unsigned-lt-{threshold}-{value:08x}",
                    [*literal(5, value), 0xe9b5, 0x1000 | threshold,
                     0xe041, 0x1111, 0xe041, 0x2222, 0xe042, 0x3333])
            validate.check(state["registers"][1] == (0x1111 if value < threshold else 0x2222) and
                           state["registers"][2] == 0x3333,
                           "unsigned less-than block chose the wrong arm or failed to retire")
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
    state = fixture("literal-add-reached-4296", [*literal(3, 0x01c09224), 0xe110, 0x30c8])
    validate.check(state["registers"][0] == 0x01c0a2ec and state["registers"][3] == 0x01c09224 and
                   state["specials"][5] & 15 == 0,
                   "reached E110/30C8 literal add lost upper immediate bits or source")
    for immediate in [-8192, -4097, -4096, -1, 0, 4095, 4096, 4296, 8191]:
        dest = 3 if immediate == 4096 else 0
        encoded = immediate & 0x3fff
        state = fixture(f"literal-add-boundary-{immediate}", [*literal(4, 0x89abcdef), 0xe064, 0x4580,
                *literal(3, 0x12345678), 0xe100 | ((encoded >> 12) << 4) | dest,
                0x3000 | (encoded & 4095)])
        expected = (0x12345678 + immediate) & 0xffffffff
        validate.check(state["registers"][dest] == expected and
                       state["registers"][3] == (expected if dest == 3 else 0x12345678) and
                       state["specials"][5] == (0x89abcde2 if immediate < 0 else 0x89abcde0),
                       "literal add signed range, destination alias, source or preserved PSR bits differ")
    for name, source, dest, value, immediate, expected, flags in [
        ("carry", 0, 0, 0xffffffff, 4096, 0x00000fff, 2),
        ("carry-zero-high-bank", 14, 15, 0xfffff000, 4096, 0, 6),
        ("positive-overflow", 0, 0, 0x7fffffff, 8191, 0x80001ffe, 9),
        ("negative-overflow", 0, 0, 0x80000000, -4096, 0x7ffff000, 3),
        ("negative-result-high-bank", 15, 14, 0, -8192, 0xffffe000, 8),
    ]:
        encoded = immediate & 0x3fff
        state = fixture(f"literal-add-flags-{name}", [*literal(4, 0x89abcdef), 0xe064, 0x4580,
                *literal(source, value), 0xe100 | ((encoded >> 12) << 4) | dest,
                (source << 12) | (encoded & 4095)])
        validate.check(state["registers"][dest] == expected and
                       state["registers"][source] == (expected if source == dest else value) and
                       state["specials"][5] == 0x89abcde0 | flags,
                       "literal add word width, carry/zero/overflow/negative flags or register aliases differ")
    fixture("packed-add", [*literal(0, 0xffffffff), 0xe0e1, 0x0001])
    for index in [0, 31]:
        fixture(f"bit-register-{index}", [*literal(0, 0x81234567), *literal(1, index),
                0xe194, 0x2100, 0xe194, 0x3101, 0xe194, 0x4102, 0xe194, 0x5103])
    for op in [0x1a10, 0x1a90]:
        for count in [0, 31, 32, 0xffffffff]:
            fixture(f"low-shift-{op:04x}-{count:08x}", [*literal(0, 0x81234567), *literal(1, count), op])
    fixture("byte-post-signed", [*literal(0, 0x01c08000), 0xe041, 0x12,
            0xee52, 0x100b, 0xeed4, 0x200b])
    state = fixture("byte-post-unsigned-low-bank", [*literal(0, 0x01c08000),
            *literal(1, 0x007f80ff), 0x6081, *literal(4, 0x01c08000),
            0x0741, 0x0742, 0x0743, 0x0747])
    validate.check(state["registers"][1:4] == [255, 128, 127] and
                   state["registers"][7] == 0 and state["registers"][4] == 0x01c08004 and
                   state["inspection"][0] == 0x007f80ff,
                   "unsigned byte post-increment load width, extension, writeback or memory differs")
    for offset, value in [(0, 0xffffff00), (1, 0x123456ff), (2, 0xabcd1280),
                          (3, 0xdeadbe7f), (4, 0x89abcde5)]:
        state = fixture(f"byte-post-store-offset-{offset}", [*literal(3, 0x01c08000 + offset),
                *literal(4, value), *literal(5, 0x89abcde5), 0xe064, 0x5580, 0x07b4])
        expected = [0xa5a5a5a5] * 3
        shift = (offset & 3) * 8
        expected[offset // 4] = (expected[offset // 4] & ~(255 << shift)) | ((value & 255) << shift)
        validate.check(state["inspection"][:3] == expected and
                       state["registers"][3:5] == [0x01c08001 + offset, value] and
                       state["specials"][5] == 0x89abcde5,
                       "byte post-increment store width, neighbor bytes, source, base or PSR differ")
    for offset, expected in [(1, 0xa5a501a5), (255, 0xffa5a5a5)]:
        address = 0x01c08000 + offset
        state = fixture(f"byte-post-store-source-base-alias-{offset}", [*literal(0, address & ~3),
                *literal(3, address), 0x07b3, 0x6002])
        validate.check(state["registers"][2] == expected and state["registers"][3] == address + 1,
                       "aliased byte store did not write incoming base low byte before increment")
    state = fixture("byte-post-memcpy-sequence", [*literal(0, 0x01c08000),
            *literal(2, 0x007f80ff), 0x6082, *literal(1, 0x01c08000),
            *literal(3, 0x01c08008), 0x0714, 0x07b4, 0x0714, 0x07b4,
            0x0714, 0x07b4, 0x0714, 0x07b4])
    validate.check(state["inspection"][:4] == [0x007f80ff, 0xa5a5a5a5, 0x007f80ff, 0xa5a5a5a5] and
                   state["registers"][1] == 0x01c08004 and state["registers"][3:5] == [0x01c0800c, 0],
                   "reached memcpy byte load/store sequence altered bytes or pointer progression")
    state = fixture("byte-post-store-head-incoming-source", [*literal(3, 0x01c08001),
            *literal(4, 0x89abcdef), 0xc7b4, 0xe044, 0x1234])
    validate.check(state["inspection"][0] == 0xa5a5efa5 and
                   state["registers"][3:5] == [0x01c08002, 0x1234],
                   "parallel head byte store used overwritten source or incorrect base")
    state = fixture("byte-post-store-tail-incoming-source", [*literal(0, 0x12345678),
            *literal(3, 0x01c08001), *literal(4, 0x89abcdef), 0xd604, 0x07b4])
    validate.check(state["inspection"][0] == 0xa5a5efa5 and
                   state["registers"][3:5] == [0x01c08002, 0x12345678],
                   "parallel byte store incorrectly writes source or lost incoming source")
    state = fixture("byte-post-store-bundle-incoming-base", [*literal(3, 0x01c08000),
            *literal(4, 0x123456ff), 0xd636, 0x07b4])
    validate.check(state["inspection"][0] == 0xa5a5a5ff and
                   state["registers"][3] == 0x01c08001 and state["registers"][6] == 0x01c08000,
                   "parallel head consumed incremented byte-store base")
    for head, expected in [(0xd606, 0x12345678), (0xd646, 0x01c08000), (0xd616, 0x11223344)]:
        state = fixture(f"byte-post-unsigned-bundle-{head:04x}", [*literal(0, 0x01c08000),
                *literal(2, 0x89abcdef), 0x6082, *literal(4, 0x01c08000),
                *literal(0, 0x12345678), *literal(1, 0x11223344), head, 0x0741])
        validate.check(state["registers"][6] == expected and state["registers"][1] == 0xef and
                       state["registers"][4] == 0x01c08001,
                       "parallel byte post-increment load did not preserve incoming base/source")
    for head, initial, expected, flags in [(0xd805, 0xffffffff, 0, 6),
                                         (0xd815, 0x100, 0x102, 0),
                                         (0xd845, 1, 0x01c08001, 0)]:
        state = fixture(f"add-byte-post-bundle-{head:04x}", [*literal(0, 0x01c08000),
                *literal(2, 0x89abcdef), 0x6082, *literal(4, 0x01c08000),
                *literal(0, 1), *literal(1, 2), *literal(5, initial), head, 0x0741])
        validate.check(state["registers"][5] == expected and state["registers"][1] == 0xef and
                       state["registers"][4] == 0x01c08001 and
                       state["specials"][5] & 15 == flags,
                       "parallel ADD result, incoming byte-load source/base or flags differ")
    state = fixture("add-high-bank-bundle-incoming-source", [*literal(14, 0x7fffffff),
            *literal(15, 1), 0xd8fe, 0xe04f, 0x4444])
    validate.check(state["registers"][14:16] == [0x80000000, 0x4444] and
                   state["specials"][5] & 15 == 9,
                   "high-bank parallel ADD did not preserve incoming source or overflow flags")
    fixture("byte-pre-unsigned", [*literal(0, 0x01c08000), 0xe041, 0x12,
            0xee52, 0x100b, 0xee58, 0x200b])
    for value in [0, 1, 0x80000000]:
        fixture(f"register-mask-branch-{value:08x}", [*literal(0, value), *literal(1, 1),
                0xfb10, 1, 0x2242])
    for kind in ["direct", "short", "register"]:
        for arm in ["then", "else"]:
            for condition in [0, 1]:
                conditional_call(kind, arm, condition)
        conditional_call(kind, "then", 0, has_else=False)
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
