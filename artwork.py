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
import os
import re
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
    from coverup_pil_imaging import to_rgb
    try:
        im = ImageOps.exif_transpose(im)
    except Exception:
        pass
    return to_rgb(im)


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
            icc = im.info.get("icc_profile")                # (the scan's colours, as it is shown)
            _rgb(im).save(buf, "JPEG", quality=int(quality), **({"icc_profile": icc} if icc else {}))
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
        icc = im.info.get("icc_profile")
        out = _rgb(im)
        if side and max(out.size) > side:
            out.thumbnail((side, side), Image.LANCZOS)
        q = int(quality)
        while True:
            buf = io.BytesIO()
            out.save(buf, "JPEG", quality=q, **({"icc_profile": icc} if icc else {}))
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


WEB_SOURCES = ("itunes", "deezer", "lastfm", "websearch", "fanart", "audiodb")
LOW_RES_FLOOR = 250            # never a cover smaller than this, enlarged or not (coverup_batch)


class CoverUp:
    """CoverUp's pipeline. only: CoverUp's sources to use ("web": shops and web search,
    "local": the album's own scans; None: all of yours)."""
    name = "coverup"

    def __init__(self, staging: Path, ini=None, local_min=1000, only=None):
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
        if only == "web":
            self.name = "web"
            self.s = self.s.with_(providers_enabled=[p for p in WEB_SOURCES])
        elif only == "local":
            self.name = "local"
            self.s = self.s.with_(providers_enabled=["local"])
        import coverup_batch
        self.args = argparse.Namespace(local_min=local_min, low_res_floor=LOW_RES_FLOOR, allow_template=False,
                                       title_font=None, fonts_dir=str(Path(coverup_batch.__file__).parent /
                                                                      "coverup_fonts"),
                                       gen_ref_strength=0.4, gen_style_strength=0.7, generate=False)
        self._last = None

    def front(self, folder, ids: dict, artist="", album="", files=None) -> dict | None:
        import match_albums as ma
        import match_core as mc
        from coverup import imaging
        from coverup.pipeline import Pipeline
        cb = self.cb
        s = self.s
        self._last = None                 # (a search that fails half way is never reused)
        if files is None:                 # (no file list: the folder's own music and its disc folders')
            files = _own_music(Path(folder))
        shared = _other_music(Path(folder), files)
        # the album is described by its own files only: the tags, IDs and embedded pictures of
        # other music in or below the folder are not this album's
        info = cb.album_info(Path(folder), ma, mc, files=files if shared else None)
        given = {k: v for k, v in ids.items() if v}
        if given:                         # the identification decides which release this is: the
            info["ids"] = given           # files' own (older, perhaps rejected) IDs never search
        if artist:
            info["artist"] = artist
        if album:
            info["album"] = mc.search_title(album)
        if shared:
            # a loose track beside other albums, or one album of a folder holding several: the
            # pictures there (and below) are not this album's own - online sources only
            s = s.with_(providers_enabled=[p for p in s["providers_enabled"] if p != "local"])
            info["images"] = []
        if getattr(self, "enlarge", False) and getattr(self, "min_px", 0):
            s = cb.upscale_settings(s, int(self.min_px), LOW_RES_FLOOR)[0]   # (small ones count: enlarged later)
        pipe = Pipeline(s, self.http, fingerprint=imaging.fingerprint)
        query = cb.make_query(info)
        res = pipe.run(query)
        self._last = (str(folder), pipe, res, query, info, s, (artist, ids.get("artist_mbid", "")))
        gen_local = [c for c in res.candidates if c.provider == "local" and cb.R.is_generated(c.data)]
        if gen_local:                     # a cover CoverUp generated earlier never blocks a real one
            res.candidates = [c for c in res.candidates if c not in gen_local]
            pipe.select(res)

        class _Log:
            def write(self, *_a):
                pass
        actions, notes = cb.decide(pipe, res, info, s, self.args, _Log())
        front = next((a for a in actions if a["type"] == "front"), None)
        if front is None and "local" in s["providers_enabled"]:
            # CoverUp keeps the album's own picture when nothing found is bigger ("existing
            # file ... is as large as anything found"): that picture is the front then
            from coverup.model import ArtType
            mine = [c for c in res.candidates if c.provider == "local" and c.art_type == ArtType.FRONT
                    and not cb.R.is_generated(c.data) and (c.extra.get("local_path") or c.extra.get("path"))]
            if mine:
                best = max(mine, key=lambda c: (c.width or 0) * (c.height or 0))
                front = {"type": "front", "cand": best, "local": True,
                         "why": f"the album's own {best.width}x{best.height} picture (nothing bigger found)"}
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
            path, w, h = self._file_size(path, w, h, q)
            return {"front": str(path), "source": "coverup:local", "width": w, "height": h,
                    "why": front["why"], "local": str(local)}
        if c.data is None:
            pipe.fetch_full(c)
        if c.data is None:
            return {"note": f"CoverUp chose {c.provider} but the download failed"}
        limit = getattr(self, "limit", MAX_BYTES)
        if c.provider != "local" and len(c.data) > limit:      # (a download; never the album's own scan)
            return {"note": f"CoverUp's {c.provider} image is {len(c.data)} bytes, over the profile's {limit}"}
        path, w, h = _stage(c.data, self.staging, q)
        path, w, h = self._file_size(path, w, h, q)
        return {"front": str(path), "source": f"coverup:{c.provider}", "width": w, "height": h, "why": front["why"]}

    def _reusable(self, folder, artist, ids):
        """The front search just made for this very album (same folder, same artist) with an
        artist photo in it - never one made for another album of the folder, or another artist's."""
        from coverup.model import ArtType
        last = getattr(self, "_last", None)
        if last and last[0] == str(folder) and len(last) > 6 and last[6] == (artist, ids.get("artist_mbid", "")) \
                and last[2] is not None and last[2].artist.get(ArtType.ARTIST_THUMB):
            return last
        return None

    def artist_photo(self, folder, ids: dict, artist="", files=None) -> dict | None:
        """The album artist's photo (CoverUp's artist sources: fanart.tv, TheAudioDB, Wikimedia
        Commons, Discogs), staged as a JPEG: {"image", "width", "height", "source"}, or a note.
        A front-cover search of the same album just now is reused (no second search)."""
        from coverup import imaging
        from coverup.model import ArtType
        from coverup.pipeline import Pipeline
        cb = self.cb
        last = self._reusable(folder, artist, ids)
        if last is not None:
            pipe, res = last[1], last[2]
        else:
            import match_albums as ma
            import match_core as mc
            if files is None:
                files = _own_music(Path(folder))
            info = cb.album_info(Path(folder), ma, mc, files=files)
            given = {k: v for k, v in ids.items() if v}
            if given:
                info["ids"] = given
            if artist:
                info["artist"] = artist
            info["images"] = []
            s = self.s.with_(artist_art=True, providers_enabled=[
                p for p in ("fanart", "audiodb", "wikimedia", "discogs") if p in self.s["providers_enabled"]
                or p in ("audiodb", "wikimedia")])              # (the two that need no key of yours)
            pipe = Pipeline(s, self.http, fingerprint=imaging.fingerprint)
            res = pipe.run(cb.make_query(info))
        c = res.artist.get(ArtType.ARTIST_THUMB)
        if c is None:
            return {"note": "no artist photo found"}
        if c.data is None:
            pipe.fetch_full(c)
        if c.data is None:
            return {"note": f"the artist photo from {c.provider} could not be downloaded"}
        if len(c.data) > getattr(self, "limit", MAX_BYTES):
            return {"note": f"the artist photo from {c.provider} is over the profile's size limit"}
        path, w, h = _stage(c.data, self.staging, self.jpeg_quality())
        return {"image": str(path), "width": w, "height": h, "source": f"coverup:{c.provider}"}

    def generate(self, folder, mode: str) -> dict | None:
        """A generated cover for the album front() just looked at (nothing usable found):
        CoverUp's creative methods - mode "template" (the offline renderer), "ai" (the AI
        service set in CoverUp's options, with its consent), "auto" (CoverUp's own order:
        fan art, AI, template). Marked as generated, so a real cover replaces it later."""
        if mode not in ("template", "ai", "auto") or not self._last or self._last[0] != str(folder):
            return None
        _f, pipe, res, query, info, s = self._last[:6]
        cb = self.cb
        order = {"template": ["template"], "ai": ["ai"]}.get(mode) or list(s["creative_method_order"] or [])
        s = s.with_(creative_method_order=order)
        args = argparse.Namespace(**vars(self.args))
        args.allow_template = mode in ("template", "auto")
        g = cb.generation_plan(res, info, args, int(s["hamming_threshold"]))
        if g is None:
            return {"note": "a usable cover exists; nothing generated"}
        cand, why = cb.make_generated(pipe, res, query, info, s, args, g)
        if cand is None or not cand.data:
            return {"note": why}
        path = self.staging / (hashlib.sha1(cand.data).hexdigest()[:16] + ".jpg")
        self.staging.mkdir(parents=True, exist_ok=True)
        path.write_bytes(cand.data)                 # (kept as made: it carries CoverUp's "generated" mark)
        return {"front": str(path), "source": "coverup:generated", "width": cand.width, "height": cand.height,
                "why": why, "generated": True}

    def _file_size(self, path, w, h, q):
        """The cover file at most CoverUp's file_max_side (0: as it is)."""
        side = int(self.s.get("file_max_side", 0) or 0)
        if side and max(w, h) > side:
            path = embed_copy(path, self.staging, side, q)
            from PIL import Image
            with Image.open(path) as im:
                w, h = im.size
        return path, w, h

    def jpeg_quality(self):
        return int(self.s.get("jpeg_quality", JPEG_QUALITY) or JPEG_QUALITY)

    def embed_max_side(self):
        try:
            return int(self.s["embed_max_side"] or 0)
        except (KeyError, TypeError, ValueError):
            return EMBED_MAX_SIDE


