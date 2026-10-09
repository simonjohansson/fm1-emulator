/* SPDX-License-Identifier: GPL-2.0-or-later */
/* FM-1 board composition and implemented SoC behavior. Hardware availability
 * does not depend on firmware identity or optional validation fixtures.
 * Controllers remain functional subsets; lifecycle extraction follows. */
#include "qemu/osdep.h"
#include "qemu/error-report.h"
#include "qemu/timer.h"
#include "qapi/error.h"
#include "hw/core/boards.h"
#include "hw/core/loader.h"
#include "hw/core/irq.h"
#include "system/address-spaces.h"
#include "system/runstate.h"
#include "system/tcg.h"
#include "cpu.h"
#include "fm1-lcd.h"
#include "fm1-system.h"
#include "fm1-nor.h"
#include "fm1-usb.h"
#include "fm1-alnk.h"

#include "fm1-poc.h"
#include "fm1-sfr.h"

/* Guards: translated code checks stores and SP writes against CPU mirrors
 * kept by the system controller; fetches are decided at translation. */
static int fm1_poc_fetch_fault(CPUPi32v2State *e, uint32_t address, unsigned size)
{
    FM1PocState *m = env_archcpu(e)->machine;
    if (!fm1_system_fetch_allowed(&m->system, address, size)) {
        return PI32V2_GUARD_PC;
    }
    return fm1_nor_fetch_fault(&m->nor, address, size);
}

static G_NORETURN void fm1_poc_guard_fault(CPUPi32v2State *e, unsigned kind,
                                           uint32_t address, unsigned size)
{
    FM1PocState *m = env_archcpu(e)->machine;
    /* The access that fired, as the capture's last_access: flags 1 write,
     * 2 instruction fetch, 4 stack pointer. */
    m->last_access_address = address;
    m->last_access_size = size;
    m->last_access_flags = kind == PI32V2_GUARD_WRITE ? 1 :
                           kind == PI32V2_GUARD_STACK ? 4 :
                           kind == PI32V2_GUARD_PC || address == e->pc ? 2 : 0;
    if (kind == PI32V2_GUARD_XIP_DISABLED || kind == PI32V2_GUARD_XIP_BOUNDS) {
        fm1_nor_guard_fault(&m->nor, kind);
    }
    fm1_system_guard_fault(kind == PI32V2_GUARD_STACK && CPU(env_archcpu(e))->cpu_index ?
                           &m->system1 : &m->system, kind);
}

static void fm1_poc_check_stack(CPUPi32v2State *e)
{
    FM1PocState *m = env_archcpu(e)->machine;
    fm1_system_check_stack(CPU(env_archcpu(e))->cpu_index ? &m->system1 : &m->system);
}

/* Resets clear the CPU state, including the machine's guard mirrors. */
static void fm1_poc_reset_state(CPUPi32v2State *e)
{
    FM1PocState *m = env_archcpu(e)->machine;
    if (CPU(env_archcpu(e))->cpu_index == 0) { fm1_test_reset_state(e); }
    FM1PocSystem *s = CPU(env_archcpu(e))->cpu_index ? &m->system1 : &m->system;
    if (s->cpu) {
        fm1_system_sync_guards(s);
    }
    /* Cores share translations, so a reset core keeps the current
     * fetch-guard generation instead of reusing a stale one. */
    e->fetch_epoch = m->system.fetch_epoch;
    e->xip_fetch = m->nor.cpu && fm1_nor_xip_enabled(&m->nor);
}

static unsigned divider(FM1TimerState *t) { return t->control & 16 ? 4 : 1; }
/* CON bits 2-3 select the clock, measured on a real FM-1: 0 counts the
 * 60 MHz peripheral clock, 2 the 24 MHz crystal. Others are refused. */
static uint64_t clock_hz(FM1TimerState *t) { return t->control & 8 ? 24000000 : 60000000; }
static uint64_t period_ticks(FM1TimerState *t)
{
    return t->period == UINT32_MAX ? 1ull << 32 : MAX(t->period, 1);
}
static uint64_t elapsed_ticks(FM1TimerState *t)
{
    if (!(t->control & 1)) { return 0; }
    uint64_t ns = qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL) - t->epoch;
    /* The 128-bit intermediate is needed only once a free-running timer
     * keeps its epoch for minutes; the result is the same. */
    if (ns < UINT64_MAX / clock_hz(t)) {
        return ns * clock_hz(t) / (divider(t) * 1000000000ull);
    }
    return muldiv64(ns, clock_hz(t), divider(t) * 1000000000ull);
}
uint32_t fm1_timer_counter(FM1TimerState *t)
{
    return (t->counter + elapsed_ticks(t)) % period_ticks(t);
}
/* Bank-0 ILAT bits 4-7 are sources 124-127. Hardware observes the request
 * through each running core's enabled-source configuration; bank-1 clear
 * does not acknowledge a bank-0 request. SET reads as zero (measured).
 * Bits 0-3 and bank-1 SET remain unqualified. */
static uint32_t software_pending(FM1PocState *m, bool core1)
{
    if (core1 && (!m->cpu1 || m->cpu1->held_reset)) { return 0; }
    uint32_t config = (core1 ? m->irq1_configs : m->irq_configs)[15];
    uint32_t enabled = 0;
    for (unsigned bit = 4; bit < 8; bit++) {
        enabled |= (config >> (bit * 4) & 1) << bit;
    }
    return m->software_latch & enabled;
}
static bool spi2_irq_level(FM1PocState *m);
static void update_irq(FM1PocState *m)
{
    bool shared = m->timer1.pending || m->timers[0].pending || m->timers[1].pending ||
                  m->alnk_irq_level ||
                  m->adc_irq_level || m->lcd.irq_level || m->uart.irq_level ||
                  m->lrct.done || spi2_irq_level(m);
    qemu_set_irq(m->irq, shared || m->ttmr.pending || software_pending(m, false));
    if (m->cpu1) {
        qemu_set_irq(m->irq1, shared || m->ttmr1.pending || software_pending(m, true));
    }
}
static void ttmr_irq(void *opaque) { update_irq(opaque); }
/* Private source selection for the reached timer/audio/software sources. Raw device
 * levels remain pending until their guest acknowledgments. */
