/* SPDX-License-Identifier: GPL-2.0-or-later */
/* A bounded USB host for a descriptor-advertised CDC ACM console. Requests
 * and payloads cross the controller's packet/IRQ interface; no guest state
 * or firmware identity selects enumeration or console behavior. */
#include "qemu/osdep.h"
#include "qemu/error-report.h"
#include "qapi/error.h"
#include "fm1-usb.h"

/* The private controller has TX buffers 1..3 and RX buffers 1..4. */
#define HOST_MAX_IN_EP 3
#define HOST_MAX_OUT_EP 4
#define HOST_PACKET_SIZE 64
#define HOST_CONTROL_TIMEOUT 2000

enum {
    HOST_DEVICE,
    HOST_ADDRESS,
    HOST_CONFIG_HEADER,
    HOST_CONFIG,
    HOST_SET_CONFIG,
    HOST_DTR,
    HOST_RUNNING,
};

static unsigned little16(const uint8_t *data)
{
    return data[0] | (data[1] << 8);
}

static void host_error(FM1USBHost *host, const char *reason)
{
    if (!host->failed) {
        error_report("FM-1 USB console: %s (stage %u, response %u/%u)",
                     reason, host->stage, host->response_length,
                     host->requested_length);
    }
    host->failed = true;
    host->waiting = false;
    host->completed = false;
    host->configured = false;
}

static int console_can_read(void *opaque)
{
    FM1USBHost *host = opaque;
    return host->configured && !host->failed ?
        sizeof(host->input) - host->input_length : 0;
}

static void console_read(void *opaque, const uint8_t *data, int length)
{
    FM1USBHost *host = opaque;
    unsigned tail;

    if (length < 0 || (unsigned)length > sizeof(host->input) - host->input_length) {
        host_error(host, "character backend exceeded input queue capacity");
        return;
    }
    tail = (host->input_head + host->input_length) % sizeof(host->input);
    for (unsigned i = 0; i < (unsigned)length; i++) {
        host->input[(tail + i) % sizeof(host->input)] = data[i];
    }
    host->input_length += length;
}

static void console_flush(FM1USBHost *host)
{
    if (host->output_length) {
        unsigned contiguous = MIN(host->output_length,
                                  sizeof(host->output) - host->output_head);
        int written = qemu_chr_fe_write(&host->console,
                                        host->output + host->output_head,
                                        contiguous);
        if (written > 0) {
            host->output_head = (host->output_head + written) %
                                sizeof(host->output);
            host->output_length -= written;
        }
    }
}

/* Select the first alternate-zero ACM function and its Union-advertised
 * data interface, or the following data interface when no Union is present. */
static bool parse_configuration(FM1USBHost *host)
{
    const uint8_t *data = host->response;
    unsigned length = host->response_length;
    int control = -1, data_interface = -1, current = -1;
    bool current_is_control = false;

    if (length < 9 || data[0] < 9 || data[1] != 2 ||
        little16(data + 2) != length || !data[5]) {
        return false;
    }
    host->configuration = data[5];
    for (unsigned offset = 0; offset < length;) {
        unsigned size = data[offset];
        unsigned type;

        if (size < 2 || size > length - offset) {
            return false;
        }
        type = data[offset + 1];
        if (type == 4) {
            if (size < 9) {
                return false;
            }
            current = data[offset + 3] == 0 ? data[offset + 2] : -1;
            current_is_control = false;
            if (current >= 0 && control < 0 && data[offset + 5] == 2 &&
                data[offset + 6] == 2) {
                control = current;
                current_is_control = true;
            } else if (current >= 0 && control >= 0 &&
                       data_interface < 0 && data[offset + 5] == 10) {
                data_interface = current;
            }
        } else if (type == 0x24 && current_is_control && size >= 5 &&
                   data[offset + 2] == 6 && data[offset + 3] == control) {
            data_interface = data[offset + 4];
        }
        offset += size;
    }
    if (control < 0 || data_interface < 0) {
        return false;
    }
    host->control_interface = control;
    current = -1;
    for (unsigned offset = 0; offset < length; offset += data[offset]) {
        unsigned size = data[offset];
        if (data[offset + 1] == 4) {
            current = data[offset + 3] == 0 && data[offset + 5] == 10 ?
                      data[offset + 2] : -1;
        } else if (data[offset + 1] == 5 && current == data_interface) {
            unsigned address, endpoint, packet;
            if (size < 7) {
                return false;
            }
            if ((data[offset + 3] & 3) != 2) {
                continue;
            }
            address = data[offset + 2];
            endpoint = address & 15;
            packet = little16(data + offset + 4);
            if (!endpoint || (address & 0x70) || !packet ||
                packet > HOST_PACKET_SIZE) {
                return false;
            }
            if (address & 0x80) {
                if (endpoint > HOST_MAX_IN_EP || host->output_endpoint) {
                    return false;
                }
                host->output_endpoint = endpoint;
                host->output_packet_size = packet;
            } else {
                if (endpoint > HOST_MAX_OUT_EP || host->input_endpoint) {
                    return false;
                }
                host->input_endpoint = endpoint;
                host->input_packet_size = packet;
            }
        }
    }
    return host->input_endpoint && host->output_endpoint;
}

