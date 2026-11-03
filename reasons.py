"""reasons.py - the reason codes written to TAGMENDER_REASON.

TAGMENDER_REASON holds short codes, then the reason in words:

    ambiguous_release: runner-up 1234 within 2.0 points - likely another pressing
    incomplete_album,duration_mismatch: track count differs; only 61% of durations agree

Codes let players, scripts and `tagmender unresolved` group albums by what stopped them;
the words say exactly what was found. Values written before codes existed (words only)
are still read: their codes are worked out from the words.

Identification confidence, provider coverage, metadata completeness and processing
errors are kept apart: a provider that was down (discogs_unavailable) is recorded, but
only makes an album unresolved when no other source identified it.
"""
from __future__ import annotations

import re

CODES = {
    "no_candidate": "no database or web source lists this album",
    "ambiguous_artist": "the credited artist could not be told apart from another of that name, "
                        "or the credit differs from the tags",
    "ambiguous_release": "more than one release or pressing fits about equally well",
    "conflicting_sources": "the sources, or the folder name and the release, disagree",
    "web_fallback_only": "only web sources (not MusicBrainz or Discogs) have this album",
    "musicbrainz_unavailable": "MusicBrainz could not be reached or answered with an error",
    "discogs_unavailable": "Discogs could not be reached or answered with an error",
    "rate_limited": "a provider asked TagMender to slow down; the album will be tried again",
    "missing_credentials": "a provider needs a key or token that is not set up",
    "unsupported_format": "TagMender cannot safely write tags to this file format",
    "incomplete_album": "the track count differs from every candidate (missing or extra tracks)",
    "duration_mismatch": "track lengths do not agree closely enough",
    "low_similarity": "artist or title agree only partly",
    "missing_collaborator": "the database credit leaves out an artist your tags name",
    "write_failed": "the tags could not be written or verified",
    "partially_tagged": "some files already carry IDs from another release",
    "other": "see the words after the codes",
}

# words in older free-text reasons, and the code each stands for (first match wins per pattern)
_PATTERNS = [
    (r"runner-up .* within|another pressing|equally", "ambiguous_release"),
    (r"track count differs|missing tracks?|extra tracks?", "incomplete_album"),
    (r"durations? (agree|unavailable)|lengths? (differ|disagree)", "duration_mismatch"),
    (r"credit (lacks|differs)|missing collaborator", "missing_collaborator"),
    (r"artist similarity|same-?name artist|ambiguous artist", "ambiguous_artist"),
    (r"title similarity|confidence \d", "low_similarity"),
    (r"folder name says|barcode differs|catalogue number .* not|contradict|conflict", "conflicting_sources"),
    (r"partially tagged", "partially_tagged"),
    (r"not on musicbrainz,? (or )?discogs|no candidates? found|^nomatch|not found", "no_candidate"),
    (r"near miss", "web_fallback_only"),
    (r"saved \d+ of \d+ files|could not (be )?(written|saved)|write failed", "write_failed"),
    (r"\b(429|rate.?limit|too many requests)\b", "rate_limited"),
    (r"discogs (token|key) (missing|not set)|no discogs token|unauthori[sz]ed|401", "missing_credentials"),
    (r"discogs .*(unavailable|down|timed? ?out|error 5\d\d)", "discogs_unavailable"),
    (r"musicbrainz .*(unavailable|down|timed? ?out|error 5\d\d)", "musicbrainz_unavailable"),
    (r"unsupported format|cannot write", "unsupported_format"),
]
_CODE_PREFIX = re.compile(r"^([a-z_]+(?:,[a-z_]+)*):\s*")
MAX_LEN = 250


def classify(text: str) -> list[str]:
    """The codes a reason in words stands for (["other"] when none fits)."""
    t = str(text or "").casefold()
    out = []
    for pat, code in _PATTERNS:
        if code not in out and re.search(pat, t):
            out.append(code)
    return out or ["other"]


def encode(text: str = "", codes=None) -> str:
    """'code1,code2: words', at most 250 characters. Text that already starts with known
    codes is kept as it is."""
    text = str(text or "").strip()
    m = _CODE_PREFIX.match(text)
    if m and all(c in CODES for c in m.group(1).split(",")):
        return text[:MAX_LEN]
    codes = [c for c in (codes or classify(text)) if c in CODES] or ["other"]
    codes = list(dict.fromkeys(codes))
    return (",".join(codes) + (f": {text}" if text else ""))[:MAX_LEN]


def parse(value: str) -> tuple[list[str], str]:
    """(codes, words) from a TAGMENDER_REASON value, old or new."""
    value = str(value or "").strip()
    m = _CODE_PREFIX.match(value)
    if m and all(c in CODES for c in m.group(1).split(",")):
        return m.group(1).split(","), value[m.end():]
    return (classify(value) if value else []), value


def describe(code: str) -> str:
    return CODES.get(code, code)
