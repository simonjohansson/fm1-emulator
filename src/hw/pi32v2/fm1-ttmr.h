/* SPDX-License-Identifier: GPL-2.0-or-later */
#ifndef HW_PI32V2_FM1_TTMR_H
#define HW_PI32V2_FM1_TTMR_H

#include "system/memory.h"
#include "qemu/timer.h"
#include "cpu.h"

typedef struct FM1PocTTMR {
    Pi32v2CPU *cpu;
    MemoryRegion mmio;
    QEMUTimer *timer;
    void (*update_irq)(void *opaque);
    void *opaque;
    uint8_t control;
    bool pending;
    uint32_t counter, period;
    int64_t epoch;
    uint64_t next_wrap;     /* ticks after epoch at which CNT next wraps */
} FM1PocTTMR;

void fm1_ttmr_init(FM1PocTTMR *ttmr, Object *owner, Pi32v2CPU *cpu,
                   void (*update_irq)(void *opaque), void *opaque);

#endif
