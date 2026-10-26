#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""TagMender: tag the confidently matched albums with MusicBrainz Picard itself.

match_albums.py decides which release each untagged album is. This script makes Picard
tag them, so every tag, file name, folder and image is exactly what Picard writes with
your settings, naming script and plugins (Discogs Enrichment, CoverUp):

  1. backup  - every file's current tags (embedded pictures too) and location are saved,
               so a run can be undone completely;
  2. seed    - the matched MusicBrainz release ID is written into the files. Picard loads a
               file carrying a release ID straight onto that release (tagger._file_loaded);
  3. Picard  - started with its own commands, a batch at a time: LOAD the folders, let
               them settle, SAVE_MATCHED, QUIT. Picard runs each command only once the
               previous one has finished, web requests and saves included;
  4. verify  - every file is checked: saved by Picard (it writes the track IDs), where it
               moved to, and whether Discogs Enrichment added its fields.

Picard must be closed when this runs: its commands would otherwise go to your open Picard
and save whatever else you have loaded there.

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
AUDIO_EXT = {".flac", ".mp3", ".m4a", ".mp4", ".ogg", ".opus", ".oga", ".wv", ".ape", ".mpc", ".dsf", ".wma"}
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


def write_albumid(path, mbid):
    """Write only the MusicBrainz release ID, the way Picard stores it for each format."""
    f = mutagen.File(path)
    if f is None:
        raise ValueError("not an audio file mutagen can write")
    if isinstance(f, MP4):
        if f.tags is None:
            f.add_tags()
        f.tags["----:com.apple.iTunes:MusicBrainz Album Id"] = [MP4FreeForm(mbid.encode())]
    elif f.tags is not None and isinstance(f.tags, ID3) or path.suffix.lower() == ".mp3":
        if f.tags is None:
            f.add_tags()
        f.tags.delall("TXXX:MusicBrainz Album Id")
        f.tags.add(TXXX(encoding=3, desc="MusicBrainz Album Id", text=[mbid]))
    else:
        if f.tags is None:
            f.add_tags()
        f.tags["MUSICBRAINZ_ALBUMID"] = [mbid]
    f.save()


TAGMENDER_TAG = "TAGMENDER"                  # Resolved / Unresolved
TAGMENDER_REASON = "TAGMENDER_REASON"       # why an album is unresolved


def write_tagmender(path, status, reason=""):
    """TAGMENDER=Resolved|Unresolved (and the reason when unresolved), in the form
    each format stores a custom tag. Nothing else in the file changes."""
    path = Path(path)
    f = mutagen.File(path)
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
    elif (f.tags is not None and isinstance(f.tags, ID3)) or path.suffix.lower() == ".mp3":
        if f.tags is None:
            f.add_tags()
        f.tags.delall(f"TXXX:{TAGMENDER_TAG}")
        f.tags.delall(f"TXXX:{TAGMENDER_REASON}")
        f.tags.add(TXXX(encoding=3, desc=TAGMENDER_TAG, text=[status]))
        if reason:
            f.tags.add(TXXX(encoding=3, desc=TAGMENDER_REASON, text=[reason]))
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
        import picard_config
        kept = {x.lower() for x in picard_config._qt_list(cp.get("setting", "preserved_tags", fallback=""))}
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


def backup_tags(path, blob_dir):
    """Every tag of the file as stored, pictures written to blob_dir by checksum."""
    f = _open(path)
    rec = {"format": type(f).__name__ if f else "", "tags": None, "pictures": []}
    if f is None:
        return rec
    if isinstance(f, FLAC):
        for p in f.pictures:
            rec["pictures"].append(_blob(p.write(), blob_dir))
    if f.tags is None:
        return rec
    if isinstance(f.tags, ID3):
        tmp = Path(blob_dir) / f"backup-{os.getpid()}.id3"
        tmp.write_bytes(b"")
        if f.tags.version[1] >= 4:
            f.tags.save(str(tmp), v2_version=4)
        else:                                 # (as the file has it: v2.3 frames, never mutagen's v2.4 view)
            ID3(str(path), translate=False).save(str(tmp), v2_version=3, v23_sep=None)
        rec["tags"] = {"id3": _blob(tmp.read_bytes(), blob_dir), "id3_version": f.tags.version[1]}
        tmp.unlink()
    elif isinstance(f, MP4):
        rec["tags"] = {"mp4": [[k, _mp4_value(v, blob_dir)] for k, v in f.tags.items()]}
    else:
        rec["tags"] = {"vorbis": [[k, v] for k, v in f.tags.items()]}
    return rec


