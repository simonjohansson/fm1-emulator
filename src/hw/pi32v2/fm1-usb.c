/* SPDX-License-Identifier: GPL-2.0-or-later */
/* USB SIE bridge and endpoint DMA. Host enumeration/terminal transport lives
 * separately in fm1-usb-host.c; only programmed DMA buffers carry guest bytes. */
#include "qemu/osdep.h"
#include "system/address-spaces.h"
#include "system/system.h"
#include "qapi/error.h"
#include "fm1-usb.h"

#define USB_BASE 0x11800u
#define USB_PADS_BASE 0x51000u
#define USB_SIE_ON 4u
#define USB_SOF_ACK 0x1000u
#define USB_SIE_READ 0x4000u
#define USB_SIE_DONE 0x8000u
#define USB_SOF_PENDING 0x2000u
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

/* The compact indexed SIE register layout exposes byte registers through the
 * word bridge. Interrupt status clears on read; endpoint status persists until
 * the guest services the packet. */
bool fm1_usb_host_ready(FM1PocUSB *usb)
{
    return usb->sie_clock_available && (usb->control & USB_SIE_ON) &&
           (usb->sie[1] & 0x40);
}

void fm1_usb_host_bus_reset(FM1PocUSB *usb)
{
    memset(usb->endpoint, 0, sizeof(usb->endpoint));
    memset(usb->tx_pending, 0, sizeof(usb->tx_pending));
    memset(usb->tx_data_end, 0, sizeof(usb->tx_data_end));
    usb->sie[0] = usb->sie[2] = usb->sie[4] = usb->sie[14] = 0;
    usb->sie[6] |= 4;
    usb->frame = 0;
    usb->control_status_out = false;
}

bool fm1_usb_host_packet(FM1PocUSB *usb, unsigned ep,
                         const uint8_t *bytes, unsigned length)
{
    unsigned csr = ep ? 4 : 1;
    /* After a final control IN packet the SIE accepts the OUT status token
     * itself; it is not a new RX data/SETUP packet for the guest to parse. */
    if (!ep && !length && usb->control_status_out && fm1_usb_host_ready(usb)) {
        usb->control_status_out = false;
        return true;
    }
    if (!fm1_usb_host_ready(usb) || ep >= ARRAY_SIZE(usb->endpoint) ||
        length > 64 || !usb->rx_address[ep] ||
        (usb->endpoint[ep][csr] & 1)) {
        return false;
    }
    if (length && address_space_write(&address_space_memory,
            usb->rx_address[ep], MEMTXATTRS_UNSPECIFIED, bytes, length) != MEMTX_OK) {
        usb_fail(usb, "USB receive DMA failed");
    }
    usb->endpoint[ep][6] = length;
    usb->endpoint[ep][7] = 0;
    usb->endpoint[ep][csr] |= 1;
    usb->sie[ep ? 4 : 2] |= 1u << ep;
    usb->dma_packets++;
    return true;
}

static void transmit_packet(FM1PocUSB *usb, unsigned ep)
{
    uint8_t bytes[1023];                 /* EP4 isochronous: up to 1023 bytes; the others 64 */
    unsigned length = usb->endpoint_count[ep];
    uint32_t address = ep ? usb->tx_address[ep] : usb->rx_address[0];
    if (!address || length > (ep == 4 ? sizeof(bytes) : 64u)) {
        usb_fail(usb, "invalid USB transmit DMA buffer or length");
    }
    if (length && address_space_read(&address_space_memory, address,
            MEMTXATTRS_UNSPECIFIED, bytes, length) != MEMTX_OK) {
        usb_fail(usb, "USB transmit DMA failed");
    }
    if (!fm1_usb_host_tx(&usb->host, ep, bytes, length)) {
        return;
    }
    usb->tx_pending[ep] = false;
    usb->endpoint[ep][1] &= ~(ep ? 1 : 2);
    usb->sie[2] |= 1u << ep;
    usb->dma_packets++;
    if (!ep && usb->tx_data_end[ep]) {
        usb->tx_data_end[ep] = false;
        usb->control_status_out = true;
        fm1_usb_host_control_done(&usb->host, false);
    }
}

