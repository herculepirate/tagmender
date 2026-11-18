"""classical.py - classical releases (rules CLS-2 to CLS-5 in docs/MATCHING_RULES.md).

is_classical(profile, rel, det, album) -> (bool, [reasons])
    CLS-2: whether an identified release is western classical music, and why. One answer per
    album (engine.Engine._classical keeps it); the reasons go to the album's Why.

work_lookups(http, rel, log) -> ({work ID: work}, lookups made)
    CLS-3: the parent works of the release's recorded works (a movement's symphony, an opera's
    act and the opera), each asked once through the shared HTTP client (its cache and pacing),
    at most MAX_DEPTH levels up and MAX_WORK_LOOKUPS per album.

mb_tags.release_tags writes a classical album's fields when its settings carry
classical=True (CLS-4); discogs_roles / the engine's Discogs index works do the same for
Discogs (CLS-4). Nothing here runs for an album that is not classical.
"""
from __future__ import annotations

import re
import unicodedata

# ---- CLS-2: recognition --------------------------------------------------------------
# a genre (MusicBrainz's, Discogs's genres and styles, a file's) naming western classical music
_STRONG_GENRE = re.compile(
    r"\b(?:classical|baroque|renaissance|medieval|opera|operas|operetta|symphony|symphonies|concerto|"
    r"concertos|concerti|sonata|sonatas|oratorio|oratorios|cantata|cantatas|chamber music|art song|lied|"
    r"lieder|string quartet|requiem)\b")
# a genre that is classical-like but not proof alone: counts with a weak signal (below)
_AMBIGUOUS_GENRE = re.compile(r"\b(?:contemporary|modern|post|minimal|new) classical\b|\bchoral\b|\bminimalism\b")
# words that make a genre something else ("neo-classical", "classical crossover", "symphonic
# metal", "rock opera", "medieval folk", "hindustani classical"): never a classical signal
_NOT_CLASSICAL = re.compile(
    r"\bneo|\bcrossover\b|\brock\b|\bmetal\b|\bpop\b|\bfolk\b|\bjazz\b|\belectronic\b|\bambient\b|"
    r"\bdarkwave\b|\bhip hop\b|\bindian\b|\bhindustani\b|\bcarnatic\b|\bpersian\b|\barabic\b|\bchinese\b|"
    r"\bjapanese\b|\bstage and screen\b|\bsoundtrack\b")

# labels that publish (almost) only classical music: a weak signal
CLASSICAL_LABELS = {
    "deutsche grammophon", "archiv produktion", "decca classics", "philips classics", "harmonia mundi",
    "harmonia mundi france", "harmonia mundi usa", "deutsche harmonia mundi", "hyperion", "naxos",
    "naxos historical", "bis", "chandos", "ecm new series", "ondine", "alpha", "alpha classics", "glossa",
    "oehmsclassics", "oehms classics", "brilliant classics", "pentatone", "pentatone classics", "2l",
    "sony classical", "warner classics", "emi classics", "erato", "virgin classics", "virgin veritas",
    "teldec classics", "channel classics", "cpo", "capriccio", "hanssler classic", "carus", "coro",
    "signum classics", "delphian", "ricercar", "accent", "arcana", "challenge classics", "profil",
    "supraphon", "analekta", "atma classique", "orfeo", "tudor", "berlin classics", "rca red seal",
    "melodiya", "мелодия", "fuga libera", "outhere", "linn classics", "bis records", "dacapo", "cedille",
    "avie", "lso live", "lpo", "onyx classics", "evil penguin classic", "naive classique", "mirare",
    "aparte", "la dolce volta", "cypres", "ramee", "passacaille", "et'cetera", "nimbus records",
    "toccata classics", "grand piano", "marco polo", "dynamic", "tactus", "stradivarius", "arts music",
    "da vinci classics", "br klassik", "br-klassik", "hyperion records", "resonus", "rubicon classics", "querstand", "genuin", "audite", "musikproduktion dabringhaus und grimm", "mdg",
}
_LABEL_TAIL = re.compile(r"\s+(?:records|recordings|label|music|gmbh|ltd\.?|inc\.?)$")
# catalogue numbers of works printed in titles (Bach's BWV, Mozart's K., opus numbers ...): weak
_CATALOGUE = re.compile(r"(?<![\w.])(?:BWV|HWV|RV|TWV|BuxWV|SWV|WoO|Hob\.|KV|K\.|D\.|[Oo]p\.|Sz\.)"
                        r"\s?(?:[IVXL]+[a-z]?\s?:\s?)?\d+[a-z]?")
