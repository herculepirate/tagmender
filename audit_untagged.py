#!/usr/bin/env python3
"""audit_untagged.py - READ-ONLY audit of a music library.

Walks <root>/<Album Artist>/<Date - Album>/ and reports, per album folder,
whether its files carry MusicBrainz and/or Discogs identifiers. Nothing is
written to the library; the only output is a CSV (and an optional log).

Tag markers (the ones Picard and the Discogs Enrichment plugin write):
  MusicBrainz : musicbrainz_albumid  (Release MBID)
  Discogs     : discogs_release_id

Usage:
  python3 audit_untagged.py ~/Music/ambient
  python3 audit_untagged.py ~/Music/metal \
      --out ~/Downloads/audit_extreme.csv

Status values:
  tagged_both     every audio file has both IDs
  musicbrainz_only / discogs_only
  partial         some files tagged, some not (or IDs differ between files)
  untagged        no file has either ID  <- the candidates for matching
  iso_only        no taggable audio, only .iso (SACD) images
  no_audio        folder has no audio at all
"""
import argparse
import csv
import os
import re
import sys
from collections import Counter
from pathlib import Path

try:
    import mutagen
    from mutagen.id3 import ID3
    from mutagen.mp4 import MP4
except ImportError:
    sys.exit("mutagen is required: pip install mutagen")

AUDIO_EXT = {".flac", ".mp3", ".m4a", ".mp4", ".ogg", ".opus", ".oga", ".wv",
             ".ape", ".wav", ".aif", ".aiff", ".dsf", ".dff", ".wma", ".mpc"}
ISO_EXT = {".iso"}
IMAGE_EXT = {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".tif", ".tiff"}
DEFAULT_SKIP = ("various artists", "various", "va", "v.a.", "v/a")

MB_KEY = "musicbrainzalbumid"
DG_KEY = "discogsreleaseid"

# Image file names worth OCR/barcode inspection later.
BACK_HINT = re.compile(r"back|rear|tray|inlay|disc|cd\d?|label|barcode|booklet|insert|inside", re.I)


def norm_key(key):
    """'TXXX:MusicBrainz Album Id' / '----:com.apple.iTunes:MusicBrainz Album Id'
    / 'MUSICBRAINZ_ALBUMID' -> 'musicbrainzalbumid'."""
    if isinstance(key, bytes):
        key = key.decode("utf-8", "ignore")
    k = key.lower()
    for prefix in ("txxx:", "----:com.apple.itunes:", "----:"):
        if k.startswith(prefix):
            k = k[len(prefix):]
    return re.sub(r"[\s_\-]", "", k)


def first_text(value):
    if isinstance(value, (list, tuple)):
        value = value[0] if value else ""
    if isinstance(value, bytes):
        value = value.decode("utf-8", "ignore")
    return str(value).strip()


