"""report.py - the run report: what a job (or a plan, before it is applied) did, and what is left for you.

Three sections, each row one album:

  decide   Needs your decision: albums whose release is not proven (Partial, album only),
           two versions mixed in one folder, a split not applied yet, a release that lists
           no track lengths - with the releases that fit best, to choose from
  failed   Could not finish: albums not identified, blocked by a conflict, or that failed
           or were stopped while applying
  done     Done (for a plan: ready to apply): tags written, files moved or copied, versions
           split into folders of their own

A row: {"section", "folder" (where the album is now), "uri" (file:// link to it), "album"
(its name), "status" (a word), "issue" (what happened, in plain words), "candidates" (see
engine.candidates_of), "files" (the album's files, for choosing a release for exactly them)}.

Nothing here writes to an album: a release is chosen with `tagmender plan FOLDER --release REF`
(or the app's preview), which is previewed and applied as any plan.

    build(plan, results=None)      a report of a plan; results: the job's album rows when applied
    for_job(job)                   a job's report (its saved plan and what happened to each album)
    for_plan(path)                 a saved plan's report, before applying
    latest_job()                   the newest job's ID, or None
    text(rep), html_page(rep), csv_text(rep)   the report as text, a web page, CSV
"""
from __future__ import annotations

import csv
import html
import io
import json
import os
import re
import time
from pathlib import Path

import reasons

SECTIONS = (("decide", "Needs your decision"), ("failed", "Could not finish"), ("done", "Done"))
PLAN_DONE = "Ready to apply"                  # the third section of a plan not applied yet
NO_LENGTHS = "the release lists no track lengths, so the files can't be proven to match"
MIXED = "two versions of the album are mixed"
CSV_COLUMNS = ("section", "album", "folder", "link", "status", "issue", "suggested releases", "apply")
FAILED = ("failed", "conflict", "cancelled")
UNDONE_NOTE = "This job was undone since: its albums were put back as they were before it."
RUNNING_NOTE = "This job is still running (or was interrupted): what it has not reached yet is not listed."
ATTENTION_TITLE = "Albums marked Unresolved or Partial"

# a reason code in plain words, for the Issue column (reasons.describe has the long sentence)
ISSUE_WORDS = {"no_candidate": "not found on MusicBrainz or Discogs",
               "incomplete_album": "the track count differs from every release",
               "ambiguous_release": "more than one release fits about equally well",
               "ambiguous_artist": "the artist could not be told apart from another of that name",
               "conflicting_sources": "the sources disagree",
               "web_fallback_only": "only found on the web",
               "musicbrainz_unavailable": "MusicBrainz could not be reached; try again later",
               "discogs_unavailable": "Discogs could not be reached; try again later",
               "web_unavailable": "a web source could not be reached; try again later",
               "rate_limited": "a database asked to slow down; try again later",
               "missing_credentials": "a key or token is not set up",
               "unsupported_format": "this file format cannot be tagged safely",
               "duration_mismatch": "the track lengths do not agree closely enough",
               "low_similarity": "the artist or title agree only partly",
               "low_confidence": "no release fits surely enough",
               "missing_collaborator": "the database credit leaves out an artist your tags name",
               "write_failed": "the tags could not be written",
               "partially_tagged": "some files already carry IDs from another release",
               "other": "not identified"}


# --------------------------------------------------------------------------
# building
# --------------------------------------------------------------------------
def file_uri(path) -> str:
    """A folder as a file:// link (what a file manager opens)."""
    try:
        return Path(os.path.abspath(str(path))).as_uri()
    except ValueError:
        return ""


def _name(a) -> str:
    rel = a.get("release") or {}
    first = lambda k: (rel.get(k) or [""])[0] if isinstance(rel.get(k), list) else str(rel.get(k) or "")  # noqa: E731
    who, what = first("albumartist") or first("artist"), first("album")
    return f"{who} - {what}" if who and what else (what or Path(a["source_dir"]).name)


def _count(gaps: str) -> int:
    """How many tracks a TAGMENDER_MISSING value names ("2, 4, 7-10" or "1:3, 2:5")."""
    n = 0
    for part in str(gaps or "").split(","):
        part = part.strip().rsplit(":", 1)[-1]
        m = re.fullmatch(r"(\d+)-(\d+)", part)
        if m:
            n += max(0, int(m.group(2)) - int(m.group(1)) + 1)
        elif part.isdigit():
            n += 1
    return n


