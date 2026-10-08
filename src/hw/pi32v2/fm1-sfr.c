/* SPDX-License-Identifier: GPL-2.0-or-later */
/* Peripheral pages. Several small register blocks share each 4 KiB page,
 * which QEMU would dispatch through a subpage, a flat-view walk and a second
 * region dispatch on every access. Each page here is one leaf region that
 * forwards an access to the block owning its offset; the block's own
 * validity checks, widths and handlers apply unchanged. Offsets no block
 * owns fail as unassigned memory does.
 *
 * Pages are lockless for loads. Device state is changed only on the vCPU
 * thread (MMIO, icount's virtual timers and queued CPU work), apart from
 * blocks mapped with fm1_sfr_map_locked, whose reads keep the BQL. Writes
 * always hold it: they reconfigure timers, IRQs, audio and the memory map. */
#include "qemu/osdep.h"
#include "qemu/main-loop.h"
#include "system/address-spaces.h"
#include "fm1-sfr.h"

#define SFR_PAGE 0x1000u
#define SFR_PAGES 16
#define SFR_BLOCKS 12

typedef struct FM1SfrPage {
    MemoryRegion mmio;
    hwaddr base;
    unsigned count;
    struct {
        hwaddr offset, size;
        MemoryRegion *mr;
        bool locked;
    } block[SFR_BLOCKS];
    uint8_t owner[SFR_PAGE / 4];    /* block index + 1 per word; 0 unowned */
} FM1SfrPage;

static FM1SfrPage pages[SFR_PAGES];
static unsigned page_count;

static MemoryRegion *block_at(FM1SfrPage *p, hwaddr addr, unsigned size,
                              hwaddr *offset, bool *locked)
{
    unsigned b = p->owner[addr / 4];
    if (!b--) {
        return NULL;
    }
    if (addr + size > p->block[b].offset + p->block[b].size) {
        return NULL;
    }
    *offset = addr - p->block[b].offset;
    *locked = p->block[b].locked;
    return p->block[b].mr;
}

static MemTxResult sfr_read(void *opaque, hwaddr addr, uint64_t *data,
                            unsigned size, MemTxAttrs attrs)
{
    hwaddr offset;
    bool locked;
    MemoryRegion *mr = block_at(opaque, addr, size, &offset, &locked);
    if (!mr) {
        return MEMTX_DECODE_ERROR;
    }
    if (locked && !bql_locked()) {
        BQL_LOCK_GUARD();
        return memory_region_dispatch_read(mr, offset, data,
                                           size_memop(size) | MO_LE, attrs);
    }
    return memory_region_dispatch_read(mr, offset, data,
                                       size_memop(size) | MO_LE, attrs);
}

static MemTxResult sfr_write(void *opaque, hwaddr addr, uint64_t data,
                             unsigned size, MemTxAttrs attrs)
{
    hwaddr offset;
    bool locked;
    MemoryRegion *mr = block_at(opaque, addr, size, &offset, &locked);
    if (!mr) {
        return MEMTX_DECODE_ERROR;
    }
    if (!bql_locked()) {
        BQL_LOCK_GUARD();
        return memory_region_dispatch_write(mr, offset, data,
                                            size_memop(size) | MO_LE, attrs);
    }
    return memory_region_dispatch_write(mr, offset, data,
                                        size_memop(size) | MO_LE, attrs);
}

/* Width and alignment are the owning block's decision. */
static const MemoryRegionOps sfr_ops = {
    .read_with_attrs = sfr_read, .write_with_attrs = sfr_write,
    .endianness = DEVICE_LITTLE_ENDIAN,
    .valid = {.min_access_size = 1, .max_access_size = 4, .unaligned = true},
    .impl = {.min_access_size = 1, .max_access_size = 4, .unaligned = true},
};

static FM1SfrPage *page_for(hwaddr base)
{
    for (unsigned i = 0; i < page_count; i++) {
        if (pages[i].base == base) {
            return &pages[i];
        }
    }
    g_assert(page_count < SFR_PAGES);
    FM1SfrPage *p = &pages[page_count++];
    g_autofree char *name = g_strdup_printf("fm1.sfr-page@0x%08" HWADDR_PRIx, base);
    p->base = base;
    memory_region_init_io(&p->mmio, NULL, &sfr_ops, p, name, SFR_PAGE);
    memory_region_enable_lockless_io(&p->mmio);
    memory_region_add_subregion(get_system_memory(), base, &p->mmio);
    return p;
}

static void map_block(hwaddr address, MemoryRegion *mr, bool locked)
{
    hwaddr base = address & ~(hwaddr)(SFR_PAGE - 1), offset = address - base;
    uint64_t size = memory_region_size(mr);
    FM1SfrPage *p = page_for(base);
    g_assert(!(offset & 3) && size && !(size & 3) && offset + size <= SFR_PAGE);
    g_assert(p->count < SFR_BLOCKS);
    for (hwaddr w = offset / 4; w < (offset + size) / 4; w++) {
        g_assert(!p->owner[w]);
        p->owner[w] = p->count + 1;
    }
    p->block[p->count].offset = offset;
    p->block[p->count].size = size;
    p->block[p->count].mr = mr;
    p->block[p->count].locked = locked;
    p->count++;
}

void fm1_sfr_map(hwaddr address, MemoryRegion *mr)
{
    map_block(address, mr, false);
}

void fm1_sfr_map_locked(hwaddr address, MemoryRegion *mr)
{
    map_block(address, mr, true);
}
