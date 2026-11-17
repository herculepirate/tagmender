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
_NOT_RIPPER = {
    "DMM", "RM", "LP", "EP", "MFSL", "MOFI", "UHQR", "QRP", "RTI", "HQ", "HQ180", "OBI", "MONO", "STEREO",
    "PROMO", "TEST", "PRESS", "PRESSING", "ORIGINAL", "FIRST", "REISSUE", "REMASTER", "REMASTERED", "VINYL",
    "RIP", "FLAC", "WAV", "DSD", "PCM", "HDTRACKS", "WEB", "CD", "SACD", "BOX", "SET", "DELUXE", "LIMITED",
    "EDITION", "JAPAN", "GERMANY", "HOLLAND", "CANADA", "FRANCE", "ITALY", "SPAIN", "EMI", "RCA", "CBS", "MCA",
    "ABC", "UA", "EMI", "ATCO", "EPIC", "ANTHEM", "MERCURY", "ATLANTIC", "COLUMBIA", "CAPITOL", "VERTIGO",
    "HARVEST", "ISLAND", "VIRGIN", "POLYDOR", "PHILIPS", "DECCA", "ASYLUM", "ELEKTRA", "REPRISE", "WARNER",
    "SIRE", "GEFFEN", "ARISTA", "CHRYSALIS", "MOTOWN", "STAX", "BLUE", "NOTE", "IMPULSE", "VERVE", "ECM",
    "PITMAN", "TERRE", "HAUTE", "SANTA", "MARIA", "JACKSONVILLE", "SPECIALTY", "ALLIED", "MASTERDISK",
    "STERLING", "JAMF", "RL", "HALF", "SPEED", "AAA", "ADD", "NEW", "USED", "NM", "VG", "EX",
}
_COUNTRY = {"US", "USA", "UK", "GB", "JP", "DE", "EU", "FR", "IT", "NL", "CA", "AU", "SK", "ES", "SE", "BR"}

_VINYL_RIP_WORDS = re.compile(r"\bvinyl\s*-?\s*rip\b|\bvinylrip\b|\bLP\s*rip\b|\bneedle\s*-?\s*drop\b", re.I)
_RES_IN_NAME = re.compile(r"\b(?:\d{2}\s*-?\s*bits?|\d{2,3}(?:[.,]\d)?\s*k?hz|\d{2}\s*[-/]\s*\d{2,3}(?:[.,]\d)?)\b", re.I)
# "rip by X", "vinyl rip by X", "ripped by X", "transfer by X", "needle drop by X" (one or two words)
_BY = re.compile(r"\b(?:vinyl\s+)?(?:rip(?:ped)?|transfer(?:red)?|needle\s*drop|digiti[sz]ed)\s+by\s*[:\-]?\s*"
                 r"([A-Za-z][\w.\-]{1,23}(?: [A-Z][\w.\-]{1,23})?)", re.I)
_POSTED = re.compile(r"^\W*(?:posted|uploaded)\s+by\s*:?\s*([A-Za-z][\w.\-]{1,23})\b", re.I | re.M)
_POSSESSIVE = re.compile(r"\b([A-Za-z][\w\-]{2,23})['’]s\s+(?:\d{2}\s*-?\s*bit|vinyl|rip|needle)", re.I)
_WORD_AFTER_BY = {"the", "a", "an", "me", "myself", "us", "my", "using", "with", "hand", "ear"}

_RIP_LINE = re.compile(r"\brip(?:s|ped|ping)?\b|\bneedle\s*-?\s*drop|\btransfer(?:red)?\b|\bdigiti[sz]ed\b", re.I)
_RIPPED_WHEN = re.compile(r"\b(?:ripped|recorded|digiti[sz]ed|transferred|rip\s+date|date\s+ripped)\b[^\n]{0,60}?"
                          r"\b((?:19[89]|20[0-9])\d)\b", re.I)


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
    name = name.strip(" .-_")
    return KNOWN_RIPPERS.get(name.casefold(), name)


