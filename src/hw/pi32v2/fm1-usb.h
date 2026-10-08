/* SPDX-License-Identifier: GPL-2.0-or-later */
#ifndef HW_PI32V2_FM1_USB_H
#define HW_PI32V2_FM1_USB_H

#include "system/memory.h"
#include "cpu.h"
#include "qemu/timer.h"
#include "fm1-usb-host.h"

/* Private controller state. An attached character backend supplies the USB
 * host clock; without one, retain the cold, unclocked controller. */
typedef struct FM1PocUSB {
    Pi32v2CPU *cpu;
    MemoryRegion mmio, pads_mmio;
    uint32_t control, bridge, pads;
    uint32_t endpoint_count[5], tx_address[5], rx_address[5];
    uint32_t recent_requests[6];
    uint64_t recent_polls[6];
    uint64_t requests, bridge_poll_reads, controller_off_writes, bridge_clears;
    uint64_t abandoned_requests, current_poll_reads, dma_packets;
    bool request_pending, host_connected, sie_clock_available;
    uint8_t sie[16], endpoint[5][8];
    bool tx_pending[5], tx_data_end[5];
    bool control_status_out;
    uint16_t frame;
    QEMUTimer *frame_timer;
    FM1USBHost host;
} FM1PocUSB;

void fm1_usb_init(FM1PocUSB *usb, Object *owner, Pi32v2CPU *cpu);

bool fm1_usb_host_ready(FM1PocUSB *usb);
void fm1_usb_host_bus_reset(FM1PocUSB *usb);
bool fm1_usb_host_packet(FM1PocUSB *usb, unsigned ep,
                         const uint8_t *bytes, unsigned length);

#endif
