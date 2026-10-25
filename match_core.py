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
import re
import unicodedata

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


def _romanize_cjk(text):
    """Japanese/Chinese in Latin letters when pykakasi is installed (else unchanged)."""
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
    r"surround|stereo|mono|boxset|box|set|hi-?res|lossless|\+)$", re.I)
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
    parts = re.split(r"\s*(?:;|/|,|&|\band\b|\bwith\b|\bfeat\.?|\bfeaturing\b|\bvs\.?\b|\bx\b)\s*",
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


def valid_barcode(code):
    """EAN-13 / UPC-A check digit, so a phone number or a run of noise is not a barcode."""
    code = str(code or "")
    if not code.isdigit() or len(code) not in (12, 13):
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
                  "dr", "rms", "khz", "bit", "hz", "db"}             # dynamic-range reports in rip notes
# US state + ZIP code ("CA 90064", "NY 11576") on label addresses
_US_STATES = set("al ak az ar ca co ct de fl ga hi id il in ia ks ky la me md ma mi mn ms mo mt ne nv "
                 "nh nj nm ny nc nd oh ok or pa ri sc sd tn tx ut vt va wa wv wi wy dc".split())
_OCR_CATNO_RE = re.compile(
    r"(?<![A-Za-z0-9])([A-Z][A-Za-z]{1,9}(?:[ -]?(?:CD|LP|DVD|SACD|MC|CDR|DA))?[ -]?\d{2,7}(?:[-/]\d{1,3})?"
    r"(?:(?:CD|LP|DVD|SACD|MC|CDR|DG)[A-Z]{0,2})?)"    # "FO532CD", "QDISS0201CDD": the format after the number
    r"(?![A-Za-z0-9])")
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


def catno_same(a, b):
    """Two catalogue numbers for one product, however written: equal; with a format
    word added or inside ("SPV 91978" / "SPV 91978 Box", "MASS 1389" / "MASS CD 1389 DG",
    "CG-0004" / "DIGICD CG-0004"); with the label's letters in front ("CD0540" /
    "MAS CD0540", "079" / "GUN 079"); with a trailing disc digit ("RR 9117" / "RR 9117 2");
    or a long number with and without its check digit ("99833708" / "9983370")."""
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
            and long_.startswith(short) and re.fullmatch(fmt + r"|[1-9]", long_[len(short):])):
        return True                                  # a format word or disc digit after
    if long_.endswith(short) and len(short) >= 3 and re.search(r"[a-z]", short) and digits \
            and re.fullmatch(fmt, long_[:-len(short)]):
        return True
    if a.isdigit() and b.isdigit() and len(short) >= 7 and long_[:-1] == short:
        return True
    return False


def catnos_agree(mine, theirs):
    return any(catno_same(x, y) for x in mine or () for y in theirs or ())


def barcode_key(value):
    """UPC-A (12 digits) and EAN-13 (the same code with a leading 0) are one
    barcode; compare on the digits without leading zeros."""
    return re.sub(r"\D", "", str(value or "")).lstrip("0")


def barcode_variants(value):
    """Forms to search for: as given, 12-digit UPC, 13-digit EAN."""
    key = barcode_key(value)
    if not key:
        return []
    out = [key.zfill(13)]
    if len(key) <= 12:
        out.append(key.zfill(12))
    return out


def duration_ok(file_secs, cand_secs, cfg):
    tol = max(cfg["duration_tol_s"], cfg["duration_tol_pct"] * cand_secs)
    return abs(file_secs - cand_secs) <= tol


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


def country_code(name):
    """ISO-like code for a release country given either way ("DE" or "Germany")."""
    raw = str(name or "").strip()
    if re.fullmatch(r"[A-Z]{2}", raw):
        return raw
    return _COUNTRY_NAMES.get(re.sub(r"[^a-z ]", " ", raw.casefold()).strip().replace("  ", " "), "")


# A label's or manufacturer's postal address on the back or in the credits says where the
# pressing comes from: "Prophecy Productions, D-54550 Daun", "Century Media Inc., 2323 W.
# El Segundo Blvd, Hawthorne, CA 90250", "Earache, PO Box 144, Nottingham NG3 4GE, UK".
# Only postal forms count (not a city named in the thanks or "Recorded in Montréal").
_EU_POSTAL = {"D": "DE", "A": "AT", "CH": "CH", "F": "FR", "NL": "NL", "B": "BE", "I": "IT", "S": "SE",
              "DK": "DK", "N": "NO", "FIN": "FI", "SF": "FI", "PL": "PL", "E": "ES", "P": "PT", "GR": "GR",
              "CZ": "CZ", "H": "HU", "L": "LU"}
