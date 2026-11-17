#!/usr/bin/env python3
"""tools/perf_check.py - the release performance check (docs/RELEASING.md, "Before pushing").

Runs on a synthetic library it makes in a temporary folder (silent FLACs, about 100 albums,
multi-disc sets among them), the providers answered by a stand-in (nothing is asked of the
network) and Qt offscreen, and measures what you wait for:

  first_row_s               a folder of 100 albums dropped on an empty Overview: its first row
  first_row_added_s         a folder of 10 albums dropped while a list shows: its first row
  first_row_during_lookup_s a folder of 10 albums dropped while a look-up runs: its first row
  first_looked_up_s         the dropped folder's first album shown looked up
  all_looked_up_s           every album of it looked up
  albums_in_cold_s          Engine.albums_in of the library, nothing remembered
  albums_in_warm_s          the same again, its files unchanged (the app's remembered tags)
  plan_album_median_s       per album, from the planner's own "timing" log lines (stagetimes)
  plan_album_p95_s
  ui_stall_max_ms           the longest the UI thread did not answer (a 10 ms QTimer watchdog)
  preview_2000_open_s       a preview of 2,000 albums opened in the Overview
  ai_calls_proven           requests to a language model for albums already proven (none allowed)

The result is written as JSON (--out) with a summary printed, then compared with the stored
numbers in tools/perf_baseline.json: more than 20% slower than those (and a little more than
timing noise), or a target missed, exits 1. --update-baseline stores this run's numbers instead.

  --scan DIR [--scan DIR ...]   also time albums_in over real folders (read only; first time and
                                again unchanged); judged against the targets scaled to their count
  --from-log FILE               only summarise the "timing" lines of a real log (median, p95, where
                                the time went) and exit

Run from the repository: env -u VIRTUAL_ENV /usr/bin/python3 tools/perf_check.py
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BASELINE = ROOT / "tools" / "perf_baseline.json"
SLOWER = 1.20                     # more than 20% slower than the baseline fails
NOISE = {"_s": 0.25, "_ms": 25.0}  # ... and by more than this (seconds, milliseconds): the
#                                   GUI's timings move by a few tenths from run to run

# the design's targets (0.8.69 performance section); the albums_in ones are for 10,000 folders
TARGETS = {"first_row_s": 2.0, "first_row_added_s": 2.0, "first_row_during_lookup_s": 2.0,
           "plan_album_median_s": 15.0, "plan_album_p95_s": 45.0, "ui_stall_max_ms": 100.0,
           "preview_2000_open_s": 2.0, "ai_calls_proven": 0}
SCAN_FIRST_S, SCAN_AGAIN_S, SCAN_FOLDERS = 60.0, 10.0, 10_000
AI_HOSTS = ("anthropic", "openai", "ai", "llm", "gemini", "ollama")


def percentile(xs, q):
    xs = sorted(xs)
    if not xs:
        return 0.0
    k = (len(xs) - 1) * q
    lo = int(k)
    hi = min(lo + 1, len(xs) - 1)
    return xs[lo] + (xs[hi] - xs[lo]) * (k - lo)


# ---- the log's stage timings ------------------------------------------------------------------
def log_summary(lines) -> dict:
    """Median and p95 seconds per album and the seconds of each stage, from "timing" lines."""
    import stagetimes
    per, stages = [], {}
    for text in lines:
        if " timing scan " in text:
            continue
        got = stagetimes.parse(text)
        if not got:
            continue
        _folder, total, secs = got
        per.append(total)
        for k, v in secs.items():
            stages[k] = stages.get(k, 0.0) + v
    return {"albums": len(per), "median_s": round(statistics.median(per), 3) if per else 0.0,
            "p95_s": round(percentile(per, 0.95), 3), "total_s": round(sum(per), 1),
            "stages_s": {k: round(v, 1) for k, v in sorted(stages.items(), key=lambda x: -x[1])}}


# ---- the synthetic library ---------------------------------------------------------------------
def make_library(base: Path, singles: int, sets: int, prefix: str = "") -> list:
    """Albums of 3 tracks (10, 20, 30 s) and sets of 2 discs of 3, tagged; returns their
    (artist, title, discs) for the stand-in providers."""
    from mutagen.flac import FLAC
    seeds = base.parent / "seed"
    seeds.mkdir(parents=True, exist_ok=True)
    for i in range(3):
        f = seeds / f"{i}.flac"
        if not f.exists():
            subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i",
                            f"sine=frequency={300 + 50 * i}:duration={10 * (i + 1)}", "-ar", "8000", "-ac", "1",
                            str(f), "-y"], check=True)
    out = []
    for n in range(singles + sets):
        discs = 2 if n >= singles else 1
        artist, title = f"{prefix}Band {n:03d}", f"{prefix}Album {n:03d}"
        top = base / artist / f"2005 - {title}"
        for d in range(1, discs + 1):
            folder = top / f"CD{d}" if discs > 1 else top
            folder.mkdir(parents=True, exist_ok=True)
            for i in range(3):
                f = folder / f"{i + 1:02d}.flac"
                shutil.copyfile(seeds / f"{i}.flac", f)
                t = FLAC(f)
                t.update({"title": f"T{i}", "album": title, "albumartist": artist, "artist": artist,
                          "tracknumber": str(i + 1), "tracktotal": "3"})
                if discs > 1:
                    t.update({"discnumber": str(d), "disctotal": str(discs)})
                t.save()
        out.append((artist, title, discs))
    return out


class StandIn:
    """The providers: MusicBrainz and Discogs answer each album as its one release (no network)."""

    def __init__(self, albums, latency=0.0):
        self.db = None
        self.latency = latency
        self.calls = []
        self.mb, self.dg = {}, {}
        for n, (artist, title, discs) in enumerate(albums):
            rid = f"00000000-0000-4000-8000-{n:012d}"
            self.mb[rid] = {
                "id": rid, "title": title, "date": "2005-03-01", "country": "DE", "status": "Official",
                "barcode": f"{4000000000000 + n}",
                "release-group": {"id": f"rg-{rid}", "primary-type": "Album", "first-release-date": "2005-03-01"},
                "artist-credit": [{"name": artist, "joinphrase": "", "artist": {"id": f"a-{n}", "name": artist,
                                                                                "sort-name": artist}}],
                "label-info": [{"label": {"name": "Label MB"}, "catalog-number": f"CAT-{n}"}],
                "relations": [], "text-representation": {"script": "Latn", "language": "eng"},
                "media": [{"format": "CD", "position": d, "tracks": [
                    {"id": f"{rid}-{d}-t{i}", "position": i + 1, "title": f"T{i}", "length": 10000 * (i + 1),
                     "recording": {"id": f"{rid}-{d}-r{i}", "title": f"T{i}"}} for i in range(3)]}
                    for d in range(1, discs + 1)]}
            did = 900000 + n
            self.dg[did] = {
                "id": did, "title": title, "year": 2005, "released": "2005-03-01",
                "uri": f"https://www.discogs.com/release/{did}", "artists": [{"name": artist, "id": 7000 + n}],
                "labels": [{"name": "Label DG", "catno": f"CAT-{n}"}], "identifiers": [], "country": "Germany",
                "genres": ["Electronic"], "styles": ["Ambient"],
                "formats": [{"name": "CD", "qty": str(discs), "descriptions": ["Album"]}],
                "tracklist": [{"type_": "track", "position": f"{d}-{i + 1}" if discs > 1 else str(i + 1),
                               "title": f"T{i}", "duration": f"0:{10 * (i + 1)}"}
                              for d in range(1, discs + 1) for i in range(3)]}

    def get_json(self, url, host, attempts=None):
        import urllib.parse as up
        import stagetimes
        self.calls.append((host, url))
        with stagetimes.stage(stagetimes.provider_of(host)):           # (as Http._fetch charges one)
            if self.latency:
                time.sleep(self.latency)
            if host == "mb":
                if "/ws/2/release/?" in url or "/ws/2/release?" in url:
                    q = up.unquote(url.split("query=")[1].split("&")[0]) if "query=" in url else ""
                    return {"releases": [{"id": r["id"], "track-count": sum(len(m["tracks"]) for m in r["media"])}
                                         for r in self.mb.values() if r["title"] in q]}
                if "/ws/2/release/" in url:
                    return self.mb.get(url.split("/release/")[1].split("?")[0])
                return {"releases": [], "recordings": [], "artists": [], "release-groups": []}
            if host == "discogs":
                if "/database/search" in url:
                    q = up.unquote(url)
                    return {"results": [{"id": r["id"], "title": f"{r['artists'][0]['name']} - {r['title']}",
                                         "year": "2005"} for r in self.dg.values() if r["title"] in q]}
                tail = url.split("?")[0].rsplit("/", 1)[1]
                return self.dg.get(int(tail)) if tail.isdigit() else None
            return None

    def get_text(self, url, host, *a, **kw):
        self.calls.append((host, url))
        return None

    def ai_calls(self) -> int:
        return sum(1 for host, _u in self.calls if any(h == str(host).lower() for h in AI_HOSTS))


# ---- measuring ---------------------------------------------------------------------------------
def profile_dict(name="Perf"):
    return {"name": name, "unattended": True, "providers": {"web": {"enabled": False}},
            "artwork": {"enabled": False}, "ocr": "off"}


def measure_scan(roots, eng=None) -> dict:
    """albums_in of ROOTS: first time (nothing remembered) and again (the app's remembered tags)."""
    import engine
    import exclusions as ex
    import profiles as pf
    e = eng or engine.Engine(pf.from_dict(profile_dict()), http=StandIn([]))
    store, out = {}, {}
    for name in ("first", "again"):
        start, albums = time.monotonic(), 0
        for r in roots:
            with ex.meta_memo(store, [os.path.abspath(r)]):     # (as the app: a pass forgets only its own folder's)
                albums += len(e.albums_in(r))
        out[name] = round(time.monotonic() - start, 3)
        out["albums"] = albums
    return out


def measure_plan(lib, albums, latency) -> dict:
    """Engine.plan of the library: the per-album seconds from its own "timing" log lines."""
    import engine
    import logsetup
    import profiles as pf
    http = StandIn(albums, latency)
    e = engine.Engine(pf.from_dict(profile_dict()), http=http)
    lines = []

    class Keep(__import__("logging").Handler):
        def emit(self, record):
            lines.append(record.getMessage())
    h = Keep()
    log = logsetup.setup("engine")
    log.addHandler(h)
    try:
        start = time.monotonic()
        plan = e.plan(str(lib))
        took = time.monotonic() - start
    finally:
        log.removeHandler(h)
    s = log_summary(lines)
    statuses = {}
    for a in plan["albums"]:
        statuses[a.get("status")] = statuses.get(a.get("status"), 0) + 1
    return dict(s, plan_s=round(took, 2), statuses=statuses, ai_calls=http.ai_calls(), plan=plan)


class Watchdog:
    """A 10 ms QTimer: the longest gap between two of its ticks is the longest the UI thread was
    busy (minus the interval). phase() names what is being measured and starts afresh."""

    def __init__(self):
        from PySide6.QtCore import QTimer
        self.worst = {}
        self.name = ""
        self.last = time.monotonic()
        self.t = QTimer()
        self.t.setInterval(10)
        self.t.timeout.connect(self.tick)
        self.t.start()

    def phase(self, name):
        self.name, self.last = name, time.monotonic()

    def tick(self):
        now = time.monotonic()
        gap = (now - self.last) * 1000.0 - 10.0
        if gap > self.worst.get(self.name, 0.0):
            self.worst[self.name] = gap
        self.last = now


def run_until(cond, timeout=600.0) -> bool:
    """The event loop, until cond() (checked every 5 ms) or the timeout."""
    from PySide6.QtCore import QEventLoop, QTimer
    if cond():
        return True
    loop = QEventLoop()
    check = QTimer()
    check.setInterval(5)
    check.timeout.connect(lambda: cond() and loop.quit())
    check.start()
    QTimer.singleShot(int(timeout * 1000), loop.quit)
    loop.exec()
    check.stop()
    return bool(cond())


def later(fn):
    """fn on the UI thread from inside the event loop (so the watchdog sees what it costs)."""
    from PySide6.QtCore import QTimer
    QTimer.singleShot(0, fn)


def measure_gui(base, lib, albums, extra, latency) -> dict:
    """The Overview, driven as you would: drop the library on it, then a folder while the list
    shows, then one while it looks up; then a preview of 2,000 albums."""
    from unittest import mock
    from PySide6.QtWidgets import QApplication
    import app_settings
    import gui
    import match_albums as ma
    import profiles as pf
    from gui import manual_match
    app = QApplication.instance() or QApplication([])
    app.setStyleSheet(gui.stylesheet())
    http = StandIn(albums + extra[0][1] + extra[1][1], latency)
    patch = mock.patch.object(ma, "Http", lambda db, cfg, token: http)
    patch.start()
    out = {}
    try:
        app_settings.set_value("add_folder", "look_up")
        pf.save(pf.from_dict(profile_dict()))
        win = gui.Main()
        win.resize(1180, 760)
        win.show()
        ov = win.pages["Overview"]
        ov.refresh_profiles()
        ov.profile.setCurrentText("Perf")
        dog = Watchdog()
        run_until(lambda: False, 0.3)                      # (the window settles)

        def rows_of(folder):
            return [a for a in ov.model.albums if str(a.get("source_dir", "")).startswith(str(folder))]

        def looked(a):
            return a.get("status") not in ("listed", None)

        # 1. the library dropped on an empty Overview
        dog.phase("drop")
        start = time.monotonic()
        later(lambda: manual_match.add_dropped(win, [str(lib)]))
        run_until(lambda: rows_of(lib), 300)
        out["first_row_s"] = round(time.monotonic() - start, 3)
        run_until(lambda: any(looked(a) for a in rows_of(lib)), 600)
        out["first_looked_up_s"] = round(time.monotonic() - start, 3)
        run_until(lambda: not ov.running and all(looked(a) for a in rows_of(lib)), 1800)
        out["all_looked_up_s"] = round(time.monotonic() - start, 3)
        out["rows"] = len(ov.model.albums)

        # 2. a folder dropped while the list shows (nothing running)
        (d2, _a2), (d3, _a3) = extra
        dog.phase("add")
        start = time.monotonic()
        later(lambda: manual_match.add_dropped(win, [str(d2)]))
        run_until(lambda: rows_of(d2), 300)
        out["first_row_added_s"] = round(time.monotonic() - start, 3)
        # 3. ... and one while its look-up runs
        run_until(lambda: bool(ov.running and ov.looking), 120)
        dog.phase("add during look-up")
        start = time.monotonic()
        later(lambda: manual_match.add_dropped(win, [str(d3)]))
        run_until(lambda: rows_of(d3), 600)
        out["first_row_during_lookup_s"] = round(time.monotonic() - start, 3)
        run_until(lambda: not ov.running, 1800)

        # 4. a preview of 2,000 albums opened
        import copy
        done = [a for a in ov.model.albums if looked(a)]
        big = []
        for i in range(2000):
            a = copy.deepcopy(done[i % len(done)])
            tail = f"/copy{i:04d}"
            a["source_dir"] = a["source_dir"] + tail
            a["key"] = str(a.get("key") or "") + tail
            a["album_files"] = [f + tail for f in a.get("album_files") or []]
            for fe in a.get("files") or []:
                if isinstance(fe, dict):
                    for k in ("src", "dst"):
                        if fe.get(k):
                            fe[k] = fe[k] + tail
            big.append(a)
        plan = dict(ov.plan or {}, albums=big)
        dog.phase("preview 2000")
        start, opened = time.monotonic(), []
        later(lambda: (ov.planned(plan), opened.append(time.monotonic())))
        run_until(lambda: bool(opened), 300)
        out["preview_2000_open_s"] = round(opened[0] - start, 3)
        run_until(lambda: False, 0.3)                      # (what it left for the event loop)
        out["ui_stall_ms_by_phase"] = {k: round(v, 1) for k, v in dog.worst.items()}
        out["ui_stall_max_ms"] = round(max(dog.worst.values() or [0.0]), 1)
        dog.t.stop()
        win.close()
    finally:
        patch.stop()
    return out


# ---- judging -----------------------------------------------------------------------------------
def judge(metrics, baseline) -> list:
    """The reasons this run fails: a target missed, or slower than the baseline."""
    bad = []
    for k, target in TARGETS.items():
        v = metrics.get(k)
        if v is not None and v > target:
            bad.append(f"{k} = {v} misses its target {target}")
    for k, v in metrics.get("scan_targets", {}).items():
        if v["value"] > v["target"]:
            bad.append(f"{k} = {v['value']} s misses its target {v['target']} s ({v['folders']} folders)")
    for k, was in (baseline or {}).items():
        v = metrics.get(k)
        if not isinstance(v, (int, float)) or not isinstance(was, (int, float)):
            continue
        noise = next((n for suffix, n in NOISE.items() if k.endswith(suffix)), 0)
        if v > was * SLOWER and v - was > noise:
            bad.append(f"{k} = {v}: more than 20% slower than the baseline {was}")
    return bad


def summary(metrics, baseline) -> str:
    rows = [("measure", "target", "baseline", "now")]
    for k in list(TARGETS) + ["first_looked_up_s", "all_looked_up_s", "albums_in_cold_s", "albums_in_warm_s",
                              "plan_s"]:
        if k in metrics:
            rows.append((k, str(TARGETS.get(k, "")), str((baseline or {}).get(k, "")), str(metrics[k])))
    for k, v in metrics.get("scan_targets", {}).items():
        rows.append((k, f"{v['target']}", "", f"{v['value']}"))
    w = [max(len(r[i]) for r in rows) for i in range(4)]
    text = "\n".join("  ".join(c.ljust(w[i]) for i, c in enumerate(r)) for r in rows)
    st = metrics.get("plan_stages_s")
    if st:
        text += "\nplan time by stage (s, all albums): " + ", ".join(f"{k} {v}" for k, v in st.items())
    if metrics.get("ui_stall_ms_by_phase"):
        text += "\nUI stalls (ms) by phase: " + ", ".join(f"{k} {v}" for k, v in metrics["ui_stall_ms_by_phase"].items())
    return text


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--out", default="", help="write the result here (JSON)")
    ap.add_argument("--baseline", default=str(BASELINE))
    ap.add_argument("--update-baseline", action="store_true", help="store this run's numbers as the baseline")
    ap.add_argument("--singles", type=int, default=90)
    ap.add_argument("--sets", type=int, default=10)
    ap.add_argument("--latency", type=float, default=0.0, help="seconds each stand-in provider answer takes")
    ap.add_argument("--no-gui", action="store_true")
    ap.add_argument("--keep", action="store_true", help="keep the temporary folder")
    ap.add_argument("--scan", action="append", default=[], help="also time albums_in over this real folder")
    ap.add_argument("--from-log", default="", help="only summarise the timing lines of this log")
    args = ap.parse_args(argv)
    sys.path.insert(0, str(ROOT))
    if args.from_log:
        with open(args.from_log, encoding="utf-8", errors="replace") as fh:
            print(json.dumps(log_summary(fh), indent=1))
        return 0

    tmp = Path(tempfile.mkdtemp(prefix="tm-perf-"))
    # isolated: this machine's settings, profiles, caches and logs are never read or written
    os.environ["XDG_CONFIG_HOME"] = str(tmp / "config")
    os.environ["XDG_STATE_HOME"] = str(tmp / "state")
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    os.environ["DISCOGS_TOKEN"] = "perf-check"
    os.environ["TAGMENDER_TAGGER_INI"] = str(tmp / "no-tagger.ini")
    os.environ.setdefault("TAGMENDER_DISCOGS_PLUGIN", str(ROOT / "plugins_builtin" / "discogs"))
    os.environ.pop("TAGMENDER_PROFILE", None)
    try:
        lib = tmp / "lib"
        albums = make_library(lib, args.singles, args.sets)
        extra = []
        for name in ("added", "during"):
            d = tmp / name
            extra.append((d, make_library(d, 9, 1, prefix=f"{name} ")))
        import version
        metrics = {"version": version.VERSION, "albums": len(albums)}
        sc = measure_scan([str(lib)])
        metrics["albums_in_cold_s"], metrics["albums_in_warm_s"] = sc["first"], sc["again"]
        folders = sum(1 for _p, _d, files in os.walk(lib) if any(f.endswith(".flac") for f in files))
        targets = {"albums_in_cold_s": (sc["first"], SCAN_FIRST_S), "albums_in_warm_s": (sc["again"], SCAN_AGAIN_S)}
        if args.scan:
            real = measure_scan(args.scan)
            n = sum(1 for r in args.scan for _p, _d, fs in os.walk(r) if fs)
            metrics["scan_real"] = dict(real, folders=n)
            targets.update({"scan_real_first_s": (real["first"], SCAN_FIRST_S),
                            "scan_real_again_s": (real["again"], SCAN_AGAIN_S)})
        metrics["scan_targets"] = {}
        for k, (v, per10k) in targets.items():
            nf = metrics["scan_real"]["folders"] if k.startswith("scan_real") else folders
            metrics["scan_targets"][k] = {"value": v, "target": round(per10k * nf / SCAN_FOLDERS, 3), "folders": nf}
        p = measure_plan(lib, albums, args.latency)
        metrics.update(plan_album_median_s=p["median_s"], plan_album_p95_s=p["p95_s"], plan_s=p["plan_s"],
                       plan_stages_s=p["stages_s"], plan_statuses=p["statuses"], ai_calls_proven=p["ai_calls"])
        if not args.no_gui:
            metrics.update(measure_gui(tmp, lib, albums, extra, args.latency))
        base = {}
        if Path(args.baseline).exists() and not args.update_baseline:
            base = json.loads(Path(args.baseline).read_text()).get("metrics") or {}
        bad = judge(metrics, base)
        print(summary(metrics, base))
        if bad:
            print("(a GUI timing near its limit can move by a few tenths: run it once more before acting on it)")
        if args.out:
            Path(args.out).write_text(json.dumps(dict(metrics, failures=bad), indent=1))
        if args.update_baseline:
            keep = {k: v for k, v in metrics.items() if isinstance(v, (int, float)) and k not in ("albums", "rows")
                    and not isinstance(v, bool) and k != "version"}
            Path(args.baseline).write_text(json.dumps({"version": metrics["version"], "metrics": keep},
                                                      indent=1, sort_keys=True) + "\n")
            print(f"baseline stored: {args.baseline}")
        for b in bad:
            print("FAIL:", b)
        print("perf check:", "FAILED" if bad else "passed")
        return 1 if bad else 0
    finally:
        if not args.keep:
            shutil.rmtree(tmp, ignore_errors=True)
        else:
            print("kept:", tmp)


if __name__ == "__main__":
    sys.exit(main())
