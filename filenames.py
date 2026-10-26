"""filenames.py - safe, length-limited file and folder names.

make_path(dest_root, components, extension, limits) -> (Path, notes)

1. Each component is made safe for the chosen compatibility:
   linux    only "/" and NUL are replaced;
   windows  also \\ : * ? " < > | and control characters; trailing dots and spaces are
            removed; reserved device names (CON, PRN, AUX, NUL, COM1-9, LPT1-9, with any
            extension) get a trailing underscore.
   Text is put in Unicode NFC form.
2. Limits: every folder name and the file name (with its extension) must fit
   max_dir_bytes / max_name_bytes in UTF-8 bytes (Linux filesystems count bytes; 255 is
   the common limit) and, with windows compatibility, also 255 UTF-16 code units (what
   NTFS counts). The whole path must fit max_path_chars characters. There is no
   universal 260-character Windows limit: it applies to some Windows programs only, so
   it is a setting (max_path_chars), not an assumption.
3. Too long? Shortened deterministically, in this order, until everything fits:
   a. abbreviations from the profile ("Volume" -> "Vol."), whole words only;
   b. word-aware truncation, least important component first (the file name's title
      part, then the album folder, then the artist folder), never below 8 characters;
      the extension and room for a collision suffix are reserved first.
4. Still too long: notes say so and the path is None (the plan reports it as a
   conflict; nothing is guessed).

collision_suffix(key) is stable: the same file always gets the same " ~1a2b" suffix,
whichever order files are planned in.
"""
from __future__ import annotations

import hashlib
import math
import re
import unicodedata
from pathlib import Path

_WIN_BAD = re.compile(r'[\\/:*?"<>|\x00-\x1f\x7f]')
_LINUX_BAD = re.compile(r"[/\x00]")
_RESERVED = re.compile(r"^(con|prn|aux|nul|com[1-9¹²³]|lpt[1-9¹²³])(\..*)?$", re.I)
MIN_KEEP = 8
SUFFIX_LEN = 6                       # " ~1a2b"


def safe_component(text: str, compat="windows", file_stem=False) -> str:
    """One folder or file name, as Picard's make_save_path makes it: a leading dot becomes
    "_" ("...And Justice for All" -> "_..And Justice for All": never a hidden folder) and,
    with Windows-compatible names, a folder name's trailing dot becomes "_" ("R.E.M." ->
    "R.E.M_"; a file name keeps its dots, its extension follows them)."""
    s = unicodedata.normalize("NFC", str(text))
    s = (_WIN_BAD if compat == "windows" else _LINUX_BAD).sub("_", s)
    s = s.strip()
    if s.startswith("."):
        s = "_" + s[1:]
    if compat == "windows":
        s = s.rstrip(" ")
        if s.endswith(".") and not file_stem:
            s = s[:-1] + "_"
        m = _RESERVED.match(s)
        if m:                                   # "AUX. Remixes" is reserved too: AUX_. Remixes
            s = m.group(1) + "_" + s[len(m.group(1)):]
    if not s or set(s) <= {"."}:
        s = "_"
    return s


