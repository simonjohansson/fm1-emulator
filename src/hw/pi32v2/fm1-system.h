/* SPDX-License-Identifier: GPL-2.0-or-later */
#ifndef HW_PI32V2_FM1_SYSTEM_H
#define HW_PI32V2_FM1_SYSTEM_H

#include "system/memory.h"
#include "qemu/timer.h"
#include "cpu.h"

/* Private state for the diagnostic application's explicit system interfaces.
 * Register addresses and wire operations come from tracked guest accesses;
 * no GPL-3 implementation is included or linked here. */
/* Plain configuration words kept without effect (fm1-system.c). */
enum { FM1_STORED_PMU, FM1_STORED_PLL, FM1_STORED_USB_PHY, FM1_STORED_OSA, FM1_STORED_DBG,
       FM1_STORED_COUNT };
#define FM1_STORED_WORDS 5

#define FM1_P33_PLAIN_COUNT 37     /* see p33_plain in fm1-system.c */

typedef struct FM1PocSystem {
    Pi32v2CPU *cpu;
    /* Optional peer owns only its per-core EMU/ETM state. P33, debug and
     * other system devices remain shared in the primary instance. */
    struct FM1PocSystem *shared, *secondary;
    MemoryRegion p33_mmio, reset_mmio, debug_mmio, emu_mmio, etm_mmio, cache_mmio;
    MemoryRegion core1_emu_message_mmio;
    MemoryRegion sdr_mmio, psram_mmio;
    MemoryRegion sys_div_mmio, chip_id_mmio;
    QEMUTimer *p33_timer, *watchdog_timer;
    uint32_t p33_control, reset_source;
    uint32_t sys_div;
    MemoryRegion stored_mmio[FM1_STORED_COUNT];
    uint32_t stored[FM1_STORED_COUNT][FM1_STORED_WORDS];   /* see stored_blocks */
    uint8_t p33_data, transfer_byte, command, phase;
    uint16_t address;
    bool p33_busy, debug_unlocked, completing_transfer;
    Pi32v2CPU *transfer_cpu;
    uint8_t p3_reset_source, valid_keep, watchdog_control, power_control;
    uint8_t p33_plain[FM1_P33_PLAIN_COUNT];                   /* see p33_plain in fm1-system.c */
    uint32_t debug_message, debug_enable, write_enable;
    uint32_t write_low[3], write_high[3], pc_low[2], pc_high[2];
    uint32_t fetch_epoch;                   /* PC-window generation */
    uint32_t emu_control, emu_message, stack_low[2], stack_high[2];
    uint32_t etm_control;
    uint32_t cache_control, cache_way[2];   /* CACHE_CON enables, DCACHE_WAY, ICACHE_WAY */
    uint64_t p33_transfers, p33_transactions, watchdog_feeds;
    uint64_t watchdog_arms, watchdog_expirations, guard_checks;
    int64_t watchdog_deadline;
} FM1PocSystem;

void fm1_system_init(FM1PocSystem *system, Object *owner, Pi32v2CPU *cpu);
void fm1_system_init_core1(FM1PocSystem *system, FM1PocSystem *shared,
                           Object *owner, Pi32v2CPU *cpu);
/* P3_LRC_CON0 bit 0: the RC32K oscillator LRCT measures is running. */
bool fm1_system_lrc_enabled(FM1PocSystem *system);
/* Copy the stack and write-window guards and ETM enable into the CPU. */
void fm1_system_sync_guards(FM1PocSystem *system);
/* PC windows only; no side effects (translation asks before executing). */
bool fm1_system_fetch_allowed(FM1PocSystem *system, uint32_t address, unsigned size);
void fm1_system_check_stack(FM1PocSystem *system);
/* Record the guest-visible message bit and stop: PI32V2_GUARD_STACK, _WRITE, _PC. */
G_NORETURN void fm1_system_guard_fault(FM1PocSystem *system, unsigned kind);

#endif
