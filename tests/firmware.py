#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Pinned saved artifacts for the optional Felucca integration checks."""
import hashlib
import struct

HASHES = {
    "felucca.bin": "12a4b4ea47248467f566ec3b6984b08f2f89d6ef5a8e9e494cab5184e8fadb36",
    "felucca.elf": "9404c41dcd5c7516243e99cdbae1a4e731ea0c69e13dea397a8db343244b5deb",
    "felucca.dis": "0d992c3113a1765f21a10bfb233bef81ee908073f26a16ea1f9d13c6fe897618",
}
ENTRY = 0x02000120


def checked_inputs(directory):
    blobs = {name: (directory / name).read_bytes() for name in HASHES}
    for name, data in blobs.items():
        if hashlib.sha256(data).hexdigest() != HASHES[name]:
            raise SystemExit(f"selected input differs: {name}")
    elf = blobs["felucca.elf"]
    header = struct.unpack_from("<16sHHIIIIIHHHHHH", elf)
    if header[0][:6] != b"\x7fELF\x01\x01" or header[4] != ENTRY:
        raise SystemExit("unexpected ELF format or application entry")
    for i in range(header[10]):
        kind, offset, vma, lma, size, _, _, _ = struct.unpack_from(
            "<8I", elf, header[5] + i * header[9])
        if kind == 1 and size:
            if lma < ENTRY or blobs["felucca.bin"][lma - ENTRY:lma - ENTRY + size] != elf[offset:offset + size]:
                raise SystemExit("ELF load segment differs from selected raw image")
    return blobs