def _gaps(a) -> str:
    for n in a.get("notes") or []:
        m = re.match(r"tracks (.+?) of the release are not in this folder", str(n))
        if m:
            return m.group(1)
    return ""


def missing_words(a) -> str:
    """"8 tracks of 32 missing: tagged the 24 present", or ""."""
    gaps = _gaps(a)
    if not gaps:
        return ""
    gone, here = _count(gaps), len([fe for fe in a.get("files") or []])
    total = gone + here
    return (f"{gone} track{'s' if gone != 1 else ''} of {total} missing: tagged the {here} present"
            f" (missing: {gaps})")


def _no_lengths(a) -> bool:
    ident = a.get("identification") or {}
    text = " ".join(map(str, (ident.get("why") or []) + (ident.get("evidence") or []) + [ident.get("reason") or ""]))
    if "durations unavailable" in text or "lists no track lengths" in text:
        return True
    return any(c.get("lengths") is False for c in (ident.get("candidates") or [])[:1])


def candidate_words(c) -> str:
    """A suggested release in words, what tells it from the others first (a narrow column cuts the end)."""
    head = c.get("title") or c["id"]
    return f"{c['details']}: {head}" if c.get("details") else head


def _reason(a) -> tuple:
    ident = a.get("identification") or {}
    return reasons.parse(ident.get("reason", ""))


def _reason_words(a) -> str:
    codes, words = _reason(a)
    lead = "; ".join(ISSUE_WORDS.get(c, reasons.describe(c)) for c in codes if c != "other")
    if lead and words:
        return f"{lead} ({words})"
    return lead or words or "not identified"


def _moved_to(a) -> str:
    """The folder the album's files go to when it is not their own (the deepest folder all share)."""
    dirs = {os.path.dirname(fe["dst"]) for fe in a.get("files") or [] if fe.get("op") != "none"
            and fe.get("dst") and fe.get("dst") != fe.get("src")}
    if not dirs:
        return ""
    try:
        return os.path.commonpath(sorted(dirs))
    except ValueError:
        return sorted(dirs)[0]


def _where(a, done) -> str:
    """Where the album is now: its new folder once its files went there, else its own."""
    return (_moved_to(a) if done else "") or a["source_dir"]


def _version_name(a) -> str:
    ident = a.get("identification") or {}
    words = " ".join(ident.get("why") or []) + " " + " ".join(a.get("notes") or [])
    rid = str(ident.get("release_id") or "")
    m = re.search(re.escape(rid) + r' "([^"]+)"', words) if rid else None
    return m.group(1) if m else _name(a)


def _done_words(a, planned) -> str:
    """What was done to the album (or will be, for a plan): tags, moves, covers and misc folders."""
    files = [fe for fe in a.get("files") or [] if fe.get("op") != "none"]
    tagged = sum(1 for fe in files if fe.get("changes") or fe.get("diff"))
    moved = [fe for fe in files if fe.get("dst") and fe.get("dst") != fe.get("src")]
    copied = any(fe.get("op") == "copy" for fe in moved)
    out = []
    if tagged:
        out.append(f"tags {'to be written to' if planned else 'written to'} {tagged} file{'s' if tagged != 1 else ''}")
    if moved:
        verb = ("to be copied" if copied else "to be moved") if planned else ("copied" if copied else "moved")
        out.append(f"{verb} to {_moved_to(a)}")
    tidy = {}
    for c in a.get("companions") or []:
        if c.get("tidy"):
            tidy[c["tidy"]] = tidy.get(c["tidy"], 0) + 1
    for kind in sorted(tidy):
        out.append(f"{tidy[kind]} file{'s' if tidy[kind] != 1 else ''} into the {kind} folder")
    art = a.get("artwork") or {}
    if art.get("file") or art.get("embed_into"):
        out.append("front cover " + ("to be added" if planned else "added"))
    sp = a.get("split")
    if sp:
        out.insert(0, f"two versions were in one folder: this is version {sp['index'] + 1} of {sp['count']}, "
                      f"{_version_name(a)}")
    miss = missing_words(a)
    if miss:
        out.append(miss)
    tier = (a.get("identification") or {}).get("tier")
    if tier == "partial":
        out.append("tagged Partial (the release is not proven)")
    elif tier == "album_only":
        out.append("tagged as the album only (the pressing is not proven)")
    return "; ".join(out) or ("nothing to change" if planned else "done")


