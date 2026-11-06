# -*- coding: utf-8 -*-
# CoverUp: MusicBrainz Picard plugin. GPL-2.0-or-later.
"""Pure-Python perceptual hashing (dHash) over a grayscale pixel grid.

The Qt adapter that produces the grayscale grid from a QImage lives in
``imaging.py`` so this module stays testable without Picard or Qt.
"""

from __future__ import annotations

from typing import Sequence

HASH_W = 9  # 9 columns -> 8 horizontal differences per row
HASH_H = 8  # 8 rows -> 64 bits


def _box_resize(pixels: Sequence[int], w: int, h: int, new_w: int, new_h: int) -> list[float]:
    """Area-average downscale of a row-major grayscale grid."""
    if w <= 0 or h <= 0 or len(pixels) != w * h:
        raise ValueError("pixel buffer does not match dimensions")
    out: list[float] = []
    for ny in range(new_h):
        y0 = (ny * h) // new_h
        y1 = max(y0 + 1, ((ny + 1) * h) // new_h)
        for nx in range(new_w):
            x0 = (nx * w) // new_w
            x1 = max(x0 + 1, ((nx + 1) * w) // new_w)
            total = 0
            count = 0
            for y in range(y0, y1):
                row = y * w
                for x in range(x0, x1):
                    total += pixels[row + x]
                    count += 1
            out.append(total / count)
    return out


def dhash(pixels: Sequence[int], w: int, h: int) -> int:
    """64-bit difference hash of a grayscale image (values 0-255)."""
    small = _box_resize(pixels, w, h, HASH_W, HASH_H)
    bits = 0
    for y in range(HASH_H):
        row = y * HASH_W
        for x in range(HASH_W - 1):
            bits = (bits << 1) | (1 if small[row + x] > small[row + x + 1] else 0)
    return bits


def hamming(a: int, b: int) -> int:
    return (a ^ b).bit_count()


def is_blank(pixels: Sequence[int], threshold: float = 4.0) -> bool:
    """True when the image is nearly uniform (standard deviation below ``threshold``)."""
    n = len(pixels)
    if n == 0:
        return True
    mean = sum(pixels) / n
    var = sum((p - mean) ** 2 for p in pixels) / n
    return var**0.5 < threshold


def trim_uniform_margins(pixels: Sequence[int], w: int, h: int, tolerance: int = 6) -> tuple[list[int], int, int]:
    """Crop near-uniform borders (scan margins) before hashing.

    Returns ``(pixels, new_w, new_h)``. If everything is uniform the input is returned unchanged.
    """
    if w <= 0 or h <= 0 or len(pixels) != w * h:
        raise ValueError("pixel buffer does not match dimensions")

    def row_uniform(y: int) -> bool:
        row = pixels[y * w : (y + 1) * w]
        return max(row) - min(row) <= tolerance

    def col_uniform(x: int) -> bool:
        col = [pixels[y * w + x] for y in range(h)]
        return max(col) - min(col) <= tolerance

    top, bottom, left, right = 0, h - 1, 0, w - 1
    while top < bottom and row_uniform(top):
        top += 1
    while bottom > top and row_uniform(bottom):
        bottom -= 1
    while left < right and col_uniform(left):
        left += 1
    while right > left and col_uniform(right):
        right -= 1
    if (top, bottom, left, right) == (0, h - 1, 0, w - 1):
        return list(pixels), w, h
    nw, nh = right - left + 1, bottom - top + 1
    if nw < 2 or nh < 2:
        return list(pixels), w, h
    out = []
    for y in range(top, bottom + 1):
        out.extend(pixels[y * w + left : y * w + right + 1])
    return out, nw, nh