COVER_STEMS = ("cover", "folder", "front")
REPLACE_MODES = ("never", "larger_only")


def _size_of(data: bytes):
    from PIL import Image
    try:
        with Image.open(io.BytesIO(data)) as im:
            return im.size
    except Exception:
        return None


def _pictures(path) -> list:
    """The track's embedded pictures as (type, bytes); [] when none or unreadable."""
    import base64
    from mutagen.flac import FLAC, Picture
    from mutagen.id3 import ID3
    from mutagen.mp4 import MP4
    import tagio
    try:
        f = tagio.mutagen_file(path)
    except Exception:
        return []
    pics = []
    try:
        if isinstance(f, FLAC):
            pics = [(p.type, p.data) for p in f.pictures]
        elif isinstance(f, MP4):
            pics = [(3, bytes(c)) for c in (f.tags or {}).get("covr", [])]
        elif isinstance(getattr(f, "tags", None), ID3):
            pics = [(fr.type, fr.data) for fr in f.tags.getall("APIC")]
        elif f is not None and f.tags is not None:
            for v in f.tags.get("metadata_block_picture", []) or []:
                try:
                    p = Picture(base64.b64decode(v))
                    pics.append((p.type, p.data))
                except Exception:
                    continue
    except Exception:
        return []
    return pics


