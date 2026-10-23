#!/usr/bin/env python3
"""check_tagged.py - audit albums already tagged by tag_albums.py against what their
ORIGINAL folder name said (before Picard renamed it): the format ("Vinyl Rip", "2CD"),
catalogue number, country, label and edition year.

Reads every run's journal (old folder, release ID, new paths) and the release data
from the matchers' caches (fetched from MusicBrainz, 1 a second, when not cached).
Changes nothing. Writes a CSV of albums whose release contradicts the folder name.

  python3 check_tagged.py                         # all runs in ~/Downloads/tagging
  python3 check_tagged.py --runs ~/Downloads/tagging/run-20261023-123353
"""
from __future__ import annotations

import argparse
import csv
import glob
import json
import os
import sqlite3
import sys
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import match_core as mc  # noqa: E402

UA = "TagMender-check/0.1 +https://github.com/herculepirate/tagmender"


class Releases:
    def __init__(self, dbs):
        self.dbs = [sqlite3.connect(f"file:{d}?mode=ro", uri=True) for d in dbs if Path(d).exists()]
        self.last = 0.0

    def mb(self, mbid):
        for db in self.dbs:
            try:
                row = db.execute("SELECT body FROM http_cache WHERE url LIKE ? AND url LIKE '%inc=%' LIMIT 1",
                                 (f"%musicbrainz.org/ws/2/release/{mbid}?%",)).fetchone()
            except sqlite3.Error:
                row = None
            if row:
                try:
                    d = json.loads(row[0])
                    if d.get("id") == mbid:
                        return d
                except ValueError:
                    pass
        if os.environ.get("TAGMENDER_NO_FETCH"):
            return None
        wait = 1.1 - (time.time() - self.last)
        if wait > 0:
            time.sleep(wait)
        self.last = time.time()
        url = f"https://musicbrainz.org/ws/2/release/{mbid}?inc=labels+media&fmt=json"
        for attempt in range(4):
            try:
                req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
                with urllib.request.urlopen(req, timeout=30) as r:
                    return json.loads(r.read().decode("utf-8"))
            except Exception as exc:                      # 503 and friends: back off
                if attempt == 3:
                    print(f"    could not fetch {mbid}: {exc}", file=sys.stderr)
                    return None
                time.sleep(3 * (attempt + 1))


def check(folder_name, rel):
    """Contradictions between the original folder name and the release used."""
    issues = []
    formats = {mc.media_kind(m.get("format")) for m in rel.get("media") or []} - {""}
    labels = "; ".join((li.get("label") or {}).get("name") or "" for li in rel.get("label-info") or [])
    catnos = {mc.norm_code(li.get("catalog-number") or "") for li in rel.get("label-info") or []} - {""}
    cand = {"country": rel.get("country") or "", "label": labels, "formats": formats, "catnos": catnos}
    hint = mc.media_hint(folder_name)
    album = {"folder": folder_name, "media_hint": hint}
    clues, against = {}, []
    mc.file_clues(album, cand, clues, against)
    folder_cat = {mc.norm_code(c) for c in mc.catno_hints(folder_name)} - {""}
    cat_ok = bool(folder_cat and catnos and mc.catnos_agree(folder_cat, catnos))
    issues += [a for a in against if a.startswith("folder name")
               and not (cat_ok and not a.startswith(("folder name says vinyl", "folder name says cd")))]
    if folder_cat and catnos and not mc.catnos_agree(folder_cat, catnos):
        issues.append(f"folder catalogue number {', '.join(mc.catno_hints(folder_name))} is not this "
                      f"release's ({', '.join(li.get('catalog-number') or '' for li in rel.get('label-info') or [])})")
    py = mc.pressing_year(folder_name)
    ry = str(rel.get("date") or "")[:4]
    if py and ry.isdigit() and abs(int(ry) - py) > 2:
        issues.append(f"folder says the {py} edition, release is from {ry}")
    return issues, cand


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--runs", nargs="*", default=sorted(glob.glob(os.path.expanduser("~/Downloads/tagging/run-*"))))
    ap.add_argument("--dbs", nargs="*", default=[os.path.expanduser(p) for p in (
        "~/Downloads/matching_extreme/match_state.db", "~/Downloads/matching/match_state.db")])
    ap.add_argument("--out", default=os.path.expanduser("~/Downloads/tagged_check.csv"))
    args = ap.parse_args()
    rels = Releases(args.dbs)
    rows, seen, n = [], set(), 0
    for run in args.runs:
        jf = Path(run) / "journal.json"
        if not jf.exists():
            continue
        try:
            journal = json.loads(jf.read_text())
        except ValueError:                                 # being written right now: try again
            time.sleep(5)
            journal = json.loads(jf.read_text())
        for a in journal:
            mbid, old = a.get("mbid"), a.get("album")
            if not mbid or (old, mbid) in seen:
                continue
            saved = [f.get("new_path") for f in a.get("files") or [] if f.get("new_path")]
            if not saved:
                continue                                   # not saved by Picard: nothing to check
            seen.add((old, mbid))
            n += 1
            rel = rels.mb(mbid)
            if not rel:
                rows.append({"run": Path(run).name, "original_folder": old, "now_in": "", "release": mbid,
                             "issues": "release data unavailable"})
                continue
            issues, cand = check(Path(old).name, rel)
            if issues:
                now = sorted({str(Path(p).parent) for p in saved})
                rows.append({"run": Path(run).name, "original_folder": old, "now_in": "; ".join(now),
                             "release": f"https://musicbrainz.org/release/{mbid}",
                             "matched": f"{rel.get('title')} | {rel.get('country') or ''} | {cand['label']} | "
                                        f"{'/'.join(sorted(cand['formats']))} | {rel.get('date') or ''}",
                             "issues": "; ".join(issues)})
                print(f"!  {Path(old).parent.name} | {Path(old).name}\n     {'; '.join(issues)}")
        del journal
    with open(args.out, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["run", "original_folder", "now_in", "release", "matched", "issues"])
        w.writeheader()
        w.writerows(rows)
    print(f"\nChecked {n} tagged albums: {len(rows)} contradict their original folder name. List: {args.out}")


if __name__ == "__main__":
    main()
