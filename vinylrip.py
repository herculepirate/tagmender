"""vinylrip.py - what tells one rip of a record from another (decided 2026-11-17).

A collector keeps several rips of one vinyl pressing (rippers, resolutions, years) and rips of
different pressings of one album. Each rip is its own copy; what tells them apart is said in
two places only:

  - the album folder's name (VIN-3): "1977 - A Farewell To Kings [Vinyl Rip PBTHAL SRM-1-1184]"
    - the ripper when one is clearly named and the pressing's catalogue number (never the
    resolution: decided 2026-11-17), with the year ripped in front ("[2022 Vinyl Rip
    SRM-1-4013]") only where two copies of one release would otherwise share one folder - as
    the naming fields %_riptag%, %_ripper%, %_rip_catalog%, %_rip_year%, %_rip_resolution% (and
    inside %_edition%);
  - one line of the comment tag (VIN-4): "Vinyl rip: PBTHAL · 24-bit/96 kHz · ripped 2012 ·
    VPI Scoutmaster, Zyx Airy 3X-SB" - each field only when it is known, beside whatever the
    comment already says (only TagMender's own line, told by its prefix, is added or replaced).

Never in the album, version or grouping tags. Conservative throughout: a ripper is named only
when the folder name or the rip notes clearly name one (VIN-3); the equipment only when the
notes label it ("Turntable: ...", "Cartridge: ...", a turntable heading's first item, a line
ending in "cartridge"); the year ripped only when a line about the rip dates it.
"""
from __future__ import annotations

import re
from collections import Counter
from pathlib import Path

PREFIX = "Vinyl rip: "                 # VIN-4: TagMender's own comment line starts so
SEP = " · "                       # " · "

LOSSLESS = {".flac", ".wav", ".aif", ".aiff", ".ape", ".wv", ".m4a", ".alac", ".tak"}

# rippers known by one name, spelled as they sign it (a folder name's "pbthal" is "PBTHAL")
KNOWN_RIPPERS = {"pbthal": "PBTHAL"}

# capitals after "Vinyl Rip" that name no ripper: labels, cutting and pressing words, formats
# (VIN-3: such a word is a ripper only when it is a known one or the rip notes name it too)
_NOT_RIPPER = {
    "DMM", "RM", "LP", "EP", "MFSL", "MOFI", "UHQR", "QRP", "RTI", "HQ", "HQ180", "OBI", "MONO", "STEREO",
    "PROMO", "TEST", "PRESS", "PRESSING", "ORIGINAL", "FIRST", "REISSUE", "REMASTER", "REMASTERED", "VINYL",
    "RIP", "FLAC", "WAV", "DSD", "PCM", "LPCM", "ALAC", "AIFF", "APE", "WAVPACK", "HDTRACKS", "WEB", "CD", "SACD",
    "BOX", "SET", "DELUXE", "LIMITED", "EDITION", "ALBUM", "SINGLE", "DOUBLE", "MAXI", "SPLIT", "LIVE", "DEMO",
    "BOOTLEG", "REMIX", "MONOMIX", "STUDIO", "MASTER", "SPECIAL", "RAW", "CLEAN", "MINT", "RARE", "AUDIOPHILE",
    "CLEAR", "WHITE", "BLACK", "COLOURED", "COLORED", "QUIEX", "ORIG",
}
_COUNTRY = {"US", "USA", "UK", "GB", "JP", "DE", "EU", "FR", "IT", "NL", "CA", "AU", "SK", "ES", "SE", "BR", "USSR"}
# words that sign no ripper: software, forum roles, the ripper's own words for himself, days
_NOT_A_NAME = {
    "the", "a", "an", "me", "myself", "us", "my", "using", "with", "hand", "ear", "way", "request", "requests",
    "eac", "exact", "audacity", "foobar", "foobar2000", "dbpoweramp", "xld", "audition", "adobe", "izotope", "rx",
    "clickrepair", "vinylstudio", "sox", "ffmpeg", "flac", "wavelab", "soundforge", "sound", "dvd", "usb",
    "admin", "administrator", "moderator", "mod", "staff", "torrentbot", "bot", "anonymous", "unknown", "dr",
    "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday", "someone", "somebody", "friend",
}

