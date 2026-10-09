#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""List the instruction forms in firmware images that the emulator rejects.

    python tools/decode_sweep.py FIRMWARE [FIRMWARE ...] [--output FILE]

Each image (raw application .bin, or .fwsc/.ufw package) is disassembled with
the JieLi toolchain's objdump (Linux x86-64, run in Docker on macOS; set
JIELI_TOOLCHAIN). Every instruction it decodes is then run through the
emulator's own decoder, translate.c compiled unchanged against no-op TCG
stand-ins (tools/decode-sweep/). Rejected forms are grouped by opcode with
vendor disassembly, occurrence counts, static reachability and accepted
sibling forms, the context needed to implement them.
"""
import argparse
import collections
import ctypes
import datetime
import hashlib
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parent.parent
SWEEP = ROOT / "tools/decode-sweep"
CACHE = ROOT / ".cache/decode-sweep"
BASE = 0x02000120
NOR_SIZE = 0x100000
LINE = re.compile(r"^ ([0-9a-f]+):\s+((?:[0-9a-f]{2} )+)\s*\t(.*)$")
TARGET = re.compile(r"<[^>]*: ([0-9a-f]+) >")


def build(tool, sources, *flags):
    if not tool.exists() or any(tool.stat().st_mtime < s.stat().st_mtime for s in sources):
        tool.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run([os.environ.get("CC", "cc"), *flags, *map(str, sources[:1]), "-o", str(tool)],
                       check=True)
    return tool


def application(path):
    """Raw application bytes as the board maps them at BASE."""
    data = path.read_bytes()
    if path.suffix.lower() not in (".fwsc", ".ufw"):
        return data
    lib = build(CACHE / "image.so", [ROOT / "src/hw/pi32v2/fm1-image.c"],
                "-std=c11", "-DFM1_IMAGE_STANDALONE", "-shared", "-fPIC")
    decode = ctypes.CDLL(str(lib)).fm1_image_decode
    decode.argtypes = [ctypes.c_void_p, ctypes.c_size_t, ctypes.c_bool, ctypes.c_void_p,
                       ctypes.c_size_t, ctypes.c_void_p, ctypes.c_size_t]
    decode.restype = ctypes.c_bool
    nor, error = ctypes.create_string_buffer(NOR_SIZE), ctypes.create_string_buffer(256)
    if not decode(ctypes.create_string_buffer(data), len(data), True, nor, NOR_SIZE, error, 256):
        raise SystemExit(f"{path}: {error.value.decode()}")
    app = nor.raw[0x4120:]
    return app[:len(app.rstrip(b"\xff"))]


def disassemble(app, toolchain):
    digest = hashlib.sha256(app).hexdigest()
    cached = CACHE / f"{digest}.dis"
    if cached.exists():
        return cached.read_text()
    if not (toolchain / "common/bin/objdump").exists():
        raise SystemExit("set JIELI_TOOLCHAIN to a JieLi Linux toolchain (tools/get_toolchain.sh in Felucca)")
    with tempfile.TemporaryDirectory(dir="/tmp") as work:
        work = Path(work)
        (work / "raw.bin").write_bytes(app)
        (work / "raw.S").write_text('.section .text,"ax"\n.global _start\n_start:\n.incbin "/w/raw.bin"\n')
        (work / "raw.ld").write_text(f"ENTRY(_start)\nSECTIONS {{ . = {BASE:#x}; .text : {{ *(.text) }} }}\n")
        script = ("/tc/pi32v2/bin/clang -target pi32v2 -c /w/raw.S -o /w/raw.o && "
                  "/tc/pi32v2/bin/ld -T /w/raw.ld /w/raw.o -o /w/raw.elf && "
                  "/tc/common/bin/objdump -d /w/raw.elf")
        native = sys.platform == "linux" and os.uname().machine == "x86_64"
        command = (["sh", "-c", script.replace("/tc", str(toolchain)).replace("/w", str(work))] if native else
                   ["docker", "run", "--rm", "--platform", "linux/amd64", "-v", f"{toolchain}:/tc:ro",
                    "-v", f"{work}:/w", "debian:bookworm-slim", "sh", "-c", script])
        text = subprocess.run(command, check=True, capture_output=True, text=True).stdout
    CACHE.mkdir(parents=True, exist_ok=True)
    cached.write_text(text)
    return text


def parse(text):
    """Instructions keyed by address: (raw bytes, vendor text)."""
    code = {}
    for line in text.splitlines():
        m = LINE.match(line)
        if m and "<unknown instruction>" not in m.group(3):
            code[int(m.group(1), 16)] = (bytes.fromhex(m.group(2)), m.group(3).strip())
    return code


def reachable(code, app):
    """Static control flow from the entry and from function pointers stored in
    the image: words that address a function prologue ([--sp] = {rets, ...})."""
    end = BASE + len(app)
    roots = {BASE}
    for offset in range(0, len(app) - 3, 4):
        word = int.from_bytes(app[offset:offset + 4], "little")
        if BASE <= word < end and not word & 1 and word in code and \
                code[word][1].startswith("[--sp] = {rets"):
            roots.add(word)
    seen, work = set(), list(roots)
    while work:
        address = work.pop()
        while address in code and address not in seen:
            seen.add(address)
            raw, text = code[address]
            m = TARGET.search(text)
            if m and ("goto" in text or text.startswith("call")):
                work.append(int(m.group(1), 16))
            words = text.split()
            # Unconditional transfers end a path; a table branch (tbb, tbh) is
            # followed by its table, which is data.
            if (text.startswith(("goto", "rts", "rti", "pc =", "tbb", "tbh")) or "{pc" in text) \
                    and "if" not in words[:1]:
                break
            address += len(raw)
    return seen


def sweep(app, addresses):
    tool = build(CACHE / "sweep", [SWEEP / "sweep.c", ROOT / "src/target/pi32v2/translate.c",
                                   ROOT / "src/target/pi32v2/cpu.h"],
                 "-std=gnu11", "-w", f"-I{SWEEP / 'include'}", f"-I{ROOT / 'src/target/pi32v2'}")
    with tempfile.NamedTemporaryFile(suffix=".bin") as image:
        image.write(app)
        image.flush()
        out = subprocess.run([str(tool), image.name, hex(BASE)], check=True, capture_output=True,
                             text=True, input="".join(f"{a:x}\n" for a in sorted(addresses))).stdout
    result = {}
    for line in out.splitlines():
        address, size, verdict, op = line.split()
        result[int(address, 16)] = (int(size), verdict, int(op, 16))
    return result


def operands(raw):
    words = [int.from_bytes(raw[i:i + 2], "little") for i in range(0, len(raw) - 1, 2)]
    return " ".join(f"{w:04x}" for w in words)


def report(name, path, app, code, live, verdicts):
    rejected = collections.defaultdict(list)
    guarded = collections.defaultdict(list)
    accepted = collections.defaultdict(list)
    for address, (size, verdict, op) in verdicts.items():
        raw, text = code[address]
        first = int.from_bytes(raw[:2], "little")
        entry = (address, raw, text, address in live)
        if verdict == "reject":
            rejected[op].append(entry)
        elif verdict == "guarded":
            guarded[first].append(entry)
        else:
            accepted[first & 0xfff0].append((first, raw, text))
    lines = [f"## {name}", "",
             f"`{path.name}`, application SHA-256 `{hashlib.sha256(app).hexdigest()[:16]}...`, "
             f"{len(app):,} bytes, {len(code):,} decoded instructions of which {len(live):,} statically "
             f"reachable.", ""]
    order = sorted(rejected, key=lambda op: (-sum(e[3] for e in rejected[op]), -len(rejected[op]), op))
    reached = [op for op in order if any(e[3] for e in rejected[op])]
    lines += [f"{len(order)} rejected opcodes ({sum(len(rejected[op]) for op in order)} occurrences), "
              f"{len(reached)} of them in statically reachable code.", ""]
    if reached:
        lines += ["| Opcode | Occurrences | Reachable | Example | Vendor disassembly |",
                  "| --- | --- | --- | --- | --- |"]
        for op in reached:
            entries = rejected[op]
            live_count = sum(e[3] for e in entries)
            best = sorted(entries, key=lambda e: (not e[3], e[0]))[0]
            lines.append(f"| `{op:04x}` | {len(entries)} | {live_count} | `{best[0]:08x}` | "
                         f"`{best[2].replace('|', '/')}` |")
        lines.append("")
    unreached = [op for op in order if op not in reached]
    if unreached:
        lines += [f"{len(unreached)} further opcodes ({sum(len(rejected[op]) for op in unreached)} "
                  "occurrences) appear only outside reachable code, mostly data decoded as instructions; "
                  "they are not listed.", ""]
    for op in reached:
        entries = sorted(rejected[op], key=lambda e: (not e[3], e[0]))
        live_count = sum(e[3] for e in entries)
        lines += [f"### `{op:04x}`", ""]
        sizes = sorted({len(e[1]) for e in entries})
        lines.append(f"{len(entries)} occurrences, {live_count} reachable; {'/'.join(map(str, sizes))} bytes.")
        parallel = [e for e in entries if int.from_bytes(e[1][:2], "little") != op]
        if parallel:
            heads = sorted({int.from_bytes(e[1][:2], 'little') for e in parallel})
            lines.append("Inside parallel instructions headed " + ", ".join(f"`{h:04x}`" for h in heads[:6]) + ".")
        lines += ["", "| Address | Reachable | Halfwords | Vendor disassembly |", "| --- | --- | --- | --- |"]
        shown, forms = 0, set()
        for address, raw, text, live_flag in entries:
            form = operands(raw)
            if form in forms:
                continue
            forms.add(form)
            lines.append(f"| `{address:08x}` | {'yes' if live_flag else 'no'} | `{form}` | "
                         f"`{text.replace('|', '/')}` |")
            shown += 1
            if shown == 8:
                break
        siblings = {}
        for first, raw, text in accepted.get(op & 0xfff0, []):
            siblings.setdefault(first, (raw, text))
        if siblings:
            lines += ["", "Accepted forms with the same top 12 bits: " + "; ".join(
                f"`{operands(raw)}` `{text.replace('|', '/')}`" for _, (raw, text) in sorted(siblings.items())[:6]) + "."]
        lines.append("")
    if guarded:
        lines += ["### Runtime-guarded forms", "",
                  "Accepted, but fault for some register values (a bit index of 32 or more, a REP body "
                  "that is not a qualified linear block when the count is nonzero).", "",
                  "| Opcode | Occurrences | Reachable | Example | Vendor disassembly |", "| --- | --- | --- | --- | --- |"]
        for first in sorted(f for f in guarded if any(e[3] for e in guarded[f])):
            entries = sorted(guarded[first], key=lambda e: (not e[3], e[0]))
            lines.append(f"| `{first:04x}` | {len(entries)} | {sum(e[3] for e in entries)} | "
                         f"`{entries[0][0]:08x}` | `{entries[0][2].replace('|', '/')}` |")
        lines.append("")
    return lines


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("firmware", nargs="+", type=Path)
    parser.add_argument("--output", type=Path, default=ROOT / "docs/instruction-gaps.md")
    parser.add_argument("--toolchain", type=Path,
                        default=Path(os.environ.get("JIELI_TOOLCHAIN", Path.home() / ".jieli/toolchain")))
    args = parser.parse_args()
    if not shutil.which("docker") and sys.platform != "linux":
        raise SystemExit("the JieLi toolchain needs Docker on this host")
    sections = []
    for path in args.firmware:
        app = application(path)
        code = parse(disassemble(app, args.toolchain))
        live = reachable(code, app)
        sections += report(path.stem, path, app, code, live, sweep(app, code))
        print(f"{path.name}: swept {len(code)} instructions", file=sys.stderr)
    head = (ROOT / "tools/decode-sweep/header.md").read_text()
    args.output.write_text(head.replace("@DATE@", datetime.date.today().isoformat()) + "\n" + "\n".join(sections))
    print(f"wrote {args.output}", file=sys.stderr)


if __name__ == "__main__":
    main()
