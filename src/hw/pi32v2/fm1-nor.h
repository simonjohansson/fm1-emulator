/* SPDX-License-Identifier: GPL-2.0-or-later */
#ifndef HW_PI32V2_FM1_NOR_H
#define HW_PI32V2_FM1_NOR_H

#include "system/memory.h"
#include "qemu/timer.h"
#include "cpu.h"

#define FM1_NOR_SIZE 0x100000u
#define FM1_NOR_XIP_BASE 0x02000000u
#define FM1_NOR_XIP_OFFSET 0x4000u
#define FM1_NOR_XIP_SIZE (FM1_NOR_SIZE - FM1_NOR_XIP_OFFSET)

/* Private NOR/SPI0 state for the unencrypted diagnostic application handoff.
 * Program/erase and package decryption are outside this read-only model. */
typedef struct FM1PocNOR {
    MemoryRegion spi_mmio, sfc_mmio, encryption_mmio, xip;
    QEMUTimer *transfer_timer;
    Pi32v2CPU *cpu;
    uint8_t *bytes;
    uint32_t control, buffer, sfc_control;
    uint32_t pd_out, iomap_con0, plain_low, plain_high;
    uint32_t address;
    uint8_t encryption_control, command, phase;
    uint8_t transfer_byte;
    bool busy, pending, transfer_receive, selected;
    uint64_t transfers, completed_transfers, acknowledgments;
    uint64_t transactions, jedec_commands, status_commands, read_commands;
    uint64_t received_bytes, read_bytes, sfc_disables, sfc_restores;
} FM1PocNOR;

void fm1_nor_init(FM1PocNOR *nor, Object *owner, Pi32v2CPU *cpu,
                  const char *raw_path);
void fm1_nor_set_pins(FM1PocNOR *nor, uint32_t pd_out,
                      uint32_t iomap_con0);
bool fm1_nor_xip_enabled(const FM1PocNOR *nor);
/* The CPU must use this at execution time as well as for data accesses:
 * cached TCG blocks otherwise avoid a new ROM fetch when SFC is disabled. */
void fm1_nor_check_access(FM1PocNOR *nor, uint32_t address,
                          unsigned size, bool write);

#endif
