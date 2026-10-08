/* SPDX-License-Identifier: GPL-2.0-or-later */
#ifndef HW_PI32V2_FM1_UART_H
#define HW_PI32V2_FM1_UART_H

#include "system/memory.h"
#include "cpu.h"

/* Bounded receiver with an unconnected external serial input. */
typedef struct FM1PocUART {
    Pi32v2CPU *cpu;
    MemoryRegion mmio;
    uint32_t registers[10];
} FM1PocUART;

void fm1_uart_init(FM1PocUART *uart, Object *owner, Pi32v2CPU *cpu);

#endif