static uint8_t sie_read(FM1PocUSB *usb, unsigned reg)
{
    if (reg < 16) {
        uint8_t value = usb->sie[reg];
        if (reg == 12) { return usb->frame; }
        if (reg == 13) { return usb->frame >> 8; }
        if (reg == 2 || reg == 4 || reg == 6) { usb->sie[reg] = 0; }
        return value;
    }
    if (reg >= 16 && reg < 24 && usb->sie[14] < 5) {
        return usb->endpoint[usb->sie[14]][reg - 16];
    }
    usb_fail(usb, "unsupported USB indexed register read");
}

static void sie_write(FM1PocUSB *usb, unsigned reg, uint8_t value)
{
    if (reg < 16) {
        if (reg == 14 && value >= 5) {
            usb_fail(usb, "unsupported USB endpoint index");
        }
        usb->sie[reg] = value;
        return;
    }
    unsigned ep = usb->sie[14];
    if (reg < 16 || reg >= 24 || ep >= 5) {
        usb_fail(usb, "unsupported USB indexed register write");
    }
    if (reg == 17 && !ep) {
        uint8_t *csr = &usb->endpoint[0][1];
        if (value & 0x40) { *csr &= ~1; }
        if (value & 0x80) { *csr &= ~0x10; }
        if (!(value & 4)) { *csr &= ~4; }
        if (value & 0x20) {
            *csr |= 4;
            fm1_usb_host_control_done(&usb->host, true);
        } else if (value & 2) {
            *csr |= 2;
            usb->tx_pending[0] = true;
            usb->tx_data_end[0] = value & 8;
            transmit_packet(usb, 0);
        } else if (value & 8) {
            fm1_usb_host_control_done(&usb->host, false);
        }
        return;
    }
    if (ep && reg == 20 && (value & 0x10)) {
        /* RXCSR FLUSHFIFO services the received packet and self-clears. */
        value &= ~0x11;
        usb->endpoint[ep][6] = usb->endpoint[ep][7] = 0;
    }
    usb->endpoint[ep][reg - 16] = value;
    if (reg == 17 && ep && (value & 1)) {
        if (ep >= ARRAY_SIZE(usb->tx_pending)) {
            usb_fail(usb, "unsupported USB transmit endpoint");
        }
        usb->tx_pending[ep] = true;
        transmit_packet(usb, ep);
    }
}

static void usb_frame(void *opaque)
{
    FM1PocUSB *usb = opaque;
    if (fm1_usb_host_ready(usb)) {
        usb->frame = (usb->frame + 1) & 0x7ff;
        usb->control |= USB_SOF_PENDING;
        for (unsigned ep = 0; ep < ARRAY_SIZE(usb->tx_pending); ep++) {
            if (usb->tx_pending[ep]) { transmit_packet(usb, ep); }
        }
    }
    fm1_usb_host_tick(&usb->host);
    timer_mod(usb->frame_timer,
              qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL) + 1000000);
}

