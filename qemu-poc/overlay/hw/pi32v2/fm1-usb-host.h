/* SPDX-License-Identifier: GPL-2.0-or-later */
#ifndef HW_PI32V2_FM1_USB_HOST_H
#define HW_PI32V2_FM1_USB_HOST_H

#include "chardev/char-fe.h"

typedef struct FM1PocUSB FM1PocUSB;

#define FM1_USB_HOST_DESCRIPTOR_SIZE 1024
#define FM1_USB_HOST_QUEUE_SIZE 4096

typedef struct FM1USBHost {
    FM1PocUSB *usb;
    CharFrontend console;
    bool enabled, started, waiting, completed, status_pending, failed;
    bool control_in, configured;
    unsigned stage, delay_ticks, wait_ticks;
    unsigned response_length, requested_length, configuration_length;
    uint8_t configuration, control_interface, input_endpoint, output_endpoint;
    uint16_t input_packet_size, output_packet_size;
    uint8_t response[FM1_USB_HOST_DESCRIPTOR_SIZE];
    uint8_t input[FM1_USB_HOST_QUEUE_SIZE], output[FM1_USB_HOST_QUEUE_SIZE];
    unsigned input_head, input_length, output_head, output_length;
} FM1USBHost;

void fm1_usb_host_init(FM1USBHost *host, FM1PocUSB *usb, Chardev *backend);
void fm1_usb_host_tick(FM1USBHost *host);
/* False leaves a guest IN packet pending until output queue space is free. */
bool fm1_usb_host_tx(FM1USBHost *host, unsigned endpoint,
                     const uint8_t *data, unsigned length);
void fm1_usb_host_control_done(FM1USBHost *host, bool stalled);
void fm1_usb_host_reset(FM1USBHost *host);

#endif
