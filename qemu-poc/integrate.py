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
    # Keep the small Cocoa host hook in the overlay rather than copying the
    # upstream backend. Anchors belong to the pinned release; fail on drift.
    cocoa = SOURCE / "ui/cocoa.m"
    content = cocoa.read_text()
    for before, after in [
        ('#include "system/runstate.h"',
         '#include "system/runstate.h"\n#include "fm1-cocoa-activity.h"'),
        ('    [NSApp setDelegate:controller];',
         '    [NSApp setDelegate:controller];\n    cocoa_vm_activity_init();'),
        ('static void cocoa_display_cleanup(void)\n{',
         'static void cocoa_display_cleanup(void)\n{\n    cocoa_vm_activity_cleanup();'),
    ]:
        if after not in content:
            if content.count(before) != 1:
                raise SystemExit("pinned Cocoa activity hook anchor differs")
            content = content.replace(before, after)
    write_changed(cocoa, content)
    # Resolve contained ordinary MMIO reads using the existing subpage table.
    # Keep upstream dispatch/validation and all complex accesses unchanged.
    physmem = SOURCE / "system/physmem.c"
    content = physmem.read_text()
    for before, after in [
        ('static MemTxResult subpage_read(void *opaque, hwaddr addr, uint64_t *data,',
         '#include "fm1-subpage-read.h"\n\n'
         'static MemTxResult subpage_read(void *opaque, hwaddr addr, uint64_t *data,'),
        ('    res = flatview_read(subpage->fv, addr + subpage->base, attrs, buf, len);',
         '    hwaddr xlat;\n'
         '    MemoryRegion *mr = subpage_read_region(subpage, addr, len, attrs, &xlat);\n'
         '    if (mr) {\n'
         '        res = flatview_read_continue(subpage->fv, addr + subpage->base,\n'
         '                                     attrs, buf, len, xlat, len, mr);\n'
         '    } else {\n'
         '        res = flatview_read(subpage->fv, addr + subpage->base, attrs, buf, len);\n'
         '    }'),
        ('    return flatview_access_valid(subpage->fv, addr + subpage->base,',
         '    if (!is_write) {\n'
         '        hwaddr xlat;\n'
         '        MemoryRegion *mr = subpage_read_region(subpage, addr, len, attrs, &xlat);\n'
         '        if (mr) {\n'
         '            return memory_region_access_valid(mr, xlat, len, false, attrs);\n'
         '        }\n'
         '    }\n\n'
         '    return flatview_access_valid(subpage->fv, addr + subpage->base,'),
    ]:
        if after not in content:
            if content.count(before) != 1:
                raise SystemExit("pinned subpage read hook anchor differs")
            content = content.replace(before, after)
    write_changed(physmem, content)
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
