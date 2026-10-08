/* SPDX-License-Identifier: GPL-2.0-or-later */
/* Pinned cputlb.c hook: read one aligned piece of a simple lockless MMIO
 * region without the generic dispatch layers. For a non-alias region whose
 * little-endian read_with_attrs handler takes this access size unadjusted,
 * with no accepts callback, memory_region_dispatch_read amounts to one
 * handler call, a size mask and the byte swap into the big-endian piece
 * int_ld_mmio_beN assembles. Every other region returns false and keeps
 * the original dispatch. */
static bool fm1_mmio_read(MemoryRegion *mr, hwaddr offset, unsigned size,
                          MemTxAttrs attrs, uint64_t *val, MemTxResult *result)
{
    const MemoryRegionOps *ops = mr->ops;
    unsigned vmin = ops->valid.min_access_size ?: 1;
    unsigned vmax = ops->valid.max_access_size ?: 4;
    unsigned imin = ops->impl.min_access_size ?: 1;
    unsigned imax = ops->impl.max_access_size ?: 4;
    uint64_t data = 0;

    if (!mr->lockless_io || mr->alias || !ops->read_with_attrs ||
        ops->valid.accepts || ops->endianness != DEVICE_LITTLE_ENDIAN ||
        size > 4 || size < MAX(vmin, imin) || size > MIN(vmax, imax) ||
        (offset & (size - 1))) {
        return false;
    }
    *result = ops->read_with_attrs(mr->opaque, offset, &data, size, attrs);
    data &= MAKE_64BIT_MASK(0, size * 8);
    *val = size == 4 ? bswap32(data) : size == 2 ? bswap16(data) : data;
    return true;
}
