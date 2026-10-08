/* SPDX-License-Identifier: GPL-2.0-or-later */
#ifndef HW_PI32V2_FM1_IMAGE_H
#define HW_PI32V2_FM1_IMAGE_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

/* Metadata supplied by a validated package. Other SPL boot parameters are
 * unknown and are deliberately not inferred from firmware identities. */
typedef struct FM1ImageHandoff {
    uint8_t flash_header[32];
    uint16_t chip_key;
    bool available;
} FM1ImageHandoff;

/* Private application handoff decoder. No execution policy depends on names,
 * identities or hashes. Package layouts outside the existing board handoff
 * are rejected rather than moving code or changing the CPU's entry state. */
bool fm1_image_decode(const uint8_t *data, size_t length, bool packaged,
                      uint8_t *nor, size_t nor_length,
                      char *error, size_t error_length);

/* Same decoder, optionally returning the package's flash header and chip key.
 * Raw input leaves metadata unavailable and changes no boot parameter state. */
bool fm1_image_decode_handoff(const uint8_t *data, size_t length, bool packaged,
                              uint8_t *nor, size_t nor_length,
                              FM1ImageHandoff *handoff,
                              char *error, size_t error_length);

#endif
