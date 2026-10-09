/* SPDX-License-Identifier: GPL-2.0-or-later */
#ifndef SWEEP_TRANSLATOR_H
#define SWEEP_TRANSLATOR_H
#include "qemu/osdep.h"
typedef enum { DISAS_NEXT, DISAS_TOO_MANY, DISAS_NORETURN, DISAS_TARGET_0 } DisasJumpType;
typedef struct DisasContextBase {
    TranslationBlock *tb;
    vaddr pc_first, pc_next;
    DisasJumpType is_jmp;
    int num_insns, max_insns;
} DisasContextBase;
typedef struct TranslatorOps {
    void (*init_disas_context)(DisasContextBase *db, struct CPUState *cs);
    void (*tb_start)(DisasContextBase *db, struct CPUState *cs);
    void (*insn_start)(DisasContextBase *db, struct CPUState *cs);
    void (*translate_insn)(DisasContextBase *db, struct CPUState *cs);
    void (*tb_stop)(DisasContextBase *db, struct CPUState *cs);
} TranslatorOps;
uint16_t sweep_fetch(vaddr address);
#define translator_lduw(env, db, address) sweep_fetch(address)
static inline bool translator_io_start(DisasContextBase *db)
{
    if (db->is_jmp == DISAS_NEXT) { db->is_jmp = DISAS_TOO_MANY; }
    return true;
}
#define translator_use_goto_tb(db, dest) false
#define translator_is_same_page(db, address) true
#define translator_loop(...) ((void)0)
#endif
