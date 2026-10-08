#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Native loader tests. Run with mise exec -- python -m unittest discover
-s tests -p test_images.py. Optional FM1_PACKAGE and FM1_RAW_REFERENCE select
user-supplied runtime data; no firmware payload is included in this repository.
"""
import binascii
import ctypes
import os
from pathlib import Path
import shlex
import struct
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
NOR_SIZE = 0x100000


def crc(data):
    return binascii.crc_hqx(data, 0)


def enc(data, key=0xFFFF):
    result = bytearray(data)
    for at in range(len(result)):
        result[at] ^= key & 255
        key = ((key << 1) ^ (0x1021 if key & 0x8000 else 0)) & 65535
    return bytes(result)


def sfc(data, offset, key):
    return b"".join(enc(data[at:at + 32], key ^ ((offset + at) >> 2))
                    for at in range(0, len(data), 32))


def jlfs(offset, size, flags, name, data_crc=0xFFFF, last=1):
    body = struct.pack("<HIIBBH16s", data_crc, offset, size, flags,
                       255, last, name.encode().ljust(16, b"\0"))
    return struct.pack("<H", crc(body)) + body


def flash_image(key=0x2468, compressed=False):
    # Encoded chip key with deterministic entropy: threshold becomes 0x55.
    entropy = bytes(16)
    key_data = entropy + bytes(0 if key & (1 << bit) else 0x55
                               for bit in range(16))
    config = key_data + struct.pack("<H", crc(key_data))
    app = bytes(range(64)) * 2
    directory = jlfs(0x120, len(app), 0x42 if compressed else 0x82,
                     "app.bin", crc(app))
    area_data = directory + bytes([255]) * (0x100 - len(directory)) + app
    area = jlfs(0x02000120, 32 + len(area_data), 0x83,
                "app_area_head", crc(area_data)) + area_data
    flash = bytearray([255] * 0x5000)
    body = struct.pack("<H4sIBBBB16s", 544, b"0.01", NOR_SIZE,
                       16, 16, 0, 255, b"test".ljust(16, b"\0"))
    flash[:32] = enc(struct.pack("<H", crc(body)) + body)
    flash[32:64] = enc(jlfs(0x200, len(config), 2, "isd_config.ini",
                            crc(config), last=0))
    flash[64:96] = enc(jlfs(0x4000, 0xFFFFFFFF, 0x81, "app_dir_head"))
    flash[0x200:0x200 + len(config)] = config
    flash[0x4000:0x4000 + len(area)] = sfc(area, 0, key)
    return bytes(flash), app


def package(fwsc=True, resource=True, key=0x2468, compressed=False,
            destination=0xEA000, resource_type=0x32):
    flash, app = flash_image(key, compressed)
    entries = []
    data = bytearray([255] * 0x6000)
    data[0x400:0x400 + len(flash)] = flash

    def entry(kind, offset, payload, allocation, name, metadata=bytes(44)):
        fields = struct.pack("<HHHHIII44s16s", kind, len(entries), crc(payload),
                             0, offset, len(payload), allocation, metadata,
                             name.encode().ljust(16, b"\0"))
        entries.append(enc(fields))

    entry(0, 0x400, flash, len(flash), "flash.bin")
    preset = b"preset-data" * 13
    if resource:
        offset = 0x5400
        metadata = struct.pack("<IIII", 0, 160, destination, 0x1000) + bytes(28)
        entry(resource_type, offset, preset, 160, "USR", metadata)
        data[offset:offset + len(preset)] = sfc(preset, offset, key)
    table = b"".join(entries)
    body = struct.pack("<HIHHI48s", crc(table), len(data), len(entries),
                       4, 512, b"test".ljust(48, b"\0"))
    data[:64] = enc(struct.pack("<H", crc(body)) + body)
    data[64:64 + len(table)] = table
    if fwsc:
        head = b"".join(data[at:at + 47] + b"\x5a"
                        for at in range(0, 940, 47))
        data = head + data[940:]
    return bytes(data), app, preset


class Handoff(ctypes.Structure):
    _fields_ = [("flash_header", ctypes.c_uint8 * 32),
                ("chip_key", ctypes.c_uint16),
                ("available", ctypes.c_bool)]


class ImageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix="fm1-image-test-")
        library = Path(cls.temp.name) / "image.so"
        subprocess.run([*shlex.split(os.environ.get("CC", "cc")),
                        "-std=c11", "-Wall", "-Wextra", "-Werror",
                        "-DFM1_IMAGE_STANDALONE", "-shared", "-fPIC",
                        str(ROOT / "src/hw/pi32v2/fm1-image.c"),
                        "-o", str(library)], check=True)
        cls.library = ctypes.CDLL(str(library))
        cls.decoder = cls.library.fm1_image_decode
        cls.decoder.argtypes = [ctypes.c_void_p, ctypes.c_size_t, ctypes.c_bool,
                                ctypes.c_void_p, ctypes.c_size_t,
                                ctypes.c_void_p, ctypes.c_size_t]
        cls.decoder.restype = ctypes.c_bool
        cls.handoff_decoder = cls.library.fm1_image_decode_handoff
        cls.handoff_decoder.argtypes = [ctypes.c_void_p, ctypes.c_size_t,
                                        ctypes.c_bool, ctypes.c_void_p,
                                        ctypes.c_size_t, ctypes.POINTER(Handoff),
                                        ctypes.c_void_p, ctypes.c_size_t]
        cls.handoff_decoder.restype = ctypes.c_bool

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def decode(self, data, packaged=True):
        source = ctypes.create_string_buffer(bytes(data))
        output = ctypes.create_string_buffer(NOR_SIZE)
        error = ctypes.create_string_buffer(256)
        success = self.decoder(source, len(data), packaged, output, NOR_SIZE,
                               error, len(error))
        return success, output.raw, error.value.decode()

    def decode_handoff(self, data, packaged=True):
        source = ctypes.create_string_buffer(bytes(data))
        output = ctypes.create_string_buffer(NOR_SIZE)
        error = ctypes.create_string_buffer(256)
        handoff = Handoff()
        # Stale metadata must be cleared for raw input and rejected packages.
        ctypes.memset(ctypes.byref(handoff), 0xAA, ctypes.sizeof(handoff))
        success = self.handoff_decoder(source, len(data), packaged, output,
                                       NOR_SIZE, ctypes.byref(handoff), error,
                                       len(error))
        return success, output.raw, error.value.decode(), handoff

    def reject(self, data, message):
        success, _, error = self.decode(data)
        self.assertFalse(success)
        self.assertIn(message, error)

    def test_raw_exact_storage_and_erased_bytes(self):
        app = bytes(range(256)) * 3
        success, nor, error = self.decode(app, False)
        self.assertTrue(success, error)
        self.assertEqual(nor, bytes([255]) * 0x4120 + app +
                         bytes([255]) * (NOR_SIZE - 0x4120 - len(app)))

    def test_raw_handoff_stays_unavailable_and_storage_unchanged(self):
        app = bytes(range(256)) * 3
        success, nor, error, handoff = self.decode_handoff(app, False)
        self.assertTrue(success, error)
        self.assertFalse(handoff.available)
        self.assertEqual(bytes(handoff), bytes(ctypes.sizeof(handoff)))
        self.assertEqual(nor, self.decode(app, False)[1])

    def test_package_handoff_metadata_uses_validated_header_and_key(self):
        for fwsc in (False, True):
            for key in (0, 0x2468, 0xFFFF):
                with self.subTest(fwsc=fwsc, key=key):
                    data, _, _ = package(fwsc=fwsc, key=key)
                    success, nor, error, handoff = self.decode_handoff(data)
                    self.assertTrue(success, error)
                    self.assertTrue(handoff.available)
                    self.assertEqual(handoff.chip_key, key)
                    self.assertEqual(bytes(handoff.flash_header),
                                     enc(flash_image(key)[0][:32]))
                    self.assertEqual(nor, self.decode(data)[1])

    def test_rejected_package_handoff_is_unavailable(self):
        data, _, _ = package(fwsc=False)
        corrupt = bytearray(data)
        corrupt[0x5400] ^= 1
        success, _, _, handoff = self.decode_handoff(corrupt)
        self.assertFalse(success)
        self.assertFalse(handoff.available)

    def test_raw_empty_and_oversized(self):
        for data in (b"", bytes(NOR_SIZE)):
            self.assertFalse(self.decode(data, False)[0])

    def test_ufw_and_fwsc_decrypt_app_and_presets(self):
        for fwsc in (False, True):
            for key in (0, 0x2468, 0xFFFF):
                with self.subTest(fwsc=fwsc, key=key):
                    data, app, preset = package(fwsc=fwsc, key=key)
                    success, nor, error = self.decode(data)
                    self.assertTrue(success, error)
                    self.assertEqual(nor[0x4120:0x4120 + len(app)], app)
                    self.assertEqual(nor[0xEA000:0xEA000 + len(preset)], preset)
                    self.assertEqual(nor[0xEA000 + len(preset):0xEB000],
                                     bytes([255]) * (0x1000 - len(preset)))
                    # The packaged ROM/SPL portion remains byte exact.
                    self.assertEqual(nor[:0x4000], flash_image(key)[0][:0x4000])

    def test_missing_presets_are_erased(self):
        data, _, _ = package(resource=False)
        success, nor, error = self.decode(data)
        self.assertTrue(success, error)
        self.assertEqual(nor[0x5000:], bytes([255]) * (NOR_SIZE - 0x5000))

    def test_truncated_and_corrupt_header(self):
        data, _, _ = package()
        for length in (0, 1, 63, 959, len(data) - 1):
            self.assertFalse(self.decode(data[:length])[0])
        bad = bytearray(data)
        bad[0] ^= 1
        self.reject(bad, "header CRC")

    def test_table_crc(self):
        data, _, _ = package(fwsc=False)
        bad = bytearray(data)
        bad[70] ^= 1
        self.reject(bad, "entry table CRC")

    def test_flash_and_resource_crc(self):
        data, _, _ = package(fwsc=False)
        bad = bytearray(data)
        bad[0x600] ^= 1
        self.reject(bad, "flash payload CRC")
        bad = bytearray(data)
        bad[0x5400] ^= 1
        self.reject(bad, "NOR resource CRC")

    def test_resource_destination_bounds_and_overlap(self):
        for destination in (0x4000, NOR_SIZE, 0xFFFFFFF0):
            data, _, _ = package(destination=destination)
            self.reject(data, "NOR resource layout")

    def test_compression_and_unknown_type_are_explicitly_unsupported(self):
        data, _, _ = package(compressed=True)
        self.reject(data, "file encoding")
        data, _, _ = package(resource_type=0x33)
        self.reject(data, "entry type or compression")

    @unittest.skipUnless(os.environ.get("FM1_PACKAGE"),
                         "set FM1_PACKAGE to check a supplied real package")
    def test_optional_real_package(self):
        success, nor, error, handoff = self.decode_handoff(
            Path(os.environ["FM1_PACKAGE"]).read_bytes())
        self.assertTrue(success, error)
        self.assertTrue(handoff.available)
        self.assertEqual(bytes(handoff.flash_header), enc(nor[:32]))
        # Independently recover the encoded key from the preserved top-level
        # configuration, rather than pinning a particular firmware identity.
        for at in range(32, 32 + 64 * 32, 32):
            header = enc(nor[at:at + 32])
            name = header[16:].split(b"\0", 1)[0]
            if name == b"isd_config.ini":
                offset = struct.unpack_from("<I", header, 4)[0]
                key_data = nor[offset:offset + 32]
                threshold = sum(key_data[:16]) & 255
                threshold = 0xAA if threshold >= 0xE0 else (
                    0x55 if threshold <= 0x10 else threshold)
                key = sum(1 << bit for bit in range(16)
                          if (key_data[16 + bit] ^ key_data[15 - bit]) < threshold)
                self.assertEqual(handoff.chip_key, key)
                break
        else:
            self.fail("real package configuration not found")
        self.assertNotEqual(nor[0x4120:0x4160], bytes([255]) * 64)
        if os.environ.get("FM1_RAW_REFERENCE"):
            raw = Path(os.environ["FM1_RAW_REFERENCE"]).read_bytes()
            self.assertEqual(nor[0x4120:0x4120 + len(raw)], raw)


if __name__ == "__main__":
    unittest.main()