def _blob(data, blob_dir):
    h = hashlib.sha1(data).hexdigest()
    p = Path(blob_dir) / h
    if not p.exists():
        p.write_bytes(data)
    return h


def _mp4_value(values, blob_dir):
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


def restore_tags(path, rec, blob_dir):
    from mutagen.flac import Picture
    from mutagen.mp4 import MP4Cover
    f = _open(path)
    if f is None:
        raise ValueError("cannot open")
    tags = rec.get("tags")
    if isinstance(f, FLAC):
        f.clear_pictures()
        for h in rec.get("pictures", []):
            f.add_picture(Picture((Path(blob_dir) / h).read_bytes()))
    if tags and "id3" in tags:
        tmp = Path(blob_dir) / f"restore-{os.getpid()}.id3"
        tmp.write_bytes((Path(blob_dir) / tags["id3"]).read_bytes())
        v23 = tags.get("id3_version", 4) < 4
        id3 = ID3(str(tmp), translate=not v23)
        tmp.unlink()
        id3.save(str(path), v2_version=3 if v23 else 4, v23_sep=None)        # (several values as stored)
        return
    if rec.get("format") == "MP3" or str(path).lower().endswith(".mp3"):
        try:
            ID3(str(path)).delete(str(path))             # it had no tags before
        except Exception:
            pass
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
            for v in vals:
                if "bytes" in v:
                    data = (Path(blob_dir) / v["bytes"]).read_bytes()
                    out.append(MP4Cover(data, v["fmt"]) if v["kind"] == "MP4Cover"
                               else MP4FreeForm(data, 1 if v["fmt"] is None else v["fmt"]))
                elif "tuple" in v:
                    out.append(tuple(v["tuple"]))
                else:
                    out.append(v["value"])
            f.tags[k] = out
    f.save()


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
        release = {"album": [b.get("title") or ""], "albumartist": [b.get("artist") or ""],
                   "artist": [b.get("artist") or ""]}
        dec = exclusions.gate(files, "tag", rules, process_anyway=process_anyway)
        if not dec.excluded and not process_anyway:
            dec = exclusions.gate(files, "tag", rules, release=release)
        if dec.excluded:
            whole = dec.summary() or next(iter(dec.excluded.values()))
            skipped.append((path, whole.reason() if dec.all_excluded else
                            f"{whole.reason()} for {len(dec.excluded)} of {len(files)} files: Picard tags whole "
                            "folders, so the album is left for you (or use --process-anyway)"))
            continue
        ids = [read_ids(p) for p in files]
        other = {i["albumid"] for i in ids if i["albumid"] and i["albumid"] != b["id"]}
        # retag (fixes found by validate.py): the files carry the WRONG release on purpose
        if other and not retag:
            skipped.append((path, f"files already carry another release ID {sorted(other)[0]}"))
            continue
        if all(i["trackid"] for i in ids) and not retag:
            skipped.append((path, "already tagged by Picard"))
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


def picard_running():
    """The first running Picard process ('pid command'), or '' when Picard is closed."""
    if os.environ.get("TAGMENDER_SKIP_PICARD_CHECK") == "1":       # tests only
        return ""
    try:
        r = subprocess.run(["ps", "-eo", "pid=,args="], capture_output=True, text=True)
    except FileNotFoundError:
        return ""
    mine = os.getpid()
    for line in r.stdout.splitlines():
        pid, _, cmd = line.strip().partition(" ")
        if pid.isdigit() and int(pid) != mine and is_picard_process(cmd.strip()):
            return f"{pid} {cmd.strip()[:150]}"
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
        log.write(f"Picard did not finish within {timeout} s\n")
        return -1


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