_PERFORMER_RELS = {"instrument", "vocal", "performer", "performing orchestra", "conductor", "chorus master",
                   "concertmaster"}


def fold(s) -> str:
    s = unicodedata.normalize("NFKD", str(s or "")).encode("ascii", "ignore").decode().casefold()
    return " ".join(re.sub(r"[^a-z0-9]+", " ", s.replace("&", " and ")).split())


def genre_kind(genre) -> str:
    """CLS-2: "strong" (a classical genre: "Classical", "Baroque", "Opera"), "ambiguous"
    ("Contemporary Classical", "Modern Classical", "Choral": counted only beside a weak signal)
    or "" (anything else - "Neo-Classical", "Classical Crossover", "Symphonic Metal", "Rock
    Opera", "Hindustani Classical")."""
    g = fold(genre)
    if not g or _NOT_CLASSICAL.search(g):
        return ""
    if _AMBIGUOUS_GENRE.search(g):
        return "ambiguous"
    return "strong" if _STRONG_GENRE.search(g) else ""


def _names(items) -> list:
    out = []
    for g in items or ():
        n = g.get("name") if isinstance(g, dict) else g
        if n and str(n) not in out:
            out.append(str(n))
    return out


def _label_kind(name) -> bool:
    n = _LABEL_TAIL.sub("", fold(name)) if fold(name) else ""
    raw = str(name or "").strip().casefold()
    return bool(n) and (n in CLASSICAL_LABELS or raw in CLASSICAL_LABELS)


def mb_people(rel) -> tuple:
    """(composers, performers) of a MusicBrainz release as {artist ID or folded name}: the
    composers of its works (the release's, the recordings' works' relationships), and who
    performs - the recordings' artists and the performer, orchestra and conductor relationships."""
    composers, performers = set(), set()

    def key(a):
        return a.get("id") or fold(a.get("name"))

    def walk(relations):
        for r in relations or ():
            if not isinstance(r, dict):
                continue
            if r.get("target-type") == "artist" and isinstance(r.get("artist"), dict):
                if r.get("type") == "composer":
                    composers.add(key(r["artist"]))
                elif r.get("type") in _PERFORMER_RELS:
                    performers.add(key(r["artist"]))
            elif r.get("target-type") == "work" and r.get("type") == "performance" and isinstance(r.get("work"), dict):
                for wr in r["work"].get("relations") or ():
                    if isinstance(wr, dict) and wr.get("type") == "composer" and isinstance(wr.get("artist"), dict):
                        composers.add(key(wr["artist"]))
    walk((rel or {}).get("relations"))
    walk(((rel or {}).get("release-group") or {}).get("relations"))
    for m in (rel or {}).get("media") or ():
        for t in m.get("tracks") or ():
            rec = t.get("recording") or {}
            walk(rec.get("relations"))
            for c in rec.get("artist-credit") or ():
                if isinstance(c, dict) and isinstance(c.get("artist"), dict):
                    performers.add(key(c["artist"]))
    composers.discard("")
    performers.discard("")
    return composers, performers


def _mb_titles(rel) -> list:
    return [str(t.get("title") or "") for m in (rel or {}).get("media") or () for t in m.get("tracks") or ()]


def _dg_titles(det) -> list:
    out = []
    for e in (det or {}).get("tracklist") or ():
        if isinstance(e, dict):
            out.append(str(e.get("title") or ""))
            out += [str(s.get("title") or "") for s in e.get("sub_tracks") or () if isinstance(s, dict)]
    return out


