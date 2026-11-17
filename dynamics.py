"""dynamics.py - copies of one release side by side: dynamic range and clipping (VIN-6).

When a plan holds two or more copies of one release (the same release ID: planned albums, or a
planned album and copies TagMender filed before, from its job history), each copy is measured -
after the plan, off the matching path and off the UI thread (gui: a worker), never deciding
anything: TagMender never picks, deletes or demotes a copy. Shown beside each other ("2 copies
of this release: 24-96 PBTHAL DR 13, clipping none; 32-192 DR 12, 3 clipped samples").

The measure (documented, standard):
  DR - the "DR" of the Pleasurize Music Foundation's TT Dynamic Range Meter (the DR14 / foobar2000
       DR Meter value rippers post as foo_dr.txt). Per track and channel, the audio is cut in
       blocks of 3 seconds; a block's RMS is sqrt(2 * mean(x^2)) and its peak max |x|; the
       loudest 20% of blocks by RMS give the RMS (the root of their mean square), and the
       second-highest block peak the peak: DR = 20 * log10(peak / RMS). A track's DR is the mean
       of its channels', shown whole; the copy's (album, "official") DR the mean of its tracks'
       whole values, rounded - as the meter's report gives it (checked against a ripper's
       foo_dr.txt: tracks DR12, DR13 ... and peaks -1.56 dB, -0.23 dB alike).
  Clipping - samples at digital full scale (|x| >= 0.9999, within 0.001 dB of 0 dBFS: a 16-bit file's 32767 too) in
       runs of two or more consecutive samples of one channel: a single sample touching full
       scale is a peak, a run is a flattened (clipped) wave.

Decoded with the ffmpeg TagMender already needs, at the file's own rate and channels (float
PCM, never resampled: a peak would move). Cached by file (path, size, modification time) in
the state folder, so a copy is measured once. Bounded: 8 copies of one release, 60 tracks of a
copy, at most MAX_SECONDS of audio a file.
"""
from __future__ import annotations

import json
import math
import os
import re
import subprocess
import threading
from pathlib import Path

BLOCK_S = 3.0
TOP = 0.2
CLIP = 0.9999                       # (16-bit full scale is 32767/32768 = 0.99997)
MAX_COPIES = 8
MAX_TRACKS = 60
MAX_SECONDS = 3 * 3600
AUDIO = {".flac", ".wav", ".aif", ".aiff", ".ape", ".wv", ".m4a", ".alac", ".tak", ".mp3", ".ogg", ".opus", ".dsf",
         ".dff", ".wma", ".aac"}
_LOCK = threading.Lock()
_DISC = re.compile(r"^(?:disc|disk|cd|lp|side)\s*\d+\b", re.I)


# ---------------------------------------------------------------------------- the measure
def dr_of_blocks(rms_blocks, peak_blocks):
    """The TT DR of one channel from its blocks' RMS and peak values (None when silent)."""
    if not rms_blocks:
        return None
    rms = sorted(rms_blocks, reverse=True)
    n = max(1, int(len(rms) * TOP))
    top = math.sqrt(sum(r * r for r in rms[:n]) / n)
    peaks = sorted(peak_blocks, reverse=True)
    peak = peaks[1] if len(peaks) > 1 else peaks[0]
    if top <= 0 or peak <= 0:
        return None
    return 20 * math.log10(peak / top)


def _clipped(x) -> int:
    """Samples of one channel at full scale in runs of two or more."""
    import numpy as np
    m = np.abs(x) >= CLIP
    if not m.any():
        return 0
    prev = np.concatenate(([False], m[:-1]))
    nxt = np.concatenate((m[1:], [False]))
    return int((m & (prev | nxt)).sum())


