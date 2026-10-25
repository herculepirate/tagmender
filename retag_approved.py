#!/usr/bin/env python3
"""retag_approved.py - the approved retags in one go, checked at every step.

Runs, in order, what you would otherwise run and paste one by one:

  1. validate.py <library> --export-fixes --only-approved     (fixes.db from approved.tsv)
  2. tag_albums.py --retag            (preview)  -> every planned album must be in
     approved.tsv and go to exactly the release approved (or its MusicBrainz twin)
  3. web_tag.py                       (preview)  -> every Discogs-only album must be
     tagged from Discogs, never from a shop's listing
  4. with --apply: tag_albums.py --retag --apply --batch 3 --settle 30, then once more
     for the albums Picard did not save, then web_tag.py --apply

It stops at the first check that fails and says why; nothing is written unless every
check passed. Everything it ran and printed is kept in ~/Downloads/autopilot/<run>/
(log.txt, summary.txt), and the summary is printed at the end.

Picard must be closed, and its "Move files to" must be the library being tagged.

  python3 retag_approved.py                                   # checks only, writes nothing
  python3 retag_approved.py --apply                           # checks, then tags
  python3 retag_approved.py --library /media/unraidmedia2/music/ambient_music --apply
  python3 retag_approved.py --new --apply       # the UNTAGGED albums the matcher is sure of
                                                # (~/Downloads/matching_<library>/match_state.db)

With --new there is no approved list: the matcher's own sure matches ("auto") of albums
that carry no release yet are tagged, with the same checks; albums left for review are
listed in the summary, never tagged.
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEFAULT_LIBRARY = "/media/unraidmedia2/music/extreme_music"


class Stop(Exception):
    pass


class Runner:
    def __init__(self, run_dir: Path):
        self.run_dir = run_dir
        self.log = open(run_dir / "log.txt", "w", encoding="utf-8")
        self.summary: list[str] = []

    def run(self, *args, check=True) -> str:
        cmd = [sys.executable, *[str(a) for a in args]]
        self.log.write(f"\n$ {' '.join(cmd)}\n")
        self.log.flush()
        print(f"  running: {' '.join(str(a) for a in args)}", flush=True)
        r = subprocess.run(cmd, cwd=HERE, capture_output=True, text=True)
        out = r.stdout + r.stderr
        self.log.write(out)
        self.log.flush()
        if check and r.returncode not in (0, None):
            raise Stop(f"{Path(str(args[0])).name} failed (exit {r.returncode}):\n" + out[-1500:])
        return out

    def say(self, line=""):
        print(line, flush=True)
        self.summary.append(line)


def approved_releases(fixes_db: Path, sure_only=False) -> dict:
    """{folder: {"mb": MusicBrainz release or "", "discogs": Discogs release or ""}} from fixes.db
    (or, sure_only, the matcher's own results: only those it is sure of, status auto)."""
    db = sqlite3.connect(fixes_db)
    out = {}
    for path, blob in db.execute("SELECT path, result FROM results"):
        o = json.loads(blob)
        if sure_only and o.get("status") != "auto":
            continue
        b = (o.get("best") or {})
        out[path] = {"mb": b.get("mb_id") or (b.get("id") if b.get("source") == "mb" else ""),
                     "discogs": b.get("id") if b.get("source") == "discogs" else ""}
    return out


def check_tag_plan(plan_csv: Path, wanted: dict) -> tuple[list, list]:
    """(albums planned, problems): every planned album approved, to the approved release."""
    planned, problems = [], []
    with open(plan_csv, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            folder, rel = row["Album folder"], row["MusicBrainz release"].rsplit("/", 1)[-1]
            if not rel:                                     # a SKIPPED line
                continue
            planned.append(folder)
            w = wanted.get(folder)
            if w is None:
                problems.append(f"{folder}: planned but not in the approved list")
            elif w["mb"] != rel:
                problems.append(f"{folder}: planned as {rel}, approved {w['mb'] or w['discogs']}")
    return planned, problems


def check_web_plan(report_csv: Path, wanted: dict) -> tuple[list, list]:
    """(albums found, problems): every Discogs-only approved album tagged from Discogs."""
    found, problems = [], []
    with open(report_csv, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            folder = row["album"]
            w = wanted.get(folder) or {}
            if not w.get("discogs") or w.get("mb"):
                continue                                    # not one of the Discogs-only ones
            if row["source"] != "Discogs":
                problems.append(f"{folder}: would be tagged from {row['source'] or 'nothing'} "
                                f"({row['result'] or row['matched']}), not Discogs {w['discogs']}")
            else:
                found.append(folder)
    return found, problems


def results_of(out: str) -> tuple[Path | None, dict]:
    """The run's results.csv and {album: state} from tag_albums' output."""
    m = re.search(r"^Results: (.+results\.csv)$", out, re.M)
    if not m:
        return None, {}
    path = Path(m.group(1))
    with open(path, newline="", encoding="utf-8") as fh:
        return path, {r["album"]: r["state"] for r in csv.DictReader(fh)}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--library", default=DEFAULT_LIBRARY)
    ap.add_argument("--apply", action="store_true", help="tag once every check passed (default: checks only)")
    ap.add_argument("--batch", type=int, default=3)
    ap.add_argument("--settle", type=int, default=30)
    ap.add_argument("--new", action="store_true",
                    help="tag the untagged albums the matcher is sure of, not the approved retags")
    args = ap.parse_args(argv)
    lib = Path(args.library)
    vdir = Path.home() / "Downloads" / f"validate_{lib.name}"
    fixes = vdir / "fixes.db"
    if args.new:
        fixes = Path.home() / "Downloads" / f"matching_{lib.name.split('_')[0]}" / "match_state.db"
        if not fixes.exists():                            # the first library's results: ~/Downloads/matching
            fixes = Path.home() / "Downloads" / "matching" / "match_state.db"
    run_dir = Path.home() / "Downloads" / "autopilot" / time.strftime("%Y%m%d-%H%M%S")
    run_dir.mkdir(parents=True, exist_ok=True)
    r = Runner(run_dir)
    r.say(f"retag_approved {'(apply)' if args.apply else '(checks only)'}: {lib}")
    try:
        retag = [] if args.new else ["--retag"]
        if args.new:
            if not fixes.exists():
                raise Stop(f"no matcher results at {fixes}")
            wanted = approved_releases(fixes, sure_only=True)
            review = [p for p, blob in sqlite3.connect(fixes).execute("SELECT path, result FROM results")
                      if json.loads(blob).get("status") == "review" and Path(p).is_dir()]
            r.say(f"1. matcher results: {len(wanted)} album(s) it is sure of; {len(review)} left for "
                  f"review (not tagged; listed in log.txt)")
            r.log.write("\nLeft for review:\n" + "\n".join(sorted(review)) + "\n")
        else:
            out = r.run("validate.py", lib, "--export-fixes", "--only-approved")
            m = re.search(r"(\d+) album\(s\) to re-tag", out)
            r.say(f"1. approved list exported: {m.group(1) if m else '?'} album(s)")
            wanted = approved_releases(fixes)

        out = r.run("tag_albums.py", "--matches-db", fixes, *retag)
        planned, problems = check_tag_plan(Path.home() / "Downloads" / "tagging" / "tag_plan.csv", wanted)
        if problems:
            raise Stop("the Picard plan does not match the approved list:\n  " + "\n  ".join(problems))
        r.say(f"2. Picard plan checked: {len(planned)} album(s), each to its approved release")

        out = r.run("web_tag.py", "--matches-db", fixes, "--library", lib)
        m = re.search(r"Report: (.+\.csv)$", out, re.M)
        found, problems = check_web_plan(Path(m.group(1)), wanted) if m else ([], ["no web_tag report"])
        if problems:
            raise Stop("the Discogs-only albums would not be tagged from Discogs:\n  " + "\n  ".join(problems))
        r.say(f"3. Discogs-only albums checked: {len(found)} album(s), each from its Discogs release")

        if not args.apply:
            r.say("Checks passed. Nothing was changed. Run again with --apply to tag.")
            return 0
        if planned:
            out = r.run("tag_albums.py", "--matches-db", fixes, *retag, "--apply",
                        "--batch", args.batch, "--settle", args.settle, check=False)
            _path, states = results_of(out)
            again = [a for a, s in states.items() if not s.startswith("tagged")]
            if again:                                       # Picard busy or slow: once more
                r.say(f"4. Picard: {len(states) - len(again)} tagged; trying {len(again)} again")
                for a in again:
                    out = r.run("tag_albums.py", "--matches-db", fixes, *retag, "--apply",
                                "--only", a, "--batch", 1, "--settle", args.settle * 2, check=False)
                    states.update(results_of(out)[1])
            done = [a for a, s in states.items() if s.startswith("tagged")]
            r.say(f"4. Picard: {len(done)} of {len(states)} album(s) tagged")
            for a, s in sorted(states.items()):
                if not s.startswith("tagged"):
                    r.say(f"     NOT DONE ({s}): {a}")
        if found:
            out = r.run("web_tag.py", "--matches-db", fixes, "--library", lib, "--apply", check=False)
            m = re.search(r"(\d+) of (\d+) found on the web", out)
            r.say(f"5. Discogs-only: {m.group(1) if m else '?'} of {m.group(2) if m else '?'} tagged from Discogs")
        r.say("Done.")
        return 0
    except Stop as exc:
        r.say(f"STOPPED, nothing more was changed: {exc}")
        return 1
    finally:
        (run_dir / "summary.txt").write_text("\n".join(r.summary) + "\n", encoding="utf-8")
        print(f"\nLog and summary: {run_dir}")


if __name__ == "__main__":
    sys.exit(main())
