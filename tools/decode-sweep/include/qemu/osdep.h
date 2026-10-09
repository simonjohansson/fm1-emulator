/* SPDX-License-Identifier: GPL-2.0-or-later */
/* Decode sweep: minimal stand-ins for the QEMU headers translate.c uses.
 * Code generation is a no-op; only the decoder's decisions are observed. */
#ifndef SWEEP_OSDEP_H
#define SWEEP_OSDEP_H
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
typedef uint64_t vaddr;
typedef uint64_t hwaddr;
#define G_NORETURN __attribute__((noreturn))
#define MIN(a, b) ((a) < (b) ? (a) : (b))
#define MAX(a, b) ((a) > (b) ? (a) : (b))
#define container_of(ptr, type, member) ((type *)((char *)(ptr) - offsetof(type, member)))
#endif
