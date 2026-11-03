"""tidy.py - an album's pictures in a "covers" folder and its other papers (rip logs, notes,
notes) in a "misc" folder of their own, when the profile asks for it ("subfolders").

    Album/cover.jpg                 stays (the front cover, what players show)
    Album/Covers/front.jpg          -> Album/front.jpg   (when the album folder has no front cover)
    Album/back.jpg                  -> Album/covers/back.jpg
    Album/Scans/Booklet/01.jpg      -> Album/covers/Booklet/01.jpg
    Album/Booklet.pdf               -> Album/covers/Booklet.pdf
    Album/Album.log, info.txt       -> Album/misc/Album.log, Album/misc/info.txt
    Album/Album.cue, Album.m3u      stay beside the audio (they name the files by their path),
    Album/Album.md5, .ffp, .sfv     as checksum lists do (the same reason)
    Album/album.nfo, artist.nfo     stay (media servers read them from the album folder)
    Album/Front Cover.jpg           stays when the folder has no cover.jpg / folder.jpg (the
                                    picture most like a front cover, or the folder's only one)
    Album/Extras/clip.txt           stays (a folder holding more than pictures and papers, a
                                    video's, is left as it is)
    Album/Disc 01/back.jpg          -> Album/Disc 01/covers/back.jpg   (where: "disc")
                                    -> Album/covers/Disc 01/back.jpg   (where: "album")

Only the destinations are worked out here; the planner plans each as a companion file of
the album (staged, verified, journaled and undoable as any other). A file already where
these rules put it stays, so planning again plans nothing new."""
from __future__ import annotations

import os
import re
from pathlib import Path

# every picture format, and PDF booklets (a shop's or label's booklet is the album's artwork)
COVER_EXT = {".jpg", ".jpeg", ".jfif", ".png", ".gif", ".bmp", ".tif", ".tiff", ".webp", ".heic", ".heif",
             ".avif", ".pdf"}
# rip logs and notes; never a cue sheet, playlist or checksum list (.cue, .m3u, .m3u8, .md5, .ffp,
# .sfv... stay with the audio they name by its path) nor a track's lyrics (.lrc)
MISC_EXT = {".log", ".txt", ".info", ".nfo", ".toc", ".accurip", ".md", ".rtf", ".doc", ".docx", ".odt",
            ".htm", ".html", ".url", ".json", ".xml", ".csv"}
# what media servers read from the album folder by its name (album.nfo, artist.nfo): left there
SIDECAR = re.compile(r"^(?:album|artist|folder|metadata)$", re.I)
SIDECAR_EXT = {".nfo", ".xml", ".json"}
# files that say nothing about a folder's contents (a folder of pictures with them is still one)
_JUNK = {"thumbs.db", "desktop.ini", ".ds_store"}
# the front cover, left in its folder: cover.jpg, folder.jpg, front.png, AlbumArt.jpg, poster.jpg
FRONT = re.compile(r"^(?:cover|folder|front|poster|albumart\w*)$", re.I)
# named otherwise, the front cover is the picture that says "front" ("Front Cover", "Band -
# Album - Front", "00 - front"), else "cover" ("cover1") and not another part of the case
SAYS_FRONT = re.compile(r"(?<![a-z])front", re.I)
SAYS_COVER = re.compile(r"(?<![a-z])cover", re.I)
NOT_FRONT = re.compile(r"(?<![a-z])(?:back|rear|inlay|inside|inner|tray|cd\d*|disc|disk|booklet|book|"
                       r"matrix|obi|spine|label|insert|sticker|page)(?![a-z])", re.I)
# folders that only hold pictures or papers: their name is not kept below covers/ or misc/
# ("Scans/Booklet/01.jpg" -> "covers/Booklet/01.jpg")
HOLDERS = {"scans", "scan", "artwork", "artworks", "art", "covers", "cover", "images", "pictures", "pics",
           "misc", "logs", "log"}
WHERE = ("disc", "album")
DEFAULTS = {"covers": False, "covers_name": "covers", "misc": False, "misc_name": "misc", "where": "disc"}
_DISC = re.compile(r"^(?:cd|disc|disk|dvd|side)\s*[-_.]?\s*(?:\d+|[a-h])\b|^\d{1,2}$", re.I)
_BAD = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def settings(profile) -> dict | None:
    """The profile's subfolders setting when either folder is on, else None."""
    s = dict(DEFAULTS, **((profile.get("subfolders") if hasattr(profile, "get") else None) or {}))
    return s if (s.get("covers") or s.get("misc")) else None


def problems(s) -> list:
    """What is wrong with a profile's subfolders setting, in plain words ([] = usable)."""
    if not isinstance(s, dict):
        return ["subfolders must be a table of covers, covers_name, misc, misc_name and where"]
    s = dict(DEFAULTS, **s)
    out = []
    for k in ("covers", "misc"):
        if not isinstance(s[k], bool):
            out.append(f"subfolders.{k} must be true or false")
    for k in ("covers_name", "misc_name"):
        v = s[k]
        if not isinstance(v, str) or not v.strip() or v.strip() in (".", "..") or v.startswith(".") or \
                _BAD.search(v) or v != v.strip() or v.endswith(".") or _DISC.match(v):
            out.append(f"subfolders.{k} must be a plain folder name (not hidden, no / or \\, not a disc "
                       "folder's name)")
    if isinstance(s["covers_name"], str) and isinstance(s["misc_name"], str) and \
            s["covers_name"].casefold() == s["misc_name"].casefold():
        out.append("subfolders.covers_name and subfolders.misc_name must differ")
    if s["where"] not in WHERE:
        out.append(f"subfolders.where must be one of {', '.join(WHERE)}")
    return out


