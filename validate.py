#!/usr/bin/env python3
"""validate.py - does TagMender identify albums you have ALREADY tagged correctly?

For every album folder in a library that carries a MusicBrainz or Discogs release ID,
the matcher is run BLIND: the release IDs, ISRCs, label, catalogue number, barcode,
country and date in the tags are ignored (they came from the release and would only
confirm it). What is left is what an untagged copy has: names, track lengths, the audio,
the disc TOC, the scans, and the ORIGINAL folder name (from tag_albums.py's journals
when Picard renamed the folder). Its pick is compared with the release in the tags.

Changes nothing in the library. Uses its own state/cache (copied from the matcher's
cache on first run), so it can run while match_loop.sh runs. Resumable: run it again
and it continues; --redo starts over.

  export DISCOGS_TOKEN=...      (read -rs -p "Discogs token: " DISCOGS_TOKEN; export DISCOGS_TOKEN)
  python3 validate.py /media/unraidmedia2/music/ambient_music
  python3 validate.py /media/unraidmedia2/music/ambient_music --artist "Steve Roach" --limit 20

Output: <out-dir>/validation.csv (one row per album) and a summary:
  agree           the matcher picks the release in your tags (or the same pressing)
  other pressing  the same album, but the matcher picks another pressing of it
  differs         the matcher is SURE of another album: one of the two is wrong
  unsure          the matcher would send it to review (why is listed)
  no match        the matcher finds nothing
  ignored         an Ignore/Skip rule excludes it
  not tagged      no release ID in the files (nothing to compare)
"""
from __future__ import annotations

import argparse
import collections
import csv
import glob
import json
import os
import re
import sqlite3
import sys
import time
import types
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import match_core as mc  # noqa: E402
import match_albums as ma  # noqa: E402

try:
    import mutagen
except ImportError:
    sys.exit("mutagen is required: pip install mutagen")


def file_ids(folder):
    """The release IDs in the album's tags: (MusicBrainz release, Discogs release)."""
    mb, dg = collections.Counter(), collections.Counter()
    for p in sorted(Path(folder).rglob("*")):
        if p.suffix.lower() not in ma.AUDIO_EXT:
            continue
        try:
            f = mutagen.File(p)
        except Exception:
            continue
        if not f or not f.tags:
            continue
        for k, v in f.tags.items():
            key = str(getattr(v, "desc", "") or k).lower().replace("----:com.apple.itunes:", "")
            key = key.replace("txxx:", "").replace(" ", "_")
            vals = getattr(v, "text", None) or (v if isinstance(v, list) else [v])
            val = str(vals[0]) if vals else ""
            if isinstance(val, bytes):
                val = val.decode("utf-8", "replace")
            if key in ("musicbrainz_albumid", "musicbrainz album id"):
                mb[val.strip()] += 1
            elif key in ("discogs_release_id", "discogs_releaseid", "discogs release id"):
                dg[val.strip()] += 1
        if sum(mb.values()) + sum(dg.values()) >= 6:
            break
    return (mb.most_common(1)[0][0] if mb else ""), (dg.most_common(1)[0][0] if dg else "")


def album_folders(library, artist=None):
    """library/Artist/Album folders holding audio (in them or in Disc subfolders)."""
    for a in sorted(Path(library).iterdir()):
        if not a.is_dir() or (artist and artist.casefold() not in a.name.casefold()):
            continue
        for d in sorted(a.iterdir()):
            if d.is_dir() and any(p.suffix.lower() in ma.AUDIO_EXT for p in d.rglob("*")):
                yield d


def original_names(state, journals):
    """Folder Picard filed an album into -> the folder name it had before (the name
    carries your notes: "[Vinyl Rip]", "(Art Music, AMG 099, Russia)"). Built once
    from tag_albums.py's journals and kept in the state db."""
    state.execute("CREATE TABLE IF NOT EXISTS origin (folder TEXT PRIMARY KEY, original TEXT, run TEXT)")
    done = {r[0] for r in state.execute("SELECT DISTINCT run FROM origin")}
    for jf in journals:
        run = Path(jf).parent.name
        if run in done:
            continue
        print(f"Reading {run} journal (original folder names)…", flush=True)
        try:
            journal = json.loads(Path(jf).read_text())
        except (OSError, ValueError) as exc:
            print(f"  skipped: {exc}")
            continue
        for a in journal:
            new = {str(Path(f["new_path"]).parent) for f in a.get("files") or [] if f.get("new_path")}
            for d in new:
                for folder in {d, str(Path(d).parent)} if Path(d).name.lower().startswith(("disc", "cd")) else {d}:
                    state.execute("INSERT OR REPLACE INTO origin VALUES (?,?,?)", (folder, a["album"], run))
        state.execute("INSERT OR REPLACE INTO origin VALUES (?,?,?)", (f"__run__{run}", "", run))
        state.commit()
        del journal
    return {f: o for f, o, _r in state.execute("SELECT folder, original, run FROM origin") if o}


