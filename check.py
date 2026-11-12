"""check: a read-only audit of the tags an album's files hold, so you can verify what a job wrote.

It reads a job's files (where the plan put them), a saved plan (the tags it would leave: each
file's tags now with the plan's changes applied; or, once the plan was applied, the files where
they are), or every album under a folder. It never writes to any file.

A finding is {album, file, kind, detail}. Kinds that count as problems: PROBLEMS. A note
(KINDS) only points at something worth a look and may be fine."""
from __future__ import annotations

import csv
import io
import json
import os
import re

ESSENTIAL = ("album", "albumartist", "artist", "title", "tracknumber")
RELEASE_IDS = ("musicbrainz_albumid", "discogs_release_id")
PRESSING = RELEASE_IDS + ("barcode", "catalognumber")
PROBLEMS = ("missing", "numbers", "featured", "ids", "pressing", "leftover")
KINDS = PROBLEMS + ("credit", "notapplied")

_FEATURED = re.compile(r"\b(?:feat\.?|featuring|ft\.|presents)\s", re.I)
_JOINED = re.compile(r"\s*(?:&|\+|,)\s+|\s+(?:and|with)\s+", re.I)
_NOT_HERE = "are not in this folder"


def _first(v) -> str:
    if isinstance(v, (list, tuple)):
        return str(v[0]) if v else ""
    return str(v or "")


def _fold(s: str) -> str:
    return " ".join(str(s).casefold().split())


def read_tags(path) -> dict:
    """The file's tags, names in lower case; None when it is missing or cannot be read."""
    import tagio
    try:
        return {k.lower(): v for k, v in tagio.read(str(path)).items()}
    except Exception:
        return None


def planned_tags(fe) -> dict:
    """What the plan leaves in a file: its tags now, then the plan's diff and changes on top."""
    tags = dict(read_tags(fe["src"]) or {})
    for row in fe.get("diff") or []:
        tags[str(row["field"]).lower()] = [] if row.get("action") == "removed" else row.get("after")
    for k, v in (fe.get("changes") or {}).items():
        tags[str(k).lower()] = [] if v is None else v
    return tags


def _check_tags(album, rows, tier, noted_missing, findings):
    """rows: [(file name, tags or None)] of one album."""
    per_disc = {}
    for name, tags in rows:
        def add(kind, detail, name=name):
            findings.append({"album": album, "file": name, "kind": kind, "detail": detail})
        if tags is None:
            add("missing", "the file cannot be read")
            continue
        for k in ESSENTIAL:
            if not _first(tags.get(k)).strip():
                add("missing", f"has no {k}")
        values = [str(v) for v in (tags.get("albumartist") or [])]
        if _FEATURED.search(" ".join(values) + " "):
            add("featured", f"the album artist names a guest: {' / '.join(values)}")
        aa, ar = _first(tags.get("albumartist")), _first(tags.get("artist"))
        if aa and ar and _fold(aa) != _fold(ar) and _fold(ar).startswith(_fold(aa)) \
                and _JOINED.match(ar[len(aa):] + " "):
            add("credit", f"album artist '{aa}' but track artist '{ar}' (a joint credit, perhaps fine)")
        d = _first(tags.get("discnumber")).partition("/")[0].strip() or "1"
        per_disc.setdefault(d, []).append((_first(tags.get("tracknumber")).partition("/")[0].strip(), name))
    for d, items in per_disc.items():
        nums = sorted(int(n) for n, _ in items if n.isdigit())
        where = f"disc {d}: " if len(per_disc) > 1 else ""
        dup = sorted({n for n in nums if nums.count(n) > 1})
        if dup:
            findings.append({"album": album, "file": "", "kind": "numbers",
                             "detail": f"{where}track number {', '.join(map(str, dup))} is used more than once"})
        elif nums and nums != list(range(1, len(nums) + 1)) and not noted_missing:
            findings.append({"album": album, "file": "", "kind": "numbers",
                             "detail": f"{where}the track numbers are {nums[0]}-{nums[-1]} with "
                                       f"{len(nums)} files, not 1 to {len(nums)}"})


def _notes_of(a) -> list:
    ident = a.get("identification") or {}
    return [str(x) for key in ("why", "notes") for x in (ident.get(key) or [])] + \
           [str(x) for x in (a.get("notes") or [])]


def _album_name(a, rows) -> str:
    for _, tags in rows:
        name = _first((tags or {}).get("album"))
        if name:
            return f"{name} ({a.get('source_dir') or ''})".strip()
    return str(a.get("source_dir") or "")


def _leftovers(a, accounted) -> list:
    src = a.get("source_dir")
    files = a.get("files") or []
    if not src or not os.path.isdir(src) or not any(
            fe.get("op") == "move" and fe.get("dst") != fe.get("src") for fe in files):
        return []
    dst_dirs = {os.path.dirname(fe["dst"]) for fe in files if fe.get("dst")}
    dst_dirs |= {os.path.dirname(d) for d in dst_dirs}                 # (a Disc folder's album folder)
    if os.path.normpath(src) in {os.path.normpath(d) for d in dst_dirs}:
        return []
    try:
        names = sorted(os.listdir(src))
    except OSError:
        return []
    return [n for n in names if not n.startswith(".") and os.path.isfile(os.path.join(src, n))
            and os.path.normpath(os.path.join(src, n)) not in accounted]


def _accounted(plan) -> set:
    out = set()
    for a in plan.get("albums") or []:
        for fe in (a.get("files") or []) + (a.get("companions") or []):
            for k in ("src", "dst", "copy_of"):
                if fe.get(k):
                    out.add(os.path.normpath(fe[k]))
    return out


