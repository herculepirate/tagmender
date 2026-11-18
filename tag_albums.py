#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""TagMender (legacy batch tool): tag the confidently matched albums with an external
tagger application. It needs that application installed; TagMender itself does not.

match_albums.py decides which release each untagged album is. This script makes the external
tagger tag them, so every tag, file name, folder and image is what it writes with your
settings, naming script and plugins (Discogs Enrichment, CoverUp):

  1. backup  - every file's current tags (embedded pictures too) and location are saved,
               so a run can be undone completely;
  2. seed    - the matched MusicBrainz release ID is written into the files. The tagger loads
               a file carrying a release ID straight onto that release;
  3. tag     - the tagger is started with its own commands, a batch at a time: LOAD the
               folders, let them settle, SAVE_MATCHED, QUIT. It runs each command only once
               the previous one has finished, web requests and saves included;
  4. verify  - every file is checked: saved by the tagger (it writes the track IDs), where it
               moved to, and whether Discogs Enrichment added its fields.

The tagger must be closed when this runs: its commands would otherwise go to your open
tagger and save whatever else you have loaded there.

  python3 tag_albums.py                         # dry run: what would be tagged
  python3 tag_albums.py --apply --limit 2       # tag two albums
  python3 tag_albums.py --apply                 # tag every confident match
  python3 tag_albums.py --undo <run-id>         # put a run back exactly as it was
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import exclusions  # noqa: E402
import reasons  # noqa: E402

HERE = Path(__file__).resolve().parent
AUDIO_EXT = exclusions.AUDIO_EXT     # (the one list of audio files every module reads)
DEFAULT_PICARD = "flatpak run org.musicbrainz.Picard"

try:
    import mutagen
    from mutagen.flac import FLAC
    from mutagen.id3 import ID3, TXXX
    from mutagen.mp4 import MP4, MP4FreeForm
except ImportError:
    sys.exit("mutagen is required: pip install mutagen")


# --------------------------------------------------------------------------
# Reading and writing the few tags this script touches
# --------------------------------------------------------------------------
def audio_files(folder):
    out = []
    for dirpath, _dirs, names in os.walk(folder):
        for n in names:
            p = Path(dirpath) / n
            if p.suffix.lower() in AUDIO_EXT and not n.startswith("."):
                out.append(p)
    return sorted(out)


STILL_RUNNING = "tagger still running, not checked"
DONE = ("tagged", "tagged, no Discogs data")        # (the states of an album saved whole)
VIDEO_EXT = {".mkv", ".m4v", ".avi", ".mov", ".webm", ".mpg", ".mpeg", ".wmv", ".flv", ".vob", ".ts",
             ".m2ts", ".mts", ".3gp", ".ogv"}


def is_video(path):
    """A video file (a music video, a concert film): never one of the album's tracks, so it
    never makes an album "saved in part". An .mp4 is a video when it holds a picture track."""
    ext = Path(path).suffix.lower()
    if ext in VIDEO_EXT:
        return True
    if ext != ".mp4":
        return False
    try:
        with open(path, "rb") as fh:
            at, n = _moov(fh)
            if n > 64 << 20:
                return False
            fh.seek(at)
            moov = fh.read(n)
    except (OSError, ValueError):
        return False
    import re
    return re.search(rb"hdlr.{8}vide", moov, re.DOTALL) is not None


def _first(v):
    if isinstance(v, (list, tuple)):
        v = v[0] if v else ""
    if isinstance(v, bytes):
        v = v.decode("utf-8", "ignore")
    return str(getattr(v, "text", [v])[0] if hasattr(v, "text") else v).strip()


def read_ids(path):
    """{'albumid', 'trackid', 'discogs'} as found in the file ('' when absent)."""
    out = {"albumid": "", "trackid": "", "discogs": ""}
    try:
        f = mutagen.File(path)
    except Exception:
        return out
    if f is None or f.tags is None:
        return out
    tags = f.tags
    if isinstance(f, MP4):
        get = lambda n: _first(tags.get(f"----:com.apple.iTunes:{n}", [b""]))  # noqa: E731
        out.update(albumid=get("MusicBrainz Album Id"), trackid=get("MusicBrainz Release Track Id"),
                   discogs=get("DISCOGS_RELEASE_ID"))
        return out
    if isinstance(tags, ID3):
        def txxx(desc):
            for fr in tags.getall("TXXX"):
                if fr.desc.casefold() == desc.casefold():
                    return _first(fr.text)
            return ""
        out.update(albumid=txxx("MusicBrainz Album Id"), trackid=txxx("MusicBrainz Release Track Id"),
                   discogs=txxx("DISCOGS_RELEASE_ID"))
        return out
    low = {str(k).casefold(): v for k, v in tags.items()}
    out.update(albumid=_first(low.get("musicbrainz_albumid", "")),
               trackid=_first(low.get("musicbrainz_releasetrackid", "")),
               discogs=_first(low.get("discogs_release_id", "")))
    return out


def read_album_title(path):
    try:
        f = mutagen.File(path, easy=True)
        return _first((f.tags or {}).get("album", "")) if f is not None else ""
    except Exception:
        return ""


def _not_fragmented(path):
    import tagio
    if tagio.fragmented(path):
        raise ValueError(f"{Path(path).name} is a fragmented MP4: TagMender does not write to it "
                         "(its audio would no longer play)")


def _staged(path, write):
    """write(stage, path) on a staging copy that takes the file's place in one rename, its
    audio checked (executor.staged_write): a crash never leaves a half-moved track. A link
    is written in the file it points to, as its undo puts it back."""
    import executor
    path = Path(path)
    _not_fragmented(path)
    real = Path(os.path.realpath(path)) if path.is_symlink() else path
    executor.staged_write(real, lambda stage: write(stage, real))


def write_albumid(path, mbid):
    """Write only the MusicBrainz release ID, the way Picard stores it for each format."""
    _staged(path, lambda stage, real: _write_albumid(stage, real, mbid))


def _write_albumid(path, real, mbid):
    import tagio
    f = tagio.mutagen_file(path)                    # (a staging copy: by its content and inner name)
    if f is None:
        raise ValueError("not an audio file mutagen can write")
    if isinstance(f, MP4):
        if f.tags is None:
            f.add_tags()
        f.tags["----:com.apple.iTunes:MusicBrainz Album Id"] = [MP4FreeForm(mbid.encode())]
    elif f.tags is not None and isinstance(f.tags, ID3) or real.suffix.lower() == ".mp3":
        def edit(t):
            t.delall("TXXX:MusicBrainz Album Id")
            t.add(TXXX(encoding=3, desc="MusicBrainz Album Id", text=[mbid]))
        import tagio
        tagio.edit_id3(path, f, edit)                # (the tag in its own version, its frames as they are)
        return
    else:
        if f.tags is None:
            f.add_tags()
        f.tags["MUSICBRAINZ_ALBUMID"] = [mbid]
    f.save()


TAGMENDER_TAG = "TAGMENDER"                  # Resolved / Partial / Unresolved
TAGMENDER_REASON = "TAGMENDER_REASON"       # why an album is unresolved


def write_tagmender(path, status, reason=""):
    """TAGMENDER=Resolved|Partial|Unresolved (and the reason when unresolved), in the form
    each format stores a custom tag. Nothing else in the file changes."""
    _staged(path, lambda stage, real: _write_tagmender(stage, real, status, reason))


def _write_tagmender(path, real, status, reason):
    import tagio
    f = tagio.mutagen_file(path)                    # (a staging copy: by its content and inner name)
    if f is None:
        raise ValueError("not an audio file mutagen can write")
    reason = reasons.encode(reason) if status == "Unresolved" else ""
    if isinstance(f, MP4):
        if f.tags is None:
            f.add_tags()
        f.tags[f"----:com.apple.iTunes:{TAGMENDER_TAG}"] = [MP4FreeForm(status.encode())]
        key = f"----:com.apple.iTunes:{TAGMENDER_REASON}"
        if reason:
            f.tags[key] = [MP4FreeForm(reason.encode())]
        elif key in f.tags:
            del f.tags[key]
    elif (f.tags is not None and isinstance(f.tags, ID3)) or real.suffix.lower() == ".mp3":
        def edit(t):
            t.delall(f"TXXX:{TAGMENDER_TAG}")
            t.delall(f"TXXX:{TAGMENDER_REASON}")
            t.add(TXXX(encoding=3, desc=TAGMENDER_TAG, text=[status]))
            if reason:
                t.add(TXXX(encoding=3, desc=TAGMENDER_REASON, text=[reason]))
        import tagio
        tagio.edit_id3(path, f, edit)                # (the tag in its own version, its frames as they are)
        return
    else:
        if f.tags is None:
            f.add_tags()
        f.tags[TAGMENDER_TAG] = [status]
        if reason:
            f.tags[TAGMENDER_REASON] = [reason]
        elif TAGMENDER_REASON in f.tags:
            del f.tags[TAGMENDER_REASON]
    f.save()


def read_tagmender(path):
    try:
        f = mutagen.File(path)
    except Exception:
        return "", ""
    tags = getattr(f, "tags", None) or {}
    if isinstance(tags, ID3):
        s, r = tags.getall(f"TXXX:{TAGMENDER_TAG}"), tags.getall(f"TXXX:{TAGMENDER_REASON}")
        return (str(s[0].text[0]) if s else ""), (str(r[0].text[0]) if r else "")
    if isinstance(f, MP4):
        g = lambda k: _first(tags.get(f"----:com.apple.iTunes:{k}", ""))  # noqa: E731
        return g(TAGMENDER_TAG), g(TAGMENDER_REASON)
    low = {str(k).upper(): v for k, v in tags.items()}
    return _first(low.get(TAGMENDER_TAG, "")), _first(low.get(TAGMENDER_REASON, ""))


def picard_preserves_tagmender(ini):
    """Picard clears tags it does not write when 'clear existing tags' is on,
    unless they are listed under Options > Tags > 'Preserve these tags'."""
    import configparser
    cp = configparser.RawConfigParser(strict=False, interpolation=None)
    try:
        cp.read(Path(ini).expanduser(), encoding="utf-8")
        clear = cp.get("setting", "clear_existing_tags", fallback="false").strip().lower() == "true"
        import tagger_settings
        kept = {x.lower() for x in tagger_settings._qt_list(cp.get("setting", "preserved_tags", fallback=""))}
    except Exception:
        return True
    return not clear or TAGMENDER_TAG.lower() in kept


def audio_key(path):
    """Identifies a file's audio across a Picard save (tags, name and folder all change):
    FLAC's own checksum of the decoded audio, else the length to the millisecond."""
    try:
        f = mutagen.File(path)
    except Exception:
        return ""
    if f is None:
        return ""
    md5 = getattr(f.info, "md5_signature", 0)
    if md5:
        return f"md5:{md5:032x}"
    return f"len:{round(getattr(f.info, 'length', 0.0) * 1000)}"


# --------------------------------------------------------------------------
# Full tag backup and restore (for --undo)
# --------------------------------------------------------------------------
def _open(path):
    """mutagen.File, which also reads TagMender's staging copies (".tm-...flac.part")."""
    import tagio
    return tagio.mutagen_file(path)


def _trailer(path) -> bytes:
    """The APEv2 / Lyrics3 / ID3v1 tags at the end of the file, as they are."""
    import payload
    with open(path, "rb") as fh:
        start = payload._trailer_start(fh, os.fstat(fh.fileno()).st_size)
        fh.seek(start)
        return fh.read()


