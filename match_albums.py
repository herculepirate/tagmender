#!/usr/bin/env python3
"""match_albums.py - phase 2: find the MusicBrainz / Discogs release for albums
that have no IDs yet. READ-ONLY with respect to your music library.

Input : the CSV from audit_untagged.py (Status = untagged by default).
Output: three CSVs in --out-dir
          matches_auto.csv     confident matches (safe to apply in phase 3)
          matches_review.csv   plausible but not certain; files left untouched
          matches_nomatch.csv  nothing plausible found
        plus match_state.db (resumable progress + HTTP cache).

Order of lookup: MusicBrainz first; Discogs only when MusicBrainz is not
already an auto match. Scoring lives in match_core.py.

Setup:
  export DISCOGS_TOKEN=...        # discogs.com/settings/developers
  python3 match_albums.py ~/Downloads/audit_ambient_music.csv --sample 100

Optional OCR (barcodes + printed text on back/disc scans):
  sudo apt install tesseract-ocr libzbar0 python3-pil python3-pyzbar
  ... --ocr
"""
import argparse
import csv
import json
import os
import random
import re
import shutil
import sqlite3
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import match_core as mc  # noqa: E402

try:
    import mutagen
    from mutagen.id3 import ID3
    from mutagen.mp4 import MP4
except ImportError:
    sys.exit("mutagen is required: pip install mutagen")

def _script_version(plugin_files=()):
    """Changes whenever the matching code changes, including the Discogs Enrichment
    plugin code it uses. Results made by an older version are re-evaluated, so a fix
    reaches albums already marked auto."""
    import hashlib
    h = hashlib.md5()
    for path in [HERE / "match_core.py", HERE / "match_albums.py", *plugin_files]:
        try:
            h.update(Path(path).read_bytes())
        except OSError:
            pass
    return h.hexdigest()[:12]


def plugin_files(dm):
    """The plugin modules the matcher's results depend on."""
    d = Path(dm.__file__).resolve().parent
    return [d / "discogs_mapping.py", d / "discogs_countries.py"]


SCRIPT_VERSION = _script_version()
_PLUGIN_STAMP = ""          # set in main() from the Discogs Enrichment code in use


def result_version():
    """Stored with every result: the matcher's version plus the plugin code's, so an
    update to either re-evaluates every album."""
    return f"{SCRIPT_VERSION}-{_PLUGIN_STAMP}" if _PLUGIN_STAMP else SCRIPT_VERSION

AUDIO_EXT = {".flac", ".mp3", ".m4a", ".mp4", ".ogg", ".opus", ".oga", ".wv",
             ".ape", ".wav", ".aif", ".aiff", ".dsf", ".dff", ".wma", ".mpc"}
USER_AGENT = "AlbumMatcher/0.1 +https://github.com/herculepirate/picard-discogs-enrichment"
DEFAULT_PLUGIN_DIR = "~/Documents/Github/picard-discogs-enrichment"


# --------------------------------------------------------------------------
# Reading what the files say
# --------------------------------------------------------------------------
def _txt(v):
    if isinstance(v, (list, tuple)):
        v = v[0] if v else ""
    if isinstance(v, bytes):
        v = v.decode("utf-8", "ignore")
    return str(v).strip()


def _names(v):
    """All values of a multi-valued artist tag, joined. The Discogs Enrichment
    plugin writes a collaboration as ONE NAME PER ARTIST (albumartist =
    ["Max Corbacho", "Bruno Sanfilippo"]); reading only the first value would
    see half the credit."""
    vals = v if isinstance(v, (list, tuple)) else [v]
    out = []
    for x in vals:
        if isinstance(x, bytes):
            x = x.decode("utf-8", "ignore")
        x = str(x).strip()
        if x and x not in out:
            out.append(x)
    return "; ".join(out)


def _int(v):
    m = re.match(r"\s*(\d+)", str(v or ""))
    return int(m.group(1)) if m else 0


_ID_KEYS = ("musicbrainzalbumid", "discogsreleaseid")
_LOSSLESS_EXT = {".flac", ".wav", ".aiff", ".aif", ".wv", ".ape", ".alac"}
CD_FRAME = 588          # a CD sector holds 588 stereo samples at 44.1 kHz
# catalogue number tag names used by Picard, JRiver (LABELNO), foobar2000, Mp3tag and others
_CATNO_KEYS = ("catalognumber", "labelno", "catalogno", "catalog", "catno", "catalog#",
               "cataloguenumber", "catalogue", "catalogueno")


def read_file(path):
    t = {"title": "", "num": 0, "disc": 0, "album": "", "artist": "", "year": 0,
         "barcode": "", "catno": "", "secs": 0.0,
         "label": "", "country": "", "media": "", "date": "",   # pressing details in the tags
         "rate": 0, "bits": 0, "samples": 0, "lossless": False,  # the audio itself
         "has_ids": False,      # already carries a MusicBrainz or Discogs release ID
         "audio_md5": 0}        # FLAC's checksum of the decoded audio (0 = unknown)
    try:
        if path.suffix.lower() in (".m4a", ".mp4"):
            f = MP4(path)
            t["secs"] = f.info.length
            for k, v in (f.tags or {}).items():
                nk = re.sub(r"[\s_\-]", "", (k.decode() if isinstance(k, bytes) else k).lower()
                            .replace("----:com.apple.itunes:", ""))
                if k == "\xa9nam": t["title"] = _txt(v)
                elif k == "\xa9alb": t["album"] = _txt(v)
                elif k == "aART": t["artist"] = _names(v)
                elif k == "\xa9ART" and not t["artist"]: t["artist"] = _txt(v)
                elif k == "\xa9day": t["year"] = _int(v[0] if v else "")
                elif k == "trkn" and v: t["num"] = v[0][0]
                elif k == "disk" and v: t["disc"] = v[0][0]
                elif nk == "barcode": t["barcode"] = _txt(v)
                elif nk in _CATNO_KEYS and not t["catno"]: t["catno"] = _txt(v)
                elif nk in _ID_KEYS: t["has_ids"] = True
            return t
        f = mutagen.File(path)
        if f is None:
            return t
        t["secs"] = getattr(f.info, "length", 0.0) or 0.0
        t["audio_md5"] = getattr(f.info, "md5_signature", 0) or 0
        t["rate"] = getattr(f.info, "sample_rate", 0) or 0
        t["bits"] = getattr(f.info, "bits_per_sample", 0) or 0
        t["samples"] = getattr(f.info, "total_samples", 0) or 0
        t["lossless"] = path.suffix.lower() in _LOSSLESS_EXT
        if t["lossless"] and not t["samples"] and t["rate"]:
            t["samples"] = round(t["secs"] * t["rate"])        # WAV/AIFF/WavPack: from the length
        tags = f.tags
        if tags is None:
            return t
        if isinstance(tags, ID3) or tags.__class__.__module__.startswith("mutagen.id3"):
            def fr(fid):
                x = tags.getall(fid)
                return _txt([str(s) for s in x[0].text]) if x else ""
            def names(fid):
                x = tags.getall(fid)
                return _names([str(s) for s in x[0].text]) if x else ""
            t.update(title=fr("TIT2"), album=fr("TALB"), artist=names("TPE2") or names("TPE1"),
                     year=_int(fr("TDRC")), num=_int(fr("TRCK")), disc=_int(fr("TPOS")),
                     label=fr("TPUB"), media=fr("TMED"), date=fr("TDRL") or fr("TDRC"))
            for frame in tags.getall("TXXX"):
                nk = re.sub(r"[\s_\-]", "", frame.desc.lower())
                if nk == "barcode": t["barcode"] = _txt(frame.text)
                elif nk in ("musicbrainzalbumreleasecountry", "releasecountry"): t["country"] = _txt(frame.text)
                elif nk in _CATNO_KEYS and not t["catno"]: t["catno"] = _txt(frame.text)
                elif nk in _ID_KEYS: t["has_ids"] = True
            return t
        low = {}
        for k, v in tags.items():
            low.setdefault(re.sub(r"[\s_\-]", "", str(k).lower()), v)
        t["has_ids"] = any(k in low for k in _ID_KEYS)
        g = lambda k: _txt(low.get(k, ""))  # noqa: E731
        n = lambda k: _names(low.get(k, ""))  # noqa: E731
        t.update(title=g("title"), album=g("album"), artist=n("albumartist") or n("artist"),
                 year=_int(g("date") or g("year")), num=_int(g("tracknumber")),
                 disc=_int(g("discnumber")), barcode=g("barcode"),
                 catno=next((g(k) for k in _CATNO_KEYS if g(k)), ""),
                 label=g("label") or g("organization") or g("publisher"),
                 country=g("releasecountry"), media=g("media"), date=g("date"))
    except Exception as exc:
        t["_error"] = str(exc)
    return t


def _natural_key(text):
    """'2 - x' before '10 - x' (plain string order puts 10 and 11 before 2)."""
    return [int(t) if t.isdigit() else t.casefold() for t in re.split(r"(\d+)", str(text))]


# When the same album sits in a folder twice in different formats, keep one copy.
_FORMAT_PREFERENCE = [".flac", ".wav", ".aiff", ".aif", ".ape", ".wv", ".dsf", ".dff",
                      ".m4a", ".mp4", ".mp3", ".ogg", ".oga", ".opus", ".mpc", ".wma"]


# Copies of the same audio differ in length by hundredths of a second (encoder
# padding); different tracks are practically never this close.
_SAME_AUDIO_S = 0.25


