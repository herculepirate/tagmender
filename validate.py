#!/usr/bin/env python3
"""validate.py - does TagMender identify albums you have ALREADY tagged correctly?

For every album folder in a library that carries a MusicBrainz or Discogs release ID,
the matcher is run BLIND: the release IDs, ISRCs, label, catalogue number, barcode,
country and date in the tags are ignored (they came from the release and would only
confirm it). What is left is what an untagged copy has: names, track lengths, the audio,
the disc TOC, the scans, and the ORIGINAL folder name (from tag_albums.py's journals
when the tagger renamed the folder). Its pick is compared with the release in the tags.
What the files say about where they came from (a shop's comment or URL) is read too.
Each check is kept with the album's audio checksums, so a renamed folder is not checked
twice, and a retagged one is checked again.

Changes nothing in the library. Uses its own state/cache (copied from the matcher's
cache on first run), so it can run while match_loop.sh runs. Resumable: run it again
and it continues; --redo starts over.

  export DISCOGS_TOKEN=...      (read -rs -p "Discogs token: " DISCOGS_TOKEN; export DISCOGS_TOKEN)
  python3 validate.py /srv/music
  python3 validate.py /srv/music --artist "Steve Roach" --limit 20

Output: <out-dir>/validation.csv (one row per album) and a summary:
  agree           the matcher picks the release in your tags (or the same pressing)
  corrects tags   the same album, another pressing, and the evidence about your copy
                  (its codes, disc ID, scans, what the files are) proves the pick: your tags
                  are the ones to fix
  other pressing  the same album, but the matcher picks another pressing of it
  equivalent / undecidable  pressings of the album tied, nothing tells them apart
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
    """The release IDs in the album's tags: (MusicBrainz release, Discogs release), the
    one most files carry."""
    return _top_ids(album_identity(folder))


def _top_ids(ident):
    mb, dg = ident["mb"], ident["dg"]
    return (mb.most_common(1)[0][0] if mb else ""), (dg.most_common(1)[0][0] if dg else "")


def mixed_ids(folder):
    """The MusicBrainz releases of an album whose files carry more than one, else [].
    Sepultura - Beneath the Remains: disc 1 tagged as the 2020 CD, disc 2 as a download."""
    return _mixed(album_identity(folder))


def _mixed(ident):
    return sorted(ident["mb"]) if len(ident["mb"]) > 1 else []


def _release_counts(folder):
    """How many files carry each MusicBrainz and each Discogs release ID (every file:
    the second disc may carry another release than the first)."""
    ident = album_identity(folder)
    return ident["mb"], ident["dg"]


# Where a file came from, as the shop or its downloader wrote it in a comment or URL tag
# ("Visit http://kinggoat.bandcamp.com", an Amazon Music or Tidal track link). It says
# what the file IS, not which database release it was tagged from, so it is read even
# though the matcher runs blind. The artist's website (Vorbis "website", ID3 WOAR) is
# not one of these tags: Picard writes it from the database. Only a tag's name is looked
# at, never its value (an ID3 URL frame's key holds the URL itself: "WOAR:https://...").
_STORES = (("Bandcamp", re.compile(r"[\w-]+\.bandcamp\.com", re.I)),
           ("Tidal", re.compile(r"tidal\.com/(?:browse/)?(?:track|album)/", re.I)),
           # Amazon Music only: a product page (/dp/) may sell the CD the files were ripped from
           ("Amazon", re.compile(r"music\.amazon\.|amazon\.[a-z.]+/(?:music/player|digital-music)", re.I)),
           ("Qobuz", re.compile(r"qobuz\.com", re.I)),
           ("iTunes", re.compile(r"(?:itunes|music)\.apple\.com", re.I)),
           ("Deezer", re.compile(r"deezer\.com", re.I)),
           ("Beatport", re.compile(r"beatport\.com", re.I)),
           ("7digital", re.compile(r"7digital\.com", re.I)),
           ("HDtracks", re.compile(r"hdtracks\.com", re.I)))
_ORIGIN_KEY = re.compile(r"comm|comment|description|\xa9cmt|url|woaf|woas|wpay|wxxx|purl|source")


def _store_names(text):
    return [name for name, rx in _STORES if rx.search(text)]


def album_identity(folder):
    """What the album's files say about themselves, read once (every file: the second disc
    may carry another release than the first):
      mb, dg        how many files carry each MusicBrainz and each Discogs release ID
      audio_key     the FLACs' own checksums of their decoded audio: the same music
                    wherever it is filed and however it is tagged
      recordings_key  the MusicBrainz recording IDs, for files without a checksum
      resolved      every file carries TAGMENDER=Resolved: a fix was already applied
      file_origin   the shops the comments and URL tags name (Bandcamp, Amazon, ...)
      whole_sectors  every file is a FLAC of whole CD sectors (44.1 kHz, samples a
                    multiple of 588), False when one is not, None when not known (no
                    FLAC, or other files too)
      files         how many audio files were read (0: none, so nothing above is known)"""
    import hashlib
    mb, dg = collections.Counter(), collections.Counter()
    md5s, recordings, marks, shops, sectors, n = [], set(), [], set(), [], 0
    for p in sorted(Path(folder).rglob("*")):
        if p.suffix.lower() not in ma.AUDIO_EXT:
            continue
        try:
            f = mutagen.File(p)
        except Exception:
            continue
        if not f:
            continue
        n += 1
        md5 = getattr(getattr(f, "info", None), "md5_signature", 0)
        if md5:
            md5s.append(f"{md5:032x}")
        sectors.append(_whole_sectors(getattr(f, "info", None)))
        mark = ""
        for k, v in (f.tags or {}).items():
            key = str(getattr(v, "desc", "") or k).lower().replace("----:com.apple.itunes:", "")
            key = key.replace("txxx:", "").replace(" ", "_")
            vals = getattr(v, "text", None) or (v if isinstance(v, list) else [v])
            val = vals[0] if vals else ""
            val = getattr(val, "data", val)              # an ID3 UFID frame / MP4 free-form value
            val = val.decode("utf-8", "replace") if isinstance(val, bytes) else str(val)
            if key in ("musicbrainz_albumid", "musicbrainz_album_id"):     # Vorbis / ID3 and MP4
                mb[val.strip()] += 1
            elif key in ("discogs_release_id", "discogs_releaseid"):
                dg[val.strip()] += 1
            elif key in ("musicbrainz_trackid", "musicbrainz_track_id") or str(k).lower() == "ufid:http://musicbrainz.org":
                recordings.add(val.strip())          # Vorbis/MP4 "track id" is the recording
            elif key == "tagmender":
                mark = val.strip()
            elif _ORIGIN_KEY.search(key.split(":")[0]):
                shops.update(_store_names(" ".join(str(getattr(x, "url", x)) for x in vals)))
        marks.append(mark)
    mb.pop("", None)
    dg.pop("", None)
    recordings.discard("")
    digest = lambda xs: hashlib.sha1("\n".join(sorted(xs)).encode()).hexdigest()[:20]   # noqa: E731
    return {"mb": mb, "dg": dg,
            "audio_key": digest(md5s) if md5s and len(md5s) == n else "",
            "recordings_key": digest(recordings) if recordings else "",
            "resolved": bool(marks) and all(m == "Resolved" for m in marks),
            "file_origin": "; ".join(sorted(shops)),
            "whole_sectors": False if False in sectors else (True if sectors and None not in sectors else None),
            "files": n}


def _whole_sectors(info):
    """A FLAC's length is a whole number of CD sectors (588 samples at 44.1 kHz): True or
    False; None for another file or sample rate (nothing said)."""
    samples, rate = getattr(info, "total_samples", 0), getattr(info, "sample_rate", 0)
    if not samples or rate != 44100:
        return None
    return samples % 588 == 0


def files_stamp(folder):
    """The album's audio files as they are now (name, size, time changed): a retag or a
    rename changes it, so a saved check is no longer about these files. Reads no tags."""
    import hashlib
    rows = []
    for p in sorted(Path(folder).rglob("*")):
        if p.suffix.lower() in ma.AUDIO_EXT:
            try:
                st = p.stat()
            except OSError:
                continue
            rows.append(f"{p.relative_to(folder)}\t{st.st_size}\t{st.st_mtime_ns}")
    return hashlib.sha1("\n".join(rows).encode()).hexdigest()[:20] if rows else ""


def album_folders(library, artist=None):
    """The album folders of each artist folder, however deep (in them or in Disc subfolders)."""
    for a in sorted(Path(library).iterdir()):
        if not a.is_dir() or (artist and artist.casefold() not in a.name.casefold()):
            continue
        yield from ma.album_dirs(a)            # nested folders too: Artist/Albums/<album>


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


def _kind(formats):
    """One media kind ("cd", "vinyl", "digital", "cassette") for a release's formats, or ""
    when it has none or several (a CD + LP box says nothing on its own)."""
    kinds = set(formats or ()) - {""}
    return next(iter(kinds)) if len(kinds) == 1 else ""


def _mb_group_kinds(http, mbid):
    """The media kind of every release in a MusicBrainz release's release group, or None
    when they are not all known (a group of more than one page: the others may hold
    another release of the same kind)."""
    rg = http.get_json(f"https://musicbrainz.org/ws/2/release-group?release={mbid}&fmt=json", "mb")
    rgid = next((g.get("id") for g in (rg or {}).get("release-groups") or [] if g.get("id")), None)
    if not rgid:
        return None
    data = http.get_json(f"https://musicbrainz.org/ws/2/release?release-group={rgid}"
                         f"&inc=media+labels&limit=100&fmt=json", "mb") or {}
    releases = data.get("releases") or []
    if (data.get("release-count") or 0) > len(releases):
        return None
    return [_kind({mc.media_kind(m.get("format")) for m in r.get("media") or []}) for r in releases]


def _discogs_master_kinds(http, rid):
    """The media kind of every version of a Discogs release's master (only the release
    itself when it has no master), or None when they are not all known (more than one
    page of versions)."""
    rel = http.get_json(f"https://api.discogs.com/releases/{rid}", "discogs") or {}
    if not rel.get("master_id"):
        return [_kind({mc.media_kind(f.get("name")) for f in rel.get("formats") or []})] if rel else None
    data = http.get_json(f"https://api.discogs.com/masters/{rel['master_id']}/versions?per_page=100",
                         "discogs") or {}
    versions = data.get("versions") or []
    pages = data.get("pagination") or {}
    if (pages.get("pages") or 1) > 1 or (pages.get("items") or 0) > len(versions):
        return None
    return [_kind({mc.media_kind(f) for f in (v.get("major_formats") or [v.get("format") or ""])})
            for v in versions]


def equivalent_twins(http, a, b):
    """A MusicBrainz and a Discogs release that can only be one pressing, though nothing
    links them (Cynic - ReFocus, Sunn O))) - Veils It White): the only release of its
    format in the MusicBrainz release group, the only version of that format in the
    Discogs master, and every track length the same. A MusicBrainz release that lists
    no format (Confessor - Blueprint Soul) is one only when it is the only release of
    its group and the Discogs release the only version of its master. A MusicBrainz
    release linked to ANOTHER Discogs release is never this one."""
    if not a or not b or {a.get("source"), b.get("source")} != {"mb", "discogs"}:
        return False
    mbr, dgr = (a, b) if a["source"] == "mb" else (b, a)
    if mbr.get("discogs_id") and str(mbr["discogs_id"]) != str(dgr.get("id")):
        return False
    kind = _kind(mbr.get("formats"))
    if mbr.get("formats") and not kind:
        return False                       # several formats (a CD + LP box): not one pressing
    if kind and kind != _kind(dgr.get("formats")):
        return False
    x, y = _secs(mbr), _secs(dgr)
    if not x or len(x) != len(y) or any(v <= 0 for v in x + y) or any(abs(i - j) > 3 for i, j in zip(x, y)):
        return False
    group, master = _mb_group_kinds(http, mbr["id"]), _discogs_master_kinds(http, dgr["id"])
    if not group or not master:
        return False
    if not kind:
        return len(group) == 1 and len(master) == 1
    return group.count(kind) == 1 and master.count(kind) == 1


def _only_digital(http, picked_mb, picked_dg):
    """The pick is the album's only digital release (in its MusicBrainz release group, else
    in its Discogs master): a shop named in the files then cannot mean another one. With a
    Bandcamp self-release and a label's download both listed, the shop says the CD in the
    tags is wrong, not which download is right."""
    kinds = _mb_group_kinds(http, picked_mb) if picked_mb else (
        _discogs_master_kinds(http, picked_dg) if picked_dg else None)
    return bool(kinds) and kinds.count("digital") == 1


def _formats_clash(a, b):
    """Both releases list their media and share none: a CD and a download are never one
    pressing, whatever label, country and year they share (Bell Witch - Stygian Bough)."""
    return bool(a.get("formats") and b.get("formats") and not set(a["formats"]) & set(b["formats"]))


def _sector_aligned(secs):
    """Every track length is a whole number of CD sectors (1/75 s, 40/3 ms), within the
    1 ms a database rounds lengths to. A download cut from the CD master has them too
    (Voivod - Lost Machine - Live: 470.600 s is 20753460 samples), so whole sectors in
    the files cannot tell such a download from the CD."""
    if not secs or any(x <= 0 for x in secs):
        return False
    sector = 40 / 3
    return all(min(ms % sector, sector - ms % sector) <= 1.0 for ms in (round(x * 1000) for x in secs))


def _sectors_cannot_tell(rec):
    """The tags hold a download whose own lengths are whole CD sectors and the pick is a
    CD: files of whole sectors fit both."""
    return bool(rec.get("tags_sector_aligned") and _tagged_kind(rec) == "digital"
                and "cd" in (rec.get("picked_kinds") or ()))


def _tie_broken(out, mb_id, dg_id, rec, mine=None, other=None):
    """The pick of a pressing is a tie-break between pressings the tagged one is among:
    the matcher left them tied (album_only) with yours in the tie; or it broke the tie on
    your country order or the label's first pressing, with yours named among the tied
    ones (_named_in_tie()), or like them (_tied_with()) and of the pick's format; or whole CD sectors chose a CD over a
    download whose lengths are whole sectors too. One of two entries of the SAME
    pressing ("listed twice") is no tie with yours: yours is neither entry."""
    tied = (out.get("album_only") or {}).get("tied")
    if tied:
        ids = {str(t.get("id")) for t in tied} | {str(t.get("mb_id")) for t in tied if t.get("mb_id")}
        return bool((mb_id and mb_id in ids) or (dg_id and str(dg_id) in ids))
    notes = "; ".join((out.get("best") or {}).get("notes") or [])
    sectors = "CD (tracks are whole CD sectors" in notes and "the folder's rip log" not in notes
    if any(f in notes for f in FALLBACKS if f != "same pressing listed twice"):
        if _named_in_tie(notes, mb_id, dg_id, mine):
            return True
        tagged = _tagged_kind(rec)
        return _tied_with(mine, other) and (not tagged or tagged in (rec.get("picked_kinds") or ())
                                            or (sectors and _sectors_cannot_tell(rec)))
    return sectors and _sectors_cannot_tell(rec)


def _named_in_tie(notes, mb_id, dg_id, mine):
    """The matcher's tie-break note names the tagged release among those it passed over
    ("pressing chosen by ... (not on 3214272, 3209926, 11216059)")."""
    m = re.search(r"\(not on ([^)]*)\)", notes)
    if not m:
        return False
    named = {x.strip() for x in m.group(1).split(",")}
    return bool(named & {str(x) for x in (mb_id, dg_id, (mine or {}).get("discogs_id")) if x})


def _tied_with(mine, other):
    """The tagged release could have been one of the tied pressings: as many tracks as the
    pick, every length within 3 s. A 13-track edition with a bonus track is not tied
    with the 12-track pressings the country order chose between (the tie note lists at
    most three of them, and only of one database, so the lengths are checked too)."""
    a, b = _secs(mine or {}), _secs(other or {})
    return bool(a and len(a) == len(b) and all(x > 0 for x in a + b)
                and all(abs(x - y) <= 3 for x, y in zip(a, b)))


_IMAGE_EXT = {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".tif", ".tiff", ".webp", ".pdf"}


def _has_scans(folder):
    """More than a front cover: scans of the back, the disc or the booklet may print a
    code the matcher did not read (Alcest - Les Voyages De L'Ame: the sticker barcode)."""
    if not folder or not Path(folder).is_dir():
        return False
    return sum(p.suffix.lower() in _IMAGE_EXT for p in Path(folder).rglob("*")) > 1


def _disc_id_tells(out, mb_id, picked_mb, rec, http):
    """A disc ID of the files (rip log, cue sheet or the audio) is attached to one of the
    two MusicBrainz releases and not to the other: the TOC tells them apart (Mournful
    Congregation - The Incubus Of Karma: only the Japanese release has it). Two
    physical releases only: a download carries no disc ID, and a Discogs-only pick has
    no MusicBrainz release to carry one."""
    if not (mb_id and picked_mb) or _tagged_kind(rec) == "digital" or "digital" in (rec.get("picked_kinds") or ()):
        return False
    for did, toc, exact in out.get("discids") or []:
        if not exact:
            continue
        data = http.get_json(f"https://musicbrainz.org/ws/2/discid/{did}?toc={toc.replace(' ', '+')}"
                             f"&cdstubs=no&fmt=json", "mb") or {}
        on = {r.get("id") for r in data.get("releases") or []}
        if (mb_id in on) != (picked_mb in on):
            return True
    return False


def _tie_only(out, mb_id, dg_id, rec, http, mine=None, other=None):
    """The pick of a pressing is a tie-break (_tie_broken()) AND nothing about the copy
    could tell the pressings apart: no scans beyond a front cover, no disc ID of the files
    that only one of them carries (Cult of Luna - Salvation: GB and US on one disc ID, a
    front cover). A tie-break the copy could have decided is a matcher miss, not a tie."""
    if not _tie_broken(out, mb_id, dg_id, rec, mine, other):
        return False
    b = out.get("best") or {}
    picked_mb = b.get("mb_id") or (b.get("id") if b.get("source") == "mb" else "")
    return not (_has_scans(rec.get("folder")) or _disc_id_tells(out, mb_id, picked_mb, rec, http))


def verdict(out, mb_id, dg_id, http, cache, rec=None):
    """Compare the blind pick with the release in the tags. The tags are not taken as
    the truth: when the pick rests on evidence about the copy (evidence_decided()) and
    the tags hold another pressing, the pick "corrects tags". rec, when given, is the
    album's record: its file_origin is read, the media kinds of both releases are added."""
    rec = {} if rec is None else rec
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
    why = "; ".join(out.get("why") or [])
    if not same and (mb_id or dg_id):
        # the same pressing listed twice, or MusicBrainz and Discogs entries of one pressing
        mine, other = tagged_release(http, mb_id, dg_id, cache), picked_release(http, b, cache)
        rec["tags_kind"] = _kind((mine or {}).get("formats"))
        rec["picked_kinds"] = sorted((other or {}).get("formats") or ())
        # whole sectors in the download's lengths, and in its files when they were read
        # (a digital release may list the CD's lengths: Cynic - Re-Traced)
        rec["tags_sector_aligned"] = (rec["tags_kind"] == "digital" and _sector_aligned(_secs(mine))
                                      and rec.get("whole_sectors") is not False)
        if rec.get("file_origin") and rec["picked_kinds"] == ["digital"]:
            rec["picked_only_digital"] = _only_digital(http, picked_mb, picked_dg)
        if mine and other and not _formats_clash(mine, other) and (
                mc.same_pressing(mine, other) or mc.same_pressing(other, mine)):
            same = True
        elif equivalent_twins(http, mine, other):
            return (("agree" if st == "auto" else "unsure (would pick yours)"),
                    "the only release of this format in MusicBrainz and in Discogs: one pressing")
    if not same and other_album(mine, other) is False:
        # the same album, another pressing of it: the evidence about your copy may say the
        # tags are wrong, nothing may tell the pressings apart, or the matcher read it wrongly
        decided = dict(rec, status=st, notes="; ".join(b.get("notes") or []),
                       picked=f"{b.get('source', '')}:{b.get('id', '')}", picked_mb_twin=b.get("mb_id") or "")
        if st == "auto" and evidence_decided(decided):
            return "corrects tags", _decided_by(decided)
        if _tie_only(out, mb_id, dg_id, rec, http, mine, other):
            return "equivalent / undecidable", why or "a tie-break between pressings nothing tells apart"
        if _tie_broken(out, mb_id, dg_id, rec, mine, other):
            why = "; ".join(x for x in (why, "tie-break") if x)      # the copy could have decided it
        return ("other pressing" if st == "auto" else "unsure"), why
    if st == "auto":
        return ("agree", "") if same else ("differs", "")
    return ("unsure (would pick yours)" if same else "unsure"), why


def _decided_by(rec):
    """Why a pick corrects the tags, in a few words."""
    if _discogs_only(rec):
        return "a pressing only Discogs lists, proven by your scans or codes"
    if rec.get("file_origin") and rec.get("picked_kinds") == ["digital"] and rec.get("picked_only_digital"):
        return f"the files came from {rec['file_origin']}; the tags hold a {rec.get('tags_kind')} release"
    return "the evidence about your copy points to the pick"


FALLBACKS = ("preferred country order", "same pressing listed twice", "label's original pressing")


# What identifies YOUR copy: its own numbers (barcode, catalogue number, disc ID), the
# country printed on it, a catalogue number or country your folder name states.
_STRONG = re.compile(r"barcode (?:\(OCR\) )?agrees|pressing chosen by barcode|catalogue number(?: \((?:OCR|folder name)\))? agrees|"
                     r"pressing chosen by catalogue number|disc ID|made in \S.* on the scans|"
                     r"left out: folder (?:catalogue number|name says [A-Z]{2}\b)")
# What the files are (a CD rip, a download, a vinyl rip): decides only against a tag whose
# format contradicts it (Jesu - Terminus: a CD rip tagged as the download).
_FORMAT = re.compile(r"pressing chosen by (?:digital release \((?:hi-res|lossless|bought from)|"
                     r"CD \((?:tracks are whole|the folder's rip log)|"
                     r"physical release)|left out: folder name says (?:cd|vinyl)")


def _picked_kind(notes):
    m = re.search(r"pressing chosen by (digital release|CD|physical release)", notes)
    if m:
        return {"digital release": "digital", "CD": "cd", "physical release": "physical"}[m.group(1)]
    m = re.search(r"left out: folder name says (cd|vinyl)", notes)
    return m.group(1) if m else ""


def _tagged_kind(rec):
    """The media of the release in the tags: from the record, else from its summary
    ("title | country | date | label | catno | CD/CD")."""
    if rec.get("tags_kind"):
        return rec["tags_kind"]
    return mc.media_kind(((rec.get("tags_release") or "").split(" | ") or [""])[-1].split("/")[0])


def _discogs_only(rec):
    """The pick is a pressing only Discogs lists (no MusicBrainz release of it)."""
    return str(rec.get("picked") or "").startswith("discogs:") and not rec.get("picked_mb_twin")


def evidence_decided(rec):
    """The pick rests on evidence about YOUR copy, strong enough to replace a tag you
    already have: its barcode, catalogue number or disc ID, the country printed on it, or
    a catalogue number or country your folder name states; or what the files are (CD rip,
    download, a shop named in their comments when the pick is the album's only download)
    when the tagged release is of another format. Not: your country order, one of two identical entries, the closest track
    lengths, a label alone (licensed editions print the licensor's), or a "digital"
    guessed from a folder name ("[RM 2015]"). A pressing only Discogs lists is never
    decided by a disc ID: the disc ID belongs to a MusicBrainz release, the one in the
    tags (King Diamond - Deadly Lullabyes: Massacre shares the Metal Blade disc's TOC)."""
    notes = rec.get("notes") or ""
    if rec.get("status") != "auto" or any(f in notes for f in FALLBACKS):
        return False
    tagged = _tagged_kind(rec)
    picked = set(rec.get("picked_kinds") or ())
    if rec.get("file_origin") and picked and "digital" not in picked:
        return False                     # the files came from a shop; a physical pick is wrong
    strong = {m.group(0) for m in _STRONG.finditer(notes)}
    proves = "proves the album" in notes
    if _discogs_only(rec):
        strong.discard("disc ID")
        proves = proves and "disc ID proves the album" not in notes
    if strong or proves:
        return True
    if (rec.get("file_origin") and picked == {"digital"} and rec.get("picked_only_digital")
            and tagged and tagged != "digital"):
        return True                      # A Swarm Of The Sun: Amazon files tagged as the CD
    formats = [m.group(0) for m in _FORMAT.finditer(notes)]
    if _sectors_cannot_tell(rec):        # Voivod - Lost Machine: the download is whole sectors too
        formats = [f for f in formats if "tracks are whole" not in f]
    if formats:
        kind = _picked_kind(notes)
        if tagged and kind and not (kind == tagged or (kind == "physical" and tagged != "digital")):
            return True
    return False


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


def export_fixes(state, out_dir, current, approved=None, only_approved=False):
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
        elif (not only_approved and r.get("verdict") in ("differs", "other pressing", "corrects tags")
              and evidence_decided(r) and r.get("version") == current and still_as_checked(r)):
            fixes.append(r)
        elif (not only_approved and r.get("verdict") == "mixed releases" and r.get("version") == current
              and r.get("status") == "auto" and still_as_checked(r)
              and (evidence_decided(r) or (r.get("picked_mb_twin") or "") in (r.get("tags_mixed") or []))):
            fixes.append(r)                            # the whole album to the one release
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


def applied_since(rec):
    """The album's files carry TAGMENDER=Resolved now, but did not when it was checked:
    a fix was applied after the check, and the check is about the old tags."""
    folder = rec.get("folder") or ""
    return bool(not rec.get("applied") and folder and Path(folder).is_dir()
                and album_identity(folder)["resolved"])


def still_as_checked(rec):
    """The album is where it was checked and its files are as they were: a fix exported
    from the check is about these tags (not a folder renamed or deleted since, nor files
    retagged or a fix applied since)."""
    folder = rec.get("folder") or ""
    return bool(folder and Path(folder).is_dir() and not changed_since(rec, folder) and not applied_since(rec))


def changed_since(rec, path):
    """The album's files were retagged or renamed after it was checked (an older record
    without a stamp counts as unchanged)."""
    return bool(rec.get("files_stamp") and Path(path).is_dir() and files_stamp(path) != rec["files_stamp"])


def moved_records(state):
    """Saved checks whose folder is gone, by the album's audio (FLAC checksums, else the
    recording IDs): when the album turns up under another folder (renamed, re-filed) the
    old check is replaced by a new one there, never counted twice."""
    out = {}
    for p, row in state.execute("SELECT path, row FROM validation").fetchall():
        if Path(p).exists():
            continue
        r = json.loads(row)
        for k in ("audio_key", "recordings_key"):
            if r.get(k):
                out[f"{k}:{r[k]}"] = p
    return out


def forget_moved(state, moved, ident):
    """The folder the album was checked under before, when its record is in `moved`; that
    record is deleted."""
    for k in ("audio_key", "recordings_key"):
        old = ident.get(k) and moved.pop(f"{k}:{ident[k]}", None)
        if old:
            for key in [key for key, path in moved.items() if path == old]:
                del moved[key]
            state.execute("DELETE FROM validation WHERE path=?", (old,))
            return old
    return ""


def present_rows(state, lib):
    """The saved checks of albums still in the library, and those whose folder is gone
    (renamed, moved or deleted since the check): those are left out of the list. A check
    of a folder outside this library is kept."""
    rows = [json.loads(r) for (r,) in state.execute("SELECT row FROM validation ORDER BY path")]
    lib = Path(lib)
    gone = [r for r in rows if lib in Path(r["folder"]).parents and not Path(r["folder"]).exists()]
    return [r for r in rows if r not in gone], gone


def settled(state, current):
    """Albums needing no new check: they agree (or have nothing to compare), or the
    current matcher already checked them, and their files have not changed since."""
    done = set()
    for p, row in state.execute("SELECT path, row FROM validation").fetchall():
        r = json.loads(row)
        if changed_since(r, p):
            continue                   # retagged or renamed since: the check is stale
        if r.get("verdict") == "agree" and not r.get("ids_checked"):
            # checked when only the first files' release IDs were read: an album whose other
            # discs carry another release is checked again
            if Path(p).is_dir() and mixed_ids(p):
                continue
            r["ids_checked"] = True
            state.execute("UPDATE validation SET row=? WHERE path=?", (json.dumps(r), p))
        if r.get("verdict") == "not tagged" and not r.get("files_stamp"):
            continue                   # an old record that read no files: perhaps tagged now
        # (a "not tagged" album is matched too, so a new matcher checks it again)
        if r.get("verdict") in ("agree", "ignored") or r.get("version") == current:
            done.add(p)
    state.commit()
    return done


def ocr_setting(args):
    """on, off or auto: --no-ocr, else --ocr, else the default profile's "ocr" setting."""
    if getattr(args, "no_ocr", False):
        return "off"
    if getattr(args, "ocr", None):
        return ma.ocr_mode(args.ocr)
    try:
        import profiles
        return ma.ocr_mode(profiles.default().get("ocr", "auto"))
    except Exception:                  # a broken Picard.ini or profile never stops a validation run
        return "auto"


def clue_settings():
    """(acoustid mode, use gnudb) from the default profile, as a tagmender run uses them
    (unsure, on when the profile cannot be read)."""
    try:
        import profiles
        prof = profiles.default()
        return ma.acoustid_mode(prof.get("acoustid", "unsure")), prof.provider_on("web", "gnudb")
    except Exception:                  # a broken Picard.ini or profile never stops a validation run
        return "unsure", True


def ocr_ready(ocr):
    """The OCR mode to run with: on stops the run when OCR cannot work (it was asked for);
    auto then notes it and goes on without; off is not checked."""
    if ocr == "off":
        return ocr
    try:
        ma.check_ocr_ready()
    except SystemExit as exc:
        if ocr == "on":
            raise
        print(f"Note: scans are not read (ocr: auto): {exc}", file=sys.stderr)
        return "off"
    return ocr


def _matched(out):
    """The matcher's answer as a record's fields (tagged and untagged albums alike)."""
    b = out.get("best") or {}
    return dict(status=out.get("status"),
                picked=f"{b.get('source', '')}:{b.get('id', '')}" if b else "",
                picked_mb_twin=b.get("mb_id", ""),
                picked_artist=b.get("artist", ""), picked_title=b.get("title", ""),
                picked_release=" | ".join(str(b.get(k) or "") for k in ("title", "country", "label", "year")),
                confidence=b.get("confidence", ""),
                why="; ".join(out.get("why") or [])[:500],
                notes="; ".join(b.get("notes") or [])[:500])


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("library")
    ap.add_argument("--out-dir", default=None, help="default: ~/Downloads/validate_<library name>")
    # (every saved-lookups folder under ~/Downloads/matching*: matching, matching_music, ...)
    ap.add_argument("--cache", nargs="*",
                    default=sorted(glob.glob(os.path.expanduser("~/Downloads/matching*/match_state.db"))))
    ap.add_argument("--journals", nargs="*", default=None,
                    help="default: ~/Downloads/tagging/run-*/journal.json")
    ap.add_argument("--plugin-dir", default=ma.DEFAULT_PLUGIN_DIR)
    ap.add_argument("--artist")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--ocr", choices=ma.OCR_MODES, default=None,
                    help="OCR of scans: auto (folders with scans or a PDF), on, off; "
                         "default: the profiles' ocr setting (auto)")
    ap.add_argument("--no-ocr", action="store_true", help="the same as --ocr off")
    ap.add_argument("--redo", action="store_true")
    ap.add_argument("--approved", default=None,
                    help="albums you approved for a retag (folder[TAB]mb:<MBID>|discogs:<id> per line); "
                         "default: approved.tsv in the output folder, when it exists")
    ap.add_argument("--only-approved", action="store_true",
                    help="with --export-fixes: only the albums in the approved list")
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
    cfg.update(ma.picard_release_preferences())
    dm = ma.load_plugin(args.plugin_dir)
    ma._PLUGIN_STAMP = ma._script_version(ma.plugin_files(dm))[:6]
    ocr = ocr_ready(ocr_setting(args))
    if args.export_fixes:
        approved = read_approved(args.approved or out_dir / "approved.tsv")
        path, fixes = export_fixes(state, out_dir, ma.result_version(), approved, args.only_approved)
        for r in fixes:
            print(f"  {r['folder']}\n      tags {r.get('tags_release') or r.get('tags_mb')}\n      ->   {r.get('picked_release')}"
                  f"  ({r.get('picked')})")
        print(f"{len(fixes)} album(s) to re-tag: {path}\n"
              f"  python3 tag_albums.py --matches-db {path} --retag            # preview, then --apply\n"
              f"  python3 web_tag.py --matches-db {path} --library {lib}      # Discogs-only ones")
        return
    http = ma.Http(state, cfg, token)
    if args.journals is None:               # looked up only when not given (tests give their own)
        args.journals = sorted(glob.glob(os.path.expanduser("~/Downloads/tagging/run-*/journal.json")))
    names = original_names(state, args.journals)
    acoustid, use_gnudb = clue_settings()
    if use_gnudb and ma.gnudb_problem():
        print(f"Note: {ma.gnudb_problem()}", file=sys.stderr)        # skipped, said once
    if acoustid == "always" and not ma.fingerprint_ready():
        print("Note: acoustid: always is set, but fingerprints are off (needs fpcalc and an AcoustID key)",
              file=sys.stderr)
    margs = types.SimpleNamespace(ocr=ocr, max_images=15, process_anyway=False, blind=True,
                                  acoustid=acoustid, use_gnudb=use_gnudb)
    # Albums already checked are skipped, except those that did not agree and were checked
    # by an older matcher: after a fix they are checked again (validate_loop.sh relies on it).
    current = ma.result_version()
    done = settled(state, current)
    moved = moved_records(state)
    folders = [d for d in album_folders(lib, args.artist)]
    if args.limit:
        folders = folders[:args.limit]
    print(f"{len(folders)} album folders; {len(done & {str(d) for d in folders})} already checked "
          f"(matcher {current})")
    counts, untagged = collections.Counter(), collections.Counter()
    for n, d in enumerate(folders, 1):
        if str(d) in done:
            continue
        if not Path(d).is_dir():                          # renamed or deleted since it was listed
            print(f"[{n}/{len(folders)}] - gone {d}", flush=True)
            counts["gone"] += 1
            continue
        ident = album_identity(d)
        mb_id, dg_id = _top_ids(ident)
        moved_from = forget_moved(state, moved, ident)
        original = names.get(str(d)) or str(d)
        artist_folder = Path(d).relative_to(lib).parts[0]          # also for Artist/Albums/<album>
        row = {"FullPath": str(d), "AlbumArtistFolder": artist_folder, "AlbumFolder": Path(original).name,
               "Status": "untagged"}
        rec = {"folder": str(d), "original_folder": Path(original).name, "tags_mb": mb_id, "tags_discogs": dg_id,
               "version": current, "checked": round(time.time()), "ids_checked": True,
               "audio_key": ident["audio_key"], "recordings_key": ident["recordings_key"],
               "files_stamp": files_stamp(d), "file_origin": ident["file_origin"],
               "whole_sectors": ident["whole_sectors"]}
        if ident["resolved"]:
            rec["applied"] = True          # TAGMENDER=Resolved: the tags are a fix already applied
        if moved_from:
            rec["moved_from"] = moved_from
        if not ident.get("files", 1):
            rec.update(verdict="no audio")             # nothing read: neither tagged nor untagged
        elif not mb_id and not dg_id:
            rec.update(verdict="not tagged")
            # matched as an untagged folder is in production: not blind, there are no IDs to
            # hide, and the files' label, catalogue number and barcode are what it has
            try:
                out = ma.process(row, http, cfg, dm, types.SimpleNamespace(**{**vars(margs), "blind": False}))
                rec.update(_matched(out))
                untagged[out.get("status") or "error"] += 1
            except ma.AuthError as exc:
                sys.exit(f"\nDiscogs: {exc}")
            except Exception as exc:                       # one album never stops the run
                rec.update(status="error", why=f"{type(exc).__name__}: {exc}")
                untagged["error"] += 1
        else:
            try:
                out = ma.process(row, http, cfg, dm, margs)
                v, why = verdict(out, mb_id, dg_id, http, {"dm": dm}, rec)
                mixed = _mixed(ident)
                if mixed:                                  # one album, several releases in its files
                    v, why = "mixed releases", f"the files carry {len(mixed)} releases: {', '.join(mixed)}"
                rec.update(_matched(out), verdict=v, why=why)
                if mixed:
                    rec["tags_mixed"] = mixed
                if v in ("differs", "other pressing", "corrects tags", "equivalent / undecidable", "unsure",
                         "no match", "mixed releases") and mb_id:
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
        mark = {"agree": "✓", "differs": "✗", "other pressing": "≈", "not tagged": "·",
                "mixed releases": "✗", "corrects tags": "!", "equivalent / undecidable": "=",
                "no audio": "-"}.get(rec["verdict"], "?")
        print(f"[{n}/{len(folders)}] {mark} {rec['verdict']:<26} {d.parent.name} | {d.name}"
              + (f"\n      tags {mb_id or dg_id}  vs  picked {rec.get('picked')}"
                 if rec["verdict"] in ("differs", "other pressing", "corrects tags", "mixed releases") else ""),
              flush=True)

    rows, gone = present_rows(state, lib)
    fields = ["verdict", "folder", "original_folder", "tags_mb", "tags_discogs", "tags_release",
              "tags_contradict_folder", "picked", "picked_mb_twin", "picked_release", "confidence",
              "status", "why", "notes", "file_origin", "applied", "moved_from"]
    with open(out_dir / "validation.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(sorted(rows, key=lambda r: (r["verdict"] != "differs", r["verdict"], r["folder"])))
    total = collections.Counter(r["verdict"] for r in rows)
    tagged = sum(v for k, v in total.items() if k not in ("not tagged", "no audio"))
    print("\nSummary (all albums checked so far):")
    for k, v in total.most_common():
        print(f"  {k:<28} {v}")
    if tagged:
        print(f"Agreement: {total['agree'] + total['unsure (would pick yours)']} of {tagged} tagged albums "
              f"pick your release ({total['agree']} sure); {total['other pressing']} pick another pressing "
              f"of the same album; {total['differs']} pick another album.\n"
              f"  {total['corrects tags']} pick a pressing the evidence proves (your tags hold another); "
              f"{total['equivalent / undecidable']} are tied between pressings nothing tells apart.")
    if sum(untagged.values()):
        print("not tagged (this run): " + ", ".join(f"{untagged[k]} {k.replace('nomatch', 'no match')}" for k in
                                                     ("auto", "review", "album_only", "nomatch", "error")))
    if counts["gone"]:
        print(f"{counts['gone']} listed folder(s) no longer exist; not checked")
    for host, note in ma.outage_notes(http).items():
        print(f"Note: {note}", file=sys.stderr)
    if gone:
        print(f"{len(gone)} checked folder(s) are gone (renamed, moved or deleted); left out of the list")
    print(f"List: {out_dir / 'validation.csv'}")


if __name__ == "__main__":
    main()