def _id3_head(path) -> bytes:
    """The ID3v2 tags at the start of the file (in front of a FLAC file's "fLaC"), as they are."""
    import payload
    with open(path, "rb") as fh:
        end = payload._id3v2_end(fh)
        fh.seek(0)
        return fh.read(end)


def _put_ends(path, blob_dir, trailer=None, head=None):
    """The trailers (and a FLAC file's ID3 head) back as backed up: "" for none."""
    import payload
    from mutagen._util import resize_bytes
    if trailer is not None:
        data = _read_blob(blob_dir, trailer) if trailer else b""
        with open(path, "r+b") as fh:
            start = payload._trailer_start(fh, os.fstat(fh.fileno()).st_size)
            fh.seek(start)
            if fh.read() != data:
                fh.seek(start)
                fh.truncate()
                fh.write(data)
    if head is not None:
        data = _read_blob(blob_dir, head) if head else b""
        with open(path, "r+b") as fh:
            end = payload._id3v2_end(fh)
            fh.seek(0)
            if fh.read(end) != data:
                resize_bytes(fh, end, len(data), 0)
                fh.seek(0)
                fh.write(data)


RECORD_KIND = 4


def _flac_meta_end(fh) -> int:
    """Where a FLAC file's audio begins: after its ID3 head, "fLaC" and its metadata blocks."""
    import payload
    pos = payload._id3v2_end(fh)
    fh.seek(pos)
    if fh.read(4) != b"fLaC":
        raise ValueError("not a FLAC file")
    pos += 4
    while True:
        fh.seek(pos)
        hdr = fh.read(4)
        if len(hdr) < 4:
            raise ValueError("truncated FLAC metadata")
        pos += 4 + int.from_bytes(hdr[1:4], "big")
        if hdr[0] & 0x80:
            return pos


def _head(path, flac=False) -> bytes:
    with open(path, "rb") as fh:
        end = _flac_meta_end(fh) if flac else __import__("payload")._id3v2_end(fh)
        fh.seek(0)
        return fh.read(end)


def _put_head(path, blob_dir, blob, flac=False):
    """The file's head - its ID3v2 tags, or a FLAC file's whole metadata - back as it was."""
    from mutagen._util import resize_bytes
    data = _read_blob(blob_dir, blob) if blob else b""
    with open(path, "r+b") as fh:
        end = _flac_meta_end(fh) if flac else __import__("payload")._id3v2_end(fh)
        fh.seek(0)
        if fh.read(end) != data:
            resize_bytes(fh, end, len(data), 0)
            fh.seek(0)
            fh.write(data)


def _ogg_head_pages(fh):
    """(the header pages of an Ogg file - its one stream's pages before the first that ends
    an audio packet -, their total size), or None (several streams, no audio, not Ogg)."""
    from mutagen.ogg import OggPage
    fh.seek(0)
    pages, end = [], 0
    while True:
        at = fh.tell()
        try:
            p = OggPage(fh)
        except Exception:
            return None
        if pages and p.serial != pages[0].serial:
            return None
        if pages and p.position not in (0, -1):
            return pages, at
        pages.append(p)
        end = fh.tell()
        if p.last or len(pages) > 4096 or end > 64 << 20:
            return None


def _ogg_head(path):
    """An Ogg file's header pages (identification, comments, setup) as they are, or None."""
    try:
        with open(path, "rb") as fh:
            got = _ogg_head_pages(fh)
            if not got:
                return None
            fh.seek(0)
            return fh.read(got[1])
    except OSError:
        return None


def _put_ogg_head(path, blob_dir, blob):
    """The header pages as they were - when they hold the same packets as the file's now
    (its tags already put back): only how mutagen laid them out across pages differs, and
    the pages after them are numbered back as they were."""
    import io
    from mutagen._util import resize_bytes
    from mutagen.ogg import OggPage
    data = _read_blob(blob_dir, blob)
    with open(path, "r+b") as fh:
        now = _ogg_head_pages(fh)
        if not now:
            return
        fh.seek(0)
        if fh.read(now[1]) == data:
            return
        pages_old = []
        bio = io.BytesIO(data)
        while bio.tell() < len(data):
            pages_old.append(OggPage(bio))
        if not pages_old or pages_old[0].serial != now[0][0].serial or \
                OggPage.to_packets(pages_old) != OggPage.to_packets(now[0]):
            return                                        # not the same headers: left as mutagen wrote them
        resize_bytes(fh, now[1], len(data), 0)
        fh.seek(0)
        fh.write(data)
        fh.seek(len(data))
        OggPage.renumber(fh, pages_old[0].serial, pages_old[-1].sequence + 1)


def _moov(fh) -> tuple[int, int]:
    """(offset, size) of an MP4 file's top-level "moov" atom (its tags, chapters and the index
    of its audio)."""
    import struct
    size_ = os.fstat(fh.fileno()).st_size
    pos = 0
    while pos + 8 <= size_:
        fh.seek(pos)
        n, kind = struct.unpack(">I4s", fh.read(8))
        if n == 1:
            n = struct.unpack(">Q", fh.read(8))[0]
        elif n == 0:
            n = size_ - pos
        if n < 8:
            break
        if kind == b"moov":
            return pos, n
        pos += n
    raise ValueError("no moov atom")


def _fragmented(fh) -> bool:
    """A fragmented MP4 (moof boxes): offsets outside its moov point into its audio."""
    import struct
    size_ = os.fstat(fh.fileno()).st_size
    pos = 0
    while pos + 8 <= size_:
        fh.seek(pos)
        n, kind = struct.unpack(">I4s", fh.read(8))
        if kind == b"moof":
            return True
        if n == 1:
            n = struct.unpack(">Q", fh.read(8))[0]
        elif n == 0:
            break
        if n < 8:
            break
        pos += n
    return False


def _child(fh, at, n, name, skip=8):
    """(offset, size) of the first child atom name in the atom at (n bytes), or None. (A meta
    atom has 4 bytes of version and flags before its children.)"""
    import struct
    pos, end = at + skip, at + n
    while pos + 8 <= end:
        fh.seek(pos)
        size, kind = struct.unpack(">I4s", fh.read(8))
        if size < 8:
            return None
        if kind == name:
            return pos, size
        pos += size
    return None


def _udta(fh):
    """(offset, size) of the moov's udta atom (where MP4 tags live), or None."""
    at, n = _moov(fh)
    return _child(fh, at, n, b"udta")


def _drop_udta(path, meta_only=False):
    """The udta atom (or the meta atom in it) a job added to an MP4 that had none, out again
    - only where the audio lies before the moov (its offsets do not move)."""
    import struct
    from mutagen._util import delete_bytes
    with open(path, "r+b") as fh:
        at, n = _moov(fh)
        u = _udta(fh)
        if u is None or _mdat_after(fh, at):
            return
        cut = _child(fh, u[0], u[1], b"meta") if meta_only else u
        if cut is None:
            return
        heads = [(at, n)] + ([u] if meta_only else [])
        for pos, size in heads:                      # (64-bit sizes: left alone)
            fh.seek(pos)
            if struct.unpack(">I", fh.read(4))[0] != size:
                return
        delete_bytes(fh, cut[1], cut[0])
        for pos, size in heads:
            fh.seek(pos)
            fh.write(struct.pack(">I", size - cut[1]))


def _first_chunk(moov: bytes):
    """The file offset of the first audio chunk an MP4 moov atom points at (stco/co64)."""
    import io
    import struct
    from mutagen.mp4 import Atoms
    atoms = Atoms(io.BytesIO(moov))
    for trak in atoms.path(b"moov")[-1].findall(b"trak", True):
        for name, fmt, size in ((b"stco", ">I", 4), (b"co64", ">Q", 8)):
            for box in trak.findall(name, True):
                fh = io.BytesIO(moov)
                fh.seek(box.offset + 8 + 4)
                if struct.unpack(">I", fh.read(4))[0]:
                    return struct.unpack(fmt, fh.read(size))[0]
    return None


def _mdat_after(fh, moov_at) -> bool:
    import struct
    size_ = os.fstat(fh.fileno()).st_size
    pos = 0
    while pos + 8 <= size_:
        fh.seek(pos)
        n, kind = struct.unpack(">I4s", fh.read(8))
        if n == 1:
            n = struct.unpack(">Q", fh.read(8))[0]
        elif n == 0:
            n = size_ - pos
        if kind == b"mdat":
            return pos > moov_at
        if n < 8:
            break
        pos += n
    return False


def _put_moov(path, blob_dir, blob):
    """(A record of b589302's: the moov atom raw.) Put back only where its audio offsets still
    hold: never over a file rewritten since (moved to "fast start", optimised)."""
    from mutagen._util import resize_bytes
    data = _read_blob(blob_dir, blob)
    with open(path, "r+b") as fh:
        if _fragmented(fh):
            raise ValueError("a fragmented MP4: its moov cannot be put back as it was")
        at, n = _moov(fh)
        fh.seek(at)
        cur = _first_chunk(fh.read(n))
        old = _first_chunk(data)
        want = None if cur is None else cur - ((n - len(data)) if _mdat_after(fh, at) else 0)
        if old != want:
            raise ValueError("the file was rewritten since the job (its audio moved); its tags are left as they are")
        fh.seek(at)
        if fh.read(n) != data:
            resize_bytes(fh, n, len(data), at)
            fh.seek(at)
            fh.write(data)


def backup_tags(path, blob_dir, legacy=False, kind=None):
    """Every tag of the file as stored, pictures written to blob_dir by checksum. An MP3's
    ID3v2 tag and a FLAC file's whole metadata are kept as they are, byte for byte, with the
    tags at the end of the file (ID3v1, APEv2, Lyrics3); Vorbis comments in their order and
    case. kind: the record as a TagMender version kept it, to compare with a job's - 0 (legacy)
    before the trailers were kept, 1 and 2 with them, RECORD_KIND now."""
    kind = 0 if legacy else (RECORD_KIND if kind is None else kind)
    f = _open(path)
    if kind >= 3:
        return _backup_raw(path, f, blob_dir, kind)
    legacy = kind == 0
    rec = {"format": type(f).__name__ if (f if kind < 2 else f is not None) else "", "tags": None, "pictures": []}
    if kind == 2:
        rec["v"] = 2                                 # (the record's kind: trailers and ID3 heads kept)
    if f is None:
        return rec
    if type(f).__name__ in ("MP3", "EasyMP3", "TrueAudio") and not legacy:
        t = _trailer(path)
        rec["trailer"] = _blob(t, blob_dir) if t else ""
    if isinstance(f, FLAC) and legacy:
        for p in f.pictures:
            rec["pictures"].append(_blob(p.write(), blob_dir))
    elif isinstance(f, FLAC):
        t, h = _trailer(path), _id3_head(path)
        rec["flac_ends"] = {"trailer": _blob(t, blob_dir) if t else "", "head": _blob(h, blob_dir) if h else ""}
        if f.seektable is not None and not f.seektable.seekpoints and kind >= 2:
            rec["flac_ends"]["empty_seektable"] = True
        for p in f.pictures:
            rec["pictures"].append(_blob(p.write(), blob_dir))
    if f.tags is None:
        return rec
    if isinstance(f.tags, ID3) and f.tags.version[0] < 2 and not legacy:
        rec["tags"] = {"id3v1_only": True}          # (the trailer has it)
        return rec
    if isinstance(f.tags, ID3) and f.tags.version[:2] == (2, 2) and "trailer" in rec and kind >= 2:
        h = _id3_head(path)                         # (mutagen cannot write v2.2: the tag as it is)
        rec["tags"] = {"id3_raw": _blob(h, blob_dir) if h else "", "id3_version": 2}
        return rec
    if isinstance(f.tags, ID3):
        tmp = Path(blob_dir) / f"backup-{os.getpid()}.id3"
        tmp.write_bytes(b"")
        v1 = "trailer" in rec                 # (an MP3's ID3v1 is kept as it is, not merged into the ID3v2)
        if f.tags.version[1] >= 4:
            (ID3(str(path), load_v1=False) if v1 else f.tags).save(str(tmp), v2_version=4)
        else:                                 # (as the file has it: v2.3 frames, never mutagen's v2.4 view)
            ID3(str(path), translate=False, load_v1=not v1).save(str(tmp), v2_version=3, v23_sep=None)
        rec["tags"] = {"id3": _blob(tmp.read_bytes(), blob_dir), "id3_version": f.tags.version[1]}
        tmp.unlink()
    elif isinstance(f, MP4):
        rec["tags"] = {"mp4": [[k, _mp4_value(v, blob_dir)] for k, v in f.tags.items()]}
    else:
        rec["tags"] = {"vorbis": [[k, v] for k, v in f.tags.items()]}
    return rec