def _same_copy(g1, g2, info):
    """Two file groups hold the same recording when they have the same number
    of tracks, the lengths agree track by track (within _SAME_AUDIO_S) AND the titles - or failing that the file names -
    agree. Lengths alone are not enough: two discs of one 60-minute piece each
    can be equally long."""
    if len(g1) != len(g2) or not g1:
        return False
    close = sum(1 for a, b in zip(g1, g2)
                if abs(info[a]["secs"] - info[b]["secs"]) <= _SAME_AUDIO_S)
    if close / len(g1) < 0.9:
        return False

    def name(p):
        t = info[p]["title"].strip().casefold()
        return t or re.sub(r"\W+", " ", p.stem).strip().casefold()
    same = sum(1 for a, b in zip(g1, g2) if name(a) == name(b))
    return same / len(g1) >= 0.8


def _drop_duplicate_copies(files, info, album_dir):
    """Folders with the same album twice - FLAC + Opus side by side, or the
    same format in two folders ('16-44/' and '24-96/') - count once. Real
    discs (different tracks) are never merged, whatever their format.
    Keeps the preferred format, then the larger (higher-resolution) copy."""
    groups = {}
    for p in files:
        groups.setdefault((p.parent, p.suffix.lower()), []).append(p)
    for k in groups:
        groups[k].sort(key=lambda p: (info[p]["num"] or 9999, _natural_key(p.name)))
    rank = {k: (not any(info[p]["has_ids"] for p in g),   # the copy already tagged
                _FORMAT_PREFERENCE.index(k[1]) if k[1] in _FORMAT_PREFERENCE else 99,
                -sum(p.stat().st_size for p in g)) for k, g in groups.items()}
    keys = sorted(groups, key=lambda k: _natural_key(f"{k[0]}{k[1]}"))
    removed = []
    for i, k1 in enumerate(keys):
        if k1 in removed:
            continue
        for k2 in keys[i + 1:]:
            if k2 in removed or not _same_copy(groups[k1], groups[k2], info):
                continue
            loser = k2 if rank[k1] <= rank[k2] else k1
            removed.append(loser)
            if loser == k1:
                break
    if not removed:
        return files, []
    gone = {p for k in removed for p in groups[k]}
    label = [f"{(k[0].relative_to(album_dir).as_posix() + '/') if k[0] != album_dir else ''}*{k[1]}"
             for k in removed]
    return [p for p in files if p not in gone], label


def _track_key(p, i):
    """What identifies a track for duplicate detection: its title tag, or the
    file name without the leading track number ("001.ACAaM" and "01.ACAAM"
    are the same track saved by two versions of a naming script)."""
    t = i["title"].strip()
    if not t:
        t = re.sub(r"^[\d\s._\-]+", "", p.stem)
    return re.sub(r"\W+", " ", t).strip().casefold()


def _drop_duplicate_tracks(files, info):
    """The same track twice in one album - same title (or same name apart
    from the number) and the same length (within _SAME_AUDIO_S) - counts once.
    Two different tracks that merely share a title differ in length."""
    buckets = {}
    for p in files:
        key = _track_key(p, info[p])
        if key:
            buckets.setdefault(key, []).append(p)

    def preference(p):
        # Keep the copy that already carries release IDs (the one Picard
        # saved last), then the preferred format, then the newest file.
        ext = p.suffix.lower()
        st = p.stat()
        return (not info[p]["has_ids"],
                _FORMAT_PREFERENCE.index(ext) if ext in _FORMAT_PREFERENCE else 99,
                -st.st_mtime, -st.st_size, _natural_key(p.name))
    # FLAC files whose decoded audio has the same checksum are the same
    # recording, whatever their names or titles say.
    for p in files:
        md5 = info[p].get("audio_md5")
        if md5:
            buckets.setdefault(f"md5:{md5:x}", []).append(p)
    gone = []
    for group in buckets.values():
        group = [p for p in group if p not in gone]
        if len(group) < 2:
            continue
        kept = []
        for p in sorted(group, key=preference):
            if any(abs(info[p]["secs"] - info[k]["secs"]) <= _SAME_AUDIO_S for k in kept):
                gone.append(p)
            else:
                kept.append(p)
    if not gone:
        return files, []
    gone_set = set(gone)
    return [p for p in files if p not in gone_set], sorted(p.name for p in gone_set)


def read_album(album_dir, artist_folder, album_folder):
    files = []
    for dirpath, _d, names in os.walk(album_dir):
        for n in names:
            p = Path(dirpath) / n
            if p.suffix.lower() in AUDIO_EXT:
                files.append(p)
    info = {p: read_file(p) for p in files}
    files, dropped_formats = _drop_duplicate_copies(files, info, album_dir)
    files, dropped_tracks = _drop_duplicate_tracks(files, info)
    infos = [(p, info[p]) for p in files]

    # Files without a disc tag take their disc from their folder: "CD1/",
    # "CD2/" ... in natural order. Without this, two disc folders that each
    # start at track 1 interleave (1-1, 2-1, 1-2, 2-2 ...).
    folders = sorted({p.parent for p in files},
                     key=lambda d: _natural_key(d.relative_to(album_dir).as_posix()))
    folder_disc = {d: i + 1 for i, d in enumerate(folders)} if len(folders) > 1 else {}
    for p, i in infos:
        i["disc_eff"] = i["disc"] or folder_disc.get(p.parent, 1)
    infos.sort(key=lambda x: (x[1]["disc_eff"], x[1]["num"] or 9999,
                              _natural_key(x[0].relative_to(album_dir).as_posix())))

    def top(key):
        c = Counter(i[key] for _p, i in infos if i[key])
        return c.most_common(1)[0][0] if c else ""

    m = re.match(r"\s*(\d{4})\s*-\s*(.*)", album_folder)
    year = top("year") or (int(m.group(1)) if m else 0)
    title = top("album") or (m.group(2) if m else album_folder)
    n_discs = len({i["disc_eff"] for _p, i in infos}) or 1
    folder_hints = mc.catno_hints(album_folder) + [h for h in mc.catno_hints(title)
                                                   if h not in mc.catno_hints(album_folder)]
    folder_match = set(mc.catno_hints(album_folder, variants=True)) | set(
        mc.catno_hints(title, variants=True))
    return {
        "artist": top("artist") or artist_folder, "title": title,
        "year": year or None, "discs": n_discs,
        "dropped_formats": dropped_formats, "dropped_tracks": dropped_tracks,
        "tracks": [{"disc": i["disc_eff"], "num": i["num"], "title": i["title"],
                    "secs": i["secs"], "path": str(_p)} for _p, i in infos],
        "barcodes": {mc.norm_code(i["barcode"]) for _p, i in infos if i["barcode"]},
        # catalogue numbers from the tags, plus any written in the folder name
        # or album title ("[20bit RM DOMO 71063-2]")
        "catnos": ({mc.norm_code(c) for _p, i in infos for c in mc.split_codes(i["catno"])}
                   | {mc.norm_code(h) for h in folder_match}),
        "catnos_from_folder": {mc.norm_code(h) for h in folder_match},
        "catno_search": sorted({c for _p, i in infos for c in mc.split_codes(i["catno"])} | set(folder_hints)),
        "pressing_year": mc.pressing_year(album_folder) or mc.pressing_year(title),
        "media_hint": mc.media_hint(album_folder),
        "folder": album_folder,
        "tag_label": top("label"), "tag_country": top("country"),
        "tag_media": mc.media_kind(top("media")), "tag_date": top("date"),
        "audio": audio_evidence([i for _p, i in infos]),
        "tocs": disc_tocs(infos) or log_tocs(album_dir) or cue_tocs(album_dir, infos),
        "scans": scan_evidence(album_dir),
    }


def audio_evidence(infos):
    """What the audio itself says about its source.

    hires: more than 16 bits or 48 kHz, so not from a CD (a vinyl rip or a download).
    cd_frames: every lossless track at 44.1 kHz is a whole number of CD sectors
    (588 samples), which a CD rip is and a download almost never is."""
    lossless = [i for i in infos if i.get("lossless") and i.get("rate")]
    hires = bool(infos) and any(i.get("bits", 0) > 16 or i.get("rate", 0) > 48000 for i in infos)
    cd_frames = (len(lossless) >= 2 and len(lossless) == len(infos)
                 and all(i["rate"] == 44100 and i.get("bits", 16) <= 16 and i["samples"]
                         and i["samples"] % CD_FRAME == 0 for i in lossless))
    # lossless CD-quality tracks that are NOT whole sectors: not a CD rip, so a download
    not_cd = (len(lossless) >= 2 and len(lossless) == len(infos) and not hires
              and all(i["rate"] == 44100 and i["samples"] for i in lossless)
              and sum(1 for i in lossless if i["samples"] % CD_FRAME == 0) == 0)
    return {"hires": hires, "cd_frames": cd_frames, "not_cd": not_cd}


def disc_tocs(infos):
    """The table of contents of each disc, rebuilt from the tracks' exact
    lengths (as freeDB/MusicBrainz disc IDs are), for CD rips only:
    {disc: "1 N leadout offset1 ... offsetN"} in MusicBrainz's toc form."""
    out = {}
    discs = {}
    for _p, i in infos:
        discs.setdefault(i.get("disc_eff", 1), []).append(i)
    for d, tracks in discs.items():
        if not tracks or len(tracks) > 99:
            continue
        if not all(i.get("lossless") and i.get("rate") == 44100 and i.get("samples")
                   and i["samples"] % CD_FRAME == 0 for i in tracks):
            continue
        offsets, pos = [], 150
        for i in tracks:
            offsets.append(pos)
            pos += i["samples"] // CD_FRAME
        out[d] = " ".join(str(x) for x in [1, len(tracks), pos] + offsets)
    return out


_LOG_TOC_ROW = re.compile(r"^\s*(\d{1,2})\s*\|\s*[\d:.]+\s*\|\s*[\d:.]+\s*\|\s*(\d+)\s*\|\s*(\d+)\s*$", re.M)


