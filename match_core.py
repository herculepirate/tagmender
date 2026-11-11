"""match_core.py - pure scoring/classification logic for album matching.

No network, no file access, no third-party imports, so it can be tested
offline. Used by match_albums.py.

Data shapes
-----------
album (what the files say):
    {"artist": str, "title": str, "year": int|None, "discs": int,
     "tracks": [{"disc": int, "num": int, "title": str, "secs": float}],
     "barcodes": set[str], "catnos": set[str]}

candidate (what MusicBrainz / Discogs says):
    {"source": "mb"|"discogs", "id": str, "url": str, "artist": str,
     "title": str, "year": int|None, "country": str, "label": str,
     "catnos": set[str], "barcodes": set[str], "disc_count": int,
     "tracks": [{"disc": int, "title": str, "secs": float}]}   # secs 0 = unknown
"""
import difflib
import functools
import math
import re
import unicodedata
from collections import Counter

DEFAULT_CFG = {
    # --- point weights (confidence = earned / available * 100) -------------
    "w_artist": 20,
    "w_title": 25,
    "w_tracks": 15,       # exact track-count match
    "w_durations": 25,    # share of tracks whose lengths agree
    "w_discs": 5,
    "w_year": 5,
    "w_code": 25,         # barcode or catalogue number agrees
    "w_ocr": 10,          # OCR text corroboration (only when OCR ran)
    # --- duration tolerance: max(abs seconds, pct of candidate length) ------
    "duration_tol_s": 3.0,
    "duration_tol_pct": 0.015,
    "min_duration_coverage": 0.6,   # candidate must list lengths for this share
    # --- classification ----------------------------------------------------
    "auto_min_confidence": 90.0,
    "auto_min_margin": 8.0,         # best must beat runner-up (same source)
    # Off by default: the folder year follows your naming script
    # (originaldate when present), while a release's year is that PRESSING's,
    # so a year match cannot tell pressings apart. Set a number (e.g. 3.0) to
    # let "the only pressing with the files' year" win on a smaller margin.
    "auto_min_margin_year_decided": None,
    # A remaster/reissue year in the folder name ("[2021 RM]") is when it was
    # remastered; the release may be dated a little later or earlier.
    "pressing_year_tolerance": 2,
    # Last tie-breaker between pressings nothing else tells apart, like Picard's
    # "preferred release countries" (Picard's own list is used when it is set).
    "preferred_countries": ["CA", "US", "XW", "XE", "GB", "DE"],
    # After the country order, and only between pressings it leaves undecided: Picard's
    # Preferred Releases types and medium formats (tagger_settings.matching_preferences reads
    # them from Picard.ini). Empty = Picard's default, no preference. Never a score.
    "preferred_release_types": [],
    "discouraged_release_types": [],
    "preferred_release_formats": [],
    "auto_min_artist": 0.85,
    "auto_min_title": 0.85,
    "auto_min_duration_frac": 0.80,
    "auto_min_duration_frac_with_code": 0.55,   # when a catalogue number/barcode agrees
    # A title that only partly matches (one title contained in the other,
    # e.g. "Esper Edition" vs "Esper Definitive Collectors Edition") is weaker
    # evidence, so the track lengths must agree almost completely.
    "exact_title_sim": 0.95,
    "auto_min_duration_frac_partial_title": 0.95,
    "review_min_confidence": 60.0,
    "near_miss_min_sim": 0.85,      # artist+title this good => review even if
                                    # the track count differs
    # --- lookups ------------------------------------------------------------
    "mb_detail_fetch": 4,
    "discogs_detail_fetch": 5,
    "discogs_delay": 1.1,
    "mb_delay": 1.1,
}

_EDITION_RE = re.compile(
    r"[\(\[\{][^\)\]\}]*(remaster|deluxe|edition|reissue|expanded|bonus|anniversary|"
    r"mono|stereo|digipak|limited|version|re-?release|special)[^\)\]\}]*[\)\]\}]",
    re.I)
_TRAIL_EDITION_RE = re.compile(r"\s+-?\s*(remaster(ed)?(\s+\d{4})?|deluxe edition)$", re.I)
_JOIN_WORDS = {"and", "feat", "featuring", "ft", "with", "vs", "x"}
_OCR_STOPWORDS = {"the", "and", "for", "with", "from", "music", "records", "recordings",
                  "all", "rights", "reserved", "made", "produced", "recorded", "disc"}


# Letters that Unicode does not split into letter + accent, spelled the way people type them
# ("ae3o & h3ae" for "æ³o & h³æ", "Lodz" for "Łódź").
_PLAIN_LETTERS = str.maketrans({"æ": "ae", "Æ": "AE", "œ": "oe", "Œ": "OE", "ø": "o", "Ø": "O",
                                "ß": "ss", "ð": "d", "Ð": "D", "đ": "d", "Đ": "D", "þ": "th",
                                "Þ": "TH", "ł": "l", "Ł": "L", "ı": "i", "ħ": "h", "ŧ": "t"})


# Cyrillic and Greek written in Latin letters, so "Фоно" matches "Fono" and
# "Κατά τον δαίμονα" matches a Latin-script listing.
_CYRILLIC = dict(zip("абвгдезийклмнопрстуфыэ", "abvgdeziyklmnoprstufye"))
_CYRILLIC.update({"ё": "e", "ж": "zh", "х": "kh", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "shch", "ъ": "",
                  "ь": "", "ю": "yu", "я": "ya", "і": "i", "ї": "yi", "є": "ye", "ґ": "g", "ў": "u"})
_GREEK = dict(zip("αβγδεζηικλμνξοπρστυφωχς", "abgdeziiklmnxoprstyfoxs"))
_GREEK.update({"θ": "th", "ψ": "ps", "χ": "ch"})
_SCRIPTS = str.maketrans({**_CYRILLIC, **{k.upper(): v.capitalize() for k, v in _CYRILLIC.items()},
                          **_GREEK, **{k.upper(): v.capitalize() for k, v in _GREEK.items()}})
_CJK = re.compile(r"[\u3040-\u30ff\u3400-\u9fff\uac00-\ud7af]")


@functools.lru_cache(maxsize=64)
def _romanize_cjk(text):
    """Japanese/Chinese in Latin letters when pykakasi is installed (else unchanged).
    Kept for the last texts: the scans' OCR text is folded again for every candidate
    scored, and romanising it takes seconds (eng+rus+jpn readings of a booklet)."""
    try:
        import pykakasi
    except ImportError:
        return text
    global _KAKASI
    try:
        _KAKASI
    except NameError:
        _KAKASI = pykakasi.kakasi()
    return " ".join(part["hepburn"] for part in _KAKASI.convert(text))


def to_latin(text):
    """Text in Latin letters: Cyrillic and Greek transliterated, Japanese/Chinese
    romanised when pykakasi is available."""
    t = str(text or "")
    if _CJK.search(t):
        t = _romanize_cjk(t)
    return t.translate(_SCRIPTS)


