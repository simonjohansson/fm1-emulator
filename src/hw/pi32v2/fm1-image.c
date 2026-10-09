/* SPDX-License-Identifier: GPL-2.0-or-later */
/* Fresh decoder from format facts, not firmware/emulator implementation text.
 * Sources: kagaimiq.github.io/jielie/datafmt/{newfw,jlfs}.html and
 * kagaimiq.github.io/jielie/misc/cipher.html; MIT jl-misctools host unpacker
 * documents UFW/FWSC framing, chip-key encoding and 32-byte SFC blocks:
 * github.com/kagaimiq/jl-misctools/tree/main/firmware.
 * Auxiliary payload encryption/address fields were checked against package
 * bytes and independent CRCs: SFC addressing is the logical UFW file offset,
 * while resource destination/extent are entry metadata words at 28/32.
 * This is an application handoff, not ROM/SPL execution or an updater. */
#ifndef FM1_IMAGE_STANDALONE
#include "qemu/osdep.h"
#else
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#endif
#include "fm1-image.h"

#define BOARD_APP_BASE 0x4000u
#define BOARD_ENTRY 0x02000120u
#define RAW_OFFSET 0x4120u
#define PACKAGE_LIMIT (16u * 1024u * 1024u)
#define MAX_ENTRIES 64u

static uint16_t word(const uint8_t *p)
{
    return p[0] | (uint16_t)p[1] << 8;
}

static uint32_t dword(const uint8_t *p)
{
    return word(p) | (uint32_t)word(p + 2) << 16;
}

static uint16_t shift(uint16_t key)
{
    return (key << 1) ^ ((key & 0x8000) ? 0x1021 : 0);
}

static uint16_t crc(const uint8_t *p, size_t length)
{
    uint16_t value = 0;
    while (length--) {
        value ^= (uint16_t)*p++ << 8;
        for (unsigned bit = 0; bit < 8; bit++) {
            value = shift(value);
        }
    }
    return value;
}

static void enc(uint8_t *p, size_t length, uint16_t key)
{
    while (length--) {
        *p++ ^= key;
        key = shift(key);
    }
}

/* Each physical 32-byte SFC block restarts its shift register. */
static void sfc(uint8_t *p, size_t length, uint32_t offset, uint16_t key)
{
    for (size_t at = 0; at < length; at += 32) {
        size_t n = length - at < 32 ? length - at : 32;
        enc(p + at, n, key ^ ((offset + at) >> 2));
    }
}

static bool span(size_t offset, size_t length, size_t total)
{
    return offset <= total && length <= total - offset;
}

static bool fail(char *error, size_t length, const char *message)
{
    if (length) {
        snprintf(error, length, "%s", message);
    }
    return false;
}

static bool named(const uint8_t *field, const char *name)
{
    size_t length = strlen(name);
    return length < 16 && !memcmp(field, name, length) && !field[length];
}

static bool jlfs_header(const uint8_t *p)
{
    return word(p) == crc(p + 2, 30);
}

/* Preserve packaged NOR bytes, decoding only the application SFC area needed
 * by the board's existing unencrypted handoff. Parse every directory header
 * and regular file CRC so incorrect keys cannot produce executable garbage. */
