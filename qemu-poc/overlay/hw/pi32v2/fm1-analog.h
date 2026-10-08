/* SPDX-License-Identifier: GPL-2.0-or-later */
#ifndef HW_PI32V2_FM1_ANALOG_H
#define HW_PI32V2_FM1_ANALOG_H

#include "system/memory.h"
#include "cpu.h"

#define FM1_ANALOG_TEST_TO_ADC (1u << 14)

typedef void (*FM1AnalogValidateWrite)(void *opaque, uint32_t old_value,
                                      uint32_t new_value);

/* One canonical shared analog word. The SAR controller owns neither this
 * word nor its reset. Other analog fields retain their composition-supplied
 * handoff values; their hardware behavior is not implemented here. */
typedef struct FM1PocAnalog {
    Pi32v2CPU *cpu;
    MemoryRegion mmio;
    uint32_t wla_con0;
    FM1AnalogValidateWrite validator;
    void *validator_opaque;
} FM1PocAnalog;

void fm1_analog_init(FM1PocAnalog *analog, Object *owner, Pi32v2CPU *cpu,
                     uint32_t initial_wla_con0);
uint32_t fm1_analog_get_wla_con0(const FM1PocAnalog *analog);
void fm1_analog_set_validator(FM1PocAnalog *analog,
                              FM1AnalogValidateWrite validate, void *opaque);
void fm1_analog_clear_validator(FM1PocAnalog *analog,
                                FM1AnalogValidateWrite validate, void *opaque);

#endif