def _pick_front(pics):
    for want in (3, 0):                  # the front cover, else an untyped ("other") picture
        for t, d in pics:
            if t == want:
                return d
    return None                          # (a back cover or booklet page is never "the front")


def embedded_front(path):
    """The track's front cover as embedded (bytes), or None: the picture typed "front cover",
    else one typed "other" (many rippers leave the type so); never a back cover or booklet."""
    return _pick_front(_pictures(path))


def _album_dirs(folder) -> list:
    """The album's folder and its disc folders ("Disc 01" ...): a cover in either is the album's."""
    from engine import is_disc_dir
    folder = Path(folder)
    out = [folder]
    try:
        out += sorted(d for d in folder.iterdir() if d.is_dir() and not d.is_symlink() and is_disc_dir(d))
    except OSError:
        pass
    return out


def folder_cover(folder) -> Path | None:
    """The album folder's own cover file (cover.jpg, folder.png, Front.jpeg ...), the largest
    if there are several; in its disc folder when the album folder has none ("Album/Disc 01/
    cover.jpg"); None when there is none."""
    for d in _album_dirs(folder):
        best = None
        try:
            for x in d.iterdir():
                if x.is_file() and not x.is_symlink() and x.stem.lower() in COVER_STEMS and \
                        x.suffix.lower() in (".jpg", ".jpeg", ".png"):      # (a link points at someone else's file)
                    sz = _size_of(x.read_bytes())
                    if sz and (best is None or min(sz) > best[1]):
                        best = (x, min(sz))
        except OSError:
            continue
        if best:
            return best[0]
    return None


