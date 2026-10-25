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
        self._last = (str(folder), pipe, res, query, info, s)
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

    def generate(self, folder, mode: str) -> dict | None:
        """A generated cover for the album front() just looked at (nothing usable found):
        CoverUp's creative methods - mode "template" (the offline renderer), "ai" (the AI
        service set in CoverUp's options, with its consent), "auto" (CoverUp's own order:
        fan art, AI, template). Marked as generated, so a real cover replaces it later."""
        if mode not in ("template", "ai", "auto") or not self._last or self._last[0] != str(folder):
            return None
        _f, pipe, res, query, info, s = self._last
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


def needs_art(folder, files, profile) -> str:
    """Why the album needs a cover ("" when it does not): a track without an embedded front
    (when covers are embedded), or no picture in its folder (when cover files are written)."""
    import tagio
    art = profile["artwork"]
    if art.get("when", "needed") == "always":
        return "always looked for"
    if art.get("embed_front", True):
        for f in files:
            try:
                if not tagio.read(f).get("~pictures"):
                    return f"{Path(f).name} has no cover"
            except Exception:
                return f"{Path(f).name} could not be read"
    if art.get("files", True):
        try:
            if not any(x.suffix.lower() in (".jpg", ".jpeg", ".png", ".webp", ".gif", ".tif", ".tiff", ".bmp")
                       for x in Path(folder).iterdir() if x.is_file()):
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


def _other_music(folder: Path, files) -> bool:
    """folder (or a folder below it) holds another album's music: a track that is not among
    files and is tagged as another album. Copies of the album's own tracks (FLAC and Opus
    side by side, "02 b (1).flac") and its untagged bonus folder are the album's; hidden
    files and NAS index folders (Synology's @eaDir, .AppleDouble) are nobody's."""
    mine = {str(f) for f in files}
    ours = {_album_id(f) for f in files} if files else set()
    try:
        for p in folder.rglob("*"):
            rel = p.relative_to(folder).parts
            if any(x.startswith((".", "@")) for x in rel) or p.suffix.lower() not in ex.AUDIO_EXT:
                continue
            if str(p) in mine or not p.is_file():
                continue
            if _album_id(p) in ours:
                continue                                  # the same album (a copy, a bonus track)
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


def find_front(providers, folder, ids, artist="", album="", log=None, files=None) -> dict | None:
    """The first provider's front that is big enough (artwork.min_px on its short side; a
    smaller one of at least LOW_RES_FLOOR is enlarged when artwork.enlarge is on), with
    "embed": the copy that goes into each track (at most embed_max_side, as CoverUp). When
    no provider has one, a cover is generated if artwork.generate says so."""
    notes = []
    small = None                                     # (the best too-small front, to enlarge)
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
        big = _enlarged(got, int(getattr(p, "min_px", 0) or 0),
                        p.jpeg_quality() if isinstance(p, CoverUp) else JPEG_QUALITY)
        if big is not None:
            try:
                return _finish(p, big, notes)
            except Exception as exc:
                notes.append(f"{p.name}: {exc}")
    for p in providers:                              # nothing at all: made, if the profile says so
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