_EU_POSTAL_RE = re.compile(r"(?<![A-Za-z0-9])(D|A|CH|F|NL|B|I|S|DK|N|FIN|SF|PL|E|P|GR|CZ|H|L)\s?-\s?(\d{4,5})"
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


def countries_from_addresses(ocr_text):
    """Countries whose postal address is printed on the scans."""
    text = str(ocr_text or "")
    out = {_EU_POSTAL[m.group(1)] for m in _EU_POSTAL_RE.finditer(text)}
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


def pressing_clues(ocr_text, cand):
    """What the scans say about this particular pressing.

    Returns (agree, disagree): agree names the clues printed on the scans that
    this pressing carries (its catalogue number, barcode, label, country);
    disagree names a clue it contradicts (the sleeve says "Made in Japan" and
    this is the German pressing)."""
    agree, disagree = {}, []
    if not ocr_text:
        return agree, disagree
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
    digits_hay = re.sub(r"\D", "", ocr_text)
    for b in sorted(cand.get("barcodes") or ()):
        key = barcode_key(b)
        if len(key) >= 11 and key in digits_hay:
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
        elif mine in printed:
            name = cand.get("country") or ""
            if len(name) == 2:
                name = next((n for n, c in _COUNTRY_NAMES.items() if c == name and len(n) > 3), name)
                name = name.upper() if name in ("us", "uk", "eu") else name.title()
            agree["country"] = f"made in {name} on the scans"
        else:
            disagree.append(f"scans say made in {'/'.join(sorted(printed))}, "
                            f"this pressing is from {cand.get('country')}")
    return agree, disagree


_VINYL_HINT = re.compile(r"vinyl|(?<![A-Za-z0-9])\d*\s*LPs?\b", re.I)
_CD_HINT = re.compile(r"(?<![A-Za-z0-9])\d*\s*CDs?\b|\bSACD\b|\bHDCD\b", re.I)    # not "FO40CD"
_DIGITAL_HINT = re.compile(r"\b(?:RM|RMX|remaster(?:ed)?|web|hi-?res)\b|\d+\s*-?\s*bit|k\s*hz", re.I)


def media_hint(folder):
    """What the folder name says the copy was made from: "vinyl", "cd", or
    "digital" (a remaster or hi-res download with no CD or vinyl named)."""
    text = str(folder or "")
    if _VINYL_HINT.search(text):
        return "vinyl"
    if _CD_HINT.search(text):
        return "cd"
    if _DIGITAL_HINT.search(text):
        return "digital"
    return ""


def media_kind(fmt):
    """A database format name as "cd", "vinyl", "digital" or "cassette"."""
    f = str(fmt or "").casefold()
    if "vinyl" in f or re.search(r'\b(?:7|10|12)"', f) or f in ("lp", "flexi-disc"):
        return "vinyl"
    if "digital" in f or f in ("file", "download"):
        return "digital"
    if "cd" in f:
        return "cd"
    if "cassette" in f:
        return "cassette"
    return ""


CLUE_WEIGHTS = {"disc TOC": 3, "barcode": 3, "catalogue number": 3, "source link": 3, "isrc": 2,
                "fingerprint": 2, "date": 2, "label": 1,
                "country": 1, "format": 1, "physical": 1, "track lengths": 1, "track titles": 1, "script": 1}


def _readable_country(code_or_name):
    name = str(code_or_name or "")
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


def file_clues(album, cand, clues, against):
    """Clues to the pressing from the files and their folder (the scans are
    read by pressing_clues): the disc ID, the tags, the folder name, the audio."""
    formats = set(cand.get("formats") or ())
    # A disc ID identifies a CD only when it has a few tracks: a one- or two-track TOC is
    # just "a CD this long", which unrelated CDs share (The Ocean - The Grand Inquisitor IV,
    # a one-track single, "matched" Michael Mauldin's The Last Musician of Ur).
    if (len(album.get("tracks") or ()) >= 3 and
            ((cand.get("source") == "mb" and cand.get("id") in (album.get("toc_releases") or ()))
             or (cand.get("source") == "discogs" and str(cand.get("id")) in (album.get("toc_discogs") or ())))):
        clues["disc TOC"] = "disc ID (track positions of the CD) matches"
    if ((cand.get("source") == "mb" and cand.get("id") in (album.get("link_releases") or ()))
            or (cand.get("source") == "discogs" and str(cand.get("id")) in (album.get("tag_discogs_links") or ()))):
        clues["source link"] = "the files' own tags link to this release"
    if cand.get("source") == "mb" and cand.get("id") in (album.get("isrc_releases") or ()):
        clues["isrc"] = "the files' ISRC codes are on this release"
    if cand.get("source") == "mb" and cand.get("id") in (album.get("fp_releases") or ()):
        clues["fingerprint"] = "audio fingerprints (AcoustID) are on this release"
    # tags written by whoever ripped or bought it
    label = album.get("tag_label")
    if label:
        mine = [w for w in fold(label).split() if w not in _LABEL_FILLER]
        theirs = [" ".join(w for w in fold(x).split() if w not in _LABEL_FILLER)
                  for x in str(cand.get("label") or "").split(";")]
        if mine and any(" ".join(mine) == x or (x and (" ".join(mine) in x or x in " ".join(mine)))
                        for x in theirs):
            clues.setdefault("label", f"label {label} in the tags")
    tc = country_code(album.get("tag_country"))
    cc = country_code(cand.get("country"))
    if tc and cc:
        if tc == cc:
            clues.setdefault("country", f"release country {_readable_country(tc)} in the tags")
        elif "XW" not in (tc, cc):
            against.append(f"tags say {tc}, this pressing is from {cand.get('country')}")
    tm = album.get("tag_media")
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
    td, cd = str(album.get("tag_date") or ""), str(cand.get("date") or "")
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
        if named and cc and cc != "XW":
            if cc in named:
                clues.setdefault("country", f"{_readable_country(cc)} in the folder name")
            elif not (cc == "XE" and named & _EUROPE) and not ("XE" in named and cc in _EUROPE):
                # "Germany" on a European pressing (made there, sold across Europe) agrees
                against.append(f"folder name says {'/'.join(sorted(named))}, "
                               f"this pressing is from {cand.get('country')}")
    # what it was made from: folder name, then the audio itself
    hint = album.get("media_hint") or ""
    audio = album.get("audio") or {}
    if formats:
        if hint and hint in formats:
            clues.setdefault("format", f"{hint} release (folder name)")
        elif hint in ("vinyl", "cd") and hint not in formats:
            against.append(f"folder name says {hint}, this pressing is {'/'.join(sorted(formats))}")
        if audio.get("hires") and hint != "vinyl":
            if "digital" in formats:
                clues.setdefault("format", "digital release (hi-res audio, which no CD carries)")
            elif formats == {"cd"}:
                against.append("hi-res audio cannot come from this CD")
        elif audio.get("cd_frames") and not hint and "cd" in formats:
            clues.setdefault("format", "CD (tracks are whole CD sectors, as ripped from a disc)")
        elif audio.get("not_cd") and not hint and "digital" in formats:
            clues.setdefault("format", "digital release (lossless tracks are not whole CD sectors, so not ripped from a CD)")
        # scans of a back cover, tray card, booklet or disc: the copy is physical
        scans = album.get("scans") or {}
        digital_audio = (audio.get("hires") and hint != "vinyl") or audio.get("not_cd") or hint == "digital"
        if scans.get("physical") and not digital_audio:
            if formats & {"cd", "vinyl", "cassette"}:
                clues.setdefault("physical", f"physical release (your folder has {scans.get('why')})")
            elif formats == {"digital"}:
                against.append(f"your folder has {scans.get('why')}, so not this digital release")


def _relative_clues(tied):
    """Clues that only make sense against the other pressings: whose track
    lengths are clearly closest, whose track titles agree with more tracks."""
    extra = {id(s): {} for s in tied}
    errs = sorted((s.get("len_err"), id(s)) for s in tied if s.get("len_err") is not None)
    if len(errs) == len(tied) and len(errs) >= 2 and errs[0][0] <= 1.0 and errs[1][0] >= errs[0][0] + 1.5:
        extra[errs[0][1]]["track lengths"] = (f"track lengths closest (off by {errs[0][0]:.1f}s on average, "
                                              f"others {errs[1][0]:.1f}s+)")
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
            return None                                # several from that country: still undecided
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


def choose_pressing(tied, preferred_countries=None):
    """Among pressings that score the same, the one the evidence points to, or None.

    Only clues that tell the tied pressings apart count (a label shared by all
    of them says nothing). Clues are weighed (a disc ID, barcode or catalogue
    number counts most); the pick must outweigh every other pressing and must
    not contradict anything the files, folder or scans say."""
    if len(tied) < 2:
        return None
    rel = _relative_clues(tied)
    allc = {id(s): dict(s.get("clues") or {}, **rel[id(s)]) for s in tied}
    # A Discogs pressing "matches the disc ID" only because a MusicBrainz release with
    # that disc ID links to it; the other Discogs pressings of the same master simply
    # lack such a link (Alcest - Les Chants de l'aurore: the Brazilian licence was picked
    # over Nuclear Blast's own CD). So among Discogs pressings it tells nothing apart.
    for s in tied:
        if s["cand"].get("source") == "discogs":
            allc[id(s)].pop("disc TOC", None)
    scored = []
    for s in tied:
        mine = allc[id(s)]
        telling = {k: v for k, v in mine.items()
                   if not all(allc[id(o)].get(k) == v for o in tied)}
        weight = sum(CLUE_WEIGHTS.get(k, 1) for k in telling)
        scored.append((weight, telling, s))
    scored.sort(key=lambda x: -x[0])
    weight, telling, pick = scored[0]
    if weight == 0 or scored[1][0] == weight or pick.get("clues_against"):
        if any(s.get("own_catno_absent") for s in tied) and all(s.get("own_catno_absent") for s in tied):
            return None                                # your pressing is not among them
        return (same_pressing_listed_twice(tied) or prefer_original(tied)
                or prefer_country(tied, preferred_countries))
    if all(s.get("own_catno_absent") for s in tied) and weight < CLUE_WEIGHTS["disc TOC"]:
        return None              # your copy's catalogue number is on none of them: only hard proof decides
    return pick, telling


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
    if len(keys) != 1 or not k[0] or len(codes) > 1 or any(s.get("clues_against") for s in tied):
        return None
    # the better-scoring entry first (Sleep's Holy Mountain [2015 ReIssue]: the entry
    # dated 2015 over one dated 1992 with the same barcode), then the fuller one
    pick = max(tied, key=lambda s: (s["confidence"], not s.get("year_off"),
                                    len(str(s["cand"].get("date") or "")), len(s["cand"].get("tracks") or ()),
                                    -tied.index(s)))
    return pick, {"duplicate": "the same pressing listed twice (same label, catalogue number and barcode); "
                               "the fuller entry is used"}


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


def score_candidate(album, cand, cfg, ocr_text=""):
    """Score one candidate against one album. Returns a dict with the
    confidence (0-100), the evidence for it and every gate that failed."""
    pts = avail = 0.0
    notes = []

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
    t_sim = title_sim(album["title"], cand["title"])
    pts += cfg["w_artist"] * a_sim
    avail += cfg["w_artist"]
    pts += cfg["w_title"] * t_sim
    avail += cfg["w_title"]
    notes.append(f"artist {a_sim:.2f}, title {t_sim:.2f}")
    lacks = missing_collaborators(album["artist"], [cand["artist"], cand.get("artist_alt") or "",
                                                     cand.get("artist_sort") or ""])
    if lacks:
        notes.append(f"credit lacks {', '.join(lacks)}")

    n_file, n_cand = len(album["tracks"]), len(cand["tracks"])
    tracks_ok = n_file > 0 and n_file == n_cand
    avail += cfg["w_tracks"]
    if tracks_ok:
        pts += cfg["w_tracks"]
    else:
        notes.append(f"track count differs ({n_file} files vs {n_cand})")

    # barcode / catalogue number
    code = ""
    conflict = False
    a_codes = {barcode_key(b) for b in album["barcodes"]} - {""}
    c_codes = {barcode_key(b) for b in cand["barcodes"]} - {""}
    if a_codes and c_codes:
        if a_codes & c_codes:
            code = "barcode"
        else:
            conflict = True      # a different product: never auto
            avail += cfg["w_code"]
            notes.append("barcode differs")
    if not code and album["catnos"] and cand["catnos"]:
        shared = album["catnos"] & cand["catnos"]
        if shared:
            code = ("catalogue number (folder name)"
                    if shared <= set(album.get("catnos_from_folder") or ()) else "catalogue number")
    if not code and ocr_text:
        hay = norm_code(ocr_text)
        for c in cand["catnos"]:
            # Only distinctive catalogue numbers (letters AND digits, 5+
            # characters, e.g. "omm0132"): a bare number like "2003" also
            # matches a printed year, a phone number or a barcode fragment.
            if len(c) >= 5 and re.search(r"[a-z]", c) and re.search(r"\d", c) and c in hay:
                code = "catalogue number (OCR)"
                break
        digits_hay = re.sub(r"\D", "", ocr_text)
        for b in cand["barcodes"]:
            key = barcode_key(b)
            if len(key) >= 11 and key in digits_hay:
                code = "barcode (OCR)"
                break
    if code:
        pts += cfg["w_code"]
        avail += cfg["w_code"]
        notes.append(f"{code} agrees")
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
            dur_frac = good / len(known)
            pts += cfg["w_durations"] * dur_frac
            avail += cfg["w_durations"]
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
    if dur_frac is None:
        if code.startswith("barcode"):
            notes.append("durations unavailable (barcode decides)")
        else:
            avail += cfg["w_durations"]  # missing evidence costs confidence
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
            pts += cfg["w_ocr"] * share
            avail += cfg["w_ocr"]
            notes.append(f"OCR text found {share:.0%} of artist/title words")

    # the scans print the credited artist ("Arrangements and Orchestrations by Gustavo Dobles")
    credit_words = [w for w in fold(cand.get("artist") or "").split() if len(w) > 2]
    scans_name_artist = bool(ocr_text and credit_words and len(credit_words) >= 2 and
                             f" {' '.join(credit_words)} " in f" {fold(ocr_text)} ")
    # ... or the files do: their track-artist, copyright or composer tags, or the
    # .info/.txt the ripper left ("ARTIST=Gustavo Dobles", "COPYRIGHT=2020 Gustavo Dobles")
    files_name_artist = bool(credit_words and len(credit_words) >= 2 and album.get("credit_text") and
                             f" {' '.join(credit_words)} " in f" {fold(album['credit_text'])} ")
    clues, clues_against = pressing_clues(ocr_text, cand)
    file_clues(album, cand, clues, clues_against)
    # A catalogue number the folder or tags name that this pressing does not carry:
    # if NO pressing carries it, the copy is probably one the databases do not list.
    own = sorted(c for c in (album.get("catnos") or ())
                 if (re.search(r"[a-z]", c) and re.search(r"\d", c) and len(c) >= 4)
                 or len(re.sub(r"\D", "", c)) >= 6)
    own_absent = bool(own) and not (set(own) & set(cand.get("catnos") or ()))
    len_err = titles_agree = None
    if tracks_ok:
        diffs = [abs(t["secs"] - c["secs"]) for t, c in zip(album["tracks"], cand["tracks"]) if c["secs"] > 0]
        len_err = round(sum(diffs) / len(diffs), 2) if diffs else None
        if any(t.get("title") for t in album["tracks"]):
            titles_agree = sum(1 for t, c in zip(album["tracks"], cand["tracks"])
                               if t.get("title") and _ratio(fold(strip_edition(t["title"])),
                                                            fold(strip_edition(c.get("title") or ""))) >= 0.9)
    conf = round(100.0 * pts / avail, 1) if avail else 0.0
    return {
        "confidence": conf, "artist_sim": round(a_sim, 3), "title_sim": round(t_sim, 3), "lacks": lacks,
        "tracks_ok": tracks_ok, "aligned": aligned, "dur_frac": dur_frac, "dur_cov": round(dur_cov, 2),
        "code": code, "year_ok": year_ok, "conflict": conflict, "notes": notes, "cand": cand,
        "pressing_year": album.get("pressing_year"),
        "clues": clues, "clues_against": clues_against, "year_off": year_off,
        "len_err": len_err, "titles_agree": titles_agree,
        "own_catnos": own, "own_catno_absent": own_absent, "scans_name_artist": scans_name_artist,
        "files_name_artist": files_name_artist,
        "other_version": other_version(album, cand),
        "has_folder_catno": bool(album.get("catnos_from_folder")),
        "folder_catno_agrees": catnos_agree(album.get("catnos_from_folder"), cand.get("catnos")),
        "folder_catno_absent": bool(album.get("catnos_from_folder") and cand.get("catnos")
                                    and not catnos_agree(album.get("catnos_from_folder"), cand.get("catnos"))),
    }


def classify(scored, cfg):
    """Decide auto / review / nomatch for ONE source's scored candidates.

    Returns {"status", "best", "runner_up", "margin", "why"}. `why` lists
    every reason a candidate that looked good did not qualify for auto.
    """
    if not scored:
        return {"status": "nomatch", "best": None, "runner_up": None,
                "margin": None, "why": ["no candidates found"]}
    def barcode_agrees(s):
        return s["code"].startswith("barcode")

    # What your folder name states about the copy is a rule, not a hint: its format
    # ("[Vinyl Rip 24bit 192khz]", "[2CD]"), country ("Germany") and catalogue number.
    # Releases that contradict it are another product. When the same album is listed
    # in a form that fits, the others drop out (however well their lengths fit); when
    # none fits, a person decides.
    # A folder catalogue number counts only when some release carries it: one on none
    # of them may not be a catalogue number at all ("[Tatman Music TM 050426]").
    catno_real = any(s.get("folder_catno_absent") is False and (s["cand"].get("catnos"))
                     and s.get("has_folder_catno") for s in scored)

    def folder_says(s):
        out = [x for x in s.get("clues_against") or () if x.startswith("folder name says")]
        if s.get("folder_catno_agrees"):     # the folder's own number is on it: that is the pressing
            out = [x for x in out if re.match(r"folder name says (vinyl|cd),", x)]
        if catno_real and s.get("folder_catno_absent"):
            out.append("folder catalogue number is not on it")
        return out
    if any(folder_says(s) for s in scored):
        fitting = [s for s in scored if not folder_says(s)]
        same_album = [s for s in fitting if s["tracks_ok"] and s["artist_sim"] >= cfg["auto_min_artist"]
                      and s["title_sim"] >= cfg["auto_min_title"]]
        said = sorted({x.split(",")[0] for s in scored for x in folder_says(s)})
        if same_album:
            dropped = len(scored) - len(fitting)
            unlisted = all(s.get("own_catno_absent") for s in scored)
            scored = fitting
            for s in same_album:
                s["notes"] = s["notes"] + [f"{dropped} release(s) left out: {'; '.join(said)}"]
            # Leaving releases out never makes an unlisted pressing a match: your copy's
            # catalogue number is on none of them, so only hard proof (a disc ID, your
            # files' link, ISRC, fingerprint, barcode) decides.
            if unlisted:
                hard = ("disc TOC", "source link", "isrc", "fingerprint")
                own = next((x for s in scored for x in s.get("own_catnos") or ()), "")
                scored = [s if (any(k in (s.get("clues") or {}) for k in hard) or s["code"].startswith("barcode"))
                          else dict(s, media_why=f"catalogue number {own.upper()} from your folder/tags is on "
                                                 "none of these pressings") for s in scored]
        else:
            best0 = max(scored, key=lambda s: s["confidence"])
            media_why = (f"{'; '.join(folder_says(best0) or said)}; no release of this album found that fits "
                         "what the folder name says")
            scored = [dict(s, media_why=media_why) for s in scored]

    def number_agrees(s):
        """Barcode, or a catalogue number from the tags or folder name (not one
        merely found somewhere in OCR text)."""
        return barcode_agrees(s) or s["code"] in ("catalogue number", "catalogue number (folder name)")
    # Ties go to the candidate whose barcode agrees, then to the one whose year does.
    ranked = sorted(scored, key=lambda s: (-s["confidence"], not barcode_agrees(s),
                                           not number_agrees(s), not s["year_ok"],
                                           s.get("year_off") if s.get("year_off") is not None else 99))
    best = ranked[0]
    # Pressings that score the same: what the scans show can tell them apart
    # (a label, "Made in Germany", a catalogue number printed on the back).
    tied = [s for s in ranked if best["confidence"] - s["confidence"] < cfg["auto_min_margin"]]
    scan_pick = None
    # A number decides only when it is the ONLY one that agrees. When several tied
    # pressings each match a number (King Diamond: the tags carry Metal Blade's
    # 3984-14359-2, the folder FONO's FO40CD), the other evidence decides.
    numbered = [s for s in tied if number_agrees(s)]
    if not numbered or len(numbered) > 1:
        scan_pick = choose_pressing(numbered or tied, cfg.get("preferred_countries"))
        if scan_pick:
            pick = scan_pick[0]
            ranked = [pick] + [s for s in ranked if s is not pick]
            best = pick
    runner = ranked[1] if len(ranked) > 1 else None
    margin = best["confidence"] - runner["confidence"] if runner else 100.0

    why = [best["media_why"]] if best.get("media_why") else []
    # The album is PROVEN when its disc ID, barcode, or a catalogue number from
    # its tags or folder name agrees and every track length agrees too. A title
    # or artist that differs from the folder's is then the database's own
    # (freeDB-style): "The Mediator" is "The Mediator Between Head and Hands
    # Must Be the Heart"; an album filed under Death is credited to Control Denied.
    proof = ("disc ID" if (best.get("clues") or {}).get("disc TOC") else
             "the files' own link" if (best.get("clues") or {}).get("source link") else
             "ISRC" if (best.get("clues") or {}).get("isrc") else
             "audio fingerprint" if (best.get("clues") or {}).get("fingerprint") else
             "barcode" if best["code"] == "barcode" else
             "catalogue number" if best["code"] in ("catalogue number", "catalogue number (folder name)")
             else "")
    if not proof and best.get("scans_name_artist") and best["title_sim"] >= cfg["exact_title_sim"]:
        proof = "the scans naming this artist"           # only for the artist credit, below
    if not proof and best.get("files_name_artist") and best["title_sim"] >= cfg["exact_title_sim"]:
        proof = "your files' own tags and notes naming this artist"   # likewise
    proven = bool(proof and best["tracks_ok"] and best["dur_frac"] is not None
                  and best["dur_frac"] >= cfg["auto_min_duration_frac"] and not best.get("conflict"))
    split_credit = bool(best.get("lacks"))     # a collaborator would be dropped: never
    if not best["tracks_ok"]:
        why.append("track count differs")
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
    barcode_decides = best["code"].startswith("barcode")
    # A catalogue number that agrees is strong, pressing-specific evidence, so
    # a few lengths may be off (database lengths are often rounded or wrong).
    min_dur = cfg["auto_min_duration_frac_with_code"] if best["code"] else cfg["auto_min_duration_frac"]
    if not best["code"] and best["title_sim"] < cfg["exact_title_sim"]:
        min_dur = max(min_dur, cfg["auto_min_duration_frac_partial_title"])
    # Track lengths may differ when something else points to this very release:
    # a barcode, catalogue number or disc ID (tags, folder name or scans), or a
    # vinyl rip matched to a vinyl release (a manual rip is never cut like the
    # database's lengths). The track count must still agree.
    vinyl = str((best.get("clues") or {}).get("format", "")).startswith("vinyl")
    excuse = ("" if not (best["tracks_ok"] and best["artist_sim"] >= cfg["auto_min_artist"]
                         and best["title_sim"] >= cfg["auto_min_title"] and not best.get("conflict"))
              else f"{best['code']} points to this release" if best["code"]
              else "disc ID points to this release" if (best.get("clues") or {}).get("disc TOC")
              else "your files' own tags link to this release" if (best.get("clues") or {}).get("source link")
              else "vinyl rip of a vinyl release (a manual rip's lengths differ)" if vinyl else "")
    lengths_excused = False
    if not barcode_decides:
        if best["dur_frac"] is None or best["dur_frac"] < min_dur:
            if excuse:
                lengths_excused = True
                best["notes"] = best["notes"] + [f"track lengths not compared: {excuse}"]
            elif best["dur_frac"] is None:
                why.append("durations unavailable")
            else:
                why.append(f"only {best['dur_frac']:.0%} of durations agree")
    if best["confidence"] < cfg["auto_min_confidence"] and not lengths_excused and not (
            proven and (best["artist_sim"] < cfg["auto_min_artist"] or best["title_sim"] < cfg["auto_min_title"])
            and not split_credit):
        why.append(f"confidence {best['confidence']} < {cfg['auto_min_confidence']}")
    if best.get("other_version"):
        why.append(f"the release is the {best['other_version']} version, which your folder and tags do not say")
    if best.get("conflict"):
        why.append("barcode differs from the candidate's")
    py = best.get("pressing_year")
    if (py and best["cand"].get("year") and not best["code"]
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
    if scan_pick and abs(margin) < cfg["auto_min_margin"] and not (barcode_decided or catno_decided):
        others = [s["cand"]["id"] for s in tied if s is not best]
        said = "; ".join(scan_pick[1].values())
        best["notes"] = best["notes"] + [f"pressing chosen by {said} "
                                         f"(not on {', '.join(others[:3])})"]
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
            "margin": round(margin, 1), "why": why}


def _label_key(labels):
    return {" ".join(w for w in fold(x).split() if w not in _LABEL_FILLER) for x in str(labels).split(";")} - {""}


def same_pressing(a, b):
    """A MusicBrainz and a Discogs candidate for the same pressing: MusicBrainz links
    to that Discogs release, or the barcode, or the catalogue number and country agree."""
    if str(a.get("discogs_id") or "") and str(a.get("discogs_id")) == str(b.get("id")):
        return True
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
            and a.get("year") and a.get("year") == b.get("year")
            and len(a.get("tracks") or ()) == len(b.get("tracks") or ())):
        return True
    # A self-released album with no catalogue number anywhere (Aeon of Horus - Exile:
    # MusicBrainz "[no label]", Worldwide; Discogs "Not On Label (... Self-released)",
    # Australia): one release if the year, format and track count agree.
    def unlabelled(c):
        lab = fold(c.get("label") or "")
        return not lab or bool(re.search(r"no label|not on label|self released|self-released", lab))
    return bool(not a.get("catnos") and not b.get("catnos") and unlabelled(a) and unlabelled(b)
                and a.get("year") and a.get("year") == b.get("year")
                and set(a.get("formats") or ()) & set(b.get("formats") or ())
                and len(a.get("tracks") or ()) == len(b.get("tracks") or ()))


