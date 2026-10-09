/* SPDX-License-Identifier: GPL-2.0-or-later */
/* Decode sweep: TCG types and no-op code generation. Conditional branches
 * and labels are tracked so a rejection emitted behind a runtime guard (a
 * bit index >= 32, an unqualified REP with a nonzero count) can be told
 * apart from an unconditional one. */
#ifndef SWEEP_TCG_OP_H
#define SWEEP_TCG_OP_H
#include "qemu/osdep.h"
typedef struct SweepTemp *TCGv_i32, *TCGv_i64, *TCGv_ptr;
typedef struct SweepLabel TCGLabel;
typedef enum { TCG_COND_EQ, TCG_COND_NE, TCG_COND_LT, TCG_COND_GE, TCG_COND_LE, TCG_COND_GT,
               TCG_COND_LTU, TCG_COND_GEU, TCG_COND_LEU, TCG_COND_GTU } TCGCond;
typedef unsigned MemOp;
enum { MO_8 = 0, MO_16 = 1, MO_32 = 2, MO_SIGN = 4, MO_ALIGN = 0x100,
       MO_UB = MO_8, MO_SB = MO_8 | MO_SIGN, MO_LEUW = MO_16, MO_LESW = MO_16 | MO_SIGN,
       MO_LEUL = MO_32 };
enum { TCG_MO_ALL = 0xf, TCG_BAR_SC = 0x30, TCG_TYPE_VA = 1 };
static inline unsigned memop_size(MemOp op) { return 1u << (op & 3); }
extern TCGv_ptr tcg_env;
extern int sweep_guards;                 /* conditional branches not yet joined */
TCGv_i32 sweep_temp(void);
TCGLabel *sweep_label(void);
#define tcg_temp_new_i32() sweep_temp()
#define tcg_temp_new_i64() sweep_temp()
extern uint32_t sweep_value;               /* the last constant made */
TCGv_i32 sweep_const(uint32_t value);
#define tcg_constant_i32(v) sweep_const(v)
#define tcg_global_mem_new_i32(...) sweep_temp()
#define tcg_global_mem_new_i64(...) sweep_temp()
#define gen_new_label() sweep_label()
#define gen_set_label(l) ((void)(l), sweep_guards = sweep_guards ? sweep_guards - 1 : 0)
#define tcg_gen_brcond_i32(...) (sweep_guards++)
#define tcg_gen_brcondi_i32(...) (sweep_guards++)
#define tcg_gen_brcondi_i64(...) (sweep_guards++)
#define tcg_gen_br(...) ((void)0)
#define tcg_gen_mov_i32(...) ((void)0)
#define tcg_gen_movi_i32(...) ((void)0)
#define tcg_gen_movi_i64(...) ((void)0)
#define tcg_gen_add_i32(...) ((void)0)
#define tcg_gen_addi_i32(...) ((void)0)
#define tcg_gen_addi_i64(...) ((void)0)
#define tcg_gen_sub_i32(...) ((void)0)
#define tcg_gen_subi_i32(...) ((void)0)
#define tcg_gen_and_i32(...) ((void)0)
#define tcg_gen_andi_i32(...) ((void)0)
#define tcg_gen_andc_i32(...) ((void)0)
#define tcg_gen_or_i32(...) ((void)0)
#define tcg_gen_ori_i32(...) ((void)0)
#define tcg_gen_xor_i32(...) ((void)0)
#define tcg_gen_xori_i32(...) ((void)0)
#define tcg_gen_eqv_i32(...) ((void)0)
#define tcg_gen_not_i32(...) ((void)0)
#define tcg_gen_shl_i32(...) ((void)0)
#define tcg_gen_shli_i32(...) ((void)0)
#define tcg_gen_shr_i32(...) ((void)0)
#define tcg_gen_shri_i32(...) ((void)0)
#define tcg_gen_sar_i32(...) ((void)0)
#define tcg_gen_sari_i32(...) ((void)0)
#define tcg_gen_shl_i64(...) ((void)0)
#define tcg_gen_shli_i64(...) ((void)0)
#define tcg_gen_shri_i64(...) ((void)0)
#define tcg_gen_sari_i64(...) ((void)0)
#define tcg_gen_mul_i32(...) ((void)0)
#define tcg_gen_muli_i32(...) ((void)0)
#define tcg_gen_mulu2_i32(...) ((void)0)
#define tcg_gen_muls2_i32(...) ((void)0)
#define tcg_gen_smin_i32(...) ((void)0)
#define tcg_gen_smax_i32(...) ((void)0)
#define tcg_gen_umin_i32(...) ((void)0)
#define tcg_gen_umax_i32(...) ((void)0)
#define tcg_gen_abs_i32(...) ((void)0)
#define tcg_gen_ext8s_i32(...) ((void)0)
#define tcg_gen_ext16s_i32(...) ((void)0)
#define tcg_gen_bswap32_i32(...) ((void)0)
#define tcg_gen_setcond_i32(...) ((void)0)
#define tcg_gen_setcondi_i32(...) ((void)0)
#define tcg_gen_concat_i32_i64(...) ((void)0)
#define tcg_gen_extu_i32_i64(...) ((void)0)
#define tcg_gen_extr_i64_i32(...) ((void)0)
#define tcg_gen_ld_i32(...) ((void)0)
#define tcg_gen_ld_i64(...) ((void)0)
#define tcg_gen_st_i32(...) ((void)0)
#define tcg_gen_st_i64(...) ((void)0)
#define tcg_gen_qemu_ld_i32(...) ((void)0)
#define tcg_gen_qemu_st_i32(...) ((void)0)
#define tcg_gen_mb(...) ((void)0)
#define tcg_gen_insn_start(...) ((void)0)
#define tcg_gen_goto_tb(...) ((void)0)
#define tcg_gen_exit_tb(...) ((void)0)
#define tcg_gen_lookup_and_goto_ptr() ((void)0)
#endif