static bool fm1_poc_select_irq(CPUPi32v2State *e, unsigned *number, unsigned *priority)
{
    FM1PocState *m = PI32V2_CPU(env_cpu(e))->machine;
    bool core1 = CPU(env_archcpu(e))->cpu_index != 0;
    const uint32_t *configs = core1 ? m->irq1_configs : m->irq_configs;
    uint32_t soft = software_pending(m, core1);
    const unsigned sources[] = {3, 5, 11, 16, 20, 24, 37, 44, 62, 63, 124, 125, 126, 127};
    const bool pending[] = {core1 ? m->ttmr1.pending : m->ttmr.pending,
                            m->timer1.pending, m->alnk_irq_level, m->lcd.irq_level,
                            m->uart.irq_level, m->adc_irq_level,
                            spi2_irq_level(m), m->lrct.done,
                            m->timers[0].pending, m->timers[1].pending,
                            soft & 16, soft & 32, soft & 64, soft & 128};
    const unsigned config[] = {(configs[0] >> 12) & 15,
                               (configs[0] >> 20) & 15,
                               (configs[1] >> 12) & 15,
                               configs[2] & 15,
                               (configs[2] >> 16) & 15,
                               configs[3] & 15,
                               (configs[4] >> 20) & 15,
                               (configs[5] >> 16) & 15,
                               (configs[7] >> 24) & 15,
                               e->irq_config >> 28,
                               (configs[15] >> 16) & 15,
                               (configs[15] >> 20) & 15,
                               (configs[15] >> 24) & 15,
                               (configs[15] >> 28) & 15};
    bool selected = false;
    for (unsigned i = 0; i < G_N_ELEMENTS(sources); i++) {
        unsigned level = config[i] >> 1;
        if (!pending[i] || !(config[i] & 1) || level < e->priority_mask) {
            continue;
        }
        /* Equal priorities take the lower source number first. This is the
         * usual convention, assumed rather than measured on an FM-1;
         * sources[] is in ascending order. */
        if (!selected || level > *priority) {
            *number = sources[i];
            *priority = level;
            selected = true;
        }
    }
    return selected;
}
static void adc_irq_input(void *opaque, int number, int level)
{
    FM1PocState *m = opaque;
    m->adc_irq_level = level;
    update_irq(m);
}
static void alnk_irq_input(void *opaque, int number, int level)
{
    FM1PocState *m = opaque;
    m->alnk_irq_level = level;
    update_irq(m);
}
static void timer_irq(FM1TimerState *t)
{
    /* SDK hwi.h assigns TIMER4 source 62; stock's ISR at 0x02034e64
     * clears its pending latch with the same CON bit-14 ACK as TIMER5. */
    update_irq(t->machine);
}
static void fm1_timer_expired(void *opaque)
{
    FM1TimerState *t = opaque;
    /* rearm() leaves an earlier QEMU timer armed; see there. */
    if (!(t->control & 1)) { return; }
    if (qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL) < t->deadline) {
        timer_mod_ns(t->timer, t->deadline);
        return;
    }
    t->pending = true;
    t->expirations++;
    timer_irq(t);
    t->deadline += DIV_ROUND_UP(period_ticks(t) * divider(t) * 1000000000ull, clock_hz(t));
    timer_mod_ns(t->timer, t->deadline);
}
/* Stock rewrites a free-running counter constantly, which only ever moves
 * its deadline later. An already armed QEMU timer is then left in place:
 * the expiry callback re-arms at the true deadline when it fires early, and
 * returns while disabled. Expiry times are unchanged. */
static void rearm(FM1TimerState *t)
{
    t->epoch = qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL);
    if (t->control & 1) {
        uint64_t remaining = period_ticks(t) - (t->counter % period_ticks(t));
        t->deadline = t->epoch + DIV_ROUND_UP(remaining * divider(t) * 1000000000ull,
                                              clock_hz(t));
        if (!timer_pending(t->timer) || timer_expire_time_ns(t->timer) > t->deadline) {
            timer_mod_ns(t->timer, t->deadline);
        }
    }
}
static G_NORETURN void timer_fail(FM1TimerState *t, const char *reason)
{
    g_autofree char *s = g_strdup_printf("unsupported TIMER%u %s", t->number, reason);
    pi32v2_fail(&t->machine->cpu->env, s);
}
static uint64_t timer_read(void *opaque, hwaddr offset, unsigned size)
{
    FM1TimerState *t = opaque;
    switch (offset) {
    case 0: return t->control | (t->pending ? 0x8000 : 0);
    case 4: return fm1_timer_counter(t);
    case 8: return t->period;
    default: timer_fail(t, "register");
    }
}
static void timer_write(void *opaque, hwaddr offset, uint64_t value, unsigned size)
{
    FM1TimerState *t = opaque;
    switch (offset) {
    case 0:
        if (value & ~0xc019ull) {
            timer_fail(t, "clock/control");
        }
        if (value & 0x4000) {
            if (t->pending) { t->acknowledgments++; }
            t->pending = false;
            timer_irq(t);
        }
        /* Acknowledgment clears the latch without restarting timer phase. */
        if ((value & 0x19) == t->control) { return; }
        t->counter = fm1_timer_counter(t);
        t->control = value & 0x19;
        break;
    case 4: t->counter = value; break;
    case 8: t->counter = fm1_timer_counter(t); t->period = value; break;
    default: timer_fail(t, "register");
    }
    rearm(t);
}

