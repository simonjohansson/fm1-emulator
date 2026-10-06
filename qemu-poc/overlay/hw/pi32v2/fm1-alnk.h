/* SPDX-License-Identifier: GPL-2.0-or-later */
#ifndef HW_PI32V2_FM1_ALNK_H
#define HW_PI32V2_FM1_ALNK_H

#include "exec/memory.h"
#include "hw/irq.h"
#include "qemu/timer.h"
#include "cpu.h"

#define FM1_ALNK_HALF_WORDS 512u
#define FM1_ALNK_HALF_BYTES (FM1_ALNK_HALF_WORDS * 4u)
#define FM1_ALNK_FRAME_RATE 44100u

/* Private functional transmitter for the reached Felucca configuration.
 * Samples remain runtime guest data. No firmware or Rust implementation is
 * copied or linked into this GPL-2.0-or-later device. */
typedef struct FM1PocALNK {
    Pi32v2CPU *cpu;
    MemoryRegion mmio, clock_mmio, iomap_mmio;
    QEMUTimer *timer;
    qemu_irq irq;
    uint16_t control0, control1, half_words;
    uint8_t pending, control3, active_half, last_half;
    uint32_t dma_address, clock_control, iomap_control;
    bool enabled, irq_level;
    int64_t epoch, deadline;
    uint64_t scheduled_halves, completions, acknowledgments;
    uint64_t coalesced_completions, skipped_captures;
    uint64_t sample_words, sample_frames, nonzero_words;
    uint32_t sample_digest, latest_half_bytes;
    uint8_t latest_half[FM1_ALNK_HALF_BYTES];
} FM1PocALNK;

void fm1_alnk_init(FM1PocALNK *alnk, Object *owner, Pi32v2CPU *cpu,
                  qemu_irq irq);

#endif
