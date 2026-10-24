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

USER_AGENT = "TagMender/0.2 +https://github.com/herculepirate/tagmender"
MAX_BYTES = 20_000_000


def _stage(data: bytes, staging: Path) -> tuple[Path, int, int]:
    """Write the image as JPEG (CMYK/PNG converted) and return (path, width, height)."""
    from PIL import Image
    staging.mkdir(parents=True, exist_ok=True)
    with Image.open(io.BytesIO(data)) as im:
        w, h = im.size
        if im.format != "JPEG" or im.mode not in ("RGB", "L"):
            buf = io.BytesIO()
            im.convert("RGB").save(buf, "JPEG", quality=93)
            data = buf.getvalue()
    path = staging / (hashlib.sha1(data).hexdigest()[:16] + ".jpg")
    path.write_bytes(data)
    return path, w, h


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
        self.cb = cb
        self.staging = staging
        coverup_loader.load_coverup(str(companions.coverup_dir()))
        from coverup.net import HttpClient
        self.s, self.plugin, _ = cb.batch_settings(ini or cb.DEFAULT_INI, "full")
        self.http = HttpClient(version="tagmender", cache_dir=ex.STATE_DIR / "coverup_cache",
                               cache_hours=float(self.s["http_cache_hours"]))
        self.args = argparse.Namespace(local_min=local_min)

    def front(self, folder, ids: dict, artist="", album="") -> dict | None:
        import match_albums as ma
        import match_core as mc
        from coverup import imaging
        from coverup.pipeline import Pipeline
        cb = self.cb
        info = cb.album_info(Path(folder), ma, mc)
        info["ids"].update({k: v for k, v in ids.items() if v})
        if artist:
            info["artist"] = artist
        if album:
            info["album"] = mc.search_title(album)
        pipe = Pipeline(self.s, self.http, fingerprint=imaging.fingerprint)
        res = pipe.run(cb.make_query(info))
        res.candidates = [c for c in res.candidates if not (c.provider == "local" and cb.R.is_generated(c.data))]

        class _Log:
            def write(self, *_a):
                pass
        actions, notes = cb.decide(pipe, res, info, self.s, self.args, _Log())
        front = next((a for a in actions if a["type"] == "front"), None)
        if front is None:
            return {"note": "; ".join(notes) or "CoverUp found no front cover"}
        c = front["cand"]
        local = c.extra.get("local_path") or c.extra.get("path")
        if front["local"] and not c.extra.get("cropped") and local:
            path = Path(local)                               # the scan in the folder is the cover
            return {"front": str(path), "source": "coverup:local", "width": c.width, "height": c.height,
                    "why": front["why"]}
        if c.data is None:
            pipe.fetch_full(c)
        if c.data is None:
            return {"note": f"CoverUp chose {c.provider} but the download failed"}
        path, w, h = _stage(c.data, self.staging)
        return {"front": str(path), "source": f"coverup:{c.provider}", "width": w, "height": h, "why": front["why"]}


class CoverArtArchive:
    name = "caa"

    def __init__(self, staging: Path):
        self.staging = staging

    def front(self, folder, ids: dict, artist="", album="") -> dict | None:
        mbid = ids.get("release_mbid")
        if not mbid:
            return None
        data = _get(f"https://coverartarchive.org/release/{mbid}/front-1200")
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
        data = _get(img["uri"], {"Authorization": f"Discogs token={self.token}"})
        path, w, h = _stage(data, self.staging)
        return {"front": str(path), "source": f"Discogs release {rid}", "width": w, "height": h}


def chain(profile, staging: Path, http=None, token="", log=None) -> list:
    """The providers to try, the profile's choice first; one that cannot start is left out
    (and logged), so artwork never stops the run."""
    first = profile["artwork"].get("provider", "coverup")
    order = [first] + [p for p in ("coverup", "caa", "discogs") if p != first]
    out = []
    for name in order:
        try:
            if name == "coverup":
                out.append(CoverUp(staging))
            elif name == "caa":
                out.append(CoverArtArchive(staging))
            elif name == "discogs":
                out.append(DiscogsImage(staging, http, token))
        except BaseException as exc:                          # CoverUp missing: SystemExit from its loader
            if log:
                log.warning("artwork provider %s unavailable: %s", name, exc)
    return out


def find_front(providers, folder, ids, artist="", album="", log=None) -> dict | None:
    notes = []
    for p in providers:
        try:
            got = p.front(folder, ids, artist, album)
        except Exception as exc:
            notes.append(f"{p.name}: {exc}")
            continue
        if got and got.get("front"):
            if notes:
                got["notes"] = notes
            return got
        if got and got.get("note"):
            notes.append(f"{p.name}: {got['note']}")
    if log and notes:
        log.info("no cover for %s: %s", folder, "; ".join(notes))
    return {"note": "; ".join(notes)} if notes else None
