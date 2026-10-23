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


def fold(text):
    """Case, accent, punctuation and spacing insensitive form."""
    t = unicodedata.normalize("NFKD", str(text or ""))
    t = "".join(c for c in t if not unicodedata.combining(c)).translate(_PLAIN_LETTERS)
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
_CATNO_ONE_TOKEN = re.compile(r"^[A-Za-z]{1,10}-?\d{2,7}(?:[-/]\d{1,3})?$")      # Warpcd92, BRC-67, DiN34
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
                  "copy", "time", "for", "suite", "route", "room", "postfach", "postbus", "cedex", "pmb", "box"}
# US state + ZIP code ("CA 90064", "NY 11576") on label addresses
_US_STATES = set("al ak az ar ca co ct de fl ga hi id il in ia ks ky la me md ma mi mn ms mo mt ne nv "
                 "nh nj nm ny nc nd oh ok or pa ri sc sd tn tx ut vt va wa wv wi wy dc".split())
_OCR_CATNO_RE = re.compile(
    r"(?<![A-Za-z0-9])([A-Z][A-Za-z]{1,9}(?:[ -]?(?:CD|LP|DVD|SACD|MC|CDR|DA))?[ -]?\d{2,7}(?:[-/]\d{1,3})?)"
    r"(?![A-Za-z0-9])")
_OCR_BARCODE_RE = re.compile(r"(?<!\d)(\d(?:[ ]?\d){10,12})(?!\d)")


def codes_from_ocr_text(text, limit=5):
    """Barcodes and catalogue numbers printed on scans, for searching.
    Barcodes must pass the check digit. Catalogue numbers need a capitalised label
    word (or letters run into the number: "CHARRMCD03") and a number that is not a
    year; label codes ("LC 01234"), work numbers ("BWV 1001"), track, disc and
    phone numbers are skipped. At most `limit` of each, in the order printed."""
    out = {"barcodes": [], "catnos": []}
    text = str(text or "")
    for m in _OCR_BARCODE_RE.finditer(text):
        code = m.group(1).replace(" ", "")
        if valid_barcode(code) and code not in out["barcodes"]:
            out["barcodes"].append(code)
    seen = set()
    for m in _OCR_CATNO_RE.finditer(text):
        cand = m.group(1).strip()
        word = re.match(r"[A-Za-z]+", cand).group(0)
        number = re.search(r"\d[\d/-]*$", cand).group(0)
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
        key = norm_code(cand)
        if key not in seen:
            seen.add(key)
            out["catnos"].append(cand)
    out["barcodes"] = out["barcodes"][:limit]
    out["catnos"] = out["catnos"][:limit]
    return out


def norm_code(value):
    """Catalogue number for comparison: no spaces, dashes or dots, any case, and no
    leading zeros inside numbers ("CHARRMCD03" is "CHARRMCD3" on Discogs)."""
    text = re.sub(r"[\s\-.]", "", str(value or "")).casefold()
    return re.sub(r"(?<!\d)0+(?=\d)", "", text)


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


def score_candidate(album, cand, cfg, ocr_text=""):
    """Score one candidate against one album. Returns a dict with the
    confidence (0-100), the evidence for it and every gate that failed."""
    pts = avail = 0.0
    notes = []

    a_sim = max(artist_sim(album["artist"], cand["artist"]),
                artist_sim(album["artist"], cand.get("artist_alt") or ""),
                artist_sim(album["artist"], cand.get("artist_sort") or ""))
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
    if want_year and cand["year"]:
        avail += cfg["w_year"]
        if want_year == cand["year"]:
            pts += cfg["w_year"]
            year_ok = True
            if album.get("pressing_year"):
                notes.append(f"year {want_year} agrees with the edition named in the folder")
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

    conf = round(100.0 * pts / avail, 1) if avail else 0.0
    return {
        "confidence": conf, "artist_sim": round(a_sim, 3), "title_sim": round(t_sim, 3), "lacks": lacks,
        "tracks_ok": tracks_ok, "aligned": aligned, "dur_frac": dur_frac, "dur_cov": round(dur_cov, 2),
        "code": code, "year_ok": year_ok, "conflict": conflict, "notes": notes, "cand": cand,
        "pressing_year": album.get("pressing_year"),
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

    def number_agrees(s):
        """Barcode, or a catalogue number from the tags or folder name (not one
        merely found somewhere in OCR text)."""
        return barcode_agrees(s) or s["code"] in ("catalogue number", "catalogue number (folder name)")
    # Ties go to the candidate whose barcode agrees, then to the one whose year does.
    ranked = sorted(scored, key=lambda s: (-s["confidence"], not barcode_agrees(s),
                                           not number_agrees(s), not s["year_ok"]))
    best = ranked[0]
    runner = ranked[1] if len(ranked) > 1 else None
    margin = best["confidence"] - runner["confidence"] if runner else 100.0

    why = []
    if not best["tracks_ok"]:
        why.append("track count differs")
    if best["artist_sim"] < cfg["auto_min_artist"]:
        why.append(f"artist similarity {best['artist_sim']:.2f} < {cfg['auto_min_artist']}"
                   + (f" (credit lacks {', '.join(best['lacks'])}; tagging from it would drop them)"
                      if best.get("lacks") else ""))
    if best["title_sim"] < cfg["auto_min_title"]:
        why.append(f"title similarity {best['title_sim']:.2f} < {cfg['auto_min_title']}")
    barcode_decides = best["code"].startswith("barcode")
    # A catalogue number that agrees is strong, pressing-specific evidence, so
    # a few lengths may be off (database lengths are often rounded or wrong).
    min_dur = cfg["auto_min_duration_frac_with_code"] if best["code"] else cfg["auto_min_duration_frac"]
    if not best["code"] and best["title_sim"] < cfg["exact_title_sim"]:
        min_dur = max(min_dur, cfg["auto_min_duration_frac_partial_title"])
    if not barcode_decides:
        if best["dur_frac"] is None:
            why.append("durations unavailable")
        elif best["dur_frac"] < min_dur:
            why.append(f"only {best['dur_frac']:.0%} of durations agree")
    if best["confidence"] < cfg["auto_min_confidence"]:
        why.append(f"confidence {best['confidence']} < {cfg['auto_min_confidence']}")
    if best.get("conflict"):
        why.append("barcode differs from the candidate's")
    py = best.get("pressing_year")
    if py and best["cand"].get("year") and best["cand"]["year"] != py and not best["code"]:
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
    if margin < cfg["auto_min_margin"] and (barcode_decided or catno_decided):
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
    return {"status": status, "best": best, "runner_up": runner,
            "margin": round(margin, 1), "why": why}


def combine(mb_result, dg_result):
    """MusicBrainz wins when it is auto; else Discogs when auto; else the
    better of the two for the review list."""
    for res in (mb_result, dg_result):
        if res and res["status"] == "auto":
            return res
    order = {"review": 0, "nomatch": 1}
    options = [r for r in (mb_result, dg_result) if r and r["best"]]
    if not options:
        return mb_result or dg_result
    options.sort(key=lambda r: (order[r["status"]], -r["best"]["confidence"]))
    return options[0]
