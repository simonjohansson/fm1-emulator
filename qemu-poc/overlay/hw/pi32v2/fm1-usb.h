/* SPDX-License-Identifier: GPL-2.0-or-later */
#ifndef HW_PI32V2_FM1_USB_H
#define HW_PI32V2_FM1_USB_H

#include "exec/memory.h"
#include "cpu.h"

/* Private cold-power-on, host-absent controller model. The SIE bridge has no
 * clock response in this state; the unchanged guest handles its own timeout
 * and retry. Responsive USB, enumeration and packet DMA are not modeled. */
typedef struct FM1PocUSB {
    Pi32v2CPU *cpu;
    MemoryRegion mmio, pads_mmio, clock_mmio;
    uint32_t control, bridge, pads, clock_control;
    uint32_t endpoint_count[4], tx_address[4], rx_address[5];
    uint32_t recent_requests[6];
    uint64_t recent_polls[6];
    uint64_t requests, bridge_poll_reads, controller_off_writes, bridge_clears;
    uint64_t abandoned_requests, current_poll_reads, dma_packets;
    bool request_pending, host_connected, sie_clock_available;
} FM1PocUSB;

void fm1_usb_init(FM1PocUSB *usb, Object *owner, Pi32v2CPU *cpu);

#endif