def ripper_in_name(folder_name: str) -> str:
    """VIN-3: the ripper the folder name names: a known ripper's name anywhere in its brackets
    ("[Vinyl Rip PBTHAL 24bit 96khz ...]"), "rip by X", or a word in capitals of 4 to 12
    letters right after "Vinyl Rip" and before the resolution that is no label, country,
    format or pressing word. Never a catalogue number ("[Vinyl Rip 24bit 192khz Mercury
    Price19]": PRICE 19 is the pressing's), never a word after the resolution."""
    text = str(folder_name or "")
    for content in re.findall(r"[\(\[\{]([^\)\]\}]+)[\)\]\}]", text):
        for w in re.findall(r"[\w\-]+", content):
            if w.casefold() in KNOWN_RIPPERS:
                return KNOWN_RIPPERS[w.casefold()]
        m = _BY.search(content)
        if m and m.group(1).split()[0].casefold() not in _WORD_AFTER_BY:
            return _canon(m.group(1))
        m = _VINYL_RIP_WORDS.search(content)
        if not m:
            continue
        after = content[m.end():].split()
        if after and not _RES_IN_NAME.match(after[0]):
            w = after[0].strip(",;:")
            if re.fullmatch(r"[A-Z]{4,12}", w) and w not in _NOT_RIPPER and w not in _COUNTRY and \
                    len(after) > 1 and _RES_IN_NAME.match(after[1]):
                return w
    return ""


def ripper_in_notes(text: str) -> str:
    """VIN-3: the ripper the rip notes (or a comment tag holding them) sign: "Vinyl rip by
    Packgrog", "ripped by X"; else "Posted by pbthal" at a line's start; else a known
    ripper's "PBTHAL's 24bit 192khz raw audio". "" when none is clearly named."""
    text = str(text or "")
    for m in _BY.finditer(text):
        first = m.group(1).split()[0]
        if first.casefold() in _WORD_AFTER_BY:
            continue
        return _canon(m.group(1) if len(m.group(1).split()) == 1 or m.group(1).split()[1][:1].isupper()
                      else first)
    m = _POSTED.search(text)
    if m and m.group(1).casefold() not in _WORD_AFTER_BY:
        return _canon(m.group(1))
    for m in _POSSESSIVE.finditer(text):
        if m.group(1).casefold() in KNOWN_RIPPERS:
            return KNOWN_RIPPERS[m.group(1).casefold()]
    return ""


# ---------------------------------------------------------------------------- year, equipment
def rip_year(text: str) -> str:
    """The year the rip was made, when a line about the rip dates it ("Vinyl rip by X, recorded
    February 1, 2012"; "Ripped: 2019-03-02"): only on a line that speaks of ripping, so an
    album's own "Recorded at Le Studio, 1981" is never taken."""
    for line in str(text or "").splitlines():
        if not _RIP_LINE.search(line):
            continue
        m = _RIPPED_WHEN.search(line)
        if m:
            return m.group(1)
    return ""


_CUT = re.compile(r"\s+(?:with|on|w/|and|using|via|\+)\s+|\s*[(\[,;]|\s+-\s+", re.I)


def _short(item: str, limit: int = 32) -> str:
    item = re.sub("^[\\s\\-*•·>﻿�]+", "", item).strip()
    item = _CUT.split(item, maxsplit=1)[0].strip(" .:-")
    return item if 2 <= len(item) <= limit else ""


def equipment(text: str) -> str:
    """The turntable and cartridge the notes name, short ("VPI Scoutmaster, Zyx Airy 3X-SB"):
    "Turntable: X" / "Cartridge: X" lines, a turntable heading's first item, a line ending in
    "cartridge" ("Zyx Airy 3X-SB cartridge"; "Pioneer PL-200 with Denon DL-110 cartridge" names
    both). "" when the notes label neither."""
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
        if not table and re.match(r"^turntable\b[^:]*$", low) and len(line) < 40:
            nxt = next((x for x in lines[i + 1:i + 3] if x), "")
            if nxt.startswith(("-", "*", "•")) or nxt[:1].isupper():
                table = _short(nxt)
            continue
        if not cart and re.search(r"\bcartridge\s*$", low) and len(line) < 80:
            body = re.sub(r"\s*cartridge\s*$", "", line, flags=re.I)
            body = re.sub(r"^[\s\-*•]+", "", body)
            parts = re.split(r"\s+with\s+", body, maxsplit=1, flags=re.I)
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


