/* SPDX-License-Identifier: GPL-2.0-or-later */
#ifndef HW_PI32V2_FM1_UART_H
#define HW_PI32V2_FM1_UART_H

#include "system/memory.h"
#include "qemu/timer.h"
#include "cpu.h"
#include "fm1-syscon.h"

/* Bounded DMA transmitter and idle receiver, with disconnected serial pins. */
typedef struct FM1PocUART {
    Pi32v2CPU *cpu;
    FM1PocSyscon *syscon;
    MemoryRegion mmio;
    uint32_t registers[10];
    QEMUTimer *timer;
    void (*update_irq)(void *opaque);
    void *opaque;
    bool busy, pending, irq_level;
    FILE *log;              /* optional development capture of TX bytes */
} FM1PocUART;

void fm1_uart_init(FM1PocUART *uart, Object *owner, Pi32v2CPU *cpu,
                    FM1PocSyscon *syscon, void (*update_irq)(void *opaque),
                    void *opaque);

#endif