_VINYL_RIP_WORDS = re.compile(r"\bvinyl\s*-?\s*rip\b|\bvinylrip\b|\bLP\s*rip\b|\bneedle\s*-?\s*drop\b", re.I)
_RES_IN_NAME = re.compile(r"\b(?:\d{2}\s*-?\s*bits?|\d{2,3}(?:[.,]\d)?\s*k?hz|\d{2}\s*[-/]\s*\d{2,3}(?:[.,]\d)?)\b", re.I)
# "rip by X", "vinyl rip by X", "ripped by X", "needle drop by X": one name, or two words both
# capitalised ("Fran Solo"; never "john at", "Foo using")
_BY = re.compile(r"\b(?i:(?:vinyl\s+)?(?:rip(?:ped)?|needle\s*-?\s*drop|digiti[sz]ed)\s+by)\s*[:\-]?\s*"
                 r"([A-Za-z][\w.\-]{1,23}(?: [A-Z][\w.\-]{1,23})?)")
_POSTED = re.compile(r"^\W*(?i:posted\s+by)\s*:?\s*([A-Za-z][\w.\-]{1,23})\b", re.M)
_POSSESSIVE = re.compile(r"\b([A-Za-z][\w\-]{2,23})['’]s\s+(?:\d{2}\s*-?\s*bit|vinyl|rip|needle)", re.I)