static bool flash_decode(uint8_t *nor, size_t size, uint16_t *chipkey,
                          FM1ImageHandoff *handoff,
                          char *error, size_t error_length)
{
    uint8_t header[32];
    uint32_t appbase = UINT32_MAX;
    bool key_found = false, last = false;
    if (size < 32) {
        return fail(error, error_length, "truncated JLFS flash header");
    }
    memcpy(header, nor, 32);
    enc(header, 32, 0xffff);
    if (!jlfs_header(header)) {
        return fail(error, error_length, "invalid JLFS flash header CRC");
    }
    if (handoff) {
        memcpy(handoff->flash_header, header, sizeof(header));
    }
    for (unsigned index = 0; index < MAX_ENTRIES; index++) {
        size_t at = 32 + index * 32;
        if (!span(at, 32, size)) {
            return fail(error, error_length, "truncated JLFS top directory");
        }
        memcpy(header, nor + at, 32);
        enc(header, 32, 0xffff);
        if (!jlfs_header(header)) {
            return fail(error, error_length, "invalid JLFS top directory CRC");
        }
        uint32_t offset = dword(header + 4), length = dword(header + 8);
        if (header[12] == 0x81 && named(header + 16, "app_dir_head")) {
            if (appbase != UINT32_MAX) {
                return fail(error, error_length, "multiple application areas unsupported");
            }
            appbase = offset;
        } else if (named(header + 16, "isd_config.ini")) {
            if (key_found || !span(offset, length, size) || length < 34 ||
                crc(nor + offset, length) != word(header + 2) ||
                crc(nor + offset, 32) != word(nor + offset + 32)) {
                return fail(error, error_length, "invalid JLFS chip-key configuration");
            }
            const uint8_t *key = nor + offset;
            uint8_t threshold = 0;
            for (unsigned bit = 0; bit < 16; bit++) {
                threshold += key[bit];
            }
            threshold = threshold >= 0xe0 ? 0xaa :
                        threshold <= 0x10 ? 0x55 : threshold;
            *chipkey = 0;
            for (unsigned bit = 0; bit < 16; bit++) {
                if ((key[16 + bit] ^ key[15 - bit]) < threshold) {
                    *chipkey |= 1u << bit;
                }
            }
            key_found = true;
        } else if (!(header[12] & 0x10) &&
                   !span(offset, length, size)) {
            return fail(error, error_length, "JLFS boot file exceeds flash payload");
        }
        if (word(header + 14)) {
            last = true;
            break;
        }
    }
    if (!last || !key_found || appbase != BOARD_APP_BASE || size <= RAW_OFFSET) {
        return fail(error, error_length, "unsupported JLFS application handoff layout");
    }
    sfc(nor + appbase, size - appbase, 0, *chipkey);
    size_t area = appbase;
    last = false;
    for (unsigned index = 0; index < MAX_ENTRIES; index++) {
        if (!span(area, 32, size) || !jlfs_header(nor + area)) {
            return fail(error, error_length, "invalid SFC area header CRC or chip key");
        }
        const uint8_t *h = nor + area;
        uint32_t extent = dword(h + 8);
        if (extent < 32 || !span(area, extent, size)) {
            return fail(error, error_length, "SFC area exceeds flash payload");
        }
        if (!index && (dword(h + 4) != BOARD_ENTRY || h[12] != 0x83)) {
            return fail(error, error_length, "unsupported SFC application entry or compression");
        }
        if (h[12] == 0x83) {
            bool directory_last = false, app_found = index != 0;
            for (unsigned n = 0; n < MAX_ENTRIES; n++) {
                size_t at = area + 32 + n * 32;
                if (!span(at, 32, area + extent) || !jlfs_header(nor + at)) {
                    return fail(error, error_length, "invalid SFC file directory CRC");
                }
                const uint8_t *file = nor + at;
                if (!(file[12] & 0x10)) {
                    uint32_t offset = dword(file + 4), length = dword(file + 8);
                    if (file[12] != 0x82 ||
                        !span(offset, length, extent) ||
                        crc(nor + area + offset, length) != word(file + 2)) {
                        return fail(error, error_length, "unsupported SFC file encoding or invalid data CRC");
                    }
                    if (!index && named(file + 16, "app.bin")) {
                        if (app_found || area + offset != RAW_OFFSET || !length) {
                            return fail(error, error_length, "unsupported application storage layout");
                        }
                        app_found = true;
                    }
                }
                if (word(file + 14)) {
                    directory_last = true;
                    break;
                }
            }
            if (!directory_last || !app_found) {
                return fail(error, error_length, "unterminated SFC directory or missing application");
            }
        } else if (h[12] != 0x82) {
            return fail(error, error_length, "unsupported SFC resource encoding");
        }
        if (crc(h + 32, extent - 32) != word(h + 2)) {
            return fail(error, error_length, "invalid SFC area data CRC");
        }
        if (word(h + 14)) {
            last = true;
            break;
        }
        area += extent;
    }
    return last || fail(error, error_length, "unterminated SFC area list");
}

