#!/usr/bin/env python3
"""coverup_batch.py - CoverUp artwork for many albums at once, outside Picard.

Uses the CoverUp plugin's own code (sources, scoring, auto-apply rules, file writer with
backups) with your CoverUp settings from Picard.ini. Pillow replaces Qt for images and
mutagen replaces Picard for embedding.

Two passes:
  --pass missing   albums with no art at all (no image in the folder, nothing embedded)
  --pass full      every album: better front, back cover and artist photo where available

Rules on top of CoverUp's:
  * local first: a local image at least --local-min px (default 1000) on its short side is
    used before anything online. An unlabelled local image ("Album Name.jpg") counts as the
    front when its picture matches the front found online, or when it is the only image in
    the album folder and roughly square.
  * a local scan of an unfolded cover (two or three covers wide) is cut into panels; the
    panel matching the online front is used (or, with nothing to compare, the right-hand
    panel, marked as a guess in the plan). Square-ish or odd-shaped images are never cut.
  * only the front is embedded, at most embed_max_side (1200 px); files are written in full.
  * low-res option (--upscale-to, default 500): images down to --low-res-floor (250) are accepted
    when nothing bigger exists, and enlarged with CoverUp's own "enlarge to minimum" step.
  * artist photos go to the album-artist folder (named by your preset: folder.jpg for Kodi).
  * generated covers are not made in a batch; albums with nothing found are listed.

Dry run first (writes only the plan), then --apply:
  python3 coverup_batch.py --audit ~/Downloads/audit_ambient_music.csv --pass missing
  python3 coverup_batch.py --audit ~/Downloads/audit_ambient_music.csv --pass missing --apply
Undo an apply:
  python3 coverup_batch.py --undo <batch id printed by --apply>
"""
from __future__ import annotations

import argparse
import base64
import configparser
import csv
import hashlib
import io
import json
import os  # noqa: F401
import random
import re
import sqlite3
import sys
import time
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import companions  # noqa: E402
import coverup_loader  # noqa: E402
import coverup_pil_render as R  # noqa: E402

DEFAULT_COVERUP = str(companions.coverup_dir())      # env, ~/Documents/Github clone, or bundled
DEFAULT_INI = "~/.var/app/org.musicbrainz.Picard/config/MusicBrainz/Picard.ini"
COVERUP_UUID = "be95dccc-b521-488a-843f-4af29033de41"
LIST_KEYS = {"apply_types", "embed_types", "providers_enabled", "provider_order", "artist_types",
             "creative_triggers", "creative_method_order"}
SPREAD_MIN_RATIO = 1.6          # wider (or taller) than this = several covers side by side
SQUAREISH = (0.8, 1.25)


# --------------------------------------------------------------------------
# Settings: your CoverUp settings from Picard.ini, plus this batch's decisions
# --------------------------------------------------------------------------
def _convert(key, raw):
    v = raw.strip()
    if v.startswith('"') and v.endswith('"'):
        v = v[1:-1].replace('\\"', '"')
    if v.startswith("@Variant") or v == "@Invalid()":
        return None                       # Qt-serialised value: use CoverUp's default
    if key in LIST_KEYS:
        return [x.strip() for x in v.split(",") if x.strip()]
    if v.lower() in ("true", "false"):
        return v.lower() == "true"
    if re.fullmatch(r"-?\d+", v):
        return int(v)
    if re.fullmatch(r"-?\d+\.\d+", v):
        return float(v)
    return v


def picard_values(ini_path):
    """(CoverUp plugin values, Picard [setting] values) from Picard.ini."""
    cp = configparser.RawConfigParser(strict=False, interpolation=None)
    cp.optionxform = str
    cp.read(Path(ini_path).expanduser(), encoding="utf-8")
    sec = f"plugin.{COVERUP_UUID}"
    plugin = {k: _convert(k, v) for k, v in cp.items(sec)} if cp.has_section(sec) else {}
    picard = {k: v.strip() for k, v in cp.items("setting")} if cp.has_section("setting") else {}
    return {k: v for k, v in plugin.items() if v is not None}, picard


def with_tagmender_keys(plugin):
    """CoverUp's API keys from TagMender's credential store (`tagmender setup`) where
    Picard.ini has none, so CoverUp works on a machine without Picard. Keys set in
    CoverUp's own options always win."""
    import credentials
    out = dict(plugin)
    for name, key in credentials.COVERUP_KEYS.items():
        if out.get(key):
            continue
        try:
            v = credentials.get(name)
        except credentials.CredentialError:
            v = ""
        if v:
            out[key] = v
    return out


def batch_settings(ini_path, pass_name):
    """Your CoverUp settings, with the choices made for this batch applied on top."""
    from coverup.settings import Settings
    plugin, picard = picard_values(ini_path)
    plugin = with_tagmender_keys(plugin)
    s = Settings.from_mapping(plugin)
    overrides = {
        "embed_types": ["front"],          # what Picard does today ("embed only a single front image")
        "save_artist_art": True,           # artist photos go to the artist folder (Kodi name: folder.jpg)
        "artist_art": True,
        "artist_types": ["thumb"],
        "creative_triggers": [],           # no generated covers in a batch
        "creative_always": False,
        "apply_types": ["front", "back", "artist"],
        "http_cache_hours": max(24 * 30, int(s["http_cache_hours"] or 0)),
    }
    if picard.get("save_images_to_tags", "true") == "false":
        overrides["embed"] = False
    return s.with_(**overrides), plugin, picard


# --------------------------------------------------------------------------
# Reading the album: files, tags, IDs, embedded art
# --------------------------------------------------------------------------
_ID_TAGS = {"musicbrainzalbumid": "release_mbid", "musicbrainzreleasegroupid": "rg_mbid",
            "musicbrainzalbumartistid": "artist_mbid", "discogsreleaseid": "discogs_release_id"}


def _norm(k):
    k = k.decode() if isinstance(k, bytes) else str(k)
    k = k.lower().replace("----:com.apple.itunes:", "").replace("txxx:", "")
    return re.sub(r"[\s_\-]", "", k)


def _vals(v):
    vals = v if isinstance(v, (list, tuple)) else [v]
    out = []
    for x in vals:
        x = x.decode() if isinstance(x, bytes) else str(x)
        out += [s.strip() for s in x.split(";") if s.strip()]
    return out


def _put(ids, key, v):
    vals = _vals(v)
    if vals:
        ids[key] = ";".join(vals) if key == "artist_mbid" else vals[0]


