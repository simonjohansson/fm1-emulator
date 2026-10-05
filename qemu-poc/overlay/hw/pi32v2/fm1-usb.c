/* SPDX-License-Identifier: GPL-2.0-or-later */
/* Fresh implementation of the saved diagnostic's USB register interface.
 * The tracked guest describes a cold boot without a cable as a nonresponsive
 * SIE: usb_start times out, leaves USB off and retries from its main loop.
 * This module retains actual requests and exposes that explicit controller
 * state. It never injects host traffic, returns descriptors, edits guest USB
 * results or creates a successful SIE completion. No Rust implementation or
 * guest USB protocol code is copied into QEMU. */
#include "qemu/osdep.h"
#include "exec/address-spaces.h"
#include "fm1-usb.h"

#define USB_BASE 0x11800u
#define USB_PADS_BASE 0x51000u
#define USB_CLOCK_BASE 0x10010u
#define USB_SIE_ON 4u
#define USB_SOF_ACK 0x1000u
#define USB_SIE_READ 0x4000u
#define SRAM_BASE 0x01c00000u
#define SRAM_END 0x01c80000u

static G_NORETURN void usb_fail(FM1PocUSB *usb, const char *reason)
{
    pi32v2_fail(&usb->cpu->env, reason);
}

static void abandon_request(FM1PocUSB *usb)
{
    if (usb->request_pending) {
        unsigned slot = (usb->requests - 1) % ARRAY_SIZE(usb->recent_requests);
        usb->recent_polls[slot] = usb->current_poll_reads;
        usb->abandoned_requests++;
        usb->request_pending = false;
    }
}

static bool known_sie_request(uint32_t value)
{
    /* Only the initialization registers used by this cold boot are supported.
     * Reads of those same registers also remain pending without a SIE clock. */
    unsigned reg = value >> 8;
    uint8_t byte = value;
    if (value & USB_SIE_READ) {
        reg = (value & ~USB_SIE_READ) >> 8;
        if (byte) { return false; }
        return reg == 1 || (reg >= 7 && reg <= 11);
    }
    switch (reg) {
    case 1: return byte == 0x60;
    case 7: return byte == 1;
    case 8: case 9: case 10: return byte == 0;
    case 11: return byte == 7;
    default: return false;
    }
}

static void bridge_write(FM1PocUSB *usb, uint32_t value)
{
    if (!value) {
        abandon_request(usb);
        usb->bridge = 0;
        usb->current_poll_reads = 0;
        usb->bridge_clears++;
        return;
    }
    if (!(usb->control & USB_SIE_ON) ||
        (value & ~(USB_SIE_READ | 0x1fffu)) ||
        !known_sie_request(value)) {
        usb_fail(usb, "unsupported USB SIE bridge request");
    }
    abandon_request(usb);
    usb->bridge = value;
    usb->current_poll_reads = 0;
    unsigned slot = usb->requests % ARRAY_SIZE(usb->recent_requests);
    usb->recent_requests[slot] = value;
    usb->recent_polls[slot] = 0;
    usb->requests++;
    usb->request_pending = true;
    /* No completion timer: this explicit cold host-absent state has no SIE
     * clock response. The guest polls and decides when to abandon the request. */
}

static uint64_t usb_read(void *opaque, hwaddr offset, unsigned size)
{
    FM1PocUSB *usb = opaque;
    switch (offset) {
    case 0: return usb->control; /* No SOF source in this cold unclocked state. */
    case 4:
        if (usb->request_pending) {
            usb->current_poll_reads++;
            usb->bridge_poll_reads++;
            unsigned slot = (usb->requests - 1) % ARRAY_SIZE(usb->recent_requests);
            usb->recent_polls[slot] = usb->current_poll_reads;
        }
        return usb->bridge; /* Actual request retained; DONE remains clear. */
    case 0x18: return usb->rx_address[0];
    case 0x3c: return usb->rx_address[4];
    }
    if (offset >= 8 && offset <= 0x14) {
        return usb->endpoint_count[(offset - 8) / 4];
    }
    if (offset >= 0x1c && offset <= 0x30) {
        unsigned ep = 1 + (offset - 0x1c) / 8;
        return offset & 4 ? usb->tx_address[ep] : usb->rx_address[ep];
    }
    usb_fail(usb, "unsupported USB0 register read");
}