def _backup_raw(path, f, blob_dir, kind=RECORD_KIND) -> dict:
    """The record of today's kind (see backup_tags); kind 3: as b589302 made it (an MP3's or
    FLAC file's head, an MP4's moov, raw - kept to compare with such a job's records)."""
    rec = {"format": type(f).__name__ if f is not None else "", "v": kind, "tags": None, "pictures": []}
    if f is None:
        return rec
    if kind == 3 and (type(f).__name__ in ("MP3", "EasyMP3", "TrueAudio") or isinstance(f, FLAC)):
        flac = isinstance(f, FLAC)
        h, t = _head(path, flac), _trailer(path)
        rec["tags"] = {("flac_raw" if flac else "id3_raw"): _blob(h, blob_dir) if h else ""}
        rec["trailer"] = _blob(t, blob_dir) if t else ""
        return rec
    if kind == 3 and isinstance(f, MP4):
        with open(path, "rb") as fh:
            at, n = _moov(fh)
            fh.seek(at)
            rec["tags"] = {"mp4_moov": _blob(fh.read(n), blob_dir)}
        return rec
    kind_ = kind
    kind = type(f).__name__
    if kind in ("MP3", "EasyMP3", "TrueAudio"):     # (an ID3v2 tag holds only tags: kept as it is)
        h, t = _head(path), _trailer(path)
        rec["tags"] = {"id3_raw": _blob(h, blob_dir) if h else ""}
        rec["trailer"] = _blob(t, blob_dir) if t else ""
        return rec
    if isinstance(f, FLAC):
        # the tags and pictures, and the whole metadata as it is: put back as it was when only
        # tags, pictures and padding differ then (never over a cue sheet or seek table added since)
        rec = backup_tags(path, blob_dir, kind=2)
        rec["v"] = kind_
        rec["flac_raw"] = _blob(_head(path, True), blob_dir)
        return rec
    if isinstance(f, MP4) and kind_ >= 4 and f.tags is None:
        with open(path, "rb") as fh:                 # (no tags: whether it had a udta, a meta in it)
            u = _udta(fh)
            rec["mp4_udta"] = u is not None
            rec["mp4_meta"] = u is not None and _child(fh, u[0], u[1], b"meta") is not None
        return rec
    if f.tags is None:
        return rec
    if isinstance(f.tags, ID3):                      # (WAV, AIFF, DSF: as before)
        tmp = Path(blob_dir) / f"backup-{os.getpid()}.id3"
        tmp.write_bytes(b"")
        if f.tags.version[1] >= 4:
            f.tags.save(str(tmp), v2_version=4)
        else:
            ID3(str(path), translate=False).save(str(tmp), v2_version=3, v23_sep=None)
        rec["tags"] = {"id3": _blob(tmp.read_bytes(), blob_dir), "id3_version": f.tags.version[1]}
        tmp.unlink()
    elif isinstance(f, MP4):
        rec["tags"] = {"mp4": [[k, _mp4_value(v, blob_dir)] for k, v in f.tags.items()]}
    elif isinstance(f.tags, list):                   # Vorbis comments: in their order, as spelled
        rec["tags"] = {"vorbis_raw": [[k, v] for k, v in list(f.tags)]}
        h = _ogg_head(path) if kind_ >= 4 and type(f).__name__.startswith("Ogg") else None
        if h:                                        # (and how its header pages were laid out)
            rec["ogg_head"] = _blob(h, blob_dir)
    else:
        rec["tags"] = {"vorbis": [[k, v] for k, v in f.tags.items()]}
    pad = getattr(f.tags, "_padding", None)          # (the room the tag had: put back as it was)
    if isinstance(pad, int) and pad >= 0 and ("vorbis_raw" in rec["tags"] or ("mp4" in rec["tags"] and kind_ >= 4)):
        rec["padding"] = pad
    return rec


def _blob(data, blob_dir):
    """data kept in blob_dir under its checksum - written whole and synced before any record
    names it (a power cut never leaves a short blob a later job would take as written)."""
    h = hashlib.sha1(data).hexdigest()
    p = Path(blob_dir) / h
    if not p.exists() or hashlib.sha1(p.read_bytes()).hexdigest() != h:
        tmp = p.with_name(f".{h}.{os.getpid()}.tmp")
        with open(tmp, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, p)
    return h


def _read_blob(blob_dir, h) -> bytes:
    """A blob, checked against its checksum: damaged data is never written into a file."""
    data = (Path(blob_dir) / h).read_bytes()
    if hashlib.sha1(data).hexdigest() != h:
        raise ValueError(f"the backup data {h[:12]} is damaged; the file is left as it is")
    return data


def _flac_blocks(data: bytes) -> list:
    """A FLAC file's metadata blocks (type, payload), from its head as _head reads it."""
    import io
    import payload
    fh = io.BytesIO(data)
    pos = payload._id3v2_end(fh) + 4
    out = []
    while pos + 4 <= len(data):
        t, n = data[pos] & 0x7F, int.from_bytes(data[pos + 1:pos + 4], "big")
        out.append((t, data[pos + 4:pos + 4 + n]))
        pos += 4 + n
        if data[pos - 4 - n] & 0x80:
            break
    return out


def _flac_raw_fits(path, raw: bytes) -> bool:
    """Only the tags, pictures and padding of path's metadata differ from raw's (its stream
    info, seek table, cue sheet and application blocks are as they were)."""
    # (an empty seek table counts with the padding: Picard's seek point fix removes it)
    keep = lambda bl: [b for b in bl if b[0] not in (1, 4, 6) and not (b[0] == 3 and not b[1])]   # noqa: E731
    try:
        return keep(_flac_blocks(raw)) == keep(_flac_blocks(_head(path, True)))
    except (OSError, ValueError, IndexError):
        return False


def _mp4_value(values, blob_dir):
    if not isinstance(values, (list, tuple)):          # (cpil, pgap, pcst: a bare true/false)
        return {"scalar": _mp4_value([values], blob_dir)[0]}
    out = []
    for v in values:
        if isinstance(v, bytes):                     # covers and freeform
            out.append({"bytes": _blob(bytes(v), blob_dir), "kind": type(v).__name__,
                        "fmt": getattr(v, "imageformat", getattr(v, "dataformat", None))})
        elif isinstance(v, tuple):
            out.append({"tuple": list(v)})
        else:
            out.append({"value": v})
    return out


def rec_key(rec, loose=True) -> str:
    """A tag backup record compared without the order of its tags (mutagen lists Vorbis
    comment keys from a set: the order differs between two reads of one file); loose: an MP4
    with an empty tag counts as one with none (whether a file was edited since); else
    exactly (whether there is anything left to put back)."""
    rec = dict(rec or {})
    tags = dict(rec.get("tags") or {})
    if loose:
        rec.pop("ogg_head", None)            # (only how the header pages are laid out)
    if not loose:
        pass
    elif tags.get("mp4") == []:
        tags = {}
        for k in ("padding", "mp4_udta", "mp4_meta"):
            rec.pop(k, None)
    elif not tags:
        for k in ("mp4_udta", "mp4_meta"):
            rec.pop(k, None)
    for k in ("vorbis", "mp4"):
        if k in tags:
            tags[k] = sorted(json.dumps(x, sort_keys=True, default=str) for x in tags[k])
    rec["tags"] = tags
    return json.dumps(rec, sort_keys=True, default=str)


def same_as_backup(path, rec, loose=True) -> bool:
    """path holds what the backup record says (compared as the version that made it made it)."""
    import tempfile
    old = rec or {}
    with tempfile.TemporaryDirectory() as d:
        kind = old.get("v") or (1 if ("trailer" in old or "flac_ends" in old) else 0)
        told = old.get("tags") or {}
        # (two shapes were written as "v": 3: b589302's raw heads and moov, c8c7cd6's today's)
        newer3 = kind == 3 and not ("mp4_moov" in told or "flac_raw" in told)
        now = backup_tags(path, d, kind=RECORD_KIND if newer3 else kind)
        if newer3:
            now["v"] = 3
        if "ogg_head" not in old:            # (a record made before the layout was kept)
            now.pop("ogg_head", None)
        return rec_key(now, loose) == rec_key(old, loose)