EVIDENCE_CLUES = ("disc TOC", "source link", "isrc", "fingerprint", "catalogue number", "barcode",
                  "label", "country", "format", "physical")


def _evidence(best):
    if not best:
        return {}
    clues = {k: v for k, v in (best.get("clues") or {}).items() if k in EVIDENCE_CLUES}
    if best["cand"].get("source") == "discogs":
        clues.pop("disc TOC", None)            # linked from MusicBrainz, not your copy's evidence
    if best.get("code"):
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


def combine(mb_result, dg_result, countries=None):
    """Discogs first: it lists far more pressings (catalogue numbers, licensed
    editions) than MusicBrainz. A confident Discogs match decides the pressing; the
    MusicBrainz release of that SAME pressing, when there is one, comes along
    ("mb_twin") so Picard can tag from MusicBrainz with Discogs filling in. When
    Discogs is not sure, a confident MusicBrainz match is used; else the better of
    the two goes to the review list."""
    if dg_result and dg_result["status"] == "auto":
        out = dict(dg_result)
        mb_best = (mb_result or {}).get("best")
        twins = [s for s in (mb_result or {}).get("scored") or []
                 if same_pressing(s["cand"], dg_result["best"]["cand"])]
        if twins:
            out["mb_twin"] = max(twins, key=lambda s: s["confidence"])["cand"]["id"]
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
            first = prefer_original([mb_best, dg_result["best"]])
            if first and first[0] is mb_best:
                mb_best["notes"] = mb_best["notes"] + [
                    "MusicBrainz chosen over Discogs's other pressing: " + first[1]["original pressing"]]
                return mb_result
            if first:
                out["best"] = dict(out["best"], notes=out["best"]["notes"] + [first[1]["original pressing"]])
                return out
            rank = {c: i for i, c in enumerate(countries or ())}
            mb_r = rank.get(country_code(mb_best["cand"].get("country")), 99)
            dg_r = rank.get(country_code(dg_result["best"]["cand"].get("country")), 99)
            if mb_r < dg_r:
                mb_best["notes"] = mb_best["notes"] + [
                    "MusicBrainz chosen over Discogs's other pressing by your country order"]
                return mb_result
            return out
        elif mb_result and mb_result["status"] == "auto" and mb_best:
            dg_result["best"]["notes"] = dg_result["best"]["notes"] + [
                f"MusicBrainz's pick ({mb_best['cand']['id']}) is another pressing; Discogs decides"]
        return out
    if mb_result and mb_result["status"] == "auto":
        return mb_result
    order = {"review": 0, "nomatch": 1}
    options = [r for r in (mb_result, dg_result) if r and r["best"]]
    if not options:
        return mb_result or dg_result
    options.sort(key=lambda r: (order[r["status"]], -r["best"]["confidence"]))
    return options[0]


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


