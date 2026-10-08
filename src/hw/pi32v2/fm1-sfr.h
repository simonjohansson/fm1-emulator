/* SPDX-License-Identifier: GPL-2.0-or-later */
#ifndef HW_PI32V2_FM1_SFR_H
#define HW_PI32V2_FM1_SFR_H

#include "system/memory.h"

/* Map a device register block into the system bus through its 4 KiB
 * peripheral page. The block keeps its own MemoryRegionOps; it is not
 * itself a subregion of system memory. Offset and size are word multiples
 * inside one page and do not overlap another block. */
void fm1_sfr_map(hwaddr address, MemoryRegion *mr);
/* As fm1_sfr_map, for a block whose reads must hold the BQL: its state is
 * also changed outside the vCPU thread. */
void fm1_sfr_map_locked(hwaddr address, MemoryRegion *mr);

#endif