bool fm1_image_decode_handoff(const uint8_t *data, size_t length, bool packaged,
                              uint8_t *nor, size_t nor_length,
                              FM1ImageHandoff *handoff,
                              char *error, size_t error_length)
{
    uint8_t headers[64 + MAX_ENTRIES * 80], ufw[64];
    size_t skew = 0, count, table_size, flash_size = 0;
    uint16_t chipkey = 0;
    if (handoff) {
        memset(handoff, 0, sizeof(*handoff));
    }
    if (error_length) {
        error[0] = 0;
    }
    if (!data || !nor || nor_length < RAW_OFFSET || !length) {
        return fail(error, error_length, "empty or invalid application image");
    }
    memset(nor, 0xff, nor_length);
    if (!packaged) {
        if (!span(RAW_OFFSET, length, nor_length)) {
            return fail(error, error_length, "raw application exceeds NOR storage");
        }
        memcpy(nor + RAW_OFFSET, data, length);
        return true;
    }
    if (length < 64 || length > PACKAGE_LIMIT) {
        return fail(error, error_length, "truncated or oversized FWSC/UFW package");
    }
    memcpy(ufw, data, 64);
    enc(ufw, 64, 0xffff);
    if (word(ufw) != crc(ufw + 2, 62)) {
        /* FWSC inserts a byte after each 47 bytes of its first 940 bytes.
         * Payloads thereafter retain UFW logical offsets with +20 skew. */
        if (length < 960) {
            return fail(error, error_length, "invalid FWSC/UFW header CRC");
        }
        for (unsigned n = 0; n < 940; n++) {
            headers[n] = data[n + n / 47];
        }
        memcpy(ufw, headers, 64);
        enc(ufw, 64, 0xffff);
        skew = 20;
    }
    if (word(ufw) != crc(ufw + 2, 62)) {
        return fail(error, error_length, "invalid FWSC/UFW header CRC");
    }
    count = word(ufw + 8);
    table_size = count * 80;
    if (!count || count > MAX_ENTRIES ||
        (skew && 64 + table_size > 940) ||
        dword(ufw + 4) != length - skew ||
        !span(64, table_size, length - skew)) {
        return fail(error, error_length, "unsupported FWSC/UFW header size or entry count");
    }
    if (!skew) {
        memcpy(headers, data, 64 + table_size);
    }
    if (crc(headers + 64, table_size) != word(ufw + 2)) {
        return fail(error, error_length, "invalid UFW entry table CRC");
    }
    for (size_t n = 0; n < count; n++) {
        uint8_t *entry = headers + 64 + n * 80;
        enc(entry, 80, 0xffff);
        uint32_t offset = dword(entry + 8), size = dword(entry + 12);
        uint32_t allocated = dword(entry + 16);
        /* The final payload may end at EOF without its allocation's trailing
         * alignment padding. Require all payload bytes and allow only the
         * omitted remainder of one 32-byte allocation block. */
        size_t total = length - skew;
        bool payload_present = span(offset, size, total);
        bool tail_padding = payload_present && size == total - offset &&
                            allocated >= size && allocated - size < 32 &&
                            !(allocated & 31);
        if (offset < (skew ? 940 : 64 + table_size) || allocated < size ||
            !payload_present ||
            (!span(offset, allocated, total) && !tail_padding) || (offset & 31)) {
            return fail(error, error_length, "invalid UFW payload bounds or alignment");
        }
        for (size_t previous = 0; previous < n; previous++) {
            const uint8_t *other = headers + 64 + previous * 80;
            size_t begin = dword(other + 8), extent = dword(other + 16);
            if (size && extent && offset < begin + extent && begin < offset + allocated) {
                return fail(error, error_length, "overlapping UFW payloads");
            }
        }
        if (!word(entry)) {
            if (flash_size || size > nor_length || !size ||
                crc(data + offset + skew, size) != word(entry + 4)) {
                return fail(error, error_length, "invalid or duplicate UFW flash payload CRC/size");
            }
            memcpy(nor, data + offset + skew, size);
            flash_size = size;
        }
    }
    if (!flash_size || !flash_decode(nor, flash_size, &chipkey, handoff, error, error_length)) {
        return flash_size ? false : fail(error, error_length, "UFW flash payload missing");
    }
    for (size_t n = 0; n < count; n++) {
        const uint8_t *entry = headers + 64 + n * 80;
        uint16_t type = word(entry);
        size_t offset = dword(entry + 8), size = dword(entry + 12);
        if (type == 0x32) {
            size_t destination = dword(entry + 28), extent = dword(entry + 32);
            if (!size || destination < flash_size || size > extent ||
                !span(destination, extent, nor_length)) {
                return fail(error, error_length, "unsupported UFW NOR resource layout or compression");
            }
            for (size_t previous = 0; previous < n; previous++) {
                const uint8_t *other = headers + 64 + previous * 80;
                if (word(other) == 0x32) {
                    size_t begin = dword(other + 28), length = dword(other + 32);
                    if (destination < begin + length && begin < destination + extent) {
                        return fail(error, error_length, "overlapping UFW NOR resources");
                    }
                }
            }
            /* The SFC key covers address bits 2-17 of the package offset the
             * resource was encrypted at. FM-1_093 carries FM-1's resource
             * unchanged (encrypted at 0x93400, stored at 0xae400), so when
             * the entry offset fails, every 32-byte-aligned key base is
             * tried; only the CRC of the whole plaintext accepts one. */
            bool decoded = false;
            for (uint32_t base = 0; base < 0x40000 && !decoded; base += 32) {
                uint32_t key_base = base ? (offset & ~0x3ffffu) | (base - 32) : offset;
                if (base && key_base == offset) {
                    continue;
                }
                memcpy(nor + destination, data + offset + skew, size);
                sfc(nor + destination, size, key_base, chipkey);
                decoded = crc(nor + destination, size) == word(entry + 4);
            }
            if (!decoded) {
                return fail(error, error_length, "invalid UFW NOR resource CRC");
            }
        } else if (type != 0 && type != 2 && type != 0x34 &&
                   type != 0x64 && type != 0xfb && type != 0xa1 && type != 0xff) {
            return fail(error, error_length, "unsupported UFW entry type or compression");
        }
        /* Known update-only metadata and the updater binary do not describe
         * destination NOR bytes and are not executed by application handoff. */
    }
    if (handoff) {
        handoff->chip_key = chipkey;
        handoff->available = true;
    }
    return true;
}


bool fm1_image_decode(const uint8_t *data, size_t length, bool packaged,
                      uint8_t *nor, size_t nor_length,
                      char *error, size_t error_length)
{
    return fm1_image_decode_handoff(data, length, packaged, nor, nor_length,
                                    NULL, error, error_length);
}