def seed_cache(state_path, cache_dbs):
    """Start from the matcher's caches (lookups, OCR) so known albums cost nothing."""
    st = sqlite3.connect(state_path)
    st.execute("CREATE TABLE IF NOT EXISTS http_cache (url TEXT PRIMARY KEY, body TEXT, ts REAL DEFAULT 0)")
    st.execute("CREATE TABLE IF NOT EXISTS ocr_cache (key TEXT PRIMARY KEY, value TEXT)")
    for c in cache_dbs:
        if not Path(c).exists():
            continue
        st.execute("ATTACH DATABASE ? AS src", (f"file:{c}?mode=ro",))
        try:
            cols = [r[1] for r in st.execute("PRAGMA src.table_info(http_cache)")]
            ts = "ts" if "ts" in cols else "0"
            st.execute(f"INSERT OR IGNORE INTO http_cache SELECT url, body, {ts} FROM src.http_cache")
            st.execute("INSERT OR IGNORE INTO ocr_cache SELECT key, value FROM src.ocr_cache")
        except sqlite3.Error as exc:
            print(f"  cache {c}: {exc}")
        st.commit()
        st.execute("DETACH DATABASE src")
    return st


def describe_mb(http, mbid):
    rel = http.get_json(f"https://musicbrainz.org/ws/2/release/{mbid}"
                        f"?inc=recordings+artist-credits+labels+url-rels&fmt=json", "mb") if mbid else None
    return rel


def short(rel):
    if not rel:
        return ""
    li = rel.get("label-info") or []
    return " | ".join([rel.get("title") or "", rel.get("country") or "", str(rel.get("date") or ""),
                       "; ".join((i.get("label") or {}).get("name") or "" for i in li),
                       "; ".join(i.get("catalog-number") or "" for i in li),
                       "/".join(m.get("format") or "" for m in rel.get("media") or [])])


def _discogs(http, rid, cache):
    if not rid or not cache.get("dm"):
        return None
    d = http.get_json(f"https://api.discogs.com/releases/{rid}", "discogs")
    return ma.discogs_candidate(d, cache["dm"]) if d else None


def tagged_release(http, mb_id, dg_id, cache):
    """The release in the tags as a candidate: the MusicBrainz release, else the Discogs
    one (albums tagged from Discogs alone carry only DISCOGS_RELEASE_ID)."""
    if mb_id:
        d = describe_mb(http, mb_id)
        if d:
            return ma.mb_candidate(d)
    return _discogs(http, dg_id, cache)


def picked_release(http, b, cache):
    if b.get("source") == "discogs":
        return _discogs(http, b.get("id"), cache)
    d = describe_mb(http, b.get("mb_id") or b.get("id"))
    return ma.mb_candidate(d) if d else None


def _secs(release):
    return [float(t.get("secs") or 0) for t in release.get("tracks") or []]


def _contained(short, long, tol=3.0):
    """Every track length of `short` found in `long`, in order (the CD inside a CD +
    7" edition, a release without its bonus tracks)."""
    if not short or any(x <= 0 for x in short):
        return False
    i = 0
    for x in long:
        if i < len(short) and abs(x - short[i]) <= tol:
            i += 1
    return i == len(short)


def other_album(mine, other):
    """False when the release in the tags and the pick are the same album, True when
    they are not, None when unknown. The same album: the same title and tracks, or one
    tracklist inside the other (Enslaved - Axioma Ethica Odini: the CD + 7" edition in
    the tags, the CD picked), or every track length agreeing under another title
    (Candlemass: "Dactylis Glomerata" with Abstrakt Algebra II as its second disc)."""
    if not mine or not other:
        return None
    a, b = _secs(mine), _secs(other)
    same_title = mc.title_sim(mine["title"], other["title"]) >= 0.85
    if same_title and len(a) == len(b):
        return False
    short, long = (a, b) if len(a) <= len(b) else (b, a)
    if same_title and _contained(short, long):
        return False
    if len(a) == len(b) and len(a) >= 3 and all(x > 0 for x in a + b):
        agree = sum(abs(x - y) <= 3 for x, y in zip(a, b))
        if agree >= 0.9 * len(a):
            return False
    return True