def restore_tags(path, rec, blob_dir):
    """The file's tags as the record has them. Nothing is written to a file that holds them
    already, nor to a fragmented MP4 (its audio would no longer play)."""
    from mutagen.flac import Picture
    from mutagen.mp4 import MP4Cover
    import tagio
    try:
        if same_as_backup(path, rec, loose=False):
            return
    except Exception:
        pass
    if tagio.fragmented(path):
        raise ValueError(f"{Path(path).name} is a fragmented MP4: its tags are left as they are "
                         "(writing them would break its audio)")
    f = _open(path)
    if f is None:
        raise ValueError("cannot open")
    tags = rec.get("tags")
    trailer = rec.get("trailer")
    if rec.get("flac_raw") and isinstance(f, FLAC) and _flac_raw_fits(path, _read_blob(blob_dir, rec["flac_raw"])):
        _put_head(path, blob_dir, rec["flac_raw"], flac=True)          # (byte for byte, as it was)
        ends = rec.get("flac_ends") or {}
        _put_ends(path, blob_dir, trailer=ends.get("trailer", ""))
        return
    if tags and ("id3_raw" in tags or "flac_raw" in tags):     # (the head as it was, byte for byte)
        flac = "flac_raw" in tags
        if flac and not _flac_raw_fits(path, _read_blob(blob_dir, tags["flac_raw"])):
            raise ValueError("its metadata changed since the job (a cue sheet or seek table); left as it is")
        _put_head(path, blob_dir, tags["flac_raw" if flac else "id3_raw"], flac=flac)
        if trailer is not None:
            _put_ends(path, blob_dir, trailer=trailer)
        return
    if tags and "mp4_moov" in tags:
        _put_moov(path, blob_dir, tags["mp4_moov"])
        return
    pad = rec.get("padding")
    save_kw = {"padding": (lambda info: pad)} if isinstance(pad, int) else {}
    if tags and "vorbis_raw" in tags:
        del f.tags[:]
        f.tags.extend((k, v) for k, v in tags["vorbis_raw"])
        f.save(**save_kw)
        if rec.get("ogg_head"):
            _put_ogg_head(path, blob_dir, rec["ogg_head"])
        return
    if isinstance(f, FLAC):
        f.clear_pictures()
        for h in rec.get("pictures", []):
            f.add_picture(Picture(_read_blob(blob_dir, h)))
        ends_ = rec.get("flac_ends") or {}
        if ends_.get("empty_seektable") and f.seektable is None:          # (Picard's seek point fix undone)
            from mutagen.flac import SeekTable
            f.seektable = SeekTable(b"")
            f.metadata_blocks.insert(1, f.seektable)
    if tags and "id3" in tags:
        tmp = Path(blob_dir) / f"restore-{os.getpid()}.id3"
        tmp.write_bytes(_read_blob(blob_dir, tags["id3"]))
        v23 = tags.get("id3_version", 4) < 4
        id3 = ID3(str(tmp), translate=not v23)
        tmp.unlink()
        id3.save(str(path), v2_version=3 if v23 else 4, v23_sep=None)        # (several values as stored)
        if trailer is not None:
            _put_ends(path, blob_dir, trailer=trailer)
        return
    if rec.get("format") in ("MP3", "EasyMP3", "TrueAudio") or str(path).lower().endswith(".mp3") or \
            type(f).__name__ in ("MP3", "EasyMP3", "TrueAudio"):
        try:                                             # it had no ID3v2 tag before
            ID3(str(path)).delete(str(path), delete_v1=trailer is None)
        except Exception:
            pass
        if trailer is not None:
            _put_ends(path, blob_dir, trailer=trailer)
        return
    if isinstance(f, MP4) and not tags and rec.get("v", 0) >= 3:      # (it had no tags: none again)
        if f.tags is not None:
            f.delete()
        if rec.get("mp4_udta") is False:
            _drop_udta(path)
        elif rec.get("mp4_meta") is False:
            _drop_udta(path, meta_only=True)
        return
    if isinstance(f, FLAC) and not tags and f.tags is not None:     # (it had no comment block)
        f.metadata_blocks = [b for b in f.metadata_blocks if b is not f.tags]
        f.tags = None
        f.save()
        ends = rec.get("flac_ends")
        if ends:
            _put_ends(path, blob_dir, trailer=ends.get("trailer", ""), head=ends.get("head", ""))
        return
    if f.tags is None:
        f.add_tags()
    f.tags.clear()
    if tags and "vorbis" in tags:
        for k, v in tags["vorbis"]:
            f.tags[k] = v
    elif tags and "mp4" in tags:
        for k, vals in tags["mp4"]:
            out = []
            scalar = isinstance(vals, dict) and "scalar" in vals
            for v in ([vals["scalar"]] if scalar else vals):
                if "bytes" in v:
                    data = _read_blob(blob_dir, v["bytes"])
                    out.append(MP4Cover(data, v["fmt"]) if v["kind"] == "MP4Cover"
                               else MP4FreeForm(data, 1 if v["fmt"] is None else v["fmt"]))
                elif "tuple" in v:
                    out.append(tuple(v["tuple"]))
                else:
                    out.append(v["value"])
            f.tags[k] = out[0] if scalar and out else out
    f.save(**(save_kw if isinstance(f, MP4) else {}))
    ends = rec.get("flac_ends")
    if isinstance(f, FLAC) and ends:
        _put_ends(path, blob_dir, trailer=ends.get("trailer", ""), head=ends.get("head", ""))


# --------------------------------------------------------------------------
# Which albums
# --------------------------------------------------------------------------
def is_no_vox(path, titles=()):
    """Kept for older callers: the "[no vox]" rule now lives in exclusions.json (rule no-vox)."""
    meta = {"path": [str(path)], "folder": [Path(path).name], "album": [t for t in titles if t]}
    return any(r.id == "no-vox" and exclusions.matches(r.when, meta) for r in exclusions.load_rules())


def matcher_version():
    """The version stamp match_albums.py gives its results (its code and match_core's)."""
    h = hashlib.md5()
    for name in ("match_core.py", "match_albums.py"):
        try:
            h.update((HERE / name).read_bytes())
        except OSError:
            pass
    return h.hexdigest()[:12]


def stale_reason(o, allow_old=False):
    """Why a saved match must not be tagged as it is: made by an older matcher (a fix
    since may pick another release)."""
    v = str(o.get("version") or "").split("-")[0]
    if not allow_old and v != matcher_version():
        return ("matched by an older version of the matcher; let match_albums.py check it again "
                "first (or --allow-old-matches)")
    return ""


def folder_contradictions(folder, mbid, releases):
    """What the folder name states (format, catalogue number, country, edition year)
    that the release contradicts. Empty when they agree or the release data is unknown."""
    try:
        import check_tagged
    except ImportError:
        return []
    rel = releases.mb(mbid) if releases else None
    if not rel:
        return []
    return check_tagged.check(Path(folder).name, rel)[0]


def recheck(matches_db, album, allow_old=False):
    """Just before tagging: the saved match again (the matcher may have changed its pick
    since the run began). Returns a reason to skip, or ''."""
    try:
        db = sqlite3.connect(f"file:{Path(matches_db).expanduser()}?mode=ro", uri=True, timeout=30)
        row = db.execute("SELECT result FROM results WHERE path=?", (album["path"],)).fetchone()
        db.close()
    except sqlite3.Error:
        return ""
    if not row:
        return ""
    o = json.loads(row[0])
    b = o.get("best") or {}
    if b.get("tie_guess"):           # (TIE-1: a pressing a tie-break only suggested: album known)
        return "the matcher only suggests this pressing (a tie-break chose it, TIE-1)"
    if o.get("status") != "auto":
        return f"the matcher now says {o.get('status')}: " + "; ".join((o.get("why") or [])[:2])
    if (b.get("mb_id") or (b.get("id") if b.get("source") == "mb" else "")) != album["mbid"]:
        return f"the matcher now picks another release ({b.get('id')})"
    return stale_reason(o, allow_old)


def select_albums(matches_db, limit=None, only=None, process_anyway=False, allow_old=False, releases=None,
                  retag=False):
    """Confident MusicBrainz matches whose folder still exists and that Picard has not
    tagged since (no file carries a different release ID, none is saved by Picard),
    and that no Ignore/Skip rule excludes, checked again here before anything is written.
    Also left out: matches made by an older matcher, and releases the folder name
    contradicts ("[Vinyl Rip]" on a digital release, another catalogue number or country)."""
    db = sqlite3.connect(Path(matches_db).expanduser())
    rules = exclusions.load_rules()
    out, skipped = [], []
    for path, blob in db.execute("SELECT path, result FROM results ORDER BY path"):
        o = json.loads(blob)
        b = o.get("best") or {}
        if b.get("tie_guess"):       # (TIE-1: a pressing a tie-break only suggested: web_tag.py's, album known)
            continue
        # Discogs decides the pressing; Picard tags from the MusicBrainz release of that
        # same pressing ("mb_id"). A Discogs pick with no such release is web_tag.py's.
        mbid = b.get("mb_id") or (b.get("id") if b.get("source") == "mb" else "")
        if o.get("status") != "auto" or not mbid:
            continue
        if only and only.casefold() not in path.casefold():
            continue
        b = dict(b, id=mbid)
        stale = stale_reason(o, allow_old)
        if stale:
            skipped.append((path, stale))
            continue
        bad = folder_contradictions(path, b["id"], releases)
        if bad:
            skipped.append((path, "the folder name contradicts the matched release: " + "; ".join(bad)))
            continue
        folder = Path(path)
        if not folder.is_dir():
            skipped.append((path, "folder no longer exists"))
            continue
        files = audio_files(folder)
        if not files:
            skipped.append((path, "no audio files"))
            continue
        import tagio
        frag = [p.name for p in files if tagio.fragmented(p)]
        if frag:                                    # (Picard writes through mutagen too)
            skipped.append((path, f"{frag[0]} is a fragmented MP4: the tagger would break its audio; "
                                  "the album is left for you"))
            continue
        release = {"album": [b.get("title") or ""], "albumartist": [b.get("artist") or ""],
                   "artist": [b.get("artist") or ""]}
        dec = exclusions.gate(files, "tag", rules, process_anyway=process_anyway)
        if not dec.excluded and not process_anyway:
            dec = exclusions.gate(files, "tag", rules, release=release)
        if dec.excluded:
            whole = dec.summary() or next(iter(dec.excluded.values()))
            skipped.append((path, whole.reason() if dec.all_excluded else
                            f"{whole.reason()} for {len(dec.excluded)} of {len(files)} files: the tagger tags whole "
                            "folders, so the album is left for you (or use --process-anyway)"))
            continue
        ids = [read_ids(p) for p in files]
        other = {i["albumid"] for i in ids if i["albumid"] and i["albumid"] != b["id"]}
        # retag (fixes found by validate.py): the files carry the WRONG release on purpose
        if other and not retag:
            skipped.append((path, f"files already carry another release ID {sorted(other)[0]}"))
            continue
        if all(i["trackid"] for i in ids) and not retag:
            skipped.append((path, "already tagged by the external tagger"))
            continue
        if (retag and not other and all(i["trackid"] for i in ids)
                and all(read_tagmender(p)[0] == "Resolved" for p in files)):
            # done by an earlier retag. Without TAGMENDER=Resolved the release ID may be only
            # the seed of a run Picard did not save, next to the old release's track IDs
            # (Enslaved - Frost: skipped as "already tagged" though Picard never saved it)
            skipped.append((path, "already tagged with this release"))
            continue
        out.append({"path": path, "mbid": b["id"], "artist": b.get("artist", ""), "title": b.get("title", ""),
                    "confidence": b.get("confidence"), "files": [str(p) for p in files]})
        if limit and len(out) >= limit:
            break
    return out, skipped


# --------------------------------------------------------------------------
# Picard
# --------------------------------------------------------------------------
def is_picard_process(cmdline):
    """A running Picard: the Flatpak app, or a program named exactly 'picard' (also when
    started through python). Folders such as 'picard-discogs-enrichment' do not count."""
    if "org.musicbrainz.Picard" in cmdline and "tag_albums.py" not in cmdline:
        return True
    return any(os.path.basename(w) == "picard" for w in cmdline.split())


def tagger_processes():
    """Every running tagger process: [(pid, uid, seconds since it started, command)]."""
    try:
        r = subprocess.run(["ps", "-eo", "pid=,uid=,etimes=,args="], capture_output=True, text=True)
    except FileNotFoundError:
        return []
    out = []
    for line in r.stdout.splitlines():
        parts = line.split(None, 3)
        if len(parts) < 4 or not all(x.isdigit() for x in parts[:3]):
            continue
        if is_picard_process(parts[3].strip()):
            out.append((int(parts[0]), int(parts[1]), float(parts[2]), parts[3].strip()))
    return out


def picard_running(since=None, exclude=()):
    """The first running tagger process ('pid command'), or '' when the tagger is closed.
    With SINCE (the time this run started the tagger): only a tagger of this user started
    since then whose PID is not in EXCLUDE (those running before) - the one this run
    started, never another user's or one that was open already."""
    if os.environ.get("TAGMENDER_SKIP_PICARD_CHECK") == "1":       # tests only
        return ""
    mine = os.getpid()
    for pid, uid, age, cmd in tagger_processes():
        if pid == mine:
            continue
        if since is not None and (uid != os.getuid() or pid in exclude or age > time.time() - since + 2):
            continue
        return f"{pid} {cmd[:150]}"
    return ""


