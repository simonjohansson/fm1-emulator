/* SPDX-License-Identifier: GPL-2.0-or-later */
#ifndef HW_PI32V2_FM1_LEDS_H
#define HW_PI32V2_FM1_LEDS_H

#include "qemu/osdep.h"
#include "ui/fm1-leds.h"

/* The panel LEDs share the key matrix: an LED lights while its column is
 * selected on the 595 chain (output low) and its row's LED line is high.
 * Firmware dims an LED by shortening that overlap, so the model integrates
 * the overlap of each LED over short windows of guest time and publishes the
 * fraction of the column's selected time it was on. */
typedef struct FM1PocLEDs {
    uint16_t selected;
    uint8_t lines;
    int64_t last_ns, window_ns;
    int64_t on_ns[FM1_LED_COLUMNS][FM1_LED_ROWS];
    int64_t selected_ns[FM1_LED_COLUMNS];
    uint8_t level[FM1_LED_COLUMNS][FM1_LED_ROWS];
} FM1PocLEDs;

/* latched: the 595 outputs (low selects a column); lines: bit n = the LED
 * line of matrix row n + 1 (PA9, PA10, PH6, PH9) is driven high. */
void fm1_leds_set(FM1PocLEDs *leds, uint16_t latched, uint8_t lines);

#endif