def fold(text):
    """Case, accent, punctuation, spacing and script insensitive form."""
    t = str(text or "")
    if _CJK.search(t):
        t = _romanize_cjk(t)
    t = unicodedata.normalize("NFKD", t)
    t = "".join(c for c in t if not unicodedata.combining(c)).translate(_SCRIPTS).translate(_PLAIN_LETTERS)
    t = t.casefold().replace("&", " and ")
    t = re.sub(r"[^\w\s]", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    if t.startswith("the "):
        t = t[4:]
    return t


# Rip/format tags people add to folder names: [24bit 96khz], [FLAC], [4CD], (DSD64)...
_TECH_WORDS = (
    r"\d+\s*-?\s*bit|\d+(\.\d+)?\s*k?hz|\d+\s*-\s*\d+|\d+\s*kbps|"
    r"flac|\bape\b|alac|aiff?|\bwav\b|\bwv\b|mp3|\bv[0-9]\b|opus|\bogg\b|"
    r"dsd\d*|sacd\S*|hdcd|\bmqa\b|hi-?res|lossless|"
    r"\bweb\b|digital|\brip\b|\brips\b|ripped|"
    r"vinyl|\blps?\b|\d+\s*lps?\b|cassette|\btape\b|"
    r"\bcds?\b|\bcdr\b|\d+\s*cds?\b|\bdvd\S*|blu-?ray|box\s*set|boxset|"
    r"\bcd\s*\d+|\bdis[ck]s?\b(\s*(\d+|one|two|three|four|five|i{1,3}|iv|v))?"
)
_TECH_RE = re.compile(r"[\(\[\{][^\)\]\}]*(" + _TECH_WORDS + r")[^\)\]\}]*[\)\]\}]", re.I)
_TRAIL_BRACKETS_RE = re.compile(r"(\s*[\(\[\{][^\)\]\}]*[\)\]\}])+\s*$")


def strip_edition(title):
    t = _TECH_RE.sub(" ", str(title or ""))
    t = _EDITION_RE.sub(" ", t)
    t = _TRAIL_EDITION_RE.sub("", t)
    return re.sub(r"\s+", " ", t).strip()


def drop_trailing_brackets(title):
    """'Dome Of The Rock [Act 144]' -> 'Dome Of The Rock'. Never empties a title."""
    t = _TRAIL_BRACKETS_RE.sub("", strip_edition(title)).strip()
    return t or strip_edition(title)


# "Garbage EP", "Peel Session [EP]", "Blue Monday (Single)": the databases usually list the title
# without the release type, which is a separate field there.
_RELEASE_TYPE_RE = re.compile(
    r"\s*[\(\[\{]?\s*\b(?:e\.?\s?p\.?|l\.?\s?p\.?|single|maxi[- ]?single|mini[- ]?album)\s*[\)\]\}]?\s*$", re.I)


def drop_release_type(title):
    """'Garbage EP' -> 'Garbage'. Never empties a title or leaves a bare article ('The EP')."""
    t = str(title or "")
    out = _RELEASE_TYPE_RE.sub("", t).strip(" -:")
    if not out or fold(out) in {"the", "a", "an"}:
        return t.strip()
    return out


def search_title(title):
    """What to put in a database query: no edition, rip-format or trailing
    bracket noise, which no catalogue entry would contain."""
    return drop_trailing_brackets(title)


# --- catalogue numbers written into folder names / album titles ------------
# e.g. "Kitaro [20bit RM DOMO 71063-2]", "[2CD ADOR 2357]", "[Warpcd92]".
_BRACKETS_RE = re.compile(r"[\(\[\{]([^\)\]\}]+)[\)\]\}]")
# words that describe the rip/edition, never part of a catalogue number
_NOT_CATNO_WORD = re.compile(
    r"^(\d+\s*-?\s*bit|\d+(\.\d+)?k?hz|\d+x\d+g?|\d+g|rm|remaster(ed)?|reissue|edition|deluxe|"
    r"expanded|limited|bonus|track|tracks|with|and|anniversary|version|complete|album|included|"
    r"\d*cds?|\d*lps?|vinyl|rip|ripped|web|digital|flac|mp3|sacd\S*|dvda?\S*|dts|\d*ch|"
    r"surround|stereo|mono|boxset|box|set|hi-?res|lossless|\+|"
    # a bit depth and sample rate ("ECM 24-96", "[24/192]", "16-44.1"): the audio, not a number
    r"(?:16|20|24|32)\s*[-/]\s*(?:44(?:\.1)?|48|88(?:\.2)?|96|176(?:\.4)?|192|352(?:\.8)?|384))$", re.I)
# words that, followed by a number, mean something else ("Volume 5", "Live 1996")
_NUMBER_WORDS = {"vol", "volume", "part", "pt", "no", "nr", "op", "opus", "chapter", "book",
                 "side", "live", "by", "disc", "disk", "cd", "track", "edition", "session",
                 "sessions", "episode", "act", "january", "february", "march", "april", "may",
                 "june", "july", "august", "september", "october", "november", "december",
                 "summer", "winter", "spring", "autumn", "fall", "festival", "the", "of"}
_CATNO_ONE_TOKEN = re.compile(r"^[A-Za-z]{1,10}-?\d{2,7}(?:[-/]\d{1,3})?(?:CD|LP|DG|DVD|SACD)?$",
                              re.I)                                         # Warpcd92, BRC-67, DiN34, FO40CD
_CATNO_NUMBER = re.compile(r"^\d{2,7}(?:[-/]\d{1,3})?$")                         # 71063-2, 058, 2357
_YEAR = re.compile(r"^(19|20)\d\d$")


def catno_hints(text, variants=False):
    """Catalogue numbers written in square or round brackets of a folder name
    or album title. Returns them as written ("DOMO 71063-2"). Conservative:
    years, volume/part numbers and rip descriptions are not catalogue numbers.

    variants=True also returns forms with the preceding label word attached
    ("Hubro CD2544" for "HUBROCD2544"), used for matching only, not searching."""
    found, extra = [], []
    for content in _BRACKETS_RE.findall(str(text or "")):
        for segment in content.split(","):
            words = [w.strip(".:") for w in segment.replace(";", " ").split()]
            words = [w for w in words if w and not _NOT_CATNO_WORD.match(w)]
            skip = set()
            for i, w in enumerate(words):
                if i in skip:
                    continue
                nxt = words[i + 1] if i + 1 < len(words) else ""
                prev = words[i - 1] if i > 0 else ""
                # "479 7050": a number in two groups (Deutsche Grammophon style)
                if re.fullmatch(r"\d{2,4}", w) and re.fullmatch(r"\d{3,5}", nxt) \
                        and not _YEAR.match(w) and len(w + nxt) >= 6:
                    found.append(f"{w} {nxt}")
                    skip.add(i + 1)
                elif re.fullmatch(r"\d{7,13}(?:-\d{1,2})?", w) and not _YEAR.match(w):
                    found.append(w)                    # "8122796510-3": a number-only catalogue number
                elif _CATNO_ONE_TOKEN.match(w) and re.search(r"[A-Za-z]", w):
                    found.append(w)
                    if re.fullmatch(r"[A-Za-z]{2,12}", prev) and prev.casefold() not in _NUMBER_WORDS:
                        extra.append(f"{prev} {w}")
                elif (prev and _CATNO_NUMBER.match(w) and not _YEAR.match(w)
                      and re.fullmatch(r"[A-Za-z][A-Za-z.]{0,11}", prev)
                      and prev.strip(".").casefold() not in _NUMBER_WORDS
                      and not re.fullmatch(r"\d{3,5}", nxt)):
                    found.append(f"{prev} {w}")
    out = []
    for f in found + (extra if variants else []):
        if f not in out:
            out.append(f)
    return out


# "[20bit RM Domo 1997]", "[2004 RM]", "[2002 remastered edition]", "(2013 Esoteric remaster)":
# the folder says which pressing this is.
_PRESSING_YEAR_RE = re.compile(
    r"(?:\b(19[5-9]\d|20\d\d)\b[^\]\)]*\b(?:rm|remaster(?:ed)?|reissue|re-?release|edition|domo)\b)"
    r"|(?:\b(?:rm|remaster(?:ed)?|reissue|re-?release|domo)\b[^\]\)]*?\b(19[5-9]\d|20\d\d)\b)", re.I)


def pressing_year(text):
    """The remaster/reissue year written in a bracket of the folder name, or None."""
    for content in _BRACKETS_RE.findall(str(text or "")):
        m = _PRESSING_YEAR_RE.search(content)
        if m:
            return int(m.group(1) or m.group(2))
        # "(2013, Warner, 8122796510-3)": a year listed with a label and catalogue number
        first = content.split(",")[0].strip()
        if _YEAR.fullmatch(first) and "," in content and catno_hints(f"({content})"):
            return int(first)
    return None


def _ratio(a, b):
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    sorted_a = " ".join(sorted(a.split()))
    sorted_b = " ".join(sorted(b.split()))
    return max(difflib.SequenceMatcher(None, a, b).ratio(),
               difflib.SequenceMatcher(None, sorted_a, sorted_b).ratio())


def title_sim(tag_title, cand_title):
    raw = _ratio(fold(tag_title), fold(cand_title))
    stripped = _ratio(fold(strip_edition(tag_title)), fold(strip_edition(cand_title)))
    bare = _ratio(fold(drop_trailing_brackets(tag_title)), fold(drop_trailing_brackets(cand_title)))
    typed = _ratio(fold(drop_release_type(drop_trailing_brackets(tag_title))),
                   fold(drop_release_type(drop_trailing_brackets(cand_title))))
    best = max(raw, stripped, bare, typed)
    # Every word of one title appears in the other ("Metamorphosis & Acoustic
    # Counterpoint" inside "Philip Glass Metamorphosis / Steve Reich Acoustic
    # Counterpoint"): the same work with extra credit text. Capped below a
    # perfect match so an exact title still ranks higher.
    a = set(fold(strip_edition(tag_title)).split()) - {"and"}
    b = set(fold(strip_edition(cand_title)).split()) - {"and"}
    if len(a) >= 2 and len(b) >= 2 and (a <= b or b <= a):
        best = max(best, 0.9)
    return best


def artist_parts(artist):
    """'A & B', 'A; B', 'A / B', 'A and B', 'A, B' -> ['A', 'B'].
    Database artist fields hold collaborators separately, so a phrase search
    for the joined credit usually finds nothing; search one name instead."""
    # (" x " only lowercase and between two names: "A x B" is a collaboration, "Brand X" a band
    # - its "X" once made every search ask for "Brand")
    parts = re.split(r"\s*(?:;|/|,|&|\band\b|\bwith\b|\bfeat\.?|\bfeaturing\b|\bvs\.?\b)\s*|(?<=\S)\s+(?-i:x)\s+(?=\S)",
                     str(artist or ""), flags=re.I)
    parts = [p.strip() for p in parts if p and p.strip()]
    return parts or [str(artist or "").strip()]


def _artist_tokens(text):
    words = fold(re.sub(r"\s*;\s*", " and ", str(text or ""))).split()
    kept = [w for w in words if w not in _JOIN_WORDS]
    # An artist whose whole name is a joining word ("X", "Ft") keeps it.
    return kept or words


def artist_sim(tag_artist, cand_artist):
    """Order-insensitive; ignores joiners ("&", "and", "feat."), so
    'Max Corbacho; Bruno Sanfilippo' ~ 'Bruno Sanfilippo & Max Corbacho'."""
    a, b = _artist_tokens(tag_artist), _artist_tokens(cand_artist)
    if not a or not b:
        return 0.0
    full = _ratio(" ".join(sorted(a)), " ".join(sorted(b)))
    # A candidate crediting extra featured artists still matches the lead.
    lead = _ratio(" ".join(a), " ".join(b[:len(a)])) if len(b) > len(a) else 0.0
    # Every name in the tag appears in the candidate's credit (composers or
    # guests listed too): "Vanessa Wagner & Wilhem Latchoumia" inside
    # "Adams, Bernstein, Glass, Monk; Vanessa Wagner, Wilhem Latchoumia".
    # Only in that direction: a database credit that is PART of the tag's credit has lost a
    # collaborator ("Blaine L. Reininger" for "Steven Brown & Blaine L. Reininger").
    contained = 0.9 if len(a) >= 2 and set(a) <= set(b) else 0.0
    best = max(full, lead * 0.95, contained)
    if missing_collaborators(tag_artist, [cand_artist]):
        best = min(best, MISSING_COLLABORATOR_SIM)
    return best


MISSING_COLLABORATOR_SIM = 0.8      # related (so it is listed for review), never close enough for auto


def missing_collaborators(tag_artist, cand_credits):
    """Names in the tag's credit that none of the candidate's credits contain. Empty when the tag
    names one artist, or when the candidate credits nobody from it at all (a different artist,
    judged by the similarity itself)."""
    parts = artist_parts(tag_artist)
    if len(parts) < 2:
        return []
    best = None
    for credit in cand_credits:
        if not credit:
            continue
        whole = fold(credit)
        names = [fold(n) for n in artist_parts(credit)]
        missing = [p for p in parts if fold(p) not in whole
                   and max((_ratio(fold(p), n) for n in names), default=0.0) < 0.85]
        if len(missing) == len(parts):
            continue                                   # nothing in common: not this rule's business
        if best is None or len(missing) < len(best):
            best = missing
    return best or []


def split_codes(value):
    """'MASO CD 90041;12957.0041.2' -> both numbers (tags such as JRiver's LABELNO hold several)."""
    return [c.strip() for c in re.split(r"\s*;\s*|\s+\|\s+", str(value or "")) if c.strip()]


def upce_to_upca(code):
    """The 12-digit UPC-A a UPC-E code (8 digits: number system, six digits, check) is
    short for; "" when it is not one. The check digit is the UPC-A one."""
    code = str(code or "")
    if not (code.isdigit() and len(code) == 8 and code[0] in "01"):
        return ""
    ns, d, check = code[0], code[1:7], code[7]
    last = d[5]
    if last in "012":
        body = d[0:2] + last + "0000" + d[2:5]
    elif last == "3":
        body = d[0:3] + "00000" + d[3:5]
    elif last == "4":
        body = d[0:4] + "00000" + d[4]
    else:
        body = d[0:5] + "0000" + last
    return ns + body + check


def valid_barcode(code, symbology=None):
    """The check digit, so a phone number or a run of noise is not a barcode.
    EAN-13 and UPC-A always; an 8-digit code only when the scanner says what it is:
    "EAN8" (its own rule) or "UPCE" (checked as the UPC-A it is short for)."""
    code = str(code or "")
    if not code.isdigit():
        return False
    if len(code) == 8 and symbology == "UPCE":
        return bool(upce_to_upca(code)) and valid_barcode(upce_to_upca(code))
    if len(code) not in (12, 13) and not (len(code) == 8 and symbology == "EAN8"):
        return False
    digits = [int(c) for c in code]
    body, check = digits[:-1], digits[-1]
    total = sum(d * (3 if i % 2 == (len(body) - 1) % 2 else 1) for i, d in enumerate(body))
    return (10 - total % 10) % 10 == check


# Words that come before a number on sleeves without it being a catalogue number:
# label codes, rights societies, work catalogues, track/disc/side numbering, phone numbers.
_OCR_NOT_CATNO = {"lc", "cd", "lp", "disc", "disk", "side", "track", "tracks", "vol", "volume", "part",
                  "no", "nr", "op", "opus", "bwv", "kv", "k", "rv", "hwv", "hob", "d", "isbn", "isrc",
                  "tel", "fax", "phone", "ddd", "add", "aad", "stereo", "mono", "mcps", "biem", "gema",
                  "siae", "sacem", "sabam", "sgae", "stemra", "jasrac", "prs", "po", "box", "ch",
                  "uk", "usa", "us", "eu", "made", "in", "p", "c", "the", "and", "of", "at", "by",
                  "page", "pages", "issue", "edition", "remaster", "remastered", "recorded", "year",
                  "dvd", "bonus", "mix", "version", "take", "bar", "bars", "min", "mins", "sec",
                  "copy", "time", "for", "suite", "route", "room", "postfach", "postbus", "cedex", "pmb", "box",
                  "dr", "rms", "khz", "bit", "hz", "db", "cdda"}     # dynamic-range and audio-check reports in rip notes
# US state + ZIP code ("CA 90064", "NY 11576") on label addresses
_US_STATES = set("al ak az ar ca co ct de fl ga hi id il in ia ks ky la me md ma mi mn ms mo mt ne nv "
                 "nh nj nm ny nc nd oh ok or pa ri sc sd tn tx ut vt va wa wv wi wy dc".split())
_OCR_CATNO_RE = re.compile(
    r"(?<![A-Za-z0-9])([A-Z][A-Za-z]{1,9}(?:[ -]?(?:CD|LP|DVD|SACD|MC|CDR|DA))?[ -]?\d{2,7}(?:[-/]\d{1,3})?"
    r"(?:(?:CD|LP|DVD|SACD|MC|CDR|DG)[A-Z]{0,2})?)"    # "FO532CD", "QDISS0201CDD": the format after the number
    r"(?![A-Za-z0-9])")
# EMI's spaced form: "CDP 7 91186 2", "CDP 566-7 91186 2" (letters, a digit, five digits, a check digit)
_OCR_SPACED_CATNO_RE = re.compile(r"(?<![A-Za-z0-9])([A-Z]{2,5} (?:\d{3}[- ])?\d[ -]?\d{5}[ -]\d)(?![A-Za-z0-9])")
_OCR_BARCODE_RE = re.compile(r"(?<!\d)(\d(?:[ °'’·.]?\d){10,12})(?!\d)")


def codes_from_ocr_text(text, limit=5):
    """Barcodes and catalogue numbers printed on scans, for searching.
    Barcodes must pass the check digit. Catalogue numbers need a capitalised label
    word (or letters run into the number: "CHARRMCD03") and a number that is not a
    year; label codes ("LC 01234"), work numbers ("BWV 1001"), track, disc and
    phone numbers are skipped. At most `limit` of each, in the order printed."""
    out = {"barcodes": [], "catnos": []}
    text = str(text or "")
    for m in _OCR_BARCODE_RE.finditer(text):
        code = re.sub(r"\D", "", m.group(1))      # "8°715392°18 2327" as OCR reads a barcode's digits
        if valid_barcode(code) and code not in out["barcodes"]:
            out["barcodes"].append(code)
    seen = set()
    for m in _OCR_SPACED_CATNO_RE.finditer(text):
        key = norm_code(m.group(1))
        if key not in seen and m.group(1).split()[0].casefold() not in _OCR_NOT_CATNO:
            seen.add(key)
            out["catnos"].append(m.group(1))
    for m in _OCR_CATNO_RE.finditer(text):
        cand = m.group(1).strip()
        word = re.match(r"[A-Za-z]+", cand).group(0)
        number = re.findall(r"\d[\d/-]*", cand)[-1]
        if word.casefold() in _OCR_NOT_CATNO:
            continue
        if not (word.isupper() or re.search(r"[A-Za-z]\d", cand)):
            continue                                   # "Track 12", "Made 2003": ordinary words
        if _YEAR.match(number) and not re.search(r"[A-Za-z]\d", cand):
            continue                                   # "Berlin 1990"
        if word.casefold() in _US_STATES and re.fullmatch(r"\d{5}", number):
            continue                                   # an address: "CA 90064"
        spaced = not re.search(r"[A-Za-z]\d", cand)
        if (spaced and len(word) >= 5 and len(re.sub(r"\D", "", number)) <= 3
                and not re.search(r"cd|lp", word, re.I)):
            continue                                   # a word and a small number: "RETURNING 43"
        cand = (_o_for_zero(cand) or [cand])[0]
        key = norm_code(cand)
        if key not in seen:
            seen.add(key)
            out["catnos"].append(cand)
    for v in _misread_variants(text) + _o_for_zero(text):
        key = norm_code(v)
        if key not in seen:
            seen.add(key)
            out["catnos"].append(v)
    out["barcodes"] = out["barcodes"][:limit]
    out["catnos"] = out["catnos"][:limit]
    return out


def norm_code(value):
    """Catalogue number for comparison: no spaces, dashes or dots, any case, and no
    leading zeros inside numbers ("CHARRMCD03" is "CHARRMCD3" on Discogs)."""
    text = re.sub(r"[\s\-.]", "", str(value or "")).casefold()
    return re.sub(r"(?<!\d)0+(?=\d)", "", text)


_EUROPE = {"DE", "GB", "FR", "NL", "BE", "AT", "CH", "IT", "ES", "PT", "SE", "NO", "DK", "FI", "PL", "CZ",
           "GR", "IE", "HU", "LU", "XE"}


# a download's codecs as Discogs names them among a "File" format's descriptions
LOSSLESS_CODECS = {"flac", "alac", "wav", "aiff", "ape", "wavpack", "dsd", "dff", "dsf", "mqa"}
LOSSY_CODECS = {"mp3", "aac", "ogg vorbis", "ogg-vorbis", "opus", "wma", "m4a"}
LOSSY_ONLY_AGAINST = "your files are lossless, this download is "


_FMT_WORD = r"(?:\d?\s?CDs?|DVD|LP|BD|Blu-?ray|Box|Digi\w*|Ltd)"
_FORMATS_AFTER = re.compile(rf"\s+{_FMT_WORD}(?:\s*[+&/]\s*{_FMT_WORD})*\s*$", re.I)


def catno_same(a, b):
    """Two catalogue numbers for one product, however written: equal; with a format
    word added or inside ("SPV 91978" / "SPV 91978 Box", "MASS 1389" / "MASS CD 1389 DG",
    "CG-0004" / "DIGICD CG-0004"); with the label's letters in front ("CD0540" /
    "MAS CD0540", "079" / "GUN 079"); with a trailing disc digit ("RR 9117" / "RR 9117 2");
    or a long number with and without its check digit ("99833708" / "9983370")."""
    # formats written after the number: "SPV 69848 CD+DVD" is SPV 69848 (Kreator - Enemy
    # Of God Revisited); only after a space, so "FO40CD" keeps its CD
    a, b = (_FORMATS_AFTER.sub("", str(x or "")) or str(x or "") for x in (a, b))
    # a box set's number with the disc after it: "8122796510-1" is disc 1 of 8122796510
    for x, y in ((a, b), (b, a)):
        m = re.fullmatch(r"\s*(\d{7,})\s*-\s*\d{1,2}\s*", x)
        if m and norm_code(y) == m.group(1):
            return True
    a, b = norm_code(a), norm_code(b)
    if not a or not b:
        return False
    if a == b:
        return True
    fmt = r"(?:digicd|digi|dcd|\dcd|cd|lp|dg|box|dvd|mc|ltd|jewel)"
    sa, sb = re.sub(fmt, "", a), re.sub(fmt, "", b)
    if sa and sa == sb and len(re.sub(r"\D", "", sa)) >= 3:
        return True
    short, long_ = sorted((a, b), key=len)
    digits = len(re.sub(r"\D", "", short))
    if digits >= 2 and long_.endswith(short) and re.fullmatch(r"[a-z]{1,5}" + fmt + "?", long_[:-len(short)]):
        return True                                  # the label's letters in front
    if (len(short) >= 3 and re.search(r"[a-z]", short) and digits
            and long_.startswith(short) and re.fullmatch(fmt + r"(?:\+" + fmt + r")*|[1-9]", long_[len(short):])):
        return True                                  # format words ("cd+dvd") or a disc digit after
    if long_.endswith(short) and len(short) >= 3 and re.search(r"[a-z]", short) and digits \
            and re.fullmatch(fmt, long_[:-len(short)]):
        return True
    if a.isdigit() and b.isdigit() and len(short) >= 7 and long_[:-1] == short:
        return True
    return False


def catnos_agree(mine, theirs):
    return any(catno_same(x, y) or catno_range_same(x, y) for x in mine or () for y in theirs or ())


# A set's numbers written as a range: "SICP 10151~4", "SICP-10151~10154" (a Japanese box:
# SICP-10151, -10152, -10153, -10154). The end is the first number with its last digits
# replaced and must come after it. "~" always means a range; a "-" ("SICP 10151-4") is as
# often a format or disc suffix ("COL 474161-2", "DOMO 71063-2", "ABC 1001-2"), so it names a
# range only in a Japanese-style number (four letters, four or more digits) and is then only
# ever the same as that range written with "~" (catno_range_same), never one of its numbers.
_CATNO_RANGE = re.compile(r"^\s*([A-Za-z]{2,6})[\s-]?(\d{3,7})\s*([~\-])\s*(\d{1,7})\s*$")


def catno_range(code, dash=True):
    """The catalogue numbers a range form names, normalised (norm_code), in order, or []
    when it is not one: "SICP 10151~4" -> [sicp10151, sicp10152, sicp10153, sicp10154].
    A "~" form names up to 20; a "-" form (only with dash, and only Japanese-style) 2 to 9."""
    m = _CATNO_RANGE.match(str(code or ""))
    if not m:
        return []
    prefix, first, sep, end = m.groups()
    if sep == "-" and not (dash and len(prefix) == 4 and len(first) >= 4):
        return []
    if len(end) > len(first):
        return []
    last = first[:len(first) - len(end)] + end
    a, b = int(first), int(last)
    n = b - a + 1
    if n < 2 or n > (20 if sep == "~" else 9):
        return []
    width = len(first)
    return [norm_code(f"{prefix}{str(x).zfill(width)}") for x in range(a, b + 1)]


def catno_range_same(a, b):
    """Two catalogue numbers of one set when either is a "~" range (catno_range): "SICP
    10151~4" and "SICP-10153" (one of its discs), or "SICP 10151-4" (a folder) and "SICP
    10151~4" (MusicBrainz) - a "-" form only when the other is that very range with "~"."""
    ta, tb = catno_range(a, dash=False), catno_range(b, dash=False)
    if ta and tb:
        return ta == tb
    if not (ta or tb):
        return False
    tilde, other = (ta, b) if ta else (tb, a)
    dashed = catno_range(other)
    if dashed:                          # ("SICP 10151-4": the whole range, never one disc of it)
        return dashed == tilde
    return norm_code(other) in tilde


def catno_range_forms(code):
    """A Japanese-style range written with "-" ("SICP 10151-4"), as MusicBrainz and Discogs
    list it: ["SICP 10151~4"]. [] for any other number."""
    m = _CATNO_RANGE.match(str(code or ""))
    if not m or m.group(3) != "-" or not catno_range(code):
        return []
    prefix, first, _sep, end = m.groups()
    return [f"{prefix} {first}~{end}"]


# ---- versions of one disc in folders of their own ("2.0/", "5.1/", "Stereo 24-96/") ------
# A folder named after a channel layout holds one version of the album's audio (an SACD's
# stereo and multichannel areas, a DVD-Audio's or Blu-ray's 2.0, 5.1 and Atmos mixes), not an
# album of its own. Its name may carry the codec, the disc and the rate too ("DTS 5.1", "LPCM
# 2.0 24-96", "BD 96-24 Stereo", "DVD-A 5.1"); any other word makes it a name, not a layout.
_LAYOUT_NUMBER = re.compile(r"(?<![\d.])([1-9])\.([0-2])(?:\.([0-9]))?(?![\d.])")
_LAYOUT_WORDS = (("atmos", "atmos", "Atmos"), ("dolby atmos", "atmos", "Atmos"),
                 ("immersive", "atmos", "Atmos"), ("auro 3d", "atmos", "Auro-3D"),
                 ("stereo", "stereo", "Stereo"), ("mono", "mono", "Mono"),
                 ("quadraphonic", "multichannel", "Quad"), ("quad", "multichannel", "Quad"),
                 ("surround", "multichannel", "Surround"), ("multichannel", "multichannel", "Multichannel"),
                 ("multi channel", "multichannel", "Multichannel"), ("multich", "multichannel", "Multichannel"),
                 ("multi ch", "multichannel", "Multichannel"))
# what may stand beside the layout: the disc, the codec, the rate and depth, "mix"
_DISC_WORDS = {"dvd": "video", "dvda": "video", "dvd a": "video", "dvd audio": "video", "dvd video": "video",
               "dvdv": "video", "bd": "video", "bdmv": "video", "blu ray": "video", "bluray": "video",
               "bd audio": "video", "pure audio": "video", "hd dvd": "video", "hddvd": "video",
               "mlp": "video", "dts": "video", "dts hd": "video", "dts hd ma": "video", "truehd": "video",
               "true hd": "video", "dolby": "", "dd": "video", "ac3": "video", "lpcm": "", "pcm": "",
               "sacd": "sacd", "dsd": "sacd", "dsf": "sacd", "dff": "sacd", "flac": "", "wav": "",
               "hires": "", "hi res": "", "mix": "", "area": "", "layer": "", "version": "", "audio": "",
               "ma": "", "hd": ""}
_RATE_WORD = re.compile(r"^(?:\d{2,3}(?:k|khz|bit)?|\d{2,3}[-/x]\d{2,3}(?:k|khz)?|\d{2,3}(?:\.\d)?k(?:hz)?)$")


def parse_version(name):
    """(layout, label, disc kind) a version folder's name gives, or None when it is not
    one: "5.1" -> ("multichannel", "5.1", ""), "LPCM 2.0 24-96" -> ("stereo", "Stereo", ""),
    "BD 96-24 Stereo" -> ("stereo", "Stereo", "video"), "Dolby Atmos" -> ("atmos", "Atmos",
    "video"), "DSD Stereo" -> ("stereo", "Stereo", "sacd"). layout: "mono", "stereo",
    "multichannel" (quad, 5.1, 7.1, surround) or "atmos" (immersive). disc kind: "sacd",
    "video" (DVD-Audio/-Video, Blu-ray: their codecs too) or "" when the name does not say.
    "24-96", "DTS" or "Blu-ray" alone name no layout."""
    text = re.sub(r"[\[\](){}]", " ", str(name or "")).casefold()
    found = []
    for m in _LAYOUT_NUMBER.finditer(text):
        n, lfe, top = int(m.group(1)), int(m.group(2)), m.group(3)
        label = m.group(0)
        if top is not None and int(top) > 0:
            found.append(("atmos", label))
        elif n + lfe == 1:
            found.append(("mono", "Mono"))
        elif n == 2 and lfe == 0:
            found.append(("stereo", "Stereo"))
        else:
            found.append(("multichannel", label))
    text = _LAYOUT_NUMBER.sub(" ", text)
    for m in re.finditer(r"(?<![a-z\d])([1-8])\s*-?\s*ch(?:annels?)?(?![a-z])", text):
        n = int(m.group(1))
        found.append(("mono", "Mono") if n == 1 else ("stereo", "Stereo") if n == 2 else ("multichannel", f"{n}ch"))
    text = re.sub(r"(?<![a-z\d])[1-8]\s*-?\s*ch(?:annels?)?(?![a-z])", " ", text)
    words = " " + re.sub(r"[\s_.\-/+,&]+", " ", text).strip() + " "
    for w, lay, label in sorted(_LAYOUT_WORDS, key=lambda x: -len(x[0])):
        if f" {w} " in words:
            found.append((lay, label))
            words = words.replace(f" {w} ", " ")
    kind = ""
    if "atmos" in {f[0] for f in found} and "dolby" in words:
        words = words.replace(" dolby ", " ")
    for w, k in sorted(_DISC_WORDS.items(), key=lambda x: -len(x[0])):
        while f" {w} " in words:
            words = words.replace(f" {w} ", " ", 1)
            kind = kind or k
    left = [w for w in words.split() if not _RATE_WORD.match(w)]
    lays = {f[0] for f in found}
    if left or len(lays) != 1:
        return None
    lay = lays.pop()
    if lay == "atmos":
        kind = kind or "video"
    return lay, found[0][1], kind


def version_layout(name):
    """The channel layout a folder name gives (parse_version): "stereo", "multichannel",
    "atmos" or "mono"; "" when the name is not a version folder's."""
    got = parse_version(name)
    return got[0] if got else ""


def version_label(name):
    """The version a folder name names, as %_version% has it: "Stereo" ("2.0" too), "5.1", "Quad",
    "Atmos"; "" when the name is not a version folder's."""
    got = parse_version(name)
    return got[1] if got else ""


def layout_of_channels(channels, area=""):
    """The layout of audio with this many channels, or as its SACD_AREA tag names it
    ("Stereo", "Multichannel"); "" when neither says."""
    a = str(area or "").casefold()
    if "multi" in a:
        return "multichannel"
    if "stereo" in a:
        return "stereo"
    try:
        n = int(channels or 0)
    except (TypeError, ValueError):
        n = 0
    return "multichannel" if n > 2 else "stereo" if n == 2 else "mono" if n == 1 else ""


def medium_layout(fmt):
    """The channel layout a database medium's format names: MusicBrainz "Hybrid SACD (SACD
    layer, 2 channels)" is "stereo", "... (SACD layer, multichannel)" "multichannel"; "" when
    the format does not say (a plain CD, an SACD without its area, a DVD or Blu-ray, which
    holds every mix on one medium)."""
    f = str(fmt or "").casefold()
    if re.search(r"multi-?channel|surround|\b[4-7]\.[01]\b|\bquad", f):
        return "multichannel"
    if re.search(r"\b2 ?ch(?:annels?)?\b|\bstereo\b|\b2\.0\b", f):
        return "stereo"
    return ""


def medium_type(fmt):
    """What a database medium is, for a version folder: "sacd" (an SACD's high-resolution
    layer), "video" (DVD-Audio, DVD-Video, Blu-ray, HD-DVD, a DualDisc's DVD side: one medium
    with every mix), "cd" (a CD, an SACD's CD layer, a DualDisc's CD side) or ""."""
    f = str(fmt or "").casefold()
    if re.search(r"(?<!sa)cd layer|cd side", f):         # ("SACD layer" holds "CD layer")
        return "cd"
    if "sacd" in f:
        return "sacd"
    if re.search(r"dvd|blu-?ray|hd-?dvd|dualdisc|\bbd\b", f):
        return "video"
    if re.search(r"\bcd\b|-cd\b|\bcd-", f) or f in ("cd", "hdcd", "shm-cd", "blu-spec cd"):
        return "cd"
    return ""


def title_mix(title):
    """The mix a track title names ("Money (5.1 Mix)", "Money - Stereo", "Money [Atmos]"):
    "stereo", "multichannel", "atmos", "mono" or ""."""
    t = str(title or "").casefold()
    if re.search(r"\batmos\b|\bimmersive\b|\b\d\.\d\.\d\b", t):
        return "atmos"
    if re.search(r"(?<![\d.])[4-7]\.[01](?![\d.])|\bsurround\b|\bmulti-?channel\b|\bquad(?:raphonic)?\b", t):
        return "multichannel"
    if re.search(r"\bstereo\b|(?<![\d.])2\.0(?![\d.])", t):
        return "stereo"
    if re.search(r"\bmono\b", t):
        return "mono"
    return ""


_MIX_ORDER = {"mono": 0, "stereo": 1, "multichannel": 2, "atmos": 3}


def _version_options(n, lay, media):
    """Where a version of n tracks of this layout can be on the release's media: [(kind,
    medium, tracks or None, how)]: an SACD layer naming it, a DVD's or Blu-ray's tracklist (the
    whole of it, or the part of it whose titles name the mix, or - its parts naming none - one
    part to be told by order), a CD for a stereo version."""
    out = []
    for m in media:
        t, tracks, fmt = medium_type(m.get("format")), m.get("tracks") or [], m.get("format")
        if t == "sacd" and medium_layout(fmt) == lay and len(tracks) == n:
            out.append(("sacd", m, tracks, "format"))
        elif t == "video" and n and len(tracks) == n:
            out.append(("video", m, tracks, "single"))
        elif t == "video" and n and len(tracks) > n and len(tracks) % n == 0:
            parts = [tracks[i:i + n] for i in range(0, len(tracks), n)]
            mixes = []
            for part in parts:
                said = {title_mix(x.get("title")) for x in part}
                mixes.append(said.pop() if len(said) == 1 else "?")
            if mixes.count(lay) == 1:
                out.append(("video", m, parts[mixes.index(lay)], "titles"))
            elif lay == "stereo" and mixes.count("") == 1 and "stereo" not in mixes and "?" not in mixes:
                # (the others' titles name their mix - "(5.1 Mix)" -, the plain titles are the
                # stereo mix's)
                out.append(("video", m, parts[mixes.index("")], "titles"))
            elif all(x == "" for x in mixes):
                out.append(("video", m, None, "order"))
        elif t == "cd" and lay == "stereo" and len(tracks) == n:
            out.append(("cd", m, tracks, "cd"))
    return out


def version_view(album, cand):
    """The album's version folders ("2.0/", "5.1/": album "version_discs" {disc: layout}) as
    their own tracks of one release (cand "media_info": its media, their formats and tracks):
    an SACD's stereo or multichannel layer, the part of a DVD's or Blu-ray's tracklist that is
    that mix (or, the tracklist listing the tracks once, the whole of it, for every version),
    each version compared with its own tracks. Which kind of medium a version is must be
    what its files say (album "version_kinds": "sacd" - an SACD_AREA tag, DSD, 88.2 kHz -,
    "video" - a DVD or Blu-ray word in its folder name, a 48 kHz-family rate past a CD's,
    more channels -, "cd" - 16-bit/44.1 kHz stereo); a version with no such evidence, or
    with two places to be, is not placed. None when any version is not.

    The view keeps the release's ID; "version_tracks" [[medium position, track index]] are
    the files' own tracks of the release (in the album's order), "version_media" {album disc:
    medium position}; no track is "missing" (the other media are other formats of it).
    "version_unsure" says why the placing is not proof (a CD-quality stereo rip taken as the
    CD; a tracklist's parts taken in order, their titles naming no mix): never exact then."""
    versions = album.get("version_discs") or {}
    media = cand.get("media_info") or []
    if not versions or not media:
        return None
    discs = sorted({t.get("disc") or 1 for t in album.get("tracks") or ()})
    if set(discs) != set(versions):
        return None
    kinds = album.get("version_kinds") or {}
    labels = album.get("version_labels") or {}
    mine = {d: [t for t in album["tracks"] if (t.get("disc") or 1) == d] for d in discs}
    chosen, hows, unsure, notes, by_order = {}, {}, [], [], {}
    for d in discs:
        kind = kinds.get(d) or ""
        if not kind:
            return None                       # (nothing says which disc this version is from)
        opts = [o for o in _version_options(len(mine[d]), versions[d], media) if o[0] == kind]
        if len(opts) != 1:
            return None
        _k, m, tracks, how = opts[0]
        if how == "order":
            by_order.setdefault(id(m), (m, []))[1].append(d)
            continue
        chosen[d], hows[d] = (m, tracks), how
        by = (album.get("version_kind_by") or {}).get(d)
        if how != "cd" and by == "media" and \
                str(cand.get("id")) not in (album.get("version_release_ids") or {}).get(d, ()):
            # (a media tag naming the disc counts when the files' release ID is this release's:
            # TagMender's own filing. Another tagger's media tag, of another release, is no proof)
            unsure.append(f"only the media tag of your {labels.get(d) or versions[d]} folder, written for another "
                          f"release, says it is from the {m.get('format')}")
        if how != "cd" and by == "audio":
            # (only the rate, depth or channels say it came off that disc: a download of
            # the mix looks the same)
            unsure.append(f"only the audio of your {labels.get(d) or versions[d]} folder (its rate, depth "
                          f"or channels) says it is from the {m.get('format')}")
        if how == "cd":
            unsure.append(f"your {labels.get(d) or versions[d]} folder (16-bit/44.1 kHz) taken as the "
                          f"{m.get('format')}: a CD-quality copy of another medium would look the same")
        if how == "single":
            notes.append(f"the {m.get('format')} lists its tracks once: your {labels.get(d) or versions[d]} "
                         "folder is those tracks")
    for m, ds in by_order.values():
        n = len(mine[ds[0]])
        parts = [m["tracks"][i:i + n] for i in range(0, len(m["tracks"]), n)]
        order = sorted(ds, key=lambda d: (_MIX_ORDER.get(versions[d], 9), d))
        if len(order) != len(parts) or len({versions[d] for d in ds}) != len(ds) or \
                any(len(mine[d]) != n for d in ds):
            return None
        for d, part in zip(order, parts):
            chosen[d], hows[d] = (m, part), "order"
        unsure.append(f"the {m.get('format')} lists {len(parts)} sets of {n} tracks naming no mix: taken in "
                      f"order as {', '.join(labels.get(d) or versions[d] for d in order)}")
    taken = {}
    for d, (m, tracks) in chosen.items():
        taken.setdefault((id(m), id(tracks[0]) if tracks else 0), []).append(d)
    if any(len(ds) > 1 and any(hows[d] != "single" for d in ds) for ds in taken.values()):
        return None                           # (two versions taken as one set of tracks)
    view, where = [], []
    for d in discs:
        m, tracks = chosen[d]
        if len(tracks) != len(mine[d]):
            return None
        view += [{"disc": d, "title": x.get("title", ""), "secs": x.get("secs") or 0.0} for x in tracks]
        where += [[m.get("position"), x.get("index")] for x in tracks]
    return dict(cand, tracks=view, version_tracks=where,
                version_media={d: chosen[d][0].get("position") for d in discs},
                version_unsure="; ".join(unsure), version_notes=notes, full_tracks=len(cand.get("tracks") or []),
                disc_count=len(discs))


def barcode_key(value):
    """UPC-A (12 digits) and EAN-13 (the same code with a leading 0) are one
    barcode; compare on the digits without leading zeros."""
    return re.sub(r"\D", "", str(value or "")).lstrip("0")


# A barcode agreeing that is NOT proof of your copy: one typed in the ripper's notes, an
# .nfo or a saved web page ("barcode (notes)": perhaps copied from another edition's page),
# and one another tagger wrote into the files ("barcode (tags)": it came FROM the release
# that tagger picked, so it only confirms that pick). Each keeps its points, as a catalogue
# number from the folder name does, but never proves the pressing, outranks the folder
# name or decides a tie.
NOTES_BARCODE = "barcode (notes)"
TAGS_BARCODE = "barcode (tags)"
# likewise a catalogue number only another tagger wrote (it came from the release it picked)
TAGS_CATNO = "catalogue number (tags)"
WEAK_BARCODES = (NOTES_BARCODE, TAGS_BARCODE, TAGS_CATNO)


# BAR-3: the scan a barcode was read on. The outer packaging (back cover, box, slipcase)
# names the product you own; a disc, booklet or inlay may carry a component's own code.
_OUTER_SCAN = re.compile(r"back|rear|box|slip|outer|o-?card|spine", re.I)
_INNER_SCAN = re.compile(r"disc|(?<![a-z])cd\d*(?![a-z])|booklet|inlay|inner|inside|insert|label|matrix", re.I)


def barcode_on_outer(album, codes) -> bool:
    """BAR-3: one of these barcodes was decoded on a scan of the outer packaging."""
    keys = _keys(codes)
    for path, found in ((album.get("scan_log") or {}).get("barcodes") or {}).items():
        stem = re.sub(r"\.[^./]+$", "", str(path).rsplit("/", 1)[-1])
        if _keys(found) & keys and _OUTER_SCAN.search(stem) and not _INNER_SCAN.search(stem):
            return True
    return False


def _outer_barcode_pick(picks):
    """BAR-3: of picks carrying different barcodes of yours, the one whose barcode was read
    on the outer packaging, when it is the only one; else None."""
    outer = [s for s in picks if s.get("barcode_outer")]
    return outer[0] if len(outer) == 1 and len(picks) > 1 else None


def barcode_proves(s):
    """The pick's barcode agrees, read off your copy (its tags as ripped or tagged by
    TagMender, a scan, a cue sheet's CATALOG, a PDF booklet): proof of the pressing."""
    code = str((s or {}).get("code") or "")
    return code.startswith("barcode") and code not in WEAK_BARCODES


def _keys(values):
    return {barcode_key(b) for b in values or ()} - {""}


def typed_only_barcode(album, barcode):
    """True when this barcode is in the album's text only where someone TYPED it: the
    ripper's notes (.txt, .nfo, .info, .md) or a saved web page, never on a scan, a PDF
    booklet, a cue sheet's CATALOG line (the disc's own MCN) or the files' tags.
    match_albums sets album["typed_text"] / album["printed_text"] when it reads the
    documents; without them, the notes' "Barcode:" lines (album["note_barcodes"]) are."""
    k = barcode_key(barcode)
    if not k or k in _keys(album.get("barcodes")) | _keys(album.get("ocr_barcodes")):
        return False
    if album.get("printed_text") is not None or album.get("typed_text") is not None:
        if barcode_printed(album.get("printed_text") or "", barcode):
            return False
        if barcode_printed(album.get("typed_text") or "", barcode):
            return True
    return k in _keys(album.get("note_barcodes"))


def tagger_barcode(album, keys):
    """True when the barcodes agreeing (keys) came only from tags another tagger wrote
    (album["tags_by_tagger"]: the files name a release, and not by TagMender's own job),
    not decoded off a scan as well."""
    if not album.get("tags_by_tagger") or not keys:
        return False
    tags = _keys(album.get("tag_barcodes"))
    return keys <= tags and not keys & _keys(album.get("ocr_barcodes"))


def tagger_catno(album, shared):
    """True when the catalogue numbers agreeing (shared) came only from tags another tagger
    wrote (as tagger_barcode), not from the folder name or the ripper's notes as well."""
    if not album.get("tags_by_tagger") or not shared:
        return False
    tags = {norm_code(c) for c in album.get("tag_catnos") or ()}
    return shared <= tags and not shared & set(album.get("catnos_from_folder") or ())


def barcode_variants(value):
    """Forms to search for: as given, 12-digit UPC, 13-digit EAN."""
    key = barcode_key(value)
    if not key:
        return []
    out = [key.zfill(13)]
    if len(key) <= 12:
        out.append(key.zfill(12))
    return out


def sleeve_times(known) -> bool:
    """LEN-1: [(file secs, listed secs)] where the release lists EVERY track a few seconds short
    of your file - none more than a second longer, none more than 8 s shorter, most of them
    short at all - as times copied off a sleeve are (Popol Vuh - Kailash, Soul Jazz: each track
    0-6 s longer than Discogs lists). Another edit or version is not short on every track. Four
    tracks at least."""
    if len(known) < 4:
        return False
    # (8 s at most, and 4% of the track: on a 40 s interlude 8 s is another edit, not a misprint)
    return all(-1.0 <= f - c <= min(8.0, max(3.0, 0.04 * c)) for f, c in known) and \
        sum(1 for f, c in known if f - c >= 0.5) >= 0.75 * len(known)


def duration_ok(file_secs, cand_secs, cfg):
    tol = max(cfg["duration_tol_s"], cfg["duration_tol_pct"] * cand_secs)
    return abs(file_secs - cand_secs) <= tol


def numbers_repeat(album) -> int:
    """How many of the album's files share their disc and track number with another (two discs'
    tracks 1 to 11 in one folder without disc tags): their order against a release is unknown."""
    seen = Counter((t.get("disc") or 1, t.get("num")) for t in album.get("tracks") or () if t.get("num"))
    return sum(n for n in seen.values() if n > 1)


def align_durations(file_secs, cand_secs, cfg):
    """Longest in-order run of files whose lengths agree with the candidate's
    tracks (a longest-common-subsequence over a duration test). Shows that
    two lists are the same album plus or minus a bonus/missing track."""
    n, m = len(file_secs), len(cand_secs)
    table = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            if cand_secs[j - 1] > 0 and duration_ok(file_secs[i - 1], cand_secs[j - 1], cfg):
                table[i][j] = table[i - 1][j - 1] + 1
            else:
                table[i][j] = max(table[i - 1][j], table[i][j - 1])
    return table[n][m]


def track_titles_agree(a, b):
    """Two track titles name the same track (folded, edition words left out: the same test
    score_candidate's titles_agree uses)."""
    return _ratio(fold(strip_edition(a or "")), fold(strip_edition(b or ""))) >= 0.9


def _title_or_its_start(a, b):
    """ALB-1: two track titles agree, or the shorter is the opening words of the longer (a
    file titled "Tramonto" for the release's "Tramonto. Caldo umido...")."""
    if track_titles_agree(a, b):
        return True
    x, y = fold(strip_edition(a or "")).split(), fold(strip_edition(b or "")).split()
    short, long_ = (x, y) if len(x) <= len(y) else (y, x)
    return bool(short) and len(" ".join(short)) >= 4 and long_[:len(short)] == short


def track_map(album, cand, cfg):
    """Which of the candidate's tracks each file is, in order: [(file_i, cand_j), ...]. A pair is
    the same track when the titles agree, or, where the file or the track has no title, when
    the lengths agree (a length alone never pairs a file with a track of another title). The
    map is the in-order run (a longest common subsequence) with the most pairs, and among those
    the most titles and lengths agreeing. Only pairs that every such run has are kept: where the
    files could be lined up with the tracks in more than one way (untitled files of similar
    lengths beside a track they lack), those files are left out of the map, as a bonus file,
    or a track the files lack, is."""
    files, ct = album.get("tracks") or [], cand.get("tracks") or []
    n, m = len(files), len(ct)
    big = 3 * (min(n, m) + 1)                   # (one more pair outweighs any agreement)

    def weight(i, j):
        f, c = files[i], ct[j]
        lengths = (c.get("secs") or 0) > 0 and duration_ok(f.get("secs") or 0, c["secs"], cfg)
        if f.get("title") and c.get("title"):
            titles = track_titles_agree(f["title"], c["title"])
            return big + 1 + lengths if titles else 0
        return big + 1 if lengths else 0
    w = [[weight(i, j) for j in range(m)] for i in range(n)]

    def best(w):
        table = [[0] * (m + 1) for _ in range(n + 1)]
        for i in range(n - 1, -1, -1):
            for j in range(m - 1, -1, -1):
                table[i][j] = max(table[i + 1][j], table[i][j + 1],
                                  table[i + 1][j + 1] + w[i][j] if w[i][j] else 0)
        return table
    table = best(w)
    pairs, i, j = [], 0, 0
    while i < n and j < m:
        if w[i][j] and table[i][j] == table[i + 1][j + 1] + w[i][j]:
            pairs.append((i, j))
            i, j = i + 1, j + 1
        elif table[i][j] == table[i + 1][j]:
            i += 1
        else:
            j += 1
    # (a pair every best run has: without it, no run is as good)
    sure = []
    for i, j in pairs:
        saved, w[i][j] = w[i][j], 0
        if best(w)[0][0] < table[0][0]:
            sure.append((i, j))
        w[i][j] = saved
    return sure


# "Made in Germany" / "Printed in the EU" on a sleeve or disc, as release countries.
# MusicBrainz gives ISO codes ("DE", "XE" for Europe), Discogs gives names ("Germany").
_COUNTRY_NAMES = {
    "us": "US", "usa": "US", "u s a": "US", "united states": "US", "america": "US",
    "uk": "GB", "u k": "GB", "england": "GB", "united kingdom": "GB", "great britain": "GB", "gb": "GB",
    "eu": "XE", "e u": "XE", "europe": "XE", "european union": "XE",
    "germany": "DE", "west germany": "DE", "japan": "JP", "canada": "CA", "france": "FR",
    "holland": "NL", "netherlands": "NL", "the netherlands": "NL", "sweden": "SE", "finland": "FI",
    "norway": "NO", "denmark": "DK", "italy": "IT", "spain": "ES", "portugal": "PT", "poland": "PL",
    "russia": "RU", "ukraine": "UA", "austria": "AT", "switzerland": "CH", "belgium": "BE",
    "greece": "GR", "czech republic": "CZ", "czechia": "CZ", "hungary": "HU", "australia": "AU",
    "brazil": "BR", "mexico": "MX", "argentina": "AR", "korea": "KR", "south korea": "KR",
    "taiwan": "TW", "china": "CN", "hong kong": "HK", "israel": "IL", "turkey": "TR",
    "worldwide": "XW",
}
_MADE_IN = re.compile(r"\b(?:made|printed|manufactured|pressed|produced)\s+in\s+(?:the\s+)?"
                      r"([a-z][a-z .]{1,24}?)(?=\s*(?:$|[,;:/|()]|\.(?:\s|$)|\s{2}|and\b|\bby\b|\d))", re.I)
_LABEL_FILLER = {"records", "recordings", "record", "music", "entertainment", "productions", "production",
                 "inc", "ltd", "llc", "gmbh", "co", "company", "label", "group", "the", "and", "media",
                 "distribution", "sound", "sounds", "audio", "international", "a", "of"}


# Country codes a folder name may give as a comma-separated field of their own, in capitals
# ("[Sony, SICP 10151-4, JP]"): never words of a title, and none a format or rip word
_FIELD_COUNTRIES = {c: c for c in ("JP", "DE", "FR", "NL", "CA", "AU", "KR", "TW", "HK", "RU", "SE", "BR",
                                   "MX", "AR", "PL", "CZ", "FI", "DK", "AT", "BE", "PT", "GR", "CN", "NZ",
                                   "IT", "ES", "GB")}
_FIELD_COUNTRIES.update({"UK": "GB", "EU": "XE"})


def country_code(name):
    """ISO-like code for a release country given either way ("DE" or "Germany")."""
    raw = str(name or "").strip()
    if re.fullmatch(r"[A-Z]{2}", raw):
        # (Discogs writes "UK" and "EU": the codes GB and XE, as MusicBrainz and your country
        # order have them - a UK pressing was never GB in the country order or the EU checks)
        return {"UK": "GB", "EU": "XE"}.get(raw, raw)
    return _COUNTRY_NAMES.get(re.sub(r"[^a-z ]", " ", raw.casefold()).strip().replace("  ", " "), "")


# A label's or manufacturer's postal address on the back or in the credits says where the
# pressing comes from: "Prophecy Productions, D-54550 Daun", "Century Media Inc., 2323 W.
# El Segundo Blvd, Hawthorne, CA 90250", "Earache, PO Box 144, Nottingham NG3 4GE, UK".
# Only postal forms count (not a city named in the thanks or "Recorded in Montréal").
_EU_POSTAL = {"D": "DE", "A": "AT", "CH": "CH", "F": "FR", "NL": "NL", "B": "BE", "I": "IT", "S": "SE",
              "DK": "DK", "N": "NO", "FIN": "FI", "SF": "FI", "PL": "PL", "E": "ES", "P": "PT", "GR": "GR",
              "CZ": "CZ", "H": "HU", "L": "LU"}
_EU_POSTAL_RE = re.compile(r"(?<![A-Za-z0-9/])(D|A|CH|F|NL|B|I|S|DK|N|FIN|SF|PL|E|P|GR|CZ|H|L)\s?-\s?(\d{4,5})"
                           r"\s+[A-ZÄÖÜÉÈ][A-Za-zäöüßéèàç-]{2,}")
_STREET_RE = re.compile(r"(?:stra(?:ss|ß)e|str\.|weg|gasse|platz|allee)\s*\d+[a-z]?,?\s+(\d{4,5})\s+[A-ZÄÖÜ][a-zäöüß]{2,}",
                        re.I)
_US_STATE_CODES = ("AL AK AZ AR CA CO CT DE FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE NV NH NJ "
              "NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY DC").split()
_US_ZIP_RE = re.compile(r"\b[A-Z][a-zA-Z .]{2,20},?\s+(" + "|".join(_US_STATE_CODES) + r")\.?\s+\d{5}(?:-\d{4})?\b")
_UK_POST_RE = re.compile(r"\b[A-Z]{1,2}\d[A-Z\d]?\s\d[A-Z]{2}\b")
_UK_NEAR = re.compile(r"\b(?:england|london|u\.?k\.?|united kingdom|scotland|wales)\b", re.I)
_CA_POST_RE = re.compile(r"\b[A-Z]\d[A-Z]\s?\d[A-Z]\d\b")
_CA_NEAR = re.compile(r"\b(?:canada|qu[eé]bec|qc|ontario|on|b\.?c\.?|alberta|ab)\b", re.I)
_NL_POST_RE = re.compile(r"\b\d{4}\s?[A-Z]{2}\b")
_NL_NEAR = re.compile(r"\b(?:the\s+)?netherlands\b|\bholland\b|\bnederland\b", re.I)
_JP_POST_RE = re.compile(r"〒\s?\d{3}-\d{4}|\b\d{3}-\d{4}\b[^\n]{0,60}\b(?:tokyo|osaka|japan)\b", re.I)
_BR_POST_RE = re.compile(r"\bCEP:?\s?\d{5}-?\d{3}\b")


def _other_country_follows(text, m):
    """A country named after a postal code on its line, and not the prefix's: the letter
    before the code is no country prefix ("ICR/N - 16153 GENOA, ITALY" is no Norwegian
    address; Sadist - Spellbound)."""
    rest = text[m.end():].split("\n", 1)[0].casefold()
    # whole country names only ("us" and "eu" are words too; Europe or Worldwide names none)
    named = {code for name, code in _COUNTRY_NAMES.items()
             if len(name) >= 5 and code not in ("XE", "XW") and re.search(rf"\b{re.escape(name)}\b", rest)}
    return bool(named) and _EU_POSTAL[m.group(1)] not in named


def countries_from_addresses(ocr_text):
    """Countries whose postal address is printed on the scans."""
    text = str(ocr_text or "")
    out = {_EU_POSTAL[m.group(1)] for m in _EU_POSTAL_RE.finditer(text) if not _other_country_follows(text, m)}
    # "Hauptstrasse 109, 73072 Donzdorf": a German street address (5 digits; Austria has 4)
    for m in _STREET_RE.finditer(text):
        out.add("DE" if len(m.group(1)) == 5 else "AT")
    if _US_ZIP_RE.search(text):
        out.add("US")
    for rx, near, code in ((_UK_POST_RE, _UK_NEAR, "GB"), (_CA_POST_RE, _CA_NEAR, "CA"),
                           (_NL_POST_RE, _NL_NEAR, "NL")):
        for m in rx.finditer(text):
            if near.search(text[max(0, m.start() - 60):m.end() + 60]):
                out.add(code)
    if _JP_POST_RE.search(text):
        out.add("JP")
    if _BR_POST_RE.search(text):
        out.add("BR")
    return out


def countries_on_scans(ocr_text):
    """Release countries printed on the scans ("Made in Germany" -> {"DE"}), and the
    countries of the postal addresses printed there (the label's own office)."""
    out = countries_from_addresses(ocr_text)
    for m in _MADE_IN.finditer(str(ocr_text or "")):
        words = re.sub(r"[^a-z ]", " ", m.group(1).casefold()).split()
        for n in range(min(3, len(words)), 0, -1):       # longest known name first
            code = _COUNTRY_NAMES.get(" ".join(words[:n]))
            if code:
                out.add(code)
                break
    return out


# A line saying whose rights these are ("... all rights reserved", "copyright ...").
# Not the bare ℗/© marks: a label's own "(P) 2010 Nuclear Blast GmbH" names the label,
# and OCR also reads the "•" between address parts as "©".
_RIGHTS_LINE = re.compile(r"copyright|rights\s+reserved|all\s+rights|\breserved\b|"
                          r"under\s+(?:exclusive\s+)?licen[cs]e", re.I)
_LICENSOR = re.compile(r"(?:licen[cs]ed|licen[cs]e)\s+(?:from|by|of)\s+(?:[A-Za-z0-9&'.-]+\s*){1,4}", re.I)
_CONFUSABLE = str.maketrans({"o": "0", "s": "5", "i": "1", "l": "1", "b": "8", "z": "2"})


def _zeroless(code):
    """A code with its zeros dropped, after OCR look-alikes are folded to digits:
    norm_code drops leading zeros only ("QDISS0201CDD" -> "qdiss201cdd") while a zero
    OCR read as O stays ("QDISSO201CDD" -> "qd1550201cdd"); without zeros both agree."""
    return ocr_confusable(code).replace("0", "")


def ocr_confusable(code):
    """A code with the letters OCR mistakes for digits made digits, so "FOSO1"
    and "FO501" compare equal (both become "f0501")."""
    return str(code or "").casefold().translate(_CONFUSABLE)


def _o_for_zero(text):
    """Catalogue numbers with a zero printed in a font OCR reads as the letter O
    ("QDISSO201CDD" for Dissonance's QDISS0201CDD): an O touching a digit is a 0."""
    out = []
    for m in re.finditer(r"(?<![A-Za-z0-9])([A-Z][A-Z0-9]{4,15})(?![A-Za-z0-9])", text):
        tok = m.group(1)
        if "O" not in tok or not re.search(r"\d", tok):
            continue
        fixed = tok
        while True:
            nxt = re.sub(r"O(?=\d)|(?<=\d)O", "0", fixed)
            if nxt == fixed:
                break
            fixed = nxt
        if fixed != tok and _OCR_CATNO_RE.fullmatch(fixed):
            out.append(fixed)
    return out


# One printed number: digit groups apart by at most two spaces or one of the marks OCR
# reads between them ("0 724384 260521", "8°715392°18 2327", "5-013929-020160").
# A line break or a word ends it.
_DIGIT_RUN = re.compile(r"\d+(?:[ \t°'’·.\-]{1,2}\d+)*")


@functools.lru_cache(maxsize=16)
def _digit_runs(text):
    return tuple(tuple(re.findall(r"\d+", m.group(0))) for m in _DIGIT_RUN.finditer(text))


def barcode_printed(text, barcode):
    """True when the barcode stands printed as ONE number in the text: whole digit
    groups of one run, joined, are the barcode (leading zeros aside). Digits are never
    joined across words or lines, so unrelated numbers ("Tel. 0172 ... LC 0123 ...")
    cannot add up to a barcode."""
    key = barcode_key(barcode)
    if len(key) < 11:
        return False
    # A barcode completed from its check digit is a guess, not a printed reading.
    text = "\n".join(l for l in str(text or "").splitlines() if not l.startswith(COMPLETED))
    for groups in _digit_runs(text):
        for i in range(len(groups)):
            joined = ""
            for g in groups[i:]:
                joined += g
                if len(joined.lstrip("0")) >= len(key):
                    break
            if joined.lstrip("0") == key:
                return True
    return False


COMPLETED = "check-digit completed: "


def barcodes_read_whole(text, codes):
    """The barcodes read in full somewhere in the text, not only completed from twelve
    digits by the check digit: a completion always passes the check, so a misread
    ("000044 388080" for Enslaved's 7090014385050) becomes a valid-looking wrong
    barcode. Completed ones are searched; only whole readings stand for the copy."""
    text = str(text or "")
    out = []
    for c in codes:
        total = len(re.findall(r"(?<!\d)" + re.escape(c) + r"(?!\d)", re.sub(r"(?<=\d)[ °'’·.](?=\d)", "", text)))
        if total > text.count(COMPLETED + c):
            out.append(c)
    return out


def complete_ean(digits12):
    """The EAN-13 whose last twelve digits OCR read: the first digit sits apart, left
    of the bars, and is the one most often lost ("5 013929 020160" read as
    "BS 013929 020160)"). The check digit fixes it: exactly one leading digit fits."""
    d = str(digits12 or "")
    if not (d.isdigit() and len(d) == 12):
        return ""
    for first in "0123456789":
        if valid_barcode(first + d):
            return first + d
    return ""


def _misread_variants(text):
    """Catalogue numbers OCR read with letters in the number ("FOSO1 cp" for
    FO501CD): the readings with those letters taken as digits, to search for."""
    out = []
    digitish = str.maketrans({"O": "0", "S": "5", "I": "1", "l": "1", "B": "8", "Z": "2"})
    for m in re.finditer(r"(?<![A-Za-z0-9])([A-Z][A-Z0-9]{3,8}?)(?:\s?([cC]{1,2}[dDpPoO0]))?(?![A-Za-z0-9])", text):
        tok, fmt = m.group(1), m.group(2)
        if not re.search(r"\d", tok) or not re.search(r"[OSIBZ]", tok[1:]):
            continue
        if any(tok[:k].isalpha() and tok[k:].isdigit() and len(tok) - k >= 3 for k in range(1, len(tok))):
            continue                                   # already read cleanly ("FO532")
        for k in (2, 1, 3):
            head, tail = tok[:k], tok[k:]
            if not head.isalpha():
                continue
            digits = tail.translate(digitish)
            if digits.isdigit() and len(digits) >= 3 and digits != tail:
                out.append(head + digits + ("CD" if fmt else ""))
                break
    return out


# Discogs identifiers of the disc itself: the matrix / runout etched or printed round the
# hub, the IFPI SID codes of the mastering room and of the mould, the label code (LC).
# Identifier type (casefolded) -> the clue it gives.
MATRIX_KINDS = {"matrix / runout": "matrix", "mastering sid code": "sid code",
                "mould sid code": "sid code", "label code": "label code"}
_MATRIX_SPLIT = re.compile(r"[\s\-‐‑–—_.,:;/\\|()\[\]{}'\"`*=+~#°]+")
# only the look-alikes OCR confuses most on a hub: O for 0, I and l for 1 (both sides alike)
_MATRIX_FOLD = str.maketrans({"o": "0", "i": "1", "l": "1"})


def matrix_tokens(text):
    """The words of a matrix code written so OCR and Discogs agree: case, spaces, dashes and
    dots dropped, O read as 0 and I/l as 1 ("QDISSO201CDD-1 15 AOO" -> qd1ss0201cdd 1 15 a00)."""
    return [t for t in _MATRIX_SPLIT.split(str(text or "").casefold().translate(_MATRIX_FOLD)) if t]


@functools.lru_cache(maxsize=16)
def _matrix_hay(text):
    """The text's words run together, and where each word starts: a code matches only
    from the start of a word to the end of one, however OCR split it into words."""
    joined, bounds = "", {0}
    for t in matrix_tokens(text):
        joined += t
        bounds.add(len(joined))
    return joined, frozenset(bounds)


def _matrix_in(hay, key):
    joined, bounds = hay
    i = joined.find(key)
    while i >= 0:
        if i in bounds and i + len(key) in bounds:
            return True
        i = joined.find(key, i + 1)
    return False


def _matrix_distinctive(key):
    """Long enough to be this code and nothing else on a sleeve: 6+ characters with letters
    and digits, or 8+ digits. Never "1", "A", a side or a disc number."""
    digits = sum(c.isdigit() for c in key)
    return bool((len(key) >= 6 and 0 < digits < len(key)) or (digits >= 8 and digits == len(key)))


def cand_matrices(cand):
    """The candidate's disc identifiers as (clue, value), from discogs_candidate."""
    return [(MATRIX_KINDS.get(str(k).casefold()), v) for k, v in cand.get("matrix") or ()
            if MATRIX_KINDS.get(str(k).casefold()) and str(v or "").strip()]


def matrix_read(text, value, own=()):
    """The part of a matrix value read in the text (folded as matrix_tokens), or "".
    The whole value, or a run of its words that is distinctive on its own and holds its
    longest word; a run that is
    only the catalogue number or barcode again (`own`, folded) says nothing new: a letter
    or three digits (zeros aside) must be left beyond it ("QDISS0201CDD-1" on a back cover's
    "QDISS0201CDD 1999" does not count, its words end elsewhere)."""
    words = matrix_tokens(value)
    if not words or not text:
        return ""
    hay = _matrix_hay(str(text))
    best = ""
    # a run always holds the value's longest word: "-1 15 A00" alone is on many discs
    stem = max(range(len(words)), key=lambda k: len(words[k]))
    for i in range(stem + 1):
        for j in range(len(words), stem, -1):
            key = "".join(words[i:j])
            if len(key) <= len(best) or not _matrix_distinctive(key):
                continue
            # zeros aside: a catalogue number is kept without its leading zeros
            rest = key.replace("0", "")
            if rest and any(rest in c.replace("0", "") for c in own):
                continue             # a piece of the catalogue number ("NB1234" of NB 1234-2)
            for c in own:
                c = c.replace("0", "")
                if c and c in rest:
                    rest = rest.replace(c, "", 1)
            if (len(rest) < 3 and not re.search(r"[a-z]", rest)) or not _matrix_in(hay, key):
                continue
            best = key
    return best


def _matrix_conflict(ring_text, values):
    """A code read on the disc ring that is this pressing's matrix with other digits:
    the same length (7+), the same first four characters, and two or more digit places
    different, none of them a letter OCR could have misread ("QDISS0202CDD" read clearly
    while this pressing is "QDISS0201CDD-1" is NOT a conflict: one digit). Returns the
    reading, or ""."""
    mine = {w for v in values for w in matrix_tokens(v)}
    long_mine = [w for w in mine if len(w) >= 7 and _matrix_distinctive(w)]
    if not long_mine:
        return ""
    for r in dict.fromkeys(matrix_tokens(ring_text)):
        if r in mine or len(r) < 7 or not _matrix_distinctive(r):
            continue
        for w in long_mine:
            if len(r) != len(w) or r[:4] != w[:4]:
                continue
            diff = [(a, b) for a, b in zip(r, w) if a != b]
            if len(diff) >= 2 and all(a.isdigit() and b.isdigit() for a, b in diff):
                return r
    return ""


def matrix_clues(ocr_text, cand, ring_text="", agree=None, disagree=None):
    """Discogs' matrix / runout, SID and label codes of this pressing read on the scans
    (the disc rings OCR'd flat, a disc scan's page OCR). A clue, never proof: added to
    agree as "matrix", "sid code" or "label code"; a matrix the disc ring clearly reads
    with other digits goes to disagree."""
    agree = {} if agree is None else agree
    disagree = [] if disagree is None else disagree
    found = cand_matrices(cand)
    if not found or not (ocr_text or ring_text):
        return agree, disagree
    own = {"".join(matrix_tokens(c)) for c in (cand.get("catnos") or ())} | \
          {barcode_key(b) for b in (cand.get("barcodes") or ())}
    text = "\n".join(t for t in (ocr_text, ring_text) if t)
    names = {"matrix": "matrix", "sid code": "SID code", "label code": "label code"}
    for clue, value in found:
        if clue not in agree and matrix_read(text, value, own):
            agree[clue] = f"{names[clue]} {value.strip()} on the scans"
    if "matrix" not in agree and ring_text:
        values = [v for c, v in found if c == "matrix"]
        other = _matrix_conflict(ring_text, values)
        if other:
            disagree.append(f"disc ring reads {other.upper()}, this pressing's matrix is "
                            f"{' / '.join(v.strip() for v in values[:2])}")
    return agree, disagree


def pressing_clues(ocr_text, cand, ring_text=""):
    """What the scans say about this particular pressing.

    Returns (agree, disagree): agree names the clues printed on the scans that
    this pressing carries (its catalogue number, barcode, label, country);
    disagree names a clue it contradicts (the sleeve says "Made in Japan" and
    this is the German pressing)."""
    agree, disagree = {}, []
    if not ocr_text:
        return matrix_clues(ocr_text, cand, ring_text, agree, disagree)
    hay = norm_code(ocr_text)
    hay_c = ocr_confusable(hay)
    hay_z = _zeroless(hay)
    for c in sorted(cand.get("catnos") or ()):
        digits = re.sub(r"\D", "", c)
        distinctive = (len(c) >= 4 and re.search(r"[a-z]", c) and digits) or len(digits) >= 6
        core = re.sub(r"(cd|lp|dvd|sacd|mc|cdr|dg)$", "", c)        # "fo501cd" read as "FOSO1 cp"
        core_ok = len(core) >= 5 and re.search(r"[a-z]", core) and len(re.sub(r"\D", "", core)) >= 3
        if distinctive and (c in hay or ocr_confusable(c) in hay_c
                            or (core_ok and ocr_confusable(core) in hay_c)
                            or (len(c) >= 7 and _zeroless(c) in hay_z)):
            agree["catalogue number"] = f"catalogue number {c.upper()} on the scans"
            break
        # a short code ("BS 004" is "bs4" once normalised: At The Gates - Gardens Of Grief on
        # Black Sun) counts when it stands printed as a code of its own, its zeros perhaps
        # read as O ("BS.OO4")
        m = re.fullmatch(r"([a-z]{2,})(\d{1,3})", c)
        if m and re.search(r"(?<![A-Za-z0-9])" + m.group(1) + r"[\s.\-/]*[0Oo]*" + m.group(2) + r"(?![0-9])",
                           ocr_text, re.I):
            agree["catalogue number"] = f"catalogue number {c.upper()} on the scans"
            break
    for b in sorted(cand.get("barcodes") or ()):
        if barcode_printed(ocr_text, b):
            agree["barcode"] = f"barcode {b} on the scans"
            break
    # "licensed from Century Media": the licensor, not this pressing's label
    # ... and not the rights holder named in the copyright line ("℗ 2018 Defen Society 3000,
    # Cryptopsy, all rights reserved" on Hammerheart's CD): the label that issued the
    # pressing is the one in the logo, the address and the catalogue number.
    no_rights = "\n".join(l for l in str(ocr_text).splitlines() if not _RIGHTS_LINE.search(l))
    text_words = " " + fold(_LICENSOR.sub(" ", no_rights)) + " "
    for label in str(cand.get("label") or "").split(";"):
        words = [w for w in fold(label).split() if w not in _LABEL_FILLER]
        phrase = " ".join(words)
        if len(phrase) >= 4 and f" {phrase} " in text_words:
            agree["label"] = f"label {label.strip()} on the scans"
            break
    printed = countries_on_scans(ocr_text)
    mine = country_code(cand.get("country"))
    if printed and mine and mine != "XW":
        if mine == "XE" and printed & _EUROPE and mine not in printed:
            pass        # a European pressing with a German or Dutch address: consistent, but no clue
        elif printed == {"XE"} and mine in _EUROPE and mine != "XE":
            pass        # "Made in the EU" on a UK or German pressing: where it was pressed, not against it
                        # (Pallas - wearewhoweare, a UK CD pressed in the EU)
        elif mine in printed:
            name = cand.get("country") or ""
            if len(name) == 2:
                name = next((n for n, c in _COUNTRY_NAMES.items() if c == name and len(n) > 3), name)
                name = name.upper() if name in ("us", "uk", "eu") else name.title()
            agree["country"] = f"made in {name} on the scans"
        else:
            disagree.append(f"scans say made in {'/'.join(sorted(printed))}, "
                            f"this pressing is from {cand.get('country')}")
    return matrix_clues(ocr_text, cand, ring_text, agree, disagree)


_VINYL_HINT = re.compile(r"vinyl|(?<![A-Za-z0-9])\d*\s*LPs?\b", re.I)
_CD_HINT = re.compile(r"(?<![A-Za-z0-9])\d*\s*CDs?\b|\bSACD\b|\bHDCD\b", re.I)    # not "FO40CD"
# A remaster ("[2016 RM 20th Annv Edn]") says nothing of the source: remasters come out on
# CD and vinyl too (Pantera - The Great Southern Trendkill: 23 tracks of whole CD sectors).
_DIGITAL_HINT = re.compile(r"\b(?:web|hi-?res)\b|\d+\s*-?\s*bit|k\s*hz", re.I)


_STORES = (("prostudio\\s*s?\\s*masters?", "ProStudioMasters"), ("hd\\s*tracks", "HDtracks"),
           ("qobuz", "Qobuz"), ("bandcamp", "Bandcamp"), ("7\\s*digital", "7digital"),
           ("itunes", "iTunes"), ("tidal", "Tidal"), ("deezer", "Deezer"),
           ("presto\\s*music", "Presto Music"), ("e-?onkyo", "e-onkyo"),
           ("nativedsd", "NativeDSD"), ("beatport", "Beatport"), ("web(?:-?dl)?", "a download shop"))
_STORE_HINT = [(re.compile(rf"(?<![A-Za-z0-9]){p}(?![A-Za-z0-9])", re.I), n) for p, n in _STORES]


def download_store(folder):
    """The download shop a folder name says the files came from ("[ProStudioMasters]",
    "(Qobuz 24-96)", "[WEB]"), or "": only inside the brackets of the album folder's own
    name, so a title ("Tidal Wave") never counts."""
    name = str(folder or "").rstrip("/\\").replace("\\", "/").rsplit("/", 1)[-1]
    for group in re.findall(r"[\[(]([^\])]*)[\])]", name):
        for rx, store in _STORE_HINT:
            if rx.search(group):
                return store
    return ""


# (a rip marker, never a word of the title: "Demo Tape")
_CASSETTE_HINT = re.compile(r"\b(?:cassette|tape)[- ]?rip\b|[\[(]\s*(?:cassette|tape)\s*[\])]", re.I)


def media_hint(folder):
    """What the folder name says the copy was made from: "vinyl", "cd", or
    "digital" (a web or hi-res download with no CD or vinyl named)."""
    text = str(folder or "")
    if _VINYL_HINT.search(text):
        return "vinyl"
    if _CD_HINT.search(text):
        return "cd"
    if _DIGITAL_HINT.search(text):
        return "digital"
    return ""


def release_details(cand):
    """What tells a release from the album's others, in words: format, country, catalogue number."""
    names = list(dict.fromkeys(str(x) for x in cand.get("media_formats") or () if x))
    fmt = ", ".join(names) or "/".join(sorted(cand.get("formats") or ()))
    return " · ".join(x for x in (fmt, cand.get("country"), cand.get("catno_shown")) if x)


def media_kind(fmt):
    """A database format name as "cd", "vinyl", "digital" or "cassette"."""
    f = str(fmt or "").casefold()
    if "vinyl" in f or re.search(r'\b(?:7|10|12)"', f) or f in ("lp", "flexi-disc"):
        return "vinyl"
    if "digital" in f or f.split(",")[0].strip() in ("file", "download"):   # Discogs: "File, FLAC, Album"
        return "digital"
    if "cd" in f:
        return "cd"
    if "cassette" in f:
        return "cassette"
    return ""


# A matrix read off the disc names the pressing (2, as an ISRC: OCR of a hub is never
# certain); a SID or label code is shared by a plant's or a label's whole output (1).
# EVI-1: barcode > catalogue number > disc ID / matrix > label, country > folder name
CLUE_WEIGHTS = {"disc TOC": 3, "disc TOC (fuzzy)": 1, "barcode": 5, "barcode (notes)": 0,
                "catalogue number": 4, "source link": 3, "isrc": 2,
                "fingerprint": 2, "date": 2, "matrix": 3, "sid code": 1, "label code": 1, "cover": 0,
                "label": 1, "packaging": 1, "country": 1, "format": 1, "physical": 1, "track lengths": 1,
                "exact lengths": 2, "track titles": 1, "script": 1}
# "exact lengths" (exact_lengths: every track fits where the others are off track by track)
# outweighs one weight-1 clue: other pressings 2 s+ off on several tracks are another mix
# "disc TOC (fuzzy)": a fuzzy disc ID or CTDB match, "a CD of about these lengths" (file_clues):
# told, never proof, and never telling pressings of one album apart (_weigh)


def _readable_country(code_or_name):
    name = str(code_or_name or "")
    if name in ("GB", "UK"):
        return "the UK"               # (not "England": the first long name listed for GB)
    if len(name) == 2:
        name = next((n for n, c in _COUNTRY_NAMES.items() if c == name and len(n) > 3), name)
        name = name.upper() if name in ("us", "uk", "eu") else name.title()
    return name


def _bracket_text(folder, pressing_only=False):
    parts = re.findall(r"[\(\[\{]([^\)\]\}]*)[\)\]\}]", str(folder or ""))
    if pressing_only:
        # "(Live at Roadburn 2011 - Holland)" says where it was played, not where it was pressed
        parts = [p for p in parts if not re.search(r"\blive\b|\brecorded\b|\b(?:at|in)\s+[A-Z]", p, re.I)]
    return " ".join(parts)


_PACKAGING = (("digibook", r"digi-?book"), ("mediabook", r"media-?book"),
              ("digipak", r"digi-?pa(?:c?k)|(?<![a-z])digi(?![a-z])"),
              ("jewel", r"jewel"), ("gatefold", r"gatefold"))


def packaging_kind(text):
    """"digipak", "digibook", "mediabook", "jewel", "gatefold" or "" from a packaging name
    (MusicBrainz "Jewel Case", "Digipak"; Discogs "Digipak"; a scan named "Digi Cover")."""
    t = str(text or "").casefold()
    for kind, rx in _PACKAGING:
        if re.search(rx, t):
            return kind
    return ""


def enhanced_clue(album, cand, clues):
    """Your disc was an Enhanced CD (album["enhanced"]: its data track in the rip log or
    AccurateRip report, its CDPLUS folder copied off it), and so is this release (R12:
    Pestilence - Mind Reflections, Metal Mind's 2006 Enhanced CD among plain CDs). A clue
    only: a release not marked enhanced may just lack the description."""
    if album.get("enhanced") and cand.get("enhanced"):
        clues.setdefault("enhanced", f"Enhanced CD ({album['enhanced']})")


def unofficial_why(best):
    """An unofficial release (Discogs "Unofficial Release", MusicBrainz "Bootleg") is never
    taken on names and lengths alone: only your barcode or a matrix read off your disc
    agreeing makes it your copy (R9: Kreator's Russian 8382890 for SPV 69848). Your files
    already tagged with that very release, which then fits like any other (Coroner - Death
    Cult, a4e8f7ab...), keep it when something of your copy backs those tags too (its
    catalogue number, disc ID or a link): a note says it is unofficial. The tags alone are
    not evidence (an earlier tagger may have got them wrong). ISRCs and fingerprints do not
    back them: a bootleg copies the official release's recordings, and both name recordings,
    not a pressing. Nor does a barcode typed in notes, or a barcode or catalogue number
    another tagger wrote (WEAK_BARCODES)."""
    if not best["cand"].get("unofficial"):
        return []
    clues = best.get("clues") or {}
    if barcode_proves(best) or clues.get("matrix"):
        return []
    backed = (best["code"].startswith("catalogue number") and best["code"] not in WEAK_BARCODES) or any(
        k in clues for k in ("disc TOC", "source link"))
    if best.get("tagged") and backed:
        best["notes"] = best["notes"] + ["the release is an unofficial one (a bootleg); kept: your files "
                                         "are tagged with it"]
        return []
    return ["the release is an unofficial one (a bootleg); nothing of your copy (barcode, matrix) says it is yours"]


def cover_clue(cover_text, cand, clues):
    """The catalogue number or label read on the front cover embedded in the files (a folder
    with no scans). The embedded cover may come from the web, so it is one weak clue
    ("cover", weight 0: told, never picking a pressing) and nothing against: never a
    barcode, never the scans' weight."""
    if not cover_text:
        return
    agree, _ = pressing_clues(cover_text, dict(cand, barcodes=set(), matrix=()))
    found = [agree[k].replace(" on the scans", "") for k in ("catalogue number", "label") if k in agree]
    if found:
        clues.setdefault("cover", " and ".join(found) + " on the front cover embedded in the files")


TOC_CLUE = "disc ID (track positions of the CD) matches"
EXACT_TOC_CLUE = TOC_CLUE + ", and MusicBrainz has that very disc ID attached to this release"
FUZZY_TOC_CLUE = ("disc ID lookup found this release only by a near match (a CD of about these lengths, "
                  "not your disc ID): not proof of the pressing")


def exact_disc_id(album, cand):
    """True when a disc ID of these files is attached to this MusicBrainz release (the
    lookup was exact, not a fuzzy "a CD of about these lengths")."""
    return (cand.get("source") == "mb" and cand.get("id") in (album.get("toc_releases") or ())
            and cand.get("id") in (album.get("exact_toc_releases") or ()))


def toc_lengths(album, cand, tracks_ok) -> bool:
    """TOC-1: the release's lengths are your files' own, whatever lengths it lists: every disc
    of these files has a TOC that fits its files to the sector, and MusicBrainz has each of
    those disc IDs attached to this release (a disc ID is a hash of the whole TOC: every
    track's start and the lead-out). Its listed lengths may be typed or rounded (Food - Food,
    Feral Records: each listed 4 s short of the disc). Three tracks or more, the track counts
    the same."""
    tocs = album.get("tocs") or {}
    if not tracks_ok or len(album.get("tracks") or ()) < 3 or not tocs or not exact_disc_id(album, cand):
        return False
    # every disc of the files has a TOC, and each fits (a disc without one, lossy or not cut on
    # sectors, has nothing to prove its lengths)
    discs = {t.get("disc") or 1 for t in album.get("tracks") or ()}
    if set(tocs) != discs or set(album.get("tocs_fit") or ()) != discs:
        return False
    # and each disc's own disc ID is on the release's medium of that disc (or, one disc of a
    # box, on the disc of the box the album is: _box_disc)
    on = (album.get("exact_toc_on") or {}).get(cand.get("id")) or {}
    for d, toc in tocs.items():
        want = cand["box_disc"] if cand.get("box_disc") and len(discs) == 1 else d
        if want not in (on.get(toc) or ()):
            return False
    return True


def short_disc_proven(album, cand, cfg=None):
    """A one- or two-track disc whose disc ID is attached to this very release, under the
    album's own title (Bell Witch - Mirror Reaper: one 48-minute track per disc). An exact
    disc ID is a hash of the whole TOC, the lead-out included, so its lead-out is the
    files' own; the title keeps out an unrelated CD that happens to be as long."""
    return (exact_disc_id(album, cand) and len(album.get("tracks") or ()) in (1, 2)
            and title_sim(album.get("title") or "", cand.get("title") or "")
            >= (cfg or DEFAULT_CFG).get("exact_title_sim", DEFAULT_CFG["exact_title_sim"]))


def file_clues(album, cand, clues, against, cfg=None):
    """Clues to the pressing from the files and their folder (the scans are
    read by pressing_clues): the disc ID, the tags, the folder name, the audio.
    cfg: the run's settings (the title a one- or two-track disc must match)."""
    formats = set(cand.get("formats") or ())
    # A disc ID identifies a CD only when it has a few tracks: a one- or two-track TOC is
    # just "a CD this long", which unrelated CDs share (The Ocean - The Grand Inquisitor IV,
    # a one-track single, "matched" Michael Mauldin's The Last Musician of Ur).
    exact = exact_disc_id(album, cand)
    # When MusicBrainz has your disc ID attached to some release, a release found only by
    # a fuzzy or CTDB lookup is another disc, not proof (Mournful Congregation - The
    # Incubus Of Karma: the US CD's own disc ID is 7 sectors shorter than the rip's)
    fuzzy_only = bool(album.get("exact_toc_releases")) and not exact
    if ((len(album.get("tracks") or ()) >= 3 or short_disc_proven(album, cand, cfg)) and
            ((cand.get("source") == "mb" and not fuzzy_only and cand.get("id") in (album.get("toc_releases") or ()))
             or (cand.get("source") == "discogs" and str(cand.get("id")) in (album.get("toc_discogs") or ())))):
        # Exact: your disc ID is attached to this very release. Otherwise the match is a
        # fuzzy MusicBrainz or CTDB one, "a CD of about these lengths" with another disc ID:
        # releases nobody attached a disc ID to (often Japanese and other regional pressings)
        # can never get it, so it is told (weight 1) but never proof, and never tells
        # pressings apart. A CTDB link to a Discogs release keeps the full clue only when
        # MusicBrainz has your exact disc ID on some release (the disc is known for sure).
        if exact or (cand.get("source") == "discogs" and album.get("exact_toc_releases")):
            clues["disc TOC"] = EXACT_TOC_CLUE if exact else TOC_CLUE
        else:
            clues["disc TOC (fuzzy)"] = FUZZY_TOC_CLUE
    # The shop page the files were bought from (Bandcamp, Deezer...: its link in the tags),
    # which MusicBrainz ties to this release (match_albums.link_releases). A MusicBrainz or
    # Discogs release link in the tags is not this: whoever tagged the files wrote it, like a
    # release ID tag (a candidate, tag_release_mb / tag_release_discogs), never proof.
    if cand.get("source") == "mb" and cand.get("id") in (album.get("link_releases") or ()):
        clues["source link"] = "the shop page your files' tags link to is this release"
    if cand.get("source") == "mb" and cand.get("id") in (album.get("isrc_releases") or ()):
        clues["isrc"] = "the files' ISRC codes are on this release"
    if cand.get("source") == "mb" and cand.get("id") in (album.get("fp_releases") or ()):
        clues["fingerprint"] = "audio fingerprints (AcoustID) are on this release"
    cover_clue(album.get("cover_text"), cand, clues)
    enhanced_clue(album, cand, clues)
    # tags written by whoever ripped or bought it. Not when another tagger wrote them (the
    # files name a release in their ID tags or links, and TagMender did not write them): their
    # label, country, date and media came FROM that release and only confirm it, never tell
    # its pressings apart (match_albums.read_album "tags_by_tagger").
    by_tagger = bool(album.get("tags_by_tagger"))
    label = None if by_tagger else album.get("tag_label")
    if label:
        mine = [w for w in fold(label).split() if w not in _LABEL_FILLER]
        theirs = [" ".join(w for w in fold(x).split() if w not in _LABEL_FILLER)
                  for x in str(cand.get("label") or "").split(";")]
        if mine and any(" ".join(mine) == x or (x and (" ".join(mine) in x or x in " ".join(mine)))
                        for x in theirs):
            clues.setdefault("label", f"label {label} in the tags")
    tc = None if by_tagger else country_code(album.get("tag_country"))
    cc = country_code(cand.get("country"))
    if tc and cc:
        if tc == cc:
            clues.setdefault("country", f"release country {_readable_country(tc)} in the tags")
        elif "XW" not in (tc, cc):
            against.append(f"tags say {tc}, this pressing is from {cand.get('country')}")
    tm = None if by_tagger else album.get("tag_media")
    if tm and formats:
        if tm in formats:
            clues.setdefault("format", f"{tm} in the tags")
        else:
            against.append(f"tags say {tm}, this pressing is {'/'.join(sorted(formats))}")
    # A Japanese (or Chinese) title in your folder name or tags names the edition:
    # "Koloss = 伏魔殿", "obZen = オブゼン" are the Japanese CDs, not the Russian or US ones.
    named_in = " ".join(str(album.get(k) or "") for k in ("folder", "title"))
    if cc:
        if re.search(r"[\u3040-\u30ff]", named_in):               # kana: Japanese only
            asian = {"JP"}
        elif re.search(r"[\u4e00-\u9fff]", named_in):             # kanji/hanzi alone
            asian = {"JP", "CN", "TW", "HK"}
        else:
            asian = set()
        if asian and cc in asian:
            clues.setdefault("country", "the Japanese/Chinese title in your folder name")
        elif asian and cc != "XW":
            against.append(f"folder name says JP (its title is in Japanese/Chinese), this pressing is from "
                           f"{cand.get('country')}")
    if cand.get("script") == "Latn" and not re.search(r"[^\x00-\u024f\s\W\d]", str(album.get("title") or "")):
        clues.setdefault("script", "listed in Latin script, like your tags")
    td, cd = "" if by_tagger else str(album.get("tag_date") or ""), str(cand.get("date") or "")
    if len(td) >= 7 and len(cd) >= 7 and td[:len(cd)] == cd[:len(td)]:
        clues.setdefault("date", f"release date {cd} in the tags")
    # folder name: "[Century Media, 9976158, Germany]"
    folder = _bracket_text(album.get("folder"), pressing_only=True)
    if folder:
        fw = " " + fold(folder) + " "
        for x in str(cand.get("label") or "").split(";"):
            phrase = " ".join(w for w in fold(x).split() if w not in _LABEL_FILLER)
            if len(phrase) >= 4 and f" {phrase} " in fw:
                clues.setdefault("label", f"label {x.strip()} in the folder name")
                break
        named = {c for n, c in _COUNTRY_NAMES.items() if len(n) > 2 and f" {n} " in fw}
        named |= {c for n, c in (("usa", "US"), ("uk", "GB"), ("eu", "XE")) if f" {n} " in fw}
        # "US" as a field of its own, in capitals: "[Relapse Records, US, RR 7157-2, LTD Edn]"
        # (never the word "us" of a title)
        if any(f.strip() == "US" for f in re.split(r"[,;]", folder)):
            named.add("US")
        # ... and likewise another country's code: "(2024, SACD, Sony, SICP 10151-4, JP, 2.0)"
        named |= {_FIELD_COUNTRIES[f.strip()] for f in re.split(r"[,;]", folder) if f.strip() in _FIELD_COUNTRIES}
        if named and cc and cc != "XW":
            if cc in named:
                clues.setdefault("country", f"{_readable_country(cc)} in the folder name")
            elif not (cc == "XE" and named & _EUROPE) and not ("XE" in named and cc in _EUROPE):
                # "Germany" on a European pressing (made there, sold across Europe) agrees
                against.append(f"folder name says {'/'.join(sorted(named))}, "
                               f"this pressing is from {cand.get('country')}")
    # the packaging your scans are named after: digipak, digibook, jewel case
    mine = set(album.get("packaging") or ())
    theirs = cand.get("packaging") or ""
    if mine and theirs:
        if theirs in mine:
            clues.setdefault("packaging", f"{theirs} packaging, like your scans")
        elif len(mine) == 1 and theirs != "gatefold" and "gatefold" not in mine:
            against.append(f"your scans show a {next(iter(mine))}, this pressing is a {theirs}")
    # what it was made from: folder name, then the audio itself
    hint = album.get("media_hint") or ""
    audio = album.get("audio") or {}
    if formats:
        if hint and hint in formats:
            clues.setdefault("format", f"{hint} release (folder name)")
        elif not hint and "cassette" in formats and _CASSETTE_HINT.search(str(album.get("folder") or "")):
            clues.setdefault("format", "cassette release (folder name)")
        elif hint in ("vinyl", "cd") and hint not in formats:
            against.append(f"folder name says {hint}, this pressing is {'/'.join(sorted(formats))}")
        if audio.get("hires") and hint != "vinyl":
            if audio.get("sacd") and cand.get("sacd_media"):
                # MED-3: DSD, or 88.2/176.4 kHz (DSD64 converted), is an SACD rip
                clues.setdefault("format", SACD_AUDIO_CLUE)
            elif "digital" in formats:
                clues.setdefault("format", HIRES_DIGITAL_CLUE)
            elif formats == {"cd"} and not cand.get("hires_media"):
                # an SACD, DVD-Audio or Blu-ray counts as a CD here, and does carry hi-res
                # audio; the release's own format says so, never the folder name ("[SACD]"
                # does not make the plain CD of it one)
                against.append("hi-res audio cannot come from this CD")
        elif audio.get("cd_frames") and not hint and "cd" in formats:
            clues.setdefault("format", SECTOR_LOG_CLUE if album.get("rip_log_toc") else SECTOR_CLUE)
        elif audio.get("not_cd") and not hint and "digital" in formats and not album.get("foreign_logs"):
            # (a rip log of another copy in the folder: these files may be a re-split or
            # re-encode of that CD rip as easily as a download; Cynic - Re-Traced)
            clues.setdefault("format", "digital release (lossless tracks are not whole CD sectors, so not ripped from a CD)")
        elif album.get("rip_log_toc") and hint in ("", "cd") and "cd" in formats:
            # an EAC/XLD log with the disc's table of contents: ripped from a CD,
            # whatever the files were converted to since (Nile - Amongst the Catacombs: opus)
            clues.setdefault("format", "CD (the folder's rip log has the disc's table of contents)")
        format_against(album, formats, against)
        # a download sold only in a lossy codec is not your lossless files (an MP3 entry and a
        # FLAC entry of one download: Pallas - The Edge Of Time); one in your files' kind agrees
        codecs = set(cand.get("file_codecs") or ())
        if codecs and formats == {"digital"} and audio.get("lossless"):
            if codecs <= LOSSY_CODECS:
                against.append(f"{LOSSY_ONLY_AGAINST}{'/'.join(sorted(codecs)).upper()} only")
            elif codecs & LOSSLESS_CODECS:
                clues.setdefault("format", f"lossless download ({'/'.join(sorted(codecs & LOSSLESS_CODECS)).upper()}), "
                                           "like your files")
        store = download_store(album.get("folder"))
        if store and hint != "vinyl":
            # a shop named in the folder ("[ProStudioMasters]"): bought as a download
            if "digital" in formats:
                clues.setdefault("format", f"digital release (bought from {store}, per the folder name)")
            else:
                against.append(f"folder name says it was bought from {store}, "
                               f"this pressing is {'/'.join(sorted(formats))}")
        # scans of a back cover, tray card, booklet or disc: the copy is physical
        scans = album.get("scans") or {}
        digital_audio = (audio.get("hires") and hint != "vinyl") or audio.get("not_cd") or hint == "digital"
        if scans.get("physical") and not digital_audio:
            if formats & {"cd", "vinyl", "cassette"}:
                clues.setdefault("physical", f"physical release (your folder has {scans.get('why')})")
            elif formats == {"digital"}:
                against.append(f"your folder has {scans.get('why')}, so not this digital release")


HIRES_DIGITAL_CLUE = "digital release (hi-res audio, which no CD carries)"
SACD_AUDIO_CLUE = "SACD (DSD or 88.2/176.4 kHz audio: an SACD rip)"


# Discs that carry hi-res audio, though media_kind calls an SACD a CD
_HIRES_DISC = re.compile(r"sacd|dvd[- ]?a(?:udio)?\b|blu-?ray|\bdsd", re.I)


_SACD_DISC = re.compile(r"sacd|\bdsd", re.I)


def sacd_media(names):
    """True when one of these database format names is an SACD (MED-3)."""
    return any(_SACD_DISC.search(str(n or "")) for n in names)


def hires_media(names):
    """True when one of these database format names is a disc that carries hi-res audio
    (SACD, Hybrid SACD, SHM-SACD, DVD-Audio, Blu-ray)."""
    return any(_HIRES_DISC.search(str(n or "")) for n in names)


SECTOR_CLUE = "CD (tracks are whole CD sectors, as ripped from a disc)"
SECTOR_LOG_CLUE = "CD (tracks are whole CD sectors, and the folder's rip log has the disc's table of contents)"


def format_against(album, formats, against):
    """What the copy was made from against what this pressing is, whatever else agrees:
    a rip log or whole CD sectors is never a digital, vinyl or cassette release, and
    lossless tracks that are not whole CD sectors never came off this CD."""
    hint = album.get("media_hint") or ""
    audio = album.get("audio") or {}
    if not formats or hint not in ("", "cd"):
        return                  # the folder name says vinyl or a download: it decides (file_clues)
    # A rip log beside hi-res audio is left from an earlier CD rip: no CD carries that
    # audio (Cynic - Traced in Air: 24-bit files and a 2008 CD log, the 2019 download)
    log = album.get("rip_log_toc") and not audio.get("hires")
    # Bell Witch - Mirror Reaper: whole sectors AND an EAC log, against a File/MP3 entry
    if log and formats == {"digital"}:
        against.append("your folder has a CD rip log, so not this digital release")
    # BTBAM - The Great Misdirect: a CD rip with its log, picked as the 2LP
    if (log or audio.get("cd_frames")) and formats <= {"vinyl", "cassette"}:
        why = "a CD rip log" if log else "tracks that are whole CD sectors"
        against.append(f"your folder has {why}, so not this {'/'.join(sorted(formats))} release")
    # BTBAM - Colors: a download, whose packaging barcode was read off a scan of the CD
    if audio.get("not_cd") and not hint and formats == {"cd"}:
        against.append("lossless tracks are not whole CD sectors, so not ripped from this CD")


def exact_lengths(tied):
    """The one pressing whose track lengths fit every track where the others' do not, as
    (pressing, why, kind) or None, kind "ms" or "close". Two ways, both track by track:
    - lengths to the millisecond: within 0.05 s on every track, the others 0.02 s or more
      off on every track and further off than it (Bell Witch - Demo 2011: the 2011 download
      and the 2015 reissue, 30 ms apart on each track);
    - within 1 s on every track, the others 2 s or more off on some tracks (BTBAM - Alaska:
      the 2020 remix is 2-3 s off the 2005 mix on five tracks of eleven), on two tracks
      or more."""
    if len(tied) < 2 or any(not s.get("len_diffs") for s in tied) or len({len(s["len_diffs"]) for s in tied}) > 1:
        return None
    best = min(tied, key=lambda s: max(s["len_diffs"]))
    top = max(best["len_diffs"])
    others = [s for s in tied if s is not best]
    if (top <= 0.05 and all(s.get("ms_lengths") for s in tied)
            and all(min(s["len_diffs"]) >= max(0.02, top + 0.02) for s in others)):
        worst = min(min(s["len_diffs"]) for s in others)
        return best, (f"track lengths match to the millisecond (every track within {top:.3f}s, "
                      f"others {worst:.3f}s+ off on every track)"), "ms"
    # two tracks at least: one entry 2 s off on one track is often a typing slip or a
    # pregap counted elsewhere, not another mix
    if top <= 1.0 and all(sum(d >= 2.0 for d in s["len_diffs"]) >= 2 for s in others):
        worst = min(sorted(s["len_diffs"])[-2] for s in others)
        return best, (f"every track length within {top:.1f}s (others {worst:.1f}s+ off on two tracks or more)"), "close"
    return None


def _relative_clues(tied):
    """Clues that only make sense against the other pressings: whose track
    lengths are clearly closest, whose track titles agree with more tracks."""
    extra = {id(s): {} for s in tied}
    errs = sorted((s.get("len_err"), id(s)) for s in tied if s.get("len_err") is not None)
    if len(errs) == len(tied) and len(errs) >= 2 and errs[0][0] <= 1.0 and errs[1][0] >= errs[0][0] + 1.5:
        extra[errs[0][1]]["track lengths"] = (f"track lengths closest (off by {errs[0][0]:.1f}s on average, "
                                              f"others {errs[1][0]:.1f}s+)")
    else:
        exact = exact_lengths(tied)
        if exact:
            extra[id(exact[0])]["exact lengths"] = exact[1]
    titles = sorted(((s.get("titles_agree") or 0), id(s)) for s in tied)
    if len(titles) >= 2 and titles[-1][0] >= titles[-2][0] + 2:      # one title may just be spelt differently
        extra[titles[-1][1]]["track titles"] = f"{titles[-1][0]} track titles agree (others {titles[-2][0]})"
    return extra


def prefer_country(tied, order):
    """Last resort, as Picard's 'preferred release countries': among pressings
    nothing else tells apart, the one from the first country in `order`."""
    if not order:
        return None
    pool = [s for s in tied if not s.get("clues_against")]
    for code in order:
        hits = [s for s in pool if country_code(s["cand"].get("country")) == code]
        if len(hits) == 1:
            return hits[0], {"country preference": f"your preferred country order ({', '.join(order)}) "
                                                   f"puts {_readable_country(code)} first"}
        if len(hits) > 1:
            # TIE-2: the original release date breaks a tie within that country only
            first = prefer_original(hits)
            if first:
                return first[0], {"country preference": f"your preferred country order puts "
                                                        f"{_readable_country(code)} first",
                                  **first[1]}
            return None                                # several from that country: still undecided
    return None


# Picard's Options > Metadata > Preferred Releases, beside the country order: release types
# (preferred, in order, and discouraged) and medium formats. Picard 3 adds them to every
# release's score with a small weight (picard/matching.py, _compare_to_release_parts: the
# "preferences" tier, "tie-breaking discriminators"); here they never touch a score. They
# only choose between pressings of one album (same_album: one release group or Discogs
# master) that nothing about your copy, your tags, the earliest date or your country order
# told apart, and such a pick is a guess (FALLBACK_PICKS): never automatic. The order of the
# guesses: same pressing listed twice, the original pressing, your country order, then your
# release types, then your formats.
def release_preferences(cfg):
    """{"types", "discouraged", "formats"} from match_core's settings (lists of names)."""
    def names(k):
        v = (cfg or {}).get(k) or []
        return [str(x).strip() for x in (v if isinstance(v, (list, tuple)) else [v]) if str(x).strip()]
    return {"types": names("preferred_release_types"), "discouraged": names("discouraged_release_types"),
            "formats": names("preferred_release_formats")}


def release_type_score(cand, preferred, discouraged):
    """Picard 3's score for the release's types (picard/matching.py,
    _weights_from_preferred_release_types): each of its types (primary and secondary) scores
    0.5, a preferred one 0.5 + 0.5 * (n - i) / n by its place i among the n preferred, and the
    mean is the score; a discouraged type makes it -1 (Picard skips that release unless
    nothing else is left). None when the entry lists no types (unknown)."""
    types = [str(t).strip().casefold() for t in cand.get("release_types") or () if str(t).strip()]
    if not types:
        return None
    pref = [str(t).strip().casefold() for t in preferred or ()]
    if any(t in {str(d).strip().casefold() for d in discouraged or ()} for t in types):
        return -1.0
    n = len(pref)
    return sum(0.5 + 0.5 * (n - pref.index(t)) / n if t in pref else 0.5 for t in types) / len(types)


# Picard's generic medium format names (picard/const/__init__.py, RELEASE_FORMATS), one per
# kind (media_kind). A sub-format (SACD, Hybrid SACD, SHM-CD, HDCD, CD-R, Blu-spec CD,
# Enhanced CD...; a sized '7"', '10"' or '12" Vinyl') has the same kind, so a kind can never
# say whether a pressing is one.
GENERIC_FORMATS = {"cd": "cd", "vinyl": "vinyl", "digital media": "digital", "cassette": "cassette"}
# The generic names a pressing may list for itself: Picard's, and Discogs's "File" (Discogs
# names a format "CD", "Vinyl", "File" or "Cassette" and puts the size in its descriptions)
_OWN_GENERIC = dict(GENERIC_FORMATS, file="digital")


def release_format_score(cand, preferred, by_name=None):
    """Picard 3's score for the release's medium formats (picard/matching.py,
    _weights_from_preferred_formats): each medium scores (n - i) / n by its format's place i
    among the n preferred (0 when not listed), and the mean is the score. by_name True: the
    entry's own format names per medium ("media_formats": "CD", '12" Vinyl') are compared as
    Picard compares them. by_name False (or None and the entry lists no names): its kind
    ("formats": cd, vinyl, digital, cassette) is compared with the preferred formats of that
    kind, and only when that is ONE generic Picard name ("CD", "Vinyl", "Digital Media",
    "Cassette"): a preferred sub-format of the kind (SACD for a CD, '12" Vinyl' for a vinyl)
    or two of them, an entry naming a sub-format for itself (SACD, '7" Vinyl'), or an entry
    of more than one kind, is unknown. None when unknown."""
    n = len(preferred or ())
    names = [str(f).strip().casefold() for f in cand.get("media_formats") or () if str(f).strip()]
    pref = [str(f).strip().casefold() for f in preferred or ()]
    if by_name or (by_name is None and names):
        if not names:
            return None
        return sum((n - pref.index(f)) / n if f in pref else 0.0 for f in names) / len(names)
    kinds = {k for k in cand.get("formats") or () if k}
    if len(kinds) != 1:
        return None                                    # none, or the media are of several kinds
    kind = next(iter(kinds))
    listed = [(i, f) for i, f in enumerate(pref) if media_kind(f) == kind]
    if not listed:
        return 0.0
    if len(listed) > 1 or GENERIC_FORMATS.get(listed[0][1]) != kind:
        return None                                    # the kind cannot tell which of them it is
    if any(_OWN_GENERIC.get(f) != kind for f in names):
        return None                                    # it names a sub-format (SACD, '7" Vinyl')
    return (n - listed[0][0]) / n


def same_album(pool):
    """True when every entry is known to be a pressing of one album: they share one
    MusicBrainz release group ("release_group") or one Discogs master ("master_id"), or,
    across the two databases, each MusicBrainz entry's release group links the Discogs
    master ("discogs_master": the master's ID in the release group's Discogs URL
    relationship) that every Discogs entry belongs to. An entry without them is not known
    to be the same album."""
    for key in ("release_group", "master_id"):
        vals = {str(s["cand"].get(key) or "").strip() for s in pool}
        if len(vals) == 1 and "" not in vals:
            return True
    vals = {str(s["cand"].get("master_id" if s["cand"].get("source") == "discogs" else "discogs_master")
                or "").strip() for s in pool}
    return (len(vals) == 1 and "" not in vals
            and len({s["cand"].get("source") == "discogs" for s in pool}) == 2)


# The notes the type and format tie-breaks leave (validate.py reads a pick with one of
# them as a tie-break, as FALLBACK_PICKS' keys)
FALLBACK_NOTES = ("your preferred release types", "your preferred medium formats")


def prefer_type_and_format(tied, prefs, by_name=None):
    """After the country order, as Picard's preferred release types and then medium formats
    (Picard weighs a release's types 5 to 7 times its format: picard/cluster.py and
    picard/file.py, the "releasetype" and "format" weights): among the tied pressings nothing
    contradicts, the ONE that scores best on its types, else on its formats. Only between
    pressings of ONE album (same_album: one release group or Discogs master), never between
    albums. Every one of them must list what is compared (an entry without types or formats
    is unknown, not worse), and all are compared the same way: by format name only when
    every one lists its names and all come from MusicBrainz, whose names Picard's setting
    uses (by_name None), else by kind (by_name False). A pressing of a discouraged type is
    out before the formats are compared, even when another's types are unknown (Picard
    skips it); it is never picked. With no preference set (Picard's default), or a tie, None."""
    prefs = prefs or {}
    pool = [s for s in tied if not s.get("clues_against")]
    if len(pool) < 2 or not same_album(pool):
        return None
    if by_name is None:
        by_name = all(s["cand"].get("media_formats") and s["cand"].get("source") == "mb" for s in pool)
    steps = []
    if prefs.get("types") or prefs.get("discouraged"):
        steps.append(("release type preference",
                      lambda c: release_type_score(c, prefs.get("types"), prefs.get("discouraged")),
                      lambda c: "/".join(c.get("release_types") or ()),
                      FALLBACK_NOTES[0] + (f" ({', '.join(prefs['types'])})" if prefs.get("types") else "")
                      + (f", not {', '.join(prefs['discouraged'])}" if prefs.get("discouraged") else "")))
    if prefs.get("formats"):
        steps.append(("format preference", lambda c: release_format_score(c, prefs["formats"], by_name),
                      lambda c: ", ".join(c.get("media_formats") if by_name else sorted(c.get("formats") or ())),
                      f"{FALLBACK_NOTES[1]} ({', '.join(prefs['formats'])})"))
    for key, score, what, why in steps:
        vals = [(score(s["cand"]), s) for s in pool]
        if not any(v is None for v, _s in vals):       # else one of them does not say: unknown
            vals.sort(key=lambda x: -x[0])
            if vals[0][0] > vals[1][0]:
                pick = vals[0][1]
                return pick, {key: f"{why} put {what(pick['cand'])} first; nothing about your copy tells "
                                   f"the pressings apart"}
        if key == "release type preference":
            # a discouraged one is out before the formats, whatever the others' types
            pool = [s for v, s in vals if v != -1.0]
            if len(pool) < 2:
                return None
    return None


def _date_key(cand):
    """A release date as a sortable (year, month, day); missing parts sort last
    within their year, so '2003' never beats '2003-07-07' by being shorter."""
    parts = [int(x) for x in re.findall(r"\d+", str(cand.get("date") or ""))[:3]]
    if not parts and cand.get("year"):
        parts = [int(cand["year"])]
    if not parts or not 1900 <= parts[0] <= 2100:
        return None
    return tuple(parts + [99] * (3 - len(parts)))


def prefer_original(tied):
    """Between pressings nothing about your copy tells apart, the label's original:
    the first pressing released. Only a pressing strictly earlier than every other
    one counts; two first released on the same day (or in the same year with no
    day given) stay undecided, and your country order decides between them."""
    if any(s.get("pressing_year") for s in tied):
        return None                       # the folder names an edition year: not the original
    pool = [s for s in tied if not s.get("clues_against")]
    if len(pool) < 2:
        return None
    on_year = [s for s in pool if not s.get("year_off")]
    if on_year and len(on_year) < len(pool):
        pool = on_year                    # the folder's year rules out the others
        if len(pool) == 1:
            return None                   # already told apart by the year; not this rule's call
    dated = [(_date_key(s["cand"]), s) for s in pool]
    if any(k is None for k, _s in dated):
        return None                       # an undated pressing could be the original
    dated.sort(key=lambda x: x[0])
    (first, pick) = dated[0]

    def surely_before(a, b):                 # 99 = not given: unknown, not late
        for x, y in zip(a, b):
            if x == 99 or y == 99:
                return False
            if x != y:
                return x < y
        return False
    if not all(surely_before(first, k) for k, _s in dated[1:]):
        return None
    c = pick["cand"]
    what = ", ".join(x for x in (c.get("label") or "", _readable_country(c.get("country")) if c.get("country")
                                 else "", str(c.get("date") or c.get("year") or "")) if x)
    return pick, {"original pressing": f"the label's original pressing, released first ({what}); "
                                       f"nothing about your copy tells the pressings apart"}


def _weigh(tied):
    """Each tied pressing's telling clues and their weight, heaviest first: only clues that
    tell the tied pressings apart count (a label shared by all of them says nothing)."""
    rel = _relative_clues(tied)
    allc = {id(s): dict(s.get("clues") or {}, **rel[id(s)]) for s in tied}
    # A Discogs pressing "matches the disc ID" only because a MusicBrainz release with
    # that disc ID links to it; the other Discogs pressings of the same master simply
    # lack such a link (Alcest - Les Chants de l'aurore: the Brazilian licence was picked
    # over Nuclear Blast's own CD). So among Discogs pressings it tells nothing apart.
    # MED-3: an SACD that fits SACD-rate audio among them: "not a CD" no longer says download
    if any(str((x.get("clues") or {}).get("format") or "") == SACD_AUDIO_CLUE for x in tied):
        for x in tied:
            if allc[id(x)].get("format") == HIRES_DIGITAL_CLUE:
                allc[id(x)].pop("format")
    for s in tied:
        if s["cand"].get("source") == "discogs":
            allc[id(s)].pop("disc TOC", None)
        # A fuzzy disc ID match ("a CD of about these lengths") tells no pressing of the album
        # from another: the one that has disc IDs submitted would always win (Q5)
        allc[id(s)].pop("disc TOC (fuzzy)", None)
        # Nor does an ISRC: it is the recording's code, shared by every pressing that
        # carries the recording, and only lists which pressings someone submitted it for
        allc[id(s)].pop("isrc", None)
        allc[id(s)].pop("fingerprint", None)        # (recordings, as an ISRC: every pressing's)
    # A disc code read for one pressing tells it apart only when every tied pressing lists
    # a code of that kind: an entry without one is unknown, not different (a label code is
    # the same on all the label's pressings, often entered for one of them only).
    listed = {k for k in set(MATRIX_KINDS.values())
              if all(any(c == k for c, _v in cand_matrices(s["cand"])) for s in tied)}
    # Likewise the script a pressing is listed in: an entry without one (a Discogs entry,
    # a MusicBrainz release nobody set it on) is unknown, not in another script.
    scripted = all(s["cand"].get("script") for s in tied)
    # And a catalogue number read on your scans: an entry listing no catalogue number at all
    # is unknown, not another number (Jon Hassell - Last Night The Moon..., 24/96: the
    # download's booklet shows ECM 2077, which the download's entry does not list; the
    # number then picked a 3-disc vinyl box over the download your folder names)
    # (a download's entry only: a CD or LP entry without its number is often just incomplete)
    catalogued = all(s["cand"].get("catnos") or set(s["cand"].get("formats") or ()) != {"digital"}
                     for s in tied)

    def scans_number(k, v):
        return k == "catalogue number" and str(v).endswith(" on the scans")
    scored = []
    for s in tied:
        mine = allc[id(s)]
        telling = {k: v for k, v in mine.items()
                   if not all(allc[id(o)].get(k) == v for o in tied)
                   and (k not in MATRIX_KINDS.values() or k in listed)
                   and (k != "script" or scripted)
                   and (catalogued or not scans_number(k, v))}
        # "cover" weighs 0: the embedded front cover is often the art a tagger fetched for
        # the release it guessed, so it never picks a pressing, only shows beside clues that do
        weight = sum(CLUE_WEIGHTS.get(k, 1) for k in telling)
        scored.append((weight, telling, s))
    scored.sort(key=lambda x: -x[0])
    return scored


def _undated_edition(edition, undated):
    """An undated pressing (no date is unknown, not another year) with strictly more
    evidence about your copy than every other pressing of the folder's edition, or None
    (Slayer - Seasons In The Abyss [RM 2015]: the undated 24/192 download against a 2013 LP
    the year tolerance lets in)."""
    scored = _weigh(edition + undated)
    weight, telling, pick = scored[0]
    if (pick not in undated or weight == 0 or scored[1][0] >= weight
            or (all(s.get("own_catno_absent") for s in edition + undated) and weight < CLUE_WEIGHTS["disc TOC"])):
        return None
    return pick, dict(telling, date=f"undated, so not ruled out by the {pick['pressing_year']} edition "
                                    f"your folder names")


# What says "this is your copy" by its own number, compared by the number and not its kind
_OWN_NUMBERS = {"barcode", "catalogue number", "code", "folder catalogue number"}


def prefers_vinyl_or_tape(formats) -> bool:
    """Your preferred medium formats put vinyl or cassette ahead of every CD: you asked for
    them, so no_media_evidence does not set them aside."""
    kinds = [media_kind(f) for f in formats or ()]
    first = next((k for k in kinds if k in ("cd", "vinyl", "cassette")), "")
    return first in ("vinyl", "cassette")


def copy_kind(album) -> str:
    """What your copy is, as far as the folder name or the audio says: "cd", "vinyl",
    "cassette", "digital", or "" (unknown; hi-res audio may be a vinyl rip or a download)."""
    hint = album.get("media_hint") or ""
    if hint in ("cd", "vinyl", "cassette", "digital"):
        return hint
    audio = album.get("audio") or {}
    if audio.get("not_cd"):
        return "digital"
    if audio.get("cd_frames") or album.get("rip_log_toc"):
        return "cd"
    return ""


def kind_fits(s) -> bool:
    """TIE-1: the pick is of the kind your copy is (or either is unknown). When it is not,
    your pressing is not listed among the tied ones: the album is known, not the pressing."""
    kind, formats = s.get("copy_kind") or "", set(s["cand"].get("formats") or ())
    return not kind or not formats or kind in formats


def no_media_evidence(tied):
    """The vinyl and cassette pressings among those tied that nothing about your copy points
    to, when a CD of this album your copy does not contradict is among them: with no evidence
    for those media the copy is a CD. Evidence is what no such CD also has: the folder name or
    your tags naming the media, a number, label or country of yours on that pressing alone, its
    packaging in your scans, its lengths or titles fitting where the CDs' do not, your files
    already tagged with it, or the edition year your folder names. An entry of several media or
    none is left alone, and so is a folder that says vinyl or a download. When your copy's
    catalogue number is on none of them, your pressing may not be among them."""
    if len(tied) < 2 or all(s.get("own_catno_absent") for s in tied):
        return []
    if any(s.get("copy_hint") in ("vinyl", "digital") for s in tied):
        return []
    cds = [s for s in tied if "cd" in (s["cand"].get("formats") or ()) and not s.get("clues_against")
           and not s.get("other_version") and not s.get("conflict") and s.get("tracks_ok")
           and not s.get("discs_off") and not s["cand"].get("unofficial")]
    if not cds:
        return []

    def shown(s):
        out = set(_evidence(s)) | ({"packaging"} if (s.get("clues") or {}).get("packaging") else set())
        out |= set(MATRIX_KINDS.values()) & set(s.get("clues") or ())
        return out - {"physical"} - _OWN_NUMBERS
    on_a_cd = set().union(*(shown(c) for c in cds))
    edition_cd = any(c.get("pressing_year") and c.get("year_ok") for c in cds)

    def same_number(s, c):
        return s.get("code") == c.get("code") and bool(
            (set(s["cand"].get("catnos") or ()) & set(c["cand"].get("catnos") or ()))
            | (set(s["cand"].get("barcodes") or ()) & set(c["cand"].get("barcodes") or ())))

    def unproven(s):
        kinds = set(s["cand"].get("formats") or ())
        if not kinds or not kinds <= {"vinyl", "cassette"}:
            return False
        if s.get("tagged") and not s.get("tagger_tagged"):
            return False
        if s.get("pressing_year") and s.get("year_ok") and not edition_cd:
            return False
        if _carries_barcode(s) and not any(_carries_barcode(c) for c in cds):
            return False
        if _OWN_NUMBERS & set(_evidence(s)) and not any(same_number(s, c) for c in cds):
            return False
        if not any(title_sim(c["cand"]["title"], s["cand"]["title"]) >= 0.85 for c in cds):
            return False                        # no CD of the same album
        return not (shown(s) - on_a_cd) and not _relative_clues([s] + cds)[id(s)]
    return [s for s in tied if unproven(s)]


def _against_key(text):
    """The part of a clue against a pressing that is about your copy ("tags say GB" of
    "tags say GB, this pressing is from DE"): only your tags' own country or media."""
    text = str(text or "")
    return text.split(", this pressing", 1)[0] if text.startswith("tags say ") else None


def _recordable(cand):
    """The pressing is a CD-R (recordable, burned): it carries no moulded matrix."""
    names = [str(f).casefold().replace("-", "") for f in cand.get("media_formats") or ()]
    return bool(names) and all(n in ("cdr", "cdrw") for n in names)


def choose_pressing(tied, preferred_countries=None, prefs=None):
    """_choose_pressing on what tells the tied pressings apart:

    - without what your tags say against EVERY tied pressing alike (EVI-2: evidence shared
      by every candidate tells nothing apart - a release country in your tags that none of
      them is, written by another tagger, must not outweigh your exact disc ID or keep the
      country order from choosing);
    - with a pressed matrix your disc shows, read for a tied pressing, against a tied CD-R
      that lists none (a burned disc carries no moulded runout: Pallas - wearewhoweare's
      "398478 @1 MTS5503" is the jewel-case CD, not the CD-R of the same release).

    The pick returned is the caller's own entry."""
    if len(tied) < 2:
        return _choose_pressing(tied, preferred_countries, prefs)
    keys = [{_against_key(a) for a in s.get("clues_against") or ()} - {None} for s in tied]
    shared = set.intersection(*keys) if keys else set()
    matrix = next((s["clues"]["matrix"] for s in tied if (s.get("clues") or {}).get("matrix")), "")
    burned = {id(s) for s in tied if matrix and _recordable(s["cand"])
              and not any(c == "matrix" for c, _v in cand_matrices(s["cand"]))}
    # MED-10: your lossless files are none of the downloads sold only in a lossy codec: those
    # leave the tie (an MP3 and a FLAC entry of one download: Pallas - The Edge Of Time)
    lossy = [s for s in tied if any(str(a).startswith(LOSSY_ONLY_AGAINST) for a in s.get("clues_against") or ())]
    if lossy and len(lossy) < len(tied):
        rest = [s for s in tied if not any(s is x for x in lossy)]
        # (one left: it, only when nothing of yours is against it either - your catalogue
        # number on none of them, hi-res audio and a CD - else the whole tie decides as before)
        if len(rest) == 1 and not rest[0].get("clues_against") and \
                not all(s.get("own_catno_absent") for s in tied):
            why = next(a for a in lossy[0]["clues_against"] if str(a).startswith(LOSSY_ONLY_AGAINST))
            return rest[0], {"format": f"{why}; this one is not"}
        if len(rest) > 1:
            return choose_pressing(rest, preferred_countries, prefs)
    if not shared and not burned:
        return _choose_pressing(tied, preferred_countries, prefs)
    view = []
    for s in tied:
        against = [a for a in s.get("clues_against") or () if _against_key(a) not in shared]
        if id(s) in burned:
            against.append(f"your disc shows a pressed {matrix.split(' on the scans')[0]}; this pressing is a CD-R")
        view.append(dict(s, clues_against=against))
    back = {id(v): s for v, s in zip(view, tied)}
    got = _choose_pressing(view, preferred_countries, prefs)
    if not got:
        return None
    return (back.get(id(got[0]), got[0]),) + tuple(got[1:])


def _choose_pressing(tied, preferred_countries=None, prefs=None):
    """Among pressings that score the same, the one the evidence points to, or None.

    prefs (release_preferences): Picard's preferred release types and formats, the very
    last tie-break, after the country order, and only between pressings of one album
    (same_album); like the country order, only when a country order is given
    (match_albums switches the country order off until every kind of evidence was tried).

    Only clues that tell the tied pressings apart count (a label shared by all
    of them says nothing). Clues are weighed (a disc ID, barcode or catalogue
    number counts most); the pick must outweigh every other pressing and must
    not contradict anything the files, folder or scans say."""
    if len(tied) < 2:
        return None
    # A remix, live or orchestral release the folder and tags do not name is another
    # recording, not a pressing: it never blocks the tie-break between real pressings
    # (Ihsahn - Ihsahn: the orchestral download tied the 24/48 download on its format clue)
    # One real pressing left is no tie to narrow: the whole tie is weighed as before, so the
    # real one still wins on its own clues (and the other version cannot make it None).
    real = [s for s in tied if not s.get("other_version")]
    if 2 <= len(real) < len(tied):
        tied = real
    # The edition your folder names ("[2021 RM]") rules out the pressings of other years
    # before anything else decides; the country order once picked Sepultura's 2006 US
    # Dante XXI over the 2021 remaster in BMG's box (85.7 over 90.5).
    # A pressing of that year your files contradict is not that edition: Slayer - Seasons
    # In The Abyss [RM 2015] is 24/192 audio, never the 2015 Japanese SHM-CD.
    edition = [s for s in tied if s.get("pressing_year") and s.get("year_ok") and not s.get("clues_against")]
    # an undated pressing is of no known year: it stays in when its evidence is stronger
    undated = [s for s in tied if s.get("pressing_year") and s.get("year_off") is None
               and not s.get("year_ok") and not s.get("clues_against")]
    if edition and undated:
        pick = _undated_edition(edition, undated)
        if pick:
            return pick
    if edition and len(edition) < len(tied):
        if len(edition) == 1:
            return edition[0], {"date": f"the {edition[0]['pressing_year']} edition your folder names"}
        tied = edition
    scored = _weigh(tied)
    weight, telling, pick = scored[0]
    # MED-4: hi-res audio says "not a CD", not "a download", when an SACD, DVD-Audio or
    # Blu-ray of the album ties too: with nothing else of yours, the pressing stays open
    # (album known), not picked by the country order
    if (telling and set(telling) == {"format"} and telling["format"] == HIRES_DIGITAL_CLUE
            and any(s["cand"].get("hires_media") and "digital" not in (s["cand"].get("formats") or ())
                    for s in tied)):
        return None
    if weight == 0 or scored[1][0] == weight or pick.get("clues_against"):
        if any(s.get("own_catno_absent") for s in tied) and all(s.get("own_catno_absent") for s in tied):
            return None                                # your pressing is not among them
        # Nothing tells them apart, and your files are already tagged with one of them:
        # keep it rather than guess another by country or date (Cult of Luna - Salvation,
        # tagged with the GB pressing, tied with the US one)
        # (and no other pressing outweighs it)
        fair = [(w, s) for w, _t, s in scored if not s.get("clues_against")]
        # (TagMender's own tags only: another tagger's IDs are its guess, not proof)
        mine = [s for w, s in fair if s.get("tagged") and not s.get("tagger_tagged")
                and w == max(x for x, _s in fair)]
        if len(mine) == 1:
            return mine[0], {"tagged release": "the release your files are tagged with; "
                                               "nothing tells the tied pressings apart"}
        # TIE-2: the country order before the original release date
        return (same_pressing_listed_twice(tied) or prefer_country(tied, preferred_countries)
                or prefer_original(tied)
                or (prefer_type_and_format(tied, prefs) if preferred_countries else None))
    if all(s.get("own_catno_absent") for s in tied) and weight < CLUE_WEIGHTS["disc TOC"]:
        return None              # your copy's catalogue number is on none of them: only hard proof decides
    return pick, telling


_VARIANT_NOTE = re.compile(r"\b(?:without|lacks?|lacking|missing|no)\s+(?:the\s+|an?\s+)?"
                          r"(?:o-?card|slip-?case|slip-?cover|obi|sticker|poster|booklet|outer\s+sleeve)", re.I)


def packaging_variant(cand):
    """A database note saying this entry is a packaging variant of a pressing ("Same as
    the first edition, but without the O-card"): the text, or ""."""
    m = _VARIANT_NOTE.search(str(cand.get("notes") or ""))
    return m.group(0) if m else ""


def same_pressing_listed_twice(tied):
    """Two database entries for ONE pressing (same label, catalogue number,
    barcode, country and format; often one entered with only a year): either is
    right, so take the one with the fuller data."""
    def key(s):
        c = s["cand"]
        return (tuple(sorted(c.get("catnos") or ())), country_code(c.get("country")),
                tuple(sorted(c.get("formats") or ())), fold(c.get("label") or ""))
    keys = {key(s) for s in tied}
    k = next(iter(keys))
    # barcodes must agree where both entries have one (an entry often lacks it)
    codes = {tuple(sorted(barcode_key(b) for b in s["cand"].get("barcodes") or ())) for s in tied} - {()}
    # a jewel case and a digipak with the same numbers are two products, not one listed twice
    packs = {s["cand"].get("packaging") for s in tied} - {"", None}
    if len(keys) != 1 or not k[0] or len(codes) > 1:
        return None
    # Packaging variants of one number: the packaging your scans show decides (Candlemass -
    # Dactylis Glomerata: GMR's O-card slipcase, 8910755, and the same CD "without the
    # O-card", 25872463); without it they are two products, not one listed twice.
    if len(packs) > 1 or any(packaging_variant(s["cand"]) for s in tied):
        shown = [s for s in tied if (s.get("clues") or {}).get("packaging")]
        if len(shown) == 1 and not shown[0].get("clues_against") and not _other_clues(shown[0], tied):
            return shown[0], {"packaging": shown[0]["clues"]["packaging"]}
        return None
    if any(s.get("clues_against") for s in tied):
        return None
    # the better-scoring entry first (Sleep's Holy Mountain [2015 ReIssue]: the entry
    # dated 2015 over one dated 1992 with the same barcode), then the fuller one
    pick = max(tied, key=lambda s: (s["confidence"], not s.get("year_off"),
                                    len(str(s["cand"].get("date") or "")), len(s["cand"].get("tracks") or ()),
                                    -tied.index(s)))
    return pick, {"duplicate": "the same pressing listed twice (same label, catalogue number and barcode); "
                               "the fuller entry is used"}


def _other_clues(pick, tied):
    """True when another of the tied entries has a clue of its own that `pick` lacks or
    reads otherwise (track lengths, a disc code): evidence the other way, which packaging
    alone does not outweigh."""
    rel = _relative_clues(tied)
    mine = dict(pick.get("clues") or {}, **rel[id(pick)])
    for o in tied:
        if o is not pick:
            theirs = dict(o.get("clues") or {}, **rel[id(o)])
            if any(k != "cover" and mine.get(k) != v for k, v in theirs.items()):
                return True
    return False


choose_pressing_from_scans = choose_pressing      # earlier name


_VERSION_WORDS = re.compile(r"\b(remix(?:ed|es)?|re-?recorded|re-?recording|instrumentals?|acoustic|unplugged|"
                            r"orchestral|demos?|karaoke|live|ambient|dub)\b", re.I)


def other_version(album, cand):
    """A word naming another version of the album in the release title ("Traced in Air
    Remixed", "... (Instrumental)", "... Live") that neither your folder name nor your
    tags carry: a different recording, however well the lengths fit. Returns the word."""
    mine = fold(" ".join(str(album.get(k) or "") for k in ("title", "folder")))
    for m in _VERSION_WORDS.finditer(fold(cand.get("title") or "")):
        w = m.group(1)
        stem = re.sub(r"(?:ed|es|s)$", "", w.replace("-", ""))
        if stem not in mine.replace("-", ""):
            return w
    return ""


def _box_disc(album, cand):
    """The disc of a multi-disc release that IS the album: the same number of tracks, every
    length within 3 s, and named like the album (its disc title or a box title that says
    nothing of it). Returned as a candidate of that disc alone, under the release's ID, so
    Picard tags the files as that disc of the box. None when no disc fits."""
    discs = {}
    for t in cand.get("tracks") or []:
        discs.setdefault(t.get("disc") or 1, []).append(t)
    if len(discs) < 2:
        return None
    files = album["tracks"]
    best = None
    for d, tr in sorted(discs.items()):
        if len(tr) != len(files) or any(not t.get("secs") for t in tr):
            continue
        off = max(abs(a["secs"] - t["secs"]) for a, t in zip(files, tr))
        name = (cand.get("disc_titles") or {}).get(d) or ""
        if off <= 3 and name and title_sim(album["title"], name) >= 0.85 and (best is None or off < best[0]):
            best = (off, d, tr, name)
    if best:
        _off, d, tr, name = best
        return dict(cand, tracks=[dict(t, disc=1) for t in tr], title=name, disc_count=1,
                    box_disc=d, box_title=cand.get("title") or "")
    # An untitled disc MusicBrainz attaches your exact disc ID to (Jesu - Sun Down / Sun
    # Rise: disc 2 of the Japanese Conqueror): the ID hashes the whole TOC, lead-out too,
    # so with the same track count (2 or more) and every length within 0.1 s it is this disc
    on = (album.get("exact_toc_media") or {}).get(cand.get("id")) or ()
    if cand.get("source") != "mb" or len(files) < 2:
        return None
    for d in sorted(on):
        tr = discs.get(d) or []
        if (len(tr) == len(files) and all(t.get("secs") for t in tr)
                and max(abs(a["secs"] - t["secs"]) for a, t in zip(files, tr)) <= 0.1):
            return dict(cand, tracks=[dict(t, disc=1) for t in tr], title=album["title"], disc_count=1,
                        box_disc=d, box_title=cand.get("title") or "", box_proof="disc ID")
    return None


def compact_numbers(numbers):
    """Track numbers as a short list: [2, 4, 5, 7, 8, 9, 10, 14] is "2, 4, 5, 7-10, 14"."""
    out, run = [], []
    for n in sorted(set(numbers)):
        if run and n == run[-1] + 1:
            run.append(n)
            continue
        if run:
            out.append(str(run[0]) if len(run) < 3 else f"{run[0]}-{run[-1]}")
            out += [str(x) for x in run[1:]] if len(run) == 2 else []
        run = [n]
    if run:
        out.append(str(run[0]) if len(run) < 3 else f"{run[0]}-{run[-1]}")
        out += [str(x) for x in run[1:]] if len(run) == 2 else []
    return ", ".join(out)


def missing_text(missing):
    """{disc: [track numbers]} as TAGMENDER_MISSING has it: "2, 4, 5, 7-10, 14" on a
    one-disc release, "1:3, 2:5" (disc:track) on a multi-disc one."""
    if not missing:
        return ""
    if len(missing) == 1 and set(missing) == {1}:
        return compact_numbers(missing[1])
    return ", ".join(f"{d}:{n}" for d in sorted(missing) for n in sorted(missing[d]))


def slot_view(album, cand):
    """Fewer files than the release has tracks, each numbered: the release seen as the
    tracks at the files' own numbers (file "011" is track 11), so each file is compared with
    its own slot. None when a file has no number, a number past its disc's tracks, or
    shares its slot with another file. The view keeps the release's ID; "slots" are the
    release tracks' positions (0-based, in the release's order) of the files, in theirs,
    and "missing" the release's tracks no file stands for ({disc: [numbers]})."""
    files, tracks = album.get("tracks") or [], cand.get("tracks") or []
    if not files or len(files) >= len(tracks) or any(t.get("video") for t in tracks):
        return None
    by_disc = {}
    for i, t in enumerate(tracks):
        by_disc.setdefault(t.get("disc") or 1, []).append(i)
    one_disc = len(by_disc) == 1
    # numbers that run on over the discs (disc 2 numbered 19-36 after disc 1's 18): each file of
    # such a disc counted from the disc's own start - when every file of that disc is past its
    # tracks and all of them land on them once the earlier discs' tracks are taken off
    # (Billy Joel - The Ultimate Collection: disc 1 lacks its track 18, disc 2 is 19-36)
    shift = {}
    if not one_disc:
        for d in sorted(by_disc):
            mine = [f.get("num") or 0 for f in files if (f.get("disc") or 1) == d]
            before = sum(len(by_disc[e]) for e in by_disc if e < d)
            if mine and before and all(n > len(by_disc[d]) for n in mine) and \
                    all(1 <= n - before <= len(by_disc[d]) for n in mine):
                shift[d] = before
    slots, taken = [], set()
    for f in files:
        disc = 1 if one_disc else (f.get("disc") or 1)
        num = (f.get("num") or 0) - shift.get(disc, 0)
        pos = by_disc.get(disc) or []
        if num < 1 or num > len(pos) or (disc, num) in taken:
            return None
        taken.add((disc, num))
        slots.append(pos[num - 1])
    missing = {d: [n for n in range(1, len(pos) + 1) if (d, n) not in taken] for d, pos in by_disc.items()}
    missing = {d: ns for d, ns in missing.items() if ns}
    return dict(cand, tracks=[tracks[i] for i in slots], slots=slots, missing=missing,
                full_tracks=len(tracks))


def _slots_why_not(album, s):
    """Why the files cannot be tagged as their own tracks of this release with the rest
    missing, or "": the release must be proven by more than its name (a disc ID or TOC,
    a barcode, or a catalogue number with the matrix), and every file must fit its slot
    by length (and by title where the files have titles)."""
    clues = s.get("clues") or {}
    proof = ("disc TOC" in clues or barcode_proves(s)
             or (s["code"].startswith("catalogue number") and s["code"] not in WEAK_BARCODES
                 and "matrix" in clues))
    if not proof:
        return "nothing but its name says it is this release"
    if s.get("conflict"):
        return "the barcode differs"
    n = len(album["tracks"])
    if s.get("dur_frac") != 1.0 or s.get("dur_cov") != 1.0:
        return "not every file's length fits its own track"
    # titles typed by hand: a typo, or the work's name before the movement's ("Pictures At
    # An Excibition for Group: Promenade" for "Promenade"), may still be the track's
    pairs = [(f["title"], c.get("title") or "") for f, c in zip(album["tracks"], s["cand"]["tracks"])
             if f.get("title")]
    agree = sum(1 for a, b in pairs if track_titles_agree(a, b) or track_titles_agree(a.rsplit(": ", 1)[-1], b))
    if pairs and agree < 0.9 * len(pairs):
        return "the files' titles do not fit their own tracks"
    return "" if n else "no files"


def _shop_sticker(album, cand, a_codes, c_codes, ocr_text=""):
    """A barcode read off the scans that no release anywhere carries (album
    "unlisted_barcodes": not on MusicBrainz, Discogs or in the shops) and that differs from
    this candidate's, while the candidate's catalogue number (from the tags, the rip's notes or
    the scans, not the folder name alone), matrix or disc ID agrees with your copy: a shop's
    price sticker (Mekong Delta - Pictures At An Exhibition, "CDVEST 78"
    5020083807827 beside the real 724348437624). Returned so it counts neither way; ""
    otherwise."""
    unlisted = {barcode_key(b) for b in album.get("unlisted_barcodes") or ()} - {""}
    if not (a_codes and c_codes and unlisted) or a_codes & c_codes or not a_codes <= unlisted:
        return ""
    hay = norm_code(ocr_text or "")
    # (a number only the folder name holds may be no catalogue number at all: the tags', the
    # rip's notes' or the scans' must agree)
    folder_only = set(album.get("catnos_from_folder") or ()) - set(album.get("catnos_typed") or ())
    agrees = (catnos_agree(set(album.get("catnos") or ()) - folder_only, cand.get("catnos"))
              or any(len(c) >= 5 and re.search(r"[a-z]", c) and re.search(r"\d", c) and c in hay
                     for c in cand.get("catnos") or ())
              or (cand.get("source") == "mb" and cand.get("id") in (album.get("toc_releases") or ()))
              or (cand.get("source") == "discogs" and str(cand.get("id")) in (album.get("toc_discogs") or ()))
              or "matrix" in pressing_clues(ocr_text or "", cand, album.get("ring_text") or "")[0])
    shown = sorted(str(b) for b in album.get("barcodes") or () if barcode_key(b) in a_codes)
    return ", ".join(shown) if agrees else ""


def with_pregap(album, cand):
    """The candidate with its discs' hidden tracks before track 1 (a MusicBrainz medium's
    "pregap", match_albums.mb_candidate "pregap_tracks") as their discs' first tracks, where
    the files have that track: one file numbered 0, first on its disc, every other file of
    the disc numbered from 1, about as long as the pregap. Else the candidate as it is: a
    rip without that file is compared with the release's tracks alone, as before.
    pregap_proven: a rip log's or CDTOC tag's TOC that fits the disc has that file in its first
    pregap (album "htoa_toc_discs"), for every disc whose pregap is used; without it (or the
    exact disc ID) the hidden track matched on its length only, which classify never takes
    automatically."""
    pregaps = cand.get("pregap_tracks") or ()
    if not pregaps:
        return cand
    discs = {}
    for t in album.get("tracks") or ():
        discs.setdefault(t.get("disc") or 1, []).append(t)
    used = []
    for p in pregaps:
        files = discs.get(p.get("disc") or 1) or []
        zero = [t for t in files if not t.get("num")]
        if (len(files) < 2 or len(zero) != 1 or files[0] is not zero[0]
                or any((t.get("num") or 0) < 1 for t in files[1:])):
            continue
        if p.get("secs") and abs((zero[0].get("secs") or 0) - p["secs"]) > 2:
            continue
        used.append(p)
    if not used:
        return cand
    tracks = []
    for t in cand.get("tracks") or ():
        tracks += [p for p in used if p.get("disc") == t.get("disc") and p not in tracks]
        tracks.append(t)
    proven = set(album.get("htoa_toc_discs") or ())
    return dict(cand, tracks=tracks, pregap_used=True,
                pregap_proven=all((p.get("disc") or 1) in proven for p in used))


def score_candidate(album, cand, cfg, ocr_text=""):
    """Score one candidate against one album. Returns a dict with the
    confidence (0-100), the evidence for it and every gate that failed."""
    pts = avail = 0.0
    notes = []
    # the hidden track before track 1, where the files have it as a file numbered 0
    cand = with_pregap(album, cand)

    a_sim = max(artist_sim(album["artist"], cand["artist"]),
                artist_sim(album["artist"], cand.get("artist_alt") or ""),
                artist_sim(album["artist"], cand.get("artist_sort") or ""))
    # learned from your library: albums by this artist are filed under that folder
    aliases = cfg.get("artist_aliases") or {}
    learned = [a for key in (album["artist"], album.get("artist_folder") or "")
               for k, lst in aliases.items() if fold(k) == fold(key) for a in lst]
    if a_sim < 0.85 and any(artist_sim(x, cand["artist"]) >= 0.85 or artist_sim(x, cand.get("artist_alt") or "") >= 0.85
                            for x in learned):
        a_sim = 1.0
        notes.append(f"you file {cand['artist']} under {album.get('artist_folder') or album['artist']}")
    n_file, n_cand = len(album["tracks"]), len(cand["tracks"])
    box_note = ""
    if n_file and n_file < n_cand and len({t.get("disc") for t in album["tracks"]}) == 1:
        # one disc of a box set ("The Legacy" from Testament - Original Album Series, folder
        # "(2013, Warner, 8122796510-1)"): the disc whose tracks and lengths are the files'
        box = _box_disc(album, cand)
        if box:
            cand = box
            n_cand = len(cand["tracks"])
            box_note = (f"disc {cand['box_disc']} of {cand['box_title']}")
    if n_file and not box_note and album.get("version_discs") and not cand.get("version_media"):
        # versions of one disc in folders of their own ("2.0/", "5.1/"): each its own tracks of
        # the release - an SACD's layer, its part of a DVD's or Blu-ray's tracklist
        view = version_view(album, cand)
        if view:
            cand = view
            n_cand = len(cand["tracks"])
    if cand.get("version_media"):          # (a view scored again: told again)
        fmts = {m.get("position"): m.get("format") for m in cand.get("media_info") or ()}
        labels = album.get("version_labels") or {}
        box_note = "; ".join([f"your {labels.get(d) or (album.get('version_discs') or {}).get(d, '')} folder is "
                              f"disc {m} ({fmts.get(m) or 'its format unnamed'})"
                              for d, m in sorted(cand["version_media"].items())] + list(cand.get("version_notes") or []))
    t_sim = title_sim(album["title"], cand["title"])
    pts += cfg["w_artist"] * a_sim
    avail += cfg["w_artist"]
    pts += cfg["w_title"] * t_sim
    avail += cfg["w_title"]
    notes.append(f"artist {a_sim:.2f}, title {t_sim:.2f}")
    if box_note:
        notes.append(box_note)
    lacks = missing_collaborators(album["artist"], [cand["artist"], cand.get("artist_alt") or "",
                                                     cand.get("artist_sort") or ""])
    if lacks:
        notes.append(f"credit lacks {', '.join(lacks)}")

    n_file, n_cand = len(album["tracks"]), len(cand["tracks"])
    if n_file and n_file != n_cand and any(t.get("box_vinyl") for t in cand["tracks"]):
        # a CD set with a vinyl single in the box (Kreator - Dying Alive, NB 3053-5: 3 CDs
        # and a 7"): the CDs were ripped, the 7" was not
        cds = [t for t in cand["tracks"] if not t.get("box_vinyl")]
        if len(cds) == n_file:
            cand = dict(cand, tracks=cds, disc_count=len({t.get("disc") for t in cds}) or 1)
            n_cand = n_file
            notes.append("the vinyl single in the box is not in your files")
    tracks_ok = n_file > 0 and n_file == n_cand
    avail += cfg["w_tracks"]
    if tracks_ok:
        pts += cfg["w_tracks"]
    else:
        notes.append(f"track count differs ({n_file} files vs {n_cand})")

    # barcode / catalogue number
    code = ""
    conflict = typed_note = False
    a_codes = {barcode_key(b) for b in album["barcodes"]} - {""}
    c_codes = {barcode_key(b) for b in cand["barcodes"]} - {""}
    sticker = _shop_sticker(album, cand, a_codes, c_codes, ocr_text)
    if sticker:
        a_codes = set()
        notes.append(f"barcode {sticker} on the scans is on no release (a shop sticker?): ignored")
    # BAR-5: a barcode decoded off your scans beats a different one in your files' tags: the
    # release carrying the scans' code has it as proof, one carrying only the tags' code has a
    # weak one (as another tagger's), and neither is "another product"
    scan_codes = ({barcode_key(b) for b in album.get("ocr_barcodes") or ()} - {""}) - a_codes \
        - {barcode_key(b) for b in album.get("unlisted_barcodes") or ()}       # (BAR-4: a sticker is no barcode)
    if scan_codes and c_codes & scan_codes and not sticker:
        code = "barcode"
    elif a_codes and c_codes:
        if a_codes & c_codes:
            # (a barcode another tagger wrote, with the release it names: it confirms that pick)
            code = TAGS_BARCODE if tagger_barcode(album, a_codes & c_codes) else "barcode"
            if code == "barcode" and scan_codes and not (a_codes & c_codes) <= (
                    {barcode_key(b) for b in album.get("ocr_barcodes") or ()}):
                code = TAGS_BARCODE                     # BAR-5: the scans show another barcode
        else:
            conflict = True      # a different product: never auto
            avail += cfg["w_code"]
            notes.append("barcode differs")
    # (a number of your folder, notes or rip replaces another tagger's barcode: that barcode
    # must not hide the folder's catalogue number, which the other pressings are weighed by)
    if (not code or code == TAGS_BARCODE) and album["catnos"] and cand["catnos"]:
        shared = album["catnos"] & cand["catnos"]
        # (a set's numbers as a range: "SICP 10151-4" in the folder, "SICP 10151~4" listed)
        shared |= {c for c in album["catnos"] - shared if any(catno_range_same(c, x) for x in cand["catnos"])}
        if shared and not (code and tagger_catno(album, shared)):
            code = ("catalogue number (folder name)"
                    if shared <= set(album.get("catnos_from_folder") or ()) else
                    TAGS_CATNO if tagger_catno(album, shared) else "catalogue number")
            if code.endswith("(folder name)") and shared <= set(album.get("catnos_typed") or ()):
                typed_note = True               # typed in the ripper's notes or log, not the folder
    # (also when only another tagger's barcode or number agrees: printed on your scans too,
    # it is your copy's own)
    if (not code or code in (TAGS_BARCODE, TAGS_CATNO)) and ocr_text:
        hay = norm_code(ocr_text)
        for c in cand["catnos"]:
            # Only distinctive catalogue numbers (letters AND digits, 5+
            # characters, e.g. "omm0132"): a bare number like "2003" also
            # matches a printed year, a phone number or a barcode fragment.
            if len(c) >= 5 and re.search(r"[a-z]", c) and re.search(r"\d", c) and c in hay:
                code = "catalogue number (OCR)"
                break
        for b in cand["barcodes"]:
            if barcode_printed(ocr_text, b):
                # typed in the notes, an .nfo or a saved page only: weighs as the folder name
                if typed_only_barcode(album, b):
                    code = code or NOTES_BARCODE      # (a catalogue number printed on a scan first)
                    continue
                code = "barcode (OCR)"
                break
    if code:
        pts += cfg["w_code"]
        avail += cfg["w_code"]
        notes.append("catalogue number (your rip's notes) agrees" if typed_note and code.endswith("(folder name)")
                     else "barcode typed in your rip's notes or a saved page agrees (not proof of the pressing)"
                     if code == NOTES_BARCODE
                     else "barcode in the tags another tagger wrote agrees (not proof of the pressing)"
                     if code == TAGS_BARCODE
                     else "catalogue number in the tags another tagger wrote agrees (not proof of the pressing)"
                     if code == TAGS_CATNO else f"{code} agrees")
    elif (album["catnos"] - set(album.get("catnos_from_folder") or ())) and cand["catnos"]:
        # Only a catalogue number from the TAGS counts against a candidate. One
        # read from the folder name may not be a catalogue number at all
        # ("[Tatman Music TM 050426]" is on many different albums).
        avail += cfg["w_code"]
        notes.append("catalogue number differs")

    # durations
    dur_frac, dur_cov = None, 0.0
    if tracks_ok:
        known = [(t["secs"], c["secs"]) for t, c in zip(album["tracks"], cand["tracks"])
                 if c["secs"] > 0]
        dur_cov = len(known) / n_cand
        if known and dur_cov >= cfg["min_duration_coverage"]:
            good = sum(1 for f, c in known if duration_ok(f, c, cfg))
            sleeve = good < len(known) and sleeve_times(known)
            if sleeve:                        # LEN-1: every length listed a few seconds short of the file's
                notes.append(f"durations {good}/{len(known)} agree as listed, and every one is listed a few "
                             f"seconds short of your file (times printed on the sleeve): taken as agreeing")
                good = len(known)
            dur_frac = good / len(known)
            pts += cfg["w_durations"] * dur_frac
            avail += cfg["w_durations"]
            if not sleeve:
                notes.append(f"durations {good}/{len(known)} agree")
    # Track counts differ by a few: do the lengths still line up in order?
    aligned = False
    if not tracks_ok and n_file and n_cand and abs(n_file - n_cand) <= 3:
        cand_known = [c["secs"] for c in cand["tracks"]]
        if sum(1 for s in cand_known if s > 0) / n_cand >= cfg["min_duration_coverage"]:
            run = align_durations([t["secs"] for t in album["tracks"]], cand_known, cfg)
            known_n = sum(1 for s in cand_known if s > 0)
            frac = run / known_n if known_n else 0.0
            if frac >= 0.85:
                aligned = True
                dur_frac = frac
                pts += cfg["w_durations"] * frac
                avail += cfg["w_durations"]
                extra = n_file - n_cand
                notes.append(f"{run} track lengths line up in order "
                             f"(files have {abs(extra)} {'extra' if extra > 0 else 'missing'} "
                             f"track{'s' if abs(extra) != 1 else ''})")
    # (the release lists too few lengths to compare, and none of those it lists disagrees;
    # a proving barcode costs nothing for lengths in the first place)
    lengths_missing = bool(dur_frac is None and tracks_ok and n_cand and not barcode_proves({"code": code})
                           and all(duration_ok(f, c, cfg) for f, c in known))
    if dur_frac is None:
        if barcode_proves({"code": code}):
            notes.append("durations unavailable (barcode decides)")
        else:
            avail += cfg["w_durations"]  # missing evidence costs confidence (_missing_lengths_free)
            if tracks_ok:
                notes.append(f"durations unavailable ({dur_cov:.0%} listed)")

    # disc layout
    if album["discs"] and cand["disc_count"]:
        avail += cfg["w_discs"]
        if album["discs"] == cand["disc_count"]:
            pts += cfg["w_discs"]
        else:
            notes.append(f"disc count {album['discs']} vs {cand['disc_count']}")

    # year (reissues are common, so a mismatch only withholds points)
    year_ok = False
    want_year = album.get("pressing_year") or album["year"]
    year_off = None
    if want_year and cand["year"]:
        avail += cfg["w_year"]
        year_off = abs(want_year - cand["year"])
        tol = cfg.get("pressing_year_tolerance", 0) if album.get("pressing_year") else 0
        if year_off <= tol:
            pts += cfg["w_year"]
            year_ok = True
            if album.get("pressing_year"):
                notes.append(f"year {cand['year']} agrees with the {want_year} edition named in the folder"
                             if year_off else
                             f"year {want_year} agrees with the edition named in the folder")
        else:
            notes.append(f"year {want_year} vs {cand['year']}")

    # OCR corroboration of artist/title text printed on the scans
    if ocr_text:
        # Whole words only: a substring test finds "sun" in "SUNDAY" and "ono"
        # in "HONOLULU". Words that appear on almost any sleeve don't count.
        hay_words = set(fold(ocr_text).split())
        words = set(_artist_tokens(album["artist"])) | set(fold(strip_edition(album["title"])).split())
        words = {w for w in words if len(w) > 2 and w not in _OCR_STOPWORDS}
        if words:
            share = sum(1 for w in words if w in hay_words) / len(words)
            # EVI-4: what the scans read only adds; words they missed (a blurry scan, an
            # unusual layout) never lower the score
            pts += cfg["w_ocr"] * share
            avail += cfg["w_ocr"] * share
            notes.append(f"OCR text found {share:.0%} of artist/title words")

    # the scans print the credited artist ("Arrangements and Orchestrations by Gustavo Dobles")
    credit_words = [w for w in fold(cand.get("artist") or "").split() if len(w) > 2]
    scans_name_artist = bool(ocr_text and credit_words and len(credit_words) >= 2 and
                             f" {' '.join(credit_words)} " in f" {fold(ocr_text)} ")
    # ... or the files do: their track-artist, copyright or composer tags, or the
    # .info/.txt the ripper left ("ARTIST=Gustavo Dobles", "COPYRIGHT=2020 Gustavo Dobles")
    files_name_artist = bool(credit_words and len(credit_words) >= 2 and album.get("credit_text") and
                             f" {' '.join(credit_words)} " in f" {fold(album['credit_text'])} ")
    clues, clues_against = pressing_clues(ocr_text, cand, album.get("ring_text") or "")
    if "barcode" in clues:
        printed = [b for b in sorted(cand.get("barcodes") or ()) if barcode_printed(ocr_text, b)]
        if printed and all(typed_only_barcode(album, b) for b in printed):
            # typed in the notes or a saved page only: told, never telling pressings apart
            del clues["barcode"]
            clues[NOTES_BARCODE] = f"barcode {printed[0]} typed in your rip's notes or a saved page"
    file_clues(album, cand, clues, clues_against, cfg)
    if "matrix" in clues:                    # a clue for telling pressings apart; no points
        notes.append(clues["matrix"].replace(" on the scans", " agrees (read on the scans)"))
    # A catalogue number the folder or tags name that this pressing does not carry:
    # if NO pressing carries it, the copy is probably one the databases do not list.
    own = sorted(c for c in (album.get("catnos") or ())
                 if (re.search(r"[a-z]", c) and re.search(r"\d", c) and len(c) >= 4)
                 or len(re.sub(r"\D", "", c)) >= 6)
    # written either way ("RR 7157-2" in the folder, "RR 7157" on the release): catno_same
    own_absent = bool(own) and not catnos_agree(own, cand.get("catnos") or ())
    len_err = titles_agree = titles_loose = len_diffs = None
    if tracks_ok:
        diffs = [abs(t["secs"] - c["secs"]) for t, c in zip(album["tracks"], cand["tracks"]) if c["secs"] > 0]
        len_err = round(sum(diffs) / len(diffs), 2) if diffs else None
        if diffs and len(diffs) == n_cand:
            len_diffs = [round(d, 3) for d in diffs]        # every track, to the millisecond
        if any(t.get("title") for t in album["tracks"]):
            titles_agree = sum(1 for t, c in zip(album["tracks"], cand["tracks"])
                               if t.get("title") and _ratio(fold(strip_edition(t["title"])),
                                                            fold(strip_edition(c.get("title") or ""))) >= 0.9)
            titles_loose = sum(1 for t, c in zip(album["tracks"], cand["tracks"])
                               if t.get("title") and _title_or_its_start(t["title"], c.get("title")))
    conf = round(100.0 * pts / avail, 1) if avail else 0.0
    outer = bool(code.startswith("barcode")) and barcode_on_outer(album, cand.get("barcodes"))     # BAR-3
    res = {"barcode_outer": outer,
        "confidence": conf, "artist_sim": round(a_sim, 3), "title_sim": round(t_sim, 3), "lacks": lacks,
        "tracks_ok": tracks_ok, "aligned": aligned, "dur_frac": dur_frac, "dur_cov": round(dur_cov, 2),
        "toc_lengths": toc_lengths(album, cand, tracks_ok),
        "lengths_missing": lengths_missing, "avail": avail,
        "numbers_repeat": numbers_repeat(album), "code": code, "year_ok": year_ok, "conflict": conflict, "notes": notes, "cand": cand,
        "copy_hint": album.get("media_hint") or "",
        "copy_kind": copy_kind(album),
        "discs_off": bool(album["discs"] and cand["disc_count"] and album["discs"] != cand["disc_count"]),
        "pressing_year": album.get("pressing_year"),
        "clues": clues, "clues_against": clues_against, "year_off": year_off,
        "len_err": len_err, "titles_agree": titles_agree, "titles_loose": titles_loose, "len_diffs": len_diffs,
        "ms_lengths": any(c["secs"] % 1 for c in cand["tracks"] if c.get("secs")),
        "own_catnos": own, "own_catno_absent": own_absent, "scans_name_artist": scans_name_artist,
        "files_name_artist": files_name_artist,
        "other_version": other_version(album, cand),
        "has_folder_catno": bool(album.get("catnos_from_folder")),
        "folder_catno_agrees": catnos_agree(album.get("catnos_from_folder"), cand.get("catnos")),
        "folder_catno_absent": bool(album.get("catnos_from_folder") and cand.get("catnos")
                                    and not catnos_agree(album.get("catnos_from_folder"), cand.get("catnos"))),
        "tagged": tagged_release(album, cand),
        # ... by another tagger, not by TagMender's own job (match_albums.tagmender_wrote)
        "tagger_tagged": bool(album.get("tags_by_tagger")) and tagged_release(album, cand),
        # an EAC/XLD log with the disc's table of contents (a log beside hi-res audio is
        # left from an earlier CD rip, as format_against has it): whatever the format
        # clue says, the files' disc ID came off a CD (_toc_maybe_download)
        "rip_log": bool(album.get("rip_log_toc") and not (album.get("audio") or {}).get("hires")),
    }
    return _with_missing_tracks(album, cand, cfg, ocr_text, res) if not tracks_ok and not box_note else res


def _with_missing_tracks(album, cand, cfg, ocr_text, res):
    """Tracks missing from the folder (Mekong Delta - Pictures At An Exhibition: 24 of the
    CD's 32): when the release is proven otherwise (_slots_why_not) and each file fits its
    own numbered track, the score of the files against those tracks, with "missing" set.
    Otherwise `res` as it was, told how the present files compare."""
    if cand.get("slots") is not None:
        return res
    view = slot_view(album, cand)
    if not view:
        return res
    s = score_candidate(album, view, cfg, ocr_text)
    why_not = _slots_why_not(album, s)
    gaps = missing_text(view["missing"])
    if why_not:
        if s.get("dur_frac") is not None:
            good = round(s["dur_frac"] * len(album["tracks"]))
            res["notes"] = res["notes"] + [f"the files' lengths fit {good} of their {len(album['tracks'])} "
                                           f"own tracks (tracks {gaps} missing), but {why_not}"]
        return res
    s["missing"] = view["missing"]
    s["notes"] = s["notes"] + [f"tracks {gaps} of {view['full_tracks']} are missing; every file fits its "
                               "own track of this release"]
    s["whole_view"] = res              # (classify: when another release holds just the files' tracks)
    return s


def _full_fit_first(scored, cfg):
    """A release that holds exactly the files' tracks, their lengths agreeing, is never held
    back by a longer one (its deluxe edition, a 2CD whose disc 1 is these files) scored
    against the files' own slots of it (_with_missing_tracks): those are then scored as
    whole releases again, as any release whose track count differs."""
    full = any(s.get("tracks_ok") and not s.get("missing") and s.get("dur_frac") is not None
               and s["dur_frac"] >= cfg["auto_min_duration_frac"] for s in scored)
    if not full:
        return scored
    return [s["whole_view"] if s.get("missing") and s.get("whole_view") else s for s in scored]


def tagged_release(album, cand):
    """The files' own tags name this release (MUSICBRAINZ_ALBUMID, DISCOGS_RELEASE_ID or an
    id in their comments). Not evidence for the album, which those tags may have got wrong;
    only a way to keep it when nothing tells tied pressings apart. A blind run blanks them."""
    ids = album.get("tag_release_discogs" if cand.get("source") == "discogs" else "tag_release_mb") or ()
    return str(cand.get("id")).lower() in {str(x).lower() for x in ids}


def _numbers_prove_album(s, cfg):
    """The candidate is this album by its own numbers, whatever the names say: a disc ID,
    the files' link, an ISRC, a fingerprint, the barcode or a catalogue number from the
    tags or folder name, with the track lengths agreeing (as classify proves the best).
    The ALBUM, not the pressing: an ISRC names the album's recordings, and a barcode typed in
    notes or written by another tagger counts here as a catalogue number from the folder
    name does (whether the pressing fits the folder name is decided apart)."""
    clues = s.get("clues") or {}
    proof = (any(k in clues for k in ("disc TOC", "source link", "isrc", "fingerprint"))
             or s["code"] == "barcode" or s["code"].startswith("barcode")
             or s["code"] in ("catalogue number", "catalogue number (folder name)", TAGS_CATNO))
    return bool(proof and s.get("dur_frac") is not None and s["dur_frac"] >= cfg["auto_min_duration_frac"]
                and not s.get("conflict"))


def _whole_sectors_ms(x):
    """A length listed to the millisecond is a whole number of CD sectors (1/75 s = 13.333
    ms): the ms value is the sector boundary truncated or rounded, never rounded up
    (MusicBrainz truncates: 204.506 s is 15338 sectors, 204506.67 ms). At most 1 ms off,
    on the low side only: about 10% of lengths that are not whole sectors pass by chance
    (7.5% with the earlier 0.04-sector tolerance, 0.53 ms either side, which missed
    MusicBrainz's truncated values; 12.5% if rounding up were accepted too)."""
    k = round(x * 75)
    ms = round(x * 1000)
    exact = k * 40 / 3
    return ms in (math.floor(exact), round(exact))


def _sector_aligned_download(s):
    """A digital release of the album whose own lengths, to the millisecond, are whole CD
    sectors: a download cut from the CD master is, so whole sectors do not tell them apart."""
    c = s["cand"]
    secs = [t.get("secs") or 0 for t in c.get("tracks") or ()]
    return bool("digital" in (c.get("formats") or ()) and s.get("tracks_ok") and s.get("ms_lengths")
                and secs and all(secs) and all(_whole_sectors_ms(x) for x in secs))


def drop_unproven_sectors(scored):
    """Whole CD sectors, and the disc ID rebuilt from them, stop counting as a CD when a
    digital release of the album is sector-aligned too (it gives the same disc ID), unless
    a rip log or the scans back the CD up (Voivod - Lost Machine - Live: the Century Media
    download is 470.600 s, 204.506 s, ... - whole sectors)."""
    if not any(_sector_aligned_download(s) for s in scored):
        return
    for s in scored:
        clues = s.get("clues") or {}
        if _disc_backed(s):
            continue
        drop = {k for k in ("format", "disc TOC", "disc TOC (fuzzy)")
                if k in clues and (k != "format" or clues[k] == SECTOR_CLUE)}
        if drop:
            s["clues"] = {k: v for k, v in clues.items() if k not in drop}
            s["notes"] = s["notes"] + ["whole CD sectors and the disc ID they give not counted: a digital "
                                       "release of this album is whole sectors too"]


# What the files are made from against what the pick is (file_clues, format_against): any
# one of these keeps the pick from going automatic.
# (Scans of a back cover against a download are not among them: a download often comes
# with its artwork, and a web listing's format is a guess.)
_FORMAT_AGAINST = re.compile(r"^hi-res audio cannot come from|"
                             r"^your folder has (?:a CD rip log|tracks that are whole CD sectors), so not this |"
                             r"^folder name says it was bought from |^lossless tracks are not whole CD sectors")


def format_contradictions(best):
    """The against-clues on a pick that say your copy cannot be that format."""
    return [x for x in best.get("clues_against") or () if _FORMAT_AGAINST.search(x)]


# Tie-breaks that pick a pressing when nothing about your copy tells them apart
FALLBACK_PICKS = ("country preference", "original pressing", "duplicate", "release type preference",
                  "format preference")


def _one_length_off(best, cfg):
    """ALB-1: the album is this release however its score fell short: every track title
    matches (titles_loose), at most one track length is off, artist and title agree, no
    barcode of yours argues, and the score is within 5 points of the cut-off."""
    n = len(best["cand"].get("tracks") or ())
    return (n >= 3 and best.get("tracks_ok") and best.get("titles_loose") == n
            and (best.get("dur_frac") or 0) >= (n - 1) / n
            and best["artist_sim"] >= cfg["auto_min_artist"] and best["title_sim"] >= cfg["auto_min_title"]
            and not best.get("conflict") and best["confidence"] >= cfg["auto_min_confidence"] - 5)


def _kept_choice(best, scored, cfg):
    """TAG-1 / CHG-1: the one candidate your files are tagged with by TagMender (its own
    choice, or yours through Change Release), when it still fits your files and no barcode or
    catalogue number of your copy is on another candidate instead; else None. Another
    tagger's release IDs pick nothing (TAG-2)."""
    mine = [s for s in scored if s.get("tagged") and not s.get("tagger_tagged") and s.get("tracks_ok")
            and not s.get("conflict") and not s.get("clues_against")
            and s["confidence"] >= cfg["review_min_confidence"]]
    if len(mine) != 1 or mine[0] is best:
        return None
    m = mine[0]

    def numbered(s):
        return barcode_proves(s) or s.get("code") in ("catalogue number", "catalogue number (folder name)",
                                                      "catalogue number (OCR)")
    if not numbered(m) and any(numbered(s) for s in scored if s is not m):
        return None
    return m


def _fits_to_the_ms(best, scored, cfg):
    """(candidate, why) for another release of the album, however it scored, whose
    millisecond lengths fit every track where the pick's miss every one (exact_lengths' first
    rule), else None. Its title or year may cost it many points (Bell Witch - Demo 2011: the
    2011 entry titled "Bell Witch", 74 points, against the 2015 reissue 30 ms short on every
    track, 100), so it never joined the tie; it only keeps the pick from going automatic."""
    if not (best.get("ms_lengths") and best.get("len_diffs")):
        return None
    for s in scored:
        if (s is best or not s.get("tracks_ok") or s["artist_sim"] < cfg["auto_min_artist"]
                or s.get("unproven_media")):
            continue
        hit = exact_lengths([best, s])
        if hit and hit[0] is s and hit[2] == "ms":
            return s, hit[1]
    return None


# The lengths' own clues: they say how well a release fits, which is what is weighed here
_LENGTH_CLUES = ("track lengths", "exact lengths")


def _exact_rival(best, scored, cfg, scan_pick=None):
    """(candidate, why) to pick instead of best: the ONE other release whose lengths fit
    every track exactly (to the millisecond listed) where best's are 0.02 s or more off on
    every track, while nothing else about your copy points to best and nothing contradicts
    the other. "Nothing else" is every clue on best the other lacks (a barcode, catalogue
    number, disc ID, link, ISRC or fingerprint; a label, country, matrix or packaging read
    on the scans; the edition your folder names), and a tie-break on the scans that chose
    best (only a country order, the earliest date or the fuller entry is no evidence). Bell
    Witch - Demo 2011: the 2011 download "Bell Witch" (74 points, every track 0.000 s off)
    against the 2015 reissue "Demo 2011" (100 points, ~30 ms short on every track). Two such
    releases, or one only within a few ms, are no proof: None (the pick is then only kept
    off auto, _fits_to_the_ms)."""
    rival = _fits_to_the_ms(best, scored, cfg)
    if not rival or max(rival[0]["len_diffs"]) > 0.001:
        return None
    if scan_pick and scan_pick[0] is best and any(k not in FALLBACK_PICKS for k in scan_pick[1]):
        return None
    s = rival[0]
    # the edition your folder names is the pick's year and not the other's: the folder decides
    if best.get("pressing_year") and best.get("year_ok") and not s.get("year_ok"):
        return None
    # a clue both carry (AcoustID's fingerprints are on every release of the recordings)
    # says nothing between them
    mine, theirs = best.get("clues") or {}, s.get("clues") or {}
    if any(k not in theirs and k not in _LENGTH_CLUES and (CLUE_WEIGHTS.get(k, 1) > 0 or k in EVIDENCE_CLUES)
           for k in mine):
        return None
    ev_theirs = _evidence(s)
    if any(k not in ev_theirs or (k == "code" and v != ev_theirs[k]) for k, v in _evidence(best).items()):
        return None
    if s.get("conflict") or s.get("clues_against") or s.get("media_why") or s.get("other_version"):
        return None
    # another release with lengths as close: the lengths do not tell which one it is
    alike = [o for o in scored if o is not s and o is not best and o.get("tracks_ok") and o.get("len_diffs")
             and len(o["len_diffs"]) == len(s["len_diffs"]) and max(o["len_diffs"]) < 0.02]
    return None if alike else rival


def _lengths_back_it(s) -> bool:
    """The candidate is this album and at least half the track lengths it lists agree: what tells
    it from an entry that lists none (one with most of them wrong does not)."""
    return bool(s.get("tracks_ok") and s.get("dur_frac") is not None and s["dur_frac"] >= 0.5
                and s.get("title_sim", 0) >= 0.85)


def _yields_to_lengths(res, other):
    """RES as combine() must see it: its pick was only lifted because it lists no lengths
    (_missing_lengths_free), and the other database has a candidate whose lengths do agree. Then
    it is not sure, and has its first score back, as it would within one database."""
    best = (res or {}).get("best")
    if not best or not best.get("lengths_free") or \
            not any(_lengths_back_it(s) for s in (other or {}).get("scored") or [(other or {}).get("best") or {}]):
        return res
    why = list(res.get("why") or [])
    return dict(res, status="review" if res.get("status") == "auto" else res.get("status"),
                best=dict(best, confidence=best["lifted_from"], lengths_free=False),
                why=why + ([] if "durations unavailable" in why else ["durations unavailable"]))


def _missing_lengths_free(scored, cfg):
    """A release that lists no track lengths loses no points and is not held back for it - unless
    another candidate's lengths do agree with the files: then those are what tells them apart, and
    the entry nothing can be compared on must not tie with it. Lengths that are listed and
    disagree still count against a release. Extra-Careful Matching keeps asking for them."""
    if cfg.get("lengths_required") or any(_lengths_back_it(s) for s in scored):
        return
    for s in scored:
        if s.get("lengths_missing") and not s.get("lengths_free") and s.get("avail", 0) > cfg["w_durations"]:
            s["lifted_from"] = s["confidence"]
            s["confidence"] = min(100.0, round(s["confidence"] * s["avail"] / (s["avail"] - cfg["w_durations"]), 1))
            s["lengths_free"] = True


def classify(scored, cfg):
    """Decide auto / review / nomatch for ONE source's scored candidates.

    Returns {"status", "best", "runner_up", "margin", "why"}. `why` lists
    every reason a candidate that looked good did not qualify for auto.
    """
    if not scored:
        return {"status": "nomatch", "best": None, "runner_up": None,
                "margin": None, "why": ["no candidates found"]}
    _missing_lengths_free(scored, cfg)
    scored = _full_fit_first(scored, cfg)

    def barcode_agrees(s):
        # (a barcode typed in notes, or written by another tagger, is no proof: WEAK_BARCODES)
        return barcode_proves(s)

    # What your folder name states about the copy is a rule, not a hint: its format
    # ("[Vinyl Rip 24bit 192khz]", "[2CD]"), country ("Germany") and catalogue number.
    # Releases that contradict it are another product. When the same album is listed
    # in a form that fits, the others drop out (however well their lengths fit); when
    # none fits, a person decides.
    # A folder catalogue number counts only when some release carries it: one on none
    # of them may not be a catalogue number at all ("[Tatman Music TM 050426]").
    catno_real = any(s.get("folder_catno_absent") is False and (s["cand"].get("catnos"))
                     and s.get("has_folder_catno") for s in scored)

    def _says(s):
        return [x for x in s.get("clues_against") or () if x.startswith("folder name says")]
    # Several releases carry the folder's own number (an unofficial Russian CD copies the
    # European one's NB 3099-0): then the rest of the folder name decides between them -
    # unless your copy cannot be the one the rest fits (Mekong Delta - Toccata, "Germany,
    # AAARRG 17" with an EAC log: the German AAARRG 17 is only a 12", the CD is Austrian).
    numbered_fit = any(s.get("folder_catno_agrees") and not _says(s) and not format_contradictions(s)
                       for s in scored)

    # (not ISRCs: they name recordings, which every pressing of the album shares; not a fuzzy
    # disc ID, "disc TOC (fuzzy)": a CD of about these lengths; not a barcode typed in notes
    # or written by another tagger)
    def proven_by_your_copy(s):
        clues = s.get("clues") or {}
        return (clues.get("disc TOC") == EXACT_TOC_CLUE or "source link" in clues
                or barcode_agrees(s))

    proven_fit = any(proven_by_your_copy(s) and not _says(s) and not format_contradictions(s) for s in scored)

    def folder_says(s):
        out = _says(s)
        if proven_by_your_copy(s) and not proven_fit:
            # what your copy itself proves (its exact disc ID, barcode - from tags as ripped or
            # as TagMender wrote them, or a scan -, the files' own link) outranks what a folder
            # name says: names can be wrong. (When a proven release fits the folder name too,
            # the name still picks between them: a barcode several pressings share.)
            return []
        if s.get("folder_catno_agrees") and not numbered_fit:
            # the folder's own number is on it: that is the pressing
            out = [x for x in out if re.match(r"folder name says (vinyl|cd),", x)]
        if catno_real and s.get("folder_catno_absent"):
            out.append("folder catalogue number is not on it")
        return out
    # The release another tagger's tags name, which the folder name rules out: one of the two
    # is wrong, and nothing of your copy beyond those tags says which. Neither is taken
    # automatically (a person decides; the folder's pressing still leads the list).
    tags_against = sorted({f"{s['cand']['id']} ({'; '.join(folder_says(s))})" for s in scored
                           if s.get("tagger_tagged") and folder_says(s)})
    if any(folder_says(s) for s in scored):
        fitting = [s for s in scored if not folder_says(s)]
        # the same album: its names agree, or its own numbers prove it ("The Mediator" in the
        # folder is "The Mediator Between Head and Hands Must Be the Heart", barcode and the
        # folder's NB 3099-0 on it; its short title alone dropped every release)
        same_album = [s for s in fitting if s["tracks_ok"] and (
            (s["artist_sim"] >= cfg["auto_min_artist"] and s["title_sim"] >= cfg["auto_min_title"])
            or _numbers_prove_album(s, cfg))]
        said = sorted({x.split(",")[0] for s in scored for x in folder_says(s)})
        if same_album:
            dropped = len(scored) - len(fitting)
            unlisted = all(s.get("own_catno_absent") for s in scored)
            scored = fitting
            for s in same_album:
                s["notes"] = s["notes"] + [f"{dropped} release(s) left out: {'; '.join(said)}"]
            # Leaving releases out never makes an unlisted pressing a match: your copy's
            # catalogue number is on none of them, so only hard proof (a disc ID, your
            # files' link, fingerprint, barcode) decides. Not ISRCs: every pressing of the
            # album shares its recordings, the unlisted one too.
            if unlisted:
                hard = ("disc TOC", "source link")      # (a fingerprint finds recordings: hard_proof)
                own = next((x for s in scored for x in s.get("own_catnos") or ()), "")
                scored = [s if (any(k in (s.get("clues") or {}) for k in hard) or barcode_proves(s))
                          else dict(s, media_why=f"catalogue number {own.upper()} from your folder/tags is on "
                                                 "none of these pressings") for s in scored]
        else:
            # each release is told what the folder name says against it (a release carrying
            # the folder's number is never told that number is not on it); one the folder name
            # fits but whose track count differs is held back by that ("track count differs")
            def media_why(s):
                said_here = "; ".join(folder_says(s))
                return (f"{said_here}; " if said_here else "") + \
                    "no release of this album found that fits what the folder name says"
            scored = [dict(s, media_why=media_why(s)) if folder_says(s) or s["tracks_ok"] else s
                      for s in scored]

    drop_unproven_sectors(scored)

    def number_agrees(s):
        """Barcode, or a catalogue number from the tags or folder name (not one
        merely found somewhere in OCR text)."""
        return barcode_agrees(s) or s["code"] in ("catalogue number", "catalogue number (folder name)")
    # Ties go to the candidate whose barcode agrees, then to the one whose year does.
    # (then one your copy cannot be - a CD rip log against a 12" - after the others)
    ranked = sorted(scored, key=lambda s: (-s["confidence"], bool(format_contradictions(s)) and not _carries_barcode(s),
                                           not barcode_agrees(s),
                                           not number_agrees(s), not s["year_ok"],
                                           s.get("year_off") if s.get("year_off") is not None else 99))
    best = ranked[0]
    # TAG-1 / CHG-1: the release your files are tagged with (by TagMender, or your Change
    # Release) stands while it fits, unless a barcode or catalogue number of yours names another
    kept_choice = _kept_choice(best, scored, cfg)
    if kept_choice:
        best = kept_choice
        ranked = [best] + [s for s in ranked if s is not best]
    # Pressings that score the same: what the scans show can tell them apart
    # (a label, "Made in Germany", a catalogue number printed on the back).
    tied = [s for s in ranked if best["confidence"] - s["confidence"] < cfg["auto_min_margin"]]
    # No vinyl or cassette without evidence for it: they are out of the tie, while they stay
    # listed for Change Release
    for s in scored:
        s.pop("unproven_media", None)
    unproven = []
    asked_for = prefers_vinyl_or_tape(release_preferences(cfg)["formats"])
    while not asked_for and not kept_choice:     # (the top pressing gone, others may now be in the tie)
        out = no_media_evidence(tied)
        if not out:
            break
        for x in out:
            x["unproven_media"] = True
        unproven += out
        ranked = [x for x in ranked if not x.get("unproven_media")]
        best = ranked[0]
        tied = [x for x in ranked if best["confidence"] - x["confidence"] < cfg["auto_min_margin"]]
    ranked += unproven
    scan_pick = None
    # A number decides only when it is the ONLY one that agrees. When several tied
    # pressings each match a number (King Diamond: the tags carry Metal Blade's
    # 3984-14359-2, the folder FONO's FO40CD), the other evidence decides.
    numbered = [s for s in tied if number_agrees(s)]
    if (not numbered or len(numbered) > 1) and not kept_choice:
        scan_pick = choose_pressing(numbered or tied, cfg.get("preferred_countries"), release_preferences(cfg))
        if scan_pick:
            pick = scan_pick[0]
            ranked = [pick] + [s for s in ranked if s is not pick]
            best = pick
    # Several tied pressings carry your barcode: it names them all. What else your copy shows
    # tells them apart first; what they are ripped to only for want of anything better (a hi-res
    # rip of the hybrid SACD and the CD of one barcode). A pressing that alone carries it is
    # never lost to the audio.
    barcoded = [s for s in tied if _carries_barcode(s)]
    audio_split = barcode_split = None
    if len(barcoded) > 1 and not kept_choice and (not scan_pick or set(scan_pick[1]) == {"format"}
                              or any(k in FALLBACK_PICKS for k in scan_pick[1])):
        fits_audio = [s for s in barcoded if not format_contradictions(s)]
        ahead = [s for s in barcoded if all(_more_behind(s, o) for o in barcoded if o is not s)]
        # BAR-3: two barcodes of yours on two pressings: the outer packaging's names yours
        two_codes = len({frozenset(_keys(s["cand"].get("barcodes"))) for s in barcoded}) > 1
        outer = _outer_barcode_pick(barcoded) if two_codes else None
        if outer:
            best, scan_pick, barcode_split = outer, None, [s for s in barcoded if s is not outer]
        elif len(ahead) == 1:
            best, scan_pick, barcode_split = ahead[0], None, [s for s in barcoded if s is not ahead[0]]
        elif len(fits_audio) == 1 and not any(_more_behind(s, fits_audio[0]) for s in barcoded):
            best, scan_pick, audio_split = fits_audio[0], None, [s for s in barcoded if s is not fits_audio[0]]
        if audio_split or barcode_split:
            ranked = [best] + [s for s in ranked if s is not best]
    runner = next((s for s in ranked[1:] if not s.get("unproven_media")), None)
    margin = best["confidence"] - runner["confidence"] if runner else 100.0
    # One release fits every track exactly where the pick misses every one, and nothing
    # else decides: the lengths are that release's own, so it is the pick. Millisecond
    # lengths are no pressing proof, though: unless a number, disc ID, link or ISRC is on
    # it too, a person confirms it (below; Bell Witch: AcoustID's fingerprints, on both,
    # prove the recordings, not which release).
    exact = _exact_rival(best, scored, cfg, scan_pick)
    if exact:
        runner, best = best, exact[0]
        margin, scan_pick = abs(best["confidence"] - runner["confidence"]), None
        best["notes"] = best["notes"] + [f"pressing chosen by {exact[1]}; {runner['cand']['id']} scored "
                                         f"{runner['confidence']} but is off on every track"]
    if unproven and "cd" in (best["cand"].get("formats") or ()):
        best["notes"] = best["notes"] + [
            "nothing about your copy points to " + " or ".join(sorted(
                {k for x in unproven for k in x["cand"].get("formats") or ()})) + ", so it is taken for a CD"]

    why = [best["media_why"]] if best.get("media_why") else []
    if tags_against:
        why.append(f"your files' tags (written by another tagger) name {', '.join(tags_against[:2])}; "
                   "the folder name rules that out and nothing else of your copy says which is right")
    # The album is PROVEN when its disc ID, barcode, or a catalogue number from
    # its tags or folder name agrees and every track length agrees too. A title
    # or artist that differs from the folder's is then the database's own
    # (freeDB-style): "The Mediator" is "The Mediator Between Head and Hands
    # Must Be the Heart"; an album filed under Death is credited to Control Denied.
    proof = ("disc ID" if (best.get("clues") or {}).get("disc TOC") else
             "the files' shop link" if (best.get("clues") or {}).get("source link") else
             "ISRC" if (best.get("clues") or {}).get("isrc") else
             "audio fingerprint" if (best.get("clues") or {}).get("fingerprint") else
             "barcode" if best["code"] == "barcode" else
             "catalogue number" if best["code"] in ("catalogue number", "catalogue number (folder name)")
             else "")
    if not proof and best.get("scans_name_artist") and best["title_sim"] >= cfg["exact_title_sim"]:
        proof = "the scans naming this artist"           # only for the artist credit, below
    if not proof and best.get("files_name_artist") and best["title_sim"] >= cfg["exact_title_sim"]:
        proof = "your files' own tags and notes naming this artist"   # likewise
    # TOC-1: an exact disc ID that fits your files is the lengths' proof too, when the title
    # agrees (a disc ID attached to the wrong release names another album)
    # ...and with the artist credited otherwise, the track titles too (a disc ID attached to
    # the wrong "Greatest Hits" in MusicBrainz names another artist's album)
    n_tracks = len(best["cand"].get("tracks") or ())
    toc_proof = bool(best.get("toc_lengths") and proof == "disc ID"
                     and best["title_sim"] >= cfg["auto_min_title"]
                     and (best["artist_sim"] >= cfg["auto_min_artist"]
                          or (n_tracks and (best.get("titles_agree") or 0) >= 0.8 * n_tracks)))
    proven = bool(proof and best["tracks_ok"] and not best.get("conflict")
                  and ((best["dur_frac"] is not None and best["dur_frac"] >= cfg["auto_min_duration_frac"])
                       or toc_proof))
    best["toc_proven"] = bool(toc_proof and proven)      # (plan._files_on_evidence: filed on it)
    split_credit = bool(best.get("lacks"))     # a collaborator would be dropped: never
    if not best["tracks_ok"]:
        why.append("track count differs")
    if best.get("numbers_repeat"):
        # (two discs' files in one folder, no disc tags: which file is which release track is a
        # guess, and a guess writes another track's IDs; nothing excuses it, a code neither)
        why.append(f"{best['numbers_repeat']} files share their track numbers on one disc: which disc "
                   "each is on is not known")
    if proven and best["artist_sim"] < cfg["auto_min_artist"] and not split_credit:
        best["notes"] = best["notes"] + [f"artist differs from the folder's; {proof} proves the album, "
                                         f"so it is credited as the database does "
                                         f"({best['cand'].get('artist')})"]
    elif best["artist_sim"] < cfg["auto_min_artist"]:
        why.append(f"artist similarity {best['artist_sim']:.2f} < {cfg['auto_min_artist']}"
                   + (f" (credit lacks {', '.join(best['lacks'])}; tagging from it would drop them)"
                      if best.get("lacks") else ""))
    if proven and best["title_sim"] < cfg["auto_min_title"]:
        best["notes"] = best["notes"] + [f"title differs from the folder's; {proof} proves the album, "
                                         f"so the database title is used ({best['cand'].get('title')})"]
    elif best["title_sim"] < cfg["auto_min_title"]:
        why.append(f"title similarity {best['title_sim']:.2f} < {cfg['auto_min_title']}")
    barcode_decides = barcode_proves(best)
    # A catalogue number that agrees is strong, pressing-specific evidence, so
    # a few lengths may be off (database lengths are often rounded or wrong).
    # A barcode typed in notes or written by another tagger (WEAK_BARCODES) is no number
    # of your copy: it counts as no code here and never excuses lengths that disagree.
    strong_code = "" if best["code"] in WEAK_BARCODES else best["code"]
    min_dur = cfg["auto_min_duration_frac_with_code"] if strong_code else cfg["auto_min_duration_frac"]
    if not strong_code and best["title_sim"] < cfg["exact_title_sim"]:
        min_dur = max(min_dur, cfg["auto_min_duration_frac_partial_title"])
    # Track lengths may differ when something else points to this very release:
    # a barcode, catalogue number or disc ID (tags, folder name or scans), or a
    # vinyl rip matched to a vinyl release (a manual rip is never cut like the
    # database's lengths). The track count must still agree.
    vinyl = str((best.get("clues") or {}).get("format", "")).startswith("vinyl")
    excuse = ("" if not (best["tracks_ok"] and best["artist_sim"] >= cfg["auto_min_artist"]
                         and best["title_sim"] >= cfg["auto_min_title"] and not best.get("conflict"))
              else f"{strong_code} points to this release" if strong_code
              else "disc ID points to this release" if (best.get("clues") or {}).get("disc TOC")
              else "the shop page your files' tags link to is this release" if (best.get("clues") or {}).get("source link")
              else "vinyl rip of a vinyl release (a manual rip's lengths differ)" if vinyl else "")
    if not excuse and toc_proof and best["tracks_ok"] and not best.get("conflict"):
        # TOC-1: your disc's table of contents is this release's; the artist may differ
        excuse = "your disc's table of contents is this release's (exact disc ID), not its listed lengths"
    lengths_excused = False
    if not barcode_decides:
        if best["dur_frac"] is None or best["dur_frac"] < min_dur:
            if excuse:
                lengths_excused = True
                best["notes"] = best["notes"] + [f"track lengths not compared: {excuse}"]
            elif best.get("lengths_free") and min_dur <= cfg["auto_min_duration_frac"]:
                pass                          # (the release lists none; a partial title still needs them)
            elif best["dur_frac"] is None and (best["tracks_ok"] or best.get("aligned")):
                why.append("durations unavailable")
            elif best["dur_frac"] is None:
                pass                          # not compared: "track count differs" says why
            else:
                why.append(f"only {best['dur_frac']:.0%} of durations agree")
    # Your copy's own number proves the album and the pressing: the files' barcode or the
    # catalogue number your folder or tags name, every track there, the lengths agreeing.
    # Points lost elsewhere (a folder year that is the recording years, "2004 - 2012"; a
    # disc split differently) do not undo that proof (ISIS - Live I - VI, DYMC-150: 89.5).
    own_number = (proven and not split_credit and best["dur_frac"] is not None
                  and best["dur_frac"] >= min_dur and not best.get("conflict")
                  and (barcode_decides or best["code"] in ("catalogue number", "catalogue number (folder name)")))
    if best["confidence"] < cfg["auto_min_confidence"] and own_number and not lengths_excused:
        best["notes"] = best["notes"] + [f"confidence {best['confidence']} is under {cfg['auto_min_confidence']}, "
                                         f"but the {best['code']} proves this release"]
    elif best["confidence"] < cfg["auto_min_confidence"] and _one_length_off(best, cfg):
        best["notes"] = best["notes"] + ["every track title matches and only one length is off (ALB-1)"]
    elif best["confidence"] < cfg["auto_min_confidence"] and not lengths_excused and not (
            proven and (best["artist_sim"] < cfg["auto_min_artist"] or best["title_sim"] < cfg["auto_min_title"])
            and not split_credit):
        why.append(f"confidence {best['confidence']} < {cfg['auto_min_confidence']}")
    if best["cand"].get("version_unsure"):
        # (which tracks of the release each version folder is rests on order or on CD-like
        # audio: the album may be right, the medium is not proven)
        why.append(f"which tracks of the release each version is is not proven: {best['cand']['version_unsure']}")
    if best.get("other_version"):
        why.append(f"the release is the {best['other_version']} version, which your folder and tags do not say")
    if best.get("conflict"):
        why.append("barcode differs from the candidate's")
    # Your copy cannot be this format (hi-res audio and a CD; a rip log and a download):
    # however well the rest fits, a person decides (Ahab - The Coral Tombs: 24/48 audio
    # matched to the only CD listed). Not when your barcode is on it: the barcode names the
    # release, and the audio says only what it was ripped to (BAR-1; BAR-1a above).
    if not (barcode_agrees(best) and not best.get("conflict")):
        why += [x for x in format_contradictions(best) if not any(x in w for w in why)]
    why += unofficial_why(best)
    # A file numbered 0 as long as the release's hidden track before track 1 is no proof that
    # the files are that disc: only the exact disc ID or a fitting rip log's or CDTOC tag's TOC
    # with the track in its pregap is (with_pregap). On its length alone, a person confirms.
    if best["cand"].get("pregap_used") and not best["cand"].get("pregap_proven") and not _exact_toc(best):
        why.append("hidden track matched on its length only")
    py = best.get("pressing_year")
    if (py and best["cand"].get("year") and not strong_code
            and abs(best["cand"]["year"] - py) > cfg.get("pressing_year_tolerance", 0)):
        why.append(f"folder name says this is the {py} edition; matched release is from "
                   f"{best['cand']['year']}")
    # A barcode identifies one product: if only the best candidate's barcode
    # agrees with the files, the barcode picks the pressing (the plugin treats
    # a barcode match as decisive for the same reason).
    # Likewise a catalogue number from your tags or folder name: only one of
    # the look-alike pressings carries it.
    barcode_decided = (runner is not None and barcode_agrees(best) and not barcode_agrees(runner)
                       and not best.get("conflict"))
    catno_decided = (runner is not None and not barcode_decided and number_agrees(best)
                     and not number_agrees(runner) and not best.get("conflict"))
    if exact:
        # chosen by its lengths, above; only pressing-level proof on it makes that automatic
        # (an ISRC names the recordings, which the other release may share: not pressing-level)
        if not (strong_code or (best.get("clues") or {}).keys() & {"disc TOC", "source link"}):
            why.append(f"pressing chosen by track lengths alone; runner-up {runner['cand']['id']} scored "
                       f"{runner['confidence']} (likely another pressing)")
    elif scan_pick and abs(margin) < cfg["auto_min_margin"] and not (barcode_decided or catno_decided):
        others = [s["cand"]["id"] for s in tied if s is not best]
        said = "; ".join(scan_pick[1].values())
        best["notes"] = best["notes"] + [f"pressing chosen by {said} "
                                         f"(not on {', '.join(others[:3])})"]
        if any(k in FALLBACK_PICKS for k in scan_pick[1]) and (cfg.get("evidence_pass") or not kind_fits(best)):
            # (the evidence pass: the country order is still to come, and decides first: TIE-2)
            why.append(f"runner-up {runner['cand']['id']} within {abs(margin):.1f} points "
                       f"(likely another pressing)")
        # TIE-1: otherwise your country order, the original date or the fuller entry picks
        # the pressing, and it is tagged in full
    elif barcode_split:
        best["notes"] = best["notes"] + [f"pressing chosen by your barcode and what else your copy shows (not on "
                                         f"{', '.join(str(s['cand']['id']) for s in barcode_split[:3])})"]
    elif audio_split:
        best["notes"] = best["notes"] + [f"pressing chosen by your barcode and audio (the same barcode "
                                         f"is on {', '.join(str(s['cand']['id']) for s in audio_split[:3])}, "
                                         "which cannot hold this audio)"]
    elif margin < cfg["auto_min_margin"] and (barcode_decided or catno_decided):
        what = "barcode" if barcode_decided else "catalogue number"
        best["notes"] = best["notes"] + [f"pressing chosen by {what} (runner-up "
                                         f"{runner['cand']['id']} does not share it)"]
    elif margin < cfg["auto_min_margin"]:
        # A pressing that is the ONLY one agreeing with the files' year is
        # accepted on a smaller margin; a tie on year still needs a human.
        year_margin = cfg.get("auto_min_margin_year_decided")
        year_decided = (year_margin is not None and best["year_ok"] and runner is not None
                        and not runner["year_ok"] and margin >= year_margin)
        if year_decided:
            best["notes"] = best["notes"] + ["pressing chosen by year (runner-up "
                                             f"{runner['cand']['id']} has a different year)"]
        else:
            why.append(f"runner-up {runner['cand']['id']} within {margin:.1f} points "
                       f"(likely another pressing)")
            if all(s.get("own_catno_absent") for s in tied) and best.get("own_catnos"):
                why.append(f"catalogue number {', '.join(c.upper() for c in best['own_catnos'][:2])} "
                           "from your folder/tags is on none of these pressings (probably one the "
                           "databases do not list)")

    if kept_choice and best is kept_choice:
        # (another release scoring higher is no reason to leave the one you have: TAG-1/CHG-1)
        why = [w for w in why if not w.startswith("runner-up ") and not w.startswith("confidence ")]
        best["notes"] = best["notes"] + ["kept the release your files are tagged with (TAG-1)"]
    if not why:
        rival = _fits_to_the_ms(best, scored, cfg)
        if rival and (not _carries_barcode(best) or _carries_barcode(rival[0])):   # (your barcode names the release)
            runner, margin = rival[0], best["confidence"] - rival[0]["confidence"]
            why.append(f"{rival[0]['cand']['id']} fits every track to the millisecond ({rival[1]}); "
                       f"the pick does not")
    if not why:
        status = "auto"
    else:
        near_miss = (best["artist_sim"] >= cfg["near_miss_min_sim"]
                     and best["title_sim"] >= cfg["near_miss_min_sim"])
        # Same title, same track count and the track lengths agree, but the
        # artist credit differs: compilations ("Various"), performers
        # credited instead of the composer, etc. Worth a human look.
        content_match = ((best["tracks_ok"] or best.get("aligned"))
                         and best["title_sim"] >= cfg["near_miss_min_sim"]
                         and (best["dur_frac"] or 0) >= cfg["auto_min_duration_frac"])
        if content_match and best["artist_sim"] < cfg["auto_min_artist"]:
            why.append("title, track count and lengths agree but the artist credit differs "
                       "(compilation or performers credited?)")
        # Track count and lengths alone never identify an album (a 3-track
        # release has many look-alikes): something textual must relate too.
        related = (best["artist_sim"] >= 0.5 or best["title_sim"] >= 0.5 or bool(best["code"]))
        status = "review" if (related and (best["confidence"] >= cfg["review_min_confidence"]
                                           or near_miss or content_match)) else "nomatch"
    best["notes"] = list(dict.fromkeys(best["notes"]))     # classified twice: each note once
    return {"status": status, "best": best, "runner_up": runner,
            "margin": round(margin, 1), "why": why,
            # the thresholds it ran with, for what later reads its candidates (_only_digital)
            "cfg": {**{k: cfg[k] for k in ("auto_min_artist", "auto_min_title")},
                    "prefer_source": cfg.get("prefer_source") or "",
                    "evidence_pass": bool(cfg.get("evidence_pass"))}}


def _label_key(labels):
    return {" ".join(w for w in fold(x).split() if w not in _LABEL_FILLER) for x in str(labels).split(";")} - {""}


_NO_CATNO = {"none", "[none]", "nocatno", "[nocatno]", "n/a"}


def _catnos(c):
    """A candidate's catalogue numbers, without MusicBrainz's "[none]" (no number at all)."""
    return {x for x in c.get("catnos") or () if norm_code(x) not in _NO_CATNO}


def _lengths_agree(a, b, tol=2.0):
    """Same number of tracks, every length listed on both sides and within tol seconds.
    A Discogs File entry often lists no lengths: then nothing agrees, nothing is proven."""
    ta, tb = a.get("tracks") or (), b.get("tracks") or ()
    return len(ta) == len(tb) and bool(ta) and all(
        x.get("secs") and y.get("secs") and abs(x["secs"] - y["secs"]) <= tol for x, y in zip(ta, tb))


def linked_discogs(cand):
    """The Discogs release ids a MusicBrainz candidate links to (all of them: "discogs_ids",
    and "discogs_id", the first, for older callers)."""
    ids = {str(x) for x in cand.get("discogs_ids") or () if x}
    if cand.get("discogs_id"):
        ids.add(str(cand["discogs_id"]))
    return ids


def links_other_discogs(a, b):
    """One of the two is a MusicBrainz release that links to Discogs releases, and the
    other is a Discogs release not among them: MusicBrainz says they are two entries (a
    repress or second edition sharing the barcode, catalogue number and country)."""
    for m, d in ((a, b), (b, a)):
        links = linked_discogs(m) if m.get("source", "mb") == "mb" else set()
        if links and d.get("source") == "discogs" and str(d.get("id")) not in links:
            return True
    return False


def same_pressing(a, b, sole_digital=False, disc_on_both=False):
    """A MusicBrainz and a Discogs candidate for the same pressing: MusicBrainz links
    to that Discogs release, or the barcode, or the catalogue number and country agree.
    When MusicBrainz links to OTHER Discogs releases only, they are not the same pressing,
    unless disc_on_both (the caller knows your exact disc ID is on both).

    sole_digital: the caller knows each is the ONLY digital entry of its release group
    and master. Discogs often files a download under the original's year (Sunn O))) -
    Veils It White: MusicBrainz 2013, Discogs 2003), so the year may then differ."""
    if str(b.get("id") or "") and str(b.get("id")) in linked_discogs(a):
        return True
    if not disc_on_both and links_other_discogs(a, b):
        return False
    # A CD and a download, a download and an LP, are two releases, whatever they share
    # (Stygian Bough: the CD and the digital release have one barcode; Sunn O))) -
    # Rehearsal Demo: the File and the 12" share label, catalogue number, country and year)
    fa, fb = set(a.get("formats") or ()), set(b.get("formats") or ())
    if fa and fb and not fa & fb:
        return False
    a, b = dict(a, catnos=_catnos(a)), dict(b, catnos=_catnos(b))
    digital = sole_digital and fa == fb == {"digital"} and _lengths_agree(a, b)
    # one barcode is often printed on several countries' pressings (SPV's US and German
    # Enemy Of God Revisited; Noise's Coroner): a country or catalogue number that differs
    # makes them two pressings, whatever the barcode
    ca, cb = country_code(a.get("country")), country_code(b.get("country"))
    if (ca and cb and ca != cb and not {ca, cb} & {"XW", "XE"}) or (
            a.get("catnos") and b.get("catnos") and not catnos_agree(a.get("catnos"), b.get("catnos"))):
        return False
    if ({barcode_key(x) for x in a.get("barcodes") or ()} & {barcode_key(x) for x in b.get("barcodes") or ()}) - {""}:
        return True
    if (catnos_agree(a.get("catnos"), b.get("catnos"))
            and country_code(a.get("country")) == country_code(b.get("country"))):
        return True
    # one entry without a catalogue number (or written so differently it cannot be
    # compared): same label, country, year and track count is the same release
    # (Coroner - No More Color: Death Cult Switzerland, CH, 1989 on both)
    if (not (a.get("catnos") and b.get("catnos")) and a.get("label") and b.get("label")
            and _label_key(a.get("label")) & _label_key(b.get("label"))
            and country_code(a.get("country")) and country_code(a.get("country")) == country_code(b.get("country"))
            and a.get("year") and (a.get("year") == b.get("year") or digital)
            and len(a.get("tracks") or ()) == len(b.get("tracks") or ())):
        return True
    # A self-released album with no catalogue number anywhere (Aeon of Horus - Exile:
    # MusicBrainz "[no label]", Worldwide; Discogs "Not On Label (... Self-released)",
    # Australia): one release if the year, format and track count agree.
    def unlabelled(c):
        lab = fold(c.get("label") or "")
        return not lab or bool(re.search(r"no label|not on label|self released|self-released", lab))
    # a download dated differently must name its country (the same, or worldwide): an
    # entry with none could be any country's
    digital = digital and bool(ca and cb) and (ca == cb or "XW" in (ca, cb))
    return bool(not a.get("catnos") and not b.get("catnos") and unlabelled(a) and unlabelled(b)
                and a.get("year") and (a.get("year") == b.get("year") or digital)
                # a format left blank is unknown (as above), when every track length agrees
                # (Confessor - Blueprint Soul: MusicBrainz's self-released CD without a format)
                and (fa & fb or ((not fa or not fb) and _lengths_agree(a, b)))
                and len(a.get("tracks") or ()) == len(b.get("tracks") or ()))


# (not "isrc": an ISRC names a recording, and only MusicBrainz candidates can carry the clue,
# so it would always tell a MusicBrainz pick from a Discogs one without telling pressings
# apart; not "disc TOC (fuzzy)": a CD of about these lengths)
EVIDENCE_CLUES = ("disc TOC", "source link", "catalogue number", "barcode",
                  "label", "country", "format", "physical")


def _evidence(best):
    if not best:
        return {}
    clues = {k: v for k, v in (best.get("clues") or {}).items() if k in EVIDENCE_CLUES}
    if best["cand"].get("source") == "discogs":
        clues.pop("disc TOC", None)            # linked from MusicBrainz, not your copy's evidence
    if best.get("code") and best["code"] not in WEAK_BARCODES:     # (notes or another tagger's: no evidence)
        clues["code"] = best["code"]
    if best.get("folder_catno_agrees"):
        clues["folder catalogue number"] = True
    return clues


def has_evidence(best, other=None):
    """The pick rests on something about YOUR copy (its numbers, scans, folder name, audio,
    disc ID) that the OTHER pick does not also have: "a CD (folder name)" says nothing
    between two CDs (Death - Leprosy [2014 RM 3CD]: Relapse US vs Relapse Japan)."""
    mine, theirs = _evidence(best), _evidence(other)
    return any(k not in theirs for k in mine)


def _guessed(res, other):
    """A pick between MusicBrainz's and Discogs's pressings by the earliest date or your
    country order: a guess, not evidence, so the album is tagged without the pressing's
    IDs (album_only, which sees both pressings), not automatically."""
    rival = other["best"]
    scored = list(res.get("scored") or [res["best"]])
    if not any(s["cand"].get("source") == rival["cand"].get("source")
               and str(s["cand"].get("id")) == str(rival["cand"].get("id")) for s in scored):
        scored.append(rival)
    margin = abs(res["best"]["confidence"] - rival["confidence"])
    return dict(res, status="review", scored=scored, runner_up=rival, margin=round(margin, 1),
                why=[f"runner-up {rival['cand']['id']} within {margin:.1f} points (likely another pressing)"])


def _carries_barcode(s) -> bool:
    """A barcode read off your copy is on this pick, and no other barcode of yours argues."""
    return bool(s) and barcode_proves(s) and not s.get("conflict")


def _more_behind(a, b) -> bool:
    """A has something about your copy that B lacks, other than the barcode they share and what
    the audio suggests (a format clue only says how the copy was made)."""
    mine, theirs = set(_evidence(a)) - {"format"}, set(_evidence(b)) - {"format"}
    def own_number(s):
        return bool(s.get("own_catnos")) and catnos_agree(s["own_catnos"], s["cand"].get("catnos") or ())
    return bool(mine - theirs) or (own_number(a) and not own_number(b))


def _exact_toc(s):
    """This pick carries a disc ID of your files that MusicBrainz has attached to it."""
    return bool(s) and (s.get("clues") or {}).get("disc TOC") == EXACT_TOC_CLUE


def _only_digital(scored, pick=None, cfg=None):
    """The one download of the album among these candidates, or None when there are none or
    several. Another album's download a search brought in (Sunn O))) - Veils It White: the
    live sets "Pigs On Ice" and "Coronavirus Live Set 13") is not one of this album's: its
    artist or title fits neither the folder's nor the pick's, and its lengths fit the files
    worse than this album's download does. The pick itself is always this album's, and so is
    a download under another title whose lengths fit as well, or that carries your files'
    fingerprints (Bell Witch: the 2011 download "Bell Witch" of "Demo 2011")."""
    cfg = cfg or DEFAULT_CFG
    digital = [s for s in scored or () if set(s["cand"].get("formats") or ()) == {"digital"}]

    def named(s):
        if s is pick or (pick and s["cand"] is pick["cand"]):
            return True
        if s.get("artist_sim", 1.0) >= cfg["auto_min_artist"] and s.get("title_sim", 1.0) >= cfg["auto_min_title"]:
            return True
        return bool(pick) and (artist_sim(s["cand"].get("artist") or "", pick["cand"].get("artist") or "")
                               >= cfg["auto_min_artist"]
                               and title_sim(s["cand"].get("title") or "", pick["cand"].get("title") or "")
                               >= cfg["auto_min_title"])
    ours = [s for s in digital if named(s)]
    fits = [max(s["len_diffs"]) for s in ours if s.get("tracks_ok") and s.get("len_diffs")]

    def fits_as_well(s):
        return bool("fingerprint" in (s.get("clues") or {}) or (
            s.get("tracks_ok") and s.get("len_diffs") and (not fits or max(s["len_diffs"]) <= max(fits))))
    digital = [s for s in digital if s in ours or fits_as_well(s)]
    return digital[0] if len(digital) == 1 else None


def _sole_download(mb_one, dg):
    """Both are the only download of the album: MusicBrainz's in its whole release group,
    Discogs's in its whole master (match_albums counts them from the full listings,
    "group_digital" and "master_digital"; unknown when a listing was not fetched or ran
    to more than one page). Two candidate lists hold only the versions fetched."""
    return bool(mb_one and mb_one["cand"].get("group_digital") == 1 and dg.get("master_digital") == 1)


def _stated_apart(a, b):
    """Both entries state a country, catalogue number or barcode, and it differs (worldwide
    and Europe count as unstated, as in same_pressing)."""
    ca, cb = country_code(a.get("country")), country_code(b.get("country"))
    na, nb = _catnos(a), _catnos(b)
    ba, bb = ({barcode_key(x) for x in c.get("barcodes") or ()} - {""} for c in (a, b))
    # (MusicBrainz linking to other Discogs releases states it apart too: links_other_discogs)
    return bool(links_other_discogs(a, b) or (ca and cb and ca != cb and not {ca, cb} & {"XW", "XE"})
                or (na and nb and not catnos_agree(na, nb)) or (ba and bb and not ba & bb))


def _length_twin(mb_scored, dg_result):
    """The one MusicBrainz download of the Discogs download's year whose every track length
    agrees with it and that states no other country, number or barcode, when nothing else
    of that format and year on either side agrees with either of the two, or lists no
    lengths: one release listed twice, however each names its label or leaves out country
    and barcode (Soused: MusicBrainz 059a2bb2 without a country, Discogs 17682040 without a
    barcode; Cynic - ReFocus: Season of Mist vs Masvidalien Records)."""
    dg = dg_result["best"]["cand"]
    fd = set(dg.get("formats") or ())
    # downloads only: two CD pressings of one year often list the very same lengths (taken
    # from the same disc ID), and a label that differs then makes two products
    if fd != {"digital"} or not dg.get("year"):
        return None

    def same(s):
        return set(s["cand"].get("formats") or ()) == fd and s["cand"].get("year") == dg["year"]

    def unlisted(c):
        return not all(t.get("secs") for t in c.get("tracks") or ())
    mbs = [s for s in mb_scored if same(s) and _lengths_agree(s["cand"], dg) and not _stated_apart(s["cand"], dg)]
    if len(mbs) != 1 or any(same(s) and s is not mbs[0] and unlisted(s["cand"]) for s in mb_scored):
        return None
    one = mbs[0]["cand"]
    rivals = [s for s in dg_result.get("scored") or () if same(s) and s["cand"] is not dg
              and (_lengths_agree(s["cand"], one) or unlisted(s["cand"]))]
    return None if rivals else mbs[0]


def mb_twins(mb_result, dg_result):
    """MusicBrainz's candidates that are the Discogs pick's pressing. A download that is the
    only one of its release group and of its master may be dated differently (same_pressing)."""
    dg = dg_result["best"]["cand"]
    scored = (mb_result or {}).get("scored") or []
    mb_one = _only_digital(scored, (mb_result or {}).get("best"), (mb_result or {}).get("cfg"))
    dg_one = _only_digital(dg_result.get("scored") or [dg_result["best"]], dg_result["best"], dg_result.get("cfg"))
    sole = bool(mb_one and dg_one and dg_one["cand"] is dg and _sole_download(mb_one, dg))
    # your exact disc ID on the MusicBrainz release, and CTDB linking your disc to the Discogs
    # one: the same disc whatever MusicBrainz's Discogs links say
    dg_disc = "disc TOC" in (dg_result["best"].get("clues") or {})
    twins = [s for s in scored if same_pressing(s["cand"], dg, sole_digital=sole and s is mb_one,
                                                disc_on_both=dg_disc and _exact_toc(s))]
    if not twins and sole and _lengths_agree(mb_one["cand"], dg) and not links_other_discogs(mb_one["cand"], dg):
        # the only download on each side, with the same lengths, is one release however
        # the two list its label (Sunn O))) - Rehearsal Demo: MusicBrainz's Bandcamp entry
        # "[no label]", XW; Discogs's File on Ideologic Organ / Southern Lord, US). It
        # names the MusicBrainz ID beside the Discogs pick, never the pick itself.
        return [mb_one]
    if not twins:
        twin = _length_twin(scored, dg_result)
        twins = [twin] if twin else []
    return twins


def _format_evidence(best):
    """What your copy shows about its format, on this pick: a format clue (hi-res audio,
    a shop, whole CD sectors, a rip log) or a disc ID."""
    return {k: v for k, v in _evidence(best).items() if k in ("format", "disc TOC")}


def _noted(res, note):
    """res with note added to its pick's notes once: combine() runs more than once on one
    result (match_albums: with and without your country order)."""
    if note not in res["best"]["notes"]:
        res["best"]["notes"] = res["best"]["notes"] + [note]
    return res


def _disc_backed(best):
    """A rip log or scans of the disc back this CD up: the files' disc ID came off a CD.
    Read from the album (score_candidate "rip_log"), not from the format clue, which a
    folder name or MEDIA tag may have set first."""
    return bool(best.get("rip_log") or "physical" in (best.get("clues") or {}))


def _toc_maybe_download(mb_best, dg_best, *results):
    """The files' disc ID may be the download's: a download whose lengths are whole CD sectors
    gives the CD's own disc ID (match_albums.disc_tocs rebuilds it from the samples; Voivod -
    Lost Machine - Live), unless a rip log or the scans back the CD up. When the Discogs
    pick is itself a download, a disc ID rebuilt from the samples cannot argue against it:
    Discogs lists lengths to the second, so whether they are whole sectors is unknown."""
    if _disc_backed(mb_best):
        return False
    if set(dg_best["cand"].get("formats") or ()) == {"digital"}:
        return True
    return any(_sector_aligned_download(s) for r in results for s in (r or {}).get("scored") or ())


def _disputed(res, other, why):
    """res, sent to review with other as its runner-up: the two sources disagree, and
    what each rests on cannot both be true. HF-2: with a database preferred in Sources, its
    pick is taken in full instead (when it fits your tracks and no barcode of yours argues),
    with the dispute noted: nothing identified waits for you."""
    prefer = preferred_source(res, other)
    if prefer:
        side = "discogs" if prefer == "discogs" else "mb"
        mine = next((r for r in (res, other) if r and r.get("best")
                     and r["best"]["cand"].get("source") == side), None)
        b = (mine or {}).get("best")
        # (only a pick that database was sure of, or unsure of only between pressings of the
        # album: never one held back for its score, artist or tracks)
        doubts = [w for w in (mine or {}).get("why") or [] if not w.startswith("investigated")]
        settled = (mine or {}).get("status") == "auto" or (doubts and all(_TIE_WHY.match(w) for w in doubts))
        if b and settled and b.get("tracks_ok") and not b.get("conflict"):
            name = "Discogs" if side == "discogs" else "MusicBrainz"
            b = dict(b, notes=list(b.get("notes") or []) + [f"{why}; {name} chosen, as Sources prefers (HF-2)"])
            return dict(mine, status="auto", best=b, why=[])
    rival = other["best"]
    scored = list(res.get("scored") or [res["best"]])
    if not any(s is rival for s in scored):
        scored.append(rival)
    margin = abs(res["best"]["confidence"] - rival["confidence"])
    return dict(res, status="review", scored=scored, runner_up=rival, margin=round(margin, 1), why=[why])


def mb_overrules(mb_result, dg_result, twins):
    """MusicBrainz's sure pick against a sure Discogs pick of another pressing, when
    MusicBrainz's rests on evidence about your copy that Discogs's lacks. Returns the result
    to use (MusicBrainz's, or a review of the two), or None to let Discogs decide.

    - An exact disc ID (R6): your rip's disc ID is attached to MusicBrainz's release, and
      not to the Discogs pick's twin (Mournful Congregation - The Incubus Of Karma: the
      Japanese release carries it, the US twin has another disc ID 7 sectors shorter).
    - Format evidence (R4): MusicBrainz's pick is the format your copy shows (hi-res audio,
      a shop, whole sectors), the Discogs pick another format with nothing to say so
      (Dave Edwardson & Krallice - Loum: the download against the CD).
    When the Discogs pick has evidence of its own that MusicBrainz's lacks, the two
    contradict each other: a person decides."""
    mb_best = (mb_result or {}).get("best")
    dg_best = dg_result["best"]
    if not (mb_result and mb_result["status"] == "auto" and mb_best) or any(s is mb_best for s in twins):
        return None
    if _carries_barcode(mb_best) and not _carries_barcode(dg_best):
        return _noted(mb_result, "MusicBrainz chosen: your barcode is on its release, not on Discogs's pick")
    # a disc ID a sector-aligned download could give says nothing about the format
    toc_unsure = _toc_maybe_download(mb_best, dg_best, mb_result, dg_result)
    if _exact_toc(mb_best) and not toc_unsure and not any(_exact_toc(s) for s in twins):
        if has_evidence(dg_best, mb_best):
            return _disputed(mb_result, dg_result,
                             f"your disc ID is attached to MusicBrainz's {mb_best['cand']['id']}, but Discogs's "
                             f"pick {dg_best['cand']['id']} has evidence of its own")
        return _noted(mb_result, "MusicBrainz chosen: your disc ID is attached to its release, not to Discogs's pick")
    if _carries_barcode(dg_best) and not _carries_barcode(mb_best):
        return None                                      # (your barcode names it: the format says only how you copied it)
    fm, fd = set(mb_best["cand"].get("formats") or ()), set(dg_best["cand"].get("formats") or ())
    mine, theirs = _format_evidence(mb_best), _format_evidence(dg_best)
    if toc_unsure:
        mine.pop("disc TOC", None)
    # (both with format evidence: they contradict each other, whatever the clue)
    if fm and fd and not fm & fd and mine:
        if toc_unsure and fd == {"digital"} and mine == {"format": SECTOR_CLUE}:
            # whole sectors alone, against a download Discogs lists to the second, are
            # not CD proof (R8): a person decides
            return _disputed(mb_result, dg_result,
                             f"your tracks are whole CD sectors, which MusicBrainz's {mb_best['cand']['id']} "
                             f"(CD) and Discogs's download {dg_best['cand']['id']} may both be")
        if theirs or has_evidence(dg_best, mb_best):
            return _disputed(mb_result, dg_result,
                             f"MusicBrainz's {mb_best['cand']['id']} ({'/'.join(sorted(fm))}) and Discogs's "
                             f"{dg_best['cand']['id']} ({'/'.join(sorted(fd))}) disagree on the format, "
                             f"each with evidence")
        return _noted(mb_result, f"MusicBrainz chosen: your copy shows a {'/'.join(sorted(fm))} release "
                                 f"({next(iter(mine.values()))}); Discogs's pick is {'/'.join(sorted(fd))} "
                                 f"with nothing to say so")
    return None


def preferred_source(*results) -> str:
    """The database the profile's Sources setting prefers, as the results were classified
    with it (cfg "prefer_source"): "discogs", "musicbrainz", or "" (the matcher's own order,
    Discogs first, with a review where the two disagree)."""
    for r in results:
        p = ((r or {}).get("cfg") or {}).get("prefer_source")
        if p in ("discogs", "musicbrainz"):
            return p
    return ""


def _musicbrainz_decides(mb_result, dg_result):
    """Sources "MusicBrainz preferred": MusicBrainz's sure pick decides. Discogs's sure pick of
    the same pressing comes along ("dg_twin", for its tags), one of another pressing does
    not: no review, a note. Only when Discogs's pick alone rests on evidence about your copy
    (MusicBrainz's on none) is it taken instead."""
    mb_best = mb_result["best"]
    dg_best = (dg_result or {}).get("best")
    if not (dg_result and dg_result["status"] == "auto" and dg_best):
        return mb_result
    if any(s is mb_best for s in mb_twins(mb_result, dg_result)):
        if mb_best["cand"].get("discogs_id"):
            return mb_result
        return dict(mb_result, dg_twin=str(dg_best["cand"]["id"]))
    if _carries_barcode(dg_best) != _carries_barcode(mb_best):      # (your barcode names the release)
        if _carries_barcode(mb_best):
            return _noted(mb_result, "MusicBrainz chosen: your barcode is on its release, not on Discogs's pick")
        return _noted(dg_result, "Discogs chosen: your barcode is on its release, not on MusicBrainz's pick")
    theirs, mine = has_evidence(dg_best, mb_best), has_evidence(mb_best, dg_best)
    if theirs and not mine:
        return _noted(dg_result, "Discogs chosen: only its pressing has evidence about your copy behind it")
    if theirs and mine:
        # (each pick resting on evidence of its own about your copy contradicts the other:
        # _disputed takes the preferred database's sure pick, HF-2)
        return _disputed(mb_result, dg_result,
                         f"MusicBrainz's {mb_best['cand']['id']} and Discogs's {dg_best['cand']['id']} are "
                         "different pressings, each with evidence about your copy")
    tagged = _tagged_one(mb_result, dg_result)
    if tagged:
        return tagged
    return _noted(mb_result, f"Discogs's pick ({dg_best['cand']['id']}) is another pressing; MusicBrainz "
                             "decides (Sources: MusicBrainz preferred)")


def _tagged_one(*results):
    """The one of two sure picks of different pressings your files are already tagged with
    (a note says so), or None when both or neither are."""
    # (another tagger's release IDs pick nothing: tagger_tagged, as choose_pressing)
    mine = [r for r in results if r["best"].get("tagged") and not r["best"].get("tagger_tagged")]
    if len(mine) != 1:
        return None
    mine[0]["best"]["notes"] = mine[0]["best"]["notes"] + [
        "kept the release your files are tagged with; MusicBrainz and Discogs are "
        "each sure of another pressing"]
    return mine[0]


def _proof_left_unsure(mb_result, dg_result, prefer=None):
    """One database is sure, the other is not, and the unsure one's pick carries proof read
    off your copy (its barcode, or your exact disc ID where the disc ID cannot be a
    download's) while the sure pick has nothing of your copy that it lacks: the two
    contradict each other, and _disputed decides (HF-2; the unsure side is often unsure only
    because two of its entries share that proof: a Japanese CD with and without obi).
    Never when the two are one pressing (same_pressing, mb_twins), and the barcode is no
    contradiction when the sure pick carries the SAME barcode (Jon Hassell - Fascinoma:
    Discogs's SACD and MusicBrainz's entry share your barcode, and only the SACD can hold
    your 24-bit audio). Your barcode on the unsure pick alone always disputes (your barcode
    is the release). An exact disc ID does not dispute the sure pick of the database your
    Sources setting prefers (prefer: "discogs"/"musicbrainz"), nor one with that disc ID
    too. Returns the review, or None."""
    for sure, other in ((dg_result, mb_result), (mb_result, dg_result)):
        if not (sure and sure.get("status") == "auto" and sure.get("best")
                and other and other.get("status") != "auto" and other.get("best")):
            continue
        preferred = bool(prefer) and prefer == ("discogs" if sure is dg_result else "musicbrainz")
        sb, ob = sure["best"], other["best"]
        mb_best, dg_best = (ob, sb) if sure is dg_result else (sb, ob)
        same_barcode = (_carries_barcode(sb) and _carries_barcode(ob)
                        and bool(_keys(sb["cand"].get("barcodes")) & _keys(ob["cand"].get("barcodes"))))
        if _carries_barcode(sb) and _carries_barcode(ob) and not same_barcode:
            outer = _outer_barcode_pick([sb, ob])                       # BAR-3
            if outer is sb:
                continue
            if outer is ob:
                taken = _barcode_taken(other, ob)
                if taken:
                    return taken
        if barcode_proves(ob) and not same_barcode:
            proof = "barcode"
        elif (_exact_toc(ob) and not _exact_toc(sb) and not preferred
              and not _toc_maybe_download(mb_best, dg_best, mb_result, dg_result)):
            proof = "exact disc ID"
        else:
            continue
        if same_pressing(mb_best["cand"], dg_best["cand"]) or any(s is mb_best for s in mb_twins(mb_result, dg_result)):
            continue
        if has_evidence(sb, ob):
            continue
        name = {"mb": "MusicBrainz", "discogs": "Discogs"}
        # (two barcodes of yours naming two releases, neither on the outer packaging: two
        # products: _disputed, the preferred database's pick - BAR-3, HF-2)
        two_products = _carries_barcode(sb) and _carries_barcode(ob) and not same_barcode
        taken = _barcode_taken(other, ob) if proof == "barcode" and not two_products else None
        if taken:
            return taken                        # BAR-2: your barcode's release, not a review
        return _disputed(sure, other,
                         f"{name.get(ob['cand'].get('source'), 'the other database')}'s "
                         f"{ob['cand']['id']} carries your copy's {proof} but is not sure "
                         f"({'; '.join(other.get('why') or []) or other.get('status')}); "
                         f"{name.get(sb['cand'].get('source'), 'the other database')}'s sure pick "
                         f"{sb['cand']['id']} has nothing of your copy behind it")
    return None


def _barcode_taken(res, best):
    """BAR-2: the unsure pick carrying your barcode, taken as the release: its tracks fit,
    no barcode of yours argues, and its doubt is not a tie with another entry carrying the
    same barcode (a CD with and without obi: the barcode names both; HF-2 decides).
    None otherwise."""
    if not best.get("tracks_ok") or best.get("conflict") or not _carries_barcode(best):
        return None
    mine = _keys(best["cand"].get("barcodes"))
    if any(s is not best and _carries_barcode(s) and _keys(s["cand"].get("barcodes")) & mine
           and best["confidence"] - s["confidence"] < 5 for s in res.get("scored") or ()):
        return None
    name = {"mb": "MusicBrainz", "discogs": "Discogs"}.get(best["cand"].get("source"), "the other database")
    best = dict(best, notes=list(best.get("notes") or []) + [
        f"{name}'s pick carries your barcode: your barcode is the release (BAR-2)"])
    return dict(res, status="auto", best=best, why=[])


def _discogs_pressings_tied(dg_result, mb_best) -> bool:
    """Discogs, which lists more pressings, is held back only by a tie between pressings of a
    release that fits your files, and MusicBrainz's sure pick has nothing about your copy behind
    it: MusicBrainz is sure only because it lists fewer. The pressing is not known (album_only)."""
    best = (dg_result or {}).get("best")
    if not best or not mb_best or dg_result.get("status") != "review":
        return False
    why = [w for w in dg_result.get("why") or [] if not w.startswith("investigated")]
    return bool(why) and all(_TIE_WHY.match(w) for w in why) and bool(best.get("tracks_ok")) and \
        (best.get("dur_frac") or 0) >= 1.0 and not best.get("conflict") and not format_contradictions(best) and \
        not _evidence(mb_best).get("code") and not has_evidence(mb_best, best)   # (your barcode or number on it: proof)


def _yields_to_audio(res, other):
    """res, unsure, when your barcode is on both databases' picks and your audio rules out only
    res's: the barcode tells them apart no more than it does pressings of one database (classify;
    Stygian Bough: the CD and the download of one barcode)."""
    best, theirs = (res or {}).get("best"), (other or {}).get("best")
    if not (res and res["status"] == "auto" and _carries_barcode(best) and _carries_barcode(theirs)
            and format_contradictions(best) and not format_contradictions(theirs)
            and not _more_behind(best, theirs)):
        return res
    return dict(res, status="review", why=list(res.get("why") or []) + format_contradictions(best))


def _yields_to_cd(res, other, prefs=None):
    """res, unsure, when it is sure of a vinyl or cassette nothing about your copy points to and
    the other database is sure of a CD (no_media_evidence, between the databases)."""
    best, theirs = (res or {}).get("best"), (other or {}).get("best")
    if not (res and res["status"] == "auto" and best and theirs and other["status"] == "auto"
            and not prefers_vinyl_or_tape((prefs or {}).get("formats"))
            and any(x is best for x in no_media_evidence([best, theirs]))):
        return res
    return dict(res, status="review", why=list(res.get("why") or [])
                + [f"nothing about your copy points to {'/'.join(sorted(best['cand']['formats']))}, "
                   f"and {theirs['cand']['id']} is a CD"])


def combine(mb_result, dg_result, countries=None, prefs=None):
    """Discogs first: it lists far more pressings (catalogue numbers, licensed
    editions) than MusicBrainz. A confident Discogs match decides the pressing; the
    MusicBrainz release of that SAME pressing, when there is one, comes along
    ("mb_twin") so Picard can tag from MusicBrainz with Discogs filling in. When
    Discogs is not sure, a confident MusicBrainz match is used; else the better of
    the two goes to the review list. prefs (release_preferences): Picard's preferred
    release types and formats, after the country order, between the two sure picks.

    The profile's Sources setting (preferred_source): "musicbrainz" lets MusicBrainz's sure
    pick decide first (_musicbrainz_decides); "discogs" keeps Discogs first. Where the two
    are sure of different pressings, the preference only breaks a tie: evidence about your
    copy that only one pick has decides for it, evidence on both sides is a review, and the
    release your files are already tagged with is kept; only then does the preferred
    database's pick stand, in place of a guess. Neither: as above."""
    prefer = preferred_source(mb_result, dg_result)
    mb_result, dg_result = _yields_to_lengths(mb_result, dg_result), _yields_to_lengths(dg_result, mb_result)
    mb_result, dg_result = _yields_to_audio(mb_result, dg_result), _yields_to_audio(dg_result, mb_result)
    mb_result, dg_result = _yields_to_cd(mb_result, dg_result, prefs), _yields_to_cd(dg_result, mb_result, prefs)
    # (before either sure pick is taken: the unsure database's pick may carry your copy's proof)
    disputed = _proof_left_unsure(mb_result, dg_result, prefer)
    if disputed:
        return disputed
    if prefer == "musicbrainz" and mb_result and mb_result["status"] == "auto" and mb_result.get("best"):
        return _musicbrainz_decides(mb_result, dg_result)
    if dg_result and dg_result["status"] == "auto":
        out = dict(dg_result)
        mb_best = (mb_result or {}).get("best")
        twins = mb_twins(mb_result, dg_result)
        # (a review here is two picks each resting on evidence about your copy, an exact disc
        # ID or the format against Discogs's own: mb_overrules, then _disputed for HF-2)
        overrule = mb_overrules(mb_result, dg_result, twins)
        if overrule:
            return overrule
        if twins:
            # the release carrying your disc ID first (Stygian Bough: the CD with the
            # rip's disc ID, not the download that shares its barcode), then the surest
            out["mb_twin"] = max(twins, key=lambda s: (_exact_toc(s), s["confidence"]))["cand"]["id"]
        elif (mb_result and mb_result["status"] == "auto" and mb_best
              and not has_evidence(dg_result["best"], mb_best) and (has_evidence(mb_best, dg_result["best"])
                                                                         or countries)):
            # Discogs picked a pressing with nothing about your copy behind it, and
            # MusicBrainz is sure of another (Candlemass: Metal Mind, Poland vs Nuclear
            # Blast, Germany). Evidence decides; without any, your country order does.
            if has_evidence(mb_best, dg_result["best"]):
                mb_best["notes"] = mb_best["notes"] + [
                    "MusicBrainz chosen: only its pressing has evidence about your copy behind it"]
                return mb_result
            # Your files already tagged with one of the two: keep it
            tagged = _tagged_one(mb_result, dg_result)
            if tagged:
                return tagged
            if prefer == "discogs":
                # (neither pick has evidence about your copy: the preference breaks the tie,
                # in place of a guess by date or country order)
                return _noted(out, f"MusicBrainz's pick ({mb_best['cand']['id']}) is another pressing; "
                                   "Discogs decides (Sources: Discogs preferred)")
            if not countries and (mb_result.get("cfg", {}).get("evidence_pass")
                                  or dg_result.get("cfg", {}).get("evidence_pass")):
                return _guessed(out, mb_result)          # (the country order is still to come: TIE-2)
            # TIE-1/TIE-2: nothing of your copy tells the two apart: your country order picks one,
            # then the original release date, then your release types and formats; the pick
            # is tagged in full. Only when none of them decides is the pressing left open.
            rank = {c: i for i, c in enumerate(countries or ())}
            mb_r = rank.get(country_code(mb_best["cand"].get("country")), 99)
            dg_r = rank.get(country_code(dg_result["best"]["cand"].get("country")), 99)
            if mb_r < dg_r:
                mb_best["notes"] = mb_best["notes"] + [
                    "MusicBrainz chosen over Discogs's other pressing by your country order (TIE-1)"]
                return mb_result
            if dg_r < mb_r:
                return _noted(out, f"Discogs chosen over MusicBrainz's other pressing ({mb_best['cand']['id']}) "
                                   "by your country order (TIE-1)")
            first = prefer_original([mb_best, dg_result["best"]])
            if first and first[0] is mb_best:
                mb_best["notes"] = mb_best["notes"] + [
                    "MusicBrainz chosen over Discogs's other pressing: " + first[1]["original pressing"]]
                return mb_result
            if first:
                out["best"] = dict(out["best"], notes=out["best"]["notes"] + [first[1]["original pressing"]])
                return out
            # (by kind: the two databases name formats differently; only when both are
            # known to be one album: the MusicBrainz pick's release group links the Discogs
            # pick's master, its "discogs_master", same_album)
            pref = (prefer_type_and_format([mb_best, dg_result["best"]], prefs, by_name=False)
                    if countries else None)
            if pref and pref[0] is mb_best:
                mb_best["notes"] = mb_best["notes"] + [
                    "MusicBrainz chosen over Discogs's other pressing: " + next(iter(pref[1].values()))]
                return mb_result
            if pref:
                out["best"] = dict(out["best"], notes=out["best"]["notes"] + [next(iter(pref[1].values()))])
                return out
            return _guessed(out, mb_result)
        elif mb_result and mb_result["status"] == "auto" and mb_best:
            dg_result["best"]["notes"] = dg_result["best"]["notes"] + [
                f"MusicBrainz's pick ({mb_best['cand']['id']}) is another pressing; Discogs decides"]
        return out
    if mb_result and mb_result["status"] == "auto":
        if _discogs_pressings_tied(dg_result, mb_result.get("best")):
            _noted(dg_result, f"MusicBrainz lists one pressing ({mb_result['best']['cand']['id']}), "
                              "but nothing about your copy shows it is that one")
            # (its pressing is one of those tied; match_albums falls back on it when even the
            # album-only tags cannot be written: _sure_if_no_album_only)
            return dict(_guessed(dg_result, mb_result), sure_elsewhere=mb_result)
        return mb_result
    order = {"review": 0, "nomatch": 1}
    options = [r for r in (mb_result, dg_result) if r and r["best"]]
    if not options:
        return mb_result or dg_result
    # a pick your files contradict (hi-res vs a CD, a rip log vs a download) is never shown
    # over one your copy's own numbers back (Sadist - Spellbound: the digital release, with
    # your CD rip log against it, over the Fono CD with your barcode and catalogue number).
    # Your copy's barcode on a pick outweighs that: the barcode names the product, the audio
    # only how it was copied (BTBAM - The Great Misdirect: the Russian CD 3190836 carries the
    # barcode read on the scans, its tracks are not whole CD sectors; the 2019 download
    # "[2019 RMX RM]" in the folder name points to carries another barcode).
    # When the other pick carries that barcode too (Stygian Bough: the CD and the download
    # share one), it tells neither apart, and the contradiction counts again.
    def own_barcode(best):
        return bool(best) and barcode_proves(best) and not best.get("conflict")

    def rank(r):
        other = next((o["best"] for o in options if o is not r), None)
        waived = own_barcode(r["best"]) and (not own_barcode(other) or _more_behind(r["best"], other))
        # a pick that holds the files' tracks before one that does not (Feared - Reborn: the
        # 2-CD Discogs entry over MusicBrainz's 12-track CD, whose disc ID is disc 1's alone)
        fits = bool(r["best"].get("tracks_ok") or r["best"].get("aligned"))
        other_fits = bool(other and (other.get("tracks_ok") or other.get("aligned")))
        return (order[r["status"]], bool(format_contradictions(r["best"])) and not waived,
                not fits and other_fits, not has_evidence(r["best"], other), -r["best"]["confidence"])
    # min(), not options.sort(): while a list sorts, it reads as empty, so rank() saw no other
    return min(options, key=rank)


# ---------------------------------------------------------------------------
# Album known, pressing not (John, 2026-10-25): when every check and the whole
# investigation leave pressings of ONE album tied, the album is still tagged, with
# what is true of all of them, and nothing that would name one pressing.
# ---------------------------------------------------------------------------
_TIE_WHY = re.compile(r"^runner-up \S+ within [\d.]+ points \(likely another pressing\)$")

# Fields that name one pressing: never written for an album whose pressing is unknown.
PRESSING_FIELDS = {"musicbrainz_albumid", "musicbrainz_trackid", "musicbrainz_discid", "discogs_release_id",
                   "discogs_uri", "discogs_released", "discogs_year", "discogs_notes", "discogs_catalognumber",
                   "catalognumber", "barcode", "asin"}


_LOW_SCORE_WHY = re.compile(r"^confidence [\d.]+ < [\d.]+$")


def _no_lengths_tie(best, why) -> bool:
    """The pick is held back only because the entries tied with it list no track lengths (which
    also lowers its score): artist, title and the tracks all match, and nothing contradicts it."""
    rest = [w for w in why if not _TIE_WHY.match(w)]
    if len(rest) == len(why) or not (best.get("lengths_free") or "durations unavailable" in rest) or \
            not all(w == "durations unavailable" or _LOW_SCORE_WHY.match(w) for w in rest):
        return False
    titles = best.get("titles_agree")
    return bool(best.get("artist_sim", 0) >= 1.0 and best.get("title_sim", 0) >= 1.0 and best.get("tracks_ok")
                and best.get("dur_frac") is None and best.get("dur_cov") == 0 and not best.get("conflict")
                and (titles is None or titles == len(best["cand"]["tracks"])))


def album_only(final, cfg, source=""):
    """The tied pressings when the ALBUM is certain and only its pressing is not: the pick
    is as sure as an automatic one (score, title, artist, every track length), and the only
    doubt is a runner-up of the same album. None otherwise."""
    best = (final or {}).get("best")
    if not best or final.get("status") == "auto":
        return None
    why = [w for w in final.get("why") or [] if not w.startswith("investigated")]
    no_lengths = _no_lengths_tie(best, why)
    if not no_lengths and (not why or not all(_TIE_WHY.match(w) for w in why)):
        return None
    if not no_lengths and (best["confidence"] < cfg["auto_min_confidence"] or not best.get("tracks_ok")
                           or (best.get("dur_frac") or 0) < 1.0 or best.get("conflict")):
        return None
    n = len(best["cand"]["tracks"])
    close = [s for s in final.get("scored") or [best] if best["confidence"] - s["confidence"] < cfg["auto_min_margin"]
             and not s.get("unproven_media")]
    tied = [s for s in close
            if title_sim(s["cand"]["title"], best["cand"]["title"]) >= 0.85 and len(s["cand"]["tracks"]) == n]
    if no_lengths and (len(tied) != len(close) or any(s.get("dur_cov") != 0 for s in tied)):
        return None                                      # (another album as close, or lengths that could disagree)
    if best.get("pressing_year") and best.get("year_ok"):
        tied = [s for s in tied if s.get("year_ok")]   # the edition your folder names, not the others
    if len(tied) < 2:
        return None
    # what the files are (a CD rip, a download, a vinyl rip) narrows the pressings whose
    # shared fields are written: a CD rip is tagged from the CDs only, so its media is "CD"
    other_kind = False
    if source:
        same_kind = [s for s in tied if source in (s["cand"].get("formats") or ())]
        # none of them is what your copy is (a download of an album listed only on vinyl and CD,
        # a CD rip among vinyl pressings): the album is still known, only its media and numbering
        # cannot be theirs (album_only_tags)
        other_kind = not same_kind and any(s["cand"].get("formats") for s in tied)
        tied = same_kind or tied
    return {"source": source, "no_lengths": no_lengths, "other_kind": other_kind, "tied": [{"source": s["cand"]["source"], "id": str(s["cand"]["id"]), "label": s["cand"].get("label") or "",
                      "country": s["cand"].get("country") or "", "formats": sorted(s["cand"].get("formats") or ()),
                      "year": s["cand"].get("year"), "layout": disc_layout(s["cand"])} for s in tied]}


def scans_name_another(final, report):
    """What album_only returns, when the pick was sure but your scans name another pressing
    and nothing of yours is on the pick (no barcode, catalogue number or disc ID): the album
    is certain, its pressing is not. report: match_albums' scans report. None otherwise."""
    best = (final or {}).get("best")
    if not best or (report or {}).get("state") != "differs" or set(report.get("found") or ()) & {"barcode", "catalogue_number"}:
        return None
    if _carries_barcode(best) or (best.get("code") and best["code"] not in WEAK_BARCODES):
        return None
    if (best.get("clues") or {}).get("disc TOC") and best["cand"].get("source") != "discogs":
        return None
    if (best.get("clues") or {}).get("source link") or (best.get("tagged") and not best.get("tagger_tagged")):
        return None                             # (your files' link, or your own earlier choice)
    n = len(best["cand"]["tracks"])
    same = [s for s in final.get("scored") or [best]
            if title_sim(s["cand"]["title"], best["cand"]["title"]) >= 0.85 and len(s["cand"]["tracks"]) == n
            and not s.get("unproven_media")]
    return {"source": "", "no_lengths": False, "other_kind": False, "scans": True,
            "tied": [{"source": s["cand"]["source"], "id": str(s["cand"]["id"]), "label": s["cand"].get("label") or "",
                      "country": s["cand"].get("country") or "", "formats": sorted(s["cand"].get("formats") or ()),
                      "year": s["cand"].get("year"), "layout": disc_layout(s["cand"])} for s in same]}


def disc_layout(cand):
    """A candidate's audio tracks per disc, in disc order ([] when it lists none)."""
    counts = {}
    for t in cand.get("tracks") or []:
        if not t.get("video"):
            d = t.get("disc") or 1
            counts[d] = counts.get(d, 0) + 1
    return [counts[d] for d in sorted(counts)]


# what one pressing numbers its own way: a CD's track 7 may be a 2xLP's disc 2, track 1
TRACK_NUMBER_FIELDS = {"tracknumber", "totaltracks", "discnumber", "totaldiscs", "~absolutetracknumber",
                       "~totalalbumtracks", "discsubtitle", "~discpregap"}
# what claims the one recording of a track (another pressing may have another recording -
# a remaster, a re-recording - with other credits and ISRCs)
RECORDING_FIELDS = {"musicbrainz_recordingid", "isrc", "engineer", "mixer", "producer", "remixer", "djmixer",
                    "arranger", "conductor", "orchestra", "chorusmaster", "~recordingcomment",
                    "~recording_firstreleasedate", "~recordingtitle"}


def album_only_tags(tags, tied, same_recordings=False, other_kind=False):
    """One file's tags, kept to what every tied pressing shares: release IDs, catalogue
    numbers, barcode, ASIN and a pressing's own notes and companies are dropped; label,
    release country, media and release date stay only when all the pressings agree on them
    (else the date becomes the album's original date). Track and disc numbers stay only when
    every pressing is of one media and one disc layout; recording IDs, ISRCs and recording
    credits only when the caller has shown every pressing has the same recordings
    (same_recordings). other_kind: your copy is none of these media, so neither their media nor
    their numbering is written, even when they all agree."""
    out = {k: v for k, v in tags.items() if k not in PRESSING_FIELDS and not k.startswith("discogs_company_")}
    same = lambda vals: len(vals) == 1 and "" not in vals and None not in vals   # noqa: E731
    if other_kind or not (same({",".join(t.get("formats") or ()) for t in tied})
                          and same({",".join(str(n) for n in t.get("layout") or ()) for t in tied})):
        for k in TRACK_NUMBER_FIELDS:
            out.pop(k, None)
    if not same_recordings:
        for k in list(out):
            if k in RECORDING_FIELDS or k.startswith("performer:"):
                out.pop(k, None)
    if not same({fold(t.get("label") or "") for t in tied}):
        out.pop("label", None)
    if not same({country_code(t.get("country")) or "" for t in tied}):
        out.pop("releasecountry", None)
    if other_kind or not same({",".join(t.get("formats") or ()) for t in tied}):
        out.pop("media", None)
    if not same({t.get("year") for t in tied}):
        original = out.get("originaldate") or out.get("originalyear")
        if original:
            out["date"] = original
        else:
            out.pop("date", None)
    return out
