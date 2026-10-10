/* SPDX-License-Identifier: GPL-2.0-or-later */
#ifndef UI_FM1_LEDS_H
#define UI_FM1_LEDS_H

#include <stdint.h>

#define FM1_LED_COLUMNS 11
/* Matrix rows 1 to 4 each have an LED line; index = row - 1. */
#define FM1_LED_ROWS 4

/* Copy the published brightness of every LED, 0 to 255, the fraction of the
 * time its column was selected that it was driven. A lit LED reads about
 * 200 or more, the firmware's dim glow about 8, a breath in between. The
 * caller holds the BQL. Zero if no board has been created. */
void fm1_leds_read(uint8_t levels[FM1_LED_COLUMNS][FM1_LED_ROWS]);

#endif