static bool complete_request(FM1USBHost *host)
{
    const uint8_t *data = host->response;

    switch (host->stage) {
    case HOST_DEVICE:
        if (host->response_length != 18 || data[0] != 18 || data[1] != 1 ||
            (data[7] != 8 && data[7] != 16 && data[7] != 32 && data[7] != 64)) {
            host_error(host, "invalid device descriptor");
            return false;
        }
        break;
    case HOST_CONFIG_HEADER:
        if (host->response_length != 9 || data[0] != 9 || data[1] != 2) {
            host_error(host, "invalid configuration descriptor header");
            return false;
        }
        host->configuration_length = little16(data + 2);
        if (host->configuration_length < 9 ||
            host->configuration_length > sizeof(host->response)) {
            host_error(host, "configuration exceeds bounded descriptor capacity");
            return false;
        }
        break;
    case HOST_CONFIG:
        if (!parse_configuration(host)) {
            host_error(host, "configuration has no supported CDC ACM endpoints");
            return false;
        }
        break;
    case HOST_DTR:
        host->configured = true;
        qemu_chr_fe_accept_input(&host->console);
        break;
    }
    host->stage++;
    host->wait_ticks = 0;
    return true;
}

static void request_setup(FM1USBHost *host)
{
    uint8_t packet[8] = {0};
    unsigned value = 0, index = 0, length = 0;

    switch (host->stage) {
    case HOST_DEVICE:
        packet[0] = 0x80;
        packet[1] = 6;
        value = 0x100;
        length = 18;
        break;
    case HOST_ADDRESS:
        packet[1] = 5;
        value = 1;
        break;
    case HOST_CONFIG_HEADER:
    case HOST_CONFIG:
        packet[0] = 0x80;
        packet[1] = 6;
        value = 0x200;
        length = host->stage == HOST_CONFIG_HEADER ? 9 :
                 host->configuration_length;
        break;
    case HOST_SET_CONFIG:
        packet[1] = 9;
        value = host->configuration;
        break;
    case HOST_DTR:
        packet[0] = 0x21;
        packet[1] = 0x22;
        value = 1; /* DTR: the host is ready for the CDC console. */
        index = host->control_interface;
        break;
    default:
        return;
    }
    packet[2] = value;
    packet[3] = value >> 8;
    packet[4] = index;
    packet[5] = index >> 8;
    packet[6] = length;
    packet[7] = length >> 8;
    if (!fm1_usb_host_packet(host->usb, 0, packet, sizeof(packet))) {
        if (++host->wait_ticks > HOST_CONTROL_TIMEOUT) {
            host_error(host, "endpoint zero did not accept SETUP");
        }
        return;
    }
    host->response_length = 0;
    host->requested_length = length;
    host->control_in = packet[0] & 0x80;
    host->waiting = true;
    host->wait_ticks = 0;
}

