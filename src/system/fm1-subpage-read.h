/* SPDX-License-Identifier: GPL-2.0-or-later */
/* Pinned physmem.c private optimization. Keep split, RAM, IOMMU and special
 * accesses on the original FlatView path. The section table belongs to the
 * same RCU-protected FlatView as the subpage; no separate cache is retained. */
static MemoryRegion *subpage_read_region(subpage_t *subpage, hwaddr addr,
                                         unsigned len, MemTxAttrs attrs,
                                         hwaddr *xlat)
{
    AddressSpaceDispatch *dispatch;
    MemoryRegionSection *section;
    MemoryRegion *mr;
    unsigned index;

    if (attrs.memory || !len || addr >= TARGET_PAGE_SIZE ||
        len > TARGET_PAGE_SIZE - addr) {
        return NULL;
    }
    index = subpage->sub_section[addr];
    if (index == PHYS_SECTION_UNASSIGNED) {
        return NULL;
    }
    for (unsigned i = 1; i < len; i++) {
        if (subpage->sub_section[addr + i] != index) {
            return NULL;
        }
    }
    dispatch = flatview_to_dispatch(subpage->fv);
    section = &dispatch->map.sections[index];
    mr = section->mr;
    if (memory_region_is_ram(mr) || memory_region_is_romd(mr) ||
        memory_region_get_iommu(mr) || mr->subpage || mr->alias) {
        return NULL;
    }
    *xlat = subpage->base + addr - section->offset_within_address_space +
            section->offset_within_region;
    /* The original accepts path sizes at the physical address, while the
     * read path sizes at the region offset. Only optimize a single access
     * in both paths; their validity callbacks and dispatch remain intact. */
    if (memory_access_size(mr, len, subpage->base + addr) != len ||
        memory_access_size(mr, len, *xlat) != len) {
        return NULL;
    }
    return mr;
}
