/* SPDX-License-Identifier: GPL-2.0-or-later */
/* Fresh implementation from reached register/width and guest protocol facts.
 * The sole supported configuration transmits 256 stereo frames per half.
 * 44100 Hz is a documented functional clock matching the selected firmware's
 * generated FS constant, not a calibrated model of the boot PLL or codec.
 * DMA samples are observed at completion boundaries; late callbacks cannot
 * reconstruct earlier SRAM contents and explicitly count skipped captures.
 * Optional host output consumes these captured samples. Receiver channels
 * and auxiliary pending sources are not implemented. Other unsupported
 * register/configuration accesses fail explicitly. */
#include "qemu/osdep.h"
#include "qemu/bswap.h"
#include "qemu/module.h"
#include "qapi/error.h"
#include "hw/core/irq.h"
#include "hw/core/resettable.h"
#include "system/address-spaces.h"
#include "fm1-alnk.h"

#define SRAM_BASE 0x01c00000u
#define SRAM_END 0x01c80000u
#define DMA_ENABLE 0x0800u
#define ACTIVE_HALF 0x8000u
#define HALF_PENDING 0x80u
#define HALF_FRAMES (FM1_ALNK_HALF_WORDS / 2u)
#define PERIOD_NUMERATOR (HALF_FRAMES * 1000000000ull)

static G_NORETURN void alnk_fail(FM1PocALNK *a, const char *reason)
{
    pi32v2_fail(&a->cpu->env, reason);
}

static void update_irq(FM1PocALNK *a)
{
    a->irq_level = a->enabled && (a->pending & HALF_PENDING);
    qemu_set_irq(a->irq, a->irq_level);
}

static int64_t next_deadline(FM1PocALNK *a)
{
    /* Split the rational period to avoid the short overflow horizon of
     * half_count * 256 * 1e9. Rounding occurs on the cumulative remainder. */
    uint64_t n = a->scheduled_halves + 1;
    uint64_t whole = PERIOD_NUMERATOR / FM1_ALNK_FRAME_RATE;
    uint64_t remainder = PERIOD_NUMERATOR % FM1_ALNK_FRAME_RATE;
    if (n > (INT64_MAX - (uint64_t)a->epoch) / (whole + 1)) {
        alnk_fail(a, "ALNK0 virtual completion deadline overflow");
    }
    return a->epoch + n * whole +
           DIV_ROUND_UP(n * remainder, FM1_ALNK_FRAME_RATE);
}

static void capture_half(FM1PocALNK *a, unsigned half)
{
    uint32_t start = a->dma_address + half * FM1_ALNK_HALF_BYTES;
    for (unsigned offset = 0; offset < FM1_ALNK_HALF_BYTES; offset += 256) {
        if (address_space_read(&address_space_memory, start + offset,
                               MEMTXATTRS_UNSPECIFIED, a->latest_half + offset,
                               256) != MEMTX_OK) {
            alnk_fail(a, "ALNK0 DMA could not read guest SRAM");
        }
    }
    for (unsigned offset = 0; offset < FM1_ALNK_HALF_BYTES; offset++) {
        a->sample_digest = (a->sample_digest ^ a->latest_half[offset]) *
                           16777619u;
    }
    for (unsigned offset = 0; offset < FM1_ALNK_HALF_BYTES; offset += 4) {
        if (ldl_le_p(a->latest_half + offset)) {
            a->nonzero_words++;
        }
    }
    a->sample_words += FM1_ALNK_HALF_WORDS;
    a->sample_frames += HALF_FRAMES;
    a->last_half = half;
    a->latest_half_bytes = FM1_ALNK_HALF_BYTES;
}

static void completed(void *opaque)
{
    FM1PocALNK *a = opaque;
    int64_t now = qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL);
    if (!a->enabled || now < a->deadline) {
        alnk_fail(a, "ALNK0 completion outside an enabled deadline");
    }
    uint64_t elapsed = now - a->epoch;
    uint64_t frames = (elapsed / 1000000000ull) * FM1_ALNK_FRAME_RATE +
                      (elapsed % 1000000000ull) * FM1_ALNK_FRAME_RATE /
                      1000000000ull;
    uint64_t reached = frames / HALF_FRAMES;
    uint64_t due = reached - a->scheduled_halves;
    if (!due) { alnk_fail(a, "ALNK0 deadline did not complete a half"); }
    /* Keep the real level latch, rather than queueing one ISR per deadline.
     * If callbacks skipped boundaries, only the newest completed half can
     * be sampled from current SRAM; earlier contents are not fabricated. */
    a->coalesced_completions += due - !(a->pending & HALF_PENDING);
    a->skipped_captures += due - 1;
    unsigned finished_half = (a->active_half + due - 1) & 1;
    capture_half(a, finished_half);
    fm1_audio_push(&a->output, a->latest_half, a->latest_half_bytes);
    a->active_half = (a->active_half + due) & 1;
    a->scheduled_halves = reached;
    a->completions += due;
    a->pending |= HALF_PENDING;
    update_irq(a);
    a->deadline = next_deadline(a);
    timer_mod_ns(a->timer, a->deadline);
}