def own_line(text: str) -> bool:
    return str(text or "").startswith(PREFIX)


def year_in_name(folder_name: str, pressing_year: str = "") -> str:
    """VIN-3: the year ripped a folder name gives: a year right before "Vinyl Rip" in a bracket
    ("[2022 Vinyl Rip 24bit 192khz Anthem R1-83736]", "[2019 DMM Vinyl Rip ...]") - unless it is
    the pressing's own year (a 2015 reissue's "[2015 Vinyl Rip ... B0022376-01]")."""
    for content in re.findall(r"[\(\[\{]([^\)\]\}]+)[\)\]\}]", str(folder_name or "")):
        m = re.search(r"\b((?:19[89]|20[0-9])\d)\b(?:\s+[A-Za-z]{1,8}){0,2}?\s+vinyl\s*-?\s*rip\b", content, re.I)
        if m and m.group(1) != str(pressing_year or "")[:4]:
            return m.group(1)
    return ""


def describe(folder, files, catno: str = "", notes: str | None = None, comments=(), pressing_year: str = "") -> dict:
    """What tells this rip apart: {"resolution": (bits, rate) or (), "ripper", "catno", "year",
    "equipment"} (each "" or () when not known). folder: the copy's album folder (its name and
    notes); comments: the files' comment tags (rip notes are often kept there; TagMender's own
    line is left out); pressing_year: the release's own year (a folder name's year that is the
    pressing's is no year ripped)."""
    folder = Path(str(folder)) if folder else None
    notes = notes_of(folder) if notes is None and folder else (notes or "")
    said = "\n".join(dict.fromkeys("\n".join(ln for ln in str(c).splitlines() if not own_line(ln))
                                   for c in comments or () if c))
    text = notes + "\n" + said
    who = ripper_in_name(folder.name) if folder else ""
    if not who:
        who = ripper_in_notes(text)
    named = year_in_name(folder.name, pressing_year) if folder else ""
    return {"resolution": resolution_of(files), "ripper": who, "catno": str(catno or "").strip(),
            "year": named or rip_year(text), "year_named": bool(named), "equipment": equipment(text)}


def name_tag(d: dict) -> str:
    """VIN-3: "PBTHAL SRM-1-1184" - the ripper and the catalogue number, those known (never the
    resolution)."""
    return " ".join(x for x in (d.get("ripper") or "", d.get("catno") or "") if x)


def comment_line(d: dict) -> str:
    """VIN-4: "Vinyl rip: PBTHAL · 24-bit/96 kHz · ripped 2012 · VPI Scoutmaster, Zyx Airy 3X-SB"
    ("" when nothing is known)."""
    parts = [d.get("ripper") or "", resolution_words(d.get("resolution")),
             f"ripped {d['year']}" if d.get("year") else "", d.get("equipment") or ""]
    parts = [p for p in parts if p]
    return PREFIX + SEP.join(parts) if parts else ""


def merged_comment(existing: str, line: str) -> str:
    """VIN-4: the comment with TagMender's own line (told by its prefix) replaced by LINE, or
    LINE added after the text already there - every other line kept as it is. Unchanged when
    LINE is there already (idempotent); "" lines are never added."""
    existing = str(existing or "")
    if not line:
        return existing
    lines = existing.splitlines()
    own = [i for i, ln in enumerate(lines) if own_line(ln)]
    if own:
        if len(own) == 1 and lines[own[0]] == line:
            return existing
        keep = [ln for i, ln in enumerate(lines) if i not in own[1:]]
        keep[own[0]] = line
        nl = "\r\n" if "\r\n" in existing else "\n"
        return nl.join(keep)
    if not existing.strip():
        return line
    nl = "\r\n" if "\r\n" in existing else "\n"
    return existing.rstrip("\r\n") + nl + line
