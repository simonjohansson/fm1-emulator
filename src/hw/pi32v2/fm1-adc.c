/* SPDX-License-Identifier: GPL-2.0-or-later */
/* Fresh SAR register model from pinned vendor disassembly and SDK interface
 * facts, not copied firmware or Rust peripheral implementation.
 *
 * Functional policies, without hardware calibration: bit6 is a per-write
 * command and reads zero; a kick clears pending, and enabled kicks latch a
 * raw board input and complete after 10us of QEMU virtual time. Busy kicks
 * restart. Enable alone does not start; identical no-kick writes preserve
 * phase, busy reconfiguration without a kick fails. Disable cancels while
 * retaining result/pending until a kick. Local reset clears only SAR state.
 * Only divider6/bit3/startupF and polling are supported when enabled. The
 * source clock, bit3 meaning, physical latency/resolution and IRQ24 behavior
 * remain unknown/unimplemented. Shared WLA state is owned elsewhere. */
#include "qemu/osdep.h"
#include "qemu/module.h"
#include "qapi/error.h"
#include "hw/core/resettable.h"
#include "fm1-adc.h"

#define ADC_ENABLE 0x10u
#define ADC_IRQ_ENABLE 0x20u
#define ADC_KICK 0x40u
#define ADC_PENDING 0x80u
#define ADC_TIMING_MASK 0xf00fu
#define ADC_TIMING_SUPPORTED 0xf00eu

static G_NORETURN void adc_fail(FM1PocADC *a, const char *reason)
{
    pi32v2_fail(&a->cpu->env, reason);
}

static void adc_completed(void *opaque)
{
    FM1PocADC *a = opaque;

    if (!a->busy || !(a->control & ADC_ENABLE) ||
        qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL) < a->deadline) {
        adc_fail(a, "SAR ADC completion outside an enabled deadline");
    }
    a->result = a->latched_raw;
    a->pending = true;
    a->busy = false;
    a->deadline = 0;
}

static uint64_t adc_read(void *opaque, hwaddr offset, unsigned size)
{
    FM1PocADC *a = opaque;

    if (size != 4 || (offset != 0 && offset != 4)) {
        adc_fail(a, "unsupported SAR ADC register read or width");
    }
    return offset == 4 ? a->result : a->control | (a->pending ? ADC_PENDING : 0);
}

static void adc_write(void *opaque, hwaddr offset, uint64_t value, unsigned size)
{
    FM1PocADC *a = opaque;
    uint32_t raw = 0;
    int64_t deadline = 0;

    if (size != 4 || (offset != 0 && offset != 4)) {
        adc_fail(a, "unsupported SAR ADC register write or width");
    }
    if (offset == 4) {
        adc_fail(a, "write to read-only SAR ADC result");
    }
    if (value & ~0xffffull) {
        adc_fail(a, "unsupported SAR ADC control fields");
    }
    if (value & ADC_IRQ_ENABLE) {
        adc_fail(a, "SAR ADC IRQ24 is unimplemented");
    }
    /* Status copied by a guest RMW is ignored. The command is recognized
     * from this payload, independent of previous control/readback bits. */
    uint32_t control = value & ~(ADC_KICK | ADC_PENDING);
    bool enabled = control & ADC_ENABLE;
    bool kick = value & ADC_KICK;
    if (enabled) {
        unsigned channel = (control >> 8) & 15;

        if ((control & ADC_TIMING_MASK) != ADC_TIMING_SUPPORTED) {
            adc_fail(a, "unsupported SAR ADC enabled timing configuration");
        }
        if (!(a->raw_channels & (1u << channel))) {
            adc_fail(a, "SAR ADC channel has no board raw input");
        }
        if (a->busy && !kick && control != a->control) {
            adc_fail(a, "SAR ADC configuration changed while busy without a kick");
        }
        if (kick) {
            if (fm1_analog_get_wla_con0(a->analog) & FM1_ANALOG_TEST_TO_ADC) {
                adc_fail(a, "unsupported SAR ADC analog-test input routing");
            }
            if (!a->read_raw(a->raw_opaque, channel, &raw)) {
                adc_fail(a, "SAR ADC board raw input is unavailable");
            }
            int64_t now = qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL);
            if (now < 0 || now > INT64_MAX - FM1_ADC_CONVERSION_NS) {
                adc_fail(a, "SAR ADC virtual completion deadline overflow");
            }
            deadline = now + FM1_ADC_CONVERSION_NS;
        }
    }
    /* Every candidate check precedes ALL local state/timer mutations. SAR
     * poststate is not in the private fatal capture; preservation relies on
     * this precommit ordering rather than postfatal runtime attestation. */
    a->control = control;
    if (kick) {
        a->pending = false;
    }
    if (!enabled) {
        timer_del(a->timer);
        a->busy = false;
        a->deadline = 0;
        a->latched_raw = 0;
    } else if (kick) {
        a->latched_raw = raw;
        a->busy = true;
        a->deadline = deadline;
        timer_mod_ns(a->timer, deadline);
    }
}