def _decide_words(a, applied) -> str:
    """Why the album needs your decision, or "" when it does not."""
    ident = a.get("identification") or {}
    tier = ident.get("tier") or ""
    out = []
    _codes, words = _reason(a)
    if a.get("status") == "unresolved" and MIXED in words:
        out.append("two versions mixed in one folder" + (f" ({words.split('(not split for you:', 1)[1].strip(' )')})"
                                                          if "(not split for you:" in words else ": split them"))
    if a.get("split") and not applied:
        sp = a["split"]
        out.append(f"two versions in one folder, to be split: version {sp['index'] + 1} of {sp['count']} is "
                   f"{_version_name(a)}; check it is the one these files are"
                   + (f" (into {sp['folder']})" if sp.get("folder") else ""))
    if tier == "partial":
        out.append("the album is known but its release is not proven: choose the release" +
                   (f" ({words})" if words else ""))
    # (album_only: every clue was tried and none tells the pressing, so it is tagged as the album,
    # its original date and no pressing's details; that is the answer, not a question)
    if _no_lengths(a) and a.get("status") != "excluded" and tier not in ("exact", "album_only"):
        out.append(NO_LENGTHS)
    return "; ".join(dict.fromkeys(out))


def _failed_words(a, res) -> str:
    if res and res.get("status") in FAILED:
        if res["status"] == "cancelled":
            return "stopped before this album was reached"
        return res.get("detail") or res.get("label") or res["status"]
    if a.get("status") == "blocked" or (res and res.get("status") == "blocked"):
        return "blocked: " + ("; ".join(a.get("conflicts") or []) or (res or {}).get("detail") or "a conflict")
    if (a.get("identification") or {}).get("tier") == "unresolved" or a.get("status") == "unresolved":
        _codes, words = _reason(a)
        if MIXED in words:
            return ""
        return "not identified: " + _reason_words(a)
    return ""


def _row(section, a, issue, folder, status, done=False) -> dict:
    ident = a.get("identification") or {}
    cands = list(ident.get("candidates") or [])
    if section == "failed" and status in ("Failed", "Stopped") and ident.get("tier") == "exact":
        cands = []                                  # (the release was certain; another would not have helped)
    if not cands and ident.get("release_id") and ident.get("provider") in ("musicbrainz", "discogs") \
            and section == "decide":
        src = "mb" if ident["provider"] == "musicbrainz" else "discogs"
        rid = str(ident["release_id"])
        cands = [{"source": ident["provider"], "id": rid, "url": ident.get("url") or "",
                  "ref": rid if src == "mb" else f"discogs:{rid}", "score": ident.get("confidence") or 0,
                  "title": _name(a), "lengths": None}]
    # (where a release chosen for it is previewed: the album's files where they are now)
    files = [fe["dst"] if done else fe["src"] for fe in a.get("files") or []] or list(a.get("album_files") or [])
    return {"section": section, "folder": folder, "uri": file_uri(folder),
            "album": _version_name(a) if a.get("split") else _name(a), "status": status,
            "issue": issue, "candidates": cands if section != "done" else [], "files": files,
            "source_dir": a["source_dir"], "apply_folder": folder if done else a["source_dir"],
            "split": bool(a.get("split")) and not done}


def rows_of(a, res=None, applied=False, undone=False) -> list:
    """The report's rows for one album of a plan (res: what applying it did; undone: the job
    was undone since, so the album is back where it was: its folder and files are those)."""
    st = (res or {}).get("status") or ""
    did = applied and st == "done"
    done = did and not undone                     # (where the album is now)
    out = []
    if a.get("status") in ("excluded", "skipped") or st in ("excluded", "skipped"):
        return out
    import plan as planmod
    tier = (a.get("identification") or {}).get("tier")
    decide = _decide_words(a, applied and st not in FAILED)
    failed = _failed_words(a, res if applied else None)
    if decide and not (applied and st in FAILED):
        out.append(_row("decide", a, decide, _where(a, done), "Partial" if tier == "partial" else
                        "Album only" if tier == "album_only" else "Split" if a.get("split") and not applied
                        else "Mixed" if MIXED in _reason(a)[1] else "Check", done))
    if failed:
        # (an Unresolved album moved aside: its link is where it went; one that failed or was
        # stopped is still where it was)
        out.append(_row("failed", a, failed, _where(a, done),
                        {"cancelled": "Stopped", "conflict": "Conflict"}.get(st, "Failed" if applied and st
                                                                             else "Blocked" if "blocked" in failed
                                                                             else "Unresolved"), done))
    if tier != "unresolved" and not failed and a.get("status") in ("ready", "done") and planmod.has_work(a) \
            and (did or not applied):
        out.append(_row("done", a, _done_words(a, planned=not applied), _where(a, done),
                        "Done" if applied else "Ready"))
    return out


