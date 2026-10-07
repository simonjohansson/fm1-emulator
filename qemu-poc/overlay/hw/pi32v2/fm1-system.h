/* SPDX-License-Identifier: GPL-2.0-or-later */
#ifndef HW_PI32V2_FM1_SYSTEM_H
#define HW_PI32V2_FM1_SYSTEM_H

#include "system/memory.h"
#include "qemu/timer.h"
#include "cpu.h"

/* Private state for the diagnostic application's explicit system interfaces.
 * Register addresses and wire operations come from tracked guest accesses;
 * no GPL-3 implementation is included or linked here. */
typedef struct FM1PocSystem {
    Pi32v2CPU *cpu;
    MemoryRegion p33_mmio, reset_mmio, debug_mmio, emu_mmio, etm_mmio, cache_mmio;
    QEMUTimer *p33_timer, *watchdog_timer;
    uint32_t p33_control, reset_source;
    uint8_t p33_data, transfer_byte, command, phase;
    uint16_t address;
    bool p33_busy, debug_unlocked;
    uint8_t p3_reset_source, valid_keep, watchdog_control, power_control;
    uint32_t debug_message, debug_enable, write_enable;
    uint32_t write_low[3], write_high[3], pc_low[2], pc_high[2];
    uint32_t emu_control, emu_message, stack_low[2], stack_high[2];
    uint32_t etm_control, branch_pc[4];
    uint64_t p33_transfers, p33_transactions, watchdog_feeds;
    uint64_t watchdog_arms, watchdog_expirations, guard_checks, branches;
    int64_t watchdog_deadline;
} FM1PocSystem;

void fm1_system_init(FM1PocSystem *system, Object *owner, Pi32v2CPU *cpu);
void fm1_system_check_access(FM1PocSystem *system, uint32_t address,
                             unsigned size, bool write, bool fetch);
void fm1_system_check_stack(FM1PocSystem *system);
void fm1_system_note_branch(FM1PocSystem *system, uint32_t from);

#endif
