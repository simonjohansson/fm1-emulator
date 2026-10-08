#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Apply the maintained overlay to the exact pristine QEMU release tree."""
from pathlib import Path
import shutil
import re

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
QEMU = "11.1.2"
SOURCE = ROOT / f".cache/qemu-{QEMU}"


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
    for path in (ROOT / "src").rglob("*"):
        if path.is_file():
            dest = SOURCE / path.relative_to(ROOT / "src")
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
    ]:
        if after not in content:
            if content.count(before) != 1:
                raise SystemExit("pinned Cocoa activity hook anchor differs")
            content = content.replace(before, after)
    write_changed(cocoa, content)
    # Keep the native instrument panel as a small maintained host adapter.
    # The stock Cocoa renderer remains the LCD child; other boards retain
    # the original interface. Hooks are checked against the pinned release.
    content = cocoa.read_text()
    for before, after in [
        ('#include "ui/kbd-state.h"',
         '#include "ui/kbd-state.h"\n#include "fm1-panel-declarations.h"'),
        ('@interface QemuCocoaAppController : NSObject',
         '#include "fm1-panel.h"\n\n@interface QemuCocoaAppController : NSObject'),
        ('- (void) resizeWindow\n{',
         '- (void) resizeWindow\n{\n    if (fm1_panel_resize(self)) { return; }'),
        ('    COCOA_DEBUG("QemuApplication: sendEvent\\n");',
         '    COCOA_DEBUG("QemuApplication: sendEvent\\n");\n'
         '    if (fm1_panel_native_event(event)) { [super sendEvent:event]; return; }'),
        ('    COCOA_DEBUG("%s\\n", __func__);\n    [cocoaView ungrabMouse];\n    [cocoaView raiseAllKeys];',
         '    COCOA_DEBUG("%s\\n", __func__);\n    fm1_panel_release();\n'
         '    [cocoaView ungrabMouse];\n    [cocoaView raiseAllKeys];'),
        ('    kbd = qkbd_state_init(dcl.con);',
         '    kbd = qkbd_state_init(dcl.con);\n    fm1_panel_install();'),
    ]:
        if after not in content:
            if content.count(before) != 1:
                raise SystemExit("pinned Cocoa panel hook anchor differs")
            content = content.replace(before, after)
    content = content.replace('qkbd_state_key_event(', 'fm1_panel_keyboard_event(')
    # Normalize both maintained cleanup hooks together so repeated integration
    # cannot insert another activity cleanup around the panel cleanup.
    content, cleanup_count = re.subn(
        r"(static void cocoa_display_cleanup\(void\)\n\{)"
        r"(?:\n    (?:fm1_panel_cleanup|cocoa_vm_activity_cleanup)\(\);)*",
        r"\1\n    fm1_panel_cleanup();\n    cocoa_vm_activity_cleanup();", content)
    if cleanup_count != 1:
        raise SystemExit("pinned Cocoa cleanup hook anchor differs")
    write_changed(cocoa, content)
    # Native firmware launcher shares QEMU's main-thread Cocoa lifecycle.
    entry = SOURCE / "system/main.c"
    content = entry.read_text()
    for before, after in [
        ('#include "system/system.h"',
         '#include "system/system.h"\n#include "fm1-launcher.h"'),
        ('    qemu_init(argc, argv);',
         '    fm1_launcher_arguments(&argc, &argv);\n    qemu_init(argc, argv);'),
    ]:
        if after not in content:
            if content.count(before) != 1:
                raise SystemExit("pinned native launcher hook anchor differs")
            content = content.replace(before, after)
    write_changed(entry, content)
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
    # Honor lockless MMIO regions on TCG loads, as the physical-memory path
    # already does. Stores and 16-byte loads keep taking the BQL.
    cputlb = SOURCE / "accel/tcg/cputlb.c"
    content = cputlb.read_text()
    load = ("    BQL_LOCK_GUARD();\n"
            "    return int_ld_mmio_beN(cpu, full, ret_be, addr, size, mmu_idx,\n"
            "                           type, ra, mr, mr_offset);\n")
    lockless = ("    if (mr->lockless_io) {\n"
                "        return int_ld_mmio_beN(cpu, full, ret_be, addr, size, mmu_idx,\n"
                "                               type, ra, mr, mr_offset);\n"
                "    }\n" + load)
    if lockless not in content:
        if content.count(load) != 1:
            raise SystemExit("pinned TCG lockless load hook anchor differs")
        content = content.replace(load, lockless)
    write_changed(cputlb, content)
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