def _results_of(plan, rows) -> list:
    """Each plan album's result (the executor's own results: by their index; else by order, one
    row per album in order, or by folder). Folders are compared as the database stores them
    (plan.db_text): a folder name that is not UTF-8 matches either way."""
    import plan as planmod
    albums = plan.get("albums") or []
    res = [None] * len(albums)
    rows = [dict(zip(("album", "status", "label", "detail", "ts"), r)) if not isinstance(r, dict) else r
            for r in rows or []]
    if rows and all(isinstance(r.get("index"), int) and 0 <= r["index"] < len(albums) for r in rows):
        for r in rows:
            res[r["index"]] = r
        return res
    key = planmod.db_text
    by_order = len(rows) <= len(albums) and all(key(albums[i]["source_dir"]) == key(r["album"])
                                                for i, r in enumerate(rows))
    if by_order:
        for i, r in enumerate(rows):
            res[i] = r
    else:
        left = list(rows)
        for i, a in enumerate(albums):
            r = next((x for x in left if key(x["album"]) == key(a["source_dir"])), None)
            if r is not None:
                left.remove(r)
                res[i] = r
    return res


def build(plan, results=None, job=None) -> dict:
    """The report of a plan; results: what applying it did (executor.job_albums rows, or the
    executor's result "albums"), None for a plan not applied. An album a stopped job never
    reached is "cancelled"."""
    applied = results is not None
    res = _results_of(plan, results) if applied else [None] * len(plan.get("albums") or [])
    if applied and job and job.get("status") not in ("running", None):
        res = [r or {"status": "cancelled", "detail": ""} for r in res]
    rep = {"job": plan.get("job") or "", "profile": plan.get("profile") or "", "applied": applied,
           "created": plan.get("created"), "status": (job or {}).get("status") or ("" if applied else "plan"),
           "rows": [], "note": ""}
    undone = str(rep["status"]).startswith("undone")
    if undone:
        rep["note"] = UNDONE_NOTE
    elif rep["status"] == "running":
        rep["note"] = RUNNING_NOTE
    for a, r in zip(plan.get("albums") or [], res):
        rep["rows"].extend(rows_of(a, r, applied, undone))
    order = {k: i for i, (k, _w) in enumerate(SECTIONS)}
    rep["rows"].sort(key=lambda r: (order[r["section"]], r["folder"].casefold()))
    rep["counts"] = {k: sum(1 for r in rep["rows"] if r["section"] == k) for k, _w in SECTIONS}
    return rep


def latest_job():
    import executor
    rows = executor.jobs(1)
    return rows[0][0] if rows else None


def for_job(job) -> dict:
    """A job's report: its saved plan (the job's folder) and what happened to each album."""
    import executor
    import plan as planmod
    pj = executor.job_dir(job) / "plan.json"
    if not pj.exists():
        raise LookupError(f"no job {job} (or its plan was removed)")
    plan = planmod.load(pj)
    info = next((dict(zip(("job", "profile", "started", "ended", "status", "summary"), r))
                 for r in executor.jobs(10000) if r[0] == job), {"job": job, "status": "done"})
    rep = build(plan, executor.job_albums(job), job=info)
    rep["started"] = info.get("started")
    return rep


def for_plan(path) -> dict:
    import plan as planmod
    return build(planmod.load(path))


def suggestions(folders, jobs=50) -> dict:
    """{folder: candidates} from the newest jobs' plans (for albums found marked Unresolved or
    Partial on disk, whose plan is gone): the releases suggested when the album was planned."""
    import executor
    import plan as planmod
    want = {os.path.realpath(f) for f in folders}
    out = {}
    for job, *_rest in executor.jobs(jobs):
        try:
            plan = planmod.load(executor.job_dir(job) / "plan.json")
        except (OSError, ValueError):
            continue
        for a in plan.get("albums") or []:
            cands = (a.get("identification") or {}).get("candidates") or []
            for f in {a["source_dir"], _moved_to(a)} - {""}:
                key = os.path.realpath(f)
                if key in want and cands and key not in out:
                    out[key] = cands
        if len(out) == len(want):
            break
    return out


