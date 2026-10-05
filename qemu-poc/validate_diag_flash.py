#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Observe the unchanged diagnostic's RAM flash driver and LCD initialization."""
import hashlib
import json
import os
from pathlib import Path

import validate
import validate_diag_startup
import validate_isa

HERE = Path(__file__).resolve().parent
CACHE = HERE / ".cache/diag-flash-validation"


def main():
    CACHE.mkdir(parents=True, exist_ok=True)
    validate_isa.CACHE.mkdir(parents=True, exist_ok=True)
    image = validate.ROOT / "build/fm1-diag.bin"
    raw = image.read_bytes()
    validate.check(hashlib.sha256(raw).hexdigest() == validate_isa.DIAG_SHA, "diagnostic image differs")
    validate.check(hashlib.sha256((validate.ROOT / "build/fm1-diag.dis").read_bytes()).hexdigest() ==
                   validate_diag_startup.DIS_SHA, "diagnostic disassembly differs")
    summary = {"entry": 0x02000120, "binary_sha256": validate_isa.DIAG_SHA,
               "nor_seed": "1 MiB erased FF; unchanged raw application at physical 0x4120",
               "lcd_init_comparison": "r1 is the fm1_lcd_wait SPI poll count; clocks differ. All other registers and specials match.",
               "checkpoints": {}}
    os.environ["FM1_POC_STATE_DIR"] = str(CACHE)
    try:
        for name, stop in [("jedec", 0x02002038), ("erased_headers", 0x020020da),
                           ("lcd_init", 0x0200218e)]:
            state = validate_isa.compare(f"diagnostic-{name}", image, stop,
                                         exact_count=False, limit=50000000,
                                         # fm1_lcd_wait leaves its timed SPI poll count in r1.
                                         polling_registers=(1,) if name == "lcd_init" else ())
            ram = (CACHE / f"state-{stop:08x}.sram").read_bytes()
            validate.check(ram[:0xb48] == raw[0x2e78:0x39c0], "RAM flash driver was changed")
            validate.check(ram[0x9694] == 1, "guest flash_ok was not set after JEDEC")
            validate.check(state["specials"][14] == 0x01c79ef0 and
                           state["specials"][13] == 0x01c7c000, "flash driver did not restore stacks")
            validate.check(state["virtual_ns"] == (state["instructions"] + 1) * 8,
                           "diagnostic icount differs")
            nor = state["nor"]
            validate.check(nor["xip_enabled"] and not nor["busy"] and not nor["selected"],
                           "flash driver did not restore XIP/deassert CS")
            expected = {"transactions": 1, "jedec_commands": 1, "status_commands": 0,
                        "read_commands": 0, "transfers": 4, "completed_transfers": 4,
                        "acknowledgments": 4, "received_bytes": 3, "read_bytes": 0,
                        "sfc_disables": 1, "sfc_restores": 1}
            if name != "jedec":
                expected.update(transactions=3, read_commands=2, transfers=26,
                                completed_transfers=26, acknowledgments=26,
                                received_bytes=15, read_bytes=12, sfc_disables=3, sfc_restores=3)
            for field, value in expected.items():
                validate.check(nor[field] == value, f"{name}: NOR {field} differs")
            if name == "erased_headers":
                validate.check(ram[0x79f64:0x79f68] == b"\xff" * 4 and
                               ram[0x79f5c:0x79f64] == b"\xff" * 8,
                               "guest did not receive the erased four/eight-byte headers")
                validate.check(state["registers"][0] == 0, "guest selected flash modification path")
            if name == "lcd_init":
                validate.check(state["registers"][1] < 4000000 and ram[0x8028:0x802c] == b"\0" * 4,
                               "LCD wait reached its guest timeout")
                validate.check(state["lcd"] == {"visible": False, "busy": False, "pixels_written": 0,
                               "commands": 6, "dma_transfers": 2, "completed_transfers": 8},
                               "LCD initialization transfer evidence differs")
                validate.check(state["usb"]["requests"] == 0, "USB initialized before expected stage")
            summary["checkpoints"][name] = state
    finally:
        del os.environ["FM1_POC_STATE_DIR"]
    (CACHE / "flash-and-lcd-init.json").write_text(json.dumps(summary, indent=2) + "\n")
    print("PASS diagnostic RAM flash driver: JEDEC 856014, two erased-header reads, restored XIP and LCD initialization")


if __name__ == "__main__":
    main()
