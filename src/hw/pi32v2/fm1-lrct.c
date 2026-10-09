/* SPDX-License-Identifier: GPL-2.0-or-later */
/* LRC measurement timer (JL_LRCT CON, NUM; IRQ 44). Enabling it counts
 * 480 MHz cycles over 32 << n periods of the RC32K oscillator (P3_LRC_CON0
 * bit 0), n being CON bits 1-5. Completion sets CON bit 7, latches NUM and
 * raises the IRQ; writing bit 6 clears bit 7. Measured on a real FM-1 with
 * stock's n = 1: completion 250-375 us after enabling, NUM 130538 and
 * 130553, i.e. an RC32K near 235 kHz. One measurement per enable; NUM holds
 * its value. With the oscillator off a measurement never completes. */
#include "qemu/osdep.h"
#include "fm1-lrct.h"
#include "fm1-sfr.h"

#define LRCT_ENABLE 1u
#define LRCT_CLEAR 0x40u
#define LRCT_DONE 0x80u
#define LRCT_COUNTS_PER_64 130538u     /* 480 MHz cycles per 64 RC32K periods */

static G_NORETURN void lrct_fail(FM1PocLRCT *l, const char *reason)
{
    pi32v2_fail(current_cpu ? cpu_env(current_cpu) : &l->system->cpu->env, reason);
}

static void complete(void *opaque)
{
    FM1PocLRCT *l = opaque;
    l->num = l->measured;
    l->done = true;
    l->completions++;
    l->update_irq(l->opaque);
}

static void start(FM1PocLRCT *l)
{
    unsigned n = (l->control >> 1) & 31;
    if (n > 20) { lrct_fail(l, "unsupported LRCT period selection"); }
    if (!fm1_system_lrc_enabled(l->system)) { return; }
    uint64_t counts = (uint64_t)LRCT_COUNTS_PER_64 * (32u << n) / 64;
    l->measured = counts;
    timer_mod_ns(l->timer, qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL) +
                 DIV_ROUND_UP(counts * 25, 12));
}

static uint64_t lrct_read(void *opaque, hwaddr offset, unsigned size)
{
    FM1PocLRCT *l = opaque;
    switch (offset) {
    case 0: return l->control | (l->done ? LRCT_DONE : 0);
    case 4: return l->num;
    default: lrct_fail(l, "unsupported LRCT register read");
    }
}

static void lrct_write(void *opaque, hwaddr offset, uint64_t value, unsigned size)
{
    FM1PocLRCT *l = opaque;
    if (offset == 4) {
        l->num = value;     /* stock saves and restores NUM around low power */
        return;
    }
    if (offset != 0 || value & ~(uint64_t)(0x3f | LRCT_CLEAR | LRCT_DONE)) {
        lrct_fail(l, "unsupported LRCT register write");
    }
    if (value & LRCT_CLEAR) {
        l->done = false;
        l->update_irq(l->opaque);
    }
    bool running = l->control & LRCT_ENABLE;
    l->control = value & 0x3f;
    if (!(l->control & LRCT_ENABLE)) {
        timer_del(l->timer);
    } else if (!running) {
        start(l);
    }
}

static const MemoryRegionOps lrct_ops = {
    .read = lrct_read, .write = lrct_write, .endianness = DEVICE_LITTLE_ENDIAN,
    .valid = {.min_access_size = 4, .max_access_size = 4},
    .impl = {.min_access_size = 4, .max_access_size = 4},
};

void fm1_lrct_init(FM1PocLRCT *l, Object *owner, FM1PocSystem *system,
                   void (*update_irq)(void *opaque), void *opaque)
{
    l->system = system;
    l->update_irq = update_irq;
    l->opaque = opaque;
    l->timer = timer_new_ns(QEMU_CLOCK_VIRTUAL, complete, l);
    memory_region_init_io(&l->mmio, owner, &lrct_ops, l, "fm1.lrct", 8);
    fm1_sfr_map(0x13600, &l->mmio);
}
