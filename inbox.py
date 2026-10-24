#!/usr/bin/env python3
"""inbox.py - drop music in a folder; TagMender identifies, tags and files it.

Watches an inbox folder. Each album that arrives (a folder of audio, or loose
tracks, which are grouped into albums first) is handled with no one stepping in:

  1. identified by the matcher (MusicBrainz, Discogs, scans, rip logs, disc IDs,
     fingerprints, ISRCs, links in the tags);
  2. a confident MusicBrainz match is tagged and filed by Picard (tag_albums.py);
  3. anything else (Discogs-only, review, no match) goes to web_tag.py, which
     tags it from the Discogs release or the web when the proof holds, and files
     it like Picard would;
  4. what nothing can identify is marked TAGMENDER=Unresolved and moved to
     <inbox>/_unresolved/, where the monthly re-check looks at it again.

  python3 inbox.py --inbox ~/Music/Inbox --library /media/unraidmedia2/music/extreme_music
  python3 inbox.py ... --once          # one pass, then stop (for a timer or cron)

Picard's "Move files to" folder must be --library, and Picard must be closed
while an album is being tagged (the inbox waits and tries again).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import match_core as mc  # noqa: E402
import match_albums as ma  # noqa: E402
import tag_albums as ta  # noqa: E402
import exclusions  # noqa: E402

_DISC_DIR = re.compile(r"^(?:cd|disc|disk|dvd|side)\s*[-_.]?\s*\d+\b|^\d{1,2}$", re.I)


def album_folders(inbox):
    """Folders holding an album: those with audio directly inside, with disc
    folders ("CD1", "Disc 2") counted as part of their parent."""
    found = set()
    for dirpath, dirs, names in os.walk(inbox):
        dirs[:] = sorted(d for d in dirs if not d.startswith("_"))
        d = Path(dirpath)
        if d == Path(inbox):
            continue
        if any(Path(n).suffix.lower() in ma.AUDIO_EXT for n in names):
            found.add(d.parent if _DISC_DIR.match(d.name) and d.parent != Path(inbox) else d)
    # a folder inside an album folder is part of it
    return sorted(f for f in found if not any(p in found for p in f.parents))


def settled(folder, minutes):
    """Nothing in the folder changed for a while (a copy or download has finished)."""
    newest = max((p.stat().st_mtime for p in Path(folder).rglob("*") if p.is_file()), default=0)
    return time.time() - newest > minutes * 60


def group_loose(inbox, http, db):
    """Loose tracks at the top of the inbox, grouped into album folders: by their
    album tags, else by the release AcoustID finds for most of them."""
    loose = [p for p in Path(inbox).iterdir() if p.is_file() and p.suffix.lower() in ma.AUDIO_EXT]
    groups = {}
    for p in loose:
        i = ma.read_file(p)
        key = (i.get("artist") or "", i.get("album") or "")
        if not key[1] and ma.fingerprint_ready():
            rels = ma.acoustid_releases(http, {"tracks": [{"path": str(p)}]}, db, n=1)
            key = ("", sorted(rels)[0]) if rels else ("", "")
        groups.setdefault(key, []).append(p)
    made = []
    for (artist, album), files in groups.items():
        if not album:
            continue                                   # nothing says which album: left for a person
        name = re.sub(r'[\\/:*?"<>|]', "_", f"{artist} - {album}" if artist else album).strip(" .")
        dest = Path(inbox) / name
        dest.mkdir(exist_ok=True)
        for f in files:
            shutil.move(str(f), dest / f.name)
        made.append(dest)
    return made


def save_result(db, out):
    db.execute("CREATE TABLE IF NOT EXISTS results (path TEXT PRIMARY KEY, result TEXT)")
    db.execute("INSERT OR REPLACE INTO results VALUES (?,?)", (out["path"], json.dumps(out)))
    db.commit()


def handle(folder, args, http, cfg, dm, db, log):
    folder = Path(folder)
    row = {"FullPath": str(folder), "AlbumArtistFolder": folder.parent.name if folder.parent != Path(args.inbox)
           else "", "AlbumFolder": folder.name, "Status": "untagged"}
    out = ma.process(row, http, cfg, dm, SimpleNamespace(ocr=True, max_images=15, process_anyway=False))
    save_result(db, out)
    best = out.get("best") or {}
    if out["status"] in ("ignored", "skipped"):          # a rule excludes it: nothing is written or moved
        log(f"{folder.name}: {out['why'][0]}")
        return out["status"]
    log(f"{folder.name}: {out['status']} {best.get('source', '')} {best.get('artist', '')} - {best.get('title', '')}")
    py = sys.executable
    if out["status"] == "auto" and best.get("mb_id"):         # Picard tags the MusicBrainz twin
        if ta.picard_running():
            log("  Picard is open: will tag on a later pass")
            return "waiting"
        r = subprocess.run([py, str(HERE / "tag_albums.py"), "--matches-db", str(args.db), "--only", str(folder),
                            "--apply", "--into", str(args.library), "--picard-ini", args.picard_ini],
                           capture_output=True, text=True)
        log("  " + (r.stdout.strip().splitlines() or [""])[-1])
        if not folder.exists():
            return "tagged"
    r = subprocess.run([py, str(HERE / "web_tag.py"), "--matches-db", str(args.db), "--only", str(folder),
                        "--apply", "--include-review", "--library", str(args.library)],
                       capture_output=True, text=True, env=os.environ)
    for line in r.stdout.splitlines():
        if line.strip().startswith(("+", "-")):
            log("  " + line.strip())
    if folder.exists():                                  # nothing could identify it
        dest = Path(args.inbox) / "_unresolved" / folder.name
        dest.parent.mkdir(exist_ok=True)
        if not dest.exists():
            shutil.move(str(folder), dest)
            out["path"] = str(dest)
            save_result(db, out)
        log(f"  unresolved -> {dest}")
        return "unresolved"
    return "tagged"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--inbox", required=True)
    ap.add_argument("--library", required=True, help="library folder albums are filed into (Picard's move folder)")
    ap.add_argument("--out-dir", default="~/Downloads/matching_inbox")
    ap.add_argument("--plugin-dir", default=ma.DEFAULT_PLUGIN_DIR)
    ap.add_argument("--picard-ini", default="~/.var/app/org.musicbrainz.Picard/config/MusicBrainz/Picard.ini")
    ap.add_argument("--interval", type=int, default=60, help="seconds between looks at the inbox")
    ap.add_argument("--settle-minutes", type=float, default=2, help="wait until a folder is unchanged this long")
    ap.add_argument("--once", action="store_true", help="one pass, then stop")
    args = ap.parse_args(argv)
    args.inbox = str(Path(args.inbox).expanduser())
    args.library = str(Path(args.library).expanduser())
    out_dir = Path(args.out_dir).expanduser()
    out_dir.mkdir(parents=True, exist_ok=True)
    args.db = out_dir / "match_state.db"
    token = os.environ.get("DISCOGS_TOKEN", "")
    if not token:
        sys.exit("Set DISCOGS_TOKEN first")
    cfg = dict(mc.DEFAULT_CFG)
    countries = ma.picard_preferred_countries(args.picard_ini)
    if countries:
        cfg["preferred_countries"] = countries
    dm = ma.load_plugin(args.plugin_dir)
    db = sqlite3.connect(args.db, timeout=60)
    http = ma.Http(db, cfg, token)
    logf = open(out_dir / "inbox.log", "a", encoding="utf-8")

    def log(msg):
        line = f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}"
        print(line, flush=True)
        logf.write(line + "\n")
        logf.flush()

    log(f"watching {args.inbox} -> {args.library}")
    state = exclusions.State()
    skipped_seen = {}                                     # folder -> signature: a skip is logged once per change
    while True:
        try:
            group_loose(args.inbox, http, db)
            rules = exclusions.load_rules()
            rh = exclusions.rules_hash(rules)
            for folder in album_folders(args.inbox):
                if not settled(folder, args.settle_minutes):
                    continue
                sig = exclusions.signature(exclusions.audio_files(folder))
                if state.still_ignored(folder, sig, rh) or skipped_seen.get(str(folder)) == (sig, rh):
                    continue                              # unchanged, and the rules too: not queued again
                try:
                    result = handle(folder, args, http, cfg, dm, db, log)
                    if result == "skipped":
                        skipped_seen[str(folder)] = (sig, rh)
                except Exception as exc:                       # one bad album never stops the inbox
                    log(f"{folder.name}: failed ({type(exc).__name__}: {exc})")
        except Exception as exc:
            log(f"pass failed ({exc})")
        if args.once:
            return 0
        time.sleep(args.interval)


if __name__ == "__main__":
    sys.exit(main())