def session_restore_on(ini):
    import configparser
    cp = configparser.RawConfigParser(strict=False, interpolation=None)
    try:
        cp.read(Path(ini).expanduser(), encoding="utf-8")
        return cp.get("setting", "session_load_last_on_startup", fallback="false").strip().lower() == "true"
    except Exception:
        return False


def picard_commands(folders, settle):
    """LOAD each folder, let late plugin requests start and finish (PAUSE waits for
    Picard to be idle first), save the matched files, quit once saves are done."""
    args = []
    for folder in folders:
        args += ["-e", "LOAD", str(folder)]
    for _ in range(3):
        args += ["-e", "PAUSE", str(settle)]
    args += ["-e", "SAVE_MATCHED", "-e", "PAUSE", "5", "-e", "QUIT", "FORCE"]
    return args


def run_picard(picard_cmd, folders, settle, timeout, log):
    cmd = picard_cmd.split() + picard_commands(folders, settle)
    log.write(f"\n$ {' '.join(cmd)}\n")
    log.flush()
    try:
        r = subprocess.run(cmd, stdout=log, stderr=subprocess.STDOUT, timeout=timeout)
        return r.returncode
    except subprocess.TimeoutExpired:
        log.write(f"The tagger did not finish within {timeout} s\n")
        return -1


def wait_for_tagger_exit(log=None, timeout=None, poll=2.0, since=None, exclude=()):
    """Wait until the tagger this run started has exited (its command can return while the
    application still saves: a window already open received the commands, or Flatpak's
    wrapper ended first). SINCE and EXCLUDE: see picard_running. '' once it is gone; the
    process found when TIMEOUT seconds (default TAGMENDER_TAGGER_EXIT_WAIT, 600) passed with
    it still running."""
    if timeout is None:
        try:
            timeout = float(os.environ.get("TAGMENDER_TAGGER_EXIT_WAIT", "600"))
        except ValueError:
            timeout = 600.0
    end = time.monotonic() + timeout
    while True:
        running = picard_running(since=since, exclude=exclude)
        left = end - time.monotonic()
        if not running or left <= 0:
            if running and log is not None:
                log.write(f"The tagger was still running {timeout:.0f} s after its commands: {running}\n")
            return running
        time.sleep(min(poll, left))


# --------------------------------------------------------------------------
# Verify: where did each file go, and was it tagged?
# --------------------------------------------------------------------------
def changed_dirs(root, since, full=False):
    """Folders under root that changed after `since` (files or folders were added).
    A new album folder changes its artist folder, so only changed folders are descended
    into: a few thousand directory reads instead of walking the whole library. With
    full=True every folder is checked (used only when a file cannot be found)."""
    out = []

    def scan(folder, depth):
        try:
            entries = list(os.scandir(folder))
        except OSError:
            return
        for e in entries:
            try:
                if not e.is_dir(follow_symlinks=False):
                    continue
                changed = e.stat(follow_symlinks=False).st_mtime >= since - 2
            except OSError:
                continue
            if changed:
                out.append(Path(e.path))
            if (changed or full) and depth < 4:
                scan(e.path, depth + 1)

    scan(root, 0)
    return out


IMAGE_EXT = {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".tif", ".tiff", ".webp"}
COVER_NAME = ("cover", "folder", "albumart")          # Picard's own local-cover file names


def _free(dest):
    if not dest.exists():
        return dest
    n = 2
    while True:
        cand = dest.with_name(f"{dest.stem} ({n}){dest.suffix}")
        if not cand.exists():
            return cand
        n += 1


def _is_cover_name(path):
    return Path(path).suffix.lower() in IMAGE_EXT and Path(path).stem.casefold().startswith(COVER_NAME)


def tidy_artwork(album_path, new_dir, extras, covers_name="covers"):
    """After Picard, so that every file of the album is in the new folder, in its place:
      - cover.* / folder.* / albumart.* images: next to the music (Picard and players read them);
      - logs, cue sheets, text and other non-images: next to the music;
      - all other artwork, scan folders included: under covers/, subfolder names kept.
    Picard moves only matching files next to the music (and matching folders), so this takes
    what it left in the old folder, the loose scans and scan folders it moved, and repairs a
    cover image that ended up inside covers/. covers_name: that folder's name (an album's own
    "Covers" folder is merged into it). Returns [[from, to], ...] for --undo."""
    old, new = Path(album_path), Path(new_dir)
    covers = new / covers_name
    holders = {"covers", covers_name.casefold()}
    moves = []

    def move(src, dest):
        dest = _free(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(dest))
        moves.append([str(src), str(dest)])

    def place(src, rel):
        """Where a file of the album belongs, by its path relative to the album folder."""
        rel = Path(rel)
        top_level = len(rel.parts) == 1
        if top_level and _is_cover_name(rel):
            target = new / rel.name                    # one already there: keep this one in covers/
            move(src, target if not target.exists() else covers / rel.name)
        elif top_level and rel.suffix.lower() not in IMAGE_EXT:
            move(src, new / rel.name)                  # logs, cue sheets, text: with the music
        elif rel.parts[0].casefold() in holders:       # the album's own Covers/ folder: merge
            move(src, covers.joinpath(*rel.parts[1:]))
        else:
            move(src, covers / rel)

    # 1. everything Picard left in the old folder
    if old.exists() and old.resolve() != new.resolve():
        if any(p.suffix.lower() in AUDIO_EXT and not is_video(p) for p in old.rglob("*") if p.is_file()):
            return moves                               # music still here: not saved, leave it
        for p in sorted(old.rglob("*")):
            if p.is_file() and is_video(p):            # a music video the tagger left: keeps its place
                move(p, new / p.relative_to(old))
            elif p.is_file():
                place(p, p.relative_to(old))
        _remove_empty(old)
    # 2. loose scans and scan folders Picard moved next to the music
    for rel in extras:
        rel = Path(rel)
        if rel.parts[0].casefold() in holders:
            continue
        p = new / rel
        if not p.is_file():
            continue
        if len(rel.parts) > 1 or (p.suffix.lower() in IMAGE_EXT and not _is_cover_name(p)):
            move(p, covers / rel)
    for p in list(new.iterdir()):                      # a Covers/ folder Picard moved: merge it
        if p.is_dir() and p.name != covers_name and p.name.casefold() in holders:
            for f in sorted(p.rglob("*")):
                if f.is_file():
                    move(f, covers / f.relative_to(p))
            _remove_empty(p, keep_parent=True)
    for rel in {Path(r).parts[0] for r in extras if len(Path(r).parts) > 1}:
        if rel.casefold() not in holders and (new / rel).is_dir():
            _remove_empty(new / rel, keep_parent=True)
    # 3. a cover image misplaced inside covers/ goes back next to the music
    if covers.is_dir() and not any(_is_cover_name(p) for p in new.iterdir() if p.is_file()):
        for p in sorted(covers.iterdir()):
            if p.is_file() and _is_cover_name(p):
                move(p, new / p.name)
    return moves

def existing_dirs(root, depth=5):
    """ROOT and every folder down to DEPTH levels below it (5: as deep as changed_dirs looks),
    as text: taken before the tagger runs, so that a folder it made is told from one that was
    there (a folder's times say only that something in it changed). A linked folder is listed
    with the folders in it, and links are not followed further."""
    root = str(Path(root))
    out = {root}

    def scan(folder, level):                    # (LEVEL: how deep the folders in FOLDER are)
        try:
            entries = list(os.scandir(folder))
        except OSError:
            return
        for e in entries:
            try:
                link = e.is_symlink()
                if not e.is_dir():
                    continue
            except OSError:
                continue
            out.add(e.path)
            if link:
                try:
                    out.update(c.path for c in os.scandir(e.path) if c.is_dir())
                except OSError:
                    pass
            elif level < depth:
                scan(e.path, level + 1)

    scan(root, 1)
    return out


def disc_number(folder, files):
    """Which disc of a set an album folder holds: the disc number its tracks carry, when they
    all carry the same one; else a "CD2" / "Disc 2" in the folder's name; None when neither
    says (two copies of a disc or of a release are told from two discs of a set by this)."""
    import re
    # (the folder's number: "CD2", "Disc 2", "Disc2of2"; never a year, "CD 2000")
    m = re.search(r"(?i)\b(?:cd|disc|disk)\s*[-_.]?\s*0*(\d{1,2})(?!\d)", Path(folder).name)
    in_name = int(m.group(1)) or None if m else None
    tags = set()
    for p in files:
        try:
            f = mutagen.File(p, easy=True)
            v = f.tags.get("discnumber") if f is not None and f.tags is not None else None
        except Exception:
            v = None
        m = re.match(r"\s*0*(\d+)\s*(?:/\s*0*(\d+))?", str(v[0])) if v else None
        tags.add((int(m.group(1)), int(m.group(2)) if m.group(2) else None) if m else None)
    if len(tags) == 1 and None not in tags:
        number, total = tags.pop()
        # (discs ripped one at a time each say "1/1": the folder's "CD2" is the one to go by)
        if in_name is not None and in_name != number and total in (None, 1):
            return in_name
        return number
    return in_name


def clear_tagger_leftovers(folders, album_paths, keep, existed, root):
    """After albums were put back: what the tagger left in the folders it made for them.
    A folder counts as made by it when it was not there before the run (EXISTED), is not
    an album's own folder, inside one or above one, and holds no audio file now. In such a
    folder a cover image is removed, and the folder once empty (never one that was there
    before); anything else is listed, never removed. KEEP: the files of every album of the
    batch, wherever they are now, never touched. Returns the files listed."""
    albums = [Path(a) for a in album_paths]
    left = []
    for d in sorted(folders):
        d = Path(d)
        if (not d.is_dir() or str(d) in existed or str(d) == str(Path(root))
                or any(d == a or a in d.parents or d in a.parents for a in albums)):
            continue
        files = [p for p in sorted(d.rglob("*")) if p.is_file()]
        if any(p.suffix.lower() in AUDIO_EXT for p in files):
            continue                                   # (music is filed there: not a leftover folder)
        for p in files:
            if str(p) in keep:
                continue
            if _is_cover_name(p):
                try:
                    p.unlink()
                    continue
                except OSError:
                    pass
            left.append(str(p))
        _remove_empty(d, keep=existed | {str(Path(root))})
    return left