def _read_log(path):
    raw = Path(path).read_bytes()[:400000]
    for enc in ("utf-16",) if raw[:2] in (b"\xff\xfe", b"\xfe\xff") else ("utf-8", "cp1252"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("latin-1", "ignore")


def log_tocs(album_dir):
    """Tables of contents from the rip logs next to the music (EAC, XLD): the
    exact disc that was ripped, whatever the files were converted to since."""
    out = []
    for p in sorted(Path(album_dir).rglob("*.log"), key=lambda p: _natural_key(str(p))):
        try:
            text = _read_log(p)
        except OSError:
            continue
        if "TOC" not in text and "toc" not in text:
            continue
        rows = [(int(n), int(s), int(e)) for n, s, e in _LOG_TOC_ROW.findall(text)]
        seen, tracks = set(), []
        for n, s, e in rows:                       # the TOC table only (first occurrence of each track)
            if n in seen:
                break
            seen.add(n)
            tracks.append((s, e))
        if 1 <= len(tracks) <= 99:
            offsets = [s + 150 for s, _e in tracks]
            toc = " ".join(str(x) for x in [1, len(tracks), tracks[-1][1] + 1 + 150] + offsets)
            if toc not in out:
                out.append(toc)
    return {i + 1: toc for i, toc in enumerate(out)}


# --------------------------------------------------------------------------
# AcoustID fingerprints (Picard's "Scan"): identify the recordings from the audio
# --------------------------------------------------------------------------
def picard_preferred_countries(ini="~/.var/app/org.musicbrainz.Picard/config/MusicBrainz/Picard.ini"):
    """Picard's Options > Metadata > Preferred Releases > countries, when set."""
    import configparser
    cp = configparser.RawConfigParser(strict=False, interpolation=None)
    try:
        cp.read(Path(ini).expanduser(), encoding="utf-8")
        raw = cp.get("setting", "preferred_release_countries", fallback="")
    except Exception:
        return []
    if not raw or raw.startswith("@"):
        return []
    return [c.strip().upper() for c in raw.split(",") if re.fullmatch(r"\s*[A-Za-z]{2}\s*", c)]


def acoustid_key():
    key = os.environ.get("TAGMENDER_ACOUSTID_KEY", "").strip()
    if not key:
        f = Path("~/.config/tagmender/acoustid_key").expanduser()
        if f.is_file():
            key = f.read_text().strip()
    return key


def fingerprint_ready():
    return bool(shutil.which("fpcalc") and acoustid_key())


def fingerprint(path, db=None):
    """Chromaprint fingerprint of a file (fpcalc), cached by file size and time."""
    import subprocess
    try:
        st = Path(path).stat()
    except OSError:
        return None
    key = f"{path}|{st.st_size}|{int(st.st_mtime)}"

    def compute():
        try:
            r = subprocess.run(["fpcalc", "-json", "-length", "120", str(path)],
                               capture_output=True, text=True, timeout=120)
            d = json.loads(r.stdout)
            return {"duration": round(d["duration"]), "fingerprint": d["fingerprint"]}
        except Exception:
            return None
    return _cached(db, "fp", key, compute)


def acoustid_releases(http, album, db=None, n=3):
    """MusicBrainz releases that hold the recordings AcoustID finds for several
    tracks of the album (first, middle and last): the releases common to all of
    them. Empty when fingerprinting is not set up or nothing is found."""
    key = acoustid_key()
    tracks = [t for t in album["tracks"] if t.get("path")]
    if not key or not tracks or not shutil.which("fpcalc"):
        return set()
    picks = [tracks[0], tracks[len(tracks) // 2], tracks[-1]][:n] if len(tracks) >= 3 else tracks
    common = None
    for t in picks:
        fp = fingerprint(t["path"], db)
        if not fp:
            continue
        data = http.get_json(f"https://api.acoustid.org/v2/lookup?client={q(key)}&format=json"
                             f"&meta=recordings+releaseids&duration={fp['duration']}"
                             f"&fingerprint={q(fp['fingerprint'])}", "acoustid")
        rels = {rel["id"] for res in (data or {}).get("results", []) if res.get("score", 0) >= 0.8
                for rec in res.get("recordings") or [] for rel in rec.get("releases") or [] if rel.get("id")}
        if not rels:
            continue
        common = rels if common is None else common & rels
    return common or set()


def mb_discid(toc):
    """The MusicBrainz disc ID of a table of contents ("1 N leadout off1 ... offN")."""
    import base64
    import hashlib
    nums = [int(x) for x in toc.split()]
    first, last, leadout, offsets = nums[0], nums[1], nums[2], nums[3:]
    text = f"{first:02X}{last:02X}{leadout:08X}" + "".join(
        f"{(offsets[i] if i < len(offsets) else 0):08X}" for i in range(99))
    digest = hashlib.sha1(text.encode("ascii")).digest()
    return base64.b64encode(digest).decode("ascii").replace("+", ".").replace("/", "_").replace("=", "-")


def ctdb_toc(toc):
    """The same table of contents in CUETools DB form (sectors from 0, colon separated)."""
    nums = [int(x) for x in toc.split()]
    return ":".join(str(x - 150) for x in nums[3:] + [nums[2]])


def toc_releases(http, album):
    """Releases whose disc matches a disc of these files, from the tables of
    contents (audio, rip log or cue sheet):

    - MusicBrainz by disc ID: exact when that disc ID is attached to a release,
      else fuzzy, as Picard's CD lookup does;
    - the CUETools database (CTDB), which links rips to MusicBrainz AND Discogs
      releases, including pressings MusicBrainz has no disc ID for.

    Sets album["toc_discogs"] (Discogs ids from CTDB) and album["discids"]
    ([(disc ID, toc, attached in MusicBrainz?)]); returns MusicBrainz ids."""
    found, discogs, discids = set(), set(), []
    for toc in (album.get("tocs") or {}).values():
        did = mb_discid(toc)
        data = http.get_json(f"https://musicbrainz.org/ws/2/discid/{did}?toc={toc.replace(' ', '+')}"
                             f"&cdstubs=no&fmt=json", "mb")
        exact = bool(data and data.get("id") == did)
        discids.append((did, toc, exact))
        for r in (data or {}).get("releases", []):
            if r.get("id"):
                found.add(r["id"])
        get_text = getattr(http, "get_text", None)
        if get_text:
            try:
                xml = get_text(f"http://db.cuetools.net/lookup2.php?version=3&ctdb=1&metadata=extensive"
                               f"&fuzzy=1&toc={ctdb_toc(toc)}", "ctdb") or ""
            except Exception:
                xml = ""
            for tag in re.findall(r"<metadata\b[^>]*>", xml):
                attrs = dict(re.findall(r'(\w+)="([^"]*)"', tag))
                if attrs.get("source") == "musicbrainz" and re.fullmatch(r"[0-9a-f-]{36}", attrs.get("id", "")):
                    found.add(attrs["id"])
                elif attrs.get("source") == "discogs" and attrs.get("id", "").isdigit():
                    discogs.add(attrs["id"])
    album["toc_discogs"] = discogs
    album["discids"] = discids
    return found


def cue_tocs(album_dir, infos):
    """Tables of contents from cue sheets: track starts from INDEX 01, the end
    from the length of the audio (per-track files are added up in order)."""
    out = []
    secs_by_name = {Path(p).name.casefold(): i["secs"] for p, i in infos if p is not None}
    track_secs = [i["secs"] for _p, i in infos]
    for cue in sorted(Path(album_dir).rglob("*.cue"), key=lambda p: _natural_key(str(p))):
        try:
            text = _read_log(cue)
        except OSError:
            continue
        files, current, starts = [], None, []
        for line in text.splitlines():
            m = re.match(r'\s*FILE\s+"?(.+?)"?\s+(WAVE|FLAC|MP3|AIFF|BINARY)?\s*$', line, re.I)
            if m:
                current = {"name": m.group(1), "tracks": []}
                files.append(current)
                continue
            m = re.match(r"\s*INDEX\s+01\s+(\d+):(\d+):(\d+)", line)
            if m and current is not None:
                current["tracks"].append(int(m.group(1)) * 4500 + int(m.group(2)) * 75 + int(m.group(3)))
        n = sum(len(f["tracks"]) for f in files)
        if not n or n > 99:
            continue
        pos, offsets = 0, []
        ok = True
        for k, f in enumerate(files):
            length = secs_by_name.get(Path(f["name"]).name.casefold())
            if length is None:
                # converted since (names changed): one file per track, in order
                length = track_secs[k] if len(files) == len(track_secs) else None
            if length is None and k < len(files) - 1:
                ok = False
                break
            offsets += [pos + s for s in f["tracks"]]
            if length is None:                         # single-file cue whose file is gone
                ok = False
                break
            pos += round(length * 75)
        if ok and offsets:
            toc = " ".join(str(x) for x in [1, len(offsets), pos + 150] + [o + 150 for o in offsets])
            if toc not in out:
                out.append(toc)
    return {i + 1: toc for i, toc in enumerate(out)}


# --------------------------------------------------------------------------
# HTTP with rate limiting and a persistent cache
# --------------------------------------------------------------------------
class AuthError(RuntimeError):
    """Discogs rejected the token. Fatal: retrying cannot help."""


def check_discogs_token(token):
    """One uncached call so a bad token stops the run before any work is done."""
    req = urllib.request.Request(
        "https://api.discogs.com/oauth/identity",
        headers={"User-Agent": USER_AGENT, "Authorization": f"Discogs token={token}"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode("utf-8")).get("username", "?")
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            raise AuthError(
                f"Discogs rejected the token (HTTP {e.code}).\n"
                "  - Create one at https://www.discogs.com/settings/developers -> 'Generate new token'\n"
                "  - Paste only the token itself, no quotes or the words 'token=':\n"
                "      export DISCOGS_TOKEN=abcdEFGH...\n"
                "  - The export only lasts for that terminal window.")
        raise
    except (urllib.error.URLError, TimeoutError):
        return None   # offline check inconclusive; real calls will retry


class Http:
    def __init__(self, db, cfg, discogs_token):
        self.db, self.cfg, self.token = db, cfg, discogs_token
        self.last = {}
        db.execute("CREATE TABLE IF NOT EXISTS http_cache (url TEXT PRIMARY KEY, body TEXT)")

    def get_json(self, url, host):
        body = self.get_text(url, host)
        return json.loads(body) if body is not None else None

    def get_text(self, url, host):
        row = self.db.execute("SELECT body FROM http_cache WHERE url=?", (url,)).fetchone()
        if row:
            return row[0]
        delay = self.cfg["discogs_delay"] if host == "discogs" else self.cfg["mb_delay"]
        headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
        if host == "discogs":
            headers["Authorization"] = f"Discogs token={self.token}"
        for attempt in range(6):
            wait = delay - (time.time() - self.last.get(host, 0))
            if wait > 0:
                time.sleep(wait)
            self.last[host] = time.time()
            try:
                with urllib.request.urlopen(urllib.request.Request(url, headers=headers),
                                            timeout=60) as r:
                    body = r.read().decode("utf-8")
                self.db.execute("INSERT OR REPLACE INTO http_cache VALUES (?,?)", (url, body))
                self.db.commit()
                return body
            except urllib.error.HTTPError as e:
                if e.code == 404:
                    return None
                if e.code in (401, 403) and host == "discogs":
                    raise AuthError("Discogs rejected the token (HTTP %d)" % e.code)
                if e.code in (429, 500, 502, 503, 504):
                    if host == "mb" and e.code == 503:
                        # MusicBrainz answers 503 when requests come too fast:
                        # slow down for the rest of the run, and retry soon.
                        self.cfg["mb_delay"] = min(3.0, self.cfg["mb_delay"] * 1.2)
                        delay = self.cfg["mb_delay"]
                        pause = int(e.headers.get("Retry-After") or 0) or 3 * (attempt + 1)
                    else:
                        pause = int(e.headers.get("Retry-After") or 0) or 15 * (attempt + 1)
                    print(f"    {host} HTTP {e.code}, waiting {pause}s", file=sys.stderr)
                    time.sleep(pause)
                    continue
                raise
            except (urllib.error.URLError, TimeoutError) as e:
                print(f"    {host} network error {e}, retrying", file=sys.stderr)
                time.sleep(10 * (attempt + 1))
        raise RuntimeError(f"giving up on {url}")


def q(s):
    return urllib.parse.quote(str(s))


# --------------------------------------------------------------------------
# MusicBrainz
# --------------------------------------------------------------------------
def mb_credit(rel):
    """The credit as printed on the release ("credited as" names)."""
    return "".join(a.get("name", "") + a.get("joinphrase", "")
                   for a in rel.get("artist-credit", []) if isinstance(a, dict))


def mb_canonical_artists(rel):
    """The artists' own MusicBrainz names, one per artist - the list Picard
    exposes as %_albumartists% and the plugin writes one name per artist."""
    return "; ".join((a.get("artist") or {}).get("name") or a.get("name", "")
                     for a in rel.get("artist-credit", []) if isinstance(a, dict))


_MB_DISCOGS_RE = re.compile(r"discogs\.com/(?:[a-zA-Z-]+/)?release/(\d+)", re.I)


def mb_linked_discogs_id(rel):
    """The Discogs RELEASE id MusicBrainz links this release to, if any - the
    same rule as the plugin's discogs_release_id_from_mb(). Master links are
    ignored: a master is the work, not a pressing."""
    for r in rel.get("relations") or []:
        if r.get("target-type") != "url":
            continue
        m = _MB_DISCOGS_RE.search((r.get("url") or {}).get("resource") or "")
        if m:
            return m.group(1)
    return ""


_MB_VIDEO_FORMATS = ("dvd-video", "blu-ray", "dvd", "vhs", "hd-dvd", "vcd", "svcd")


def mb_sort_names(rel):
    """Each artist's MusicBrainz sort name (Latin script, e.g. "Kitaro" for 喜多郎)."""
    return "; ".join((a.get("artist") or {}).get("sort-name", "")
                     for a in rel.get("artist-credit", []) if isinstance(a, dict))


def mb_candidate(rel):
    tracks = []
    audio_media = [m for m in rel.get("media", [])
                   if str(m.get("format") or "").lower() not in _MB_VIDEO_FORMATS]
    for mi, media in enumerate(audio_media, 1):
        for t in media.get("tracks", []):
            tracks.append({"disc": mi, "title": t.get("title", ""),
                           "secs": (t.get("length") or 0) / 1000.0})
    infos = rel.get("label-info") or []
    return {
        "source": "mb", "id": rel["id"], "url": f"https://musicbrainz.org/release/{rel['id']}",
        "discogs_id": mb_linked_discogs_id(rel),
        "artist": mb_credit(rel), "artist_alt": mb_canonical_artists(rel),
        "artist_sort": mb_sort_names(rel),
        "title": rel.get("title", ""),
        "year": int(rel["date"][:4]) if str(rel.get("date") or "")[:4].isdigit() else None,
        "date": str(rel.get("date") or ""),
        "script": (rel.get("text-representation") or {}).get("script") or "",
        "country": rel.get("country", ""),
        "label": "; ".join(sorted({i["label"]["name"] for i in infos if i.get("label")})),
        "catnos": {mc.norm_code(i.get("catalog-number")) for i in infos if i.get("catalog-number")},
        "barcodes": {mc.norm_code(rel["barcode"])} if rel.get("barcode") else set(),
        "disc_count": len([m for m in audio_media if m.get("tracks")]) or len(audio_media) or 1,
        "tracks": tracks,
        "formats": {k for k in (mc.media_kind(m.get("format")) for m in audio_media) if k},
    }


def _mb_track_total(rel):
    return sum(int(m.get("track-count") or 0) for m in rel.get("media") or []
               if str(m.get("format") or "").lower() not in _MB_VIDEO_FORMATS)


def version_priority(album, catnos=(), country="", formats=()):
    """Which versions of an album to fetch first (lower is sooner): your
    catalogue number, then the country and format your folder names."""
    mine = {mc.norm_code(c) for c in album.get("catnos") or ()}
    theirs = {mc.norm_code(c) for c in catnos if c}
    folder = mc.fold(album.get("folder") or "")
    cc = mc.country_code(country)
    named = {c for n, c in mc._COUNTRY_NAMES.items() if len(n) > 2 and f" {n} " in f" {folder} "}
    kinds = {mc.media_kind(f) for f in formats if f}
    hint = album.get("media_hint") or ("cd" if (album.get("scans") or {}).get("physical") else "")
    return (0 if mine & theirs else 1, 0 if cc and cc in named else 1, 0 if hint and hint in kinds else 1)


def mb_search(http, album, cfg):
    # Inside a quoted Lucene phrase only " and \ are special.
    title = mc.search_title(album["title"]).replace("\\", " ").replace('"', " ")
    first = mc.artist_parts(album["artist"])[0].replace("\\", " ").replace('"', " ")
    short = re.split(r"\s+[-:–]\s+", title)[0].strip() or title
    queries = []
    for b in sorted(album["barcodes"]):
        for form in mc.barcode_variants(b):
            queries.append(f"barcode:{form}")
    for c in album.get("catno_search", [])[:3]:
        c = c.replace("\\", " ").replace('"', " ")
        queries.append(f'catno:"{c}" AND artist:"{first}"')
        queries.append(f'catno:"{c}"')
    if album.get("pressing_year"):
        queries.append(f'release:"{title}" AND artist:"{first}" AND date:{album["pressing_year"]}*')
    # Collaborators are separate artists in MusicBrainz, so search ONE name.
    queries.append(f'release:"{title}" AND artist:"{first}"')
    typeless = mc.drop_release_type(title)
    if typeless != title:                                   # "Garbage EP" is listed as "Garbage"
        queries.append(f'release:"{typeless}" AND artist:"{first}"')
    queries.append(f'release:"{title}"')
    if short != title:
        queries.append(f'release:"{short}" AND artist:"{first}"')
    queries.append(f'"{title}" AND "{first}"')
    n_files = len(album["tracks"])
    seen, hits = set(), []
    for query in queries:
        data = http.get_json(f"https://musicbrainz.org/ws/2/release/?query={q(query)}&fmt=json&limit=15", "mb")
        for r in (data or {}).get("releases", []):
            if r["id"] not in seen:
                seen.add(r["id"])
                hits.append(r)
        if len(hits) >= 5 and any(r.get("track-count") == n_files for r in hits):
            break
    same = [r for r in hits if r.get("track-count") == n_files]
    chosen = (same or hits)[:cfg["mb_detail_fetch"] if same else 2]
    # Releases whose disc ID matches the files are fetched whatever their title
    # (a freeDB-style lookup finds an album the folder names differently).
    have = {r["id"] for r in chosen}
    chosen += [{"id": rid} for rid in sorted(album.get("toc_releases") or ()) if rid not in have][:6]
    # Every version of the album (its release group): the edition with your track
    # count (bonus tracks, deluxe 2CD) or your pressing may not be among the hits.
    have = {r["id"] for r in chosen}
    rg = next(((r.get("release-group") or {}).get("id") for r in (same or hits)
               if (r.get("release-group") or {}).get("id")), None)
    if rg:
        data = http.get_json(f"https://musicbrainz.org/ws/2/release?release-group={rg}"
                             f"&inc=media+labels&limit=100&fmt=json", "mb")
        versions = [v for v in (data or {}).get("releases", []) if v.get("id") not in have]
        exact = [v for v in versions if _mb_track_total(v) == n_files]
        exact.sort(key=lambda v: version_priority(album, catnos=[i.get("catalog-number") for i in v.get("label-info") or []],
                                                  country=v.get("country"),
                                                  formats=[m.get("format") for m in v.get("media") or []]))
        chosen += [{"id": v["id"]} for v in exact[:4 if same else 8]]
    out = []
    for r in chosen:
        det = http.get_json(f"https://musicbrainz.org/ws/2/release/{r['id']}"
                            f"?inc=recordings+artist-credits+labels+url-rels&fmt=json", "mb")
        if det:
            out.append(mb_candidate(det))
    return out


# --------------------------------------------------------------------------
# Discogs
# --------------------------------------------------------------------------
_POS_DISC = re.compile(r"^\s*(?:[A-Za-z]{1,4})?(\d+)\s*[-.]\s*\d+")


def flatten_tracklist(tracklist, dm, parent_position=None):
    """Audio tracks in order. Discogs 'index' entries (a work split into
    movements) carry their audio in nested sub_tracks; headings carry none.
    The plugin's parse_discs() skips index entries, so it cannot be used
    alone for counting.

    A sub-track's own position ("2.1", "2a") numbers it WITHIN its work, not
    a disc, so it takes its parent entry's position for disc numbering."""
    out = []
    for e in tracklist or []:
        if not isinstance(e, dict):
            continue
        kind = str(e.get("type_") or e.get("type") or "track").strip().casefold()
        if kind == "heading":
            continue
        if kind == "index":
            subs = flatten_tracklist(e.get("sub_tracks"), dm,
                                     parent_position=e.get("position") or "")
            if subs:
                out.extend(subs)
                continue
        pos = parent_position if parent_position is not None else (e.get("position") or "")
        out.append({"position": pos, "title": e.get("title", ""),
                    "secs": dm._parse_duration(e.get("duration"))})
    return out


def discogs_candidate(rel, dm):
    # The credit exactly as the plugin writes it (format_artist_credit honours
    # Discogs' "credited as" names and join words), plus the canonical names,
    # since files may carry either form.
    artists = dm.format_artist_credit(rel.get("artists"))
    canonical = " & ".join(dm.clean_artist_name(a.get("name")) for a in rel.get("artists", [])
                           if isinstance(a, dict))
    flat = flatten_tracklist(rel.get("tracklist"), dm)
    discs = dm.parse_discs(rel)
    disc_count = dm.release_disc_count(rel)     # same rule as the plugin
    tracks = []
    for t in flat:
        m = _POS_DISC.match(str(t["position"]))
        tracks.append({"disc": int(m.group(1)) if m else 1, "title": t["title"], "secs": t["secs"]})
    if discs and len(discs) > 1 and not any(_POS_DISC.match(str(t["position"])) for t in flat):
        # numbered by heading rows instead of position prefixes
        n = 0
        for d in discs:
            for _ in d["tracks"]:
                if n < len(tracks):
                    tracks[n]["disc"] = d["number"]
                n += 1
    labels = rel.get("labels") or []
    barcodes = {mc.norm_code(i["value"]) for i in rel.get("identifiers", [])
                if i.get("type") == "Barcode" and i.get("value")}
    year = rel.get("year") or 0
    if not year and str(rel.get("released") or "")[:4].isdigit():
        year = int(rel["released"][:4])
    return {
        "source": "discogs", "id": str(rel["id"]),
        "url": rel.get("uri") or f"https://www.discogs.com/release/{rel['id']}",
        "artist": artists or canonical, "artist_alt": canonical,
        "title": rel.get("title", ""), "year": year or None,
        "date": str(rel.get("released") or ""),
        "country": rel.get("country", ""),
        "label": "; ".join(sorted({dm.clean_artist_name(l.get("name")) for l in labels})),
        "catnos": {mc.norm_code(l["catno"]) for l in labels
                   if l.get("catno") and str(l["catno"]).casefold() != "none"},
        "barcodes": barcodes, "disc_count": disc_count, "tracks": tracks,
        "formats": {k for k in (mc.media_kind(f.get("name")) for f in rel.get("formats") or []) if k},
    }


def discogs_search(http, album, cfg, dm):
    title = mc.search_title(album["title"])
    first = mc.artist_parts(album["artist"])[0]
    short = re.split(r"\s+[-:–]\s+", title)[0].strip() or title
    searches = []
    for b in sorted(album["barcodes"]):
        for form in mc.barcode_variants(b):
            searches.append(f"barcode={q(form)}")
    for c in album.get("catno_search", [])[:3]:
        searches.append(f"catno={q(c)}&artist={q(first)}")
        searches.append(f"catno={q(c)}")
    if album.get("pressing_year"):
        searches.append(f"artist={q(first)}&release_title={q(title)}&year={album['pressing_year']}")
    searches.append(f"artist={q(first)}&release_title={q(title)}")
    typeless = mc.drop_release_type(title)
    if typeless != title:                                   # "Garbage EP" is listed as "Garbage"
        searches.append(f"artist={q(first)}&release_title={q(typeless)}")
    # Compilations are filed under "Various" and works under their performers,
    # so also search the title without any artist.
    searches.append(f"release_title={q(title)}")
    searches.append(f"q={q(first + ' ' + title)}")
    if short != title:
        searches.append(f"artist={q(first)}&release_title={q(short)}")
    seen, results = set(), []
    for s in searches:
        data = http.get_json(f"https://api.discogs.com/database/search?type=release&per_page=15&{s}", "discogs")
        for r in (data or {}).get("results", []):
            if r["id"] not in seen:
                seen.add(r["id"])
                results.append(r)
        if len(results) >= 8:
            break

    def pre(r):
        a, t = dm.split_discogs_title(r.get("title", ""))
        s = 0.35 * mc.artist_sim(album["artist"], dm.clean_artist_name(a)) + 0.65 * mc.title_sim(album["title"], t)
        return s + (0.05 if str(r.get("year") or "") == str(album["year"] or "") else 0)

    results.sort(key=pre, reverse=True)
    out = []
    for r in results[:cfg["discogs_detail_fetch"]]:
        if pre(r) < 0.5 and not album["barcodes"] and not album.get("catno_search"):
            continue
        det = http.get_json(f"https://api.discogs.com/releases/{r['id']}", "discogs")
        if det:
            out.append(discogs_candidate(det, dm))
    # Discogs releases the CUETools database links to your disc
    have = {str(c["id"]) for c in out}
    for rid in sorted(album.get("toc_discogs") or ())[:4]:
        if rid not in have:
            det = http.get_json(f"https://api.discogs.com/releases/{rid}", "discogs")
            if det:
                out.append(discogs_candidate(det, dm))
    # Every version of the album (its Discogs master) when none of these has your
    # track count or your catalogue number.
    n_files = len(album["tracks"])
    mine = {mc.norm_code(c) for c in album.get("catnos") or ()}
    found_count = any(len(c["tracks"]) == n_files for c in out)
    found_catno = not mine or any(mine & set(c["catnos"]) for c in out)
    master = next((r.get("master_id") for r in results if r.get("master_id") and pre(r) >= 0.7), None)
    if master and not (found_count and found_catno):
        data = http.get_json(f"https://api.discogs.com/masters/{master}/versions?per_page=100", "discogs")
        have = {str(c["id"]) for c in out}
        versions = [v for v in (data or {}).get("versions", []) if str(v.get("id")) not in have]
        versions.sort(key=lambda v: version_priority(album, catnos=[v.get("catno") or ""], country=v.get("country"),
                                                     formats=str(v.get("format") or "").split(",")))
        for v in versions[:6]:
            det = http.get_json(f"https://api.discogs.com/releases/{v['id']}", "discogs")
            if det:
                cand = discogs_candidate(det, dm)
                if len(cand["tracks"]) == n_files or (mine & set(cand["catnos"])):
                    out.append(cand)
    return out


# --------------------------------------------------------------------------
# Optional OCR
# --------------------------------------------------------------------------
IMAGE_EXT = {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".tif", ".tiff"}
_HINT = re.compile(r"back|rear|tray|inlay|disc|cd\d?|label|barcode|booklet|insert|inside|matrix", re.I)


OCR_VERSION = "ocr-2"   # bump when the OCR code changes, so cached OCR is redone


# Images that are not scans of the release: covers saved from the web, rip
# screenshots and spectrum/analysis pictures ("tech.info/Au.jpg", "ksnip_...").
_NOT_A_SCAN = re.compile(r"^(cover|folder|front|albumart\w*|thumb\w*|ksnip\S*|screenshot\S*|spectr\S*|au\d*|"
                         r"expose\d*|mv|dr|dynamic\S*|\d{2,4}x\d{2,4}\S*)$", re.I)
_NOT_A_SCAN_DIR = re.compile(r"^(tech[ ._-]?info|info|spectr\S*|analysis|screens?(hots)?)$", re.I)
_PHYSICAL_NAME = re.compile(r"back|rear|tray|inlay|disc|disk|(?<![a-z])cd\d*(?![a-z])|obi|matrix|spine|booklet|"
                            r"inside|insert|(?<![a-z])label|sticker|case", re.I)


def scan_evidence(album_dir):
    """Does the folder hold scans of a physical copy (a back cover, tray card,
    booklet, disc or obi)? A download comes with a cover image at most, so such
    scans mean the disc was owned. Returns {"physical": bool, "why": str}."""
    try:
        from PIL import Image
    except ImportError:
        Image = None
    for dirpath, dirs, names in os.walk(album_dir):
        dirs[:] = [d for d in dirs if not _NOT_A_SCAN_DIR.match(d)]
        for n in sorted(names):
            p = Path(dirpath) / n
            if p.suffix.lower() not in IMAGE_EXT or p.suffix.lower() == ".gif" or _NOT_A_SCAN.match(p.stem):
                continue
            if _PHYSICAL_NAME.search(p.stem):
                return {"physical": True, "why": f"a scan named {p.name}"}
            if Image is not None:
                try:
                    with Image.open(p) as im:
                        w, h = im.size
                except Exception:
                    continue
                if w >= 1000 and 1.15 <= w / max(h, 1) <= 1.5:
                    return {"physical": True, "why": f"a tray-card scan ({p.name})"}
    return {"physical": False, "why": ""}


def list_images(base, max_n):
    """Every image under the album folder (subfolders included), most likely
    to carry a barcode or catalogue number first. Exact duplicate files
    (e.g. 'scan 1 (copy).jpg') are scanned once.

    Returns (paths, key): the key identifies exactly these image contents, so
    OCR results can be cached and reused while the images are unchanged."""
    import hashlib
    found = []
    for dirpath, _d, names in os.walk(base):
        for n in sorted(names):
            p = Path(dirpath) / n
            if p.suffix.lower() in IMAGE_EXT:
                found.append(p)
    found.sort(key=lambda p: (0 if _HINT.search(p.stem) else 1, str(p).lower()))
    seen, out, digests = set(), [], []
    for p in found:
        try:
            digest = hashlib.md5(p.read_bytes()).hexdigest()
        except OSError:
            continue
        if digest in seen:
            continue
        seen.add(digest)
        out.append(str(p))
        digests.append(digest)
        if max_n and len(out) >= max_n:
            break
    key = hashlib.md5((OCR_VERSION + "|" + "|".join(digests)).encode()).hexdigest()
    return out, key


def _cached(db, kind, key, compute):
    """OCR is slow (seconds per image); store its result so re-evaluating an
    album after a script fix does not redo it."""
    if db is None:
        return compute()
    db.execute("CREATE TABLE IF NOT EXISTS ocr_cache (key TEXT PRIMARY KEY, value TEXT)")
    row = db.execute("SELECT value FROM ocr_cache WHERE key=?", (f"{kind}:{key}",)).fetchone()
    if row:
        return json.loads(row[0])
    value = compute()
    db.execute("INSERT OR REPLACE INTO ocr_cache VALUES (?,?)", (f"{kind}:{key}", json.dumps(value)))
    db.commit()
    return value


def _valid_barcode(code):
    """Check digit test for EAN-13 / UPC-A / EAN-8, so noise is not accepted."""
    if not code.isdigit() or len(code) not in (8, 12, 13):
        return False
    digits = [int(c) for c in code]
    body, check = digits[:-1], digits[-1]
    total = sum(d * (3 if i % 2 == (len(body) - 1) % 2 else 1) for i, d in enumerate(body))
    return (10 - total % 10) % 10 == check


def _barcode_variants(im):
    """Progressively more effortful versions of one scan. A barcode is often a
    small part of a big page, slightly blurred, or tiny: enlarge it, sharpen
    it, and try each part of the page on its own."""
    from PIL import Image, ImageFilter, ImageOps
    gray = ImageOps.grayscale(im)
    yield gray
    enhanced = ImageOps.autocontrast(gray).filter(ImageFilter.UnsharpMask(radius=2, percent=180))
    yield enhanced
    w, h = gray.size
    longest = max(w, h)
    for scale in (2, 3):
        if longest * scale <= 6500:
            yield enhanced.resize((w * scale, h * scale), Image.LANCZOS)
    # overlapping tiles (3x3 grid, 50% overlap), each enlarged to ~2400px
    tw, th = w // 2, h // 2
    for gx in range(3):
        for gy in range(3):
            x0, y0 = min(gx * tw // 2 * 1, w - tw), min(gy * th // 2 * 1, h - th)
            tile = enhanced.crop((x0, y0, x0 + tw, y0 + th))
            factor = max(1.0, min(3.0, 2400 / max(tile.size)))
            if factor > 1.0:
                tile = tile.resize((int(tile.size[0] * factor), int(tile.size[1] * factor)), Image.LANCZOS)
            yield tile


def ocr_barcodes(paths, per_image_s=8.0, total_s=40.0):
    """Decode product barcodes from scans. Returns validated codes only.

    Stops at the first image that yields one. Time-boxed, because an image
    with no readable barcode would otherwise cost the full multi-pass search."""
    try:
        from PIL import Image
        from pyzbar.pyzbar import decode, ZBarSymbol
    except ImportError:
        return set()
    symbols = [ZBarSymbol.EAN13, ZBarSymbol.EAN8, ZBarSymbol.UPCA, ZBarSymbol.UPCE]
    started = time.time()
    for p in paths:
        if time.time() - started > total_s:
            break
        try:
            im = Image.open(p)
            im.load()
            if im.mode not in ("RGB", "L"):
                im = im.convert("RGB")
        except Exception:
            continue
        t_img = time.time()
        small = max(im.size) < 1000          # too few pixels for the heavy passes
        hit = set()
        for n, variant in enumerate(_barcode_variants(im)):
            if n >= 2 and small:
                break
            for rotation in (0, 90, 180, 270):
                if time.time() - t_img > per_image_s:
                    break
                v = variant if rotation == 0 else variant.rotate(rotation, expand=True)
                try:
                    for d in decode(v, symbols=symbols):
                        code = re.sub(r"\D", "", d.data.decode("ascii", "ignore"))
                        if _valid_barcode(code):
                            hit.add(code)
                except Exception:
                    continue
                if hit:
                    break
            if hit or time.time() - t_img > per_image_s:
                break
        if hit:
            return hit
    return set()


_LANGS = None


def ocr_langs():
    """English, plus Russian and Japanese when their tesseract packs are installed
    (tesseract-ocr-rus, tesseract-ocr-jpn): FONO sleeves, Japanese obi strips."""
    global _LANGS
    if _LANGS is None:
        import subprocess
        try:
            have = subprocess.run(["tesseract", "--list-langs"], capture_output=True, text=True,
                                  timeout=30).stdout.split()
        except Exception:
            have = []
        _LANGS = "+".join(x for x in ("eng", "rus", "jpn") if x in have) or "eng"
    return _LANGS


def ocr_spines(paths):
    """Text printed along the spines of a CD tray card (catalogue numbers often
    appear only there), which page OCR misses: it runs sideways. Each side strip
    of a landscape scan is cut off, turned upright, enlarged and read."""
    import subprocess
    import tempfile
    try:
        from PIL import Image
    except ImportError:
        return ""
    chunks = []
    for p in paths:
        try:
            im = Image.open(p).convert("L")
        except Exception:
            continue
        w, h = im.size
        if not (1.15 <= w / max(h, 1) <= 1.6) or w < 600:
            continue                                   # tray cards are about 151 x 118 mm
        strip_w = max(int(w * 0.07), 30)
        for box in ((0, 0, strip_w, h), (w - strip_w, 0, w, h)):
            strip = im.crop(box)
            scale = max(1.0, min(3.0, 3000 / h))
            if scale > 1.0:
                strip = strip.resize((int(strip.size[0] * scale), int(strip.size[1] * scale)))
            for angle in (90, 270):
                try:
                    with tempfile.NamedTemporaryFile(suffix=".png") as tmp:
                        strip.rotate(angle, expand=True).save(tmp.name)
                        r = subprocess.run(["tesseract", tmp.name, "stdout", "--psm", "11", "-l", ocr_langs()],
                                           capture_output=True, text=True, timeout=60)
                    chunks.append(r.stdout)
                except Exception:
                    continue
    return "\n".join(chunks)


def ocr_text(paths):
    """OCR via the tesseract program itself (no pytesseract needed)."""
    import subprocess
    import tempfile
    try:
        from PIL import Image
    except ImportError:
        return ""
    chunks = []
    for p in paths:
        try:
            im = Image.open(p).convert("L")
            im.thumbnail((2400, 2400))
            with tempfile.NamedTemporaryFile(suffix=".png") as tmp:
                im.save(tmp.name)
                r = subprocess.run(["tesseract", tmp.name, "stdout", "--psm", "11", "-l", ocr_langs()],
                                   capture_output=True, text=True, timeout=120)
            chunks.append(r.stdout)
        except Exception:
            continue
    return "\n".join(chunks)


# --------------------------------------------------------------------------
# Pipeline
# --------------------------------------------------------------------------
def summarize(res):
    b = res["best"]
    if not b:
        return None
    c = b["cand"]
    return {"source": c["source"], "id": c["id"], "url": c["url"],
            "artist": c["artist"], "title": c["title"], "year": c["year"],
            "country": c["country"], "label": c["label"],
            "tracks": len(c["tracks"]), "confidence": b["confidence"],
            "notes": b["notes"], "discogs_id": c.get("discogs_id", "")}


def run_source(name, finder, album, cfg, ocr_txt=""):
    cands = finder()
    scored = [mc.score_candidate(album, c, cfg, ocr_txt) for c in cands]
    res = mc.classify(scored, cfg)
    res["scored"] = scored
    res["source"] = name
    return res


def process(row, http, cfg, dm, args):
    base = Path(row["FullPath"])
    if not base.is_dir():
        # Moved or renamed since the audit (Picard moves a folder when it tags the album).
        # Not a "no match": nobody looked it up. Its new folder is picked up by a fresh audit.
        return {"path": row["FullPath"], "artist_folder": row["AlbumArtistFolder"],
                "album_folder": row["AlbumFolder"], "tag_artist": "", "tag_title": "", "tag_year": None,
                "tracks": 0, "status": "moved", "best": None, "alts": [], "version": result_version(),
                "why": ["folder no longer exists (moved or renamed, e.g. by Picard after tagging); "
                        "run audit_untagged.py again to pick up its new folder"]}
    album = read_album(base, row["AlbumArtistFolder"], row["AlbumFolder"])
    out = {"path": row["FullPath"], "artist_folder": row["AlbumArtistFolder"],
           "album_folder": row["AlbumFolder"], "tag_artist": album["artist"],
           "tag_title": album["title"], "tag_year": album["year"],
           "tracks": len(album["tracks"])}
    if not album["tracks"]:
        out.update(status="nomatch", why=["no audio files"], best=None, alts=[])
        return out
    if album.get("dropped_formats"):
        out["dropped_formats"] = album["dropped_formats"]
    if album.get("dropped_tracks"):
        out["dropped_tracks"] = album["dropped_tracks"]

    images, img_key = [], ""
    db = getattr(http, "db", None)
    if args.ocr:
        images, img_key = list_images(base, args.max_images)
        if images:
            found = set(_cached(db, "barcodes", img_key, lambda: sorted(ocr_barcodes(images))))
            if found and not album["barcodes"]:
                album["barcodes"] = found
                out["barcodes_from_ocr"] = ";".join(sorted(found))

    if album.get("tocs"):
        try:
            album["toc_releases"] = toc_releases(http, album)
        except Exception:
            album["toc_releases"] = set()
        if album["toc_releases"]:
            out["toc_releases"] = len(album["toc_releases"])
    mb_res = run_source("mb", lambda: mb_search(http, album, cfg), album, cfg)
    dg_res = None
    source_notes = []
    if mb_res["status"] != "auto":
        try:
            dg_res = run_source("discogs", lambda: discogs_search(http, album, cfg, dm), album, cfg)
        except AuthError:
            raise
        except Exception as exc:     # keep what MusicBrainz found
            source_notes.append(f"Discogs lookup failed ({type(exc).__name__}: {exc}); "
                                "result is MusicBrainz-only")
    final = mc.combine(mb_res, dg_res)

    ocr_txt = ""
    if args.ocr and images and final["status"] != "auto":
        # cached per language set: installing a language pack reads the scans again
        kind = "text" if ocr_langs() == "eng" else f"text-{ocr_langs()}"
        text = _cached(db, kind, img_key, lambda: ocr_text(images))
        spines = _cached(db, "spines" if ocr_langs() == "eng" else f"spines-{ocr_langs()}", img_key,
                         lambda: ocr_spines(images))
        if spines.strip():
            text = text + "\n" + spines
        if text.strip():
            # Barcodes and catalogue numbers printed on the scans are SEARCHED, not only
            # compared: the release they identify may not be among what the title found.
            codes = mc.codes_from_ocr_text(text)
            known_b = {mc.barcode_key(b) for b in album["barcodes"]}
            known_c = {mc.norm_code(c) for c in album.get("catno_search", [])}
            new_b = [b for b in codes["barcodes"] if mc.barcode_key(b) not in known_b]
            new_c = [c for c in codes["catnos"] if mc.norm_code(c) not in known_c]
            extra = {"mb": [], "discogs": []}
            if new_b or new_c:
                out["codes_from_ocr"] = ";".join(new_b + new_c)
                probe = dict(album, barcodes=set(new_b), catno_search=new_c)
                extra["mb"] = mb_search(http, probe, cfg)
                try:
                    extra["discogs"] = discogs_search(http, probe, cfg, dm)
                except AuthError:
                    raise
                except Exception as exc:
                    source_notes.append(f"Discogs lookup of codes read from scans failed ({exc})")
                if not album["barcodes"] and new_b:
                    album["barcodes"] = set(new_b)       # as for barcodes decoded from the scans

            def rescore(res, name):
                cands = [s["cand"] for s in (res or {}).get("scored", [])]
                ids = {c["id"] for c in cands}
                cands += [c for c in extra[name] if c["id"] not in ids]
                if not cands:
                    return res
                scored = [mc.score_candidate(album, c, cfg, text) for c in cands]
                new = mc.classify(scored, cfg)
                new["scored"] = scored
                new["source"] = name
                return new
            mb_res, dg_res = rescore(mb_res, "mb"), rescore(dg_res, "discogs")
            final = mc.combine(mb_res, dg_res)
            ocr_txt = text

    # Still unsure: fingerprint a few tracks (AcoustID) and add the releases that
    # hold those recordings; they also prove the album when its title differs.
    if final["status"] != "auto" and fingerprint_ready():
        try:
            rels = acoustid_releases(http, album, db)
        except Exception as exc:
            rels = set()
            source_notes.append(f"fingerprint lookup failed ({exc})")
        if rels:
            album["fp_releases"] = rels
            out["fingerprint_releases"] = len(rels)
            have = {s["cand"]["id"] for s in (mb_res or {}).get("scored", [])}
            new = []
            for rid in sorted(rels - have)[:6]:
                det = http.get_json(f"https://musicbrainz.org/ws/2/release/{rid}"
                                    f"?inc=recordings+artist-credits+labels+url-rels&fmt=json", "mb")
                if det:
                    new.append(mb_candidate(det))
            cands = [s["cand"] for s in (mb_res or {}).get("scored", [])] + new
            if cands:
                scored = [mc.score_candidate(album, c, cfg, ocr_txt) for c in cands]
                mb_res = dict(mc.classify(scored, cfg), scored=scored, source="mb")
                final = mc.combine(mb_res, dg_res)
    if album.get("discids"):
        out["discids"] = album["discids"]

    status = final["status"]
    why = list(final["why"]) + source_notes
    if source_notes and status == "nomatch":
        status = "error"      # not a real "no match": must be retried, not filed
    if row.get("Status") == "partial" and status == "auto":
        status = "review"
        why.append("album is partially tagged already")
    alts = []
    for res in (mb_res, dg_res):
        if res and res.get("scored"):
            for s in sorted(res["scored"], key=lambda s: -s["confidence"])[:3]:
                c = s["cand"]
                alts.append(f"{c['source']}:{c['id']} {s['confidence']} {c['artist']} - {c['title']} ({c['year']})")
    out.update(status=status, why=why, best=summarize(final), alts=alts,
               margin=final.get("margin"), version=result_version())
    return out


COLS = ["Status", "Confidence", "Source", "ID", "URL", "MatchedArtist", "MatchedTitle",
        "MatchedYear", "MatchedCountry", "MatchedLabel", "Margin", "Evidence",
        "WhyNotAuto", "Alternatives", "TagArtist", "TagAlbum", "TagYear", "Tracks",
        "AlbumArtistFolder", "AlbumFolder", "FullPath", "BarcodeFromOCR", "CodesFromOCR", "LinkedDiscogsID",
        "Notes"]


def to_row(o):
    b = o.get("best") or {}
    return {"Status": o["status"], "Confidence": b.get("confidence", ""),
            "Source": b.get("source", ""), "ID": b.get("id", ""), "URL": b.get("url", ""),
            "MatchedArtist": b.get("artist", ""), "MatchedTitle": b.get("title", ""),
            "MatchedYear": b.get("year", ""), "MatchedCountry": b.get("country", ""),
            "MatchedLabel": b.get("label", ""), "Margin": o.get("margin", ""),
            "Evidence": " | ".join(b.get("notes", [])), "WhyNotAuto": " | ".join(o.get("why", [])),
            "Alternatives": " || ".join(o.get("alts", [])),
            "TagArtist": o["tag_artist"], "TagAlbum": o["tag_title"], "TagYear": o["tag_year"],
            "Tracks": o["tracks"], "AlbumArtistFolder": o["artist_folder"],
            "AlbumFolder": o["album_folder"], "FullPath": o["path"],
            "BarcodeFromOCR": o.get("barcodes_from_ocr", ""),
            "CodesFromOCR": o.get("codes_from_ocr", ""),
            "LinkedDiscogsID": b.get("discogs_id", ""),
            "Notes": " | ".join(filter(None, [
                ("duplicate copies ignored: " + ", ".join(o["dropped_formats"]))
                if o.get("dropped_formats") else "",
                (f"{len(o['dropped_tracks'])} duplicate track file(s) ignored, e.g. "
                 + ", ".join(o["dropped_tracks"][:3]))
                if o.get("dropped_tracks") else ""]))}


def export(db, out_dir):
    out_dir = Path(out_dir)
    rows = {"auto": [], "review": [], "nomatch": [], "moved": []}
    for (blob,) in db.execute("SELECT result FROM results"):
        o = json.loads(blob)
        if o["status"] in rows:
            rows[o["status"]].append(to_row(o))
    # Discs MusicBrainz has no disc ID for: one link each to attach it (you check
    # the release on the MusicBrainz page and confirm; nothing is sent from here).
    subs = []
    for (blob,) in db.execute("SELECT result FROM results ORDER BY path"):
        o = json.loads(blob)
        b = o.get("best") or {}
        for did, toc, exact in o.get("discids") or []:
            if exact:
                continue
            n = toc.split()[1]
            subs.append({"Album": o["path"], "DiscID": did, "Tracks": n,
                         "MatchedRelease": b.get("url", "") if o["status"] == "auto" and b.get("source") == "mb" else "",
                         "AttachLink": f"https://musicbrainz.org/cdtoc/attach?id={did}&tracks={n}"
                                       f"&toc={toc.replace(' ', '+')}"})
    with open(out_dir / "discid_submissions.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["Album", "DiscID", "Tracks", "MatchedRelease", "AttachLink"])
        w.writeheader()
        w.writerows(subs)
    for status, items in rows.items():
        key = (lambda r: -float(r["Confidence"] or 0))
        with open(out_dir / f"matches_{status}.csv", "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=COLS)
            w.writeheader()
            w.writerows(sorted(items, key=key))
    return {k: len(v) for k, v in rows.items()}


def load_plugin(plugin_dir):
    d = Path(plugin_dir).expanduser()
    if not (d / "discogs_mapping.py").exists():
        sys.exit(f"discogs_mapping.py not found in {d}\n"
                 "Use --plugin-dir, or: git clone https://github.com/herculepirate/"
                 "picard-discogs-enrichment ~/Documents/Github/picard-discogs-enrichment")
    sys.path.insert(0, str(d))
    import discogs_mapping
    return discogs_mapping


def check_ocr_ready():
    """Fail loudly if --ocr was asked for but cannot actually work."""
    import shutil
    problems = []
    try:
        from PIL import Image  # noqa: F401
    except ImportError:
        problems.append("Pillow missing        -> sudo apt install python3-pil")
    try:
        from pyzbar.pyzbar import decode  # noqa: F401
    except ImportError as exc:
        # pyzbar raises ImportError both when it is not installed and when it
        # is installed but cannot find the zbar library itself.
        if "zbar" in str(exc).lower() and "librar" in str(exc).lower():
            problems.append(f"zbar library missing ({exc}) -> sudo apt install libzbar0")
        else:
            problems.append("pyzbar missing        -> sudo apt install python3-pyzbar")
    except Exception as exc:
        problems.append(f"pyzbar failed to load ({exc}) -> sudo apt install libzbar0 python3-pyzbar")
    if not shutil.which("tesseract"):
        problems.append("tesseract binary missing -> sudo apt install tesseract-ocr")
    if problems:
        sys.exit("--ocr was requested but OCR is not ready:\n  " + "\n  ".join(problems)
                 + "\n(Or in a venv made with --system-site-packages: pip install pillow pyzbar)")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("audit_csv")
    ap.add_argument("--out-dir", default="~/Downloads/matching")
    ap.add_argument("--plugin-dir", default=DEFAULT_PLUGIN_DIR)
    ap.add_argument("--include-partial", action="store_true",
                    help="also look up partially tagged albums (never auto-applied)")
    ap.add_argument("--sample", type=int, help="random sample of N albums (seeded)")
    ap.add_argument("--limit", type=int, help="first N albums only")
    ap.add_argument("--artist", help="only artist folders containing this text")
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--ocr", action="store_true", help="barcode + text OCR on scan images")
    ap.add_argument("--max-images", type=int, default=15,
                    help="most images to OCR per album, back/disc-named first "
                         "(default 15; 0 = no limit)")
    ap.add_argument("--redo", action="store_true", help="ignore saved results")
    ap.add_argument("--retry-review", action="store_true",
                    help="re-evaluate albums previously filed as review (cheap: lookups "
                         "come from the cache)")
    ap.add_argument("--retry-nomatch", action="store_true",
                    help="look up again the albums previously filed as nomatch "
                         "(use after a script fix); matched/review results are kept")
    ap.add_argument("--keep-old-results", action="store_true",
                    help="do not re-evaluate results made by an older version of the scripts")
    ap.add_argument("--config", help="JSON file overriding thresholds/weights")
    args = ap.parse_args()

    if args.ocr:
        check_ocr_ready()
    cfg = dict(mc.DEFAULT_CFG)
    picard_countries = picard_preferred_countries()
    if picard_countries:
        cfg["preferred_countries"] = picard_countries
    if args.config:
        cfg.update(json.load(open(Path(args.config).expanduser())))
    if not fingerprint_ready():
        print("Note: fingerprints (AcoustID) are off: install fpcalc (libchromaprint-tools) and put an "
              "AcoustID application key in ~/.config/tagmender/acoustid_key", file=sys.stderr)
    token = os.environ.get("DISCOGS_TOKEN", "")
    if not token:
        sys.exit("Set DISCOGS_TOKEN (discogs.com/settings/developers -> Generate token)")

    dm = load_plugin(args.plugin_dir)
    global _PLUGIN_STAMP
    _PLUGIN_STAMP = _script_version(plugin_files(dm))[:6]
    out_dir = Path(args.out_dir).expanduser()
    out_dir.mkdir(parents=True, exist_ok=True)
    import fcntl
    lock = open(out_dir / "match.lock", "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        sys.exit(f"Another match_albums.py is already working in {out_dir} "
                 "(is auto_ambient.sh running?). Stop it first, or use a different --out-dir.")
    db = sqlite3.connect(out_dir / "match_state.db")
    db.execute("CREATE TABLE IF NOT EXISTS results (path TEXT PRIMARY KEY, result TEXT)")
    if args.redo:
        db.execute("DELETE FROM results")
    else:
        pass  # --retry-* are handled below without deleting anything
    try:
        who = check_discogs_token(token)
    except AuthError as exc:
        sys.exit(f"\n{exc}")
    if who:
        print(f"Discogs token OK (user: {who})")
    # Earlier versions saved failed lookups as final results; drop them so
    # those albums are looked up again.
    stale = [p for (p, b) in db.execute("SELECT path, result FROM results")
             if json.loads(b).get("status") == "error"
             or any(str(w).startswith("error:") for w in json.loads(b).get("why", []))]
    for p in stale:
        db.execute("DELETE FROM results WHERE path=?", (p,))
    db.commit()
    if stale:
        print(f"Re-queued {len(stale)} albums whose earlier lookup had failed")
    # Albums to decide again. Their old results are NOT deleted first: they stay
    # in the CSVs until the new decision replaces them, so an interrupted run
    # never leaves the review/nomatch lists half empty.
    #   - results from an older version of the scripts (including auto matches),
    #     unless --keep-old-results; lookups and OCR come from the caches;
    #   - with --retry-nomatch / --retry-review, results with that status.
    redo = {}
    retry = {s for s, on in (("nomatch", args.retry_nomatch), ("review", args.retry_review)) if on}
    for (p, b) in db.execute("SELECT path, result FROM results"):
        o = json.loads(b)
        if not args.keep_old_results and o.get("version") != result_version():
            redo[p] = o["status"]
        elif o["status"] in retry:
            redo[p] = o["status"]
        elif o["status"] == "moved" and Path(p).is_dir():
            redo[p] = o["status"]                    # the folder is back: match it
    if redo:
        print(f"Re-evaluating {len(redo)} albums ("
              + ", ".join(f"{n} {s}" for s, n in sorted(Counter(redo.values()).items())) + ")")
    http = Http(db, cfg, token)

    wanted = {"untagged"} | ({"partial"} if args.include_partial else set())
    rows = [r for r in csv.DictReader(open(Path(args.audit_csv).expanduser(), encoding="utf-8"))
            if r["Status"] in wanted]
    if args.artist:
        rows = [r for r in rows if args.artist.lower() in r["AlbumArtistFolder"].lower()]
    if args.sample:
        random.Random(args.seed).shuffle(rows)
        rows = rows[:args.sample]
    if args.limit:
        rows = rows[:args.limit]

    # The loop runner stops a round with SIGTERM when the scripts change, so
    # the next round uses the fix right away. Treat it like Ctrl+C: the album
    # in progress is simply redone next time; finished ones are already saved.
    import signal

    def _stop(_signum, _frame):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, _stop)

    done = {p for (p,) in db.execute("SELECT path FROM results")} - set(redo)
    todo = [r for r in rows if r["FullPath"] not in done]
    # Re-evaluations first (nomatch, then review, then auto), new albums after,
    # so the effect of a fix shows up in the CSVs quickly.
    order = {"nomatch": 0, "review": 1, "auto": 2}
    todo.sort(key=lambda r: order.get(redo.get(r["FullPath"]), 3))
    print(f"{len(rows)} albums selected, {len(rows) - len(todo)} already done, {len(todo)} to go")
    t0 = time.time()
    try:
        for i, row in enumerate(todo, 1):
            try:
                o = process(row, http, cfg, dm, args)
            except KeyboardInterrupt:
                raise
            except AuthError as exc:
                print(f"\nSTOPPED: {exc}\nFix the token and run the same command again; "
                      "finished albums are kept.")
                break
            except Exception as exc:
                # Not filed: it will be retried on the next run.
                print(f"[{i}/{len(todo)}] ERROR (will retry next run) "
                      f"{row['AlbumArtistFolder']} | {row['AlbumFolder']}: "
                      f"{type(exc).__name__}: {exc}")
                continue
            if o["status"] == "error":
                print(f"[{i}/{len(todo)}] LOOKUP FAILED (will retry next run) "
                      f"{o['artist_folder']} | {o['album_folder']}: {'; '.join(o['why'])}")
                continue
            db.execute("INSERT OR REPLACE INTO results VALUES (?,?)",
                       (o["path"], json.dumps(o, default=list)))
            db.commit()
            b = o.get("best") or {}
            print(f"[{i}/{len(todo)}] {o['status']:7s} {str(b.get('confidence', '')):>5} "
                  f"{o['artist_folder']} | {o['album_folder']}"
                  f"{'  <- ' + b['source'] + ':' + b['id'] if b and o['status'] == 'auto' else ''}")
            if i % 25 == 0:
                export(db, out_dir)
    except KeyboardInterrupt:
        print("\nInterrupted - progress is saved; run the same command to resume.")
    counts = export(db, out_dir)
    print(f"\nTotals so far: {counts}\nFiles in {out_dir}: matches_auto/review/nomatch/moved.csv "
          f"({(time.time() - t0) / 60:.1f} min this run)")


if __name__ == "__main__":
    main()
