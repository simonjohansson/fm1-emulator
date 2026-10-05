#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Verify unchanged diagnostic startup through guest protection setup."""
import hashlib
import json
import os
from pathlib import Path
import subprocess

import validate
import validate_isa

HERE = Path(__file__).resolve().parent
CACHE = HERE / ".cache/diag-validation"
DIS_SHA = "c7e14d50882ac2a9878f5b4d2c81e01cdee594ce2c070aa699d70d03fd8f71d3"
RAM_SHA = "09c143e0726a4ac39dbd6f5f964caf2af65035f45e3dbdb61cd287b0157afede"


def main():
    CACHE.mkdir(parents=True, exist_ok=True)
    validate_isa.CACHE.mkdir(parents=True, exist_ok=True)
    image = validate.ROOT / "build/fm1-diag.bin"
    raw = image.read_bytes()
    validate.check(hashlib.sha256(raw).hexdigest() == validate_isa.DIAG_SHA, "diagnostic image differs")
    validate.check(hashlib.sha256((validate.ROOT / "build/fm1-diag.dis").read_bytes()).hexdigest() == DIS_SHA,
                   "diagnostic disassembly differs")
    summary = {"entry": 0x02000120, "binary_sha256": validate_isa.DIAG_SHA,
               "disassembly_sha256": DIS_SHA, "sram_initialization": "startup regions and mailbox A5; persistent regions zero",
               "checkpoints": {}}
    os.environ["FM1_POC_STATE_DIR"] = str(CACHE)
    try:
        for name, stop in [("copies", 0x02001f3c), ("guards", 0x0200200a)]:
            qemu = validate_isa.compare(f"diagnostic-{name}", image, stop, exact_count=False)
            ram = (CACHE / f"state-{stop:08x}.sram").read_bytes()
            validate.check(ram[:0xb48] == raw[0x2e78:0x39c0] and
                           hashlib.sha256(ram[:0xb48]).hexdigest() == RAM_SHA, "guest RAM-text copy differs")
            validate.check(ram[0x8000:0x8014] == raw[0x39c0:0x39d4], "guest data copy differs")
            expected_bss = bytearray(0x1ce0)
            if name == "guards":
                # Startup restores the reset reason after clearing BSS.
                expected_bss[0x1664] = 1
            validate.check(ram[0x8020:0x9d00] == expected_bss, "guest BSS/reset reason differs")
            mailbox = ram[0x7fd80:0x7fe00]
            validate.check(mailbox == (b"\xa5" * 128 if name == "copies" else b"\0" * 128), "mailbox clear differs")
            validate.check(qemu["p33_transfers"] == 18 and qemu["p33_transactions"] == 6 and
                           qemu["watchdog_arms"] == qemu["watchdog_feeds"] == 1 and
                           qemu["watchdog_expirations"] == 0, "P33/watchdog startup differs")
            validate.check(qemu["specials"][14] == 0x01c79ef0 and qemu["specials"][13] == 0x01c7c000,
                           "startup stack state differs")
            validate.check(qemu["virtual_ns"] == (qemu["instructions"] + 1) * 8, "diagnostic icount differs")
            if name == "guards":
                validate.check(qemu["emu_control"] == 12 and qemu["write_enable"] == 3 and
                               qemu["debug_enable"] == 0x3f0030 and qemu["guard_checks"] > 13000,
                               "guest did not enable stack/write/PC guards")
            summary["checkpoints"][name] = qemu
    finally:
        del os.environ["FM1_POC_STATE_DIR"]
    (CACHE / "startup.json").write_text(json.dumps(summary, indent=2) + "\n")
    print("PASS diagnostic startup: poisoned BSS/data/RAM-text and mailbox, timed P33, watchdog and enforced guards")


if __name__ == "__main__":
    main()