def verify(album, files_rec, candidates):
    """Find each original file (by its audio) in the folders that changed, and say whether
    Picard saved it and whether Discogs Enrichment added its fields."""
    by_key = {}
    for d in candidates:
        for p in audio_files(d) if d.is_dir() else []:
            ids = read_ids(p)
            if ids["albumid"] == album["mbid"]:
                by_key.setdefault(audio_key(p), []).append((p, ids))
    # saved_paths / discogs_paths: the original paths of the files counted in saved / with_discogs
    result = {"saved": 0, "with_discogs": 0, "not_saved": [], "new_paths": {}, "saved_paths": [],
              "discogs_paths": []}

    def saved(ids, rec):
        # Picard saved the file when it carries a track ID of this release. On a retag the
        # old release's track ID is still there until Picard saves, so it does not count
        # (AVKRVST - The Approbation: "tagged" with only the seeded album ID, old fields kept).
        # A file that already carried this very release keeps its track ID when saved
        # (Sepultura - Beneath the Remains: disc 1 already had it, disc 2 was the download).
        return bool(ids["trackid"]) and (ids["trackid"] != (rec.get("old_trackid") or "")
                                         or (rec.get("old_albumid") or "") == album["mbid"])

    def discogs(ids, rec):
        return bool(ids["discogs"]) and (ids["discogs"] != (rec.get("old_discogs") or "") or saved(ids, rec))

    for rec in files_rec:
        old = Path(rec["path"])
        if old.exists():
            ids = read_ids(old)
            if saved(ids, rec):                          # saved in place (not moved)
                result["saved"] += 1
                result["saved_paths"].append(rec["path"])
                if discogs(ids, rec):
                    result["with_discogs"] += 1
                    result["discogs_paths"].append(rec["path"])
                result["new_paths"][rec["path"]] = rec["path"]
            else:
                result["not_saved"].append(rec["path"])
            continue
        found = by_key.get(rec["key"]) or []
        if found:
            p, ids = found.pop(0)
            result["new_paths"][rec["path"]] = str(p)
            if saved(ids, rec):
                result["saved"] += 1
                result["saved_paths"].append(rec["path"])
            if discogs(ids, rec):
                result["with_discogs"] += 1
                result["discogs_paths"].append(rec["path"])
        else:
            result["not_saved"].append(rec["path"] + " (moved, new place not found)")
    return result


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--matches-db", default="~/Downloads/matching/match_state.db")
    ap.add_argument("--out-dir", default="~/Downloads/tagging")
    ap.add_argument("--library-root", default=None,
                    help="where the tagger moves files (your 'Move files to' setting); default: read from "
                         "your tagger settings")
    ap.add_argument("--tagger-ini", dest="picard_ini", metavar="PATH",
                    # (TAGMENDER_TAGGER_INI first, as everywhere else: the test runner points it away
                    # from your own settings)
                    default=(os.environ.get("TAGMENDER_TAGGER_INI") or os.environ.get("TAGMENDER_PICARD_INI")
                             or "~/.var/app/org.musicbrainz.Picard/config/MusicBrainz/Picard.ini"),
                    help="the tagger's settings file (default: found automatically)")
    ap.add_argument("--picard-ini", dest="picard_ini", default=argparse.SUPPRESS, help=argparse.SUPPRESS)
    ap.add_argument("--tagger", dest="picard", metavar="COMMAND", default=DEFAULT_PICARD,
                    help="command that starts the external tagger application (default: its Flatpak app)")
    ap.add_argument("--picard", dest="picard", default=argparse.SUPPRESS, help=argparse.SUPPRESS)
    ap.add_argument("--apply", action="store_true", help="tag (default: dry run)")
    ap.add_argument("--mark-unresolved", action="store_true",
                    help=f"write {TAGMENDER_TAG}=Unresolved and the reason into albums left for review "
                         "or with no match (with --apply; undoable)")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--only", help="only folders whose path contains this text")
    ap.add_argument("--process-anyway", action="store_true",
                    help="tag albums even when an Ignore/Skip rule excludes them (this run only)")
    ap.add_argument("--into", help="albums come from outside the library (an inbox) and the tagger files them "
                                   "into this library folder, which must be the tagger's 'Move files to' folder")
    ap.add_argument("--retag", action="store_true",
                    help="re-tag albums that carry another release (with validate.py's fixes.db)")
    ap.add_argument("--allow-old-matches", action="store_true",
                    help="also tag matches made by an older version of the matcher")
    ap.add_argument("--batch", type=int, default=10, help="albums per tagger run (default 10)")
    ap.add_argument("--settle", type=int, default=10, help="seconds the tagger is left idle before saving")
    ap.add_argument("--undo", help="restore a run: its files' tags, names and folders")
    ap.add_argument("--tidy", metavar="RUN_ID", help="move the scans of an earlier run's albums into covers/")
    ap.add_argument("--no-tidy", action="store_true",
                    help="leave scans where the tagger put them (default: move them into covers/)")
    ap.add_argument("--covers-name", default="covers",
                    help="the name of the folder scans go into (default: covers)")
    args = ap.parse_args()
    out_dir = Path(args.out_dir).expanduser()
    out_dir.mkdir(parents=True, exist_ok=True)

    if args.undo:
        return undo(out_dir, args.undo)
    if not picard_preserves_tagmender(args.picard_ini):
        print(f"Note: add '{TAGMENDER_TAG.lower()}, {TAGMENDER_REASON.lower()}' to the tagger's Options > Tags > "
              "'Preserve these tags', or the tagger removes them whenever it saves these files again.")
    if args.mark_unresolved:
        return mark_unresolved(args, out_dir)
    if args.tidy:
        return tidy_run(out_dir, args.tidy, args.library_root or picard_move_target(args.picard_ini),
                        covers_name=args.covers_name)

    root = args.library_root or picard_move_target(args.picard_ini)
    try:
        import check_tagged
        releases = check_tagged.Releases([Path(args.matches_db).expanduser()])
    except ImportError:
        releases = None
    albums, skipped = select_albums(args.matches_db, args.limit, args.only, args.process_anyway,
                                    args.allow_old_matches, releases, retag=args.retag)
    print(f"{len(albums)} album(s) to tag; {len(skipped)} skipped")
    outside = outside_move_target(albums, args.picard_ini)
    if outside and args.into and Path(args.into).expanduser().resolve() == Path(outside[0]).resolve():
        outside = None                                # an inbox: Picard files them into this library
    if outside:
        target, paths = outside
        msg = (f"The tagger moves tagged files to {target}, but {len(paths)} of these album(s) are in another "
               f"library, e.g.\n  {paths[0]}\nTagging them now would move them into {target}. In the tagger: "
               "Options > File Naming > 'Move files to this directory when saving': set it to the library "
               "being tagged (and back afterwards), close the tagger, and run again.")
        if args.apply:
            sys.exit(msg)
        print("WARNING: " + msg)
    if not args.apply:
        plan = out_dir / "tag_plan.csv"
        with open(plan, "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(["Album folder", "Files", "MusicBrainz release", "Matched as", "Confidence"])
            for a in albums:
                w.writerow([a["path"], len(a["files"]), f"https://musicbrainz.org/release/{a['mbid']}",
                            f"{a['artist']} - {a['title']}", a["confidence"]])
            for p, why in skipped:
                w.writerow([p, "", "", f"SKIPPED: {why}", ""])
        print(f"Dry run, nothing changed. Plan: {plan}\nThe tagger will move files under: {root}\n"
              "Run again with --apply (start with --limit 2), with the tagger closed.")
        return 0

    if not albums:
        for p, why in skipped:
            print(f"  skipped: {p}\n      {why}")
        print("Nothing to tag.")
        return 0

    running = picard_running()
    if running:
        sys.exit("The tagger is open. Close it first: its commands would go to the open tagger and "
                 f"save whatever else is loaded there.\n  found: {running}")
    if session_restore_on(args.picard_ini):
        sys.exit("The tagger is set to start with your last session (Options > Sessions). Turn that off "
                 "for this run: its files would be saved along with the albums being tagged.")
    if not root or not Path(root).is_dir():
        sys.exit(f"Library folder the tagger moves files to not found: {root!r}. Pass --library-root.")

    run_id = time.strftime("%Y%m%d-%H%M%S")
    run_dir, n = out_dir / f"run-{run_id}", 1
    while run_dir.exists():                    # two runs in the same second
        n += 1
        run_dir = out_dir / f"run-{run_id}-{n}"
    run_id = run_dir.name[len("run-"):]
    blob_dir = run_dir / "blobs"
    blob_dir.mkdir(parents=True)
    log = open(run_dir / "tagger.log", "w", encoding="utf-8")
    journal = []
    pending = []                     # (the batch being seeded: in the journal even if the run stops)
    results = []
    print(f"Run {run_id}: backups and log in {run_dir}")
    existed = existing_dirs(root)            # (folders there before the tagger: never removed)
    run_made = set()                         # (folders this run's earlier batches made)
    signals = _stop_like_ctrl_c()
    in_flight = None                         # (a batch the tagger was started on and not yet checked)
    try:
        for start in range(0, len(albums), args.batch):
            batch = []
            import tagio
            for a in albums[start:start + args.batch]:        # 0: the match is still the matcher's pick
                frag = [Path(p).name for p in a["files"] if tagio.fragmented(p)]
                why = (f"{frag[0]} is a fragmented MP4: the tagger would break its audio" if frag else
                       recheck(args.matches_db, a, args.allow_old_matches))
                if why:
                    print(f"    skipped {Path(a['path']).parent.name} | {Path(a['path']).name}: {why}")
                    results.append({"album": a["path"], "state": "skipped", "saved": 0, "files": len(a["files"]),
                                    "discogs": 0, "now_in": "", "release": a["mbid"], "not_saved": why})
                else:
                    batch.append(a)
            if not batch:
                continue
            for a in batch:                                   # 1+2: back up, then seed
                a["extras"] = extra_files(a["path"])
                a["disc"] = disc_number(a["path"], a["files"])
                a["records"] = []
                for p in a["files"]:
                    before = read_ids(p)                     # a retag: the old release's IDs are there
                    rec = {"path": p, "key": audio_key(p), "backup": backup_tags(p, blob_dir),
                           "old_trackid": before["trackid"], "old_discogs": before["discogs"],
                           "old_albumid": before["albumid"], "video": is_video(p)}
                    a["records"].append(rec)
                # every album prepared so far in this batch (later ones have no backup yet)
                pending = [{"album": x["path"], "mbid": x["mbid"], "files": x["records"], "extras": x["extras"]}
                           for x in batch if "records" in x]
                write_journal(run_dir, journal + pending)
                for p in a["files"]:
                    write_albumid(Path(p), a["mbid"])
                    # until Picard saves it, the album is not done: a seed left behind by a run
                    # that stopped is never taken for "already tagged with this release"
                    write_tagmender(Path(p), "Seeded", "release ID written; the tagger has not saved yet")
            journal += [{"album": a["path"], "mbid": a["mbid"], "files": a["records"], "extras": a["extras"],
                         "moves": a.setdefault("moves", []), "kept_dirs": a.setdefault("kept_dirs", [])}
                        for a in batch]
            pending = []
            started = time.time()
            n0 = start + 1
            print(f"[{n0}-{start + len(batch)}/{len(albums)}] The tagger: tagging {len(batch)} album(s)…", flush=True)
            before_pids = {pid for pid, *_ in tagger_processes()}
            launched = time.time()
            in_flight = (batch, started)
            rc = run_picard(args.picard, [a["path"] for a in batch], args.settle,
                            timeout=600 + 120 * len(batch), log=log)
            # (the tagger may still be saving after its command returned - a window it left
            # open, a slow share: verified before it is done, files it saves later look unsaved
            # and are left without the mark, Nile - What Should Not Be Unearthed, 8 of 10)
            still = wait_for_tagger_exit(log=log, since=launched, exclude=before_pids)
            cands = changed_dirs(root, started) + [Path(a["path"]) for a in batch]
            if still:
                # nothing is written to a file the tagger may be writing: no mark, no seed taken
                # out, nothing put back. Only where the files are now is looked up, for --undo.
                print(f"    the tagger is still running ({still}). Nothing was written to the {len(batch)} "
                      f"album(s) of this batch while it may still save them: they are left as the tagger "
                      f"leaves them. When it has closed, check them and run again, or put them back with "
                      f"--undo {run_id}. No further batch is started.")
                for a in batch:
                    try:
                        found = verify(a, a["records"], cands).get("new_paths", {})
                    except Exception:
                        found = {}
                    for rec in a["records"]:
                        rec["new_path"] = found.get(rec["path"], "")
                    dirs = sorted({str(Path(p).parent) for p in found.values()})
                    results.append({"album": a["path"], "state": STILL_RUNNING, "saved": "",
                                    "files": len(a["records"]), "discogs": "", "now_in": "; ".join(dirs),
                                    "release": a["mbid"], "not_saved": ""})
                    print(f"    {STILL_RUNNING:24s} {Path(a['path']).parent.name} | {Path(a['path']).name}")
                write_journal(run_dir, journal)
                in_flight = None
                if rc not in (0, None):
                    print(f"    (the tagger exited with {rc}; see {run_dir / 'tagger.log'})")
                break
            full = None
            for a in batch:                                   # 4: verify
                v = verify(a, a["records"], cands)
                if any("new place not found" in x for x in v["not_saved"]):
                    full = full or changed_dirs(root, started, full=True)
                    v = verify(a, a["records"], full + [Path(a["path"])])
                a["verify"] = v
                recs = a["records"]
                # (the album's audio tracks decide: a music video the tagger leaves unsaved never
                # makes it "saved in part"; an audio track it leaves unsaved does)
                tracks = [r for r in recs if not r.get("video")] or recs
                n = len(tracks)
                if "saved_paths" in v:
                    sp, dp = set(v["saved_paths"]), set(v["discogs_paths"])
                    saved = sum(r["path"] in sp for r in tracks)
                    discogs = sum(r["path"] in dp for r in tracks)
                else:
                    saved, discogs = v["saved"], v["with_discogs"]
                state = ("tagged" if saved == n and discogs == n else
                         "tagged, no Discogs data" if saved == n else
                         "partly saved" if saved else "not saved")
                for rec in recs:
                    rec["new_path"] = v["new_paths"].get(rec["path"], "")
                    if not rec["new_path"] and Path(rec["path"]).exists():
                        try:                          # not saved: take the seeded release ID out again
                            restore_tags(Path(rec["path"]), rec["backup"], blob_dir)
                        except Exception as exc:
                            print(f"    could not take the release ID out of {rec['path']}: {exc}")
                moved = any(r["new_path"] != r["path"] and (r["new_path"] or not Path(r["path"]).exists())
                            for r in recs)
                # all or nothing: an album the tagger did not save whole is put back whole - never
                # left with some tracks tagged and moved and the rest behind in the old folder
                # (Boris & Sunn O))) - Altar, 7 of 8 moved; Sepultura - Beneath the Remains), nor
                # moved unsaved into a folder of the tagger's
                a["_check"] = {"saved": saved, "discogs": discogs, "n": n, "state": state,
                               "failed": not state.startswith("tagged") and bool(saved or moved),
                               "new_dirs": sorted({str(Path(p).parent) for p in v["new_paths"].values()}),
                               "reason": f"The tagger saved {saved} of {n} files", "with": []}
                # (the folders the files went to that were there before: never removed, by --undo either)
                a["kept_dirs"][:] = sorted({str(d) for p in v["new_paths"].values() for d in Path(p).parents
                                            if str(d) in existed})
            write_journal(run_dir, journal)    # (where every file went, before anything moves)
            in_flight = None
            # ...and so is every part of one set: the discs of one release filed in sibling folders
            # are put back together when one of them is not saved whole (never one disc tagged,
            # the other not: Boris & Sunn O))) - Altar). Only discs with different numbers are
            # parts of a set; two copies of a disc or release (a FLAC and an MP3 rip) never are.
            groups = {}
            for a in batch:
                key = a.get("mbid") or (f"discogs:{a['discogs']}" if a.get("discogs") else "")
                if key and a.get("disc") is not None:
                    groups.setdefault(key, []).append(a)
            for members in groups.values():
                discs = [m["disc"] for m in members]
                members = [m for m in members if discs.count(m["disc"]) == 1]
                if len(members) < 2:
                    continue
                bad = [m for m in members if m["_check"]["state"] not in DONE]
                for m in members if bad else ():  # (a part saved whole: the others not; one not: the rest)
                    m_bad = any(x is m for x in bad)
                    m["_check"]["with"] = [Path(x["path"]).name for x in (members if m_bad else bad) if x is not m]
            # (a part neither saved nor moved needs no putting back: it is only marked)
            put_back = [a for a in batch if a["_check"]["failed"]
                        or (a["_check"]["with"] and a["_check"]["state"] in DONE)]
            for a in batch:
                c = a["_check"]
                if c["with"] and not c["failed"] and c["state"] not in DONE:
                    c["reason"] += f"; the other parts of its set were put back too: {', '.join(c['with'])}"
            made = set()
            for a in put_back:
                c, recs = a["_check"], a["records"]
                made |= {str(Path(r["new_path"]).parent) for r in recs
                         if r["new_path"] and r["new_path"] != r["path"]}
                _r, problems, where = undo_album({"album": a["path"], "files": recs, "extras": a["extras"],
                                                  "moves": a["moves"], "kept_dirs": a["kept_dirs"]}, blob_dir)
                for rec in recs:                      # where each file is now, whatever went wrong
                    rec["new_path"] = where.get(rec["path"], rec["new_path"])
                    if not rec["new_path"]:
                        rec["missing"] = True
                if not problems:
                    a["moves"].clear()
                write_journal(run_dir, journal)    # (stopped here: --undo still finds them)
                c["new_dirs"] = sorted({str(Path(r["new_path"]).parent) for r in recs
                                        if r["new_path"] and r["new_path"] != r["path"]})
                missing = [r["path"] for r in recs if r.get("missing")]
                if not c["failed"]:                   # saved whole, put back with the part that was not
                    c["state"] = "saved, put back with its set"
                    c["reason"] += (f"; put back with {', '.join(c['with'])}, a part of the same release "
                                    "the tagger did not save whole")
                else:
                    c["state"] = ("partly saved" if c["saved"] else "not saved") + ", put back"
                    if c["with"]:
                        c["reason"] += f"; put back together with {', '.join(c['with'])} (the same release)"
                if problems:
                    c["state"] += " in part"
                    c["reason"] += ("; the album could be put back only in part: "
                                    + "; ".join(([f"{len(missing)} file(s) missing"] if missing else [])
                                                + [f"files left in {d}" for d in c["new_dirs"]]))
                elif c["failed"]:
                    c["reason"] += "; the album was put back as it was"
            if put_back:
                keep = {r["new_path"] for x in batch for r in x["records"] if r.get("new_path")}
                for p in clear_tagger_leftovers(made, [x["path"] for x in batch], keep, existed | run_made, root):
                    print(f"      left by the tagger, not removed: {p}")
                write_journal(run_dir, journal)    # (a later --undo finds every file)
            for a in batch:
                c, recs, v = a.pop("_check"), a["records"], a["verify"]
                state, saved, n, new_dirs = c["state"], c["saved"], c["n"], c["new_dirs"]
                done = state in DONE
                results.append({"album": a["path"], "state": state, "saved": saved, "files": n,
                                "discogs": c["discogs"], "now_in": "; ".join(new_dirs),
                                "release": a["mbid"], "not_saved": "; ".join(v["not_saved"][:3])})
                print(f"    {state:24s} {Path(a['path']).parent.name} | {Path(a['path']).name}"
                      + (f"  ->  {new_dirs[0]}" if new_dirs and new_dirs[0] != a["path"] else ""))
                if c["with"]:
                    print(f"      one set with: {', '.join(c['with'])} (put back together)")
                if "put back in part" in state:
                    for d in new_dirs:
                        print(f"      could not be put back, left in: {d}")
                    for p in (r["path"] for r in recs if r.get("missing")):
                        print(f"      missing (not found after the tagger): {p}")
                if saved or state.endswith("in part") or c["with"]:   # 4b: TAGMENDER=Resolved / Unresolved
                    status = "Resolved" if done else "Unresolved"
                    for rec in recs:                  # (every file, where it actually is)
                        at = rec["new_path"] or (rec["path"] if not done and Path(rec["path"]).exists() else "")
                        if at:
                            try:
                                write_tagmender(at, status, "" if done else c["reason"])
                            except Exception as exc:
                                print(f"    could not write {TAGMENDER_TAG} to {at}: {exc}")
                if done and new_dirs and not args.no_tidy:   # 5: artwork
                    album_dir = os.path.commonpath(new_dirs) if len(new_dirs) > 1 else new_dirs[0]
                    try:
                        # extend, not replace: the journal holds this same list
                        a["moves"].extend(tidy_artwork(a["path"], album_dir, a["extras"], args.covers_name))
                    except OSError as exc:
                        print(f"    could not tidy the artwork: {exc}")
                    if a["moves"]:
                        print(f"    moved {len(a['moves'])} scan/artwork file(s) into covers/")
            run_made |= {str(d) for d in changed_dirs(root, started) if d.is_dir() and str(d) not in existed}
            write_journal(run_dir, journal)
            if rc not in (0, None):
                print(f"    (the tagger exited with {rc}; see {run_dir / 'tagger.log'})")
    except KeyboardInterrupt:
        print("\nInterrupted. Albums not saved still carry only the seeded release ID; "
              f"--undo {run_id} restores everything in this run.")
    finally:
        if in_flight is not None:
            locate_interrupted(in_flight[0], root, in_flight[1], run_dir, journal, run_id)
        write_journal(run_dir, journal + pending)
        _restore_signals(signals)
        with open(run_dir / "results.csv", "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=["album", "state", "saved", "files", "discogs", "now_in",
                                               "release", "not_saved"])
            w.writeheader()
            w.writerows(results)
        log.close()
    counts = {}
    for r in results:
        counts[r["state"]] = counts.get(r["state"], 0) + 1
    print(f"\n{counts}\nResults: {run_dir / 'results.csv'}\nUndo the whole run: "
          f"python3 tag_albums.py --undo {run_id}")
    if counts:
        _navidrome()
    return 0


