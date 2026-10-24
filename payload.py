"""payload.py - a fingerprint of a file's AUDIO DATA, unchanged by any tag edit.

Writing tags changes a file's bytes, so a whole-file hash cannot show that the audio was
left alone. digest(path) reads each container's own structure and hashes only the audio:

  FLAC        every byte after the metadata blocks (ID3v1/APEv2 trailers excluded)
  MP3, AAC    the frames between the ID3v2 header and any ID3v1/APEv2/Lyrics3 trailer
  MP4/M4A     the contents of every mdat box (AAC and ALAC)
  Ogg         every packet after the codec's header packets (Vorbis 3, Opus 2), with
              their granule positions - pages may be renumbered by a tag edit, packets not
  WAV, AIFF   the 'data' / 'SSND' chunk
  WavPack, APE, Musepack, TTA, OptimFROG
              everything before the APEv2 / ID3v1 trailer
  DSF         the 'data' chunk

Other formats are hashed from the packets ffmpeg demuxes (-c copy, so nothing is decoded
or changed), when ffmpeg is installed. Otherwise digest() returns None: the format is not
verified, and the executor refuses to write tags to it unless the profile allows
unverified writes.

Decoding for fingerprints is separate (fpcalc) and read-only.
"""
from __future__ import annotations

import hashlib
import shutil
import struct
import subprocess
from pathlib import Path

CHUNK = 1 << 20


def _hash_range(fh, start, end, h):
    fh.seek(start)
    left = end - start
    while left > 0:
        b = fh.read(min(CHUNK, left))
        if not b:
            break
        h.update(b)
        left -= len(b)


def _trailer_start(fh, size):
    """Where the APEv2 / Lyrics3v2 / ID3v1 trailers at the end of a file begin."""
    end = size
    changed = True
    while changed and end > 0:
        changed = False
        if end >= 128:
            fh.seek(end - 128)
            if fh.read(3) == b"TAG":
                end -= 128
                changed = True
                continue
        if end >= 32:
            fh.seek(end - 32)
            foot = fh.read(32)
            if foot[:8] == b"APETAGEX":
                tag_size = struct.unpack("<I", foot[12:16])[0]
                flags = struct.unpack("<I", foot[20:24])[0]
                total = tag_size + (32 if flags & 0x80000000 else 0)
                if 0 < total <= end:
                    end -= total
                    changed = True
                    continue
        if end >= 15:
            fh.seek(end - 15)
            tail = fh.read(15)
            if tail[6:] == b"LYRICS200":
                n = int(tail[:6]) if tail[:6].isdigit() else 0
                if 0 < n + 15 <= end:
                    end -= n + 15
                    changed = True
    return end


def _id3v2_end(fh):
    fh.seek(0)
    pos = 0
    while True:                                 # some files carry more than one ID3v2 tag
        fh.seek(pos)
        h = fh.read(10)
        if len(h) < 10 or h[:3] != b"ID3":
            return pos
        size = (h[6] << 21) | (h[7] << 14) | (h[8] << 7) | h[9]
        pos += 10 + size + (10 if h[5] & 0x10 else 0)


def _flac(fh, size, h):
    start = _id3v2_end(fh)
    fh.seek(start)
    if fh.read(4) != b"fLaC":
        return False
    pos = start + 4
    while True:
        fh.seek(pos)
        hdr = fh.read(4)
        if len(hdr) < 4:
            return False
        last, length = hdr[0] & 0x80, int.from_bytes(hdr[1:4], "big")
        pos += 4 + length
        if last:
            break
    _hash_range(fh, pos, _trailer_start(fh, size), h)
    return True


def _mpeg(fh, size, h):
    _hash_range(fh, _id3v2_end(fh), _trailer_start(fh, size), h)
    return True


def _atoms(fh, start, end):
    pos = start
    while pos + 8 <= end:
        fh.seek(pos)
        hdr = fh.read(8)
        n, kind = struct.unpack(">I4s", hdr)
        hlen = 8
        if n == 1:
            n = struct.unpack(">Q", fh.read(8))[0]
            hlen = 16
        elif n == 0:
            n = end - pos
        if n < hlen:
            return
        yield kind, pos + hlen, pos + n
        pos += n


def _mp4(fh, size, h):
    found = False
    for kind, a, b in _atoms(fh, 0, size):
        if kind == b"mdat":
            _hash_range(fh, a, b, h)
            found = True
    return found


def _ogg_packets(fh):
    """(packet bytes, granule of the page the packet ends on) for every packet of the
    first logical stream."""
    serial, buf = None, b""
    while True:
        hdr = fh.read(27)
        if len(hdr) < 27:
            return
        if hdr[:4] != b"OggS":
            raise ValueError("not an Ogg page")
        granule = struct.unpack("<q", hdr[6:14])[0]
        sn = struct.unpack("<I", hdr[14:18])[0]
        nseg = hdr[26]
        lacing = fh.read(nseg)
        body = fh.read(sum(lacing))
        if serial is None:
            serial = sn
        if sn != serial:
            continue
        pos = 0
        for lv in lacing:
            buf += body[pos:pos + lv]
            pos += lv
            if lv < 255:
                yield buf, granule
                buf = b""


