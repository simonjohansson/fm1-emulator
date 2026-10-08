# SPDX-License-Identifier: GPL-2.0-or-later
"""Small synthetic guests and helpers for the native emulator regressions."""

import json
import os
from pathlib import Path
import struct
import subprocess

ROOT = Path(__file__).resolve().parent.parent
QEMU = ROOT / "emulator"
COMMAND = [str(QEMU), "--qemu", "-M", "fm1-poc", "-accel", "tcg,thread=single",
           "-icount", "shift=3,align=off,sleep=off", "-display", "none",
           "-serial", "none", "-monitor", "none", "-nodefaults", "-no-user-config"]
ENTRY = 0x02000120
INSPECTION = 0x01c08000
USB = 0x11800


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


def check(condition, message):
    if not condition:
        raise AssertionError(message)


def environment(**settings):
    clean = {key: value for key, value in os.environ.items()
             if not key.startswith("FM1_POC_")}
    clean.update(settings)
    return clean


def run_guest(directory, guest):
    image = directory / "guest.bin"
    image.write_bytes(guest.bytes() + bytes(16))
    return subprocess.run(
        [*COMMAND, "-kernel", str(image), "-append", "diag"],
        cwd=ROOT, env=environment(FM1_POC_STOP_PC=hex(guest.pc),
                                  FM1_POC_MAX_INSTRUCTIONS="10000"),
        capture_output=True, text=True, timeout=30)


def guest_state(directory, guest):
    result = run_guest(directory, guest)
    check(result.returncode == 0, result.stderr)
    return json.loads(result.stdout)
