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
FILE_TIMEOUT = 600.0                  # s: a file decoded longer is left unmeasured
MAX_CACHED = 50000
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


def half_up(x: float) -> int:
    """x rounded as a meter shows it: halves up (12.5 is 13), never to the even number."""
    return int(math.floor(x + 0.5))


def _decode_args(info, path) -> tuple:
    """(rate, channels, ffmpeg's args) to decode PATH to 32-bit float PCM at a rate the meter
    reads: the file's own rate - a DSD file's (2.8 MHz and up) at 176.4 kHz, as its PCM is read."""
    rate, ch = int(getattr(info, "sample_rate", 0) or 0), int(getattr(info, "channels", 0) or 0)
    if rate > 384000 or Path(str(path)).suffix.lower() in (".dsf", ".dff"):
        rate = 176400
    return rate, ch, ["-ac", str(ch), "-ar", str(rate)]


def measure_file(path, cancel=None, timeout=None) -> dict | None:
    """{"dr": the track's DR (float), "clipped": samples, "peak": max |x|} or None (unreadable,
    silent, no ffmpeg, too slow, or cancelled)."""
    import shutil
    import time
    import numpy as np
    if not shutil.which("ffmpeg"):
        return None
    try:
        import mutagen
        rate, ch, args = _decode_args(mutagen.File(str(path)).info, path)
    except Exception:
        return None
    if not rate or not ch:
        return None
    deadline = time.monotonic() + (timeout or FILE_TIMEOUT)
    block = int(rate * BLOCK_S) * ch
    proc = subprocess.Popen(["ffmpeg", "-v", "error", "-nostdin", "-i", str(path), "-t", str(MAX_SECONDS),
                             "-map", "0:a:0", *args, "-f", "f32le", "-acodec", "pcm_f32le", "-"],
                            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    import threading
    # (a read that blocks - a stalled share, a stuck decoder - is ended by killing ffmpeg at the
    # deadline: the read then returns, never waits forever)
    watchdog = threading.Timer(max(0.0, deadline - time.monotonic()), proc.kill)
    watchdog.daemon = True
    watchdog.start()
    rms = [[] for _ in range(ch)]
    peaks = [[] for _ in range(ch)]
    tail = [(False, False)] * ch
    clipped, top = 0, 0.0
    try:
        while True:
            if (cancel is not None and cancel()) or time.monotonic() > deadline:
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
                n, tail[c] = _run_count(s, tail[c])
                clipped += n
    finally:
        watchdog.cancel()
        proc.stdout.close()
        proc.kill()
        proc.wait()
    if time.monotonic() > deadline:
        return None
    drs = [d for d in (dr_of_blocks(rms[c], peaks[c]) for c in range(ch)) if d is not None]
    if not drs:
        return None
    return {"dr": sum(drs) / len(drs), "clipped": clipped, "peak": top}


def _run_count(x, state=(False, False)) -> tuple:
    """(samples of one channel at full scale in runs of two or more, the state for the next
    block). state: (the block before ended at full scale, that last sample was counted) - a run
    across two blocks is one run: the sample left alone at the end of the block before is
    counted once its partner shows here."""
    import numpy as np
    before, counted = state
    m = np.abs(x) >= CLIP
    if not len(m):
        return 0, state
    prev = np.concatenate(([before], m[:-1]))
    nxt = np.concatenate((m[1:], [False]))
    hit = m & (prev | nxt)
    n = int(hit.sum())
    if before and m[0] and not counted:
        n += 1
    return n, (bool(m[-1]), bool(hit[-1]))


# ---------------------------------------------------------------------------- the cache
def _cache_path() -> Path:
    import exclusions
    return Path(exclusions.STATE_DIR) / "dynamics.json"


def _stamp(path) -> str:
    st = os.stat(path)
    return f"{st.st_size}:{st.st_mtime_ns}:{st.st_ino}"


def _load() -> dict:
    try:
        got = json.loads(_cache_path().read_text(encoding="utf-8"))
        return got if isinstance(got, dict) else {}
    except (OSError, ValueError):
        return {}


def _save(cache: dict):
    """The cache written - only when it changed (cache["~changed"]), files gone pruned, at most
    MAX_CACHED files kept."""
    if not cache.pop("~changed", False):
        return
    keep = {k: v for k, v in cache.items() if not k.startswith("~") and os.path.exists(k)}
    if len(keep) > MAX_CACHED:
        keep = dict(sorted(keep.items(), key=lambda kv: -float((kv[1] or {}).get("t") or 0))[:MAX_CACHED])
    p = _cache_path()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_name(f".{p.name}.tmp")
        tmp.write_text(json.dumps(keep), encoding="utf-8")
        os.replace(tmp, p)
    except OSError:
        pass


def _analysis():
    """levels.Cache - the measurements kept by the audio itself (its digest): the same audio
    renamed, moved or retagged is not decoded again."""
    try:
        import exclusions
        import levels
        return levels.Cache(Path(exclusions.STATE_DIR) / "analysis.db")
    except Exception:
        return None


def measure_copy(files, cancel=None, cache=None) -> dict | None:
    """{"dr": int, "clipped": int, "tracks": n} of one copy (its audio files), each file's audio
    measured once: by its path, size, time (the cache) and, a file changed or moved, its audio
    itself (levels.Cache, by digest: tags written since measure nothing again). None when nothing
    was measured."""
    import time
    own = cache is None
    cache = _load() if own else cache
    db = None
    drs, clipped = [], 0
    try:
        for f in [f for f in files if Path(str(f)).suffix.lower() in AUDIO][:MAX_TRACKS]:
            if cancel is not None and cancel():
                return None
            key = os.path.realpath(str(f))
            try:
                stamp = _stamp(key)
            except OSError:
                continue
            hit = cache.get(key)
            if not (isinstance(hit, dict) and hit.get("fp") == stamp):
                if db is None:
                    db = _analysis() or False
                import levels
                akey = levels._audio_key(key)
                got = db.cached("dr1", akey)[1] if db else None
                if got is None:
                    got = measure_file(key, cancel)
                    if got is None:
                        continue
                    if db:
                        db.put("dr1", akey, got)
                hit = dict(got, fp=stamp, t=time.time())
                with _LOCK:
                    cache[key] = hit
                    cache["~changed"] = True
            drs.append(half_up(hit["dr"]))            # (each track's DR as the meter shows it: whole)
            clipped += int(hit.get("clipped") or 0)
    finally:
        if db:
            db.close()
        if own:
            _save(cache)
    if not drs:
        return None
    return {"dr": half_up(sum(drs) / len(drs)), "clipped": clipped, "tracks": len(drs)}


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
    or TagMender filed (its job history, when filed; "cached": as already read): copy = {"folder",
    "files", "album": the plan's album index or None (a copy filed before)}. Copies only of a proven release (never an
    album known: its pressing is not)."""
    by = {}
    planned = set()
    for i, a in enumerate((plan or {}).get("albums") or ()):
        if not _proven(a):
            continue
        files = [fe["src"] for fe in a.get("files") or () if fe.get("src")]
        for rid in _ids_of(a):
            by.setdefault(rid, []).append({"folder": a.get("source_dir") or "", "files": files, "album": i,
                                           "discs": _discs_of(a.get("files") or ())})
        # (paths as written, no file system asked: the plan's are absolute already)
        planned |= {os.path.normpath(f) for f in files} | {os.path.normpath(fe["dst"])
                                                           for fe in a.get("files") or () if fe.get("dst")}
    if filed and by:
        try:
            import match_albums as ma
            # ("cached": the job history as the matcher last read it in this process - the app's
            # look-ups do - never read anew here: rebuilding it held the UI thread's turn for
            # hundreds of ms)
            owned = ma._own_index() if filed != "cached" else dict(ma._OWN_INDEX[1] or {})
        except Exception:
            owned = {}
        found = {}
        wanted = set(by)
        for path, ids in owned.items():
            hit = {str(x).casefold() for part in (ids or ()) for x in (part or ())} & wanted
            if not hit or os.path.normpath(path) in planned or not os.path.exists(path):
                continue
            for rid in hit:
                found.setdefault((rid, str(_album_folder(Path(path)))), []).append(path)
        for (rid, folder), files in sorted(found.items()):
            if folder not in {c["folder"] for c in by[rid]}:
                by[rid].append({"folder": folder, "files": sorted(files), "album": None,
                                "discs": _discs_of([{"src": f} for f in sorted(files)[:3]])})
    out = []
    for rid, cs in by.items():
        # (copies hold the same disc: disc 1 in one folder and disc 2 in another are two parts of
        # one set, never two copies)
        cs = [c for c in cs if any(c is not o and c["discs"] & o["discs"] for o in cs)]
        cs = _no_transcodes(cs)
        if len(cs) >= 2:
            out.append((rid, cs[:MAX_COPIES]))
    return out


def _lengths(files) -> tuple:
    """The first tracks' lengths, to the millisecond."""
    import mutagen
    out = []
    for f in sorted(files)[:4]:
        try:
            out.append(round(float(mutagen.File(str(f)).info.length), 3))
        except Exception:
            return ()
    return tuple(out)


def _no_transcodes(cs) -> list:
    """VIN-6: copies whose every length is another's to the millisecond are one rip (a transcode
    to a lower resolution, or the same files twice), never two copies to compare: the first kept."""
    out, seen = [], set()
    for c in cs:
        key = _lengths(c["files"])
        if key and key in seen:
            continue
        seen.add(key)
        out.append(c)
    return out


def _discs_of(entries) -> set:
    """The disc numbers a planned album's files take (as written, or as the files have them)."""
    out = set()
    for fe in entries:
        got = (fe.get("changes") or {}).get("discnumber") or fe.get("discnumber")
        if not got:
            for r in fe.get("diff") or ():
                if r.get("field") == "discnumber" and r.get("after"):
                    got = r["after"]
        if not got and fe.get("src"):
            try:
                import tagio
                got = tagio.read(fe["src"]).get("discnumber")
            except Exception:
                got = None
        v = str((got if isinstance(got, list) else [got or "1"])[0] or "1").split("/")[0].strip()
        out.add(v or "1")
    return out or {"1"}


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
