# -*- coding: utf-8 -*-
# CoverUp: MusicBrainz Picard plugin. GPL-2.0-or-later.
"""Read image dimensions from the first bytes of a file (JPEG, PNG, GIF, WebP, BMP). Pure Python.

Used to learn the real size of a remote image from a small ranged download, without decoding it.
"""

from __future__ import annotations

import struct


def probe(data: bytes) -> tuple[int, int, str] | None:
    """Return ``(width, height, format)`` or None if the header is not recognised or is truncated."""
    if not data or len(data) < 10:
        return None
    try:
        if data[:8] == b"\x89PNG\r\n\x1a\n" and len(data) >= 24 and data[12:16] == b"IHDR":
            w, h = struct.unpack(">II", data[16:24])
            return w, h, "png"
        if data[:6] in (b"GIF87a", b"GIF89a"):
            w, h = struct.unpack("<HH", data[6:10])
            return w, h, "gif"
        if data[:4] == b"RIFF" and data[8:12] == b"WEBP" and len(data) >= 30:
            return _webp(data)
        if data[:2] == b"BM" and len(data) >= 26:
            w, h = struct.unpack("<ii", data[18:26])
            return abs(w), abs(h), "bmp"
        if data[:2] == b"\xff\xd8":
            return _jpeg(data)
    except struct.error:
        return None
    return None


_SOF = {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}


def _jpeg(data: bytes) -> tuple[int, int, str] | None:
    i = 2
    n = len(data)
    while i + 4 <= n:
        if data[i] != 0xFF:
            i += 1
            continue
        marker = data[i + 1]
        if marker == 0xFF:  # fill byte
            i += 1
            continue
        if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:  # no length
            i += 2
            continue
        if i + 4 > n:
            return None
        seg_len = struct.unpack(">H", data[i + 2 : i + 4])[0]
        if marker in _SOF:
            if i + 9 > n:
                return None
            h, w = struct.unpack(">HH", data[i + 5 : i + 9])
            return w, h, "jpeg"
        if marker == 0xDA:  # start of scan before any SOF: give up
            return None
        i += 2 + seg_len
    return None


def _webp(data: bytes) -> tuple[int, int, str] | None:
    chunk = data[12:16]
    if chunk == b"VP8X":
        w = 1 + int.from_bytes(data[24:27], "little")
        h = 1 + int.from_bytes(data[27:30], "little")
        return w, h, "webp"
    if chunk == b"VP8 " and len(data) >= 30:
        w, h = struct.unpack("<HH", data[26:30])
        return w & 0x3FFF, h & 0x3FFF, "webp"
    if chunk == b"VP8L" and len(data) >= 25:
        b = data[21:25]
        w = 1 + (((b[1] & 0x3F) << 8) | b[0])
        h = 1 + (((b[3] & 0x0F) << 10) | (b[2] << 2) | ((b[1] & 0xC0) >> 6))
        return w, h, "webp"
    return None
