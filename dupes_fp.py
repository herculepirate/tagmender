#!/usr/bin/env python3
"""dupes_fp.py - the same recording in more than one place, across libraries.

dedupe_twins.py finds duplicate copies inside one album folder. This finds the
same recording anywhere: a track in ambient and in extreme, a single that is also
on the album, the same album ripped twice under different names.

Recordings are recognised by their MusicBrainz recording ID (written by Picard),
and with --fingerprint also by AcoustID for files without one (fpcalc + your
AcoustID key). Writes a CSV report with the copy to keep first (lossless over
lossy, then higher bit depth/sample rate, then the copy on a full album). Moves
nothing.

  python3 dupes_fp.py /media/unraidmedia2/music/ambient_music /media/unraidmedia2/music/extreme_music
"""
from __future__ import annotations

import argparse
import csv
import os
import sqlite3
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import match_core as mc  # noqa: E402
import match_albums as ma  # noqa: E402

import mutagen  # noqa: E402


def recording_id(path):
    f = mutagen.File(path)
    tags = getattr(f, "tags", None) or {}
    for k in ("MUSICBRAINZ_TRACKID", "musicbrainz_trackid", "UFID:http://musicbrainz.org", "TXXX:MUSICBRAINZ_TRACKID",
              "----:com.apple.iTunes:MusicBrainz Track Id"):
        try:
            v = tags.get(k)
        except Exception:
            v = None
        if v:
            v = v[0] if isinstance(v, list) else v
            v = getattr(v, "data", v)
            return v.decode() if isinstance(v, bytes) else str(v)
    return ""


def acoustid_recording(http, path, db):
    """The AcoustID recording (MusicBrainz recording ID) of a file, or ""."""
    key = ma.acoustid_key()
    fp = ma.fingerprint(str(path), db) if key else None
    if not fp:
        return ""
    data = http.get_json(f"https://api.acoustid.org/v2/lookup?client={ma.q(key)}&format=json&meta=recordings"
                         f"&duration={fp['duration']}&fingerprint={ma.q(fp['fingerprint'])}", "acoustid") or {}
    best = max(data.get("results") or [], key=lambda r: r.get("score", 0), default={})
    recs = best.get("recordings") or []
    return recs[0]["id"] if best.get("score", 0) >= 0.9 and recs else ""


def rank(path):
    """Sort key: the copy to keep first."""
    f = mutagen.File(path)
    info = getattr(f, "info", None)
    lossless = Path(path).suffix.lower() in (".flac", ".wav", ".aiff", ".wv", ".ape")
    siblings = sum(1 for p in Path(path).parent.iterdir() if p.suffix.lower() in ma.AUDIO_EXT)
    return (not lossless, -(getattr(info, "bits_per_sample", 0) or 0), -(getattr(info, "sample_rate", 0) or 0),
            -siblings, str(path))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("roots", nargs="+")
    ap.add_argument("--fingerprint", action="store_true", help="also fingerprint files without a recording ID")
    ap.add_argument("--out", default="~/Downloads/duplicate_recordings.csv")
    ap.add_argument("--cache-db", default="~/Downloads/matching/match_state.db")
    args = ap.parse_args(argv)
    db = sqlite3.connect(Path(args.cache_db).expanduser(), timeout=60) if args.fingerprint else None
    http = ma.Http(db, dict(mc.DEFAULT_CFG), "") if db else None
    by_rec, seen = {}, 0
    for root in args.roots:
        for dirpath, dirs, names in os.walk(Path(root).expanduser()):
            dirs[:] = sorted(d for d in dirs if d not in ("2delete", "covers"))
            for n in sorted(names):
                p = Path(dirpath) / n
                if p.suffix.lower() not in ma.AUDIO_EXT:
                    continue
                seen += 1
                try:
                    rid = recording_id(p) or (acoustid_recording(http, p, db) if http else "")
                except Exception:
                    rid = ""
                if rid:
                    by_rec.setdefault(rid, []).append(p)
    rows = []
    for rid, paths in by_rec.items():
        albums = {p.parent for p in paths}
        if len(albums) < 2:
            continue                                  # the same album twice is dedupe_twins.py's job
        for i, p in enumerate(sorted(paths, key=rank)):
            rows.append({"Recording": f"https://musicbrainz.org/recording/{rid}", "Keep": "yes" if i == 0 else "",
                         "File": str(p), "Format": p.suffix.lstrip(".").lower()})
    out = Path(args.out).expanduser()
    with open(out, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["Recording", "Keep", "File", "Format"])
        w.writeheader()
        w.writerows(rows)
    groups = len({r["Recording"] for r in rows})
    print(f"{seen} files; {groups} recordings found in more than one album. Report: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
