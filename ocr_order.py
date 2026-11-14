"""ocr_order.py - which scans OCR reads first, learned from which ones paid off.

Every scan has a role, told by its name and its folder's: back, tray (inlay), disc (CD, matrix,
ring), obi, spine, label (sticker, barcode), booklet (pages, numbered spreads), front, other.
After each album, record() counts, per role, the scans read and the ones whose code (barcode,
catalogue number, matrix) the identified release confirms. rank() reads the roles with the best
hit rate first.

The counts start from assets/ocr_order.json (shipped with TagMender: what users' exported counts
say); this computer's own counts (state folder, ocr_order.json) are added to them, so the order
follows how your scans are named. export() gives this computer's counts alone - role names and
numbers, never a file name, folder or album - to share (a GitHub issue), so the shipped order
improves. Nothing is sent anywhere by TagMender.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import exclusions as ex

HERE = Path(__file__).resolve().parent
SHIPPED = HERE / "assets" / "ocr_order.json"
VERSION = 1

# (role, pattern on the scan's name or its folder's); the first that matches is its role
ROLES = (
    ("disc", re.compile(r"(?<![a-z])(?:cd|disc|disk|dvd|matrix|ring|vinyl|side[ _-]?[ab]|lp)\d*(?![a-z])", re.I)),
    ("obi", re.compile(r"(?<![a-z])obi(?![a-z])", re.I)),
    ("spine", re.compile(r"spine|side(?:s)?(?![ _-]?[ab]\b)", re.I)),
    ("label", re.compile(r"(?<![a-z])(?:label|sticker|barcode|hype)(?![a-z])", re.I)),
    ("back", re.compile(r"back|rear|(?<![a-z])tray(?:[ _-]?card)?(?![a-z])|(?<![a-z])r(?:ueck|ück)", re.I)),
    ("tray", re.compile(r"inlay|inside|inner|insert|(?<![a-z])tray", re.I)),
    ("booklet", re.compile(r"booklet|(?<![a-z])book(?![a-z])|page|(?<![a-z])p\d+|(?<![a-z])scan\d+|"
                           r"(?<![a-z])img\d+|^\d{1,3}$", re.I)),
    ("front", re.compile(r"front|cover|folder|(?<![a-z])fr(?![a-z])", re.I)),
)
ROLE_NAMES = tuple(r for r, _ in ROLES) + ("other",)


# a folder that holds all of an album's artwork ("covers", "Artwork", "Scans": TagMender's own
# companion folders among them) says nothing of which side each scan is (IDM-1: "box_002.jpg"
# moved into "covers/" by a job is no front cover, nor read later for being one)
_ART_FOLDER = re.compile(r"^\s*(?:covers?|art(?:work)?s?|scans?|images?|pictures?|pics|photos?|artwork scans?)\s*$",
                         re.I)


def role_of(path) -> str:
    p = Path(path)
    for where in (p.stem, "" if _ART_FOLDER.match(p.parent.name) else p.parent.name):
        for role, pat in ROLES:
            if pat.search(where):
                return role
    return "other"


# ---- the counts -----------------------------------------------------------------------------
def _local() -> Path:
    return ex.STATE_DIR / "ocr_order.json"


def _read(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    roles = data.get("roles") if isinstance(data, dict) else None
    return {r: {"reads": int(v.get("reads", 0)), "hits": int(v.get("hits", 0))}
            for r, v in (roles or {}).items() if r in ROLE_NAMES and isinstance(v, dict)}


def counts() -> dict:
    """{role: {"reads", "hits"}}: the shipped counts plus this computer's."""
    out = {r: {"reads": 0, "hits": 0} for r in ROLE_NAMES}
    for src in (_read(SHIPPED), _read(_local())):
        for r, v in src.items():
            out[r]["reads"] += v["reads"]
            out[r]["hits"] += v["hits"]
    return out


def hit_rate(role: str, table=None) -> float:
    """(hits + 1) / (reads + 2): a role read rarely stays near one half, not at 0 or 1."""
    v = (table or counts()).get(role) or {"reads": 0, "hits": 0}
    return (v["hits"] + 1) / (v["reads"] + 2)


def rank(paths, tie=None) -> list:
    """PATHS with the roles that pay off most first; within a role by TIE(path) when given
    (lower first), then by name."""
    table = counts()
    rates = {r: hit_rate(r, table) for r in ROLE_NAMES}

    def key(p):
        role = role_of(p)                  # roles with the same rate are kept apart, not mixed
        return -rates[role], ROLE_NAMES.index(role), tie(p) if tie else 0, str(p).lower()
    return sorted(paths, key=key)


def record(read, hits):
    """Count an album's scans: READ every scan OCR read, HITS those whose code the identified
    release confirmed. Kept on this computer only."""
    mine = _read(_local())
    for r in ROLE_NAMES:
        mine.setdefault(r, {"reads": 0, "hits": 0})
    hit = {str(h) for h in hits}
    for p in read:
        role = role_of(p)
        mine[role]["reads"] += 1
        if str(p) in hit:
            mine[role]["hits"] += 1
    f = _local()
    f.parent.mkdir(parents=True, exist_ok=True)
    tmp = f.with_suffix(".tmp")
    tmp.write_text(json.dumps({"version": VERSION, "roles": mine}, indent=1, sort_keys=True), encoding="utf-8")
    tmp.replace(f)


def export() -> str:
    """This computer's counts alone, to share: role names and numbers only."""
    return json.dumps({"version": VERSION, "roles": _read(_local())}, indent=1, sort_keys=True)


def stats() -> list:
    """One row per role, in reading order: {"role", "shipped": {"reads", "hits"}, "mine": {...},
    "rate"} (the hit rate rank() orders by)."""
    shipped, mine, table = _read(SHIPPED), _read(_local()), counts()
    zero = {"reads": 0, "hits": 0}
    rows = [{"role": r, "shipped": shipped.get(r, zero), "mine": mine.get(r, zero), "rate": hit_rate(r, table)}
            for r in ROLE_NAMES]
    return sorted(rows, key=lambda row: -row["rate"])