def unresolved_albums(matches_db, only=None):
    """Albums the matcher left for a person: (folder, reason)."""
    db = sqlite3.connect(Path(matches_db).expanduser())
    out = []
    for path, blob in db.execute("SELECT path, result FROM results ORDER BY path"):
        o = json.loads(blob)
        if o.get("status") not in ("review", "nomatch"):
            continue
        if only and only.casefold() not in path.casefold():
            continue
        if not Path(path).is_dir():
            continue
        why = "; ".join(o.get("why") or []) or o.get("status")
        out.append((path, f"{o.get('status')}: {why}"[:250]))
    return out


def mark_unresolved(args, out_dir):
    import tagio
    albums = unresolved_albums(args.matches_db, args.only)
    todo = []
    for path, reason in albums:
        files = [p for p in audio_files(Path(path)) if not tagio.fragmented(p)]   # (never written to)
        if not files or all(read_ids(p)["trackid"] for p in files):
            continue                                     # tagged by hand since
        if all(read_tagmender(p) == ("Unresolved", reasons.encode(reason)) for p in files):
            continue
        if any(read_tagmender(p)[0] in ("Resolved", "Partial") for p in files):
            continue                                     # tagged since (fully, or album only: Partial)
        todo.append((path, reason, files))
    print(f"{len(todo)} album(s) to mark {TAGMENDER_TAG}=Unresolved")
    if not args.apply:
        for path, reason, _f in todo[:20]:
            print(f"  {path}\n      {reason}")
        print("Dry run, nothing changed. Run again with --apply.")
        return 0
    run_id = time.strftime("%Y%m%d-%H%M%S")
    run_dir, n = out_dir / f"run-{run_id}", 1
    while run_dir.exists():
        n += 1
        run_dir = out_dir / f"run-{run_id}-{n}"
    run_id = run_dir.name[len("run-"):]
    blob_dir = run_dir / "blobs"
    blob_dir.mkdir(parents=True)
    journal, done = [], 0
    signals = _stop_like_ctrl_c()
    try:
        for path, reason, files in todo:
            recs = []
            for p in files:
                rec = {"path": str(p), "new_path": str(p), "key": "", "backup": backup_tags(p, blob_dir)}
                recs.append(rec)
            journal.append({"album": path, "mbid": None, "files": recs, "extras": [], "moves": []})
            write_journal(run_dir, journal)
            for p in files:
                write_tagmender(p, "Unresolved", reason)
            done += 1
    finally:
        write_journal(run_dir, journal)
        _restore_signals(signals)
    print(f"Marked {done} album(s). Undo: python3 tag_albums.py --undo {run_id}")
    return 0