def small_covers(folder, files, profile, ours=None) -> dict:
    """The album's covers: {"owns": its existing front covers, largest first, as (path or
    bytes, w, h) - never another album's (a shared folder's cover file is no one's); and,
    with artwork.replace "larger_only", those smaller than artwork.min_px on the short side,
    which a larger cover replaces: "file": (path, w, h) or None, "embedded": {track: (w, h)}
    ((0, 0): pictures, but no front among them)}. files: the tracks TagMender writes; ours:
    every file of the album as found (its second copies too), for telling its folder's cover
    from another album's."""
    from plan import EMBEDDABLE
    art = profile["artwork"]
    low = int(art.get("min_px") or 0)
    replacing = art.get("replace", "larger_only") == "larger_only" and low > 0
    out = {"file": None, "embedded": {}, "owns": []}
    cap = embed_cap(profile)
    # a folder holding other music too: its cover file is no one album's (never replaced, never
    # "own", and none is written there - the plan asks the same question)
    out["shared"] = _other_music(Path(folder), ours or files)
    if art.get("files", True) and not out["shared"]:
        c = folder_cover(folder)
        if c is not None:
            try:
                sz = _size_of(c.read_bytes())
            except OSError:
                sz = None
            if sz:
                out["owns"].append((c, *sz))
                if replacing and min(sz) < low:
                    out["file"] = (c, *sz)
    if art.get("embed_front", True):
        for f in files:
            if Path(f).suffix.lower() not in EMBEDDABLE:
                continue                          # (a cover there is never written: never looked for)
            pics = _pictures(f)
            data = _pick_front(pics)
            if data is None:
                if pics and replacing:
                    out["embedded"][str(f)] = (0, 0)          # a back cover or booklet alone: a front is added
                continue
            sz = _size_of(data)
            # small, unless it is as large as TagMender embeds (embed_max_side caps the long side:
            # with min_px above that, or a wide cover, it would be replaced again on every run)
            if replacing and sz and min(sz) < low and max(sz) < cap - 2:
                out["embedded"][str(f)] = sz
            if sz:
                out["owns"].append((data, *sz))
    out["owns"].sort(key=lambda o: -min(o[1:]))
    return out


def has_small(small: dict) -> bool:
    return bool(small and (small.get("file") or small.get("embedded")))


_CAP = []


def embed_cap(profile=None) -> int:
    """The smallest long side TagMender embeds covers at for this profile: EMBED_MAX_SIDE, or
    CoverUp's embed_max_side when CoverUp is among its sources and that is smaller."""
    srcs = sources_of(profile["artwork"]) if profile is not None else ["coverup"]
    if not any(x in srcs for x in ("coverup", "web", "local")):
        return EMBED_MAX_SIDE                 # (Cover Art Archive and Discogs covers embed at TagMender's size)
    if _CAP:
        return _CAP[0]
    cap = EMBED_MAX_SIDE
    try:
        import coverup_batch as cb
        import coverup_loader
        import picard_config
        coverup_loader.load_coverup(str(companions.coverup_dir()))
        found = picard_config.ini_path()
        st = cb.batch_settings(str(found) if found else cb.DEFAULT_INI, "full")[0]
        v = int(st["embed_max_side"] or 0)
        if v > 0:
            cap = min(cap, v)
        _CAP.append(cap)                      # (only what CoverUp said is kept; a failure is tried again)
    except BaseException:                     # CoverUp not there: TagMender's own size
        pass
    return cap


def own_cover(small: dict, staging: Path) -> dict | None:
    """The album's best existing cover that can be read, as a found front (for enlarging
    when nothing larger is found anywhere)."""
    staging.mkdir(parents=True, exist_ok=True)
    for what, _w, _h in (small or {}).get("owns") or []:
        try:
            data = what.read_bytes() if isinstance(what, Path) else what
            path, w, h = _stage(data, staging)
        except Exception:
            continue                          # (a truncated or unreadable picture: the next one)
        return {"front": str(path), "width": w, "height": h, "source": "the album's own cover"}
    return None