static void bridge_write(FM1PocUSB *usb, uint32_t value)
{
    if (!value && (!usb->sie_clock_available || !(usb->control & USB_SIE_ON))) {
        abandon_request(usb);
        usb->bridge = 0;
        usb->current_poll_reads = 0;
        usb->bridge_clears++;
        return;
    }
    if (!(usb->control & USB_SIE_ON) ||
        (value & ~(USB_SIE_READ | 0x1fffu)) ||
        (!usb->sie_clock_available && !known_sie_request(value))) {
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
    if (usb->sie_clock_available) {
        unsigned reg = (value & ~USB_SIE_READ) >> 8;
        if (value & USB_SIE_READ) {
            usb->bridge = (value & ~0xffu) | sie_read(usb, reg);
        } else {
            sie_write(usb, reg, value);
        }
        usb->bridge |= USB_SIE_DONE;
        usb->request_pending = false;
    }
    /* With no host clock, DONE remains clear and the guest owns its timeout. */
}

static uint64_t usb_read(void *opaque, hwaddr offset, unsigned size)
{
    FM1PocUSB *usb = opaque;
    switch (offset) {
    case 0: return usb->control;
    case 4:
        if (usb->request_pending) {
            usb->current_poll_reads++;
            usb->bridge_poll_reads++;
            unsigned slot = (usb->requests - 1) % ARRAY_SIZE(usb->recent_requests);
            usb->recent_polls[slot] = usb->current_poll_reads;
        }
        return usb->bridge;
    case 0x18: return usb->rx_address[0];
    case 0x3c: return usb->rx_address[4];
    case 0x34: return usb->endpoint_count[4];
    case 0x38: return usb->tx_address[4];
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
    case 0: {
        uint64_t allowed = 0xf8003dull | USB_SOF_ACK |
                           (usb->sie_clock_available ? USB_SOF_PENDING : 0);
        if (value & ~allowed) {
            usb_fail(usb, "unsupported USB0 control bits");
        }
        uint32_t sof = usb->control & USB_SOF_PENDING;
        usb->control = (value & ~(USB_SOF_ACK | USB_SOF_PENDING)) |
                       ((value & USB_SOF_ACK) || !(value & USB_SIE_ON) ? 0 : sof);
        if (!usb->control) {
            usb->controller_off_writes++;
            abandon_request(usb);
            if (usb->host_connected) { fm1_usb_host_reset(&usb->host); }
        }
        return;
    }
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
    case 0x34:
        /* EP4, the isochronous IN endpoint (Felucca 1.0's USB audio input),
         * has its own count and transmit address (SDK usb_write_ep_cnt /
         * usb_set_dma_taddr, ep 4). A full-speed iso packet is <= 1023 bytes. */
        if (value && !usb->sie_clock_available) {
            usb_fail(usb, "USB endpoint packet DMA is unimplemented");
        }
        if (value > 1023) { usb_fail(usb, "unsupported USB endpoint DMA length"); }
        usb->endpoint_count[4] = value;
        return;
    case 0x38:
        if ((value & 3) || value < SRAM_BASE || (uint64_t)value + 1023 > SRAM_END) {
            usb_fail(usb, "USB endpoint buffer must be aligned and entirely in SRAM");
        }
        usb->tx_address[4] = value;
        return;
    }
    if (offset >= 8 && offset <= 0x14) {
        if (value && !usb->sie_clock_available) {
            usb_fail(usb, "USB endpoint packet DMA is unimplemented");
        }
        if (value > 64) {
            usb_fail(usb, "unsupported USB endpoint DMA length");
        }
        usb->endpoint_count[(offset - 8) / 4] = value;
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

#define USB_OPS(name) \
static const MemoryRegionOps name##_ops = { \
    .read = name##_read, .write = name##_write, .endianness = DEVICE_LITTLE_ENDIAN, \
    .valid = {.min_access_size = 4, .max_access_size = 4}, \
    .impl = {.min_access_size = 4, .max_access_size = 4}, \
}
USB_OPS(usb);
USB_OPS(pads);

void fm1_usb_init(FM1PocUSB *usb, Object *owner, Pi32v2CPU *cpu)
{
    usb->cpu = cpu;
    Chardev *backend = serial_hd(0);
    usb->host_connected = backend != NULL;
    usb->sie_clock_available = backend != NULL;
    fm1_usb_host_init(&usb->host, usb, backend);
    if (backend) {
        usb->frame_timer = timer_new_ns(QEMU_CLOCK_VIRTUAL, usb_frame, usb);
        timer_mod(usb->frame_timer,
                  qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL) + 1000000);
    }
    memory_region_init_io(&usb->mmio, owner, &usb_ops, usb, "fm1.usb0-cold", 0x40);
    memory_region_add_subregion(get_system_memory(), USB_BASE, &usb->mmio);
    memory_region_init_io(&usb->pads_mmio, owner, &pads_ops, usb, "fm1.usb-pads", 4);
    memory_region_add_subregion(get_system_memory(), USB_PADS_BASE, &usb->pads_mmio);
}