def _navidrome():
    """Ask Navidrome to rescan when ~/.config/tagmender/navidrome.json is set up."""
    try:
        import enrich
        if enrich.navidrome_rescan():
            print("Navidrome rescan requested")
    except Exception:
        pass


def extra_files(folder):
    """Non-audio files in an album folder (scans, logs, cue sheets), relative paths."""
    out = []
    for dirpath, _dirs, names in os.walk(folder):
        for n in names:
            p = Path(dirpath) / n
            if p.suffix.lower() not in AUDIO_EXT:
                out.append(str(p.relative_to(folder)))
    return sorted(out)


def outside_move_target(albums, ini):
    """(target, [album paths]) for albums Picard would move out of their own library:
    its 'move files' option is on and they are not under its move target. None if all is well."""
    import configparser
    cp = configparser.RawConfigParser(strict=False, interpolation=None)
    try:
        cp.read(Path(ini).expanduser(), encoding="utf-8")
        moving = cp.get("setting", "move_files", fallback="false").strip().lower() == "true"
        target = cp.get("setting", "move_files_to", fallback="")
    except Exception:
        return None
    if not moving or not target:
        return None
    base = Path(target).expanduser().resolve()
    bad = [a["path"] for a in albums if not Path(a["path"]).resolve().is_relative_to(base)]
    return (str(base), bad) if bad else None


def picard_move_target(ini):
    import configparser
    cp = configparser.RawConfigParser(strict=False, interpolation=None)
    try:
        cp.read(Path(ini).expanduser(), encoding="utf-8")
        return cp.get("setting", "move_files_to", fallback=None)
    except Exception:
        return None


def tidy_run(out_dir, run_id, root=None, covers_name="covers"):
    """The artwork step for an earlier run (one made before it existed, or interrupted before
    it recorded where Picard moved the files). Undo still works."""
    run_dir = out_dir / f"run-{run_id}"
    journal = json.loads((run_dir / "journal.json").read_text())
    started = time.mktime(time.strptime(run_id[:15], "%Y%m%d-%H%M%S"))
    cands = None
    total = 0
    for album in journal:
        if root and any(not r.get("new_path") for r in album["files"]):
            # interrupted before checking: find where Picard put the files now
            if cands is None:
                cands = changed_dirs(root, started)
            v = verify({"mbid": album["mbid"]}, album["files"], cands + [Path(album["album"])])
            if any("new place not found" in x for x in v["not_saved"]):
                v = verify({"mbid": album["mbid"]}, album["files"],
                           changed_dirs(root, started, full=True) + [Path(album["album"])])
            for r in album["files"]:
                r["new_path"] = v["new_paths"].get(r["path"], "")
        new_dirs = sorted({str(Path(r["new_path"]).parent) for r in album["files"] if r.get("new_path")})
        if not new_dirs or any(not r.get("new_path") for r in album["files"]):
            print(f"  skipped (not fully saved): {album['album']}")
            continue
        album_dir = os.path.commonpath(new_dirs) if len(new_dirs) > 1 else new_dirs[0]
        moves = tidy_artwork(album["album"], album_dir, album.get("extras", []), covers_name)
        album.setdefault("moves", []).extend(moves)
        total += len(moves)
        print(f"  {len(moves):3d} file(s) into covers/  {album_dir}")
    write_journal(run_dir, journal)
    print(f"Moved {total} scan/artwork file(s). --undo {run_id} still restores everything.")
    return 0


def write_journal(run_dir, journal):
    """journal.json written whole or not at all: into journal.json.tmp, synced, then put in
    its place - a run killed while writing it (a closed terminal, a power cut) leaves the
    one written before, never a half one that --undo cannot read."""
    path = Path(run_dir) / "journal.json"
    tmp = path.with_name("journal.json.tmp")
    with open(tmp, "w") as fh:
        json.dump(journal, fh)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def locate_interrupted(batch, root, started, run_dir, journal, run_id, budget=120):
    """A run stopped (Ctrl-C, a closed terminal, a kill) after the tagger was started on BATCH
    and before its albums were checked: the tagger may have moved files the journal does not
    know of yet. Where each file is now is looked up, read-only - nothing is written to the
    files, no mark, nothing put back - and the journal is written after each album, so that
    --undo finds them. A file found nowhere is new_path "" and missing. Bounded: the quick
    look at the folders that changed, never the whole library, and no album is started after
    BUDGET seconds; a second Ctrl-C stops the lookup with the journal as far as it got."""
    print(f"    The batch was interrupted after the tagger was started on it: looking up where its "
          f"{len(batch)} album(s) are now (nothing is written to them).", flush=True)
    deadline = time.time() + budget
    looked = 0
    try:
        cands = changed_dirs(root, started)
        for a in batch:
            if time.time() > deadline:
                print("    (the lookup took too long: the rest of the batch is left as the journal had it)")
                break
            cands_a = cands + [Path(a["path"])]
            try:
                found = verify(a, a["records"], cands_a).get("new_paths", {})
            except Exception:
                found = {}
            for rec in a["records"]:
                rec.pop("missing", None)
                at = found.get(rec["path"], "")
                if not at and Path(rec["path"]).exists():
                    at = rec["path"]                    # (still where it was, its tags as they are)
                rec["new_path"] = at
                if not at:
                    rec["missing"] = True
            write_journal(run_dir, journal)
            looked += 1
    except KeyboardInterrupt:
        print(f"    (the lookup was stopped after {looked} of {len(batch)} album(s))")
    print(f"    The batch was interrupted: its albums are left as the tagger left them. "
          f"--undo {run_id} puts them back.")


def _stop_like_ctrl_c():
    """A closed terminal (SIGHUP) or a plain kill (SIGTERM) stops a run the way Ctrl-C does,
    so that it writes its journal before it ends. The handlers it replaced, to put back."""
    import signal

    def stop(_signum, _frame):
        raise KeyboardInterrupt
    old = {}
    for name in ("SIGHUP", "SIGTERM"):
        sig = getattr(signal, name, None)
        if sig is not None:
            try:
                old[sig] = signal.signal(sig, stop)
            except (ValueError, OSError):          # (not the main thread)
                pass
    return old


def _restore_signals(old):
    import signal
    for sig, handler in old.items():
        try:
            signal.signal(sig, handler)
        except (ValueError, OSError):
            pass


def undo(out_dir, run_id, force=False):
    run_dir = out_dir / f"run-{run_id}"
    journal = json.loads((run_dir / "journal.json").read_text())
    blob_dir = run_dir / "blobs"
    restored = failed = 0
    for album in journal:
        r, f, _where = undo_album(album, blob_dir, force)
        restored, failed = restored + r, failed + f
    print(f"Restored {restored} file(s); {failed} problem(s).")
    return 0 if not failed else 1


def undo_album(album, blob_dir, force=False):
    """Put one album of a run back as it was: its files' tags, names and folders, the scans
    and logs the tagger moved along. (restored, problems, where): where maps each file's
    original path to where it is now - that path when it was put back, the path it was left
    at when it could not be, '' when it is nowhere to be found."""
    restored = failed = 0
    where = {}
    for src, dest in reversed(album.get("moves", [])):     # the artwork tidy, backwards
        if Path(dest).exists() and not Path(src).exists():
            try:
                Path(src).parent.mkdir(parents=True, exist_ok=True)
                shutil.move(dest, src)
            except OSError as exc:
                failed += 1
                print(f"  could not move {dest} back: {exc}")
    kept = album.get("kept_dirs", [])
    for d in {str(Path(dest).parent) for _s, dest in album.get("moves", [])}:
        _remove_empty(Path(d), keep=kept)
    new_dirs = {str(Path(r["new_path"]).parent) for r in album["files"]
                if r.get("new_path") and r["new_path"] != r["path"]}
    for rec in album["files"]:
        cur = Path(rec.get("new_path") or rec["path"])
        old = Path(rec["path"])
        where[rec["path"]] = str(cur)
        if not cur.exists():
            where[rec["path"]] = ""
            if rec.get("missing"):          # (the run said so when it lost it: not a problem of this undo)
                print(f"  still missing (reported by the run): {old}")
                continue
            failed += 1
            print(f"  not found: {cur}")
            continue
        if rec.get("not_written"):          # (a run that could not write this track: nothing to put back)
            continue
        try:
            import executor
            if rec.get("after_sig") and not force and not executor._sig_is(cur, rec["after_sig"]) and \
                    not same_as_backup(cur, rec["backup"]):
                failed += 1
                print(f"  {cur}: its tags were edited after the run; left as they are (--force puts them back)")
                continue
            # (through a staging copy: a crash never leaves a half-written track; a track that
            # is a link - the run wrote through it - is put back in the file it points to)
            real = Path(os.path.realpath(cur)) if cur.is_symlink() else cur
            executor.staged_write(real, lambda stage, r=rec: restore_tags(stage, r["backup"], blob_dir))
            if cur != old:
                old.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(cur), str(old))
            where[rec["path"]] = str(old)
            restored += 1
        except Exception as exc:
            failed += 1
            print(f"  could not restore {cur}: {exc}")
            if not cur.exists():            # (moved, then the move failed part way)
                where[rec["path"]] = str(old) if old.exists() else ""
    # scans, logs and cue sheets Picard moved along with the music
    for rel in album.get("extras", []):
        old = Path(album["album"]) / rel
        if old.exists():
            continue
        for d in new_dirs:
            for cand in (Path(d) / rel, Path(d) / Path(rel).name):
                if cand.exists():
                    try:
                        old.parent.mkdir(parents=True, exist_ok=True)
                        shutil.move(str(cand), str(old))
                    except OSError as exc:
                        failed += 1
                        print(f"  could not move {cand} back: {exc}")
                    break
            else:
                continue
            break
    for d in new_dirs:                               # remove the folders Picard made, if now empty
        _remove_empty(Path(d), keep=kept)
    return restored, failed, where


def _remove_empty(folder, keep_parent=False, keep=()):
    """Remove FOLDER's empty folders, FOLDER itself, then its parent (the artist folder) if
    now empty. KEEP: folders never removed, empty or not (those there before a run)."""
    keep = {str(Path(k)) for k in keep}
    for dirpath, _dirs, _names in sorted(os.walk(folder), key=lambda x: -len(x[0])):
        if str(Path(dirpath)) in keep:
            continue
        try:
            os.rmdir(dirpath)
        except OSError:
            pass
    if keep_parent or str(Path(folder).parent) in keep:
        return
    try:
        os.rmdir(Path(folder).parent)                    # the artist folder, if now empty
    except OSError:
        pass


if __name__ == "__main__":
    sys.exit(main())