def check_plan_album(a, accounted, applied) -> dict:
    """One planned album. applied: look at the files where the plan put them; otherwise at the
    tags the plan would leave."""
    findings, files = [], a.get("files") or []
    tier = (a.get("identification") or {}).get("tier")
    folder = str(a.get("source_dir") or "")
    gone = not any(os.path.exists(fe.get("dst") or "") or (not applied and os.path.exists(fe.get("src") or ""))
                   for fe in files)
    if files and gone:
        findings.append({"album": folder, "file": "", "kind": "notapplied",
                         "detail": "none of this album's files exist, at the old or the new place "
                                   "(the plan was not applied, or the files were removed)"})
        return {"album": folder, "folder": folder, "tier": tier, "files": len(files), "findings": findings}
    rows = []
    for fe in files:
        if applied:
            tags = read_tags(fe["dst"])
        elif os.path.exists(fe.get("src") or ""):
            tags = planned_tags(fe)
        else:
            tags = read_tags(fe["dst"])
        rows.append((os.path.basename(fe.get("dst") or fe.get("src") or ""), tags))
    album = _album_name(a, rows)
    _check_tags(album, rows, tier, any(_NOT_HERE in n for n in _notes_of(a)), findings)
    if tier == "exact" and rows and not any(_first((t or {}).get(k)) for _, t in rows for k in RELEASE_IDS):
        findings.append({"album": album, "file": "", "kind": "ids",
                         "detail": "identified as an exact release, but no release ID is written"})
    if tier == "album_only":
        for fe in files:
            for k in PRESSING:
                if _first((fe.get("changes") or {}).get(k)).strip():
                    findings.append({"album": album, "file": os.path.basename(fe.get("dst") or fe.get("src") or ""),
                                     "kind": "pressing",
                                     "detail": f"only the album is known, yet the pressing's {k} is written"})
    if applied:
        left = _leftovers(a, accounted)
        if left:
            more = f" and {len(left) - 5} more" if len(left) > 5 else ""
            findings.append({"album": album, "file": "", "kind": "leftover",
                             "detail": f"left behind in the old folder {folder}: {', '.join(left[:5])}{more}"})
    for f in findings:
        f["album"] = album
    return {"album": album, "folder": folder, "tier": tier, "files": len(files), "findings": findings}


def check_plan(plan, applied=None) -> list:
    """applied: True = check the files where the plan put them (a job's); otherwise decide per
    album (a saved plan: the files are at their new place if they moved and the old ones are gone;
    else the tags it would leave)."""
    acc, out = _accounted(plan), []
    for a in plan.get("albums") or []:
        if a.get("status") not in ("ready", "unchanged"):
            continue
        files = a.get("files") or []
        mode = bool(applied) or any(
            fe.get("dst") and fe.get("dst") != fe.get("src") and os.path.exists(fe["dst"])
            and not os.path.exists(fe.get("src") or "") for fe in files)
        out.append(check_plan_album(a, acc, mode))
    return out


def folder_albums(path, profile) -> list:
    """The albums below path, grouped as `tagmender scan` groups them (engine.albums_in)."""
    import engine
    e = engine.Engine.__new__(engine.Engine)          # offline: grouping only
    e.profile = profile
    return e.albums_in(path)


def check_folder(path, profile) -> list:
    out = []
    for g in folder_albums(path, profile):
        folder = str(g.folder)
        rows = [(os.path.basename(str(f)), read_tags(f)) for f in g.files]
        findings = []
        _check_tags(folder, rows, None, False, findings)
        out.append({"album": folder, "folder": folder, "tier": None, "files": len(g.files), "findings": findings})
    return out


def result(target, mode, albums) -> dict:
    counts = {k: 0 for k in KINDS}
    for a in albums:
        for f in a["findings"]:
            counts[f["kind"]] = counts.get(f["kind"], 0) + 1
    return {"target": str(target), "mode": mode, "albums": albums, "counts": counts,
            "problems": sum(counts[k] for k in PROBLEMS), "ok": not any(counts[k] for k in PROBLEMS)}


_WHAT = {"job": "the files the job wrote", "plan": "a saved plan: the tags it would leave",
         "files": "the files where a saved plan put them", "folder": "the albums in the folder"}
_WORDS = {"missing": "a needed tag is missing", "numbers": "track numbers look wrong",
          "featured": "a guest is named in the album artist", "ids": "no release ID written",
          "pressing": "a pressing's details written for an album-only match",
          "leftover": "files left behind in the old folder", "credit": "a joint credit to look at (a note)",
          "notapplied": "not applied or files removed (a note)"}


def text(res) -> str:
    lines = [f"Checked {_WHAT.get(res['mode'], res['mode'])}: {res['target']}"]
    for a in res["albums"]:
        if not a["findings"]:
            continue
        lines.append("")
        lines.append(a["album"])
        for f in a["findings"]:
            lines.append(f"  {f['kind']:<10} {f['file'] + ': ' if f['file'] else ''}{f['detail']}")
    n = sum(len(a["findings"]) for a in res["albums"])
    lines.append("")
    if not n:
        lines.append(f"{len(res['albums'])} album(s) checked, nothing found.")
    else:
        lines.append(f"{len(res['albums'])} album(s) checked. Found:")
        for k in KINDS:
            if res["counts"].get(k):
                lines.append(f"  {res['counts'][k]:>4}  {_WORDS[k]}")
    lines.append("Nothing was changed.")
    return "\n".join(lines)


def json_text(res) -> str:
    return json.dumps(res, indent=2, ensure_ascii=False)


def csv_text(res) -> str:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["album", "file", "kind", "detail"])
    for a in res["albums"]:
        for f in a["findings"]:
            w.writerow([f["album"], f["file"], f["kind"], f["detail"]])
    return buf.getvalue()
