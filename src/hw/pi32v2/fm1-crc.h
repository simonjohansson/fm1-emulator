/* SPDX-License-Identifier: GPL-2.0-or-later */
#ifndef HW_PI32V2_FM1_CRC_H
#define HW_PI32V2_FM1_CRC_H

#include "system/memory.h"
#include "cpu.h"

typedef struct FM1PocCRC {
    Pi32v2CPU *cpu;
    MemoryRegion mmio;
    uint32_t value;
} FM1PocCRC;

void fm1_crc_init(FM1PocCRC *crc, Object *owner, Pi32v2CPU *cpu);

#endif
