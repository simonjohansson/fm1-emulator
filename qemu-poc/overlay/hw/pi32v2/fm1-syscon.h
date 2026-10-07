/* SPDX-License-Identifier: GPL-2.0-or-later */
#ifndef HW_PI32V2_FM1_SYSCON_H
#define HW_PI32V2_FM1_SYSCON_H

#include "system/memory.h"
#include "cpu.h"

/* Private ownership of the three already implemented shared words. Rates,
 * reset and additional clock/pinmux registers are outside this component. */
typedef enum FM1SysconWord {
    FM1_SYSCON_CLK_CON1,
    FM1_SYSCON_CLK_CON2,
    FM1_SYSCON_IOMAP_CON5,
    FM1_SYSCON_WORD_COUNT,
} FM1SysconWord;

typedef void (*FM1SysconValidateWrite)(void *opaque, uint32_t old_value,
                                      uint32_t new_value);

typedef struct FM1PocSyscon {
    Pi32v2CPU *cpu;
    MemoryRegion mmio[FM1_SYSCON_WORD_COUNT];
    uint32_t words[FM1_SYSCON_WORD_COUNT];
    FM1SysconValidateWrite validators[FM1_SYSCON_WORD_COUNT];
    void *validator_opaque[FM1_SYSCON_WORD_COUNT];
} FM1PocSyscon;

/* Initialize regions only; SoC composition maps them at 0x10010, 0x10014
 * and 0x51030 respectively. No additional addresses become available. */
void fm1_syscon_init(FM1PocSyscon *syscon, Object *owner, Pi32v2CPU *cpu);
uint32_t fm1_syscon_get(const FM1PocSyscon *syscon, FM1SysconWord word);
/* Validators may reject a write before canonical state changes. They must
 * not change state themselves. Register once during controller wiring. */
void fm1_syscon_set_validator(FM1PocSyscon *syscon, FM1SysconWord word,
                              FM1SysconValidateWrite validate, void *opaque);

#endif
