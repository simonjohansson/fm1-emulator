/* SPDX-License-Identifier: GPL-2.0-or-later */
#ifndef PI32V2_CPU_PARAM_H
#define PI32V2_CPU_PARAM_H
#define TARGET_PAGE_BITS 12
#define TARGET_PHYS_ADDR_SPACE_BITS 32
#define TARGET_VIRT_ADDR_SPACE_BITS 32
/* Single guest CPU only; no TARGET_SUPPORTS_MTTCG. */
#define TCG_GUEST_DEFAULT_MO 0
#endif
