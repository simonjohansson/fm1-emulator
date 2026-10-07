#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Boot the unchanged foundation from _start; keep every run in .cache."""
import hashlib
import json
from pathlib import Path
import re
import struct
import sys

import validate

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
CACHE = HERE / ".cache/boot-validation"
FOUNDATION = "build/foundation/firmware.bin"
DISASSEMBLY = "build/foundation/firmware.dis"
DISASSEMBLY_SHA = "207527b5bf6dd9a4703ea1f2ff0af18daa13c155e58c6b2749b6f534bbda11b7"


def save(name, value):
    (CACHE / f"{name}.json").write_text(json.dumps(value, indent=2) + "\n")


def reference(inspection, pressed):
    # Only the existing CLI is used. GPL-3-only implementation remains in a
    # separate process, and no decoder/device source is imported into QEMU.
    command = ["mise", "exec", "--", "cargo", "run", "--manifest-path",
               "rust-emulator/Cargo.toml", "--locked", "--offline", "--",
               "boot", "build/foundation/firmware.elf", "--until",
               "foundation_done", "--inspect", inspection]
    if pressed:
        command.extend(["--press", "0:4"])
    return validate.json_run(command)


def main():
    CACHE.mkdir(parents=True, exist_ok=True)
    manifest = json.loads((HERE / "fixtures.json").read_text())
    for filename, digest in manifest["files"].items():
        validate.check(hashlib.sha256((ROOT / filename).read_bytes()).hexdigest() == digest,
                       f"fixture hash differs: {filename}")
    validate.check(hashlib.sha256((ROOT / DISASSEMBLY).read_bytes()).hexdigest() == DISASSEMBLY_SHA,
                   "foundation disassembly differs")

    # Preserve the existing validation interface and tracked generated records.
    # The original probe, timer, explicit-fault and infrastructure checks run
    # unchanged, with their outputs redirected to disposable experiment cache.
    validate.RESULTS = CACHE / "regression"
    sys.argv = ["validate.py", "all"]
    validate.main()

    image = (ROOT / FOUNDATION).read_bytes()
    expected_ram = list(struct.unpack("<II", image[0x244:0x24c]))
    expected_probe = [int(word, 16) for word in manifest["probe"]["expected_hex"]]
    summary = {"binary_sha256": manifest["files"][FOUNDATION],
               "disassembly_sha256": DISASSEMBLY_SHA,
               "entry": 0x02000120, "stop": 0x020002ba,
               "initial_sram_byte": 0xa5, "modes": {}}
    for pressed in [True, False]:
        mode = "foundation" if pressed else "foundation-released"
        log = CACHE / f"{mode}-tcg.log"
        qemu = validate.json_run([*validate.COMMAND, "-kernel", FOUNDATION,
                                  "-append", mode, "-d", "op", "-D", str(log)])
        oracle = reference("foundation_results:10", pressed)
        probe = reference("cpu_probe_results:12", pressed)
        matrix = reference("matrix_results:11", pressed)
        validate.check(qemu["pc"] == oracle["pc"] == 0x020002ba, "startup did not reach foundation_done")
        for i in [0, 1, 2, 5, 6, 7, 8, 9]:
            validate.check(qemu["inspection"][i] == oracle["inspection"][i],
                           f"{mode}: result word {i} differs")
        validate.check(qemu["data"] == qemu["inspection"][0] == 0xc001cafe,
                       "guest data copy failed")
        validate.check(qemu["bss"] == qemu["inspection"][1] == 0,
                       "guest did not clear poisoned BSS")
        validate.check(qemu["ram_code"] == expected_ram and qemu["inspection"][2] == 0x5a17,
                       "guest RAM copy/execution failed")
        markers = set(int(x, 16) for x in re.findall(r"^ ---- ([0-9a-f]+) 0+ 0+$", log.read_text(), re.MULTILINE))
        validate.check({0x02000120, 0x01c00000, 0x01c00004, 0x01c00006}.issubset(markers),
                       "startup or RAM execution was not translated")
        validate.check(qemu["probe"] == probe["inspection"] == expected_probe,
                       "full-startup probe differs from hardware/reference")
        expected_matrix = [0xe1 if pressed else 0x1e1] + [0x1e1] * 10
        validate.check(qemu["matrix"] == matrix["inspection"] == expected_matrix,
                       "guest matrix column samples differ")
        validate.check(qemu["shift_edges"] == 176 and qemu["latch_edges"] == 11 and
                       qemu["latched_columns"] == 0xfbff, "guest matrix shift/latch sequence differs")
        validate.check(qemu["inspection"][4] > qemu["inspection"][3] and
                       qemu["timer4_counter"] > qemu["inspection"][4], "TIMER4 did not advance")
        validate.check(qemu["specials"][14] == 0x01c7a000 and qemu["specials"][13] == 0x01c7c000,
                       "application/interrupt stack was not restored")
        for field in ["irq_entries", "rti_count", "timer_expirations", "acknowledgments"]:
            validate.check(qemu[field] == 1, f"{mode}: expected exactly one {field}")
        validate.check(not qemu["pending"] and not qemu["in_irq"], "interrupt did not complete")
        validate.check(qemu["last_irq_handler"] == 0x020002bc and
                       qemu["last_irq_pc"] in [0x02000280, 0x02000282], "wrong vector or interrupted PC")
        validate.check(qemu["entry_icfg"] == 0x013f0302 and qemu["return_icfg"] == 0x013f0700,
                       "IRQ source/priority/return state differs")
        validate.check(qemu["virtual_ns"] == (qemu["instructions"] + 1) * 8, "unexpected icount mapping")
        save(f"qemu-{mode}", qemu)
        save(f"rust-{mode}", {"foundation": oracle, "probe": probe, "matrix": matrix})
        summary["modes"][mode] = {"passed": True, "qemu_guest_instructions": qemu["instructions"],
                                   "rust_guest_instructions": oracle["instructions"],
                                   "qemu_timer4_samples": qemu["inspection"][3:5],
                                   "rust_timer4_samples": oracle["inspection"][3:5]}
        print(f"PASS {mode}: unchanged _start -> foundation_done; "
              f"{qemu['instructions']} guest instructions; poisoned SRAM, RAM code, probe, matrix and IRQ")
    save("foundation", summary)


if __name__ == "__main__":
    main()