def _units(s: str, compat: str) -> int:
    """The larger of UTF-8 bytes and (with windows compatibility) UTF-16 code units;
    characters for compat "chars" (the path length limit)."""
    if compat == "chars":
        return len(s)
    b = len(s.encode("utf-8", "surrogateescape"))
    return max(b, len(s.encode("utf-16-le", "surrogatepass")) // 2) if compat == "windows" else b


def _fits(s, limit, compat):
    return _units(s, compat) <= limit


def abbreviate(text: str, abbreviations: dict) -> str:
    for long, short in sorted((abbreviations or {}).items(), key=lambda kv: -len(kv[0])):
        text = re.sub(rf"(?<!\w){re.escape(long)}(?!\w)", short, text, flags=re.I)
    return text


def truncate_words(text: str, limit: int, compat: str) -> str:
    """The longest whole-word prefix that fits - cut inside a word when whole words would
    keep less than half of what fits (a title with no spaces, as Japanese is written, or one
    very long word: never "13" for "13 <a long title>"; Picard cuts such a name inside it)."""
    if _fits(text, limit, compat):
        return text
    words = text.split(" ")
    out = ""
    for w in words:
        cand = (out + " " + w) if out else w
        if not _fits(cand, limit, compat):
            break
        out = cand
    if len(out) < MIN_KEEP or _units(out, compat) * 2 < limit:
        out = text
        while out and not _fits(out, limit, compat):
            out = out[:-1]
    return out.rstrip(" .,-–—_") or out


# ---- Picard's Windows path shortening (picard/util/filenaming.py, Picard 3.0.2) ------------
# With Windows compatibility on and "Windows long path support" off - Picard's default off
# Windows - a path below the destination's mount point fits Windows' 259 characters: every
# name 226 UTF-16 units at most, the folders proportionally shorter when they are too long.
WIN_MAX_FILEPATH_LEN = 259
WIN_MAX_DIRPATH_LEN = WIN_MAX_FILEPATH_LEN - 12
WIN_MAX_NODE_LEN = 255


def _u16len(text: str) -> int:
    return len(text.encode("utf-16-le", "surrogatepass")) // 2


def _u16cut(text: str, length: int) -> str:
    raw = text.encode("utf-16-le", "surrogatepass")[:max(0, length) * 2]
    if len(raw) >= 2 and 0xD800 <= int.from_bytes(raw[-2:], "little") <= 0xDBFF:
        raw = raw[:-2]                                  # (never half a surrogate pair)
    return raw.decode("utf-16-le", "surrogatepass")


def _win_shorten(dirs: list, stem: str, ext: str, length: int):
    """Picard's shorten_path in UTF-16 mode: each folder name, and the file name with its
    extension, at most length units (stripped)."""
    def cut(name, n):
        return name and _u16cut(name, n).strip() or ""
    return [cut(d, length) for d in dirs], cut(stem, length - len(ext))


def _ratio_cut(text: str, ratio: float) -> str:
    limit = max(1, int(math.floor(_u16len(text) / ratio)))
    return _u16cut(text, limit).strip()


def win_short_parts(parts: list, ext: str, reserved: int) -> list:
    """parts ([folders..., file stem]) shortened as Picard's _make_win_short_filename makes
    a relative path fit, reserved characters taken by the folder it goes into."""
    max_node = WIN_MAX_NODE_LEN - 29
    remaining = WIN_MAX_DIRPATH_LEN - reserved
    dirs, stem = _win_shorten(parts[:-1], parts[-1], ext, max_node)
    dirs = [d for d in dirs if d]
    dirpath = "/".join(dirs)
    dplen = _u16len(dirpath)
    if dplen <= remaining:
        filename_max = WIN_MAX_FILEPATH_LEN - (reserved + dplen + 1)
        _d, stem = _win_shorten([], stem, ext, filename_max)
        return dirs + [stem]
    names = list(dirs)
    remaining -= len(names) - 1
    average = float(remaining) / len(names)
    if average < 1:
        return dirs + [stem]                            # (Picard: "Path too long"; the limits below say so)
    short = [dn for dn in names if len(dn) <= average]
    total = sum(map(_u16len, names))
    shortchars = sum(map(_u16len, short))
    if remaining > shortchars + len(names) - len(short):
        ratio = float(total - shortchars) / (remaining - shortchars)
        names = [_ratio_cut(dn, ratio) if len(dn) > average else dn for dn in names]
    else:
        ratio = float(total) / remaining
        names = [_ratio_cut(dn, ratio) for dn in names]
    recovered = remaining - sum(map(_u16len, names))
    filename_max = WIN_MAX_FILEPATH_LEN - WIN_MAX_DIRPATH_LEN - 1 + recovered
    _d, stem = _win_shorten([], stem, ext, filename_max)
    return names + [stem]


def collision_suffix(key: str) -> str:
    return " ~" + hashlib.sha1(key.encode("utf-8", "surrogateescape")).hexdigest()[:4]


def make_path(dest_root, components, extension, limits: dict, suffix: str = ""):
    """(path or None, notes). components: [folders..., file stem]."""
    compat = limits.get("compat", "windows")
    max_name = int(limits.get("max_name_bytes", 255))
    max_dir = int(limits.get("max_dir_bytes", 255))
    max_path = int(limits.get("max_path_chars", 4096))
    abbr = limits.get("abbreviations") or {}
    notes = []
    ext = extension if extension.startswith(".") or not extension else "." + extension
    parts = [safe_component(c, compat, file_stem=(i == len(components) - 1)) for i, c in enumerate(components)]
    if not parts:
        return None, ["the naming template gave an empty path"]
    if limits.get("win_reserved") is not None:          # (Picard's 259-character Windows paths)
        short = win_short_parts(parts, ext, int(limits["win_reserved"]))
        if short != parts:
            notes.append(f"shortened {'/'.join(parts)!r} -> {'/'.join(short)!r} for Windows' path length "
                         "(as Picard does; Picard's \"Windows long path support\" setting turns it off)")
            parts = [safe_component(c, compat, file_stem=(i == len(short) - 1)) for i, c in enumerate(short)]
    reserve = len(ext) + SUFFIX_LEN       # always leave room for a collision suffix

    def limit_of(i):
        return (max_name - reserve) if i == len(parts) - 1 else max_dir

    # a. abbreviations where a component is too long
    for i, p in enumerate(parts):
        if not _fits(p, limit_of(i), compat) and abbr:
            q = safe_component(abbreviate(p, abbr), compat, file_stem=(i == len(parts) - 1))
            if q != p:
                notes.append(f"abbreviated {p!r} -> {q!r}")
                parts[i] = q
    # b. each component within its own limit
    for i, p in enumerate(parts):
        if not _fits(p, limit_of(i), compat):
            q = safe_component(truncate_words(p, limit_of(i), compat), compat, file_stem=(i == len(parts) - 1))
            notes.append(f"shortened {p!r} -> {q!r}")
            parts[i] = q

    def total():
        return len(str(Path(dest_root).joinpath(*parts))) + reserve

    # c. the whole path, least important first: file name, album folder, artist folder...
    order = list(range(len(parts) - 1, -1, -1))
    for i in order:
        if total() <= max_path:
            break
        over = total() - max_path
        p = parts[i]
        target = max(MIN_KEEP, len(p) - over)
        if target < len(p):
            q = truncate_words(p, target, "chars")      # characters here, not bytes
            q = q if len(q) >= MIN_KEEP or len(p) <= MIN_KEEP else p[:MIN_KEEP]
            q = safe_component(q, compat, file_stem=(i == len(parts) - 1))
            if q != p:
                notes.append(f"shortened {p!r} -> {q!r} for the path length limit")
                parts[i] = q
    if total() > max_path:
        notes.append(f"cannot fit the path in {max_path} characters even after shortening "
                     f"(destination folder alone is {len(str(dest_root))})")
        return None, notes
    parts[-1] = parts[-1] + suffix + ext
    return Path(dest_root).joinpath(*parts), notes


def fold(path) -> str:
    """The form two paths collide in on a case-insensitive filesystem."""
    return unicodedata.normalize("NFC", str(path)).casefold()
