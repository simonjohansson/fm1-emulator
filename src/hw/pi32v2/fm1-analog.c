/* SPDX-License-Identifier: GPL-2.0-or-later */
/* Fresh interface model from pinned SDK register/field facts. WLA_CON0
 * bit14 (TEST_TO_ADC_EN), its source TEST_TO_ADC_S in bits 15-17, and the
 * RWF bias fields in bits 0-7 (enable, IGEN_SEL, test output and
 * selection), which stock configures and nothing modeled observes, may
 * change. The ADC validator freezes routing during conversions, and a
 * conversion through the test route still faults. Preserve all other initial fields rather than
 * claiming ADC ownership or inventing analog-block reset behavior. */
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
    if ((value ^ a->wla_con0) & ~(uint64_t)(FM1_ANALOG_TEST_TO_ADC | 0x380ff)) {
        pi32v2_fail(&a->cpu->env, "unsupported WLA_CON0 field change");
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