def read_tags(path):
    """Return (dict of wanted tags, length_seconds). Wanted keys:
    mb, dg, artist, album, date, disc."""
    out = {"mb": "", "dg": "", "artist": "", "album": "", "date": "", "disc": ""}
    length = 0.0
    ext = path.suffix.lower()
    try:
        if ext in (".m4a", ".mp4"):
            f = MP4(path)
            length = f.info.length
            for k, v in (f.tags or {}).items():
                nk = norm_key(k)
                if nk == MB_KEY:
                    out["mb"] = first_text(v)
                elif nk == DG_KEY:
                    out["dg"] = first_text(v)
                elif k == "\xa9alb":
                    out["album"] = first_text(v)
                elif k == "aART":
                    out["artist"] = first_text(v)
                elif k == "\xa9ART" and not out["artist"]:
                    out["artist"] = first_text(v)
                elif k == "\xa9day":
                    out["date"] = first_text(v)
                elif k == "disk" and v:
                    out["disc"] = str(v[0][0])
            return out, length

        f = mutagen.File(path)
        if f is None:
            return out, 0.0
        length = getattr(f.info, "length", 0.0) or 0.0
        tags = f.tags
        if tags is None:
            return out, length

        if isinstance(tags, ID3) or tags.__class__.__module__.startswith("mutagen.id3"):
            for frame in tags.getall("TXXX"):
                nk = norm_key(frame.desc)
                if nk == MB_KEY:
                    out["mb"] = first_text(frame.text)
                elif nk == DG_KEY:
                    out["dg"] = first_text(frame.text)
            for fid, field in (("TALB", "album"), ("TPE2", "artist"),
                               ("TDRC", "date"), ("TPOS", "disc")):
                fr = tags.getall(fid)
                if fr:
                    out[field] = first_text([str(t) for t in fr[0].text])
            if not out["artist"]:
                fr = tags.getall("TPE1")
                if fr:
                    out["artist"] = first_text([str(t) for t in fr[0].text])
            return out, length

        # Vorbis-style comments (FLAC/OGG/OPUS/WavPack/APE) and similar dicts.
        items = tags.items() if hasattr(tags, "items") else []
        lowered = {}
        for k, v in items:
            lowered.setdefault(norm_key(str(k)), v)
        out["mb"] = first_text(lowered.get(MB_KEY, ""))
        out["dg"] = first_text(lowered.get(DG_KEY, ""))
        out["album"] = first_text(lowered.get("album", ""))
        out["artist"] = first_text(lowered.get("albumartist", "")
                                   or lowered.get("artist", ""))
        out["date"] = first_text(lowered.get("date", "") or lowered.get("year", ""))
        out["disc"] = first_text(lowered.get("discnumber", "") or lowered.get("disc", ""))
    except Exception as exc:  # unreadable file: report, don't crash
        out["_error"] = f"{type(exc).__name__}: {exc}"
    return out, length


_DISC_DIR = re.compile(r"^(?:cd|disc|disk|dvd|side)\s*[-_.]?\s*\d+\b|^\d{1,2}$", re.I)


def album_dirs(top):
    """Album folders under an artist folder, however deep (Artist/Albums/<album>); a
    disc folder counts as its parent's. Same rule as match_albums.album_dirs."""
    owners = []
    for dirpath, dirs, names in os.walk(top):
        dirs[:] = sorted(d for d in dirs if not d.startswith((".", "_")) and d != "2delete")
        d = Path(dirpath)
        if d == Path(top) or not any(Path(n).suffix.lower() in AUDIO_EXT for n in names):
            continue
        owner = d.parent if _DISC_DIR.match(d.name) and d.parent != Path(top) else d
        if owner not in owners:
            owners.append(owner)
    # every direct subfolder still gets a row (one holding only an ISO or scans, as before)
    for c in sorted(p for p in Path(top).iterdir()
                    if p.is_dir() and not p.name.startswith((".", "_")) and p.name != "2delete"):
        if not any(o == c or c in o.parents for o in owners):
            owners.append(c)
    return sorted(owners)


