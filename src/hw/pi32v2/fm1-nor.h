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

/* Private NOR/SPI0 state. Writes last for this session only; the supplied
 * firmware file is never modified and no persistent host format is added. */
typedef struct FM1PocNOR {
    MemoryRegion spi_mmio, sfc_mmio, encryption_mmio, xip;
    QEMUTimer *transfer_timer, *write_timer;
    Pi32v2CPU *cpu;
    uint8_t *bytes;
    uint32_t control, buffer, sfc_control;
    uint32_t pd_out, iomap_con0, plain_low, plain_high;
    uint32_t address;
    uint8_t encryption_control, command, phase;
    uint8_t transfer_byte;
    bool busy, pending, transfer_receive, selected;
    bool write_enabled, write_busy, ignore_command, program_data;
    uint8_t write_command, page_buffer[256];
    uint32_t program_base, write_address;
    uint64_t transfers, completed_transfers, acknowledgments;
    uint64_t transactions, jedec_commands, status_commands, read_commands;
    uint64_t received_bytes, read_bytes, sfc_disables, sfc_restores;
} FM1PocNOR;

void fm1_nor_init(FM1PocNOR *nor, Object *owner, Pi32v2CPU *cpu,
                  const char *raw_path);
void fm1_nor_set_pins(FM1PocNOR *nor, uint32_t pd_out,
                      uint32_t iomap_con0);
bool fm1_nor_xip_enabled(const FM1PocNOR *nor);
/* Instruction fetches only: the guard kind refusing size bytes at address, or
 * -1. Data reads fault through the XIP device while SFC is disabled. */
int fm1_nor_fetch_fault(FM1PocNOR *nor, uint32_t address, unsigned size);
G_NORETURN void fm1_nor_guard_fault(FM1PocNOR *nor, unsigned kind);

#endif
