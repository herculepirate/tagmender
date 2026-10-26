#!/usr/bin/env python3
"""health_report.py - one page on the state of your libraries.

For each library: how many albums are tagged, what the matcher decided for the
rest, why albums are waiting for review, and what the tagging runs did. Writes a
single HTML file that opens in any browser (no internet needed).

  python3 health_report.py \\
      --library extreme ~/Downloads/audit_extreme_music.csv ~/Downloads/matching_extreme \\
      --library ambient ~/Downloads/audit_ambient_music.csv ~/Downloads/matching \\
      --out ~/Downloads/library_health.html

Run audit_untagged.py first for up-to-date tag counts.
"""
from __future__ import annotations

import argparse
import csv
import html
import json
import re
import sqlite3
import sys
import time
from collections import Counter
from pathlib import Path

AUDIT_LABELS = [("tagged_both", "MusicBrainz + Discogs"), ("musicbrainz_only", "MusicBrainz only"),
                ("discogs_only", "Discogs only"), ("partial", "Partly tagged"),
                ("album_only", "Album tags (pressing unknown)"), ("untagged", "Untagged")]


def reason_group(why):
    w = " ".join(why)
    for key, label in (("track count", "Track count differs"), ("durations", "Track lengths differ"),
                       ("edition", "Edition year not listed"), ("title similarity", "Title differs"),
                       ("artist similarity", "Artist differs"), ("runner-up", "Pressing undecided"),
                       ("no candidates", "Not found")):
        if key in w:
            return label
    return "Other"


def library_stats(audit_csv, match_dir):
    audit = Counter()
    if audit_csv and Path(audit_csv).expanduser().exists():
        for r in csv.DictReader(open(Path(audit_csv).expanduser(), encoding="utf-8")):
            audit[r.get("Status", "")] += 1
    match, reasons, sources = Counter(), Counter(), Counter()
    dbp = Path(match_dir).expanduser() / "match_state.db" if match_dir else None
    if dbp and dbp.exists():
        db = sqlite3.connect(dbp)
        for (blob,) in db.execute("SELECT result FROM results"):
            o = json.loads(blob)
            match[o.get("status")] += 1
            if o.get("status") == "auto":
                sources[(o.get("best") or {}).get("source") or "?"] += 1
            if o.get("status") in ("review", "nomatch"):
                reasons[reason_group(o.get("why") or [])] += 1
    return audit, match, reasons, sources


def tagging_runs(tag_dir):
    states, runs, last = Counter(), 0, ""
    for run in sorted(Path(tag_dir).expanduser().glob("run-*")):
        res = run / "results.csv"
        if not res.exists():
            continue
        runs += 1
        last = run.name[len("run-"):]
        for r in csv.DictReader(open(res, encoding="utf-8")):
            states[r.get("state") or r.get("result") or "?"] += 1
    return states, runs, last


def bars(items, total=None):
    total = total or sum(n for _l, n in items) or 1
    rows = []
    for label, n in items:
        pct = 100 * n / total
        rows.append(f'<div class="row"><span class="lbl">{html.escape(str(label))}</span>'
                    f'<span class="bar"><i style="width:{pct:.1f}%"></i></span>'
                    f'<span class="num">{n}</span></div>')
    return "".join(rows)


def render(libs, tag_states, runs, last):
    parts = []
    for name, (audit, match, reasons, sources) in libs:
        total = sum(audit.values())
        tagged = audit["tagged_both"] + audit["musicbrainz_only"] + audit["discogs_only"]
        pct = 100 * tagged / total if total else 0
        parts.append(f"<section><h2>{html.escape(name)}</h2>"
                     f'<p class="big">{pct:.0f}% <small>tagged ({tagged} of {total} albums)</small></p>'
                     f"<h3>Tags in the files</h3>{bars([(lbl, audit[k]) for k, lbl in AUDIT_LABELS], total)}"
                     f"<h3>What the matcher decided</h3>"
                     f"{bars([('Confident: MusicBrainz', sources['mb']), ('Confident: Discogs only', sources['discogs']), ('Review', match['review']), ('No match', match['nomatch'])])}"
                     f"<h3>Why albums wait</h3>{bars(sorted(reasons.items(), key=lambda x: -x[1]))}</section>")
    parts.append(f"<section><h2>Tagging runs</h2><p>{runs} runs, latest {html.escape(last or '-')}</p>"
                 f"{bars(sorted(tag_states.items(), key=lambda x: -x[1]))}</section>")
    return f"""<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Library Health</title><style>
:root{{--bg:#fafaf8;--fg:#1d1d1b;--mut:#6b6b66;--bar:#2f6f8f;--track:#e6e6e1;--card:#fff}}
@media (prefers-color-scheme:dark){{:root{{--bg:#151515;--fg:#ececea;--mut:#a3a39e;--bar:#6fb3d2;--track:#2a2a2a;--card:#1d1d1d}}}}
body{{background:var(--bg);color:var(--fg);font:15px/1.45 system-ui,sans-serif;max-width:60rem;margin:0 auto;padding:1.5rem 1rem}}
section{{background:var(--card);border-radius:10px;padding:1rem 1.2rem;margin:1rem 0}}
h1{{margin:.2rem 0}} h2{{margin:.2rem 0 .6rem}} h3{{font-size:.9rem;color:var(--mut);margin:1rem 0 .4rem;font-weight:600}}
.big{{font-size:2rem;margin:.2rem 0}} .big small{{font-size:.9rem;color:var(--mut)}}
.row{{display:grid;grid-template-columns:12rem 1fr 3.5rem;gap:.6rem;align-items:center;margin:.25rem 0}}
.bar{{background:var(--track);border-radius:4px;height:.7rem;overflow:hidden}} .bar i{{display:block;height:100%;background:var(--bar)}}
.num{{text-align:right;font-variant-numeric:tabular-nums}} .lbl{{color:var(--fg)}}
@media (max-width:540px){{.row{{grid-template-columns:8rem 1fr 3rem}}}}
</style></head><body><h1>Library Health</h1><p style="color:var(--mut)">{time.strftime('%Y-%m-%d %H:%M')}</p>
{''.join(parts)}</body></html>"""


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--library", nargs=3, action="append", metavar=("NAME", "AUDIT_CSV", "MATCH_DIR"), required=True)
    ap.add_argument("--tagging-dir", default="~/Downloads/tagging")
    ap.add_argument("--out", default="~/Downloads/library_health.html")
    args = ap.parse_args(argv)
    libs = [(name, library_stats(audit, match)) for name, audit, match in args.library]
    states, runs, last = tagging_runs(args.tagging_dir)
    out = Path(args.out).expanduser()
    out.write_text(render(libs, states, runs, last), encoding="utf-8")
    print(f"Library health: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