def verdict(out, mb_id, dg_id, http, cache):
    """Compare the blind pick with the release in the tags."""
    st = out.get("status")
    b = out.get("best") or {}
    if st in ("ignored", "skipped"):
        return "ignored", ""
    if st == "nomatch" or not b:
        return "no match", "; ".join(out.get("why") or [])
    picked_mb = b.get("mb_id") or (b.get("id") if b.get("source") == "mb" else "")
    picked_dg = b.get("id") if b.get("source") == "discogs" else (b.get("discogs_id") or "")
    same = bool((mb_id and picked_mb == mb_id) or (dg_id and str(picked_dg) == str(dg_id)))
    mine = other = None
    if not same and (mb_id or dg_id):
        # the same pressing listed twice, or MusicBrainz and Discogs entries of one pressing
        mine, other = tagged_release(http, mb_id, dg_id, cache), picked_release(http, b, cache)
        if mine and other and (mc.same_pressing(mine, other) or mc.same_pressing(other, mine)):
            same = True
    if not same and other_album(mine, other) is False:
        # the same album, another pressing of it: nothing in the files or folder name tells
        # the pressings apart, or the matcher read it differently
        return ("other pressing" if st == "auto" else "unsure"), "; ".join(out.get("why") or [])
    if st == "auto":
        return ("agree", "") if same else ("differs", "")
    return ("unsure (would pick yours)" if same else "unsure"), "; ".join(out.get("why") or [])


FALLBACKS = ("preferred country order", "same pressing listed twice", "label's original pressing")


def evidence_decided(rec):
    """The pick rests on evidence about YOUR copy (a catalogue number, barcode, disc ID,
    label, country or address on the scans, the folder name, the audio), not on a
    fallback (your country order, or one of two identical entries)."""
    notes = rec.get("notes") or ""
    if rec.get("status") != "auto" or any(f in notes for f in FALLBACKS):
        return False
    return bool(re.search(r"pressing chosen by|left out:|catalogue number .*agrees|barcode .*agrees|"
                          r"disc ID|proves the album|Discogs chosen", notes))


def read_approved(path):
    """Albums you approved for a retag, one per line: the folder, then optionally a tab
    and the release to use ("mb:<MBID>" or "discogs:<id>"); without one, the pick of
    the latest check is used. Lines starting with # are comments."""
    out = {}
    try:
        lines = Path(path).read_text(encoding="utf-8").splitlines()
    except OSError:
        return out
    for line in lines:
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        folder, _, rel = line.partition("\t")
        rel = rel.strip()
        if rel and not re.fullmatch(r"(?:mb:[0-9a-f-]{36}|discogs:\d+)", rel):
            raise ValueError(f"approved list: release must be mb:<MBID> or discogs:<id>, not {rel!r}")
        out[folder.strip().rstrip("/")] = rel
    return out


def export_fixes(state, out_dir, current, approved=None):
    """fixes.db for tag_albums.py --retag (and web_tag.py): the albums whose tags hold
    another release than the one the evidence points to, and the albums you approved
    (a pick nothing about the copy decides, or another release you named)."""
    approved = approved or {}
    rows = [json.loads(r) for (r,) in state.execute("SELECT row FROM validation")]
    fixes = []
    for r in rows:
        if r.get("folder") in approved:
            rel = approved[r["folder"]]
            if rel and rel != r.get("picked"):          # the same pick keeps its MusicBrainz twin
                r = dict(r, picked=rel, picked_mb_twin=rel[3:] if rel.startswith("mb:") else "",
                         notes="approved by you; " + (r.get("notes") or ""))
            elif not r.get("picked"):
                continue                               # nothing picked and nothing named
            fixes.append(r)
        elif (r.get("verdict") in ("differs", "other pressing") and evidence_decided(r)
              and r.get("version") == current):
            fixes.append(r)
    path = out_dir / "fixes.db"
    if path.exists():
        path.unlink()
    db = sqlite3.connect(path)
    db.execute("CREATE TABLE results (path TEXT PRIMARY KEY, result TEXT)")
    db.execute("CREATE TABLE http_cache (url TEXT PRIMARY KEY, body TEXT, ts REAL DEFAULT 0)")
    for r in fixes:
        src, _, rid = (r.get("picked") or ":").partition(":")
        best = {"source": src, "id": rid, "mb_id": r.get("picked_mb_twin") or (rid if src == "mb" else ""),
                "artist": r.get("picked_artist", ""), "title": r.get("picked_title", ""),
                "confidence": r.get("confidence"), "notes": (r.get("notes") or "").split("; ")}
        db.execute("INSERT INTO results VALUES (?,?)", (r["folder"], json.dumps(
            {"path": r["folder"], "status": "auto", "best": best, "version": current,
             "why": [], "fix_of": r.get("tags_mb") or r.get("tags_discogs")})))
        for rel in (best["mb_id"],):
            if rel:
                row = state.execute("SELECT url, body, ts FROM http_cache WHERE url LIKE ? AND url LIKE '%inc=%' "
                                    "LIMIT 1", (f"%/release/{rel}?%",)).fetchone()
                if row:
                    db.execute("INSERT OR IGNORE INTO http_cache VALUES (?,?,?)", row)
    db.commit()
    return path, fixes