static void check_configuration(FM1PocALNK *a)
{
    if (a->control0 != (0x0180u | DMA_ENABLE) ||
        a->control1 != 0x5000u || a->control3 != 0x83u ||
        a->half_words != FM1_ALNK_HALF_WORDS ||
        fm1_syscon_get(a->syscon, FM1_SYSCON_CLK_CON2) ||
        fm1_syscon_get(a->syscon, FM1_SYSCON_IOMAP_CON5)) {
        alnk_fail(a, "unsupported ALNK0 enabled configuration");
    }
    if ((a->dma_address & 3) || a->dma_address < SRAM_BASE ||
        (uint64_t)a->dma_address + 2u * FM1_ALNK_HALF_BYTES > SRAM_END) {
        alnk_fail(a, "ALNK0 double buffer must be aligned and entirely in SRAM");
    }
}

static void control0_write(FM1PocALNK *a, uint16_t value)
{
    if (value & ~(0x0180u | DMA_ENABLE | ACTIVE_HALF)) {
        alnk_fail(a, "unsupported ALNK0 CON0 bits");
    }
    uint16_t control = value & ~ACTIVE_HALF;
    if (a->enabled && ((control ^ a->control0) & ~DMA_ENABLE)) {
        alnk_fail(a, "ALNK0 configuration changed while DMA is enabled");
    }
    a->control0 = control;
    bool enabled = control & DMA_ENABLE;
    if (enabled && !a->enabled) {
        check_configuration(a);
        a->epoch = qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL);
        a->scheduled_halves = 0;
        a->active_half = 0;
        a->deadline = next_deadline(a);
        timer_mod_ns(a->timer, a->deadline);
    } else if (!enabled && a->enabled) {
        timer_del(a->timer);
        a->deadline = 0;
    }
    if (enabled != a->enabled) {
        fm1_audio_set_enabled(&a->output, enabled);
    }
    a->enabled = enabled;
    update_irq(a);
}

static bool correct_width(hwaddr offset, unsigned size)
{
    switch (offset) {
    case 0: case 4: case 0x20: return size == 2;
    case 8: case 12: return size == 1;
    case 0x1c: return size == 4;
    default: return false;
    }
}

static uint64_t alnk_read(void *opaque, hwaddr offset, unsigned size)
{
    FM1PocALNK *a = opaque;
    if (!correct_width(offset, size)) {
        alnk_fail(a, "unsupported ALNK0 register read or width");
    }
    switch (offset) {
    case 0: return a->control0 | (a->active_half ? ACTIVE_HALF : 0);
    case 4: return a->control1;
    case 8: return a->pending;
    case 12: return a->control3;
    default: alnk_fail(a, "ALNK0 DMA address and length are write-only");
    }
}

static void alnk_write(void *opaque, hwaddr offset, uint64_t value,
                       unsigned size)
{
    FM1PocALNK *a = opaque;
    if (!correct_width(offset, size)) {
        alnk_fail(a, "unsupported ALNK0 register write or width");
    }
    switch (offset) {
    case 0: control0_write(a, value); return;
    case 8: {
        /* Pending bits are read-only. RMW acknowledgments may write them
         * back; low bits clear the matching high bits and self-clear. */
        uint8_t cleared = a->pending & ((value & 15u) << 4);
        if (cleared & HALF_PENDING) { a->acknowledgments++; }
        a->pending &= ~cleared;
        update_irq(a);
        return;
    }
    case 4:
        if (value != 0 && value != 0x1000 && value != 0x5000) {
            alnk_fail(a, "unsupported ALNK0 CON1 configuration");
        }
        if (a->enabled && value != a->control1) {
            alnk_fail(a, "ALNK0 configuration changed while DMA is enabled");
        }
        a->control1 = value;
        return;
    case 12:
        if (value != 0 && value != 3 && value != 0x83) {
            alnk_fail(a, "unsupported ALNK0 CON3 configuration");
        }
        if (a->enabled && value != a->control3) {
            alnk_fail(a, "ALNK0 configuration changed while DMA is enabled");
        }
        a->control3 = value;
        return;
    case 0x1c:
        if (a->enabled) { alnk_fail(a, "ALNK0 DMA address changed while enabled"); }
        a->dma_address = value;
        return;
    case 0x20:
        if (a->enabled) { alnk_fail(a, "ALNK0 DMA length changed while enabled"); }
        if (value != FM1_ALNK_HALF_WORDS) {
            alnk_fail(a, "unsupported ALNK0 DMA half length");
        }
        a->half_words = value;
        return;
    }
    g_assert_not_reached();
}

static void validate_clock_write(void *opaque, uint32_t old_value,
                                 uint32_t new_value)
{
    FM1PocALNK *a = opaque;
    if (a->enabled && new_value != old_value) {
        alnk_fail(a, "ALNK0 clock changed while DMA is enabled");
    }
}

static void validate_iomap_write(void *opaque, uint32_t old_value,
                                 uint32_t new_value)
{
    FM1PocALNK *a = opaque;
    if (a->enabled && new_value != old_value) {
        alnk_fail(a, "ALNK0 routing changed while DMA is enabled");
    }
}

