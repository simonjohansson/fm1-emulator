/* SPDX-License-Identifier: GPL-2.0-or-later */
#ifndef HW_PI32V2_FM1_ALNK_H
#define HW_PI32V2_FM1_ALNK_H

#include "hw/core/sysbus.h"
#include "qemu/timer.h"
#include "cpu.h"
#include "fm1-syscon.h"

#define FM1_ALNK_HALF_WORDS 512u
#define FM1_ALNK_HALF_BYTES (FM1_ALNK_HALF_WORDS * 4u)
#define FM1_ALNK_FRAME_RATE 44100u

#define TYPE_FM1_ALNK "fm1-alnk"
OBJECT_DECLARE_SIMPLE_TYPE(FM1PocALNK, FM1_ALNK)

/* Private functional transmitter for the reached Felucca configuration.
 * Samples remain runtime guest data. No firmware or Rust implementation is
 * copied or linked into this GPL-2.0-or-later device. */
struct FM1PocALNK {
    SysBusDevice parent_obj;
    Pi32v2CPU *cpu;
    FM1PocSyscon *syscon;
    MemoryRegion mmio;
    QEMUTimer *timer;
    qemu_irq irq;
    uint16_t control0, control1, half_words;
    uint8_t pending, control3, active_half, last_half;
    uint32_t dma_address;
    bool enabled, irq_level;
    bool validators_registered;
    int64_t epoch, deadline;
    uint64_t scheduled_halves, completions, acknowledgments;
    uint64_t coalesced_completions, skipped_captures;
    uint64_t sample_words, sample_frames, nonzero_words;
    uint32_t sample_digest, latest_half_bytes;
    uint8_t latest_half[FM1_ALNK_HALF_BYTES];
};

/* Bind the composition-owned interfaces before sysbus_realize(). Address
 * mapping and IRQ wiring remain with SoC composition. */
void fm1_alnk_bind(FM1PocALNK *alnk, Pi32v2CPU *cpu, FM1PocSyscon *syscon);

#endif