/* Digital GPIO and the board's 2x74HC595 chain. A shift/latch edge only
 * reaches the chain when its PA pin is configured as a digital output.
 * Matrix closures pull an enabled input low while a latched column is low.
 * These are wiring/register facts from the guest HAL, not copied driver code. */
static uint32_t gpio_pins(FM1PocState *m)
{
    return m->gpio[0][0] & ~m->gpio[0][2] & m->gpio[0][3];
}
static uint64_t gpio_read(void *opaque, hwaddr offset, unsigned size)
{
    FM1PocState *m = opaque;
    unsigned port = offset / 0x40;
    offset %= 0x40;
    if ((port != 0 && port != 1 && port != 2 && port != 3 && port != 7) ||
        offset >= sizeof(m->gpio[0])) {
        pi32v2_fail(&m->cpu->env, "unsupported GPIO register");
    }
    if (port == 2 || port == 3 || port == 7) {
        if (offset == 4) {
            pi32v2_fail(&m->cpu->env, "unsupported PC GPIO read");
        }
        return m->gpio[port][offset / 4];
    }
    if (offset == 4) {
        uint32_t inputs = m->gpio[port][4] & ~m->gpio[port][5] & m->gpio[port][2] & m->gpio[port][3];
        for (unsigned col = 0; col < 11; col++) {
            uint8_t closures = m->matrix[col] | m->input.matrix[col];

            if (!(m->latched & (1u << col))) {
                if (port == 1) {
                    if (closures & 32) { inputs &= ~(1u << 7); }
                } else {
                    if (closures & 1) { inputs &= ~1u; }
                    for (unsigned row = 1; row < 5; row++) {
                        if (closures & (1u << row)) { inputs &= ~(1u << (row + 4)); }
                    }
                }
            }
        }
        return (m->gpio[port][0] & ~m->gpio[port][2] & m->gpio[port][3]) | inputs;
    }
    if (offset >= sizeof(m->gpio[0])) { pi32v2_fail(&m->cpu->env, "unsupported PA GPIO register"); }
    return m->gpio[port][offset / 4];
}
static void gpio_write(void *opaque, hwaddr offset, uint64_t value, unsigned size)
{
    FM1PocState *m = opaque;
    unsigned port = offset / 0x40;
    offset %= 0x40;
    if ((port != 0 && port != 1 && port != 2 && port != 3 && port != 7) ||
        offset == 4 || offset >= sizeof(m->gpio[0])) {
        pi32v2_fail(&m->cpu->env, "unsupported PA GPIO write");
    }
    if (port != 0) {
        m->gpio[port][offset / 4] = value;
        if (port == 2) { fm1_lcd_set_pins(&m->lcd, m->gpio[2][0], m->iomap_con1, m->gpio[0][0]); }
        else if (port == 3) { fm1_nor_set_pins(&m->nor, m->gpio[3][0], m->iomap_con0); }
        return;
    }
    uint32_t before = gpio_pins(m);
    m->gpio[0][offset / 4] = value;
    uint32_t after = gpio_pins(m);
    if (!(before & 8) && (after & 8)) {
        m->shift = (m->shift << 1) | ((after >> 4) & 1);
        m->shift_edges++;
    }
    if (!(before & 2) && (after & 2)) {
        m->latched = m->shift;
        m->latch_edges++;
    }
    fm1_lcd_set_pins(&m->lcd, m->gpio[2][0], m->iomap_con1, m->gpio[0][0]);
}
/* SPI2 (0x11e00, IRQ 37) as stock drives the 74HC595 chain: a DMA
 * transmit of CNT bytes from ADR, shifted MSB first through PA3/PA4 when
 * IOMAP_CON1 bit 17 routes it there. CON uses the SPI0/1 layout (enable
 * bit 0, receive 0x1000, pending 0x8000 cleared by 0x4000); 0x2000 enables
 * the IRQ, as stock relies on. A byte takes 8 x (BAUD + 1) periods of the
 * 60 MHz peripheral clock, the SPI1 timing measured on an FM-1. */
