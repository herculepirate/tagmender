#!/usr/bin/env python3
"""mb_seed.py - add the albums MusicBrainz is missing, in one click each.

For albums TagMender tagged from Discogs or the web (TAGMENDER_SOURCE set, no
MusicBrainz release ID), writes one HTML page with a pre-filled MusicBrainz
"Add release" form per album: title, artist (with its MusicBrainz ID when
known), date, label, barcode, format and every track with its length, plus the
source page as a link and in the edit note. Open the page, press the button,
check the release editor and submit. Nothing is sent from here.

Once MusicBrainz has the release, the matcher finds it on its next re-check and
Picard tags the album like any other.

  python3 mb_seed.py /media/unraidmedia2/music/extreme_music --out ~/Downloads/mb_seeds.html
"""
from __future__ import annotations

import argparse
import html
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import tag_albums as ta  # noqa: E402

import mutagen  # noqa: E402


def _get(f, *keys):
    """A tag's values from any format (Vorbis, ID3 TXXX/frames, MP4 freeform)."""
    tags = getattr(f, "tags", None) or {}
    for k in keys:
        for cand in (k, k.upper(), k.lower(), f"TXXX:{k}", f"----:com.apple.iTunes:{k}"):
            try:
                v = tags.get(cand) if hasattr(tags, "get") else None
            except Exception:
                v = None
            if v:
                vals = v if isinstance(v, list) else [v]
                out = []
                for x in vals:
                    x = getattr(x, "text", x)
                    for y in (x if isinstance(x, list) else [x]):
                        out.append(y.decode("utf-8", "ignore") if isinstance(y, bytes) else str(y))
                return [o for o in out if o]
    return []


ID3_NAMES = {"title": "TIT2", "album": "TALB", "albumartist": "TPE2", "artist": "TPE1", "date": "TDRC",
             "label": "TPUB", "media": "TMED"}
MP4_NAMES = {"title": "\xa9nam", "album": "\xa9alb", "albumartist": "aART", "artist": "\xa9ART", "date": "\xa9day"}


def tag(f, name):
    keys = [name]
    if name in ID3_NAMES:
        keys.append(ID3_NAMES[name])
    if name in MP4_NAMES:
        keys.append(MP4_NAMES[name])
    vals = _get(f, *keys)
    return vals[0] if vals else ""


def candidates(root):
    """Album folders whose files carry TAGMENDER_SOURCE and no MusicBrainz release ID."""
    for dirpath, dirs, names in os.walk(root):
        dirs.sort()
        files = sorted(Path(dirpath) / n for n in names if Path(n).suffix.lower() in ta.AUDIO_EXT)
        if not files:
            continue
        try:
            first = mutagen.File(files[0])
        except Exception:
            continue
        if first is None or not _get(first, "TAGMENDER_SOURCE") or ta.read_ids(files[0])["albumid"]:
            continue
        yield Path(dirpath), files


def seed_fields(files):
    """The release editor's seeding fields for one album."""
    tracks = []
    for p in files:
        f = mutagen.File(p)
        tracks.append((f, tag(f, "title"), tag(f, "artist"), round((f.info.length or 0) * 1000),
                       _get(f, "discnumber", "TPOS") or ["1"]))
    f0 = tracks[0][0]
    fields = [("name", tag(f0, "album")),
              ("artist_credit.names.0.name", tag(f0, "albumartist") or tag(f0, "artist")),
              ("status", "official"), ("type", "album")]
    ids = _get(f0, "MUSICBRAINZ_ALBUMARTISTID", "MusicBrainz Album Artist Id")
    if ids:
        fields.append(("artist_credit.names.0.mbid", ids[0]))
    date = tag(f0, "date")
    for part, value in zip(("year", "month", "day"), date.split("-")):
        if value.isdigit():
            fields.append((f"events.0.date.{part}", value))
    label = tag(f0, "label")
    if label:
        fields.append(("labels.0.name", label))
    barcode = (_get(f0, "BARCODE") or [""])[0]
    fields.append(("barcode", barcode) if barcode else ("no_barcode", "1"))
    media = tag(f0, "media") or "Digital Media"
    discs = {}
    for f, title, artist, ms, disc in tracks:
        d = int(str(disc[0]).split("/")[0] or 1) - 1
        discs.setdefault(d, []).append((title, artist, ms))
    for d, rows in sorted(discs.items()):
        fields.append((f"mediums.{d}.format", media))
        for n, (title, artist, ms) in enumerate(rows):
            fields += [(f"mediums.{d}.track.{n}.name", title), (f"mediums.{d}.track.{n}.number", str(n + 1)),
                       (f"mediums.{d}.track.{n}.length", str(ms))]
            if artist and artist != fields[1][1]:
                fields.append((f"mediums.{d}.track.{n}.artist_credit.names.0.name", artist))
    source = (_get(f0, "TAGMENDER_SOURCE") or [""])[0]
    if source:
        fields.append(("urls.0.url", source))
    fields.append(("edit_note", f"Seeded by TagMender from {source or 'the files'}; track lengths from the files."))
    return fields


def page(albums):
    forms = []
    for folder, fields in albums:
        inputs = "".join(f'<input type="hidden" name="{html.escape(k)}" value="{html.escape(v)}">' for k, v in fields)
        title = dict(fields).get("name", folder.name)
        artist = dict(fields).get("artist_credit.names.0.name", "")
        forms.append(f'<form method="post" action="https://musicbrainz.org/release/add" target="_blank">'
                     f'{inputs}<p><b>{html.escape(artist)} – {html.escape(title)}</b><br>'
                     f'<small>{html.escape(str(folder))}</small><br>'
                     f'<button type="submit">Open in the MusicBrainz release editor</button></p></form>')
    return ("<!doctype html><meta charset=utf-8><title>TagMender: add to MusicBrainz</title>"
            "<body style='font-family:sans-serif;max-width:52rem;margin:2rem auto;padding:0 1rem'>"
            f"<h1>Albums MusicBrainz is missing ({len(albums)})</h1>"
            "<p>Each button opens MusicBrainz's release editor filled in from your files. Check it, then "
            "submit. Log in to MusicBrainz first.</p>" + "".join(forms) + "</body>")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("roots", nargs="+", help="library folders to look through")
    ap.add_argument("--out", default="~/Downloads/mb_seeds.html")
    args = ap.parse_args(argv)
    albums = []
    for root in args.roots:
        for folder, files in candidates(Path(root).expanduser()):
            albums.append((folder, seed_fields(files)))
    out = Path(args.out).expanduser()
    out.write_text(page(albums), encoding="utf-8")
    print(f"{len(albums)} album(s) to add to MusicBrainz: open {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