void fm1_usb_host_tick(FM1USBHost *host)
{
    uint8_t packet[HOST_PACKET_SIZE];
    unsigned length;

    if (!host->enabled) {
        return;
    }
    console_flush(host);
    if (host->failed || !fm1_usb_host_ready(host->usb)) {
        return;
    }
    if (!host->started) {
        host->started = true;
        host->delay_ticks = 10;
        fm1_usb_host_bus_reset(host->usb);
        return;
    }
    if (host->delay_ticks) {
        host->delay_ticks--;
        return;
    }
    if (host->waiting || host->completed) {
        if (++host->wait_ticks > HOST_CONTROL_TIMEOUT) {
            host_error(host, "control transfer timed out");
            return;
        }
    }
    if (host->completed) {
        if (host->status_pending) {
            /* Finish a control IN transfer with its zero-length OUT status.
             * The next SETUP waits for a separate controller tick. */
            if (fm1_usb_host_packet(host->usb, 0, packet, 0)) {
                host->status_pending = false;
            }
            return;
        }
        host->completed = false;
        if (!complete_request(host)) {
            return;
        }
    }
    if (!host->waiting && host->stage != HOST_RUNNING) {
        request_setup(host);
        return;
    }
    if (host->configured && host->input_length) {
        length = MIN(host->input_length, host->input_packet_size);
        for (unsigned i = 0; i < length; i++) {
            packet[i] = host->input[(host->input_head + i) % sizeof(host->input)];
        }
        if (fm1_usb_host_packet(host->usb, host->input_endpoint, packet, length)) {
            host->input_head = (host->input_head + length) % sizeof(host->input);
            host->input_length -= length;
            qemu_chr_fe_accept_input(&host->console);
        }
    }
}

bool fm1_usb_host_tx(FM1USBHost *host, unsigned endpoint,
                     const uint8_t *data, unsigned length)
{
    unsigned tail;

    if (!host->enabled) {
        return true;
    }
    if (!endpoint) {
        if (!host->failed && host->waiting && length) {
            if (!host->control_in ||
                length > host->requested_length - host->response_length) {
                host_error(host, "unexpected control response length");
            } else {
                memcpy(host->response + host->response_length, data, length);
                host->response_length += length;
            }
        }
        return true;
    }
    /* CDC interrupt notifications are independent of its byte stream. */
    if (!host->output_endpoint || endpoint != host->output_endpoint) {
        return true;
    }
    if (length > host->output_packet_size) {
        host_error(host, "CDC IN packet exceeds advertised maximum");
        return true;
    }
    console_flush(host);
    if (length > sizeof(host->output) - host->output_length) {
        return false;
    }
    tail = (host->output_head + host->output_length) % sizeof(host->output);
    for (unsigned i = 0; i < length; i++) {
        host->output[(tail + i) % sizeof(host->output)] = data[i];
    }
    host->output_length += length;
    console_flush(host);
    return true;
}

void fm1_usb_host_control_done(FM1USBHost *host, bool stalled)
{
    if (!host->enabled || host->failed || !host->waiting) {
        return;
    }
    if (stalled) {
        host_error(host, "device stalled an enumeration request");
        return;
    }
    host->waiting = false;
    host->completed = true;
    host->status_pending = host->control_in;
}

void fm1_usb_host_reset(FM1USBHost *host)
{
    host->started = false;
    host->waiting = false;
    host->completed = false;
    host->status_pending = false;
    host->failed = false;
    host->configured = false;
    host->stage = HOST_DEVICE;
    host->delay_ticks = host->wait_ticks = 0;
    host->response_length = host->requested_length = 0;
    host->configuration_length = 0;
    host->configuration = host->control_interface = 0;
    host->input_endpoint = host->output_endpoint = 0;
    host->input_packet_size = host->output_packet_size = 0;
    host->input_head = host->input_length = 0;
    /* Accepted console output stays queued across a controller reset. */
}

void fm1_usb_host_init(FM1USBHost *host, FM1PocUSB *usb, Chardev *backend)
{
    host->usb = usb;
    host->enabled = backend != NULL;
    fm1_usb_host_reset(host);
    if (backend) {
        qemu_chr_fe_init(&host->console, backend, &error_fatal);
        qemu_chr_fe_set_handlers(&host->console, console_can_read, console_read,
                                 NULL, NULL, host, NULL, true);
    }
}