def for_attention(rows, look_up=True) -> dict:
    """The report of the albums the Needs attention page found marked Unresolved or Partial
    (tagmender._unresolved_albums rows): Partial ones need your decision, Unresolved ones could
    not be finished; each with the releases its last plan suggested, when one is kept."""
    found = suggestions([r["folder"] for r in rows]) if look_up and rows else {}
    rep = {"job": "", "profile": "", "applied": True, "status": "attention", "rows": [], "note": "",
           "title": ATTENTION_TITLE}
    for r in rows:
        if "album known, pressing not established" in (r.get("reason") or ""):
            continue                                 # (every clue tried: tagged as the album, nothing to decide)
        partial = str(r.get("mark", "")).lower() == "partial"
        codes = [c for c in r.get("codes") or [] if c != "other"]
        lead = "; ".join(ISSUE_WORDS.get(c, reasons.describe(c)) for c in codes)
        words = r.get("reason") or ""
        issue = f"{lead} ({words})" if lead and words else (lead or words or "not identified")
        if MIXED in words:
            issue, section = "two versions mixed in one folder: " + words, "decide"
        elif partial:
            issue, section = "tagged Partial, the release is not proven: " + issue, "decide"
        else:
            section = "failed"
        if "durations unavailable" in words:
            issue += "; " + NO_LENGTHS
        folder = r["folder"]
        rep["rows"].append({"section": section, "folder": folder, "uri": file_uri(folder),
                            "album": Path(folder).name, "status": "Partial" if partial else "Unresolved",
                            "issue": issue, "candidates": found.get(os.path.realpath(folder), []), "files": [],
                            "source_dir": folder, "apply_folder": folder, "split": False})
    rep["counts"] = {k: sum(1 for x in rep["rows"] if x["section"] == k) for k, _w in SECTIONS}
    return rep


def section_title(rep, key) -> str:
    if key == "done" and not rep.get("applied"):
        return PLAN_DONE
    return dict(SECTIONS)[key]


def apply_command(row, cand, profile="") -> str:
    """The command that previews the album with this release (nothing is written until applied)
    with the job's profile; "" for one version of a folder holding two (its files are chosen in
    the app's report)."""
    import shlex
    if row.get("split"):
        return ""
    folder = row.get("apply_folder") or row["source_dir"]
    named = f" --profile {shlex.quote(profile)}" if profile and profile != "default" else ""
    return f"tagmender plan {shlex.quote(folder)} --release {shlex.quote(cand['ref'])}{named}"


def heading(rep) -> str:
    if rep.get("title"):
        return rep["title"]
    if rep.get("applied"):
        when = time.strftime("%Y-%m-%d %H:%M", time.localtime(rep["started"])) if rep.get("started") else ""
        return f"Report of job {rep['job']}" + (f" ({when}, {rep['profile']})" if when else
                                                 f" ({rep['profile']})" if rep.get("profile") else "")
    return f"Report of plan {rep['job']} (not applied yet; {rep['profile']})"


def summary(rep) -> str:
    c = rep["counts"]
    return ", ".join(f"{c[k]} {section_title(rep, k).lower()}" for k, _w in SECTIONS)


# --------------------------------------------------------------------------
# output
# --------------------------------------------------------------------------
def text(rep) -> str:
    out = [heading(rep)] + ([rep["note"]] if rep.get("note") else [])
    for key, _w in SECTIONS:
        rows = [r for r in rep["rows"] if r["section"] == key]
        out.append(f"\n{section_title(rep, key)} ({len(rows)})")
        if not rows:
            out.append("    nothing")
        for r in rows:
            out.append(f"  {r['album']}  [{r['status']}]")
            out.append(f"    folder: {r['folder']}")
            out.append(f"    link:   {r['uri']}")
            out.append(f"    issue:  {r['issue']}")
            for c in r["candidates"]:
                out.append(f"    suggested: {candidate_words(c)}  score {c['score']}  {c['url']}")
                out.append(f"      apply:   {apply_command(r, c, rep.get('profile')) or 'in the app (Report: Apply This Release)'}")
    out.append("\n" + summary(rep))
    return "\n".join(out)