def is_classical(profile=None, rel=None, det=None, album=None) -> tuple:
    """CLS-2: (classical, reasons) of an identified album - REL its MusicBrainz release (as
    looked up), DET its Discogs release, ALBUM the files (match_albums.read_album). Classical
    when one strong signal holds: the profile's tradition is western classical; Discogs's genre
    is Classical; a MusicBrainz genre (when the lookup carries them) is a classical one; an
    artist of the release's credit composed the works and performs on none of them; the
    credit is MusicBrainz's classical "Composer; performers", led by a composer of the works.
    Weak signals - a classical label, works' catalogue numbers in the titles (BWV, K., Op.,
    Hob., RV, D.), Classical Extras' tags in your files - never decide alone: two of different
    kinds do, or one beside an ambiguous signal (a "Contemporary Classical" or "Choral" genre,
    a "; " credit whose composer the lookup does not name). "Neo-Classical", "Classical
    Crossover", "Symphonic Metal" and the like are no signal."""
    strong, ambiguous, weak = [], [], {}
    tradition = profile.get("tradition") if hasattr(profile, "get") else ""
    if str(tradition or "") == "western_classical":
        strong.append("the profile's tradition is western classical")
    # Discogs: its genres and styles
    for g in _names((det or {}).get("genres")) + _names((det or {}).get("styles")):
        kind = genre_kind(g)
        if kind == "strong" and fold(g) == "classical" and g in _names((det or {}).get("genres")):
            strong.append("Discogs's genre is Classical")
        elif kind == "strong":
            strong.append(f"Discogs's genre or style {g!r}")
        elif kind == "ambiguous":
            ambiguous.append(f"Discogs's style {g!r}")
    # MusicBrainz: the release's and its group's genres (when the lookup asked for them)
    rg = (rel or {}).get("release-group") or {}
    for g in _names((rel or {}).get("genres")) + _names(rg.get("genres")):
        kind = genre_kind(g)
        if kind == "strong":
            strong.append(f"MusicBrainz's genre {g!r}")
        elif kind == "ambiguous":
            ambiguous.append(f"MusicBrainz's genre {g!r}")
    # MusicBrainz: who composed and who performs
    ac = [c for c in (rel or {}).get("artist-credit") or () if isinstance(c, dict) and isinstance(c.get("artist"), dict)]
    if ac:
        composers, performers = mb_people(rel)
        keys = [c["artist"].get("id") or fold(c["artist"].get("name")) for c in ac]
        names = [c.get("name") or c["artist"].get("name") or "" for c in ac]
        # ("Composer, Composer; performers": the "; " after the composers, wherever it comes)
        semi = any(";" in str(c.get("joinphrase") or "") for c in ac[:-1])
        for k, n in zip(keys, names):
            if k in composers and k not in performers:
                strong.append(f"{n} composed the works and performs on none of them")
                break
        if semi and keys[0] in composers:
            strong.append(f"the release's credit is led by its composer {names[0]} (\"Composer; performers\")")
        elif semi and not composers:
            ambiguous.append("the release's credit is MusicBrainz's \"Composer; performers\" form")
    # weak: labels, catalogue numbers, Classical Extras' tags
    labels = [((li or {}).get("label") or {}).get("name") for li in (rel or {}).get("label-info") or ()] + \
        [(lb or {}).get("name") for lb in (det or {}).get("labels") or () if isinstance(lb, dict)]
    hit = next((x for x in labels if x and _label_kind(x)), "")
    if hit:
        weak["label"] = f"the classical label {hit}"
    tracks = list((album or {}).get("tracks") or ())
    titles = _mb_titles(rel) + _dg_titles(det) + [str(t.get("title") or "") for t in tracks]
    cat = next((m.group(0) for m in (_CATALOGUE.search(t) for t in titles) if m), "")
    if cat:
        weak["catalogue"] = f"works' catalogue numbers in the titles ({cat.strip()} ...)"
    if any("classical extras" in str(t.get("clue_text") or "").casefold() for t in tracks):
        weak["classical_extras"] = "your files carry Classical Extras' tags"
    reasons = strong + ambiguous + list(weak.values())
    if strong:
        return True, reasons
    if (ambiguous and weak) or len(weak) >= 2:
        return True, reasons
    return False, reasons


def composer_first_form(rel) -> bool:
    """MusicBrainz's classical credit form "Composer; performers": the release credit's first
    joins is a "; " ("Alfred Schnittke, Arvo Pärt; Chor des Bayerischen Rundfunks")."""
    ac = [c for c in (rel or {}).get("artist-credit") or () if isinstance(c, dict)]
    return len(ac) >= 2 and any(";" in str(c.get("joinphrase") or "") for c in ac[:-1])


def why_line(reasons) -> str:
    return "classical (CLS-2): " + "; ".join(reasons)