static void check_buffer(FM1PocUSB *usb, uint32_t address)
{
    if ((address & 3) || address < SRAM_BASE ||
        (uint64_t)address + 68 > SRAM_END) {
        usb_fail(usb, "USB endpoint buffer must be aligned and entirely in SRAM");
    }
}

static void usb_write(void *opaque, hwaddr offset, uint64_t value, unsigned size)
{
    FM1PocUSB *usb = opaque;
    switch (offset) {
    case 0:
        if (value & ~(0xf8003dull | USB_SOF_ACK)) {
            usb_fail(usb, "unsupported USB0 control bits");
        }
        usb->control = value & ~USB_SOF_ACK;
        if (!usb->control) {
            usb->controller_off_writes++;
            abandon_request(usb);
        }
        return;
    case 4:
        bridge_write(usb, value);
        return;
    case 0x18:
        check_buffer(usb, value);
        usb->rx_address[0] = value;
        return;
    case 0x3c:
        check_buffer(usb, value);
        usb->rx_address[4] = value;
        return;
    }
    if (offset >= 8 && offset <= 0x14) {
        if (value) {
            usb_fail(usb, "USB endpoint packet DMA is unimplemented");
        }
        usb->endpoint_count[(offset - 8) / 4] = 0;
        return;
    }
    if (offset >= 0x1c && offset <= 0x30) {
        unsigned ep = 1 + (offset - 0x1c) / 8;
        check_buffer(usb, value);
        if (offset & 4) { usb->tx_address[ep] = value; }
        else { usb->rx_address[ep] = value; }
        return;
    }
    usb_fail(usb, "unsupported USB0 register write");
}

static uint64_t pads_read(void *opaque, hwaddr offset, unsigned size)
{
    return ((FM1PocUSB *)opaque)->pads;
}

static void pads_write(void *opaque, hwaddr offset, uint64_t value, unsigned size)
{
    FM1PocUSB *usb = opaque;
    if (value & ~0x1efcull) { usb_fail(usb, "unsupported USB pad configuration"); }
    usb->pads = value;
}

static uint64_t clock_read(void *opaque, hwaddr offset, unsigned size)
{
    return ((FM1PocUSB *)opaque)->clock_control;
}

static void clock_write(void *opaque, hwaddr offset, uint64_t value, unsigned size)
{
    FM1PocUSB *usb = opaque;
    if (value & ~3ull) { usb_fail(usb, "unsupported USB clock selector fields"); }
    usb->clock_control = value;
}

#define USB_OPS(name) \
static const MemoryRegionOps name##_ops = { \
    .read = name##_read, .write = name##_write, .endianness = DEVICE_LITTLE_ENDIAN, \
    .valid = {.min_access_size = 4, .max_access_size = 4}, \
    .impl = {.min_access_size = 4, .max_access_size = 4}, \
}
USB_OPS(usb);
USB_OPS(pads);
USB_OPS(clock);

void fm1_usb_init(FM1PocUSB *usb, Object *owner, Pi32v2CPU *cpu)
{
    usb->cpu = cpu;
    usb->host_connected = false;
    usb->sie_clock_available = false;
    memory_region_init_io(&usb->mmio, owner, &usb_ops, usb, "fm1.usb0-cold", 0x40);
    memory_region_add_subregion(get_system_memory(), USB_BASE, &usb->mmio);
    memory_region_init_io(&usb->pads_mmio, owner, &pads_ops, usb, "fm1.usb-pads", 4);
    memory_region_add_subregion(get_system_memory(), USB_PADS_BASE, &usb->pads_mmio);
    memory_region_init_io(&usb->clock_mmio, owner, &clock_ops, usb, "fm1.usb-clock", 4);
    memory_region_add_subregion(get_system_memory(), USB_CLOCK_BASE, &usb->clock_mmio);
}
