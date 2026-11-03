"""covers_find.py - every cover there is for an album, to choose one by eye (Find Covers…).

  own(folder, files)                the album's own pictures: in its folder, its disc folders and
                                    their picture folders (Artwork/, Scans/ ...), and the front
                                    embedded in its tracks
  found(folder, files, ids, ...)    every front cover CoverUp's sources find (Cover Art Archive,
                                    Discogs, fanart.tv, TheAudioDB, shops, web search ...)
  ids_of(album)                     the identifiers a plan's album gives the cover search

Each is a Choice: where it is from, its size, a small preview and how to get the full image.
Nothing here writes anything; the chosen image goes through the inspector like Choose Image….
"""
from __future__ import annotations

import io
from dataclasses import dataclass, field
from pathlib import Path

import artwork

IMAGE_EXT = (".jpg", ".jpeg", ".png", ".webp", ".tif", ".tiff", ".bmp")
THUMB = 240                              # (the preview's side, px)
MAX_FOUND = 40                           # (the found covers shown, largest first)


@dataclass
class Choice:
    kind: str                            # own | found | ai
    source: str                          # "Artwork/Scans/Front.jpg", "Cover Art Archive", "AI: Era"
    width: int = 0
    height: int = 0
    thumb: bytes | None = None           # a small JPEG for the sheet
    data: bytes | None = None            # the full image, when already read
    fetch: object = None                 # () -> bytes, for one read only when it is chosen
    note: str = ""
    extra: dict = field(default_factory=dict)

    def full(self) -> bytes:
        if self.data is None and callable(self.fetch):
            self.data = self.fetch()
        return self.data or b""


def thumbnail(data: bytes, side=THUMB) -> bytes | None:
    """A small JPEG of an image, or None when it is not one."""
    from PIL import Image
    try:
        with Image.open(io.BytesIO(data)) as im:
            im = im.convert("RGB")
            im.thumbnail((side, side))
            out = io.BytesIO()
            im.save(out, "JPEG", quality=85)
            return out.getvalue()
    except Exception:
        return None


def _picture_files(folder: Path) -> list[Path]:
    out = []
    for d in artwork._album_dirs(folder):
        for where in [d] + artwork._art_folders(d):
            try:
                out += sorted(x for x in where.iterdir() if x.is_file() and not x.is_symlink()
                              and x.suffix.lower() in IMAGE_EXT)
            except OSError:
                continue
    return out


def own(folder, files=()) -> list[Choice]:
    """The album's own pictures, fronts first, then the largest; each once."""
    folder = Path(folder)
    seen, out = set(), []
    for p in _picture_files(folder):
        try:
            data = p.read_bytes()
        except OSError:
            continue
        if data in seen:
            continue
        seen.add(data)
        size = artwork._size_of(data)
        if not size:
            continue
        out.append(Choice("own", str(p.relative_to(folder)), size[0], size[1], thumbnail(data), data,
                          extra={"front": artwork._says_front(p.stem) or p.stem.lower() in artwork.COVER_STEMS}))
    for f in list(files)[:1]:                       # (the tracks share one front, as written)
        data = artwork.embedded_front(f)
        if data and data not in seen:
            size = artwork._size_of(data) or (0, 0)
            out.append(Choice("own", f"in {Path(f).name}", size[0], size[1], thumbnail(data), data,
                              extra={"front": True}))
    out.sort(key=lambda c: (not c.extra.get("front"), -(c.width * c.height)))
    return out


PROVIDER_WORDS = {"caa": "Cover Art Archive", "discogs": "Discogs", "fanart": "fanart.tv", "audiodb": "TheAudioDB",
                  "lastfm": "Last.fm", "itunes": "Apple", "deezer": "Deezer", "bandcamp": "Bandcamp",
                  "wikimedia": "Wikimedia Commons", "websearch": "Web search", "local": "Your scans"}


def found(folder, files, ids: dict, artist="", album="", staging=None, cancelled=lambda: False) -> list[Choice]:
    """Every front cover CoverUp's sources find for the album, largest first (MAX_FOUND), each
    with a preview read now and its full image read only when chosen. The album's own scans
    (CoverUp's "local") are left to own()."""
    import tempfile
    staging = Path(staging or tempfile.mkdtemp(prefix="tm-covers-"))
    cu = artwork.CoverUp(staging)
    cu.s = cu.s.with_(providers_enabled=[p for p in cu.s["providers_enabled"] if p != "local"])
    pipe, fronts = cu.candidates(folder, ids, artist, album, files=list(files) or None)
    out = []
    for c in fronts[:MAX_FOUND]:
        if cancelled():
            break
        thumb = c.thumb_data
        if thumb is None and c.thumb_url:
            try:
                thumb = artwork._get(c.thumb_url, limit=2_000_000)
            except Exception:
                thumb = None
        out.append(Choice("found", PROVIDER_WORDS.get(c.provider, c.provider), c.width or 0, c.height or 0,
                          thumbnail(thumb) if thumb else None, c.data,
                          fetch=lambda c=c: c.data or pipe.fetch_full(c),
                          note=c.attribution or c.comment or "", extra={"provider": c.provider}))
    return out


def ids_of(album: dict) -> tuple[dict, str, str]:
    """(ids, artist, album title) for a plan's album: the release it was matched to - the tags
    the plan writes over the first file's own (reads that file) - else its identification."""
    import tagio
    t = {}
    fe = next(iter(album.get("files") or []), None)
    if fe:
        try:
            t = dict(tagio.read(fe["src"]))
        except Exception:                            # (unreadable: the plan's changes only)
            t = {}
        t.update(fe.get("changes") or {})
    one = lambda k: str((t.get(k) or [""])[0]) if isinstance(t.get(k), list) else str(t.get(k) or "")  # noqa: E731
    ident = album.get("identification") or {}
    rid, prov = str(ident.get("release_id") or ""), str(ident.get("provider") or "")
    ids = {"release_mbid": one("musicbrainz_albumid") or (rid if prov.startswith("musicbrainz") else ""),
           "rg_mbid": one("musicbrainz_releasegroupid"),
           "artist_mbid": "; ".join(t.get("musicbrainz_albumartistid") or [])
           if isinstance(t.get("musicbrainz_albumartistid"), list) else "",
           "discogs_release_id": one("discogs_release_id") or (rid if prov == "discogs" else ""),
           "discogs_master_id": one("discogs_master_id")}
    rel = album.get("release") or {}
    first = lambda v: str(v[0]) if isinstance(v, list) and v else ""  # noqa: E731
    artist = one("albumartist") or one("artist") or first(rel.get("albumartist"))
    title = one("album") or first(rel.get("album"))
    return ids, artist, title