def _ogg(fh, size, h):
    fh.seek(0)
    packets = _ogg_packets(fh)
    first = next(packets, None)
    if first is None:
        return False
    p0 = first[0]
    if p0[:7] == b"\x01vorbis":
        headers = 3
    elif p0[:8] == b"OpusHead":
        headers = 2
    elif p0[:5] == b"\x7fFLAC":
        headers = 1 + struct.unpack(">H", p0[7:9])[0]
    else:
        return False
    n = 1
    for pkt, granule in packets:
        n += 1
        if n <= headers:
            continue
        h.update(struct.pack("<q", granule))
        h.update(pkt)
    return True


def _riff_chunk(fh, size, h, big_endian, form_ids, want):
    fh.seek(0)
    head = fh.read(12)
    if head[8:12] not in form_ids:
        return False
    pos, fmt = 12, ">I" if big_endian else "<I"
    while pos + 8 <= size:
        fh.seek(pos)
        cid = fh.read(4)
        n = struct.unpack(fmt, fh.read(4))[0]
        if cid == want:
            _hash_range(fh, pos + 8, min(size, pos + 8 + n), h)
            return True
        pos += 8 + n + (n & 1)
    return False


def _dsf(fh, size, h):
    fh.seek(0)
    if fh.read(4) != b"DSD ":
        return False
    pos = struct.unpack("<Q", fh.read(8))[0]          # size of the DSD chunk
    while pos + 12 <= size:
        fh.seek(pos)
        cid = fh.read(4)
        n = struct.unpack("<Q", fh.read(8))[0]
        if cid == b"data":
            _hash_range(fh, pos + 12, min(size, pos + n), h)
            return True
        if n < 12:
            return False
        pos += n
    return False


def _apev2_family(fh, size, h):
    _hash_range(fh, _id3v2_end(fh), _trailer_start(fh, size), h)
    return True


def _sniff(fh):
    fh.seek(0)
    head = fh.read(64)
    if head[:3] == b"ID3":
        fh.seek(_id3v2_end(fh))
        head2 = fh.read(4)
        if head2 == b"fLaC":
            return "flac"
        return "mpeg"
    if head[:4] == b"fLaC":
        return "flac"
    if head[4:8] == b"ftyp":
        return "mp4"
    if head[:4] == b"OggS":
        return "ogg"
    if head[:4] == b"RIFF" and head[8:12] == b"WAVE":
        return "wav"
    if head[:4] == b"FORM" and head[8:12] in (b"AIFF", b"AIFC"):
        return "aiff"
    if head[:4] in (b"wvpk", b"MAC ", b"MPCK", b"TTA1", b"OFR ") or head[:3] == b"MP+":
        return "apev2"
    if head[:4] == b"DSD ":
        return "dsf"
    if head[:2] in (b"\xff\xfb", b"\xff\xfa", b"\xff\xf3", b"\xff\xf2", b"\xff\xe3", b"\xff\xf1", b"\xff\xf9"):
        return "mpeg"
    return None


_NATIVE = {"flac": _flac, "mpeg": _mpeg, "mp4": _mp4, "ogg": _ogg, "dsf": _dsf, "apev2": _apev2_family,
           "wav": lambda fh, s, h: _riff_chunk(fh, s, h, False, (b"WAVE",), b"data"),
           "aiff": lambda fh, s, h: _riff_chunk(fh, s, h, True, (b"AIFF", b"AIFC"), b"SSND")}


def _ffmpeg(path):
    exe = shutil.which("ffmpeg")
    if not exe:
        return None
    try:
        r = subprocess.run([exe, "-v", "error", "-nostdin", "-i", str(path), "-map", "0:a", "-c", "copy",
                            "-f", "hash", "-hash", "sha256", "-"], capture_output=True, timeout=600, text=True)
    except (OSError, subprocess.TimeoutExpired):
        return None
    out = r.stdout.strip()
    return "ffmpeg:" + out.split("=", 1)[1] if r.returncode == 0 and "=" in out else None


def digest(path) -> str | None:
    """'kind:sha256' of the audio data, or None when it cannot be told apart from tags."""
    path = Path(path)
    try:
        size = path.stat().st_size
        with open(path, "rb") as fh:
            kind = _sniff(fh)
            if kind:
                h = hashlib.sha256()
                try:
                    if _NATIVE[kind](fh, size, h):
                        return f"{kind}:{h.hexdigest()}"
                except (ValueError, struct.error, OSError):
                    pass
    except OSError:
        return None
    return _ffmpeg(path)


def method(path) -> str:
    d = digest(path)
    return d.split(":", 1)[0] if d else "unverified"


def file_sha256(path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for b in iter(lambda: fh.read(CHUNK), b""):
            h.update(b)
    return h.hexdigest()