static const MemoryRegionOps alnk_ops = {
    .read = alnk_read, .write = alnk_write,
    .endianness = DEVICE_LITTLE_ENDIAN,
    .valid = {.min_access_size = 1, .max_access_size = 4},
    .impl = {.min_access_size = 1, .max_access_size = 4},
};
static void alnk_reset_enter(Object *obj, ResetType type)
{
    FM1PocALNK *a = FM1_ALNK(obj);

    /* Only local state belongs to enter. Keep the QOM object, bindings,
     * shared syscon words, guest SRAM and IRQ connection intact. */
    timer_del(a->timer);
    fm1_audio_set_enabled(&a->output, false);
    a->control0 = a->control1 = a->half_words = 0;
    a->pending = a->control3 = a->active_half = a->last_half = 0;
    a->dma_address = 0;
    a->enabled = a->irq_level = false;
    a->epoch = a->deadline = 0;
    a->scheduled_halves = a->completions = a->acknowledgments = 0;
    a->coalesced_completions = a->skipped_captures = 0;
    a->sample_words = a->sample_frames = a->nonzero_words = 0;
    a->sample_digest = 2166136261u;
    a->latest_half_bytes = 0;
    memset(a->latest_half, 0, sizeof(a->latest_half));
}

static void alnk_reset_hold(Object *obj, ResetType type)
{
    FM1PocALNK *a = FM1_ALNK(obj);
    qemu_irq_lower(a->irq);
}

static void alnk_instance_init(Object *obj)
{
    FM1PocALNK *a = FM1_ALNK(obj);
    SysBusDevice *sbd = SYS_BUS_DEVICE(obj);

    a->timer = timer_new_ns(QEMU_CLOCK_VIRTUAL, completed, a);
    a->sample_digest = 2166136261u;
    memory_region_init_io(&a->mmio, obj, &alnk_ops, a, "fm1.alnk0", 0x24);
    sysbus_init_mmio(sbd, &a->mmio);
    sysbus_init_irq(sbd, &a->irq);
}

static void alnk_realize(DeviceState *dev, Error **errp)
{
    FM1PocALNK *a = FM1_ALNK(dev);

    if (!a->cpu || !a->syscon) {
        error_setg(errp, "ALNK requires composition-owned CPU and syscon bindings");
        return;
    }
    if (!fm1_audio_init(&a->output, errp)) {
        return;
    }
    fm1_syscon_set_validator(a->syscon, FM1_SYSCON_CLK_CON2,
                              validate_clock_write, a);
    fm1_syscon_set_validator(a->syscon, FM1_SYSCON_IOMAP_CON5,
                              validate_iomap_write, a);
    a->validators_registered = true;
}

static void alnk_unrealize(DeviceState *dev)
{
    FM1PocALNK *a = FM1_ALNK(dev);

    device_cold_reset(dev);
    fm1_audio_cleanup(&a->output);
    if (a->validators_registered) {
        fm1_syscon_clear_validator(a->syscon, FM1_SYSCON_CLK_CON2,
                                    validate_clock_write, a);
        fm1_syscon_clear_validator(a->syscon, FM1_SYSCON_IOMAP_CON5,
                                    validate_iomap_write, a);
        a->validators_registered = false;
    }
}

static void alnk_instance_finalize(Object *obj)
{
    FM1PocALNK *a = FM1_ALNK(obj);

    /* Qdev unparent unrealizes a realized child before finalization. */
    g_assert(!a->validators_registered && !a->output.voice &&
             !a->output.shutdown_registered);
    timer_free(a->timer);
}

static const Property alnk_properties[] = {
    DEFINE_AUDIO_PROPERTIES(FM1PocALNK, output.backend),
};

static void alnk_class_init(ObjectClass *klass, const void *data)
{
    DeviceClass *dc = DEVICE_CLASS(klass);
    ResettableClass *rc = RESETTABLE_CLASS(klass);

    dc->realize = alnk_realize;
    dc->unrealize = alnk_unrealize;
    device_class_set_props(dc, alnk_properties);
    /* CPU/syscon bindings and physical wiring are supplied by composition;
     * this private controller cannot be created independently with -device. */
    dc->user_creatable = false;
    dc->hotpluggable = false;
    rc->phases.enter = alnk_reset_enter;
    rc->phases.hold = alnk_reset_hold;
}

static const TypeInfo alnk_info = {
    .name = TYPE_FM1_ALNK,
    .parent = TYPE_SYS_BUS_DEVICE,
    .instance_size = sizeof(FM1PocALNK),
    .instance_init = alnk_instance_init,
    .instance_finalize = alnk_instance_finalize,
    .class_init = alnk_class_init,
};

static void alnk_register_types(void)
{
    type_register_static(&alnk_info);
}
type_init(alnk_register_types)

void fm1_alnk_bind(FM1PocALNK *a, Pi32v2CPU *cpu, FM1PocSyscon *syscon)
{
    g_assert(!DEVICE(a)->realized && !a->cpu && !a->syscon);
    a->cpu = cpu;
    a->syscon = syscon;
}