def as_format(front: Path, suffix: str, staging: Path) -> Path:
    """front as the file type a name ending in suffix promises (a cover.png stays a PNG)."""
    from PIL import Image
    want = "PNG" if suffix.lower() == ".png" else "JPEG"
    with Image.open(front) as im:
        if im.format == want:
            return front
        icc = im.info.get("icc_profile")
        im = _rgb(im) if want == "JPEG" else im.convert("RGBA" if "A" in im.getbands() else "RGB")
        buf = io.BytesIO()
        im.save(buf, want, **({"quality": JPEG_QUALITY} if want == "JPEG" else {}),
                **({"icc_profile": icc} if icc else {}))
    staging.mkdir(parents=True, exist_ok=True)
    path = staging / (hashlib.sha1(buf.getvalue()).hexdigest()[:16] + (".png" if want == "PNG" else ".jpg"))
    path.write_bytes(buf.getvalue())
    return path


ARTIST_FILE = "artist.jpg"      # what Plex, Jellyfin, Navidrome and LMS read in an artist folder
ARTIST_EMBED_MAX_SIDE = 800     # the artist photo in every track: small (it is in every track)


def artist_settings(profile) -> dict | None:
    """{"file": name or "", "embed": bool} when the profile gets artist photos: artwork on,
    tag level Enthusiast or Audiophile, artwork.artist.enabled (on unless set off)."""
    import levels
    art = profile["artwork"]
    a = art.get("artist") or {}
    if not art.get("enabled") or levels.level(profile) == "basic" or a.get("enabled", True) is False:
        return None
    return {"file": a.get("file", ARTIST_FILE) or "", "embed": bool(a.get("embed", True))}


def has_artist_picture(path) -> bool:
    return any(t in (7, 8) for t, _d in _pictures(path))       # lead artist, artist/performer


def has_front(path) -> bool:
    """The track has a front cover (typed so, or untyped): a back cover or an artist photo
    alone is not one."""
    return _pick_front(_pictures(path)) is not None


ARTIST_STEMS = ("artist", "folder")   # in an artist folder (one holding album folders) both are its photo


def artist_folder_photo(folder) -> Path | None:
    """The artist folder's own photo (artist.jpg, or folder.jpg - Kodi, Jellyfin), if any."""
    try:
        for stem in ARTIST_STEMS:
            for x in sorted(Path(folder).iterdir()):
                if x.is_file() and not x.is_symlink() and x.stem.lower() == stem and \
                        x.suffix.lower() in (".jpg", ".jpeg", ".png"):
                    return x
    except OSError:
        return None
    return None


def artist_folder_has_photo(folder) -> bool:
    return artist_folder_photo(folder) is not None


def artist_dir_for(album_folder, artist: str, roots, compat="windows") -> Path | None:
    """Where the album artist's photo goes: the album folder's parent, when it lies strictly
    inside one of roots (never a root itself) and is named after the album artist ("The
    Beatles" or "Beatles, The"); else None."""
    from engine import is_disc_dir
    import filenames as fnm
    album = Path(album_folder)
    if is_disc_dir(album):
        album = album.parent
    d = album.parent
    rs = [Path(os.path.realpath(os.path.expanduser(str(r)))) for r in roots]
    real = Path(os.path.realpath(str(d)))
    if not any(r in real.parents for r in rs) or real in rs:
        return None
    names = {artist}
    if artist.lower().startswith("the "):
        names.add(f"{artist[4:]}, {artist[:3]}")
    if fnm.fold(d.name) not in {fnm.fold(fnm.safe_component(n, compat)) for n in names}:
        return None
    return d


def is_collaboration(artist_mbids: str, folder_name: str) -> bool:
    """Several album artists ("A & B"): no one artist's photo stands for them."""
    if len([x for x in str(artist_mbids or "").split(";") if x.strip()]) > 1:
        return True
    try:
        import coverup_batch as cb
        import match_core as mc
        return bool(cb.is_collaboration(artist_mbids, folder_name, mc))
    except Exception:
        return False


def _photo_cache_dir() -> Path:
    return ex.STATE_DIR / "artist_photos"


def cached_artist_photo(key: str):
    """(True, {"image": ...}) or (True, None) for "none found, asked lately"; (False, None):
    never asked. Kept across runs, so an artist is looked up once, not once per album and run;
    "none found" is asked again after 30 days."""
    import time as _time
    h = hashlib.sha1(key.encode("utf-8", "surrogateescape")).hexdigest()[:20]
    d = _photo_cache_dir()
    img, none = d / f"{h}.jpg", d / f"{h}.none"
    if img.exists():
        try:
            with __import__("PIL.Image", fromlist=["Image"]).open(img) as im:
                w, h2 = im.size
        except Exception:
            return False, None
        src = (d / f"{h}.src").read_text().strip() if (d / f"{h}.src").exists() else "artist photo"
        return True, {"image": str(img), "width": w, "height": h2, "source": src}
    if none.exists() and _time.time() - none.stat().st_mtime < 30 * 86400:
        return True, None
    return False, None