def extensions(s) -> set:
    """The extensions that go with an album into these folders (besides the profile's companions)."""
    if not s:
        return set()
    return (COVER_EXT if s.get("covers") else set()) | (MISC_EXT if s.get("misc") else set())


def kind_of(path: Path, s) -> str:
    """"covers", "misc" or "" (the file stays where the album's other rules put it)."""
    ext = Path(path).suffix.lower()
    if s.get("covers") and ext in COVER_EXT:
        return "covers"
    if s.get("misc") and ext in MISC_EXT:
        return "misc"
    return ""


def _is_front(want: Path, front_stems=()) -> bool:
    return Path(want).suffix.lower() in COVER_EXT and (
        bool(FRONT.match(Path(want).stem)) or Path(want).stem.casefold() in {x.casefold() for x in front_stems})


def fronts_at(wants, album: Path, homes, front_stems=(), keep=()) -> set:
    """The folders the music lands in (the album folder, its disc folders) that get a front
    cover of their own from these files (where each would go otherwise); keep: the fronts
    named otherwise (likely_fronts)."""
    homes = {Path(h) for h in homes} | {Path(album)}
    return {Path(w).parent for w in wants if Path(w).parent in homes and _is_front(w, front_stems)} | \
        {Path(w).parent for w in keep}


def likely_fronts(wants, album: Path, homes, front_stems=()) -> set:
    """The pictures kept as the front cover of a folder the music lands in that has none named
    cover.jpg, folder.jpg...: the one most like a front cover (its name says front or cover, not
    back or inlay), else the folder's only picture when nothing names it another part."""
    homes = {Path(h) for h in homes} | {Path(album)}
    by_home = {}
    for w in map(Path, wants):
        if w.parent in homes and w.suffix.lower() in COVER_EXT - {".pdf"}:
            by_home.setdefault(w.parent, []).append(w)
    out = set()
    for pics in by_home.values():
        if any(_is_front(w, front_stems) for w in pics):
            continue
        maybe = [w for w in pics if not NOT_FRONT.search(w.stem)]
        named = [w for w in pics if SAYS_FRONT.search(w.stem)] or [w for w in maybe if SAYS_COVER.search(w.stem)]
        if named:
            out.add(min(named, key=lambda w: (len(w.name), w.name)))
        elif len(pics) == 1 and maybe:
            out.add(pics[0])
    return out


def _only_papers(folder: Path) -> bool:
    """The folder holds nothing but pictures and papers (Scans/, Booklet/), or cannot be read;
    a folder with a video or anything else in it (Extras/) is not one to take apart."""
    try:
        with os.scandir(folder) as it:
            names = [e.name for e in it if e.is_file(follow_symlinks=False)]
    except OSError:
        return True
    keep = COVER_EXT | MISC_EXT
    return all(Path(n).suffix.lower() in keep for n in names if n.casefold() not in _JUNK)


def _in_other_folder(src: Path, want: Path, home: Path, homes) -> bool:
    """src (the file where it is now) sits in a folder below its home that is neither a disc
    folder nor a folder of pictures and papers only: it is left where it is with that folder."""
    for i in range(len(want.relative_to(home).parts) - 1):
        if want.parents[i] in homes or i >= len(src.parents) - 1:
            continue
        if not _only_papers(src.parents[i]):
            return True
    return False


def target(want: Path, album: Path, homes, s, front_stems=(), fronted=None, src=None,
           keep=()) -> tuple[Path, str]:
    """(where the file goes, "covers" | "misc" | ""): want is where it would go otherwise (its
    own place, for an album tagged where it is); album the album's folder, homes the folders its
    music lands in (the album folder, its disc folders). Outside the album folder, or neither a
    picture nor a paper, it stays at want.
    fronted (fronts_at; updated): the folders with a front cover beside the music. A front
    cover kept in Scans/ or Covers/ of one that has none goes beside the music instead (the
    front cover is the album folder's). src: where the file is now (a folder of its own that
    holds more than pictures and papers keeps it); keep: fronts named otherwise (likely_fronts)."""
    want, album = Path(want), Path(album)
    kind = kind_of(want, s)
    if not kind:
        return want, ""
    homes = {Path(h) for h in homes} | {album}
    if kind == "covers" and want.parent in homes and (_is_front(want, front_stems) or want in keep):
        return want, ""                                   # the front cover stays beside the music
    if want.parent in homes and want.suffix.lower() in SIDECAR_EXT and SIDECAR.match(want.stem):
        return want, ""                                   # (album.nfo: what a media server reads there)
    if kind == "covers" and fronted is not None and _is_front(want, front_stems):
        home = max((h for h in homes if h in want.parents), key=lambda h: len(h.parts), default=None)
        if home is not None and home not in fronted:
            fronted.add(home)
            return home / want.name, ""
    if s.get("where") == "album":
        home = album if (want == album or album in want.parents) else None
    else:
        home = max((h for h in homes if h in want.parents), key=lambda h: len(h.parts), default=None)
    if home is None or (src is not None and _in_other_folder(Path(src), want, home, homes)):
        return want, ""
    names = {s["covers_name"].casefold(), s["misc_name"].casefold()} | HOLDERS
    sub = [x for x in want.relative_to(home).parts[:-1] if x.casefold() not in names]
    return home.joinpath(s[f"{kind}_name"], *sub, want.name), kind


def blocked(dst: Path, album: Path) -> Path | None:
    """The first folder on the way from the album folder to dst's folder that is there but
    is not a real folder (a file named "misc", a "covers" link): the file cannot go there.
    None when every one is a folder or can be made."""
    dst, album = Path(dst), Path(album)
    if album not in dst.parents:
        return None
    for q in reversed(dst.parents):
        if q == album or album not in q.parents:
            continue
        if q.is_symlink() or (q.exists() and not q.is_dir()):
            return q
    return None