def measure_file(path, cancel=None) -> dict | None:
    """{"dr": the track's DR (float), "clipped": samples, "peak": max |x|} or None (unreadable,
    silent, or cancelled)."""
    import numpy as np
    try:
        import mutagen
        info = mutagen.File(str(path)).info
        rate, ch = int(getattr(info, "sample_rate", 0) or 0), int(getattr(info, "channels", 0) or 0)
    except Exception:
        return None
    if not rate or not ch:
        return None
    block = int(rate * BLOCK_S) * ch
    proc = subprocess.Popen(["ffmpeg", "-v", "error", "-nostdin", "-i", str(path), "-t", str(MAX_SECONDS),
                             "-map", "0:a:0", "-f", "f32le", "-acodec", "pcm_f32le", "-"],
                            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    rms = [[] for _ in range(ch)]
    peaks = [[] for _ in range(ch)]
    clipped, top = 0, 0.0
    try:
        while True:
            if cancel is not None and cancel():
                return None
            raw = proc.stdout.read(block * 4)
            if not raw:
                break
            x = np.frombuffer(raw[:len(raw) - len(raw) % (4 * ch)], dtype="<f4").reshape(-1, ch)
            if not len(x):
                break
            for c in range(ch):
                s = x[:, c].astype(np.float64)
                rms[c].append(math.sqrt(2 * float(np.mean(s * s))))
                p = float(np.max(np.abs(s)))
                peaks[c].append(p)
                top = max(top, p)
                clipped += _clipped(s)
    finally:
        proc.stdout.close()
        proc.kill()
        proc.wait()
    drs = [d for d in (dr_of_blocks(rms[c], peaks[c]) for c in range(ch)) if d is not None]
    if not drs:
        return None
    return {"dr": sum(drs) / len(drs), "clipped": clipped, "peak": top}


# ---------------------------------------------------------------------------- the cache
def _cache_path() -> Path:
    import exclusions
    return Path(exclusions.STATE_DIR) / "dynamics.json"


def _fp(path) -> str:
    st = os.stat(path)
    return f"{st.st_size}:{st.st_mtime_ns}"


def _load() -> dict:
    try:
        return json.loads(_cache_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save(cache: dict):
    p = _cache_path()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_name(f".{p.name}.tmp")
        tmp.write_text(json.dumps(cache), encoding="utf-8")
        os.replace(tmp, p)
    except OSError:
        pass


def measure_copy(files, cancel=None, cache=None) -> dict | None:
    """{"dr": int, "clipped": int, "tracks": n} of one copy (its audio files), each file measured
    once (the cache, by path, size and modification time); None when nothing was measured."""
    own = cache is None
    cache = _load() if own else cache
    drs, clipped, changed = [], 0, False
    for f in [f for f in files if Path(str(f)).suffix.lower() in AUDIO][:MAX_TRACKS]:
        if cancel is not None and cancel():
            return None
        key = os.path.realpath(str(f))
        try:
            fp = _fp(key)
        except OSError:
            continue
        hit = cache.get(key)
        if not (isinstance(hit, dict) and hit.get("fp") == fp):
            got = measure_file(key, cancel)
            if got is None:
                continue
            hit = dict(got, fp=fp)
            with _LOCK:
                cache[key] = hit
            changed = True
        drs.append(round(hit["dr"]))                # (each track's DR as the meter shows it: whole)
        clipped += int(hit.get("clipped") or 0)
    if own and changed:
        _save(cache)
    if not drs:
        return None
    return {"dr": int(round(sum(drs) / len(drs))), "clipped": clipped, "tracks": len(drs)}


# ---------------------------------------------------------------------------- the copies
def _album_folder(path: Path) -> Path:
    d = Path(path).parent
    return d.parent if _DISC.match(d.name) else d


def _ids_of(album) -> set:
    ident = album.get("identification") or {}
    rid = str(ident.get("release_id") or "").strip()
    return {rid.casefold()} if rid else set()


def _proven(album) -> bool:
    ident = album.get("identification") or {}
    return ident.get("status") == "identified" and (ident.get("tier") or "exact") not in ("partial", "album_only") \
        and album.get("status") not in ("excluded", "skipped", "failed")


def copies(plan, filed=True) -> list:
    """[(release ID, [copy, ...])] for each release two or more copies of which this plan holds
    or TagMender filed (its job history, when filed): copy = {"folder", "files", "album": the
    plan's album index or None (a copy filed before)}. Copies only of a proven release (never an
    album known: its pressing is not)."""
    by = {}
    planned = set()
    for i, a in enumerate((plan or {}).get("albums") or ()):
        if not _proven(a):
            continue
        files = [fe["src"] for fe in a.get("files") or () if fe.get("src")]
        for rid in _ids_of(a):
            by.setdefault(rid, []).append({"folder": a.get("source_dir") or "", "files": files, "album": i})
        planned |= {os.path.realpath(f) for f in files} | {os.path.realpath(fe.get("dst") or "")
                                                           for fe in a.get("files") or () if fe.get("dst")}
    if filed and by:
        try:
            import match_albums as ma
            owned = ma._own_index()
        except Exception:
            owned = {}
        found = {}
        for path, ids in owned.items():
            names = {str(x).casefold() for part in (ids or ()) for x in (part or ())}
            hit = names & set(by)
            if not hit or os.path.realpath(path) in planned or not os.path.exists(path):
                continue
            for rid in hit:
                found.setdefault((rid, str(_album_folder(Path(path)))), []).append(path)
        for (rid, folder), files in sorted(found.items()):
            if folder not in {c["folder"] for c in by[rid]}:
                by[rid].append({"folder": folder, "files": sorted(files), "album": None})
    return [(rid, cs[:MAX_COPIES]) for rid, cs in by.items() if len(cs) >= 2]


def label(folder, files) -> str:
    """What tells a copy apart in a line: its resolution and ripper ("24-96 PBTHAL"), else its
    folder's name."""
    try:
        import vinylrip
        res = vinylrip.resolution_name(vinylrip.resolution_of(files))
        who = vinylrip.ripper_in_name(Path(folder).name) or vinylrip.ripper_in_notes(vinylrip.notes_of(folder))
    except Exception:
        res, who = "", ""
    words = " ".join(x for x in (res, who) if x)
    return words or Path(str(folder)).name


def measure(plan, cancel=None, progress=None, filed=True) -> dict:
    """VIN-6: {album index: [{"folder", "label", "dr", "clipped", "this"}]} for each album of the
    plan that is one of two or more copies of a release (copies). Slow work (decoding audio): run
    it after the plan, off the UI thread; cancel() stops it."""
    out = {}
    groups = copies(plan, filed=filed)
    cache = _load()
    done, total = 0, sum(len(cs) for _rid, cs in groups)
    try:
        for _rid, cs in groups:
            rows = []
            for c in cs:
                if cancel is not None and cancel():
                    return out
                got = measure_copy(c["files"], cancel, cache) or {}
                rows.append({"folder": c["folder"], "label": label(c["folder"], c["files"]), "dr": got.get("dr"),
                             "clipped": got.get("clipped"), "album": c["album"]})
                done += 1
                if progress:
                    progress(done, total)
            for c in cs:
                if c["album"] is not None:
                    out[c["album"]] = [dict({k: v for k, v in r.items() if k != "album"},
                                            this=r["album"] == c["album"]) for r in rows]
    finally:
        _save(cache)
    return out


def annotate(plan, found: dict):
    """Put measure()'s rows on the plan's albums ("copies")."""
    albums = (plan or {}).get("albums") or []
    for i, rows in found.items():
        if 0 <= i < len(albums):
            albums[i]["copies"] = rows


def words(rows) -> str:
    """"2 copies of this release: 24-96 PBTHAL DR 13, clipping none; 32-192 DR 12, 3 clipped samples"
    (this album's copy first)."""
    if not rows or len(rows) < 2:
        return ""
    parts = []
    for r in sorted(rows, key=lambda r: not r.get("this")):
        dr = f"DR {r['dr']}" if r.get("dr") is not None else "DR not measured"
        clip = r.get("clipped")
        clip = ("clipping none" if clip == 0 else "1 clipped sample" if clip == 1 else
                f"{clip} clipped samples" if clip is not None else "")
        parts.append(" ".join(x for x in (r.get("label") or "", dr) if x) + (f", {clip}" if clip else "")
                     + (" (this one)" if r.get("this") else ""))
    return f"{len(rows)} copies of this release: " + "; ".join(parts)