# ---- CLS-3: the works' hierarchy ------------------------------------------------------
WORK_URL = "https://musicbrainz.org/ws/2/work/{0}?inc=artist-rels+work-rels&fmt=json"
MAX_DEPTH = 3                  # a number's act, the act's opera, ... at most this many levels up
MAX_WORK_LOOKUPS = 80          # per album: a box of more parent works keeps the rest unresolved


def parent_link(work) -> dict | None:
    """A work's relationship to the work it is a part of (MusicBrainz "parts", backward)."""
    for r in (work or {}).get("relations") or ():
        if isinstance(r, dict) and r.get("target-type") == "work" and r.get("type") == "parts" and \
                r.get("direction") == "backward" and isinstance(r.get("work"), dict) and r["work"].get("id"):
            return r
    return None


def recorded_works(rel) -> list:
    out = []
    for m in (rel or {}).get("media") or ():
        for t in m.get("tracks") or ():
            for r in (t.get("recording") or {}).get("relations") or ():
                if isinstance(r, dict) and r.get("target-type") == "work" and r.get("type") == "performance" \
                        and isinstance(r.get("work"), dict):
                    out.append(r["work"])
    return out


def work_lookups(http, rel, log=None, cap=MAX_WORK_LOOKUPS) -> tuple:
    """CLS-3: ({work ID: the work with its artist and work relationships}, lookups asked) for
    the parent works of the release's recorded works, up to MAX_DEPTH levels, each work once.
    MusicBrainz being unreachable is raised (match_albums.ProviderUnavailable): the album is
    tried again later, never tagged with half its works."""
    works, asked, todo = {}, 0, []
    for w in recorded_works(rel):
        link = parent_link(w)
        if link and link["work"]["id"] not in todo:
            todo.append(link["work"]["id"])
    depth = {wid: 1 for wid in todo}
    capped = 0
    while todo:
        wid = todo.pop(0)
        if wid in works:
            continue
        if asked >= cap:
            capped += 1
            continue
        got = http.get_json(WORK_URL.format(wid), "mb")
        asked += 1
        if not isinstance(got, dict):
            continue
        works[wid] = got
        up = parent_link(got)
        if up and depth[wid] < MAX_DEPTH and up["work"]["id"] not in works and up["work"]["id"] not in depth:
            depth[up["work"]["id"]] = depth[wid] + 1
            todo.append(up["work"]["id"])
    if capped and log is not None:
        log.info("classical: %d parent work(s) not looked up (at most %d per album)", capped, cap)
    return works, asked


# ---- CLS-4: Discogs --------------------------------------------------------------------
# Discogs roles a classical release credits that are no performance (the Discogs plugin makes
# every role it does not know a performer:<role>)
DISCOGS_ROLES = {"performer:libretto by": "lyricist", "performer:text by": "lyricist",
                 "performer:libretto": "lyricist",
                 "performer:composer": "composer", "performer:translated by": "discogs_credit_translated_by"}


def discogs_roles(fields: dict) -> dict:
    """CLS-4: a classical Discogs release's fields with its librettists and text authors as
    lyricist, a "Composer" role as composer, its translators in a credit tag of their own
    (discogs_credit_translated_by) - never performer:libretto by."""
    if not isinstance(fields, dict) or not any(k in DISCOGS_ROLES for k in fields):
        return fields
    out = {}
    for k, v in fields.items():
        to = DISCOGS_ROLES.get(k, k)
        if to not in out:
            out[to] = v if to == k else list(v if isinstance(v, list) else [v])
            continue
        have = out[to] if isinstance(out[to], list) else [out[to]]
        out[to] = have + [x for x in (v if isinstance(v, list) else [v]) if x not in have]
    return out


# ---- Classical Extras' tags in your files ---------------------------------------------
# the fields Classical Extras curated: kept on a file that carries its stamp (filled only
# where empty, CLS-5)
CE_FIELDS = ("work", "movement", "movementnumber", "movementtotal", "showmovement")


def classical_extras_file(before: dict) -> bool:
    """CLS-5: the file's tags carry Classical Extras' stamp (its STAMP tag, or its options in
    a comment)."""
    for k, v in (before or {}).items():
        key = str(k).casefold()
        if key == "stamp" or key.endswith(":stamp") or "comment" in key or key.startswith("comm"):
            vals = v if isinstance(v, (list, tuple)) else [v]
            if any("classical extras" in str(x).casefold() for x in vals):
                return True
    return False
