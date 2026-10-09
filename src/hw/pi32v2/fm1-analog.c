/* SPDX-License-Identifier: GPL-2.0-or-later */
/* Fresh interface model from pinned SDK register/field facts. Only the
 * SAR ADC consumes WLA_CON0: TEST_TO_ADC_EN (bit 14) and TEST_TO_ADC_S
 * (bits 15-17). Its validator freezes routing during conversions, and the
 * ADC faults on test sources it does not model. The remaining fields are
 * RF bias configuration for the inert radio; stock's startup sets them and
 * nothing modeled observes them, so every field may change. The initial
 * value is kept as handed off; no analog-block reset is invented. */
#include "qemu/osdep.h"
#include "fm1-analog.h"

static uint64_t analog_read(void *opaque, hwaddr offset, unsigned size)
{
    FM1PocAnalog *a = opaque;

    if (offset || size != 4) {
        pi32v2_fail(&a->cpu->env, "unsupported WLA_CON0 register read or width");
    }
    return a->wla_con0;
}

static void analog_write(void *opaque, hwaddr offset, uint64_t value,
                          unsigned size)
{
    FM1PocAnalog *a = opaque;

    if (offset || size != 4) {
        pi32v2_fail(&a->cpu->env, "unsupported WLA_CON0 register write or width");
    }
    /* Consumers may reject a supported field change while active. Both the
     * shared word and the consumer's state remain untouched until accepted. */
    if (a->validator) {
        a->validator(a->validator_opaque, a->wla_con0, value);
    }
    a->wla_con0 = value;
}

static const MemoryRegionOps analog_ops = {
    .read = analog_read, .write = analog_write,
    .endianness = DEVICE_LITTLE_ENDIAN,
    .valid = {.min_access_size = 1, .max_access_size = 4},
    .impl = {.min_access_size = 1, .max_access_size = 4},
};

void fm1_analog_init(FM1PocAnalog *a, Object *owner, Pi32v2CPU *cpu,
                     uint32_t initial_wla_con0)
{
    a->cpu = cpu;
    a->wla_con0 = initial_wla_con0;
    a->validator = NULL;
    a->validator_opaque = NULL;
    memory_region_init_io(&a->mmio, owner, &analog_ops, a, "fm1.wla-con0", 4);
}

uint32_t fm1_analog_get_wla_con0(const FM1PocAnalog *a)
{
    return a->wla_con0;
}

void fm1_analog_set_validator(FM1PocAnalog *a,
                              FM1AnalogValidateWrite validate, void *opaque)
{
    g_assert(validate && !a->validator);
    a->validator = validate;
    a->validator_opaque = opaque;
}

void fm1_analog_clear_validator(FM1PocAnalog *a,
                                FM1AnalogValidateWrite validate, void *opaque)
{
    g_assert(validate && a->validator == validate && a->validator_opaque == opaque);
    a->validator = NULL;
    a->validator_opaque = NULL;
}
