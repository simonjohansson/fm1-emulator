/* SPDX-License-Identifier: GPL-2.0-or-later */
#ifndef HW_PI32V2_FM1_LRCT_H
#define HW_PI32V2_FM1_LRCT_H

#include "system/memory.h"
#include "qemu/timer.h"
#include "cpu.h"
#include "fm1-system.h"

typedef struct FM1PocLRCT {
    FM1PocSystem *system;
    MemoryRegion mmio;
    QEMUTimer *timer;
    void (*update_irq)(void *opaque);
    void *opaque;
    uint8_t control;
    bool done;
    uint32_t num, measured;
    uint64_t completions;
} FM1PocLRCT;

void fm1_lrct_init(FM1PocLRCT *lrct, Object *owner, FM1PocSystem *system,
                   void (*update_irq)(void *opaque), void *opaque);

#endif