def tidy_artwork(album_path, new_dir, extras):
    """After Picard, so that every file of the album is in the new folder, in its place:
      - cover.* / folder.* / albumart.* images: next to the music (Picard and players read them);
      - logs, cue sheets, text and other non-images: next to the music;
      - all other artwork, scan folders included: under covers/, subfolder names kept.
    Picard moves only matching files next to the music (and matching folders), so this takes
    what it left in the old folder, the loose scans and scan folders it moved, and repairs a
    cover image that ended up inside covers/. Returns [[from, to], ...] for --undo."""
    old, new = Path(album_path), Path(new_dir)
    covers = new / "covers"
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
        elif rel.parts[0].casefold() == "covers":      # the album's own Covers/ folder: merge
            move(src, covers.joinpath(*rel.parts[1:]))
        else:
            move(src, covers / rel)

    # 1. everything Picard left in the old folder
    if old.exists() and old.resolve() != new.resolve():
        if any(p.suffix.lower() in AUDIO_EXT for p in old.rglob("*") if p.is_file()):
            return moves                               # music still here: not saved, leave it
        for p in sorted(old.rglob("*")):
            if p.is_file():
                place(p, p.relative_to(old))
        _remove_empty(old)
    # 2. loose scans and scan folders Picard moved next to the music
    for rel in extras:
        rel = Path(rel)
        if rel.parts[0].casefold() == "covers":
            continue
        p = new / rel
        if not p.is_file():
            continue
        if len(rel.parts) > 1 or (p.suffix.lower() in IMAGE_EXT and not _is_cover_name(p)):
            move(p, covers / rel)
    for p in list(new.iterdir()):                      # a Covers/ folder Picard moved: merge it
        if p.is_dir() and p.name != "covers" and p.name.casefold() == "covers":
            for f in sorted(p.rglob("*")):
                if f.is_file():
                    move(f, covers / f.relative_to(p))
            _remove_empty(p, keep_parent=True)
    for rel in {Path(r).parts[0] for r in extras if len(Path(r).parts) > 1}:
        if rel.casefold() != "covers" and (new / rel).is_dir():
            _remove_empty(new / rel, keep_parent=True)
    # 3. a cover image misplaced inside covers/ goes back next to the music
    if covers.is_dir() and not any(_is_cover_name(p) for p in new.iterdir() if p.is_file()):
        for p in sorted(covers.iterdir()):
            if p.is_file() and _is_cover_name(p):
                move(p, new / p.name)
    return moves

