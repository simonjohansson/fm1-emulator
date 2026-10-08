/* SPDX-License-Identifier: GPL-2.0-or-later */
#ifndef HW_PI32V2_FM1_POC_H
#define HW_PI32V2_FM1_POC_H
#include "hw/core/boards.h"
#include "hw/core/irq.h"
#include "qemu/timer.h"
#include "cpu.h"
#include "fm1-lcd.h"
#include "fm1-system.h"
#include "fm1-nor.h"
#include "fm1-usb.h"
#include "fm1-alnk.h"
#include "fm1-syscon.h"
#include "fm1-adc.h"
#include "fm1-analog.h"
#include "fm1-input.h"

#define TYPE_FM1_POC_MACHINE MACHINE_TYPE_NAME("fm1-poc")
#define FM1_POC_MAX_ALNK_RESETS 16
#define FM1_POC_MAX_ADC_RESETS 16
OBJECT_DECLARE_SIMPLE_TYPE(FM1PocState, FM1_POC_MACHINE)
typedef struct FM1TimerState {
    FM1PocState *machine;
    MemoryRegion mmio;
    QEMUTimer *timer;
    unsigned number;
    uint32_t control, counter, period;
    bool pending;
    int64_t epoch, deadline;
    uint64_t expirations, acknowledgments;
} FM1TimerState;

struct FM1PocState {
    MachineState parent_obj;
    Pi32v2CPU *cpu;
    MemoryRegion irq_mmio, gpio_mmio, iomap_mmio;
    FM1TimerState timers[2];
    qemu_irq irq, alnk_irq;
    uint32_t irq_configs[32];
    uint32_t gpio[8][8], iomap_con0, iomap_con1;
    FM1PocLCD lcd;
    FM1PocSystem system;
    FM1PocNOR nor;
    FM1PocUSB usb;
    FM1PocALNK alnk;
    FM1PocSyscon syscon;
    FM1PocADC adc;
    FM1PocAnalog analog;
    FM1PocInput input;
    unsigned frames;
    const char *frame_dir;
    QEMUTimer *display_key_timer;
    int64_t display_key_deadline;
    /* Optional test-only local reset schedule, never guest hardware state. */
    QEMUTimer *alnk_reset_timer;
    int64_t alnk_reset_times[FM1_POC_MAX_ALNK_RESETS];
    unsigned alnk_reset_count, alnk_reset_index;
    QEMUTimer *adc_reset_timer;
    int64_t adc_reset_times[FM1_POC_MAX_ADC_RESETS];
    unsigned adc_reset_count, adc_reset_index;
    uint32_t analog_initial_wla_con0;
    uint16_t shift, latched;
    uint8_t matrix[11];
    uint64_t shift_edges, latch_edges;
    uint64_t loop_visits, loop_target_irqs;
    bool keep_open, finished, display_live, saving_fault;
    bool alnk_probe, alnk_irq_level;
    /* Optional validation setup; never used to select hardware behavior. */
    bool timer_fixture, foundation_fixture, display_fixture, diag_fixture;
    bool felucca_fixture, application;
    uint32_t initial_gpr[16], initial_spr[16];
    uint32_t last_access_address, last_access_size, last_access_flags;
};

void fm1_test_configure(FM1PocState *m, MachineState *ms);
void fm1_test_seed_ram(FM1PocState *m);
void fm1_test_start(FM1PocState *m);
void fm1_test_reset_state(CPUPi32v2State *e);
G_NORETURN void fm1_poc_finish(CPUPi32v2State *e);
void fm1_poc_fault(CPUPi32v2State *e, const char *reason);
uint32_t fm1_timer_counter(FM1TimerState *t);
#endif
