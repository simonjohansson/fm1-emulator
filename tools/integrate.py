#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Apply the maintained overlay to the exact pristine QEMU release tree."""
from pathlib import Path
import shutil

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
    # Native firmware launcher rewrites the command line before QEMU parses it.
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
    # The 2D SDL renderer sets a logical size, so SDL already reports mouse
    # positions in surface pixels. Scaling them again by the window size
    # sent clicks astray in any window but the natural size, and the grab
    # that confines the cursor ended only on an exact window-edge position.
    sdl = SOURCE / "ui/sdl2.c"
    content = sdl.read_text()
    for before, after in [
        ("        max_x = scr_w - 1;\n"
         "        max_y = scr_h - 1;\n"
         "        if (gui_grab && !gui_fullscreen\n"
         "            && (ev->motion.x == 0 || ev->motion.y == 0 ||\n"
         "                ev->motion.x == max_x || ev->motion.y == max_y)) {\n",
         "        max_x = surface_width(scon->surface) - 1;\n"
         "        max_y = surface_height(scon->surface) - 1;\n"
         "        if (gui_grab && !gui_fullscreen\n"
         "            && (ev->motion.x <= 0 || ev->motion.y <= 0 ||\n"
         "                ev->motion.x >= max_x || ev->motion.y >= max_y)) {\n"),
        ("    int scr_w, scr_h, surf_w, surf_h, x, y, dx, dy;\n",
         "    int scr_w, scr_h, x, y, dx, dy;\n"),
        ("    surf_w = surface_width(scon->surface);\n"
         "    surf_h = surface_height(scon->surface);\n"
         "    x = (int64_t)ev->motion.x * surf_w / scr_w;\n"
         "    y = (int64_t)ev->motion.y * surf_h / scr_h;\n"
         "    dx = (int64_t)ev->motion.xrel * surf_w / scr_w;\n"
         "    dy = (int64_t)ev->motion.yrel * surf_h / scr_h;\n",
         "    x = ev->motion.x;\n"
         "    y = ev->motion.y;\n"
         "    dx = ev->motion.xrel;\n"
         "    dy = ev->motion.yrel;\n"),
        ("    x = (int64_t)bev->x * surface_width(scon->surface) / scr_w;\n"
         "    y = (int64_t)bev->y * surface_height(scon->surface) / scr_h;\n",
         "    x = bev->x;\n"
         "    y = bev->y;\n"),
    ]:
        if after not in content:
            if content.count(before) != 1:
                raise SystemExit("pinned SDL mouse scaling anchor differs")
            content = content.replace(before, after)
    # Tell the console the window's size in display pixels, on Retina
    # screens too, so a console that draws itself (the FM-1 panel) can fill
    # it sharply rather than being stretched; size the window back in points.
    for before, after in [
        ("    if (scon->hidden) {\n        flags |= SDL_WINDOW_HIDDEN;\n    }\n",
         "    if (scon->hidden) {\n        flags |= SDL_WINDOW_HIDDEN;\n    }\n"
         "    flags |= SDL_WINDOW_ALLOW_HIGHDPI;\n"),
        ("        scon->real_renderer = SDL_CreateRenderer(scon->real_window, -1, 0);\n",
         "        scon->real_renderer = SDL_CreateRenderer(scon->real_window, -1, 0);\n"
         "        sdl2_report_size(scon, false);\n"),
        ("            QemuUIInfo info = {\n"
         "                .width = ev->window.data1,\n"
         "                .height = ev->window.data2,\n"
         "            };\n"
         "            qemu_console_set_ui_info(scon->dcl.con, &info, true);\n",
         "            sdl2_report_size(scon, true);\n"),
        ("void sdl2_window_resize(struct sdl2_console *scon)\n"
         "{\n"
         "    if (!scon->real_window) {\n"
         "        return;\n"
         "    }\n"
         "\n"
         "    SDL_SetWindowSize(scon->real_window,\n"
         "                      surface_width(scon->surface),\n"
         "                      surface_height(scon->surface));\n"
         "}\n",
         "void sdl2_report_size(struct sdl2_console *scon, bool delay)\n"
         "{\n"
         "    QemuUIInfo info = { 0 };\n"
         "    int width, height;\n"
         "\n"
         "    SDL_GetWindowSize(scon->real_window, &width, &height);\n"
         "    if (scon->real_renderer) {\n"
         "        SDL_GetRendererOutputSize(scon->real_renderer, &width, &height);\n"
         "    }\n"
         "    info.width = width;\n"
         "    info.height = height;\n"
         "    qemu_console_set_ui_info(scon->dcl.con, &info, delay);\n"
         "}\n"
         "\n"
         "void sdl2_window_resize(struct sdl2_console *scon)\n"
         "{\n"
         "    int width = surface_width(scon->surface);\n"
         "    int height = surface_height(scon->surface);\n"
         "    int window_w, window_h, pixels_w, pixels_h;\n"
         "\n"
         "    if (!scon->real_window) {\n"
         "        return;\n"
         "    }\n"
         "    if (scon->real_renderer) {\n"
         "        SDL_GetWindowSize(scon->real_window, &window_w, &window_h);\n"
         "        SDL_GetRendererOutputSize(scon->real_renderer, &pixels_w, &pixels_h);\n"
         "        if (pixels_w > 0 && pixels_h > 0) {\n"
         "            width = (int64_t)width * window_w / pixels_w;\n"
         "            height = (int64_t)height * window_h / pixels_h;\n"
         "        }\n"
         "        if (width == window_w && height == window_h) {\n"
         "            return;\n"
         "        }\n"
         "    }\n"
         "    SDL_SetWindowSize(scon->real_window, width, height);\n"
         "}\n"),
    ]:
        if after not in content:
            if content.count(before) != 1:
                raise SystemExit("pinned SDL display size anchor differs")
            content = content.replace(before, after)
    write_changed(sdl, content)
    header = SOURCE / "include/ui/sdl2.h"
    content = header.read_text()
    decl = "void sdl2_report_size(struct sdl2_console *scon, bool delay);\n"
    anchor = "void sdl2_window_resize(struct sdl2_console *scon);\n"
    if decl not in content:
        if content.count(anchor) != 1:
            raise SystemExit("pinned SDL header anchor differs")
        content = content.replace(anchor, anchor + decl)
    write_changed(header, content)
    # Peripheral pages (fm1-sfr.c) left no subpages, so the former subpage
    # read hook is retired: restore upstream text in trees it patched.
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
        if after in content:
            content = content.replace(after, before)
        elif content.count(before) != 1:
            raise SystemExit("pinned physmem.c subpage anchor differs")
    write_changed(physmem, content)
    (SOURCE / "system/fm1-subpage-read.h").unlink(missing_ok=True)
    # Honor lockless MMIO regions on TCG loads, as the physical-memory path
    # already does. 16-byte accesses keep taking the BQL.
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
    # Stores to lockless regions (the FM-1 peripheral pages) also skip the
    # generic BQL: the page dispatcher takes it for every block except those
    # whose handlers lock themselves (fm1_sfr_map_self_locking).
    store = ("    BQL_LOCK_GUARD();\n"
             "    return int_st_mmio_leN(cpu, full, val_le, addr, size, mmu_idx,\n"
             "                           ra, mr, mr_offset);\n")
    lockless_store = ("    if (mr->lockless_io) {\n"
                      "        return int_st_mmio_leN(cpu, full, val_le, addr, size, mmu_idx,\n"
                      "                               ra, mr, mr_offset);\n"
                      "    }\n" + store)
    if lockless_store not in content:
        if content.count(store) != 1:
            raise SystemExit("pinned TCG lockless store hook anchor differs")
        content = content.replace(store, lockless_store)
    write_changed(cputlb, content)
    # Timestamp icount's "guest is late" warnings with the host wall clock
    # and the guest's virtual time, so lag can be related to activity.
    cpuexec = SOURCE / "accel/tcg/cpu-exec.c"
    content = cpuexec.read_text()
    late = ('            qemu_printf("Warning: The guest is now late by %.1f to %.1f seconds\\n",\n'
            "                        threshold_delay - 1,\n"
            "                        threshold_delay);\n")
    stamped = ("            g_autoptr(GDateTime) now = g_date_time_new_now_local();\n"
               "            g_autofree char *stamp = g_date_time_format(now, \"%H:%M:%S\");\n"
               '            qemu_printf("[%s.%03d] Warning: The guest is now late by %.1f to %.1f "\n'
               '                        "seconds (guest time %.1f s)\\n", stamp,\n'
               "                        g_date_time_get_microsecond(now) / 1000,\n"
               "                        threshold_delay - 1, threshold_delay,\n"
               "                        (sc->realtime_clock + sc->diff_clk) / 1e9);\n")
    if stamped not in content:
        if content.count(late) != 1:
            raise SystemExit("pinned icount late-warning hook anchor differs")
        content = content.replace(late, stamped)
    write_changed(cpuexec, content)
    content = cputlb.read_text()
    # Read simple lockless regions without the generic dispatch layers
    # (fm1-mmio-read.h); other regions keep memory_region_dispatch_read.
    for before, after in [
        ("static uint64_t int_ld_mmio_beN(CPUState *cpu, CPUTLBEntryFull *full,",
         '#include "fm1-mmio-read.h"\n\n'
         "static uint64_t int_ld_mmio_beN(CPUState *cpu, CPUTLBEntryFull *full,"),
        ("        r = memory_region_dispatch_read(mr, mr_offset, &val,\n"
         "                                        this_mop, full->attrs);\n",
         "        if (!fm1_mmio_read(mr, mr_offset, this_size, full->attrs, &val, &r)) {\n"
         "            r = memory_region_dispatch_read(mr, mr_offset, &val,\n"
         "                                            this_mop, full->attrs);\n"
         "        }\n"),
    ]:
        if after not in content:
            if content.count(before) != 1:
                raise SystemExit("pinned TCG MMIO read hook anchor differs")
            content = content.replace(before, after)
    write_changed(cputlb, content)
    # Every icount deadline notifies each AioContext of the virtual clock,
    # waking the main loop (and contending for the BQL) ten thousand times
    # a guest second although no context holds virtual timers: icount runs
    # them on the vCPU. Skip contexts with no timer of that clock.
    asyncc = SOURCE / "util/async.c"
    content = asyncc.read_text()
    notify = ("static void aio_timerlist_notify(void *opaque, QEMUClockType type)\n"
              "{\n"
              "    aio_notify(opaque);\n"
              "}\n")
    quiet = ("static void aio_timerlist_notify(void *opaque, QEMUClockType type)\n"
             "{\n"
             "    AioContext *ctx = opaque;\n"
             "    if (type == QEMU_CLOCK_VIRTUAL && !timerlist_has_timers(ctx->tlg.tl[type])) {\n"
             "        return;\n"
             "    }\n"
             "    aio_notify(opaque);\n"
             "}\n")
    if quiet not in content:
        if content.count(notify) != 1:
            raise SystemExit("pinned AioContext timer notify hook anchor differs")
        content = content.replace(notify, quiet)
    write_changed(asyncc, content)
    # Round-robin icount splits each slice evenly among all vCPUs, so a
    # core held in reset or halted still halves the running core's slices
    # and doubles its CPU-loop exits. Split among runnable vCPUs only;
    # icount_prepare_for_run still caps every budget at the next deadline.
    # An idle vCPU's cpu_exec only returns EXCP_HALTED; skip it rather than
    # cycling the BQL and replay locks for it every slice.
    rr = SOURCE / "accel/tcg/tcg-accel-ops-rr.c"
    content = rr.read_text()
    for before, after in [
        ('#include "system/tcg.h"\n',
         '#include "system/tcg.h"\n#include "system/cpus.h"\n'),
        ("            cpu_budget = icount_percpu_budget(cpu_count);\n",
         "            CPUState *idle;\n"
         "            CPU_FOREACH(idle) {\n"
         "                cpu_count -= cpu_thread_is_idle(idle);\n"
         "            }\n"
         "            icount_set_parallel(MAX(cpu_count, 1));\n"
         "            cpu_budget = icount_percpu_budget(MAX(cpu_count, 1));\n"
         "            if (rr_held) {\n"
         "                cpu_budget = MIN(cpu_budget, 1024);\n"
         "            }\n"),
        ("            if (cpu_can_run(cpu)) {\n",
         "            if (cpu_can_run(cpu) && !cpu_thread_is_idle(cpu)) {\n"),
        # A kick from the main loop (host timing) used to end the slice and
        # hand over to the next vCPU early, so two-core runs were not
        # reproducible. Resume the same vCPU with its unrun budget; yield
        # only at a timer deadline, for queued work or a stop request.
        ("                if (icount_enabled()) {\n"
         "                    icount_prepare_for_run(cpu, cpu_budget);\n"
         "                }\n"
         "                r = tcg_cpu_exec(cpu);\n"
         "                if (icount_enabled()) {\n"
         "                    icount_process_data(cpu);\n"
         "                }\n",
         "                int64_t slice = cpu_budget, before;\n"
         "                unsigned stalls = 0;\n"
         "                do {\n"
         "                    before = slice;\n"
         "                    if (icount_enabled()) {\n"
         "                        icount_prepare_for_run(cpu, slice);\n"
         "                    }\n"
         "                    r = tcg_cpu_exec(cpu);\n"
         "                    if (icount_enabled()) {\n"
         "                        slice = cpu->icount_budget ?\n"
         "                            cpu->neg.icount_decr.u16.low + cpu->icount_extra : 0;\n"
         "                        icount_process_data(cpu);\n"
         "                    }\n"
         "                } while (icount_enabled() && r == EXCP_INTERRUPT &&\n"
         "                         slice > 0 && (slice < before || ++stalls < 3) &&\n"
         "                         qemu_clock_deadline_ns_all(QEMU_CLOCK_VIRTUAL,\n"
         "                                                    QEMU_TIMER_ATTR_ALL) != 0 &&\n"
         "                         cpu_work_list_empty(cpu) &&\n"
         "                         cpu_can_run(cpu) && !cpu_thread_is_idle(cpu));\n"),
        # Two cores run in parallel on hardware, so a critical section or
        # the step from requesting a task switch to taking it lasts only its
        # own instructions. Ending a slice there let the other core's slice
        # pass and device IRQs overtook the switch (stock lost a wakeup).
        # A target may keep the same vCPU for a few short passes (1024
        # instructions); timers still run between them, the other vCPU waits.
        ("            cpu = CPU_NEXT(cpu);\n"
         "        } /* while (cpu && !cpu->exit_request).. */\n",
         "            if (cpu && tcg_rr_hold_slice && rr_held < 16 &&\n"
         "                cpu_can_run(cpu) && !cpu_thread_is_idle(cpu) &&\n"
         "                tcg_rr_hold_slice(cpu)) {\n"
         "                rr_held++;\n"
         "                break;\n"
         "            }\n"
         "            rr_held = 0;\n"
         "            cpu = CPU_NEXT(cpu);\n"
         "        } /* while (cpu && !cpu->exit_request).. */\n"),
        ("static void rr_deal_with_unplugged_cpus(void)\n",
         "bool (*tcg_rr_hold_slice)(CPUState *cpu);\n"
         "static unsigned rr_held;\n\n"
         "static void rr_deal_with_unplugged_cpus(void)\n"),
    ]:
        if after not in content:
            if content.count(before) != 1:
                raise SystemExit("pinned round-robin icount budget hook anchor differs")
            content = content.replace(before, after)
    write_changed(rr, content)
    # Two runnable FM-1 cores execute in parallel: with the summed icount
    # clock each would get half its time. Halve the time per instruction
    # (shift - 1) while two vCPUs are runnable, keeping the clock continuous
    # as QEMU's adaptive icount does.
    common = SOURCE / "accel/tcg/icount-common.c"
    content = common.read_text()
    anchor = "static void icount_adjust_rt(void *opaque)\n"
    hook = ("/* FM-1: shift for 1 or 2 runnable vCPUs, from the configured one. */\n"
            "void icount_set_parallel(int runnable)\n"
            "{\n"
            "    static int base = -1;\n"
            "    if (base < 0) {\n"
            "        base = timers_state.icount_time_shift;\n"
            "    }\n"
            "    int shift = runnable > 1 && base > 0 ? base - 1 : base;\n"
            "    if (shift == timers_state.icount_time_shift) {\n"
            "        return;\n"
            "    }\n"
            "    seqlock_write_lock(&timers_state.vm_clock_seqlock,\n"
            "                       &timers_state.vm_clock_lock);\n"
            "    int64_t now = icount_get_locked();\n"
            "    qatomic_set(&timers_state.icount_time_shift, shift);\n"
            "    qatomic_set(&timers_state.qemu_icount_bias,\n"
            "                now - (timers_state.qemu_icount << shift));\n"
            "    seqlock_write_unlock(&timers_state.vm_clock_seqlock,\n"
            "                         &timers_state.vm_clock_lock);\n"
            "}\n\n")
    if hook not in content:
        if content.count(anchor) != 1:
            raise SystemExit("pinned icount shift anchor differs")
        content = content.replace(anchor, hook + anchor)
    # With align on, a guest that goes idle while late warps by up to its
    # lateness at once instead of sleeping, so the host latency of every
    # halt and wake-up through the main loop is made up rather than piling
    # up as lateness. A guest that runs ahead is still slowed as before.
    for before, after in [
        ('#include "system/cpu-timers-internal.h"\n',
         '#include "system/cpu-timers-internal.h"\n'
         '#include "internal-common.h"\n'),
        ("            seqlock_write_lock(&timers_state.vm_clock_seqlock,\n"
         "                               &timers_state.vm_clock_lock);\n"
         "            if (timers_state.vm_clock_warp_start == -1\n",
         "            seqlock_write_lock(&timers_state.vm_clock_seqlock,\n"
         "                               &timers_state.vm_clock_lock);\n"
         "            if (icount_align_option) {\n"
         "                /*\n"
         "                 * align only slows down a guest that runs ahead of real\n"
         "                 * time. A guest that is late does not sleep through its\n"
         "                 * idle time, it warps by up to the lateness at once, so the\n"
         "                 * host latency of every halt and wake-up through the main\n"
         "                 * loop is made up instead of piling up as lateness.\n"
         "                 */\n"
         "                int64_t late = clock - icount_get_locked();\n"
         "                /* A pending warp will credit the time since it started. */\n"
         "                if (timers_state.vm_clock_warp_start != -1) {\n"
         "                    late -= clock - timers_state.vm_clock_warp_start;\n"
         "                }\n"
         "                if (late > 0) {\n"
         "                    int64_t skip = MIN(late, deadline);\n"
         "                    qatomic_set(&timers_state.qemu_icount_bias,\n"
         "                                timers_state.qemu_icount_bias + skip);\n"
         "                    deadline -= skip;\n"
         "                }\n"
         "            }\n"
         "            if (deadline == 0) {\n"
         "                seqlock_write_unlock(&timers_state.vm_clock_seqlock,\n"
         "                                     &timers_state.vm_clock_lock);\n"
         "                qemu_clock_notify(QEMU_CLOCK_VIRTUAL);\n"
         "                return;\n"
         "            }\n"
         "            if (timers_state.vm_clock_warp_start == -1\n"),
    ]:
        if after not in content:
            if content.count(before) != 1:
                raise SystemExit("pinned icount idle warp anchor differs")
            content = content.replace(before, after)
    write_changed(common, content)
    header = SOURCE / "include/exec/icount.h"
    content = header.read_text()
    decl = ("void icount_set_parallel(int runnable);\n"
            "/* FM-1: run this vCPU again before the next (IRQs masked). */\n"
            "extern bool (*tcg_rr_hold_slice)(CPUState *cpu);\n")
    if decl not in content:
        anchor = "#endif /* EXEC_ICOUNT_H */"
        if content.count(anchor) != 1:
            raise SystemExit("pinned icount header anchor differs")
        content = content.replace(anchor, decl + "\n" + anchor)
    write_changed(header, content)
    # The icount budget also stopped at host REALTIME timer deadlines (for
    # UI input), another host-dependent slice boundary. The main loop runs
    # those timers on its own thread; bound slices by guest timers only.
    icount = SOURCE / "accel/tcg/tcg-accel-ops-icount.c"
    content = icount.read_text()
    before = ("        /* Check realtime timers, because they help with input processing */\n"
              "        deadline = qemu_soonest_timeout(deadline,\n"
              "                qemu_clock_deadline_ns_all(QEMU_CLOCK_REALTIME,\n"
              "                                           QEMU_TIMER_ATTR_ALL));\n")
    after = "        /* FM-1: guest (virtual) timers alone bound a slice. */\n"
    if after not in content:
        if content.count(before) != 1:
            raise SystemExit("pinned icount limit anchor differs")
        content = content.replace(before, after)
    write_changed(icount, content)
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