#define SPI2_ENABLE 1u
#define SPI2_RECEIVE 0x1000u
#define SPI2_IRQ_ENABLE 0x2000u
#define SPI2_ACK 0x4000u
#define SPI2_PENDING 0x8000u
static bool spi2_irq_level(FM1PocState *m)
{
    return (m->spi2_con & SPI2_IRQ_ENABLE) && m->spi2_pending;
}
static void spi2_complete(void *opaque)
{
    FM1PocState *m = opaque;
    for (uint32_t i = 0; i < m->spi2_cnt; i++) {
        uint8_t byte = 0;
        if (address_space_read(&address_space_memory, m->spi2_adr + i,
                               MEMTXATTRS_UNSPECIFIED, &byte, 1) != MEMTX_OK) {
            pi32v2_fail(&m->cpu->env, "SPI2 DMA source is unmapped");
        }
        m->shift = (m->shift << 8) | byte;
        m->shift_edges += 8;
    }
    m->spi2_busy = false;
    m->spi2_pending = true;
    update_irq(m);
}
static uint64_t spi2_read(void *opaque, hwaddr offset, unsigned size)
{
    FM1PocState *m = opaque;
    switch (offset) {
    case 0: return m->spi2_con | (m->spi2_pending ? SPI2_PENDING : 0);
    case 4: return m->spi2_baud;
    case 12: return m->spi2_adr;
    case 16: return m->spi2_cnt;
    default: pi32v2_fail(current_cpu ? cpu_env(current_cpu) : &m->cpu->env,
                         "unsupported SPI2 register read");
    }
}
static void spi2_write(void *opaque, hwaddr offset, uint64_t value, unsigned size)
{
    FM1PocState *m = opaque;
    CPUPi32v2State *env = current_cpu ? cpu_env(current_cpu) : &m->cpu->env;
    switch (offset) {
    case 0:
        if (value & SPI2_RECEIVE || value & ~0xffffull) {
            pi32v2_fail(env, "unsupported SPI2 control");
        }
        if (value & SPI2_ACK) { m->spi2_pending = false; }
        m->spi2_con = value & ~(SPI2_ACK | SPI2_PENDING);
        break;
    case 4: m->spi2_baud = value; break;
    case 12: m->spi2_adr = value; break;
    case 16:
        m->spi2_cnt = value;
        /* Stock writes CNT before enabling; only an enabled write starts. */
        if (!(m->spi2_con & SPI2_ENABLE)) { break; }
        if (!(m->iomap_con1 & 0x20000) || m->spi2_busy) {
            pi32v2_fail(env, "SPI2 transfer needs an idle, PA-routed controller");
        }
        m->spi2_busy = true;
        m->spi2_pending = false;
        timer_mod_ns(m->spi2_timer, qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL) +
                     DIV_ROUND_UP((uint64_t)value * 8 * (m->spi2_baud + 1) * 1000, 60));
        break;
    default: pi32v2_fail(env, "unsupported SPI2 register write");
    }
    update_irq(m);
}
static const MemoryRegionOps spi2_ops = {
    .read = spi2_read, .write = spi2_write, .endianness = DEVICE_LITTLE_ENDIAN,
    .valid = {.min_access_size = 4, .max_access_size = 4},
    .impl = {.min_access_size = 4, .max_access_size = 4},
};
static uint64_t iomap_read(void *opaque, hwaddr offset, unsigned size)
{
    FM1PocState *m = opaque;
    switch (offset) {
    case 0: return m->iomap_con0;
    case 4: return m->iomap_con1;
    case 8: return m->iomap_con2;
    case 12: return m->iomap_con3;
    default: pi32v2_fail(&m->cpu->env, "unsupported IOMAP register");
    }
}
static void iomap_write(void *opaque, hwaddr offset, uint64_t value, unsigned size)
{
    FM1PocState *m = opaque;
    if (!offset) {
        if (value & ~0x20ull) { pi32v2_fail(&m->cpu->env, "unsupported IOMAP_CON0 routing"); }
        m->iomap_con0 = value;
        fm1_nor_set_pins(&m->nor, m->gpio[3][0], m->iomap_con0);
        return;
    }
    /* Reached UART pin selections are independent of NOR/LCD routing.
     * The UART receiver has no external input; no GPIO edge becomes a byte. */
    if (offset == 8 || offset == 12) {
        uint64_t mask = offset == 8 ? 0x3f00 : 0xf0;
        if (value & ~mask) {
            pi32v2_fail(&m->cpu->env, "unsupported UART IOMAP routing");
        }
        if (offset == 8) { m->iomap_con2 = value; }
        else { m->iomap_con3 = value; }
        return;
    }
    /* Bit 4 routes SPI1 to the LCD. Bit 17 routes SPI2 to port C
     * (SDK spi.h): CLK PA3, DO PA4, DI PA2, the 74HC595 chain's pins. */
    if (value & ~0x20010ull) { pi32v2_fail(&m->cpu->env, "unsupported IOMAP_CON1 routing"); }
    m->iomap_con1 = value;
    fm1_lcd_set_pins(&m->lcd, m->gpio[2][0], m->iomap_con1, m->gpio[0][0]);
}
static uint64_t irq_read(void *opaque, hwaddr offset, unsigned size)
{
    Pi32v2CPU *cpu = opaque;
    FM1PocState *m = cpu->machine;
    uint32_t *configs = CPU(cpu)->cpu_index ? m->irq1_configs : m->irq_configs;
    if (offset < 0x80 && !(offset & 3)) { return configs[offset / 4]; }
    switch (offset) {
    case 0x80:
        return ((CPU(cpu)->cpu_index ? m->ttmr1.pending : m->ttmr.pending) ? 1u << 3 : 0) |
               (m->timer1.pending ? 1u << 5 : 0) | (m->alnk_irq_level ? 1u << 11 : 0) |
               (m->lcd.irq_level ? 1u << 16 : 0) | (m->uart.irq_level ? 1u << 20 : 0) |
               (m->adc_irq_level ? 1u << 24 : 0);
    case 0x84: return (spi2_irq_level(m) ? 1u << 5 : 0) | (m->lrct.done ? 1u << 12 : 0) |
                      (m->timers[0].pending ? 1u << 30 : 0) |
                      (m->timers[1].pending ? 0x80000000u : 0);
    case 0x8c: return software_pending(m, CPU(cpu)->cpu_index != 0) << 24;
    case 0xa0: return 0;
    case 0xa8: return cpu->env.priority_mask;
    default: pi32v2_fail(&cpu->env, "unsupported IRQ register");
    }
}
static void irq_write(void *opaque, hwaddr offset, uint64_t value, unsigned size)
{
    Pi32v2CPU *cpu = opaque;
    FM1PocState *m = cpu->machine;
    uint32_t *configs = CPU(cpu)->cpu_index ? m->irq1_configs : m->irq_configs;
    if (offset < 0x80 && !(offset & 3)) {
        /* Any source may be configured; only exception 1, tick timer 3,
         * TIMER1 5, audio 11, SPI1 16, SAR ADC 24, SPI2 37, LRCT 44, TIMER4 62,
         * TIMER5 63 and software
         * 124-127 are raised. Stock FM-1 firmware
         * configures sources it never uses in this machine. */
        configs[offset / 4] = value;
        if (offset == 0x1c) { cpu->env.irq_config = value; }
        update_irq(m);
        return;
    }
    switch (offset) {
    case 0xa0:
        if (CPU(cpu)->cpu_index || (value & ~0xf0ull)) {
            pi32v2_fail(&cpu->env, "unsupported software IRQ request bank or source");
        }
        m->software_latch |= value;
        break;
    case 0xa8:
        if (value > 7) { pi32v2_fail(&cpu->env, "invalid priority mask"); }
        cpu->env.priority_mask = value; break;
    case 0xa4:
        if (value != 255 && (value & ~0xf0ull)) {
            pi32v2_fail(&cpu->env, "unsupported IRQ pending clear");
        }
        if (value == 255 && (m->timers[0].pending || m->timers[1].pending ||
                             m->timer1.pending || m->lrct.done ||
                             m->adc_irq_level || m->lcd.irq_level || m->uart.irq_level ||
                             spi2_irq_level(m))) {
            pi32v2_fail(&cpu->env, "IRQ clear requires a device acknowledgment");
        }
        if (!CPU(cpu)->cpu_index) { m->software_latch &= ~value; }
        break;
    default: pi32v2_fail(&cpu->env, "unsupported IRQ write");
    }
    update_irq(m);
}
static const MemoryRegionOps timer_ops = {
    .read = timer_read, .write = timer_write, .endianness = DEVICE_LITTLE_ENDIAN,
    .valid = {.min_access_size = 4, .max_access_size = 4},
    .impl = {.min_access_size = 4, .max_access_size = 4},
};
static const MemoryRegionOps gpio_ops = {
    .read = gpio_read, .write = gpio_write, .endianness = DEVICE_LITTLE_ENDIAN,
    .valid = {.min_access_size = 4, .max_access_size = 4},
    .impl = {.min_access_size = 4, .max_access_size = 4},
};
static const MemoryRegionOps iomap_ops = {
    .read = iomap_read, .write = iomap_write, .endianness = DEVICE_LITTLE_ENDIAN,
    .valid = {.min_access_size = 4, .max_access_size = 4},
    .impl = {.min_access_size = 4, .max_access_size = 4},
};
static const MemoryRegionOps irq_ops = {
    .read = irq_read, .write = irq_write, .endianness = DEVICE_LITTLE_ENDIAN,
    .valid = {.min_access_size = 4, .max_access_size = 4},
    .impl = {.min_access_size = 4, .max_access_size = 4},
};

