#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Focused synthetic guest gates for diagnostic peripherals; cache only.

These disposable fixtures exercise the private diagnostic machine interface.
They do not modify the saved diagnostic or establish complete hardware support.
Encoding facts come from the pinned Apache SLEIGH and saved vendor disassembly;
no Rust device implementation is imported or used as an in-process oracle.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import struct
import subprocess

import validate

HERE = Path(__file__).resolve().parent
CACHE = HERE / ".cache/peripheral-validation"
ENTRY = 0x02000120
RAM = 0x01c00000
INSPECTION = 0x01c08000
SFC = 0x40200
SPI = 0x11c00
IOMAP = 0x5101c
PD_OUT = 0x500c0
USB = 0x11800
REQUESTS = [0x160, 0xb07, 0x701, 0x800, 0x900, 0xa00]


class Guest:
    """Emit only the small, already accepted instruction forms these gates need."""

    def __init__(self, base=ENTRY):
        self.base = base
        self.words = []
        self.instructions = 0

    @property
    def pc(self):
        return self.base + len(self.words) * 2

    def emit(self, *words):
        self.words.extend(words)
        self.instructions += 1

    def literal(self, reg, value, special=False):
        value &= 0xffffffff
        self.emit((0xffe0 if special else 0xffc0) | reg,
                  value & 0xffff, value >> 16)

    def load(self, reg, base, offset=0):
        assert 0 <= reg < 8 and 0 <= base < 8 and offset % 4 == 0
        assert -64 <= offset <= 60
        self.emit(0x6000 | reg | (base << 4) | (((offset // 4) & 31) << 8))

    def store(self, reg, base, offset=0):
        assert 0 <= reg < 8 and 0 <= base < 8 and offset % 4 == 0
        assert -64 <= offset <= 60
        self.emit(0x6080 | reg | (base << 4) | (((offset // 4) & 31) << 8))

    def add(self, reg, value):
        assert 0 <= reg < 8 and -128 <= value <= 127
        value &= 255
        self.emit(0x20c0 | reg | ((value & 31) << 8) | ((value >> 5) << 3))

    def branch_zero(self, reg, target, nonzero=False):
        delta = target - (self.pc + 2)
        assert 0 <= reg < 8 and delta % 2 == 0 and -256 <= delta <= 254
        delta &= 511
        self.emit(0x4000 | reg | (0x80 if nonzero else 0) |
                  (((delta >> 1) & 31) << 8) | (((delta >> 6) & 7) << 4))

    def write(self, address, value):
        # r0 and r1 are scratch; all accesses are guest word stores.
        self.literal(1, address)
        self.literal(0, value)
        pc = self.pc
        self.store(0, 1)
        return pc

    def bytes(self):
        return struct.pack("<" + "H" * len(self.words), *self.words)


def ram_call(body, warm_xip=False):
    guest = Guest()
    if warm_xip:
        guest.literal(4, ENTRY)
        guest.load(5, 4)  # Populate the same XIP data translation before disable.
    data = body.bytes()
    data += b"\0" * (-len(data) % 4)
    guest.literal(1, RAM)
    for word in struct.unpack("<" + "I" * (len(data) // 4), data):
        guest.literal(0, word)
        guest.store(0, 1)
        guest.add(1, 4)
    guest.literal(7, RAM)
    guest.emit(0x00c7)  # call r7; the SPI protocol runs from actual copied SRAM.
    return guest


def run(name, guest, error=None, fault_pc=None, expected=None, instructions=None):
    image = CACHE / f"{name}.bin"
    data = guest.bytes() + b"\0" * 16
    image.write_bytes(data)
    env = dict(os.environ, FM1_POC_STOP_PC=hex(guest.pc),
               FM1_POC_MAX_INSTRUCTIONS="2000000")
    result = subprocess.run([*validate.COMMAND, "-kernel", str(image), "-append", "diag"],
                            cwd=validate.ROOT, env=env, capture_output=True,
                            text=True, timeout=30)
    record = {"fixture_sha256": hashlib.sha256(data).hexdigest(),
              "stop_pc": guest.pc, "returncode": result.returncode}
    if error:
        validate.check(result.returncode != 0 and error in result.stderr,
                       f"{name}: expected explicit fault {error!r}; got {result.stderr}")
        match = re.search(r"at PC 0x([0-9a-f]+) after (\d+) instructions", result.stderr)
        validate.check(match is not None, f"{name}: missing guest fault location")
        if fault_pc is not None:
            validate.check(int(match[1], 16) == fault_pc,
                           f"{name}: fault occurred at another instruction: {result.stderr}")
        record.update(diagnostic=result.stderr.strip(), fault_pc=int(match[1], 16),
                      guest_instructions=int(match[2]))
    else:
        validate.check(result.returncode == 0, f"{name}: {result.stderr}")
        state = json.loads(result.stdout)
        validate.check(state["pc"] == guest.pc, f"{name}: wrong guest stop")
        for field, value in (expected or {}).items():
            actual = state[field]
            if isinstance(value, dict):
                for index, item in value.items():
                    validate.check(actual[index] == item, f"{name}: {field}[{index}] differs")
            else:
                validate.check(actual == value, f"{name}: {field} differs")
        if instructions is not None:
            validate.check(state["instructions"] == instructions,
                           f"{name}: loop retirement count differs")
        record["snapshot"] = state
    (CACHE / f"{name}.json").write_text(json.dumps(record, indent=2) + "\n")
    print(f"PASS {name}")
    return record


def system_cases():
    records = {}
    for valid in [True, False]:
        guest = Guest()
        guest.literal(14, INSPECTION + 4, special=True)
        guest.write(0x01eef0e4, INSPECTION if valid else INSPECTION + 4)  # USP low
        guest.write(0x01eef0e0, INSPECTION + 4)  # USP high
        guest.write(0x01eef0d0, 8)  # Stack range checking enabled.
        guest.literal(0, 0x11223344)
        push_pc = guest.pc
        guest.emit(0xe8d8, 1)  # push {r0}: SP decrements before the memory access.
        name = "stack-push-boundary" if valid else "stack-push-outside"
        records[name] = run(name, guest,
                            error=None if valid else "guest stack pointer lies outside",
                            fault_pc=None if valid else push_pc,
                            expected={"inspection": {0: 0x11223344},
                                      "specials": {14: INSPECTION}} if valid else None)
    # A word beginning below a one-byte protected range must still intersect it.
    for enabled in [False, True]:
        guest = Guest()
        guest.write(0x01eee240, 0xe7)
        guest.write(0x01eee2c0, INSPECTION + 2)
        guest.write(0x01eee280, INSPECTION + 2)
        guest.write(0x01eee348, int(enabled))
        store_pc = guest.write(INSPECTION, 0x55667788)
        name = "write-guard-disabled" if not enabled else "write-guard-crossing"
        records[name] = run(name, guest,
                            error="CPU write intersects an enabled guest guard window" if enabled else None,
                            fault_pc=store_pc if enabled else None,
                            expected=None if enabled else {"inspection": {0: 0x55667788}})
    return records


def spi_poll(guest):
    guest.literal(1, SPI)
    guest.literal(2, 0x8000)
    loop = guest.pc
    guest.load(3, 1)
    guest.emit(0x19a3)  # r3 &= r2 (pending bit).
    guest.branch_zero(3, loop)


def spi_start(guest):
    guest.write(SFC, 0)
    guest.write(IOMAP, 0)
    guest.write(PD_OUT, 0)
    guest.write(SPI, 0x29)


def nor_cases():
    records = {}
    # Warm an actual target TB, return to SRAM, change SFC, and call that same
    # target again. This also works with the diagnostic's one-instruction TBs.
    target_pc = ENTRY + 0x400
    exit_pc = ENTRY + 0x500
    for enabled in [True, False]:
        body = Guest(RAM)
        body.literal(6, 0)
        body.literal(7, target_pc)
        body.emit(0x00c7)
        body.write(SFC, int(enabled))
        body.emit(0x00c7)
        body.literal(7, exit_pc)
        body.emit(0x00c7)
        guest = ram_call(body)
        assert guest.pc <= target_pc
        guest.words.extend([0] * ((target_pc - guest.pc) // 2))
        guest.add(6, 1)
        guest.emit(0x0080)
        guest.words.extend([0] * ((exit_pc - guest.pc) // 2))
        name = "cached-xip-enabled" if enabled else "cached-xip-fetch-disabled"
        records[name] = run(name, guest,
                            error=None if enabled else "XIP access while SFC is disabled or unrouted",
                            fault_pc=None if enabled else target_pc,
                            expected={"registers": {6: 2}} if enabled else None)
    body = Guest(RAM)
    body.write(SFC, 0)
    load_pc = body.pc
    body.load(6, 4)
    body.emit(0x0080)
    records["cached-xip-data-disabled"] = run(
        "cached-xip-data-disabled", ram_call(body, warm_xip=True),
        error="XIP access while SFC is disabled or unrouted", fault_pc=load_pc)

    # Positive protocol control: request idle status, poll real pending, receive
    # one byte, acknowledge it, release CS and restore XIP before returning.
    body = Guest(RAM)
    spi_start(body)
    body.write(SPI + 8, 0x05)
    spi_poll(body)
    body.write(SPI, 0x5029)
    body.write(SPI + 8, 0xff)
    spi_poll(body)
    body.load(6, 1, 8)
    body.write(SPI, 0x5029)
    body.write(PD_OUT, 1)
    body.write(SPI, 0)
    body.write(IOMAP, 0x20)
    body.write(SFC, 1)
    body.emit(0x0080)
    records["nor-status-protocol"] = run("nor-status-protocol", ram_call(body),
                                          expected={"registers": {6: 0}})
    for name, command, error in [
        ("nor-unknown-command", 0x99, "unsupported NOR command"),
        ("nor-write-enable", 0x06, "NOR program/erase/write-enable is unimplemented"),
    ]:
        body = Guest(RAM)
        spi_start(body)
        body.write(SPI + 8, command)
        spi_poll(body)
        body.emit(0x0080)
        # Command rejection occurs on the virtual transfer timer; its exact
        # polling PC depends on the timer boundary, so only the reason is fixed.
        records[name] = run(name, ram_call(body), error=error)
    return records


def usb_cases():
    records = {}
    guest = Guest()
    guest.write(USB, 0x3d)
    guest.literal(2, USB + 4)
    guest.literal(5, 0x8000)  # SIE DONE bit.
    guest.literal(6, 0)
    guest.literal(7, INSPECTION)
    for index, request in enumerate(REQUESTS):
        guest.literal(0, request)
        guest.store(0, 2)
        guest.literal(1, -20000)
        loop = guest.pc
        guest.load(3, 2)
        guest.emit(0x1634)  # r4 = r3, preserving the actual bridge request.
        guest.emit(0x19d4)  # r4 &= r5.
        guest.emit(0x1946)  # r6 |= r4: observe DONE on every guest poll.
        guest.add(1, 1)
        guest.branch_zero(1, loop, nonzero=True)
        guest.store(3, 7, index * 4)
    guest.store(6, 7, 24)
    retired = guest.instructions + len(REQUESTS) * 6 * (20000 - 1)
    records["usb-cold-six-requests"] = run(
        "usb-cold-six-requests", guest,
        expected={"inspection": dict(enumerate([*REQUESTS, 0]))}, instructions=retired)
    records["usb-cold-six-requests"].update(
        guest_bridge_reads=120000, requests=REQUESTS,
        scope="host absent, unavailable SIE clock, no DONE; no configured USB/CDC claim")
    for name, enabled, request in [
        ("usb-unknown-request", True, 0x200),
        ("usb-sie-disabled", False, REQUESTS[0]),
    ]:
        guest = Guest()
        guest.write(USB, 0x3d if enabled else 0)
        store_pc = guest.write(USB + 4, request)
        records[name] = run(name, guest, error="unsupported USB SIE bridge request",
                            fault_pc=store_pc)
    return records


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("group", choices=["all", "system", "nor", "usb"],
                        default="all", nargs="?")
    args = parser.parse_args()
    CACHE.mkdir(parents=True, exist_ok=True)
    records = {}
    for name, cases in [("system", system_cases), ("nor", nor_cases), ("usb", usb_cases)]:
        if args.group in ["all", name]:
            records[name] = cases()
    summary = {"synthetic_guests": True, "unchanged_diagnostic_modified": False,
               "stack_failure_claim": "immediate fault at push PC; no post-fault RAM observation",
               "groups": records}
    (CACHE / f"summary-{args.group}.json").write_text(json.dumps(summary, indent=2) + "\n")


if __name__ == "__main__":
    main()