def remember_artist_photo(key: str, got):
    h = hashlib.sha1(key.encode("utf-8", "surrogateescape")).hexdigest()[:20]
    d = _photo_cache_dir()
    d.mkdir(parents=True, exist_ok=True)
    try:
        if got and got.get("image"):
            tmp = d / f".{h}.tmp"
            tmp.write_bytes(Path(got["image"]).read_bytes())
            os.replace(tmp, d / f"{h}.jpg")
            (d / f"{h}.src").write_text(got.get("source", ""))
            return dict(got, image=str(d / f"{h}.jpg"))
        (d / f"{h}.none").touch()
    except OSError:
        pass
    return got


def needs_art(folder, files, profile, small=None, stays=True) -> str:
    """Why the album needs a cover ("" when it does not): a track without an embedded front
    (when covers are embedded), or no picture in its folder (when cover files are written),
    or a cover smaller than artwork.min_px (artwork.replace "larger_only")."""
    import tagio
    art = profile["artwork"]
    if art.get("when", "needed") == "always":
        return "always looked for"
    small = small_covers(folder, files, profile) if small is None else small
    if small.get("file"):
        c, w, h = small["file"]
        return f"{c.name} is {w}x{h}, smaller than {art['min_px']} px"
    if small.get("embedded"):
        t, (w, h) = next(iter(small["embedded"].items()))
        return (f"{Path(t).name} has pictures but no front cover" if not w else
                f"the cover in {Path(t).name} is {w}x{h}, smaller than {art['min_px']} px")
    if art.get("embed_front", True):
        from plan import EMBEDDABLE
        for f in files:
            if Path(f).suffix.lower() not in EMBEDDABLE:
                continue                          # (WavPack, APE, DSF ...: a cover is never embedded there)
            if not has_front(f):                  # (an artist photo or a back cover alone is no cover)
                return f"{Path(f).name} has no cover"
    # a shared folder never gets a cover file - while the album stays there; moved or copied
    # into a folder of its own, it gets one
    if art.get("files", True) and not (small.get("shared") and stays):
        try:
            if not any(x.suffix.lower() in (".jpg", ".jpeg", ".png", ".webp", ".gif", ".tif", ".tiff", ".bmp")
                       for d in _album_dirs(folder) for x in d.iterdir() if x.is_file()):
                return "no picture in the folder"
        except OSError:
            return ""
    return ""


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


def _album_id(path):
    """(title without the disc, album artist or artist), lower case; ("", "") untagged."""
    from engine import _album_title, _first
    m = ex.read_meta(path)
    title = _album_title(m)
    who = (_first(m, "albumartist") or _first(m, "artist")).casefold()
    return (title, who) if title else ("", "")


def _is_disc(d: Path) -> bool:
    from engine import is_disc_dir
    return is_disc_dir(d)


def _bare_stem(p: Path) -> str:
    """"02 b (1)", "02 - b", "02. b" and "02 b" alike: a duplicate's (or another format's) name."""
    st = re.sub(r"\s*\(\d+\)$", "", p.stem)
    st = re.sub(r"^(\d+)\s*[-._]+\s*", r"\1 ", st)
    return re.sub(r"\s+", " ", st).strip().casefold()


def _nobodys(part: str) -> bool:
    """Folders whose files are no album's: hidden ones, NAS index folders (Synology's @eaDir),
    and the ones TagMender never looks into (_old, _unresolved, 2delete)."""
    return part.startswith((".", "@", "_")) or part == "2delete"


_BONUS = re.compile(r"^(bonus|extras?|b[- ]?sides?|outtakes|demos?|rarities|unreleased|instrumentals?)\b",
                    re.IGNORECASE)


