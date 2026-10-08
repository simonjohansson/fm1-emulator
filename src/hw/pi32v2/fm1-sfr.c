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
        uint8_t direct;             /* access sizes the block's handlers take as is */
    } block[SFR_BLOCKS];
    uint8_t owner[SFR_PAGE / 4];    /* block index + 1 per word; 0 unowned */
} FM1SfrPage;

static FM1SfrPage pages[SFR_PAGES];
static unsigned page_count;

static MemoryRegion *block_at(FM1SfrPage *p, hwaddr addr, unsigned size,
                              hwaddr *offset, bool *locked, bool *direct)
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
    *direct = (p->block[b].direct & size) && !(*offset & (size - 1));
    return p->block[b].mr;
}

/* An aligned access of a size the block both accepts and implements calls
 * its handler as QEMU's accessor would; anything else takes the block's
 * full dispatch, including its validity failure. */
static MemTxResult block_read(MemoryRegion *mr, hwaddr offset, uint64_t *data,
                              unsigned size, bool direct, MemTxAttrs attrs)
{
    if (direct) {
        *data = mr->ops->read(mr->opaque, offset, size) & MAKE_64BIT_MASK(0, size * 8);
        return MEMTX_OK;
    }
    return memory_region_dispatch_read(mr, offset, data, size_memop(size) | MO_LE, attrs);
}

static MemTxResult block_write(MemoryRegion *mr, hwaddr offset, uint64_t data,
                               unsigned size, bool direct, MemTxAttrs attrs)
{
    if (direct) {
        mr->ops->write(mr->opaque, offset, data & MAKE_64BIT_MASK(0, size * 8), size);
        return MEMTX_OK;
    }
    return memory_region_dispatch_write(mr, offset, data, size_memop(size) | MO_LE, attrs);
}

static MemTxResult sfr_read(void *opaque, hwaddr addr, uint64_t *data,
                            unsigned size, MemTxAttrs attrs)
{
    hwaddr offset;
    bool locked, direct;
    MemoryRegion *mr = block_at(opaque, addr, size, &offset, &locked, &direct);
    if (!mr) {
        return MEMTX_DECODE_ERROR;
    }
    if (locked && !bql_locked()) {
        BQL_LOCK_GUARD();
        return block_read(mr, offset, data, size, direct, attrs);
    }
    return block_read(mr, offset, data, size, direct, attrs);
}

static MemTxResult sfr_write(void *opaque, hwaddr addr, uint64_t data,
                             unsigned size, MemTxAttrs attrs)
{
    hwaddr offset;
    bool locked, direct;
    MemoryRegion *mr = block_at(opaque, addr, size, &offset, &locked, &direct);
    if (!mr) {
        return MEMTX_DECODE_ERROR;
    }
    if (!bql_locked()) {
        BQL_LOCK_GUARD();
        return block_write(mr, offset, data, size, direct, attrs);
    }
    return block_write(mr, offset, data, size, direct, attrs);
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

/* Sizes (1, 2, 4 as a mask) dispatched to plain little-endian handlers
 * without adjustment: inside both the valid and the implemented range. */
static uint8_t direct_sizes(const MemoryRegionOps *ops)
{
    unsigned vmin = ops->valid.min_access_size ?: 1, vmax = ops->valid.max_access_size ?: 4;
    unsigned imin = ops->impl.min_access_size ?: 1, imax = ops->impl.max_access_size ?: 4;
    uint8_t sizes = 0;
    if (!ops->read || !ops->write || ops->valid.accepts ||
        ops->endianness != DEVICE_LITTLE_ENDIAN) {
        return 0;
    }
    for (unsigned size = 1; size <= 4; size <<= 1) {
        if (size >= MAX(vmin, imin) && size <= MIN(vmax, imax)) {
            sizes |= size;
        }
    }
    return sizes;
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
    p->block[p->count].direct = direct_sizes(mr->ops);
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