def read_ids_and_art(path):
    """Release IDs and embedded pictures [(type, mime, desc, data)] of one audio file."""
    import mutagen
    from mutagen.flac import FLAC, Picture
    from mutagen.id3 import ID3
    from mutagen.mp4 import MP4
    ids, pics = {}, []
    try:
        f = mutagen.File(path)
    except Exception:
        return ids, pics
    if f is None:
        return ids, pics
    tags = f.tags
    if isinstance(f, FLAC):
        pics = [(p.type, p.mime, p.desc, p.data) for p in f.pictures]
    if isinstance(f, MP4):
        for k, v in (tags or {}).items():
            nk = _norm(k)
            if nk in _ID_TAGS and v:
                _put(ids, _ID_TAGS[nk], v)
        for c in (tags or {}).get("covr", []):
            pics.append((3, "image/png" if c.imageformat == 14 else "image/jpeg", "", bytes(c)))
        return ids, pics
    if tags is None:
        return ids, pics
    if isinstance(tags, ID3) or tags.__class__.__module__.startswith("mutagen.id3"):
        for fr in tags.getall("TXXX"):
            nk = _norm(fr.desc)
            if nk in _ID_TAGS and fr.text:
                _put(ids, _ID_TAGS[nk], list(fr.text))
        for fr in tags.getall("APIC"):
            pics.append((int(fr.type), fr.mime, fr.desc, fr.data))
        return ids, pics
    for k, v in tags.items():
        nk = _norm(k)
        if nk in _ID_TAGS and v:
            _put(ids, _ID_TAGS[nk], v)
        if nk == "metadatablockpicture":
            for b64 in (v if isinstance(v, list) else [v]):
                try:
                    p = Picture(base64.b64decode(b64))
                    pics.append((p.type, p.mime, p.desc, p.data))
                except Exception:
                    pass
    return ids, pics


def album_info(album_dir, ma, mc, match_db=None, files=None):
    """Everything the batch needs about one album folder - or, with `files`, about those
    files only (an album sharing its folder: the others' tags, IDs and pictures stay out)."""
    from coverup.imageinfo import probe
    if files is not None:
        files = sorted(Path(p) for p in files)
    else:
        files = sorted(p for p in Path(album_dir).rglob("*")
                       if p.is_file() and p.suffix.lower() in ma.AUDIO_EXT and not p.name.startswith("."))
    tags = [ma.read_file(p) for p in files]
    ids, front_embedded, any_embedded, front_embedded_data = {}, None, False, None
    embedded_generated = False
    for p in files:
        i, pics = read_ids_and_art(p)
        for k, v in i.items():
            ids.setdefault(k, v)
        if pics:
            any_embedded = True
            for t, _m, _d, data in pics:
                if t == 3 and R.is_generated(data):
                    embedded_generated = True
                if t == 3 and front_embedded is None and not R.is_generated(data):
                    info = probe(data[:65536])
                    front_embedded = (info[0], info[1]) if info else (0, 0)
                    front_embedded_data = data
    def top(key):
        vals = [t[key] for t in tags if t.get(key)]
        return max(set(vals), key=vals.count) if vals else ""
    folder = Path(album_dir).name
    m = re.match(r"\s*(\d{4})\s*-\s*(.*)", folder)
    title = top("album") or (m.group(2) if m else folder)
    year = top("year") or (int(m.group(1)) if m else None)
    discs = {t["disc"] for t in tags if t.get("disc")}
    # release IDs found by the matcher for albums without them in the tags
    if match_db and not ids.get("release_mbid") and not ids.get("discogs_release_id"):
        row = match_db.execute("SELECT result FROM results WHERE path=?", (str(album_dir),)).fetchone()
        if row:
            o = json.loads(row[0])
            b = o.get("best") or {}
            if o.get("status") == "auto" and b:
                if b.get("source") == "mb":
                    ids["release_mbid"] = b["id"]
                    if b.get("discogs_id"):
                        ids["discogs_release_id"] = b["discogs_id"]
                elif b.get("source") == "discogs":
                    ids["discogs_release_id"] = b["id"]
                    if b.get("mb_id"):
                        ids["release_mbid"] = b["mb_id"]
    images = [p for p in Path(album_dir).rglob("*")
              if p.is_file() and p.suffix.lower() in (".jpg", ".jpeg", ".png", ".tif", ".tiff", ".webp", ".gif", ".bmp")
              and ".coverup-backup" not in p.parts]
    catnos = [t["catno"] for t in tags if t.get("catno")] + mc.catno_hints(folder)
    return {
        "dir": Path(album_dir), "files": files, "artist": top("artist") or Path(album_dir).parent.name,
        "album": mc.search_title(title), "year": year, "ids": ids,
        "barcode": top("barcode"), "catno": catnos[0] if catnos else "",
        "discs": len(discs) or 1, "tracks": len(files), "front_embedded": front_embedded,
        "any_embedded": any_embedded, "images": images,
        "front_embedded_data": front_embedded_data, "embedded_generated": embedded_generated,
        "collab": is_collaboration(ids.get("artist_mbid", ""), Path(album_dir).parent.name, mc),
        "artist_image": biggest_image(Path(album_dir).parent),
        "no_vox": bool(NO_VOX.search(str(album_dir)) or NO_VOX.search(title or "")),
    }


# Instrumental "[no vox]" editions keep their own front cover (often made for the edition).
NO_VOX = re.compile(r"no[\s._-]?vox", re.I)


def is_collaboration(artist_mbids, folder_name, mc):
    """Several album artists. Tags written by Picard / Discogs Enrichment hold one ID per artist, so
    two IDs decide it (and one ID means a single act such as 'Simon & Garfunkel'); without IDs the
    folder name is split the way the matcher splits credits."""
    ids = {x.strip() for x in str(artist_mbids or "").split(";") if x.strip()}
    if ids:
        return len(ids) > 1
    return len(mc.artist_parts(folder_name)) > 1