static bool fm1_poc_lock(CPUPi32v2State *e, bool acquire)
{
    FM1PocState *m = env_archcpu(e)->machine;
    int core = CPU(env_archcpu(e))->cpu_index;
    if (!m->cpu1) { return true; }
    if (acquire) {
        if (m->lock_owner >= 0 && m->lock_owner != core) { return false; }
        m->lock_owner = core;
    } else if (m->lock_owner == core) {
        m->lock_owner = -1;
        Pi32v2CPU *peer = core ? m->cpu : m->cpu1;
        if (peer->lock_waiting) {
            peer->lock_waiting = false;
            CPU(peer)->halted = peer->held_reset || peer->core_paused;
            qemu_cpu_kick(CPU(peer));
        }
    }
    return true;
}

static uint64_t core_control_read(void *opaque, hwaddr offset, unsigned size)
{
    FM1PocState *m = opaque;
    unsigned core = offset / 4;
    Pi32v2CPU *cpu = core ? m->cpu1 : m->cpu;
    return m->core_control[core] | (cpu && cpu->core_paused ? 0x11 : 0);
}

static void core_control_write(void *opaque, hwaddr offset, uint64_t value, unsigned size)
{
    FM1PocState *m = opaque;
    unsigned core = offset / 4;
    if ((value ^ m->core_control[core]) & ~0x1full ||
        (!core && ((value ^ m->core_control[0]) & 2))) {
        pi32v2_fail(&m->cpu->env, "unsupported core control bits");
    }
    Pi32v2CPU *cpu = core ? m->cpu1 : m->cpu;
    if (!cpu) {
        pi32v2_fail(&m->cpu->env, "core-1 control requires -smp 2");
    }
    if ((value & 0xc) == 0xc) {
        pi32v2_fail(&m->cpu->env, "simultaneous core pause/resume is unsupported");
    }
    uint32_t previous = m->core_control[core];
    /* Bits 2/3 are self-clearing pause/resume commands, not stored enables.
     * Hardware reports a stopped core through bits 0/4. */
    m->core_control[core] = value & ~0x1du;
    if (core && (value & 2)) {
        if (!(previous & 2)) {
            fm1_poc_lock(&cpu->env, false);
            cpu_reset(CPU(cpu));
        }
        cpu->held_reset = true;
    }
    if (value & 4) { cpu->core_paused = true; }
    if (value & 8) {
        cpu->core_paused = false;
        cpu->resume_requested = true;
    }
    if (core && !(value & 2) && cpu->resume_requested && cpu->held_reset) {
        /* Hardware probes confirm the SRAM vector and cnum=1, and execute
         * the stock-style supervisor-to-user RTI handoff. Boot-ROM code,
         * retained reset registers and startup latency remain unmodeled. */
        cpu->env.pc = address_space_ldl(&address_space_memory, 0x01c7fff8,
                                           MEMTXATTRS_UNSPECIFIED, NULL);
        cpu->env.in_irq = true;
        cpu->env.spr[ICFG] = 0x100;
        cpu->held_reset = false;
    }
    CPU(cpu)->halted = cpu->held_reset || cpu->core_paused || cpu->lock_waiting;
    if (CPU(cpu)->halted) { cpu_exit(CPU(cpu)); }
    else { qemu_cpu_kick(CPU(cpu)); }
    update_irq(m);
}

/* JL_SRC sample-rate converter (IRQ 58): stock's startup clears CON0 and
 * stores the remaining configuration. Conversion is unimplemented, so any
 * other CON0 value faults instead of silently producing no output. */
static uint64_t src_read(void *opaque, hwaddr offset, unsigned size)
{
    return ((FM1PocState *)opaque)->src[offset / 4];
}
static void src_write(void *opaque, hwaddr offset, uint64_t value, unsigned size)
{
    FM1PocState *m = opaque;
    if (offset == 0 && (value & ~0x40ull)) {
        pi32v2_fail(current_cpu ? cpu_env(current_cpu) : &m->cpu->env,
                    "SRC conversion is unimplemented");
    }
    m->src[offset / 4] = offset ? value : 0;
}
static const MemoryRegionOps src_ops = {
    .read = src_read, .write = src_write,
    .endianness = DEVICE_LITTLE_ENDIAN,
    .valid = {.min_access_size = 4, .max_access_size = 4},
    .impl = {.min_access_size = 4, .max_access_size = 4},
};