def audit_album(album_dir, log):
    audio, isos, images = [], [], []
    for dirpath, _dirs, files in os.walk(album_dir):
        for name in sorted(files):
            p = Path(dirpath) / name
            ext = p.suffix.lower()
            if ext in AUDIO_EXT:
                audio.append(p)
            elif ext in ISO_EXT:
                isos.append(p)
            elif ext in IMAGE_EXT:
                images.append(p.relative_to(album_dir).as_posix())

    n = len(audio)
    mb_ids, dg_ids = Counter(), Counter()
    mb_n = dg_n = 0
    total = 0.0
    artist = album = date = ""
    discs = set()
    fmts = Counter()
    errors = 0
    for p in audio:
        t, length = read_tags(p)
        fmts[p.suffix.lower().lstrip(".")] += 1
        total += length
        if "_error" in t:
            errors += 1
            log.write(f"{p}\t{t['_error']}\n")
        if t["mb"]:
            mb_n += 1
            mb_ids[t["mb"]] += 1
        if t["dg"]:
            dg_n += 1
            dg_ids[t["dg"]] += 1
        artist = artist or t["artist"]
        album = album or t["album"]
        date = date or t["date"]
        if t["disc"]:
            discs.add(t["disc"].split("/")[0].strip())

    if n == 0:
        status = "iso_only" if isos else "no_audio"
    else:
        has_mb, has_dg = mb_n > 0, dg_n > 0
        consistent = len(mb_ids) <= 1 and len(dg_ids) <= 1
        full_mb, full_dg = mb_n == n, dg_n == n
        if not has_mb and not has_dg:
            status = "untagged"
        elif not consistent:
            status = "partial"
        elif full_mb and full_dg:
            status = "tagged_both"
        elif full_mb and not has_dg:
            status = "musicbrainz_only"
        elif full_dg and not has_mb:
            status = "discogs_only"
        else:
            status = "partial"

    candidate_imgs = [i for i in images if BACK_HINT.search(Path(i).stem)]
    return {
        "Status": status,
        "Tracks": n,
        "Discs": len(discs) or "",
        "TotalSeconds": round(total),
        "TagAlbumArtist": artist,
        "TagAlbum": album,
        "TagDate": date,
        "MB_Files": mb_n,
        "Discogs_Files": dg_n,
        "MB_ReleaseIDs": ";".join(mb_ids),
        "Discogs_ReleaseIDs": ";".join(dg_ids),
        "Formats": ";".join(f"{k}:{v}" for k, v in sorted(fmts.items())),
        "IsoFiles": len(isos),
        "ImageCount": len(images),
        "OcrCandidateImages": ";".join(candidate_imgs),
        "AllImages": ";".join(images),
        "ReadErrors": errors,
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("root", help="library root, e.g. .../ambient_music")
    ap.add_argument("--out", help="CSV path (default ~/Downloads/audit_<root>.csv)")
    ap.add_argument("--skip", default=",".join(DEFAULT_SKIP),
                    help="comma list of artist-folder names to skip (case-insensitive)")
    args = ap.parse_args()

    root = Path(args.root).expanduser()
    if not root.is_dir():
        sys.exit(f"Not a directory: {root}")
    skip = {s.strip().lower() for s in args.skip.split(",") if s.strip()}
    out_path = Path(args.out).expanduser() if args.out else \
        Path.home() / "Downloads" / f"audit_{root.name}.csv"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    log_path = out_path.with_suffix(".errors.log")

    fields = ["AlbumArtistFolder", "AlbumFolder", "FullPath", "Status", "Tracks",
              "Discs", "TotalSeconds", "TagAlbumArtist", "TagAlbum", "TagDate",
              "MB_Files", "Discogs_Files", "MB_ReleaseIDs", "Discogs_ReleaseIDs",
              "Formats", "IsoFiles", "ImageCount", "OcrCandidateImages",
              "AllImages", "ReadErrors"]

    summary = Counter()
    skipped = []
    artists = sorted(p for p in root.iterdir() if p.is_dir())
    with open(out_path, "w", newline="", encoding="utf-8") as fh, \
            open(log_path, "w", encoding="utf-8") as log:
        w = csv.DictWriter(fh, fieldnames=fields)
        w.writeheader()
        for ai, artist_dir in enumerate(artists, 1):
            if artist_dir.name.strip().lower() in skip:
                skipped.append(artist_dir.name)
                continue
            for album_dir in album_dirs(artist_dir):
                row = audit_album(album_dir, log)
                row.update({"AlbumArtistFolder": artist_dir.name,
                            "AlbumFolder": album_dir.name,
                            "FullPath": str(album_dir)})
                w.writerow(row)
                summary[row["Status"]] += 1
            if ai % 25 == 0:
                print(f"  ...{ai}/{len(artists)} artist folders", file=sys.stderr)
                fh.flush()

    total = sum(summary.values())
    print(f"\nScanned {total} album folders under {root}")
    for status, count in summary.most_common():
        print(f"  {status:17s} {count}")
    if skipped:
        print(f"Skipped artist folders: {', '.join(skipped)}")
    print(f"\nCSV: {out_path}\nRead errors log: {log_path}")


if __name__ == "__main__":
    main()