def _other_music(folder: Path, files) -> bool:
    """folder (or any folder below it) holds music that is not this album's - so a picture
    there is no one album's cover. files: every file of the album (as grouped, and where the
    plan puts them). The album's own: copies of its tracks ("02 b (1).flac", "01 - a.mp3"
    beside "01 a.flac"), tracks tagged as the same album, its untagged disc folders, and - for
    an album of several tracks - a bonus folder ("Bonus", "Extras", "B-Sides" ...) smaller
    than the album, untagged or by the same artist. A loose single beside the artist's album
    folders is not an album with bonus folders: that folder's picture is the artist's.
    Answers as soon as it knows (a library root is not read to the end); a folder that
    cannot be read counts as holding other music."""
    folder = Path(folder)
    if not folder.is_dir():
        return False                        # (a folder the plan is yet to make holds nothing)
    mine = {str(f) for f in files}
    ids = {_album_id(f) for f in files} if files else set()
    untagged = bool(files) and ids <= {("", "")}      # the album itself carries no tags
    ids.discard(("", ""))                   # (untagged is no album: never "the same" as this one)
    whos = {w for _t, w in ids}
    stems = {_bare_stem(Path(f)) for f in files}
    top = sum(1 for f in files if Path(f).parent == folder)
    counts = {}
    trouble = []
    for dirpath, dirs, names in os.walk(folder, onerror=trouble.append):
        d = Path(dirpath)
        rel_dir = d.relative_to(folder).parts
        dirs[:] = sorted(x for x in dirs if not _nobodys(x))
        for n in sorted(names):
            p = d / n
            if p.suffix.lower() not in ex.AUDIO_EXT or n.startswith(".") or str(p) in mine:
                continue
            aid = _album_id(p)
            if aid in ids:
                continue                              # the same album (a copy, a bonus track)
            if _bare_stem(p) in stems and aid in (("", ""), *ids):
                continue                              # a copy of one of its own tracks
            if not rel_dir:
                return True                           # another album's track beside this one
            sub = rel_dir[0]
            if aid == ("", "") and _is_disc(folder / sub):
                continue                              # its untagged disc folder
            bonus_like = _BONUS.match(sub) is not None
            ok = top >= 2 and bonus_like and ((untagged and aid == ("", "")) or (aid[1] and aid[1] in whos))
            if not ok:
                return True
            counts[sub] = counts.get(sub, 0) + 1
            if counts[sub] >= top:
                return True                           # (a "bonus" folder as big as the album is not one)
    return bool(trouble)


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


def sources_of(art: dict) -> list:
    """The profile's cover sources, in order (an older profile's "provider" goes first)."""
    srcs = list(art.get("sources") or ["coverup", "caa", "discogs"])
    first = art.get("provider")
    if first and first in srcs and srcs == ["coverup", "caa", "discogs"] and first != "coverup":
        srcs = [first] + [x for x in srcs if x != first]
    return srcs


def chain(profile, staging: Path, http=None, token="", log=None) -> list:
    """The providers to try, in the profile's order (artwork.sources); one that cannot start
    is left out (and logged), so artwork never stops the run. The profile's artwork.max_bytes
    limits what is downloaded, artwork.min_px what is taken (find_front)."""
    art = profile["artwork"]
    limit = int(art.get("max_bytes") or MAX_BYTES)
    out = []
    for name in sources_of(art):
        try:
            if name in ("coverup", "web", "local"):
                out.append(CoverUp(staging, only=None if name == "coverup" else name))
            elif name == "caa":
                out.append(CoverArtArchive(staging))
            elif name == "discogs":
                out.append(DiscogsImage(staging, http, token))
            else:
                continue
            out[-1].limit = limit
            out[-1].min_px = int(art.get("min_px") or 0)
            out[-1].enlarge = bool(art.get("enlarge", True))
            out[-1].generate_mode = art.get("generate", "off")
        except BaseException as exc:                          # CoverUp missing: SystemExit from its loader
            if log:
                log.warning("artwork provider %s unavailable: %s", name, exc)
    return out


def _enlarged(got: dict, min_px: int, quality=JPEG_QUALITY) -> dict | None:
    """got's front enlarged to min_px on its short side (CoverUp's "enlarge to minimum"),
    when it is at least LOW_RES_FLOOR; None when it is smaller than that."""
    from PIL import Image
    w, h = int(got.get("width") or 0), int(got.get("height") or 0)
    if min(w, h) < LOW_RES_FLOOR:
        return None
    with Image.open(got["front"]) as im:
        icc = im.info.get("icc_profile")
        im = _rgb(im)
        k = min_px / float(min(im.size))
        im = im.resize((round(im.width * k), round(im.height * k)), Image.LANCZOS)
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=int(quality), **({"icc_profile": icc} if icc else {}))
    path = Path(got["front"]).parent / (hashlib.sha1(buf.getvalue()).hexdigest()[:16] + ".jpg")
    path.write_bytes(buf.getvalue())
    return dict(got, front=str(path), width=im.width, height=im.height, enlarged_from=f"{w}x{h}",
                source=got.get("source", "") + " (enlarged)")


