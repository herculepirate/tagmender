# -*- coding: utf-8 -*-
"""Drop-in replacement for CoverUp's imaging.py using Pillow instead of Qt, so CoverUp's
pipeline, providers and file writer can run in a batch outside Picard.

Same functions, same behaviour: a 64x64 grayscale grid, CoverUp's own margin trimming,
dHash and blank test (imported from coverup.matching.phash), the same resize/convert rules
in prepare(), and JPEG input returned untouched by to_jpeg().
"""
from __future__ import annotations

import io
from dataclasses import dataclass

from PIL import Image, ImageOps

GRID = 64


@dataclass
class Fingerprint:
    phash: int | None
    blank: bool
    width: int
    height: int
    format: str


def _phash_mod():
    from coverup.matching import phash   # CoverUp's own pure-Python hashing
    return phash


def _probe(data):
    from coverup.imageinfo import probe
    return probe(data)


def load(data: bytes) -> Image.Image:
    img = Image.open(io.BytesIO(data))
    img.load()
    try:
        img = ImageOps.exif_transpose(img)   # Qt applies EXIF orientation when loading
    except Exception:
        pass
    return img


def _format_of(data: bytes) -> str:
    if data[:3] == b"\xff\xd8\xff":
        return "jpeg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    if data[:3] == b"GIF":
        return "gif"
    if data[:4] in (b"II*\x00", b"MM\x00*"):
        return "tiff"
    return "unknown"


def _has_alpha(img: Image.Image) -> bool:
    return img.mode in ("RGBA", "LA", "PA") or (img.mode == "P" and "transparency" in img.info)


def gray_grid(img: Image.Image, size: int = GRID) -> list[int]:
    small = img.convert("L").resize((size, size), Image.BILINEAR)
    return list(small.tobytes())


def fingerprint(data: bytes) -> Fingerprint | None:
    """Perceptual hash + blank flag + decoded size. None if the data is not a decodable image."""
    if not data:
        return None
    try:
        img = load(data)
    except Exception:
        return None
    ph = _phash_mod()
    grid = gray_grid(img)
    trimmed, tw, th = ph.trim_uniform_margins(grid, GRID, GRID)
    blank = ph.is_blank(grid)
    return Fingerprint(ph.dhash(trimmed, tw, th), blank, img.width, img.height, _format_of(data))


def size_of(data: bytes) -> tuple[int, int] | None:
    try:
        img = load(data)
    except Exception:
        return None
    return img.width, img.height


def dimensions(data: bytes) -> tuple[int, int] | None:
    info = _probe(data[:65536])
    return (info[0], info[1]) if info else size_of(data)


def encode(img: Image.Image, fmt: str = "jpeg", quality: int = 92) -> bytes:
    fmt = fmt.lower()
    buf = io.BytesIO()
    if fmt == "jpeg":
        if _has_alpha(img):                      # flatten onto white, like CoverUp
            img = img.convert("RGBA")
            flat = Image.new("RGB", img.size, (255, 255, 255))
            flat.paste(img, mask=img.split()[-1])
            img = flat
        elif img.mode != "RGB":
            img = img.convert("RGB")
        img.save(buf, "JPEG", quality=max(1, min(100, int(quality))), optimize=True)
    else:
        img.save(buf, "PNG")
    return buf.getvalue()


def to_jpeg(data: bytes, quality: int = 95) -> bytes:
    if _format_of(data) == "jpeg":
        return data
    return encode(load(data), "jpeg", quality)


def rotate(data: bytes, degrees: int, quality: int = 95) -> bytes:
    img = load(data)
    turns = (int(degrees) // 90) % 4
    if turns:
        img = img.rotate(-90 * turns, expand=True)   # positive = clockwise, as in CoverUp
    return encode(img, "jpeg", quality)


def crop(data: bytes, x: int, y: int, w: int, h: int, quality: int = 95) -> bytes:
    img = load(data)
    x, y = max(0, min(int(x), img.width - 1)), max(0, min(int(y), img.height - 1))
    w, h = max(1, min(int(w), img.width - x)), max(1, min(int(h), img.height - y))
    return encode(img.crop((x, y, x + w, y + h)), "jpeg", quality)


def is_valid_image(data: bytes) -> bool:
    try:
        return bool(data) and load(data) is not None
    except Exception:
        return False


def prepare(data: bytes, max_side: int = 0, fmt: str = "jpeg", quality: int = 92, min_side: int = 0,
            upscale: bool = False) -> tuple[bytes, int, int, str]:
    """Resize (never up unless ``upscale``) and/or convert. Returns (bytes, width, height, format).
    Mirrors CoverUp's imaging.prepare()."""
    src_fmt = _format_of(data)
    img = load(data)
    w, h = img.width, img.height
    target = img
    longest = max(w, h)
    if max_side and longest > max_side:
        target = img.copy()
        target.thumbnail((max_side, max_side), Image.LANCZOS)
    elif upscale and min_side and longest < min_side:
        scale = min_side / longest
        target = img.resize((round(w * scale), round(h * scale)), Image.LANCZOS)
    out_fmt = fmt
    if fmt == "original":
        out_fmt = src_fmt if src_fmt in ("jpeg", "png") else ("png" if _has_alpha(img) else "jpeg")
    if target is img and out_fmt == src_fmt:
        return data, w, h, src_fmt
    return encode(target, out_fmt, quality), target.width, target.height, out_fmt


def extension_for(fmt: str) -> str:
    return {"jpeg": ".jpg", "png": ".png", "webp": ".webp", "gif": ".gif"}.get(fmt, ".jpg")


def mime_for(fmt: str) -> str:
    return {"jpeg": "image/jpeg", "png": "image/png", "webp": "image/webp", "gif": "image/gif"}.get(fmt, "image/jpeg")