def settled(state, current):
    """Albums needing no new check: they agree (or have nothing to compare), or the
    current matcher already checked them."""
    done = set()
    for p, r in state.execute("SELECT path, row FROM validation"):
        r = json.loads(r)
        if r.get("verdict") in ("agree", "not tagged", "ignored") or r.get("version") == current:
            done.add(p)
    return done


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("library")
    ap.add_argument("--out-dir", default=None, help="default: ~/Downloads/validate_<library name>")
    ap.add_argument("--cache", nargs="*", default=[os.path.expanduser(p) for p in (
        "~/Downloads/matching/match_state.db", "~/Downloads/matching_extreme/match_state.db")])
    ap.add_argument("--journals", nargs="*",
                    default=sorted(glob.glob(os.path.expanduser("~/Downloads/tagging/run-*/journal.json"))))
    ap.add_argument("--plugin-dir", default=ma.DEFAULT_PLUGIN_DIR)
    ap.add_argument("--artist")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--no-ocr", action="store_true")
    ap.add_argument("--redo", action="store_true")
    ap.add_argument("--approved", default=None,
                    help="albums you approved for a retag (folder[TAB]mb:<MBID>|discogs:<id> per line); "
                         "default: approved.tsv in the output folder, when it exists")
    ap.add_argument("--export-fixes", action="store_true",
                    help="only write fixes.db (albums the evidence says are tagged as the wrong release) and stop")
    args = ap.parse_args()

    lib = Path(args.library).expanduser()
    out_dir = Path(args.out_dir or f"~/Downloads/validate_{lib.name}").expanduser()
    out_dir.mkdir(parents=True, exist_ok=True)
    token = os.environ.get("DISCOGS_TOKEN", "")
    if not token and not args.export_fixes:
        sys.exit('Set DISCOGS_TOKEN first: read -rs -p "Discogs token: " DISCOGS_TOKEN; export DISCOGS_TOKEN')
    state_path = out_dir / "state.db"
    fresh = not state_path.exists()
    state = seed_cache(state_path, args.cache) if fresh else sqlite3.connect(state_path)
    state.execute("CREATE TABLE IF NOT EXISTS results (path TEXT PRIMARY KEY, result TEXT)")
    state.execute("CREATE TABLE IF NOT EXISTS validation (path TEXT PRIMARY KEY, row TEXT)")
    if args.redo:
        state.execute("DELETE FROM validation")
        state.execute("DELETE FROM results")
    state.commit()

    cfg = dict(mc.DEFAULT_CFG)
    countries = ma.picard_preferred_countries()
    if countries:
        cfg["preferred_countries"] = countries
    dm = ma.load_plugin(args.plugin_dir)
    ma._PLUGIN_STAMP = ma._script_version(ma.plugin_files(dm))[:6]
    if not args.no_ocr:
        ma.check_ocr_ready()
    if args.export_fixes:
        approved = read_approved(args.approved or out_dir / "approved.tsv")
        path, fixes = export_fixes(state, out_dir, ma.result_version(), approved)
        for r in fixes:
            print(f"  {r['folder']}\n      tags {r.get('tags_release') or r.get('tags_mb')}\n      ->   {r.get('picked_release')}"
                  f"  ({r.get('picked')})")
        print(f"{len(fixes)} album(s) to re-tag: {path}\n"
              f"  python3 tag_albums.py --matches-db {path} --retag            # preview, then --apply\n"
              f"  python3 web_tag.py --matches-db {path} --library {lib}      # Discogs-only ones")
        return
    http = ma.Http(state, cfg, token)
    names = original_names(state, args.journals)
    margs = types.SimpleNamespace(ocr=not args.no_ocr, max_images=15, process_anyway=False, blind=True)
    # Albums already checked are skipped, except those that did not agree and were checked
    # by an older matcher: after a fix they are checked again (validate_loop.sh relies on it).
    current = ma.result_version()
    done = settled(state, current)
    folders = [d for d in album_folders(lib, args.artist)]
    if args.limit:
        folders = folders[:args.limit]
    print(f"{len(folders)} album folders; {len(done & {str(d) for d in folders})} already checked "
          f"(matcher {current})")
    counts = collections.Counter()
    for n, d in enumerate(folders, 1):
        if str(d) in done:
            continue
        mb_id, dg_id = file_ids(d)
        original = names.get(str(d)) or str(d)
        row = {"FullPath": str(d), "AlbumArtistFolder": d.parent.name, "AlbumFolder": Path(original).name,
               "Status": "untagged"}
        rec = {"folder": str(d), "original_folder": Path(original).name, "tags_mb": mb_id, "tags_discogs": dg_id,
               "version": current, "checked": round(time.time())}
        if not mb_id and not dg_id:
            rec.update(verdict="not tagged")
        else:
            try:
                out = ma.process(row, http, cfg, dm, margs)
                v, why = verdict(out, mb_id, dg_id, http, {"dm": dm})
                b = out.get("best") or {}
                rec.update(verdict=v, why=why, status=out.get("status"),
                           picked=f"{b.get('source', '')}:{b.get('id', '')}" if b else "",
                           picked_mb_twin=b.get("mb_id", ""),
                           picked_artist=b.get("artist", ""), picked_title=b.get("title", ""),
                           picked_release=" | ".join(str(b.get(k) or "") for k in ("title", "country", "label", "year")),
                           confidence=b.get("confidence", ""),
                           notes="; ".join(b.get("notes") or [])[:500])
                if v in ("differs", "other pressing", "unsure", "no match") and mb_id:
                    mine = describe_mb(http, mb_id)
                    rec["tags_release"] = short(mine)
                    try:
                        import check_tagged
                        bad = check_tagged.check(Path(original).name, mine)[0] if mine else []
                    except Exception:
                        bad = []
                    rec["tags_contradict_folder"] = "; ".join(bad)
            except ma.AuthError as exc:
                sys.exit(f"\nDiscogs: {exc}")
            except Exception as exc:                       # one album never stops the run
                rec.update(verdict="error", why=f"{type(exc).__name__}: {exc}")
        state.execute("INSERT OR REPLACE INTO validation VALUES (?,?)", (str(d), json.dumps(rec)))
        state.commit()
        counts[rec["verdict"]] += 1
        mark = {"agree": "✓", "differs": "✗", "other pressing": "≈", "not tagged": "·"}.get(rec["verdict"], "?")
        print(f"[{n}/{len(folders)}] {mark} {rec['verdict']:<26} {d.parent.name} | {d.name}"
              + (f"\n      tags {mb_id or dg_id}  vs  picked {rec.get('picked')}"
                 if rec["verdict"] in ("differs", "other pressing") else ""),
              flush=True)

    rows = [json.loads(r) for (r,) in state.execute("SELECT row FROM validation ORDER BY path")]
    fields = ["verdict", "folder", "original_folder", "tags_mb", "tags_discogs", "tags_release",
              "tags_contradict_folder", "picked", "picked_mb_twin", "picked_release", "confidence",
              "status", "why", "notes"]
    with open(out_dir / "validation.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(sorted(rows, key=lambda r: (r["verdict"] != "differs", r["verdict"], r["folder"])))
    total = collections.Counter(r["verdict"] for r in rows)
    tagged = sum(v for k, v in total.items() if k not in ("not tagged",))
    print("\nSummary (all albums checked so far):")
    for k, v in total.most_common():
        print(f"  {k:<28} {v}")
    if tagged:
        print(f"Agreement: {total['agree'] + total['unsure (would pick yours)']} of {tagged} tagged albums "
              f"pick your release ({total['agree']} sure); {total['other pressing']} pick another pressing "
              f"of the same album; {total['differs']} pick another album.")
    print(f"List: {out_dir / 'validation.csv'}")


if __name__ == "__main__":
    main()
