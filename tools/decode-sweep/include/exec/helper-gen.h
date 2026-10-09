/* SPDX-License-Identifier: GPL-2.0-or-later */
/* Decode sweep: helper calls are no-ops, except the decoder's rejection. */
#ifndef SWEEP_HELPER_GEN_H
#define SWEEP_HELPER_GEN_H
void sweep_illegal(uint32_t op);
#define gen_helper_pi32v2_illegal(env, op) sweep_illegal(((void)(op), sweep_value))
#define gen_helper_pi32v2_finish(...) ((void)0)
#define gen_helper_pi32v2_frame(...) ((void)0)
#define gen_helper_pi32v2_loop(...) ((void)0)
#define gen_helper_pi32v2_budget(...) ((void)0)
#define gen_helper_pi32v2_guard_fault(...) ((void)0)
#define gen_helper_pi32v2_if(...) ((void)0)
#define gen_helper_pi32v2_repeat(...) ((void)0)
#define gen_helper_pi32v2_advance(...) ((void)0)
#define gen_helper_pi32v2_call_return(...) ((void)0)
#define gen_helper_pi32v2_return_end(...) ((void)0)
#define gen_helper_pi32v2_unsigned_le_end(...) ((void)0)
#define gen_helper_pi32v2_signed_branch_end(...) ((void)0)
#define gen_helper_pi32v2_long_register_ne_end(...) ((void)0)
#define gen_helper_pi32v2_flush(...) ((void)0)
#define gen_helper_pi32v2_alu(...) ((void)0)
#define gen_helper_pi32v2_div(...) ((void)0)
#define gen_helper_pi32v2_divs(...) ((void)0)
#define gen_helper_pi32v2_rti(...) ((void)0)
#define gen_helper_pi32v2_lock(...) ((void)0)
#define gen_helper_pi32v2_fop(...) ((void)0)
#define gen_helper_pi32v2_fmac(...) ((void)0)
#define gen_helper_pi32v2_funary(...) ((void)0)
#define gen_helper_pi32v2_fcmp(...) ((void)0)
#endif