def verify(album, files_rec, candidates):
    """Find each original file (by its audio) in the folders that changed, and say whether
    Picard saved it and whether Discogs Enrichment added its fields."""
    by_key = {}
    for d in candidates:
        for p in audio_files(d) if d.is_dir() else []:
            ids = read_ids(p)
            if ids["albumid"] == album["mbid"]:
                by_key.setdefault(audio_key(p), []).append((p, ids))
    result = {"saved": 0, "with_discogs": 0, "not_saved": [], "new_paths": {}}

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
                result["with_discogs"] += discogs(ids, rec)
                result["new_paths"][rec["path"]] = rec["path"]
            else:
                result["not_saved"].append(rec["path"])
            continue
        found = by_key.get(rec["key"]) or []
        if found:
            p, ids = found.pop(0)
            result["new_paths"][rec["path"]] = str(p)
            result["saved"] += 1 if saved(ids, rec) else 0
            result["with_discogs"] += discogs(ids, rec)
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
                    help="where Picard moves files (your 'Move files to' setting); default: read from Picard.ini")
    ap.add_argument("--picard-ini", default="~/.var/app/org.musicbrainz.Picard/config/MusicBrainz/Picard.ini")
    ap.add_argument("--picard", default=DEFAULT_PICARD, help=f"command that starts Picard (default: {DEFAULT_PICARD})")
    ap.add_argument("--apply", action="store_true", help="tag (default: dry run)")
    ap.add_argument("--mark-unresolved", action="store_true",
                    help=f"write {TAGMENDER_TAG}=Unresolved and the reason into albums left for review "
                         "or with no match (with --apply; undoable)")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--only", help="only folders whose path contains this text")
    ap.add_argument("--process-anyway", action="store_true",
                    help="tag albums even when an Ignore/Skip rule excludes them (this run only)")
    ap.add_argument("--into", help="albums come from outside the library (an inbox) and Picard files them "
                                   "into this library folder, which must be Picard's 'Move files to' folder")
    ap.add_argument("--retag", action="store_true",
                    help="re-tag albums that carry another release (with validate.py's fixes.db)")
    ap.add_argument("--allow-old-matches", action="store_true",
                    help="also tag matches made by an older version of the matcher")
    ap.add_argument("--batch", type=int, default=10, help="albums per Picard run (default 10)")
    ap.add_argument("--settle", type=int, default=10, help="seconds Picard is left idle before saving")
    ap.add_argument("--undo", help="restore a run: its files' tags, names and folders")
    ap.add_argument("--tidy", metavar="RUN_ID", help="move the scans of an earlier run's albums into covers/")
    ap.add_argument("--no-tidy", action="store_true",
                    help="leave scans where Picard put them (default: move them into covers/)")
    args = ap.parse_args()
    out_dir = Path(args.out_dir).expanduser()
    out_dir.mkdir(parents=True, exist_ok=True)

    if args.undo:
        return undo(out_dir, args.undo)
    if not picard_preserves_tagmender(args.picard_ini):
        print(f"Note: add '{TAGMENDER_TAG.lower()}, {TAGMENDER_REASON.lower()}' to Picard's Options > Tags > "
              "'Preserve these tags', or Picard removes them whenever it saves these files again.")
    if args.mark_unresolved:
        return mark_unresolved(args, out_dir)
    if args.tidy:
        return tidy_run(out_dir, args.tidy, args.library_root or picard_move_target(args.picard_ini))

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
        msg = (f"Picard moves tagged files to {target}, but {len(paths)} of these album(s) are in another "
               f"library, e.g.\n  {paths[0]}\nTagging them now would move them into {target}. In Picard: "
               "Options > File Naming > 'Move files to this directory when saving': set it to the library "
               "being tagged (and back afterwards), close Picard, and run again.")
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
        print(f"Dry run, nothing changed. Plan: {plan}\nPicard will move files under: {root}\n"
              "Run again with --apply (start with --limit 2), with Picard closed.")
        return 0

    if not albums:
        for p, why in skipped:
            print(f"  skipped: {p}\n      {why}")
        print("Nothing to tag.")
        return 0

    running = picard_running()
    if running:
        sys.exit("Picard is open. Close it first: its commands would go to the open Picard and "
                 f"save whatever else is loaded there.\n  found: {running}")
    if session_restore_on(args.picard_ini):
        sys.exit("Picard is set to start with your last session (Options > Sessions). Turn that off "
                 "for this run: its files would be saved along with the albums being tagged.")
    if not root or not Path(root).is_dir():
        sys.exit(f"Library folder Picard moves files to not found: {root!r}. Pass --library-root.")

    run_id = time.strftime("%Y%m%d-%H%M%S")
    run_dir, n = out_dir / f"run-{run_id}", 1
    while run_dir.exists():                    # two runs in the same second
        n += 1
        run_dir = out_dir / f"run-{run_id}-{n}"
    run_id = run_dir.name[len("run-"):]
    blob_dir = run_dir / "blobs"
    blob_dir.mkdir(parents=True)
    log = open(run_dir / "picard.log", "w", encoding="utf-8")
    journal = []
    results = []
    print(f"Run {run_id}: backups and log in {run_dir}")
    try:
        for start in range(0, len(albums), args.batch):
            batch = []
            for a in albums[start:start + args.batch]:        # 0: the match is still the matcher's pick
                why = recheck(args.matches_db, a, args.allow_old_matches)
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
                a["records"] = []
                for p in a["files"]:
                    before = read_ids(p)                     # a retag: the old release's IDs are there
                    rec = {"path": p, "key": audio_key(p), "backup": backup_tags(p, blob_dir),
                           "old_trackid": before["trackid"], "old_discogs": before["discogs"],
                           "old_albumid": before["albumid"]}
                    a["records"].append(rec)
                # every album prepared so far in this batch (later ones have no backup yet)
                with open(run_dir / "journal.json", "w") as fh:
                    json.dump(journal + [{"album": x["path"], "mbid": x["mbid"], "files": x["records"],
                                          "extras": x["extras"]} for x in batch if "records" in x], fh)
                for p in a["files"]:
                    write_albumid(Path(p), a["mbid"])
                    # until Picard saves it, the album is not done: a seed left behind by a run
                    # that stopped is never taken for "already tagged with this release"
                    write_tagmender(Path(p), "Seeded", "release ID written; Picard has not saved yet")
            journal += [{"album": a["path"], "mbid": a["mbid"], "files": a["records"], "extras": a["extras"],
                         "moves": a.setdefault("moves", [])} for a in batch]
            started = time.time()
            n0 = start + 1
            print(f"[{n0}-{start + len(batch)}/{len(albums)}] Picard: tagging {len(batch)} album(s)…", flush=True)
            rc = run_picard(args.picard, [a["path"] for a in batch], args.settle,
                            timeout=600 + 120 * len(batch), log=log)
            cands = changed_dirs(root, started) + [Path(a["path"]) for a in batch]
            full = None
            for a in batch:                                   # 4: verify
                v = verify(a, a["records"], cands)
                if any("new place not found" in x for x in v["not_saved"]):
                    full = full or changed_dirs(root, started, full=True)
                    v = verify(a, a["records"], full + [Path(a["path"])])
                a["verify"] = v
                n = len(a["records"])
                state = ("tagged" if v["saved"] == n and v["with_discogs"] == n else
                         "tagged, no Discogs data" if v["saved"] == n else
                         "partly saved" if v["saved"] else "not saved")
                new_dirs = sorted({str(Path(p).parent) for p in v["new_paths"].values()})
                results.append({"album": a["path"], "state": state, "saved": v["saved"], "files": n,
                                "discogs": v["with_discogs"], "now_in": "; ".join(new_dirs),
                                "release": a["mbid"], "not_saved": "; ".join(v["not_saved"][:3])})
                print(f"    {state:24s} {Path(a['path']).parent.name} | {Path(a['path']).name}"
                      + (f"  ->  {new_dirs[0]}" if new_dirs and new_dirs[0] != a["path"] else ""))
                for rec in a["records"]:
                    rec["new_path"] = v["new_paths"].get(rec["path"], "")
                    if not rec["new_path"] and Path(rec["path"]).exists():
                        try:                          # not saved: take the seeded release ID out again
                            restore_tags(Path(rec["path"]), rec["backup"], blob_dir)
                        except Exception as exc:
                            print(f"    could not take the release ID out of {rec['path']}: {exc}")
                if v["saved"]:                                # 4b: TAGMENDER=Resolved / Unresolved
                    status = "Resolved" if state.startswith("tagged") else "Unresolved"
                    reason = "" if status == "Resolved" else f"Picard saved {v['saved']} of {n} files"
                    for rec in a["records"]:
                        if rec["new_path"]:
                            try:
                                write_tagmender(rec["new_path"], status, reason)
                            except Exception as exc:
                                print(f"    could not write {TAGMENDER_TAG} to {rec['new_path']}: {exc}")
                if state.startswith("tagged") and new_dirs and not args.no_tidy:   # 5: artwork
                    album_dir = os.path.commonpath(new_dirs) if len(new_dirs) > 1 else new_dirs[0]
                    try:
                        # extend, not replace: the journal holds this same list
                        a["moves"].extend(tidy_artwork(a["path"], album_dir, a["extras"]))
                    except OSError as exc:
                        print(f"    could not tidy the artwork: {exc}")
                    if a["moves"]:
                        print(f"    moved {len(a['moves'])} scan/artwork file(s) into covers/")
            with open(run_dir / "journal.json", "w") as fh:
                json.dump(journal, fh)
            if rc not in (0, None):
                print(f"    (Picard exited with {rc}; see {run_dir / 'picard.log'})")
    except KeyboardInterrupt:
        print("\nInterrupted. Albums not saved still carry only the seeded release ID; "
              f"--undo {run_id} restores everything in this run.")
    finally:
        with open(run_dir / "journal.json", "w") as fh:
            json.dump(journal, fh)
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
    albums = unresolved_albums(args.matches_db, args.only)
    todo = []
    for path, reason in albums:
        files = audio_files(Path(path))
        if not files or all(read_ids(p)["trackid"] for p in files):
            continue                                     # tagged by hand since
        if all(read_tagmender(p) == ("Unresolved", reasons.encode(reason)) for p in files):
            continue
        if any(read_tagmender(p)[0] == "Resolved" for p in files):
            continue                                     # tagged from the web since
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
    try:
        for path, reason, files in todo:
            recs = []
            for p in files:
                rec = {"path": str(p), "new_path": str(p), "key": "", "backup": backup_tags(p, blob_dir)}
                recs.append(rec)
            journal.append({"album": path, "mbid": None, "files": recs, "extras": [], "moves": []})
            with open(run_dir / "journal.json", "w") as fh:
                json.dump(journal, fh)
            for p in files:
                write_tagmender(p, "Unresolved", reason)
            done += 1
    finally:
        with open(run_dir / "journal.json", "w") as fh:
            json.dump(journal, fh)
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