_RIP_LINE = re.compile(r"\brip(?:s|ped|ping)?\b|\bneedle\s*-?\s*drop|\btransfer(?:red)?\b|\bdigiti[sz]ed\b", re.I)
_MONTH = (r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?")
# VIN-3: a year ripped follows the rip verb (or a date label) at once, a date between at most:
# "recorded February 1, 2012", "ripped 2012-02-01", "Rip date: 2019-03-02", "transferred in 2008";
# never "Ripped from my 1985 UK first pressing", "Recorded live 1981", "from the 1989 reissue"
_RIPPED_WHEN = re.compile(
    r"\b(?:ripped|recorded|digiti[sz]ed|transferred|rip\s+date|date\s+ripped|ripping\s+date)\s*[:=]?\s*"
    r"(?:(?:on|in)\s+)?(?:" + _MONTH + r"\s+\d{1,2}(?:st|nd|rd|th)?,?\s+|\d{1,2}(?:st|nd|rd|th)?\s+" + _MONTH +
    r",?\s+|\d{1,2}[./]\d{1,2}[./])?((?:19[89]|20[0-9])\d)\b(?!\s*(?:uk|us|german|japan\w*|first|original|"
    r"pressings?|reissues?|copy|copies|lp|records?|vinyl|edition|issue|release)\b)", re.I)


def _first_text(v) -> str:
    if isinstance(v, (list, tuple)):
        return str(v[0]) if v else ""
    return str(v or "")


# ---------------------------------------------------------------------------- resolution
def resolution_of(files) -> tuple:
    """(bits, sample rate) the album's lossless files share most, from the audio itself
    (mutagen's stream info, never a folder name or the notes); () for lossy files or none read."""
    import mutagen
    seen = Counter()
    for f in list(files or ())[:40]:
        p = Path(str(f))
        if p.suffix.lower() not in LOSSLESS:
            continue
        try:
            info = mutagen.File(str(p)).info
        except Exception:
            continue
        if p.suffix.lower() == ".m4a" and "alac" not in str(getattr(info, "codec", "")).lower():
            continue
        bits, rate = getattr(info, "bits_per_sample", 0) or 0, getattr(info, "sample_rate", 0) or 0
        if bits and rate:
            seen[(int(bits), int(rate))] += 1
    return seen.most_common(1)[0][0] if seen else ()


def _khz(rate: int) -> str:
    k = rate / 1000.0
    return f"{k:.1f}".rstrip("0").rstrip(".") if k != int(k) else str(int(k))


def resolution_name(res) -> str:
    """"24-96", "32-192", "16-44.1" for a folder name; "" for none."""
    return f"{res[0]}-{_khz(res[1])}" if res else ""


def resolution_words(res) -> str:
    """"24-bit/96 kHz" for the comment line; "" for none."""
    return f"{res[0]}-bit/{_khz(res[1])} kHz" if res else ""


# ---------------------------------------------------------------------------- ripper
def _canon(name: str) -> str:
    name = name.strip(" .-_:")
    return KNOWN_RIPPERS.get(name.casefold(), name)


def _a_name(name: str) -> bool:
    words = str(name or "").strip(" .-_:").split()
    return bool(words) and all(w.strip(".").casefold() not in _NOT_A_NAME for w in words)


def _signed(m) -> bool:
    """A "rip by X" match that names someone: not software or a forum word, and - unless a known
    ripper - not a word in lower case running on into the sentence ("rip by john at ...",
    "rip by Foo using Audacity"): a name ends at punctuation or the line's end."""
    name = m.group(1)
    if not _a_name(name):
        return False
    if name.split()[0].casefold() in KNOWN_RIPPERS:
        return True
    after = m.string[m.end():m.end() + 30]
    return not re.match(r" +[a-z]", after)


def _brackets(text):
    return re.findall(r"[\(\[\{]([^\)\]\}]+)[\)\]\}]", str(text or ""))


def ripper_in_name(folder_name: str) -> str:
    """VIN-3: the ripper a folder name names for sure: a known ripper's name anywhere in its
    brackets ("[Vinyl Rip PBTHAL 24bit 96khz ...]"), or "rip by X". A word in capitals after
    "Vinyl Rip" is only a candidate (caps_in_name): never a catalogue number ("[Vinyl Rip 24bit
    192khz Mercury Price19]": PRICE 19 is the pressing's), a label, a country or a format."""
    for content in _brackets(folder_name):
        for w in re.findall(r"[\w\-]+", content):
            if w.casefold() in KNOWN_RIPPERS:
                return KNOWN_RIPPERS[w.casefold()]
        m = _BY.search(content)
        if m and _signed(m):
            return _canon(m.group(1))
    return ""


def caps_in_name(folder_name: str) -> str:
    """VIN-3: a word in capitals of 4 to 12 letters right after "Vinyl Rip" and before the
    resolution, that is no label, country, format or pressing word ("[Vinyl Rip KRONOS 24bit
    96khz ...]"): a ripper only where the rip notes name it too (describe)."""
    for content in _brackets(folder_name):
        m = _VINYL_RIP_WORDS.search(content)
        if not m:
            continue
        after = content[m.end():].split()
        if len(after) > 1 and not _RES_IN_NAME.match(after[0]) and _RES_IN_NAME.match(after[1]):
            w = after[0].strip(",;:")
            if re.fullmatch(r"[A-Z]{4,12}", w) and w not in _NOT_RIPPER and w not in _COUNTRY:
                return w
    return ""


# TagMender's own folder edition (VIN-3): "[1977 Vinyl Rip PBTHAL SRM-1-1184 ripped 2012]",
# "[Vinyl Rip SRM-1-1184]" - no resolution, no other words
_OWN_FOLDER = re.compile(r"^(?:(?:19|20)\d\d )?Vinyl Rip(?: (?P<rest>.+?))?(?: ripped (?P<year>(?:19|20)\d\d))?$")


def own_folder(folder_name: str, catno: str = "") -> dict:
    """VIN-3: what TagMender's own folder edition says ({"ripper", "year"}), read back so a
    folder it named plans the same again (idempotent); {} for any other name. The ripper is what
    stands before the catalogue number (catno, the release's)."""
    for content in _brackets(folder_name):
        m = _OWN_FOLDER.match(content.strip())
        if not m or _RES_IN_NAME.search(content):
            continue
        rest = (m.group("rest") or "").strip()
        catno = str(catno or "").strip()
        if catno and rest.endswith(catno):
            rest = rest[:-len(catno)].strip()
        elif catno and rest:
            continue                                  # (another pressing's number: not ours to read)
        who = rest if rest and len(rest.split()) <= 3 and _a_name(rest) and not re.search(r"\d{3}", rest) else ""
        return {"ripper": _canon(who) if who else "", "year": m.group("year") or ""}
    return {}


def ripper_in_notes(text: str) -> str:
    """VIN-3: the ripper the rip notes (or a comment tag holding them) sign: "Vinyl rip by
    Packgrog", "ripped by X"; else "Posted by pbthal" at a line's start; else a known
    ripper's "PBTHAL's 24bit 192khz raw audio". Never software ("ripped by EAC"), a forum's
    "admin", "request", a day, "me"; never "transferred by" (a mastering engineer: "transferred
    by Bob Ludwig"), nor "uploaded by". "" when none is clearly named."""
    text = str(text or "")
    for m in _BY.finditer(text):
        if _signed(m):
            return _canon(m.group(1))
    m = _POSTED.search(text)
    if m and _a_name(m.group(1)):
        return _canon(m.group(1))
    for m in _POSSESSIVE.finditer(text):
        if m.group(1).casefold() in KNOWN_RIPPERS:
            return KNOWN_RIPPERS[m.group(1).casefold()]
    return ""


# ---------------------------------------------------------------------------- year, equipment
def rip_year(text: str, not_years=()) -> str:
    """The year the rip was made, when a line about the rip dates it right after its verb
    ("Vinyl rip by X, recorded February 1, 2012"; "Rip date: 2019-03-02"; "transferred in
    2008"): only on a line that speaks of ripping, never a year before a pressing, reissue,
    copy or record word ("ripped from my 1985 UK first pressing"), nor one of not_years (the
    pressing's, the album's original)."""
    skip = {str(y)[:4] for y in not_years or () if y}
    for line in str(text or "").splitlines():
        if not _RIP_LINE.search(line):
            continue
        for m in _RIPPED_WHEN.finditer(line):
            if m.group(1) not in skip:
                return m.group(1)
    return ""


_CUT = re.compile(r"\s+(?:with|on|w/|and|using|via|\+)\s+|\s*[(\[,;]|\s+-\s+", re.I)


def _short(item: str, limit: int = 32) -> str:
    item = re.sub("^[\\s\\-*•·>﻿�]+", "", item).strip()
    item = _CUT.split(item, maxsplit=1)[0].strip(" .:-")
    return item if 2 <= len(item) <= limit else ""


def _gear(words: str) -> bool:
    """A make and model, not a sentence: every word capitalised or holding a digit."""
    ws = [w for w in re.split(r"\s+", words.strip()) if w]
    return bool(ws) and len(ws) <= 6 and all(w[:1].isupper() or any(c.isdigit() for c in w) for w in ws)


def equipment(text: str) -> str:
    """The turntable and cartridge the notes name, short ("VPI Scoutmaster, Zyx Airy 3X-SB"):
    "Turntable: X" / "Deck: X" / "Cartridge: X" lines, the first listed item ("- X") under a
    turntable heading, a line naming a make and model and ending in "cartridge" ("Zyx Airy
    3X-SB cartridge"; "Pioneer PL-200 with Denon DL-110 cartridge" names both). Never a
    sentence ("Track 3 has a click in the cartridge"). "" when the notes label neither."""
    lines = [x.strip() for x in str(text or "").splitlines()]
    table = cart = ""
    for i, line in enumerate(lines):
        low = line.casefold()
        m = re.match(r"^[\-*\s]*(turntable|deck|cartridge|cart|stylus)\s*:\s*(.+)$", line, re.I)
        if m:
            what, val = m.group(1).casefold(), m.group(2)
            if what in ("turntable", "deck") and not table:
                table = _short(val)
            elif what in ("cartridge", "cart") and not cart:
                cart = _short(val)
            continue
        if not table and re.match(r"^turntable(?:\s+(?:equipment|profile|setup|used))*\s*:?\s*$", low):
            nxt = next((x for x in lines[i + 1:i + 3] if x), "")
            if nxt.startswith(("-", "*", "•")):
                got = _short(nxt)
                table = got if _gear(got) else ""
            continue
        if not cart and re.search(r"\bcartridge\s*$", low) and len(line) < 80:
            body = re.sub(r"\s*cartridge\s*$", "", line, flags=re.I)
            body = re.sub("^[\\s\\-*•]+", "", body)
            parts = re.split(r"\s+with\s+", body, maxsplit=1, flags=re.I)
            if not all(_gear(x) for x in parts):
                continue
            if len(parts) == 2:
                if not table:
                    table = _short(parts[0])
                cart = _short(parts[1])
            else:
                cart = _short(body)
    return ", ".join(x for x in (table, cart) if x)


# ---------------------------------------------------------------------------- the copy
def notes_of(folder) -> str:
    """The rip notes beside the music (.txt, .nfo, .info, .md, in sub-folders too)."""
    try:
        import match_albums as ma
        return ma.notes_text(Path(str(folder)))
    except Exception:
        return ""


def notes_here(folder, limit=20000) -> str:
    """The notes in FOLDER itself only (not its sub-folders): a set's parent folder's."""
    out = []
    try:
        for p in sorted(Path(str(folder)).iterdir()):
            if p.suffix.lower() in (".info", ".txt", ".nfo", ".md") and p.is_file() and p.stat().st_size < limit:
                out.append(p.read_text(encoding="utf-8", errors="replace"))
    except OSError:
        pass
    return " ".join(out)[:limit]


_RES_PART = re.compile(r"^\d{1,2}-bit/\d{2,3}(?:\.\d)? kHz$")
_YEAR_PART = re.compile(r"^ripped ((?:19|20)\d\d)$")


def parse_own(line: str) -> dict | None:
    """VIN-4: TagMender's own comment line read back ({"ripper", "resolution", "year",
    "equipment"} as written), or None: only a line that is wholly in the form TagMender writes -
    "Vinyl rip: " then, in order and joined by " · ", a ripper, the resolution ("24-bit/96
    kHz"), "ripped 2012", the equipment, with the resolution or the year there. A line of your
    own ("Vinyl rip: my own notes, Technics SL-1200") is never taken for it."""
    line = str(line or "")
    if not line.startswith(PREFIX):
        return None
    parts = line[len(PREFIX):].split(SEP)
    if not parts or any(not p.strip() or p != p.strip() for p in parts):
        return None
    out = {"ripper": "", "resolution": "", "year": "", "equipment": ""}
    i = 0
    if i < len(parts) and not _RES_PART.match(parts[i]) and not _YEAR_PART.match(parts[i]):
        if i + 1 >= len(parts) or not (_RES_PART.match(parts[i + 1]) or _YEAR_PART.match(parts[i + 1])):
            return None                               # (a ripper is followed by the resolution or year)
        out["ripper"] = parts[i]
        i += 1
    if i < len(parts) and _RES_PART.match(parts[i]):
        out["resolution"] = parts[i]
        i += 1
    if i < len(parts) and _YEAR_PART.match(parts[i]):
        out["year"] = _YEAR_PART.match(parts[i]).group(1)
        i += 1
    if not (out["resolution"] or out["year"]):
        return None
    if i < len(parts):
        out["equipment"] = parts[i]
        i += 1
    return out if i == len(parts) else None


def own_line(text: str) -> bool:
    return parse_own(text) is not None


def own_in(comments) -> dict:
    """TagMender's own line in these comment values, read back ({} when none)."""
    for c in comments or ():
        for ln in re.split(r"\r?\n", str(c or "")):
            got = parse_own(ln)
            if got:
                return got
    return {}


def year_in_name(folder_name: str, pressing_year: str = "", original_year: str = "") -> str:
    """VIN-3: the year ripped a folder name gives: your own form, a year right before "Vinyl
    Rip" in a bracket that also names the rip's resolution ("[2022 Vinyl Rip 24bit 192khz Anthem
    R1-83736]") - never ED-2's own "[1985 Vinyl Rip]" nor a word between ("[1985 UK Vinyl
    Rip]", "[2019 DMM Vinyl Rip]": a pressing's), nor the pressing's or the album's own year;
    or TagMender's own "... ripped 2022]"."""
    skip = {str(y)[:4] for y in (pressing_year, original_year) if y}
    for content in _brackets(folder_name):
        m = re.match(r"^\s*((?:19[89]|20[0-9])\d)\s+vinyl\s*-?\s*rip\b(.*)$", content, re.I)
        if m and _RES_IN_NAME.search(m.group(2)) and m.group(1) not in skip:
            return m.group(1)
        m = _OWN_FOLDER.match(content.strip())
        if m and m.group("year") and not _RES_IN_NAME.search(content):
            return m.group("year")
    return ""


def describe(folder, files, catno: str = "", notes: str | None = None, comments=(), pressing_year: str = "",
             original_year: str = "", artist: str = "", parent_notes: str = "") -> dict:
    """What tells this rip apart: {"resolution": (bits, rate) or (), "ripper", "catno", "year",
    "equipment"} (each "" or () when not known). folder: the copy's album folder (its name and
    notes); comments: the files' comment tags (rip notes are often kept there; TagMender's own
    line is read back, never as notes); pressing_year / original_year: the release's own years
    (no year ripped); artist: the album artist (never a ripper). The ripper: the folder name's
    (a known ripper, "rip by X", TagMender's own folder form), TagMender's own comment line, the
    notes' signature, then a capitalised word after "Vinyl Rip" the notes name too."""
    folder = Path(str(folder)) if folder else None
    notes = notes_of(folder) if notes is None and folder else (notes or "")
    own = own_in(comments)
    said = "\n".join(dict.fromkeys("\n".join(ln for ln in str(c).splitlines() if not own_line(ln.rstrip("\r")))
                                   for c in comments or () if c))
    text = notes + "\n" + said + "\n" + (parent_notes or "")
    name = folder.name if folder else ""
    mine = own_folder(name, catno)
    who = ripper_in_name(name) or mine.get("ripper") or own.get("ripper") or ripper_in_notes(text)
    if not who:
        cap = caps_in_name(name)
        if cap and re.search(rf"\b{re.escape(cap)}\b", text, re.I):
            who = cap
    if who and artist and who.casefold() == str(artist).casefold():
        who = ""
    year = year_in_name(name, pressing_year, original_year) or own.get("year") or \
        rip_year(text, (pressing_year, original_year))
    return {"resolution": resolution_of(files), "ripper": who, "catno": str(catno or "").strip(),
            "year": year, "equipment": equipment(text) or own.get("equipment") or ""}


def name_tag(d: dict) -> str:
    """VIN-3: "PBTHAL SRM-1-1184" - the ripper and the catalogue number, those known (never the
    resolution)."""
    return " ".join(x for x in (d.get("ripper") or "", d.get("catno") or "") if x)


def comment_line(d: dict) -> str:
    """VIN-4: "Vinyl rip: PBTHAL · 24-bit/96 kHz · ripped 2012 · VPI Scoutmaster, Zyx Airy 3X-SB"
    ("" when neither the resolution nor the year ripped is known: the line is told by them)."""
    res, year = resolution_words(d.get("resolution")), d.get("year") or ""
    if not (res or year):
        return ""
    parts = [d.get("ripper") or "", res, f"ripped {year}" if year else "",
             (d.get("equipment") or "").replace(SEP, ", ")]
    line = PREFIX + SEP.join(p for p in parts if p)
    return line if own_line(line) else ""


def merged_comment(existing: str, line: str) -> str:
    """VIN-4: the comment with TagMender's own line (one wholly in its form: parse_own) replaced
    by LINE, or LINE added after the text already there - every other line kept as it is, a
    line of yours that only starts "Vinyl rip: " too. Unchanged when LINE is there already
    (idempotent); "" lines are never added."""
    existing = str(existing or "")
    if not line:
        return existing
    nl = "\r\n" if "\r\n" in existing else "\n"
    lines = re.split(r"\r?\n", existing)
    if line in lines:
        return existing
    own = [i for i, ln in enumerate(lines) if own_line(ln)]
    if own:
        lines[own[0]] = line
        return nl.join(lines)
    if not existing.strip():
        return line
    return existing.rstrip("\r\n") + nl + line


def merged_values(values, line: str) -> list:
    """merged_comment over a comment's values (a Vorbis comment may hold several): the value
    holding TagMender's own line gets it, else the first; a value that is the line itself
    alone is never added twice."""
    values = [str(v) for v in values or ()]
    if not line:
        return values
    if any(line in re.split(r"\r?\n", v) for v in values):
        return values
    at = next((i for i, v in enumerate(values) if any(own_line(ln) for ln in re.split(r"\r?\n", v))), None)
    if at is None:
        if not values:
            return [line]
        at = 0
    out = list(values)
    out[at] = merged_comment(values[at], line)
    return out