def find_front(providers, folder, ids, artist="", album="", log=None, files=None, own=None,
               keep=False) -> dict | None:
    """The first provider's front that is big enough (artwork.min_px on its short side; a
    smaller one of at least LOW_RES_FLOOR is enlarged when artwork.enlarge is on), with
    "embed": the copy that goes into each track (at most embed_max_side, as CoverUp). When
    no provider has one, a cover is generated if artwork.generate says so. own: the album's
    own (too small) cover, enlarged when nothing larger is found - never generated over."""
    notes = []
    small = None                                     # (the best too-small front, to enlarge)
    if own and providers and own.get("width"):
        want = int(getattr(providers[0], "min_px", 0) or 0)
        big_own = min(own["width"], own["height"]) >= want
        # the album has a good cover: that one, with no lookup - unless CoverUp looks at the
        # album's own scans itself (it crops and straightens them; it takes them first)
        if big_own and not any(isinstance(p, CoverUp) and p.name in ("coverup", "local") for p in providers):
            try:
                return _finish(providers[0], own, notes)
            except Exception as exc:
                notes.append(f"the album's own cover: {exc}")
        if big_own or getattr(providers[0], "enlarge", False):
            small = (providers[0], own)           # (used when nothing larger is found)
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
                if getattr(p, "enlarge", False) and (small is None or
                                                     min(got["width"], got["height"]) > min(small[1]["width"],
                                                                                            small[1]["height"])):
                    small = (p, got)
                continue
            try:
                return _finish(p, got, notes)
            except Exception as exc:                     # not an image after all
                notes.append(f"{p.name}: {exc}")
                continue
        if got and got.get("note"):
            notes.append(f"{p.name}: {got['note']}")
    if small is not None:                            # nothing big enough: the best small one, enlarged
        p, got = small
        if own and got is not own and min(own["width"], own["height"]) >= min(got["width"], got["height"]):
            got = own                                # (what was found is no larger than the album's own)
        want = int(getattr(p, "min_px", 0) or 0)
        if min(got["width"], got["height"]) >= want:  # the album's own, already large enough (an
            try:                                      # embedded front for a small cover.jpg): as it is
                return _finish(p, got, notes)
            except Exception as exc:
                notes.append(f"{p.name}: {exc}")
        big = _enlarged(got, want, p.jpeg_quality() if isinstance(p, CoverUp) else JPEG_QUALITY)
        if big is not None:
            try:
                return _finish(p, big, notes)
            except Exception as exc:
                notes.append(f"{p.name}: {exc}")
    for p in providers:                              # nothing at all: made, if the profile says so
        if own or keep:
            break                                    # (the album has a cover: never one made over it)
        mode = getattr(p, "generate_mode", "off")
        if isinstance(p, CoverUp) and mode != "off":
            try:
                got = p.generate(folder, mode)
            except Exception as exc:
                notes.append(f"generating: {exc}")
                break
            if got and got.get("front"):
                try:
                    return _finish(p, got, notes)
                except Exception as exc:
                    notes.append(f"generating: {exc}")
            if got and got.get("note"):
                notes.append(f"generating: {got['note']}")
            break
    if log and notes:
        log.info("no cover for %s: %s", folder, "; ".join(notes))
    return {"note": "; ".join(notes)} if notes else None


def _finish(p, got, notes):
    side = p.embed_max_side() if isinstance(p, CoverUp) else EMBED_MAX_SIDE
    q = p.jpeg_quality() if isinstance(p, CoverUp) else JPEG_QUALITY
    if got.get("generated"):
        got["embed"] = got["front"]                   # (as made: small enough, and keeps its mark)
    else:
        got["embed"] = str(embed_copy(Path(got["front"]), Path(got["front"]).parent, side, q))
    if notes:
        got["notes"] = notes
    return got