def album_only(final, cfg, source=""):
    """The tied pressings when the ALBUM is certain and only its pressing is not: the pick
    is as sure as an automatic one (score, title, artist, every track length), and the only
    doubt is a runner-up of the same album. None otherwise."""
    best = (final or {}).get("best")
    if not best or final.get("status") == "auto":
        return None
    why = [w for w in final.get("why") or [] if not w.startswith("investigated")]
    if not why or not all(_TIE_WHY.match(w) for w in why):
        return None
    if best["confidence"] < cfg["auto_min_confidence"] or not best.get("tracks_ok") \
            or (best.get("dur_frac") or 0) < 1.0 or best.get("conflict"):
        return None
    n = len(best["cand"]["tracks"])
    tied = [s for s in final.get("scored") or [best]
            if best["confidence"] - s["confidence"] < cfg["auto_min_margin"]
            and title_sim(s["cand"]["title"], best["cand"]["title"]) >= 0.85 and len(s["cand"]["tracks"]) == n]
    if len(tied) < 2:
        return None
    # what the files are (a CD rip, a download, a vinyl rip) narrows the pressings whose
    # shared fields are written: a CD rip is tagged from the CDs only, so its media is "CD"
    if source:
        same_kind = [s for s in tied if source in (s["cand"].get("formats") or ())]
        if same_kind:
            tied = same_kind
    return {"source": source, "tied": [{"source": s["cand"]["source"], "id": str(s["cand"]["id"]), "label": s["cand"].get("label") or "",
                      "country": s["cand"].get("country") or "", "formats": sorted(s["cand"].get("formats") or ()),
                      "year": s["cand"].get("year")} for s in tied]}


def album_only_tags(tags, tied):
    """One file's tags, kept to what every tied pressing shares: release IDs, catalogue
    numbers, barcode, ASIN and a pressing's own notes and companies are dropped; label,
    release country, media and release date stay only when all the pressings agree on them
    (else the date becomes the album's original date)."""
    out = {k: v for k, v in tags.items() if k not in PRESSING_FIELDS and not k.startswith("discogs_company_")}
    same = lambda vals: len(vals) == 1 and "" not in vals and None not in vals   # noqa: E731
    if not same({fold(t.get("label") or "") for t in tied}):
        out.pop("label", None)
    if not same({country_code(t.get("country")) or "" for t in tied}):
        out.pop("releasecountry", None)
    if not same({",".join(t.get("formats") or ()) for t in tied}):
        out.pop("media", None)
    if not same({t.get("year") for t in tied}):
        original = out.get("originaldate") or out.get("originalyear")
        if original:
            out["date"] = original
        else:
            out.pop("date", None)
    return out
