#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Apply the experimental overlay to the exact pristine QEMU release tree."""
from pathlib import Path
import shutil

HERE = Path(__file__).resolve().parent
QEMU = "11.1.2"
SOURCE = HERE / f".cache/qemu-{QEMU}"


def append_once(path, line):
    text = path.read_text()
    if line not in text:
        path.write_text(text + "\n" + line + "\n")


def write_changed(path, content):
    if not path.exists() or path.read_text() != content:
        path.write_text(content)


def main():
    if (SOURCE / "VERSION").read_text().strip() != QEMU:
        raise SystemExit(f"expected QEMU {QEMU}")
    for path in (HERE / "overlay").rglob("*"):
        if path.is_file():
            dest = SOURCE / path.relative_to(HERE / "overlay")
            dest.parent.mkdir(parents=True, exist_ok=True)
            if not dest.exists() or dest.read_bytes() != path.read_bytes():
                shutil.copy2(path, dest)
    write_changed(SOURCE / "configs/targets/pi32v2-softmmu.mak",
                  "TARGET_ARCH=pi32v2\nTARGET_LONG_BITS=32\n")
    devices = SOURCE / "configs/devices/pi32v2-softmmu"
    devices.mkdir(exist_ok=True)
    write_changed(devices / "default.mak", "CONFIG_FM1_POC=y\n")
    for directory in ["hw", "target"]:
        append_once(SOURCE / directory / "meson.build", "subdir('pi32v2')")
        append_once(SOURCE / directory / "Kconfig", "source pi32v2/Kconfig")
    arch = SOURCE / "include/qemu/base-arch-defs.h"
    content = arch.read_text()
    if "QEMU_ARCH_PI32V2" not in content:
        arch.write_text(content.replace("    QEMU_ARCH_ALL =         UINT32_MAX,",
            "    QEMU_ARCH_PI32V2 =      (1UL << SYS_EMU_TARGET_PI32V2),\n    QEMU_ARCH_ALL =         UINT32_MAX,"))
    # Explicitly approved by the user: isolated QEMU QMP architecture enum.
    qapi = SOURCE / "qapi/machine.json"
    content = qapi.read_text()
    if "'pi32v2'" not in content:
        qapi.write_text(content.replace("'ppc64', 'riscv32'", "'pi32v2', 'ppc64', 'riscv32'"))


if __name__ == "__main__":
    main()