def biggest_image(folder):
    """(width, height) of the largest image directly in a folder, or None."""
    from PIL import Image
    best = None
    try:
        entries = list(Path(folder).iterdir())
    except OSError:
        return None
    for p in entries:
        if p.is_file() and p.suffix.lower() in (".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".tif", ".tiff"):
            try:
                with Image.open(p) as im:          # reads the header only
                    d = im.size
            except Exception:
                continue
            if d and (best is None or d[0] * d[1] > best[0] * best[1]):
                best = (d[0], d[1])
    return best


def make_query(info):
    from coverup.model import Query
    ids = info["ids"]
    dg = ids.get("discogs_release_id") or ""
    return Query(
        artist=info["artist"], album=info["album"], year=info["year"] or None,
        barcode=info["barcode"], catalog_number=info["catno"],
        medium_count=info["discs"], track_count=info["tracks"],
        release_mbid=ids.get("release_mbid", ""), rg_mbid=ids.get("rg_mbid", ""),
        artist_mbid=(ids.get("artist_mbid", "").split(";")[0].strip()),
        discogs_release_id=int(dg) if str(dg).isdigit() else None,
        album_dirs=[str(info["dir"])],
    )


# --------------------------------------------------------------------------
# Local first, and cutting unfolded scans into panels
# --------------------------------------------------------------------------
def panels(data, img):
    """Crops of an unfolded scan: n panels side by side (or stacked), n = 2 or 3.
    Returns [(label, jpeg bytes, w, h)], or [] when the image is not a spread."""
    w, h = img.width, img.height
    ratio = max(w, h) / max(1, min(w, h))
    if ratio < SPREAD_MIN_RATIO:
        return []
    n = 3 if ratio >= 2.6 else 2
    if abs(ratio - n) > 0.35:           # not a whole number of covers: odd format, leave it
        return []
    out = []
    names = {2: ["left", "right"], 3: ["left", "middle", "right"]}[n] if w >= h else \
        {2: ["top", "bottom"], 3: ["top", "middle", "bottom"]}[n]
    for i in range(n):
        if w >= h:
            box = (round(i * w / n), 0, round((i + 1) * w / n), h)
        else:
            box = (0, round(i * h / n), w, round((i + 1) * h / n))
        crop = img.crop(box)
        buf = io.BytesIO()
        _jpeg(crop, buf, img)
        out.append((names[i], buf.getvalue(), crop.width, crop.height))
    return out


def _jpeg(im, buf, src):
    """im as a JPEG into buf: depth-safe (16-bit scans), keeping src's colour profile."""
    from coverup_pil_imaging import to_rgb
    icc = src.info.get("icc_profile")
    to_rgb(im).save(buf, "JPEG", quality=95, **({"icc_profile": icc} if icc else {}))


def square_of_panel(label, data):
    """A panel that is not square (the scan includes part of the spine or a margin) cut to a square
    against its outer edge, where the front sits: right panel -> right edge, left -> left, etc."""
    from PIL import Image
    img = Image.open(io.BytesIO(data))
    w, h = img.width, img.height
    s = min(w, h)
    if max(w, h) <= s * 1.05:
        return None
    if w > h:
        x = {"left": 0, "right": w - s}.get(label, (w - s) // 2)
        box = (x, 0, x + s, s)
    else:
        y = {"top": 0, "bottom": h - s}.get(label, (h - s) // 2)
        box = (0, y, s, y + s)
    buf = io.BytesIO()
    _jpeg(img.crop(box), buf, img)
    return buf.getvalue(), s, s


def local_front(res, info, local_min, hamming_threshold, log):
    """Pick the local front by the batch's 'local first' rule. Returns (Candidate, reason) or (None, why)."""
    from coverup import imaging
    from coverup.matching.phash import hamming
    from coverup.model import ArtType, Candidate, Tier

    sel = res.selections.get(ArtType.FRONT)
    online = [c for c in res.candidates if c.provider not in ("local", "embedded") and c.art_type == ArtType.FRONT
              and c.phash is not None]
    ref = [sel.winner.phash] if sel and sel.winner and sel.winner.provider != "local" and sel.winner.phash is not None \
        else [c.phash for c in online]
    locals_ = [c for c in res.candidates if c.provider == "local" and c.art_type in (ArtType.FRONT, ArtType.OTHER)
               and not R.is_generated(c.data)]
    root_imgs = [c for c in locals_ if Path(c.extra.get("local_path", "")).parent == info["dir"]]
    options = []
    for c in locals_:
        try:
            img = imaging.load(c.data)
        except Exception:
            continue
        pieces = panels(c.data, img)
        if pieces:
            best = None
            for label, data, w, h in pieces:
                variants = [(data, w, h, 1)]
                sq = square_of_panel(label, data) if ref else None
                if sq:
                    variants.append((*sq, 0))              # on a tie, the square crop wins
                for vdata, vw, vh, pref in variants:
                    fp = imaging.fingerprint(vdata)
                    dist = min((hamming(fp.phash, r) for r in ref), default=None)
                    if dist is not None and dist <= hamming_threshold and \
                            (best is None or (dist, pref) < (best[0], best[5])):
                        best = (dist, label, vdata, vw, vh, pref)
            if best:
                dist, label, data, w, h, _pref = best
                options.append((data, w, h, f"{label} panel of {c.comment} (matches the online front)", c, True))
            elif not ref and c.art_type == ArtType.FRONT:
                label, data, w, h = pieces[-1] if img.width >= img.height else pieces[0]
                options.append((data, w, h, f"{label} panel of {c.comment} (GUESS: nothing online to compare)", c, False))
            continue
        ratio = c.width / max(1, c.height)
        if not (SQUAREISH[0] <= ratio <= SQUAREISH[1]):
            continue                        # odd shape: leave to CoverUp
        if c.art_type == ArtType.FRONT:
            options.append((c.data, c.width, c.height, f"local {c.comment}", c, True))
        elif c.phash is not None and ref and min(hamming(c.phash, r) for r in ref) <= hamming_threshold:
            options.append((c.data, c.width, c.height, f"local {c.comment} (matches the online front)", c, True))
        elif not ref and len(root_imgs) == 1 and c is root_imgs[0]:
            options.append((c.data, c.width, c.height, f"local {c.comment} (only image in the folder)", c, True))
    options = [o for o in options if min(o[1], o[2]) >= local_min]
    if not options:
        return None, ""
    data, w, h, why, src, _confirmed = max(options, key=lambda o: o[1] * o[2])
    cand = Candidate("local", src.provider_id, src.url, ArtType.FRONT, Tier.A, width=w, height=h,
                     bytes=len(data), data=data, thumb_data=data, dims_known=True, comment=why,
                     attribution="local file")
    cand.extra["local_path"] = src.extra.get("local_path", "")
    cand.extra["cropped"] = data is not src.data
    return cand, why


def local_typed(res, art, local_min):
    """A labelled local image of this type (back...) that is hi-res, or None."""
    cands = [c for c in res.candidates if c.provider == "local" and c.art_type == art
             and c.dims_known and min(c.width, c.height) >= local_min]
    return max(cands, key=lambda c: c.width * c.height, default=None)


# --------------------------------------------------------------------------
# Embedding the front (mutagen), with a backup of what was there
# --------------------------------------------------------------------------
def embed_front(files, jpeg, w, h, desc, backup):
    """Replace embedded front pictures with `jpeg` in every file. Previous pictures are
    recorded in `backup` (dict path -> list) so undo can put them back."""
    import mutagen
    from mutagen.flac import FLAC, Picture
    from mutagen.id3 import APIC, ID3
    from mutagen.mp4 import MP4, MP4Cover
    done, skipped = 0, []
    import tagio
    for p in files:
        if tagio.fragmented(p):
            skipped.append(f"{p.name}: a fragmented MP4 (not written to: its audio would no longer play)")
            continue
        try:
            f = mutagen.File(p)
        except Exception as exc:
            skipped.append(f"{p.name}: {exc}")
            continue
        if f is None:
            skipped.append(f"{p.name}: unreadable")
            continue
        _ids, before = read_ids_and_art(p)
        backup[str(p)] = [(t, m, d, base64.b64encode(data).decode()) for t, m, d, data in before]
        pic = Picture()
        pic.type, pic.mime, pic.desc, pic.data, pic.width, pic.height, pic.depth = 3, "image/jpeg", desc, jpeg, w, h, 24
        if isinstance(f, FLAC):
            keep = [x for x in f.pictures if x.type != 3]
            f.clear_pictures()
            for x in keep:
                f.add_picture(x)
            f.add_picture(pic)
        elif isinstance(f, MP4):
            f.tags = f.tags or {}
            f.tags["covr"] = [MP4Cover(jpeg, imageformat=MP4Cover.FORMAT_JPEG)]
        elif f.tags is not None and (isinstance(f.tags, ID3) or f.tags.__class__.__module__.startswith("mutagen.id3")):
            for k in [k for k in list(f.tags.keys()) if k.startswith("APIC") and f.tags[k].type == 3]:
                del f.tags[k]
            f.tags.add(APIC(encoding=3, mime="image/jpeg", type=3, desc=desc, data=jpeg))
        elif hasattr(f, "tags") and f.tags is not None and hasattr(f.tags, "keys"):   # Ogg Vorbis / Opus
            existing = []
            for b64 in f.tags.get("metadata_block_picture", []):
                try:
                    q = Picture(base64.b64decode(b64))
                    if q.type != 3:
                        existing.append(b64)
                except Exception:
                    pass
            f.tags["metadata_block_picture"] = existing + [base64.b64encode(pic.write()).decode()]
        else:
            skipped.append(f"{p.name}: format not supported for embedding")
            backup.pop(str(p), None)
            continue
        f.save()
        done += 1
    return done, skipped


def restore_embedded(path, pics):
    import mutagen
    from mutagen.flac import FLAC, Picture
    from mutagen.id3 import APIC, ID3
    from mutagen.mp4 import MP4, MP4Cover
    import tagio
    if tagio.fragmented(path):
        raise ValueError(f"{Path(path).name} is a fragmented MP4: not written to")
    f = mutagen.File(path)
    raw = [(t, m, d, base64.b64decode(b)) for t, m, d, b in pics]
    if isinstance(f, FLAC):
        f.clear_pictures()
        for t, m, d, data in raw:
            q = Picture()
            q.type, q.mime, q.desc, q.data = t, m, d, data
            f.add_picture(q)
    elif isinstance(f, MP4):
        if raw:
            f.tags["covr"] = [MP4Cover(data, imageformat=MP4Cover.FORMAT_PNG if m == "image/png" else MP4Cover.FORMAT_JPEG)
                              for _t, m, _d, data in raw]
        elif f.tags and "covr" in f.tags:
            del f.tags["covr"]
    elif f.tags is not None and (isinstance(f.tags, ID3) or f.tags.__class__.__module__.startswith("mutagen.id3")):
        f.tags.delall("APIC")
        for t, m, d, data in raw:
            f.tags.add(APIC(encoding=3, mime=m, type=t, desc=d, data=data))
    else:
        out = []
        for t, m, d, data in raw:
            q = Picture()
            q.type, q.mime, q.desc, q.data = t, m, d, data
            out.append(base64.b64encode(q.write()).decode())
        if out:
            f.tags["metadata_block_picture"] = out
        elif "metadata_block_picture" in f.tags:
            del f.tags["metadata_block_picture"]
    f.save()


# --------------------------------------------------------------------------
# One album
# --------------------------------------------------------------------------
def decide(pipe, res, info, s, args, log):
    """What to do for this album: list of dict(type, candidate, source, why, local)."""
    from coverup.model import ArtType
    types = {ArtType.FRONT, ArtType.BACK, ArtType.ARTIST_THUMB}
    plan, notes = pipe.auto_plan(res, types)
    by_type = {it.art_type: it for it in plan}
    actions = []

    from coverup.model import Candidate, Tier
    th = int(s["hamming_threshold"])
    area = lambda wh: (wh[0] * wh[1]) if wh else 0                      # noqa: E731
    dims = lambda c: (c.width, c.height) if c.dims_known else None      # noqa: E731

    # FRONT: local first
    lf, why = local_front(res, info, args.local_min, th, log)
    if lf is not None:
        actions.append({"type": "front", "cand": lf, "why": why, "local": True})
    else:
        # Never replace what is already there with something smaller. Sizes are the images' own
        # sizes, before any enlarging. A bigger embedded front is saved as the file instead.
        have = best_local(res, ArtType.FRONT, th)
        choices = []
        if ArtType.FRONT in by_type:
            c = by_type[ArtType.FRONT].candidate
            choices.append((area(dims(c)), 1, c, f"{c.provider} {c.tier.value}-tier {c.width}x{c.height}"))
        fe, fdata = info.get("front_embedded"), info.get("front_embedded_data")
        if fe and fdata and area(fe) > 0:
            c = Candidate("embedded", "embedded-front", "", ArtType.FRONT, Tier.A, width=fe[0], height=fe[1],
                          bytes=len(fdata), data=fdata, thumb_data=fdata, dims_known=True,
                          comment="embedded front", attribution="embedded in the music files")
            choices.append((area(fe), 0, c, f"embedded front {fe[0]}x{fe[1]} (bigger than anything online)"))
        if choices:
            a_, _p, c, why = max(choices, key=lambda x: (x[0], x[1]))
            if a_ > area(have):
                actions.append({"type": "front", "cand": c, "why": why, "local": False})
            else:
                notes.append(f"front: existing file {have[0]}x{have[1]} is as large as anything found")
    # BACK
    if local_typed(res, ArtType.BACK, args.local_min) is None and ArtType.BACK in by_type:
        c = by_type[ArtType.BACK].candidate
        have = best_local(res, ArtType.BACK, th)
        if area(dims(c)) > area(have):
            actions.append({"type": "back", "cand": c, "why": f"{c.provider} {c.width}x{c.height}", "local": False})
        else:
            notes.append(f"back: existing file {have[0]}x{have[1]} is as large as anything found")
    # ARTIST: not for collaborations (a photo of one of them would stand for both)
    if ArtType.ARTIST_THUMB in by_type:
        c = by_type[ArtType.ARTIST_THUMB].candidate
        have = info.get("artist_image")
        if info.get("collab"):
            notes.append("artist: collaboration folder, no single-artist photo")
        elif area(dims(c)) > area(have):
            actions.append({"type": "artist", "cand": c, "why": f"{c.provider} {c.width}x{c.height}", "local": False})
        else:
            notes.append(f"artist: existing photo {have[0]}x{have[1]} is as large as anything found")
    return actions, notes


def best_local(res, art, hamming_threshold):
    """(w, h) of the biggest image already in the album folder for this type, or None. Unlabelled
    images count for the front when they look like the online front."""
    from coverup.matching.phash import hamming
    from coverup.model import ArtType
    ref = [c.phash for c in res.candidates if c.provider not in ("local", "embedded")
           and c.art_type == art and c.phash is not None]
    best = None
    for c in res.candidates:
        if c.provider != "local" or not c.dims_known:
            continue
        ok = c.art_type == art or (
            art == ArtType.FRONT and c.art_type == ArtType.OTHER and c.phash is not None and ref
            and min(hamming(c.phash, r) for r in ref) <= hamming_threshold)
        if ok and (best is None or c.width * c.height > best[0] * best[1]):
            best = (c.width, c.height)
    return best


# --------------------------------------------------------------------------
# Generated covers: only for albums left with no usable front at all
# --------------------------------------------------------------------------
LIBRARY_GENRES = {"ambient_music": "ambient", "extreme_music": "extreme metal",
                  "classical_music": "classical", "lossy_lossless_music": ""}
COVER_STEMS = ("folder", "cover", "front")
IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".webp")


def _year_of(name):
    m = re.match(r"\s*(\d{4})\b", name)
    return int(m.group(1)) if m else None


def sibling_style_reference(album_dir, year):
    """The front cover of the artist's album nearest in time (the band's look), from your library.
    Returns (bytes, label) or None. Generated covers are never used as a reference."""
    from PIL import Image
    album_dir = Path(album_dir)
    try:
        sibs = [p for p in album_dir.parent.iterdir() if p.is_dir() and p != album_dir]
    except OSError:
        return None
    def dist(p):
        y = _year_of(p.name)
        return (0, abs(y - year)) if (y and year) else (1, 0)
    for sib in sorted(sibs, key=lambda p: (dist(p), p.name)):
        files = []
        for folder in (sib, sib / "covers"):
            try:
                files += [f for f in folder.iterdir() if f.is_file() and f.suffix.lower() in IMAGE_EXTS
                          and f.stem.lower() in COVER_STEMS]
            except OSError:
                continue
        found = []
        for f in files:
            try:
                data = f.read_bytes()
                if R.is_generated(data):
                    continue
                with Image.open(io.BytesIO(data)) as im:
                    w, h = im.size
            except Exception:
                continue
            if min(w, h) >= 300 and 0.8 <= w / h <= 1.25:
                found.append((w * h, data, f"{sib.name}/{f.relative_to(sib)}"))
        if found:
            _a, data, label = max(found, key=lambda x: x[0])
            return data, label
    return None


def generation_plan(res, info, args, hamming_threshold):
    """None when the album has a usable front (or already a generated one). Otherwise what to build on:
    the tiny existing front (rebuilt at full size) or the band's look from their other albums."""
    from coverup.model import ArtType
    if info.get("embedded_generated") or getattr(res, "_had_generated", False):
        return None
    floor = int(args.low_res_floor or 0)
    tiny = []
    for c in res.candidates:
        if c.provider == "local" and c.art_type == ArtType.FRONT and c.dims_known and c.data:
            tiny.append((c.width * c.height, max(c.width, c.height), c.data, f"local {c.width}x{c.height}"))
    fe, fdata = info.get("front_embedded"), info.get("front_embedded_data")
    if fe and fdata:
        tiny.append((fe[0] * fe[1], max(fe), fdata, f"embedded {fe[0]}x{fe[1]}"))
    best = max(tiny, key=lambda x: x[0], default=None)
    if best and best[1] >= floor:
        return None                               # a usable cover exists; enlarging handles it
    if best:
        return {"ref": best[2], "kind": "rebuild", "describe": f"AI rebuild of the tiny {best[3]} cover"}
    style = sibling_style_reference(info["dir"], info.get("year"))
    if style:
        return {"ref": style[0], "kind": "style", "describe": f"AI cover in the style of {style[1]}"}
    return {"ref": None, "kind": "new", "describe": "AI cover from the album details"}


def _square_jpeg(data, size):
    from PIL import Image, ImageOps
    with Image.open(io.BytesIO(data)) as im:
        im = ImageOps.fit(im.convert("RGB"), (size, size), Image.LANCZOS)
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=95)
    return buf.getvalue()


LOGO_SOURCES = ("fanart", "wikimedia", "audiodb")      # the band's own logo, found by its MusicBrainz ID


def band_logo(res, s):
    """The band's real logo: your choice in CoverUp first, then fanart.tv HD, Wikidata, AudioDB,
    fanart.tv standard. Logos from any other source (web search) are never used."""
    logos = [c for c in (res.logos or []) if c.provider in LOGO_SOURCES]
    if not logos:
        return None
    chosen = s.logo_choice(res.query.artist_mbid)
    for c in logos:
        if chosen and c.url == chosen:
            return c
    def rank(c):
        hd = c.extra.get("fanart_kind") == "hdmusiclogo"
        order = 0 if (c.provider == "fanart" and hd) else {"wikimedia": 1, "audiodb": 2}.get(c.provider, 3)
        return (order, -(c.likes or 0), -(c.width * c.height))
    return min(logos, key=rank)


def make_generated(pipe, res, q, info, s, args, g):
    """Run your CoverUp creative method order (fan art, AI, template). Returns (Candidate, why) or
    (None, why not). Never raises."""
    from PIL import Image
    from coverup.creative import ai
    from coverup.creative.generate import comment_for, resolve_template
    from coverup.creative.template_engine import build_prompt
    from coverup.model import ArtType, Candidate, Tier
    template = resolve_template(s)
    if not q.genre:
        q.genre = LIBRARY_GENRES.get(Path(info["dir"]).parent.parent.name, "")
    seed = int(hashlib.md5(str(info["dir"]).encode()).hexdigest()[:8], 16)
    methods = [m for m in (s["creative_method_order"] or []) if m != "template" or args.allow_template]
    notes = []
    for method in methods:
        try:
            if method == "fanart":
                fan = [c for c in res.of_type(ArtType.FRONT) if c.provider == "fanart" and not c.rejected_reason]
                if fan:
                    best = max(fan, key=lambda c: (c.likes, c.score))
                    pipe.fetch_full(best)
                    return best, "fan art from fanart.tv"
                notes.append("no fan art")
                continue
            if method == "ai":
                backend = ai.make_backend(s["ai_backend"], pipe.http, s)
                if backend is None:
                    notes.append("no AI service set in CoverUp's options")
                    continue
                remote = ai.is_remote(backend.url)
                if remote and ai.host_of(backend.url) not in (s["ai_consented_hosts"] or []):
                    notes.append(f"AI service {ai.host_of(backend.url)} not approved in CoverUp")
                    continue
                size = int(s["ai_size"] or 1024)
                ref, strength = g["ref"], (args.gen_ref_strength if g["kind"] == "rebuild" else args.gen_style_strength)
                if ref and ((remote and not s["ai_allow_remote_reference"]) or not backend.supports_img2img()):
                    ref = None
                prompt = build_prompt(template, q, bool(s["creative_match_era"]), bool(s["creative_match_genre"]))
                if ref and g["kind"] == "style":
                    prompt += ", in the same visual style, colours and mood as the reference album cover by this artist"
                elif ref:
                    prompt += ", faithful detailed high-resolution recreation of the reference album cover"
                req = ai.GenRequest(prompt, template.negative, size, seed, 1,
                                    _square_jpeg(ref, size) if ref else None, strength)
                imgs = backend.generate(req)
                if not imgs:
                    notes.append("AI returned no image")
                    continue
                bg = Image.open(io.BytesIO(imgs[0]))
                bg.load()
                extra = {"method": "ai", "template": template.name, "seed": seed, "model": backend.model or backend.NAME,
                         "prompt": prompt, "backend": backend.NAME, "reference": g["kind"] if ref else "none"}
            elif method == "template":
                bg = R.procedural(template.procedural, 1024, seed, template.palette)
                extra = {"method": "template", "template": template.name, "seed": seed, "backend": "procedural"}
            else:
                continue
            # 1000-1200 px: hi-res, and small enough to embed untouched (keeps the generated mark)
            target = max(1000, min(min(bg.size), int(s["embed_max_side"] or 1200)))
            target = min(target, int(s["embed_max_side"] or 1200)) if s["embed_max_side"] else target
            bg = bg.convert("RGB").resize((target, target), Image.LANCZOS) if bg.size != (target, target) else bg
            logo_img, logo_src = None, ""
            show_logo = bool(s["creative_overlay_logo"]) and template.overlay_logo
            if show_logo and not info.get("collab"):
                lc = band_logo(res, s)
                if lc is not None:
                    try:
                        logo_img = Image.open(io.BytesIO(lc.data or pipe.fetch_full(lc)))
                        logo_img.load()
                        logo_src = lc.provider
                    except Exception:
                        logo_img = None
            name = Path(info["dir"]).parent.name if info.get("collab") else q.artist
            title_line = (q.album or "") + (f"  ·  {q.year}" if q.year else "")
            override = R.artist_font(Path(info["dir"]).parent) or args.title_font
            disp, textf, keep_case, family = R.choose_fonts(Path(info["dir"]).parent.parent.name, name,
                                                            [name, title_line], getattr(args, "fonts_dir", None), override)
            art = R.compose(bg, name, q.album, q.year, logo_img, show_logo=show_logo,
                            show_title=bool(s["creative_overlay_title"]) and template.overlay_title,
                            position=s["creative_logo_position"], font_path=disp, text_font=textf,
                            keep_case=keep_case)
            data = R.encode_generated(art, extra["method"], template.name)
            c = Candidate("generated", f"gen-{seed}", "", ArtType.FRONT, Tier.G, width=art.width, height=art.height,
                          bytes=len(data), data=data, thumb_data=data, dims_known=True, generated=True,
                          comment=comment_for(extra["method"], template.name), attribution="CoverUp")
            c.extra.update(extra)
            why = f"GENERATED: {g['describe'] if extra['method'] == 'ai' and extra.get('reference') != 'none' else extra['method']}" \
                  + (f"; band logo from {logo_src}" if logo_img is not None else "; artist name as text") \
                  + f"; font {Path(override).name if override else family}"
            return c, why
        except Exception as exc:              # generation must never break a batch
            notes.append(f"{method} failed: {type(exc).__name__}: {exc}")
    return None, "no cover and nothing generated (" + "; ".join(notes or ["no creative method enabled"]) + ")"


def process_album(album_dir, ctx, args):
    from coverup.apply.writer import FileItem, FolderPlan
    from coverup.model import ArtType
    from coverup.pipeline import Pipeline
    from coverup import imaging
    ma, mc, s, http, log = ctx["ma"], ctx["mc"], ctx["s"], ctx["http"], ctx["log"]
    out = {"path": str(album_dir), "rows": [], "status": "", "note": ""}
    import exclusions                                    # Ignore/Skip rules: no artwork at all
    dec = exclusions.gate(album_dir, "artwork", process_anyway=getattr(args, "process_anyway", False))
    if dec.all_excluded:
        whole = dec.summary()
        out.update(status="ignored" if whole.action == "ignore" else "skipped", note=whole.reason())
        return out
    info = album_info(album_dir, ma, mc, ctx.get("match_db"))
    if dec.excluded:
        info["files"] = [f for f in info["files"] if Path(f) not in dec.excluded]
        out["note"] = f"{len(dec.excluded)} file(s) left out by a rule"
    if not info["files"]:
        out.update(status="skipped", note="no audio files")
        return out
    has_art = bool(info["images"]) or info["any_embedded"]
    if args.pass_name == "missing" and has_art:
        out.update(status="skipped", note="already has art")
        return out

    pipe = Pipeline(s, http, fingerprint=imaging.fingerprint)
    q = make_query(info)
    res = pipe.run(q)
    gen_local = [c for c in res.candidates if c.provider == "local" and R.is_generated(c.data)]
    if gen_local:                       # art we generated earlier never blocks a real cover
        res.candidates = [c for c in res.candidates if c not in gen_local]
        pipe.select(res)
        res._had_generated = True
    actions, notes = decide(pipe, res, info, s, args, log)
    gen_note = ""
    if (getattr(args, "generate", False)
            and not any(a["type"] == "front" for a in actions)):
        g = generation_plan(res, info, args, int(s["hamming_threshold"]))
        if g is not None:
            if args.apply:
                cand, gen_note = make_generated(pipe, res, q, info, s, args, g)
                if cand is not None:
                    actions.insert(0, {"type": "front", "cand": cand, "why": gen_note, "local": False})
                else:
                    notes.append(gen_note)
            else:
                gen_note = "would generate: " + g["describe"]
    gen_row = None
    if gen_note and not args.apply:
        gen_row = {"album": str(album_dir), "type": "front", "source": "generated", "size": "",
                   "why": gen_note, "action": "would generate", "detail": "embed"}
        if not actions:
            out["rows"].append(gen_row)
            out.update(status="planned", note="; ".join(notes))
            return out
    if not actions:
        out.update(status="nothing", note="; ".join(notes) or "nothing found")
        return out

    want_embed = bool(s["embed"])
    for a in actions:
        c = a["cand"]
        why = a["why"]
        up = ctx.get("upscale_to") or 0
        if up and c.dims_known and 0 < max(c.width, c.height) < up:
            why += f"; low-res, upscaled to {up}"
        row = {"album": str(album_dir), "type": a["type"], "source": c.provider, "size": f"{c.width}x{c.height}",
               "why": why, "action": "", "detail": ""}
        out["rows"].append(row)
    if not args.apply:
        for row, a in zip(out["rows"], actions):
            row["action"] = "would apply"
            if a["type"] == "front" and want_embed:
                fe = info["front_embedded"]
                row["detail"] = (f"embed (now: {fe[0]}x{fe[1]})" if fe else "embed (none now)")
        if gen_row:
            out["rows"].insert(0, gen_row)
        out["status"] = "planned"
        return out

    # ---- apply ----------------------------------------------------------
    for a in actions:
        if a["cand"].data is None:
            try:
                pipe.fetch_full(a["cand"])          # CoverUp's download (falls back to smaller sizes)
            except Exception as exc:
                log.write(f"{album_dir}\t{a['type']} download failed: {exc}\n")
    items = []
    for a in actions:
        c = a["cand"]
        if c.data is None:
            continue
        art = {"front": ArtType.FRONT, "back": ArtType.BACK, "artist": ArtType.ARTIST_THUMB}[a["type"]]
        uncropped_local = a["local"] and not c.extra.get("cropped")
        if not (art == ArtType.FRONT and uncropped_local):     # the local file is already there
            items.append(FileItem(art, c.data, candidate=c))
    batch = ctx["batch"]
    file_results = []
    if items and s["save_files"]:
        ws = ctx.get("s_write") or s
        if getattr(res, "_had_generated", False) and not any(getattr(i.candidate, "generated", False) for i in items):
            ws = ws.with_(collision_policy="overwrite")   # a real cover replaces our generated one (backed up)
        fp = FolderPlan(items, ws, batch)
        file_results = fp.write_for_folder(info["dir"], 1, imaging.prepare)
        for r in file_results:
            if r.action in ("written", "replaced", "kept_both") and r.path is not None:
                ctx["touched_folders"].add(str(r.path.parent))
    front = next((a for a in actions if a["type"] == "front" and a["cand"].data), None)
    emb_note = ""
    if front and want_embed:
        c = front["cand"]
        fe = info["front_embedded"]
        up = int(ctx.get("upscale_to") or 0)
        cap = int(s["embed_max_side"] or 0)
        _d, nw, nh, _f = imaging.prepare(c.data, cap, "jpeg", int(s["jpeg_quality"]))   # its own size, capped
        data, w, h, _fmt = imaging.prepare(c.data, cap, "jpeg", int(s["jpeg_quality"]),
                                           min_side=up, upscale=bool(up))
        if fe and fe[0] * fe[1] >= nw * nh:
            emb_note = f"kept embedded {fe[0]}x{fe[1]} (as large)"
        else:
            desc = f"CoverUp: {c.provider}" if s["record_source_in_comment"] else ""
            backup = {}
            n, skipped = embed_front(info["files"], data, w, h, desc, backup)
            ctx["embed_backup"].update(backup)
            emb_note = f"embedded {w}x{h} in {n} file(s)" + (f"; {len(skipped)} skipped" if skipped else "")
    for row, a in zip(out["rows"], actions):
        row["action"] = "applied" if a["cand"].data is not None else "download failed"
        if a["type"] == "front":
            row["detail"] = emb_note
    out["files"] = [f"{r.action} {r.path or ''} {r.reason}".strip() for r in file_results]
    out["status"] = "applied"
    return out


def upscale_settings(s, upscale_to, floor):
    """Low-res option. CoverUp uses one number (min_<type>) both as the search minimum and as the
    size its "enlarge images smaller than the minimum" step grows to, so the batch uses two copies:
    a search copy that also accepts images down to ``floor``, and a write copy that enlarges anything
    smaller than ``upscale_to`` with CoverUp's own prepare(). Bigger images always win the search."""
    if not upscale_to:
        return s, s
    keys = ("min_front", "min_back", "min_disc", "min_booklet", "min_artist", "apply_min_side")
    search = s.with_(**{k: min(int(s[k] or 0), floor) for k in keys})
    write = s.with_(**{k: upscale_to for k in keys[:5]}, upscale_to_minimum=True)
    return search, write


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------
def album_dirs(args):
    out = []
    for audit in args.audit or []:
        with open(Path(audit).expanduser(), encoding="utf-8") as fh:
            for r in csv.DictReader(fh):
                if r.get("Status") in ("no_audio", "iso_only"):
                    continue
                if args.pass_name == "missing" and int(r.get("ImageCount") or 0) > 0:
                    continue                    # quick pre-filter; embedded art is checked per album
                out.append(Path(r["FullPath"]))
    for root in args.root or []:
        root = Path(root).expanduser()
        for artist in sorted(p for p in root.iterdir() if p.is_dir() and p.name != "2delete"):
            out.extend(sorted(p for p in artist.iterdir() if p.is_dir()))
    return out


def undo(batch_id, out_dir):
    from coverup.apply import files as F
    state = Path(out_dir) / f"applied_{batch_id}.json"
    if not state.exists():
        sys.exit(f"No record of batch {batch_id} in {out_dir}")
    rec = json.loads(state.read_text())
    restored_files = []
    for folder in rec.get("folders", []):
        restored_files += F.undo_batch(folder, batch_id)
    n = 0
    for path, pics in rec.get("embedded", {}).items():
        try:
            restore_embedded(path, pics)
            n += 1
        except Exception as exc:
            print(f"  could not restore {path}: {exc}")
    print(f"Undo {batch_id}: {len(restored_files)} image file change(s) reverted, embedded art restored in {n} file(s).")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--audit", action="append", help="audit CSV(s) from audit_untagged.py (all statuses used)")
    ap.add_argument("--root", action="append", help="or scan <root>/<Album Artist>/<Album>/")
    ap.add_argument("--pass", dest="pass_name", choices=("missing", "full"), default="missing")
    ap.add_argument("--apply", action="store_true", help="write images and embed (default: plan only)")
    ap.add_argument("--process-anyway", action="store_true",
                    help="also albums an Ignore/Skip rule excludes (this run only)")
    ap.add_argument("--undo", help="undo an applied batch by its id")
    ap.add_argument("--local-min", type=int, default=1000, help="local image short side for 'local first'")
    ap.add_argument("--upscale-to", type=int, default=500,
                    help="enlarge chosen images smaller than this (px); 0 = off (default 500)")
    ap.add_argument("--low-res-floor", type=int, default=250,
                    help="with --upscale-to, never use images smaller than this (px, default 250)")
    ap.add_argument("--no-generate", dest="generate", action="store_false",
                    help="never generate covers (default: generate for albums with no usable front)")
    ap.add_argument("--allow-template", action="store_true",
                    help="also use CoverUp's offline template covers when AI is unavailable")
    ap.add_argument("--title-font", help="one font file for every generated cover (a .ttf/.otf in the artist folder wins)")
    ap.add_argument("--fonts-dir", default=str(Path(__file__).resolve().parent / "coverup_fonts"),
                    help="folder of the bundled cover fonts")
    ap.add_argument("--gen-ref-strength", type=float, default=0.4,
                    help="AI change allowed when rebuilding a tiny cover (0-1, lower = closer)")
    ap.add_argument("--gen-style-strength", type=float, default=0.7,
                    help="AI change allowed from the band's other cover used as style (0-1)")
    ap.add_argument("--coverup-dir", default=DEFAULT_COVERUP)
    ap.add_argument("--picard-ini", default=DEFAULT_INI)
    ap.add_argument("--matches-db", default="~/Downloads/matching/match_state.db",
                    help="release IDs found by match_albums.py for untagged albums")
    ap.add_argument("--out-dir", default="~/Downloads/covers")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--sample", type=int)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--redo", action="store_true", help="ignore saved results of earlier runs")
    args = ap.parse_args()

    coverup_loader.load_coverup(args.coverup_dir)
    out_dir = Path(args.out_dir).expanduser()
    out_dir.mkdir(parents=True, exist_ok=True)
    if args.undo:
        undo(args.undo, out_dir)
        return

    import match_albums as ma
    import match_core as mc
    from coverup.net import HttpClient
    s, plugin, picard = batch_settings(args.picard_ini, args.pass_name)
    if not plugin:
        print(f"Note: no CoverUp settings found in {args.picard_ini}; using CoverUp defaults.")
    http = HttpClient(version="batch", cache_dir=out_dir / "http_cache", cache_hours=float(s["http_cache_hours"]))
    mdb_path = Path(args.matches_db).expanduser()
    match_db = sqlite3.connect(mdb_path) if mdb_path.exists() else None

    db = sqlite3.connect(out_dir / "covers_state.db")
    db.execute("CREATE TABLE IF NOT EXISTS results (pass TEXT, path TEXT, mode TEXT, result TEXT, "
               "PRIMARY KEY(pass, path, mode))")
    mode = "apply" if args.apply else "plan"
    if args.redo:
        db.execute("DELETE FROM results WHERE pass=? AND mode=?", (args.pass_name, mode))
        db.commit()

    dirs = album_dirs(args)
    if args.sample:
        random.Random(args.seed).shuffle(dirs)
        dirs = dirs[:args.sample]
    if args.limit:
        dirs = dirs[:args.limit]
    done = {p for (p, r) in db.execute("SELECT path, result FROM results WHERE pass=? AND mode=?", (args.pass_name, mode))
            if json.loads(r).get("status") != "nothing"}      # 'nothing found' is retried on every run
    todo = [d for d in dirs if str(d) not in done]
    batch = time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:4]
    print(f"Pass '{args.pass_name}' ({'APPLY' if args.apply else 'plan only'}): {len(dirs)} albums, "
          f"{len(dirs) - len(todo)} already done, {len(todo)} to go"
          + (f"; batch id {batch}" if args.apply else ""))
    log = open(out_dir / "covers_errors.log", "a", encoding="utf-8")
    import logging
    logging.basicConfig(filename=str(out_dir / "coverup.log"), level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    s_search, s_write = upscale_settings(s, args.upscale_to, args.low_res_floor)
    ctx = {"ma": ma, "mc": mc, "s": s_search, "s_write": s_write, "upscale_to": args.upscale_to, "http": http, "log": log, "match_db": match_db, "batch": batch,
           "touched_folders": set(), "embed_backup": {}}
    record = out_dir / f"applied_{batch}.json"

    def save_record():
        if args.apply:
            record.write_text(json.dumps({"batch": batch, "folders": sorted(ctx["touched_folders"]),
                                          "embedded": ctx["embed_backup"]}))
    counts = {}
    try:
        for i, d in enumerate(todo, 1):
            try:
                o = process_album(d, ctx, args)
            except KeyboardInterrupt:
                raise
            except Exception as exc:
                log.write(f"{d}\t{type(exc).__name__}: {exc}\n")
                log.flush()
                print(f"[{i}/{len(todo)}] ERROR {d.parent.name} | {d.name}: {type(exc).__name__}: {exc}")
                continue
            counts[o["status"]] = counts.get(o["status"], 0) + 1
            db.execute("INSERT OR REPLACE INTO results VALUES (?,?,?,?)",
                       (args.pass_name, str(d), mode, json.dumps(o, default=str)))
            db.commit()
            if o["status"] in ("planned", "applied", "nothing"):
                what = ", ".join(f"{r['type']} {r['source']} {r['size']}" for r in o["rows"]) or o["note"]
                print(f"[{i}/{len(todo)}] {o['status']:8s} {d.parent.name} | {d.name}: {what}")
            if args.apply and i % 10 == 0:
                save_record()
    except KeyboardInterrupt:
        print("\nInterrupted - progress is saved; run the same command to continue.")
    save_record()
    export(db, out_dir, args.pass_name, mode)
    print(f"\n{counts}\nPlan/results: {out_dir / f'covers_{args.pass_name}_{mode}.csv'}"
          + (f"\nUndo with: python3 coverup_batch.py --undo {batch}" if args.apply else
             "\nNothing was changed. Add --apply to write images and embed the front."))


def export(db, out_dir, pass_name, mode):
    path = out_dir / f"covers_{pass_name}_{mode}.csv"
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["Status", "Album", "Type", "Source", "Size", "Why", "Action", "Detail", "Note"])
        for (blob,) in db.execute("SELECT result FROM results WHERE pass=? AND mode=?", (pass_name, mode)):
            o = json.loads(blob)
            if not o["rows"]:
                w.writerow([o["status"], o["path"], "", "", "", "", "", "", o.get("note", "")])
            for r in o["rows"]:
                w.writerow([o["status"], o["path"], r["type"], r["source"], r["size"], r["why"],
                            r["action"], r["detail"], o.get("note", "")])


if __name__ == "__main__":
    main()