def tidy_run(out_dir, run_id, root=None):
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
        moves = tidy_artwork(album["album"], album_dir, album.get("extras", []))
        album.setdefault("moves", []).extend(moves)
        total += len(moves)
        print(f"  {len(moves):3d} file(s) into covers/  {album_dir}")
    (run_dir / "journal.json").write_text(json.dumps(journal))
    print(f"Moved {total} scan/artwork file(s). --undo {run_id} still restores everything.")
    return 0


def undo(out_dir, run_id):
    run_dir = out_dir / f"run-{run_id}"
    journal = json.loads((run_dir / "journal.json").read_text())
    blob_dir = run_dir / "blobs"
    restored = failed = 0
    for album in journal:
        for src, dest in reversed(album.get("moves", [])):     # the artwork tidy, backwards
            if Path(dest).exists() and not Path(src).exists():
                Path(src).parent.mkdir(parents=True, exist_ok=True)
                shutil.move(dest, src)
        for d in {str(Path(dest).parent) for _s, dest in album.get("moves", [])}:
            _remove_empty(Path(d))
        new_dirs = {str(Path(r["new_path"]).parent) for r in album["files"]
                    if r.get("new_path") and r["new_path"] != r["path"]}
        for rec in album["files"]:
            cur = Path(rec.get("new_path") or rec["path"])
            if not cur.exists():
                failed += 1
                print(f"  not found: {cur}")
                continue
            try:
                restore_tags(cur, rec["backup"], blob_dir)
                old = Path(rec["path"])
                if cur != old:
                    old.parent.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(cur), str(old))
                restored += 1
            except Exception as exc:
                failed += 1
                print(f"  could not restore {cur}: {exc}")
        # scans, logs and cue sheets Picard moved along with the music
        for rel in album.get("extras", []):
            old = Path(album["album"]) / rel
            if old.exists():
                continue
            for d in new_dirs:
                for cand in (Path(d) / rel, Path(d) / Path(rel).name):
                    if cand.exists():
                        old.parent.mkdir(parents=True, exist_ok=True)
                        shutil.move(str(cand), str(old))
                        break
                else:
                    continue
                break
        for d in new_dirs:                               # remove the folders Picard made, if now empty
            _remove_empty(Path(d))
    print(f"Restored {restored} file(s); {failed} problem(s).")
    return 0 if not failed else 1


def _remove_empty(folder, keep_parent=False):
    for dirpath, _dirs, _names in sorted(os.walk(folder), key=lambda x: -len(x[0])):
        try:
            os.rmdir(dirpath)
        except OSError:
            pass
    if keep_parent:
        return
    try:
        os.rmdir(Path(folder).parent)                    # the artist folder, if now empty
    except OSError:
        pass


if __name__ == "__main__":
    sys.exit(main())