static void validate_analog_write(void *opaque, uint32_t old_value,
                                  uint32_t new_value)
{
    FM1PocADC *a = opaque;

    /* Busy-only routing freeze is a functional policy. Idle route changes
     * are accepted; a subsequent GPIO kick validates the canonical route. */
    if (a->busy && old_value != new_value) {
        adc_fail(a, "WLA_CON0 ADC routing changed while SAR ADC is busy");
    }
}

static const MemoryRegionOps adc_ops = {
    .read = adc_read, .write = adc_write,
    .endianness = DEVICE_LITTLE_ENDIAN,
    .valid = {.min_access_size = 1, .max_access_size = 4},
    .impl = {.min_access_size = 1, .max_access_size = 4},
};

static void adc_reset_enter(Object *obj, ResetType type)
{
    FM1PocADC *a = FM1_ADC(obj);

    timer_del(a->timer);
    a->control = a->result = a->latched_raw = 0;
    a->pending = a->busy = false;
    a->deadline = 0;
    /* Keep board providers, shared analog word/validator and QOM wiring. */
}

static void adc_instance_init(Object *obj)
{
    FM1PocADC *a = FM1_ADC(obj);
    SysBusDevice *sbd = SYS_BUS_DEVICE(obj);

    a->timer = timer_new_ns(QEMU_CLOCK_VIRTUAL, adc_completed, a);
    memory_region_init_io(&a->mmio, obj, &adc_ops, a, "fm1.sar-adc", 8);
    sysbus_init_mmio(sbd, &a->mmio);
}

static void adc_realize(DeviceState *dev, Error **errp)
{
    FM1PocADC *a = FM1_ADC(dev);

    if (!a->cpu || !a->analog || !a->raw_channels || !a->read_raw) {
        error_setg(errp, "SAR ADC requires composition-owned CPU, analog and raw input bindings");
        return;
    }
    fm1_analog_set_validator(a->analog, validate_analog_write, a);
    a->validator_registered = true;
}

static void adc_unrealize(DeviceState *dev)
{
    FM1PocADC *a = FM1_ADC(dev);

    device_cold_reset(dev);
    if (a->validator_registered) {
        fm1_analog_clear_validator(a->analog, validate_analog_write, a);
        a->validator_registered = false;
    }
}

static void adc_instance_finalize(Object *obj)
{
    FM1PocADC *a = FM1_ADC(obj);

    g_assert(!a->validator_registered);
    timer_free(a->timer);
}

static void adc_class_init(ObjectClass *klass, const void *data)
{
    DeviceClass *dc = DEVICE_CLASS(klass);
    ResettableClass *rc = RESETTABLE_CLASS(klass);

    dc->realize = adc_realize;
    dc->unrealize = adc_unrealize;
    dc->user_creatable = false;
    dc->hotpluggable = false;
    rc->phases.enter = adc_reset_enter;
}

static const TypeInfo adc_info = {
    .name = TYPE_FM1_ADC,
    .parent = TYPE_SYS_BUS_DEVICE,
    .instance_size = sizeof(FM1PocADC),
    .instance_init = adc_instance_init,
    .instance_finalize = adc_instance_finalize,
    .class_init = adc_class_init,
};

static void adc_register_types(void)
{
    type_register_static(&adc_info);
}
type_init(adc_register_types)

void fm1_adc_bind(FM1PocADC *a, Pi32v2CPU *cpu, FM1PocAnalog *analog,
                  uint16_t raw_channels, FM1ADCRawInput read_raw, void *opaque)
{
    g_assert(!DEVICE(a)->realized && !a->cpu && !a->analog && !a->read_raw);
    a->cpu = cpu;
    a->analog = analog;
    a->raw_channels = raw_channels;
    a->read_raw = read_raw;
    a->raw_opaque = opaque;
}
