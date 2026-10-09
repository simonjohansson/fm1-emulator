/* SPDX-License-Identifier: GPL-2.0-or-later */
#ifndef HW_PI32V2_FM1_ADC_H
#define HW_PI32V2_FM1_ADC_H

#include "hw/core/sysbus.h"
#include "qemu/timer.h"
#include "cpu.h"
#include "fm1-analog.h"

#define FM1_ADC_CONVERSION_NS 10000
#define TYPE_FM1_ADC "fm1-sar-adc"
OBJECT_DECLARE_SIMPLE_TYPE(FM1PocADC, FM1_ADC)

/* A provider is a side-effect-free board input read. Return false when the
 * raw source is unavailable. RES retains the supplied 32-bit value exactly;
 * this interface does not assert a measured converter resolution. */
typedef bool (*FM1ADCRawInput)(void *opaque, unsigned channel, uint32_t *raw);

struct FM1PocADC {
    SysBusDevice parent_obj;
    Pi32v2CPU *cpu;
    FM1PocAnalog *analog;
    FM1ADCRawInput read_raw;
    void *raw_opaque;
    uint16_t raw_channels;
    MemoryRegion mmio;
    QEMUTimer *timer;
    qemu_irq irq;
    uint32_t control, result, latched_raw;
    bool pending, busy, validator_registered;
    int64_t deadline;
};

/* Composition supplies board inputs and shared analog ownership before
 * realization. Only the board maps the controller's eight-byte region. */
void fm1_adc_bind(FM1PocADC *adc, Pi32v2CPU *cpu, FM1PocAnalog *analog,
                  uint16_t raw_channels, FM1ADCRawInput read_raw, void *opaque);

#endif