def _cell(text) -> str:
    """A CSV cell a spreadsheet shows as text: one starting = + - @ (or a tab or CR) would be
    run as a formula, so it gets a ' in front (an album tag "=1+1", a folder "-x-")."""
    t = str(text)
    return "'" + t if t[:1] in ("=", "+", "-", "@", "\t", "\r") else t


def csv_text(rep) -> str:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(CSV_COLUMNS)
    for r in rep["rows"]:
        w.writerow([_cell(x) for x in (
            section_title(rep, r["section"]), r["album"], r["folder"], r["uri"], r["status"], r["issue"],
            " | ".join(f"{candidate_words(c)} {c['url']}" for c in r["candidates"]),
            " | ".join(filter(None, (apply_command(r, c, rep.get("profile")) for c in r["candidates"]))))])
    return buf.getvalue()


def json_text(rep) -> str:
    return json.dumps(rep, indent=1, ensure_ascii=False, default=str)


_CSS = """
:root { --bg: #ffffff; --ink: #1d1d1f; --muted: #6e6e73; --line: #d2d2d7; --accent: #0a66c2; --head: #f5f5f7; }
@media (prefers-color-scheme: dark) {
  :root { --bg: #1c1c1e; --ink: #f5f5f7; --muted: #a1a1a6; --line: #3a3a3c; --accent: #4da3ff; --head: #2c2c2e; }
}
body { background: var(--bg); color: var(--ink); font: 14px/1.45 system-ui, sans-serif; margin: 24px 16px; }
h1 { font-size: 20px; margin: 0 0 4px; } h2 { font-size: 16px; margin: 28px 0 8px; }
p.sum { color: var(--muted); margin: 0 0 16px; }
table { border-collapse: collapse; width: 100%; } th, td { text-align: left; vertical-align: top;
  padding: 6px 8px; border-bottom: 1px solid var(--line); } th { background: var(--head); font-weight: 600; }
a { color: var(--accent); } code { font-size: 12px; word-break: break-all; } .none { color: var(--muted); }
.wrap { overflow-x: auto; }
"""


def html_page(rep) -> str:
    e = html.escape
    out = ["<!doctype html><html><head><meta charset='utf-8'>"
           "<meta name='viewport' content='width=device-width, initial-scale=1'>",
           f"<title>TagMender report</title><style>{_CSS}</style></head><body>",
           f"<h1>{e(heading(rep))}</h1>" + (f"<p>{e(rep['note'])}</p>" if rep.get("note") else "")
           + f"<p class='sum'>{e(summary(rep))}</p>"]
    for key, _w in SECTIONS:
        rows = [r for r in rep["rows"] if r["section"] == key]
        out.append(f"<h2>{e(section_title(rep, key))} ({len(rows)})</h2>")
        if not rows:
            out.append("<p class='none'>Nothing.</p>")
            continue
        out.append("<div class='wrap'><table><tr><th>Album</th><th>Folder</th><th>Issue</th>"
                   "<th>Suggested releases</th></tr>")
        for r in rows:
            sugg = "<br>".join(f"<a href='{e(c['url'])}'>{e(candidate_words(c))}</a> (score {c['score']})"
                               + (f"<br><code>{e(apply_command(r, c, rep.get('profile')))}</code>"
                                  if apply_command(r, c) else "")
                               for c in r["candidates"]) or "—"
            out.append(f"<tr><td>{e(r['album'])}<br><span class='none'>{e(r['status'])}</span></td>"
                       f"<td><a href='{e(r['uri'])}'>{e(r['folder'])}</a></td><td>{e(r['issue'])}</td>"
                       f"<td>{sugg}</td></tr>")
        out.append("</table></div>")
    out.append("</body></html>")
    return "\n".join(out)


def save(rep, path, kind):
    """The report written to path as "html" or "csv" (UTF-8; a folder name that is not UTF-8
    shows its odd bytes as \\xe9). Written beside it first, then put in its place: a failed
    write leaves no half file."""
    data = html_page(rep) if kind == "html" else csv_text(rep)
    try:                    # (odd bytes as \\xe9, as the file system has them)
        raw = data.encode("utf-8", "surrogateescape").decode("utf-8", "backslashreplace").encode("utf-8")
    except UnicodeError:
        raw = data.encode("utf-8", errors="backslashreplace")
    path = Path(path)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.part")
    try:
        tmp.write_bytes(raw)
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink()
    return path