/* JL_RAND R64L/R64H. Hardware entropy would make runs irreproducible, so
 * each read returns the next word of a fixed-seed xorshift64 sequence. */
static uint64_t rand_read(void *opaque, hwaddr offset, unsigned size)
{
    FM1PocState *m = opaque;
    m->rand_state ^= m->rand_state << 13;
    m->rand_state ^= m->rand_state >> 7;
    m->rand_state ^= m->rand_state << 17;
    return (uint32_t)m->rand_state;
}
static void rand_write(void *opaque, hwaddr offset, uint64_t value, unsigned size)
{
    FM1PocState *m = opaque;
    pi32v2_fail(current_cpu ? cpu_env(current_cpu) : &m->cpu->env,
                "write to read-only random number generator");
}
static const MemoryRegionOps rand_ops = {
    .read = rand_read, .write = rand_write,
    .endianness = DEVICE_LITTLE_ENDIAN,
    .valid = {.min_access_size = 4, .max_access_size = 4},
    .impl = {.min_access_size = 4, .max_access_size = 4},
};

/* Inert Wi-Fi/RF: JL_WL (0x14000) and the undocumented baseband/RF windows
 * at 0x20000-0x31fff that stock's startup configures (accesses observed at
 * 0x200xx-0x201xx, 0x280xx, 0x2fcxx-0x2fdxx and 0x303xx-0x313xx).
 * Registers store their last value and no radio exists behind them. The
 * serial RF write port at 0x3101c starts with bit 17 and is polled until
 * that bit clears; with no radio the transfer finishes immediately, so the
 * bit reads as clear. */
#define RF_BASE 0x20000u
#define RF_SERIAL_PORT (0x3101cu - RF_BASE)
/* Stock latches a radio timer by writing 1 to 0x2001c, polling it to zero
 * and reading 0x20020. With no radio the latch completes at once and the
 * timer reads virtual microseconds; its unit is assumed, not measured. */
#define RF_TIMER_LATCH (0x2001cu - RF_BASE)
#define RF_TIMER_VALUE (0x20020u - RF_BASE)
#define RF_SERIAL_BUSY 0x20000u
static uint64_t wl_read(void *opaque, hwaddr offset, unsigned size)
{
    return ((FM1PocState *)opaque)->wl[offset / 4];
}
static void wl_write(void *opaque, hwaddr offset, uint64_t value, unsigned size)
{
    ((FM1PocState *)opaque)->wl[offset / 4] = value;
}
static uint64_t rf_read(void *opaque, hwaddr offset, unsigned size)
{
    uint32_t value = ((FM1PocState *)opaque)->rf[offset / 4];
    return offset == RF_SERIAL_PORT ? value & ~RF_SERIAL_BUSY : value;
}
static void rf_write(void *opaque, hwaddr offset, uint64_t value, unsigned size)
{
    FM1PocState *m = opaque;
    if (offset == RF_TIMER_LATCH && value) {
        m->rf[RF_TIMER_VALUE / 4] = qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL) / 1000;
        value = 0;
    }
    m->rf[offset / 4] = value;
}
/* WLA_CON1-39 (0x11904): RF analog configuration beside WLA_CON0, part of
 * the inert radio. CON1-30 store; read-only CON31-39 read zero. Stock's RF
 * calibration pulses WLA_CON30, then polls bit 5 and takes an 8-bit trim
 * code from bits 8-15. With no radio, calibration is done at once with the
 * mid-scale code 0x80, a chosen value rather than a measured one. */
#define WLA_CON30 29                    /* index: the array starts at CON1 */
static uint64_t wla_read(void *opaque, hwaddr offset, unsigned size)
{
    uint32_t value = ((FM1PocState *)opaque)->wla[offset / 4];
    return offset / 4 == WLA_CON30 ? (value & ~0xff00u) | 0x8020u : value;
}
static void wla_write(void *opaque, hwaddr offset, uint64_t value, unsigned size)
{
    FM1PocState *m = opaque;
    if (offset / 4 >= 30) {
        pi32v2_fail(current_cpu ? cpu_env(current_cpu) : &m->cpu->env,
                    "write to read-only WLA_CON31-39");
    }
    m->wla[offset / 4] = value;
}
static const MemoryRegionOps wla_ops = {
    .read = wla_read, .write = wla_write,
    .endianness = DEVICE_LITTLE_ENDIAN,
    .valid = {.min_access_size = 4, .max_access_size = 4},
    .impl = {.min_access_size = 4, .max_access_size = 4},
};
static const MemoryRegionOps wl_ops = {
    .read = wl_read, .write = wl_write,
    .endianness = DEVICE_LITTLE_ENDIAN,
    .valid = {.min_access_size = 4, .max_access_size = 4},
    .impl = {.min_access_size = 4, .max_access_size = 4},
};
static const MemoryRegionOps rf_ops = {
    .read = rf_read, .write = rf_write,
    .endianness = DEVICE_LITTLE_ENDIAN,
    .valid = {.min_access_size = 4, .max_access_size = 4},
    .impl = {.min_access_size = 4, .max_access_size = 4},
};

static const MemoryRegionOps core_control_ops = {
    .read = core_control_read, .write = core_control_write,
    .endianness = DEVICE_LITTLE_ENDIAN,
    .valid = {.min_access_size = 4, .max_access_size = 4},
    .impl = {.min_access_size = 4, .max_access_size = 4},
};

