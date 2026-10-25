"""artwork.py - the front cover for an identified album, fetched into staging; never written here.

Providers, tried in order until one gives a cover:

  coverup   CoverUp's own pipeline (herculepirate/picard-coverup), headless: your CoverUp
            settings from Picard.ini (or CoverUp's defaults), its sources, release matching,
            visual consensus and "local scans first" rule - with the identified release IDs.
  caa       Cover Art Archive, for a MusicBrainz release ID (front image).
  discogs   the Discogs release's primary image (needs the Discogs token).

The chosen image goes to a staging folder; the plan says which files get it embedded
(only files without an embedded picture) and whether cover.jpg is written (only when the
folder has no image). The executor does the writing, verified and undoable. A cover that
cannot be found or fetched never blocks tagging: the album is tagged without one and the
note says why.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import urllib.request
from pathlib import Path

import companions
import exclusions as ex

from version import VERSION as _VERSION  # noqa: E402
USER_AGENT = f"TagMender/{_VERSION} ( https://github.com/herculepirate/tagmender )"   # MusicBrainz asks for app/version ( contact )
MAX_BYTES = 20_000_000


EMBED_MAX_SIDE = 1200          # CoverUp's embed_max_side default: what goes into each track
JPEG_QUALITY = 95              # CoverUp's jpeg_quality default
EMBED_LIMIT = 16_000_000       # a FLAC picture block holds less than 16 MiB


def _rgb(im):
    """im as RGB, the right way up (EXIF orientation applied); transparency flattened onto
    white, as CoverUp does; 16-bit and 32-bit grey scaled down, never clipped to white."""
    from PIL import Image, ImageOps
    try:
        im = ImageOps.exif_transpose(im)
    except Exception:
        pass
    if im.mode in ("I;16", "I;16L", "I;16B", "I;16N", "I"):
        hi = 65535.0 if im.mode != "I" or (im.getextrema() or (0, 0))[1] > 255 else 255.0
        im = im.convert("I").point(lambda v: v * (255.0 / hi)).convert("L")
    elif im.mode == "F":
        lo, hi = im.getextrema()
        im = im.point(lambda v: (v - lo) * 255.0 / ((hi - lo) or 1)).convert("L")
    if im.mode in ("RGBA", "LA") or (im.mode == "P" and "transparency" in im.info):
        im = im.convert("RGBA")
        bg = Image.new("RGB", im.size, (255, 255, 255))
        bg.paste(im, mask=im.split()[-1])
        return bg
    return im.convert("RGB")


def _stage(data: bytes, staging: Path, quality=JPEG_QUALITY) -> tuple[Path, int, int]:
    """Write the image as JPEG (PNG, TIFF, WebP, BMP, CMYK converted) and return (path,
    width, height)."""
    from PIL import Image
    staging.mkdir(parents=True, exist_ok=True)
    with Image.open(io.BytesIO(data)) as im:
        im.load()                                   # (a truncated image fails here, not later)
        w, h = im.size
        if im.format != "JPEG" or im.mode not in ("RGB", "L"):
            buf = io.BytesIO()
            _rgb(im).save(buf, "JPEG", quality=int(quality))
            data = buf.getvalue()
            w, h = Image.open(io.BytesIO(data)).size       # (after turning it the right way up)
    path = staging / (hashlib.sha1(data).hexdigest()[:16] + ".jpg")
    path.write_bytes(data)
    return path, w, h


def embed_copy(front: Path, staging: Path, max_side=EMBED_MAX_SIDE, quality=JPEG_QUALITY) -> Path:
    """The front as it is embedded into each track: a JPEG at most max_side pixels on its
    long side (CoverUp's embed_max_side) and small enough for every format's picture
    block; front itself when it already is one."""
    from PIL import Image
    data = Path(front).read_bytes()
    with Image.open(io.BytesIO(data)) as im:
        im.load()
        side = int(max_side or 0)
        upright = (im.getexif() or {}).get(0x0112, 1) in (0, 1)
        if im.format == "JPEG" and im.mode in ("RGB", "L") and (not side or max(im.size) <= side) \
                and len(data) < EMBED_LIMIT and upright:
            return Path(front)
        out = _rgb(im)
        if side and max(out.size) > side:
            out.thumbnail((side, side), Image.LANCZOS)
        q = int(quality)
        while True:
            buf = io.BytesIO()
            out.save(buf, "JPEG", quality=q)
            if len(buf.getvalue()) < EMBED_LIMIT or q <= 50:
                break
            q -= 10
    staging.mkdir(parents=True, exist_ok=True)
    path = staging / (hashlib.sha1(buf.getvalue()).hexdigest()[:16] + ".embed.jpg")
    path.write_bytes(buf.getvalue())
    return path


def _get(url, headers=None, limit=MAX_BYTES) -> bytes | None:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, **(headers or {})})
    with urllib.request.urlopen(req, timeout=60) as r:
        data = r.read(limit + 1)
    if len(data) > limit:
        raise ValueError(f"image larger than {limit} bytes")
    return data


class CoverUp:
    name = "coverup"

    def __init__(self, staging: Path, ini=None, local_min=1000):
        import coverup_batch as cb
        import coverup_loader
        import picard_config
        self.cb = cb
        self.staging = staging
        coverup_loader.load_coverup(str(companions.coverup_dir()))
        from coverup.net import HttpClient
        if ini is None:                  # the Picard.ini TagMender reads ($TAGMENDER_PICARD_INI, Flatpak, native)
            found = picard_config.ini_path()
            ini = str(found) if found else cb.DEFAULT_INI
        self.s, self.plugin, _ = cb.batch_settings(ini, "full")
        self.http = HttpClient(version="tagmender", cache_dir=ex.STATE_DIR / "coverup_cache",
                               cache_hours=float(self.s["http_cache_hours"]))
        self.args = argparse.Namespace(local_min=local_min)

    def front(self, folder, ids: dict, artist="", album="", files=None) -> dict | None:
        import match_albums as ma
        import match_core as mc
        from coverup import imaging
        from coverup.pipeline import Pipeline
        cb = self.cb
        s = self.s
        if files is None:                 # (no file list: the folder's own music and its disc folders')
            files = _own_music(Path(folder))
        shared = _other_music(Path(folder), files)
        # the album is described by its own files only: the tags, IDs and embedded pictures of
        # other music in or below the folder are not this album's
        info = cb.album_info(Path(folder), ma, mc, files=files if shared else None)
        info["ids"].update({k: v for k, v in ids.items() if v})
        if artist:
            info["artist"] = artist
        if album:
            info["album"] = mc.search_title(album)
        if shared:
            # a loose track beside other albums, or one album of a folder holding several: the
            # pictures there (and below) are not this album's own - online sources only
            s = s.with_(providers_enabled=[p for p in s["providers_enabled"] if p != "local"])
            info["images"] = []
        pipe = Pipeline(s, self.http, fingerprint=imaging.fingerprint)
        res = pipe.run(cb.make_query(info))
        gen_local = [c for c in res.candidates if c.provider == "local" and cb.R.is_generated(c.data)]
        if gen_local:                     # a cover CoverUp generated earlier never blocks a real one
            res.candidates = [c for c in res.candidates if c not in gen_local]
            pipe.select(res)

        class _Log:
            def write(self, *_a):
                pass
        actions, notes = cb.decide(pipe, res, info, s, self.args, _Log())
        front = next((a for a in actions if a["type"] == "front"), None)
        if front is None:
            return {"note": "; ".join(notes) or "CoverUp found no front cover"}
        c = front["cand"]
        local = c.extra.get("local_path") or c.extra.get("path")
        q = s.get("jpeg_quality", JPEG_QUALITY) or JPEG_QUALITY
        if front["local"] and not c.extra.get("cropped") and local:
            # the scan in the folder is the cover: a JPEG copy of it, staged (the scan itself
            # may be a TIFF or a CMYK JPEG, and it may move with the album before cover.jpg
            # is made)
            path, w, h = _stage(Path(local).read_bytes(), self.staging, q)
            return {"front": str(path), "source": "coverup:local", "width": w, "height": h,
                    "why": front["why"], "local": str(local)}
        if c.data is None:
            pipe.fetch_full(c)
        if c.data is None:
            return {"note": f"CoverUp chose {c.provider} but the download failed"}
        limit = getattr(self, "limit", MAX_BYTES)
        if len(c.data) > limit:
            return {"note": f"CoverUp's {c.provider} image is {len(c.data)} bytes, over the profile's {limit}"}
        path, w, h = _stage(c.data, self.staging, q)
        return {"front": str(path), "source": f"coverup:{c.provider}", "width": w, "height": h, "why": front["why"]}

    def jpeg_quality(self):
        return int(self.s.get("jpeg_quality", JPEG_QUALITY) or JPEG_QUALITY)

    def embed_max_side(self):
        try:
            return int(self.s["embed_max_side"] or 0)
        except (KeyError, TypeError, ValueError):
            return EMBED_MAX_SIDE


def _own_music(folder: Path) -> list:
    from engine import is_disc_dir
    out = []
    try:
        for p in folder.iterdir():
            if p.is_file() and p.suffix.lower() in ex.AUDIO_EXT:
                out.append(p)
            elif p.is_dir() and is_disc_dir(p):
                out += [x for x in p.rglob("*") if x.suffix.lower() in ex.AUDIO_EXT]
    except OSError:
        pass
    return out


def _other_music(folder: Path, files) -> bool:
    """folder (or a folder below it) holds music that is not among files - not counting the
    same tracks in another format (FLAC and Opus side by side), hidden files and NAS index
    folders (Synology's @eaDir, .AppleDouble)."""
    mine = {str(f) for f in files}
    stems = {(str(Path(f).parent), Path(f).stem) for f in files}
    try:
        for p in folder.rglob("*"):
            rel = p.relative_to(folder).parts
            if any(x.startswith((".", "@")) for x in rel) or p.suffix.lower() not in ex.AUDIO_EXT:
                continue
            if str(p) in mine or (str(p.parent), p.stem) in stems or not p.is_file():
                continue
            return True
    except OSError:
        return False
    return False


class CoverArtArchive:
    name = "caa"

    def __init__(self, staging: Path):
        self.staging = staging

    def front(self, folder, ids: dict, artist="", album="") -> dict | None:
        mbid = ids.get("release_mbid")
        if not mbid:
            return None
        data = _get(f"https://coverartarchive.org/release/{mbid}/front-1200", limit=getattr(self, "limit", MAX_BYTES))
        path, w, h = _stage(data, self.staging)
        return {"front": str(path), "source": f"Cover Art Archive {mbid}", "width": w, "height": h}


class DiscogsImage:
    name = "discogs"

    def __init__(self, staging: Path, http=None, token=""):
        self.staging, self.http, self.token = staging, http, token

    def front(self, folder, ids: dict, artist="", album="") -> dict | None:
        rid = ids.get("discogs_release_id")
        if not rid or self.http is None or not self.token:
            return None
        det = self.http.get_json(f"https://api.discogs.com/releases/{rid}", "discogs") or {}
        imgs = det.get("images") or []
        img = next((i for i in imgs if i.get("type") == "primary"), imgs[0] if imgs else None)
        if not img or not img.get("uri"):
            return None
        data = _get(img["uri"], {"Authorization": f"Discogs token={self.token}"}, limit=getattr(self, "limit", MAX_BYTES))
        path, w, h = _stage(data, self.staging)
        return {"front": str(path), "source": f"Discogs release {rid}", "width": w, "height": h}


def chain(profile, staging: Path, http=None, token="", log=None) -> list:
    """The providers to try, the profile's choice first; one that cannot start is left out
    (and logged), so artwork never stops the run. The profile's artwork.max_bytes limits
    what is downloaded, artwork.min_px what is taken (find_front)."""
    art = profile["artwork"]
    first = art.get("provider", "coverup")
    order = [first] + [p for p in ("coverup", "caa", "discogs") if p != first]
    limit = int(art.get("max_bytes") or MAX_BYTES)
    out = []
    for name in order:
        try:
            if name == "coverup":
                out.append(CoverUp(staging))
            elif name == "caa":
                out.append(CoverArtArchive(staging))
            elif name == "discogs":
                out.append(DiscogsImage(staging, http, token))
            out[-1].limit = limit
            out[-1].min_px = int(art.get("min_px") or 0)
        except BaseException as exc:                          # CoverUp missing: SystemExit from its loader
            if log:
                log.warning("artwork provider %s unavailable: %s", name, exc)
    return out


def find_front(providers, folder, ids, artist="", album="", log=None, files=None) -> dict | None:
    """The first provider's front that is big enough (artwork.min_px on its short side),
    with "embed": the copy that goes into each track (at most embed_max_side, as CoverUp)."""
    notes = []
    for p in providers:
        try:
            if isinstance(p, CoverUp):
                got = p.front(folder, ids, artist, album, files=files)
            else:
                got = p.front(folder, ids, artist, album)
        except Exception as exc:
            notes.append(f"{p.name}: {exc}")
            continue
        if got and got.get("front"):
            low = getattr(p, "min_px", 0)
            if low and got.get("width") and got.get("height") and min(got["width"], got["height"]) < low:
                notes.append(f"{p.name}: {got['width']}x{got['height']} is smaller than the profile's "
                             f"{low} px")
                continue
            side = p.embed_max_side() if isinstance(p, CoverUp) else EMBED_MAX_SIDE
            q = p.jpeg_quality() if isinstance(p, CoverUp) else JPEG_QUALITY
            try:
                got["embed"] = str(embed_copy(Path(got["front"]), Path(got["front"]).parent, side, q))
            except Exception as exc:                         # not an image after all
                notes.append(f"{p.name}: {exc}")
                continue
            if notes:
                got["notes"] = notes
            return got
        if got and got.get("note"):
            notes.append(f"{p.name}: {got['note']}")
    if log and notes:
        log.info("no cover for %s: %s", folder, "; ".join(notes))
    return {"note": "; ".join(notes)} if notes else None