static const Pi32v2MachineOps machine_ops = {
    .reset_state = fm1_poc_reset_state, .select_irq = fm1_poc_select_irq,
    .fetch_fault = fm1_poc_fetch_fault, .guard_fault = fm1_poc_guard_fault,
    .check_stack = fm1_poc_check_stack,
    .lock = fm1_poc_lock,
};

static bool board_adc_raw(void *opaque, unsigned channel, uint32_t *raw)
{
    FM1PocState *m = opaque;
    /* Functional board defaults, without physical calibration or a claim
     * about converter resolution. The controller retains each raw value. */
    switch (channel) {
    case 3: *raw = 600; return true; /* PB1 */
    case 4: *raw = m->input.master_raw; return true; /* PB6 / MASTER */
    default: return false;
    }
}

static void machine_init(MachineState *ms)
{
    FM1PocState *m = FM1_POC_MACHINE(ms);
    if (ms->smp.cpus == 2 && qemu_tcg_mttcg_enabled()) {
        error_report("fm1-poc -smp 2 requires -accel tcg,thread=single");
        exit(EXIT_FAILURE);
    }
    m->cpu = PI32V2_CPU(cpu_create(TYPE_PI32V2_CPU));
    m->cpu->machine = m;
    m->cpu->ops = &machine_ops;
    fm1_test_configure(m, ms);
    cpu_reset(CPU(m->cpu));
    m->lock_owner = -1;
    m->core_control[0] = 0x01000000;
    m->core_control[1] = 0x01000002;  /* measured: core 1 held in reset */
    if (ms->smp.cpus == 2) {
        m->cpu1 = PI32V2_CPU(object_new(TYPE_PI32V2_CPU));
        m->cpu1->machine = m;
        m->cpu1->ops = &machine_ops;
        m->cpu1->observer_ops = m->cpu->observer_ops;
        m->cpu1->stop_pc = UINT32_MAX;
        m->cpu1->instruction_limit = m->cpu->instruction_limit;
        /* Without core-0 observers, translation is identical for both
         * cores and shared code is translated once. */
        m->cpu1->private_translation = m->cpu->stop_pc != UINT32_MAX ||
                                       m->cpu->frame_pc || m->cpu->loop_pc;
        object_property_set_bool(OBJECT(m->cpu1), "start-powered-off", true, &error_fatal);
        qdev_realize(DEVICE(m->cpu1), NULL, &error_fatal);
    }
    memory_region_add_subregion(get_system_memory(), 0x01c00000, ms->ram);
    /* Cache-side RAM. The SDK linker scripts place free cache ways as RAM at
     * 0x1f20000 (eight 4 KiB I-cache ways, then eight D-cache ways); stock
     * startup zeroes the tag ranges below before using them. Caching itself
     * is not modelled, so a way in use as cache simply behaves as memory. */
    static const struct { hwaddr base, size; const char *name; } cache_ram[] = {
        {0x01f00000, 0x4000, "fm1.cache-tag0"}, {0x01f08000, 0x2000, "fm1.cache-tag1"},
        {0x01f0a000, 0x200, "fm1.cache-tag2"}, {0x01f0b000, 0x200, "fm1.cache-tag3"},
        {0x01f20000, 0x10000, "fm1.cache-ram"},
    };
    for (unsigned i = 0; i < G_N_ELEMENTS(cache_ram); i++) {
        memory_region_init_ram(&m->cache_ram[i], NULL, cache_ram[i].name,
                               cache_ram[i].size, &error_fatal);
        memory_region_add_subregion(get_system_memory(), cache_ram[i].base, &m->cache_ram[i]);
    }
    fm1_test_seed_ram(m);
    m->latched = UINT16_MAX;
    /* Explicit application handoff with SFC routed to the board NOR. This
     * same device topology is used by every image and optional test fixture. */
    m->gpio[3][0] = 1;
    m->iomap_con0 = 0x20;
    fm1_nor_init(&m->nor, OBJECT(m), m->cpu, ms->kernel_filename);
    fm1_nor_set_pins(&m->nor, m->gpio[3][0], m->iomap_con0);
    for (unsigned i = 0; i < 3; i++) {
        FM1TimerState *t = i == 2 ? &m->timer1 : &m->timers[i];
        t->machine = m;
        t->number = i == 2 ? 1 : i + 4;
        t->timer = timer_new_ns(QEMU_CLOCK_VIRTUAL, fm1_timer_expired, t);
        memory_region_init_io(&t->mmio, OBJECT(m), &timer_ops, t,
                              i == 2 ? "fm1.timer1" : i ? "fm1.timer5" : "fm1.timer4", 12);
        fm1_sfr_map(0x10400 + t->number * 0x100, &t->mmio);
    }
    memory_region_init_io(&m->gpio_mmio, OBJECT(m), &gpio_ops, m, "fm1.gpio", 0x1e0);
    fm1_sfr_map(0x50000, &m->gpio_mmio);
    memory_region_init_io(&m->irq_mmio, OBJECT(m), &irq_ops, m->cpu, "fm1.irq", 0xac);
    fm1_sfr_map(0x01eef100, &m->irq_mmio);
    m->irq = qdev_get_gpio_in(DEVICE(m->cpu), 0);
    memory_region_init_io(&m->core_control_mmio, OBJECT(m), &core_control_ops,
                          m, "fm1.core-control", 8);
    fm1_sfr_map(0x01eee000, &m->core_control_mmio);
    m->spi2_timer = timer_new_ns(QEMU_CLOCK_VIRTUAL, spi2_complete, m);
    memory_region_init_io(&m->spi2_mmio, OBJECT(m), &spi2_ops, m, "fm1.spi2", 20);
    fm1_sfr_map(0x11e00, &m->spi2_mmio);
    memory_region_init_io(&m->src_mmio, OBJECT(m), &src_ops, m, "fm1.src", 36);
    fm1_sfr_map(0x14300, &m->src_mmio);
    m->rand_state = 0x464d312d454d55ull;     /* any fixed nonzero seed */
    memory_region_init_io(&m->wla_mmio, OBJECT(m), &wla_ops, m, "fm1.wla-inert",
                          sizeof(m->wla));
    fm1_sfr_map(0x11904, &m->wla_mmio);
    memory_region_init_io(&m->wl_mmio, OBJECT(m), &wl_ops, m, "fm1.wl-inert",
                          sizeof(m->wl));
    fm1_sfr_map(0x14000, &m->wl_mmio);
    memory_region_init_io(&m->rf_mmio, OBJECT(m), &rf_ops, m, "fm1.rf-inert",
                          sizeof(m->rf));
    /* The window spans many SFR pages and shares none with other blocks. */
    memory_region_add_subregion(get_system_memory(), RF_BASE, &m->rf_mmio);
    memory_region_init_io(&m->rand_mmio, OBJECT(m), &rand_ops, m, "fm1.rand", 8);
    fm1_sfr_map(0x13b00, &m->rand_mmio);
    if (m->cpu1) {
        m->irq1 = qdev_get_gpio_in(DEVICE(m->cpu1), 0);
        memory_region_init_io(&m->irq1_mmio, OBJECT(m), &irq_ops, m->cpu1,
                              "fm1.core1-irq", 0xac);
        fm1_sfr_map(0x01eef300, &m->irq1_mmio);
        fm1_system_init_core1(&m->system1, &m->system, OBJECT(m), m->cpu1);
    }
    fm1_system_init(&m->system, OBJECT(m), m->cpu);
    fm1_syscon_init(&m->syscon, OBJECT(m), m->cpu);
    fm1_sfr_map(0x10010, &m->syscon.mmio[FM1_SYSCON_CLK_CON1]);
    fm1_sfr_map(0x10014, &m->syscon.mmio[FM1_SYSCON_CLK_CON2]);
    fm1_sfr_map(0x51030, &m->syscon.mmio[FM1_SYSCON_IOMAP_CON5]);
    m->alnk_irq = qemu_allocate_irq(alnk_irq_input, m, 11);
    object_initialize_child(OBJECT(m), "alnk0", &m->alnk, TYPE_FM1_ALNK);
    fm1_alnk_bind(&m->alnk, m->cpu, &m->syscon);
    sysbus_realize(SYS_BUS_DEVICE(&m->alnk), &error_fatal);
    fm1_sfr_map(0x12e00, sysbus_mmio_get_region(SYS_BUS_DEVICE(&m->alnk), 0));
    sysbus_connect_irq(SYS_BUS_DEVICE(&m->alnk), 0, m->alnk_irq);
    /* Zero is the board's functional application handoff choice, not an
     * established analog-block reset value. Optional test seeds are separate. */
    fm1_analog_init(&m->analog, OBJECT(m), m->cpu, m->analog_initial_wla_con0);
    fm1_sfr_map(0x11900, &m->analog.mmio);
    object_initialize_child(OBJECT(m), "sar-adc", &m->adc, TYPE_FM1_ADC);
    fm1_adc_bind(&m->adc, m->cpu, &m->analog, (1u << 3) | (1u << 4),
                 board_adc_raw, m);
    sysbus_realize(SYS_BUS_DEVICE(&m->adc), &error_fatal);
    sysbus_connect_irq(SYS_BUS_DEVICE(&m->adc), 0, qemu_allocate_irq(adc_irq_input, m, 24));
    fm1_sfr_map(0x13100, sysbus_mmio_get_region(SYS_BUS_DEVICE(&m->adc), 0));
    fm1_usb_init(&m->usb, OBJECT(m), m->cpu);
    fm1_uart_init(&m->uart, OBJECT(m), m->cpu, &m->syscon, ttmr_irq, m);
    fm1_crc_init(&m->crc, OBJECT(m), m->cpu);
    fm1_ttmr_init(&m->ttmr, OBJECT(m), m->cpu, ttmr_irq, m);
    fm1_lrct_init(&m->lrct, OBJECT(m), &m->system, ttmr_irq, m);
    if (m->cpu1) {
        fm1_ttmr_init(&m->ttmr1, OBJECT(m), m->cpu1, ttmr_irq, m);
    }
    fm1_sfr_map(0x12100, &m->uart.mmio);
    fm1_lcd_init(&m->lcd, OBJECT(m), m->cpu, ttmr_irq, m);
    memory_region_init_io(&m->iomap_mmio, OBJECT(m), &iomap_ops, m, "fm1.iomap", 16);
    fm1_sfr_map(0x5101c, &m->iomap_mmio);
    fm1_lcd_set_pins(&m->lcd, m->gpio[2][0], m->iomap_con1, m->gpio[0][0]);
    object_initialize_child(OBJECT(m), "board-input", &m->input, TYPE_FM1_INPUT);
    fm1_input_bind(&m->input, m->cpu);
    qdev_realize(DEVICE(&m->input), NULL, &error_fatal);
    fm1_test_start(m);
}
static void machine_class_init(ObjectClass *oc, const void *data)
{
    MachineClass *mc = MACHINE_CLASS(oc);
    mc->desc = "FM-1 pi32v2 application machine (partial hardware model)";
    mc->init = machine_init;
    mc->default_cpu_type = TYPE_PI32V2_CPU;
    mc->default_ram_size = 512 * 1024;
    mc->default_ram_id = "fm1.sram";
    mc->max_cpus = 2;
    mc->min_cpus = mc->default_cpus = 1;
    mc->no_floppy = true; mc->no_cdrom = true; mc->no_parallel = true;
}
static const TypeInfo machine_type = {
    .name = TYPE_FM1_POC_MACHINE, .parent = TYPE_MACHINE,
    .instance_size = sizeof(FM1PocState), .class_init = machine_class_init,
};
static void register_types(void) { type_register_static(&machine_type); }
type_init(register_types)
