#!/usr/bin/env python3
"""Runs every tests/test_*.py (run_tests.sh calls it; the environment is set up there).

Each file runs in its own process with its own XDG folders, several at once (--jobs), and is
stopped when it runs longer than --timeout seconds; its line says how long it took. The files
in SERIAL run one at a time after the others (they use something only one may use at once).
--shard I/N runs the I-th of N parts (CI runs the parts on several machines at once).
No test reaches the network: tests/_offline/sitecustomize.py refuses any connection that
leaves this computer, so a test that would wait on a server fails at once and says so.
Output: one line per file in file order, the failures' details, then "<n> tests in all";
exit 1 when a file failed or was stopped."""
import argparse
import json
import math
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# (each runs alone: mounts, a fixed local port or folder, timing-sensitive watcher passes)
SERIAL = {
}
ARGS = {"tests/test_match.py": ["TAGMENDER_DISCOGS_PLUGIN"], "tests/test_coverup_batch.py": ["TAGMENDER_COVERUP_DIR"]}
# Long files are split: their tests shared out over several processes side by side (a file that
# took over SPLIT_OVER seconds, in parts of about PART_SECONDS), and the longest work starts
# first. The times come from the last full run on this computer (TIMINGS_CACHE), else from the
# times kept in the repository (TIMINGS, for CI; refresh it with --save-timings).
TIMINGS = ROOT / "tests" / "timings.json"
TIMINGS_CACHE = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / "tagmender-tests" / "timings.json"
SPLIT_OVER, PART_SECONDS, MAX_PARTS = 40, 25, 10


def load_timings():
    for f in (TIMINGS_CACHE, TIMINGS):
        try:
            return {k: float(v) for k, v in json.loads(f.read_text()).items()}
        except (OSError, ValueError, AttributeError):
            continue
    return {}


def parts_for(t, timings):
    secs = timings.get(t, 0)
    if secs <= SPLIT_OVER:
        return 1
    return min(MAX_PARTS, math.ceil(secs / PART_SECONDS))


def test_ids(t, py, env):
    """The test IDs of file T (module.Class.method), for SPLIT."""
    code = ("import sys, unittest\n"
            "def walk(s):\n"
            "    for x in s:\n"
            "        yield from (walk(x) if isinstance(x, unittest.TestSuite) else [x.id()])\n"
            f"print('\\n'.join(walk(unittest.defaultTestLoader.loadTestsFromName({Path(t).stem!r}))))")
    out = subprocess.run([py, "-W", "ignore", "-c", code], cwd=ROOT, env=env, stdout=subprocess.PIPE,
                         stderr=subprocess.DEVNULL, timeout=120)
    ids = [x for x in out.stdout.decode().split() if x.startswith(Path(t).stem + ".")]
    return ids if out.returncode == 0 else []


def fresh_env(scratch):
    env = dict(os.environ)
    for k in ("XDG_CONFIG_HOME", "XDG_STATE_HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME"):
        env[k] = tempfile.mkdtemp(dir=scratch)
    env["PYTHONPATH"] = os.pathsep.join([str(ROOT / "tests")] + [x for x in [env.get("PYTHONPATH")] if x])
    return env


def run_one(t, scratch, py, timeout, ids=None):
    """Run file T (or only its tests IDS, a SPLIT part) in a process of its own."""
    env = fresh_env(scratch)
    args = [env[k] for k in ARGS.get(t, ())]
    cmd = [py, "-W", "ignore", "-m", "unittest", *ids] if ids else [py, "-W", "ignore", t, *args]
    start = time.monotonic()
    p = subprocess.Popen(cmd, cwd=ROOT, env=env, stdout=subprocess.PIPE,
                         stderr=subprocess.STDOUT, start_new_session=True)
    try:
        out, _ = p.communicate(timeout=timeout)
        rc = p.returncode
    except subprocess.TimeoutExpired:
        os.killpg(p.pid, signal.SIGKILL)               # (the file and anything it started)
        out, _ = p.communicate()
        rc = "timeout"
    return t, rc, out.decode("utf-8", "replace"), time.monotonic() - start, env


def details(t, rc, out, env, py):
    """The failure lines run_tests.sh always printed: what failed and why; for a file that
    crashed or was stopped, how it ended and, once more test by test, the last test it ran."""
    lines = out.splitlines()
    shown = [x for x in lines if re.match(r"^(FAIL|ERROR):", x)][:10]
    shown += ["    " + x for x in lines if re.match(r"^[A-Za-z_.]*(Error|Exception)\b", x)][:10]
    if rc == "timeout":
        shown.append("    stopped: ran longer than the time limit (--timeout); its last output:")
    if not any(re.match(r"^(FAIL|ERROR):", x) for x in lines):
        shown += ["    " + x for x in lines[-15:]]
        if rc != "timeout":
            shown.append("    -- again, naming each test:")
            again = subprocess.run([py, "-W", "ignore", "-m", "unittest", "-v", t], cwd=ROOT, env=env,
                                   stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=900)
            keep = [x for x in again.stdout.decode("utf-8", "replace").splitlines()
                    if re.search(r" \.\.\. |^(FAIL|ERROR|Traceback|  File|[A-Za-z]+Error)", x)]
            shown += ["    " + x for x in keep[-25:]]
    return shown


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--jobs", type=int, default=min(os.cpu_count() or 2, 48))
    ap.add_argument("--timeout", type=int, default=300)
    ap.add_argument("--shard", default="1/1")
    ap.add_argument("--save-timings", action="store_true", help="also keep this run's times in tests/timings.json")
    ap.add_argument("files", nargs="*")
    a = ap.parse_args()
    py = os.environ.get("PYTHON") or sys.executable
    files = a.files or sorted(str(p.relative_to(ROOT)) for p in (ROOT / "tests").glob("test_*.py"))
    i, n = (int(x) for x in a.shard.split("/"))
    if n > 1:
        # (each part gets about the same work by the kept times, longest first onto the lightest
        # part; every part works this out alike, so each file is in exactly one)
        known = load_timings()
        load, mine = [0.0] * n, []
        for f in sorted(files, key=lambda f: (-known.get(f, PART_SECONDS), f)):
            k = load.index(min(load))
            load[k] += known.get(f, PART_SECONDS)
            if k == i - 1:
                mine.append(f)
        files = sorted(mine)
    scratch = tempfile.mkdtemp(prefix="tagmender-tests.")
    results = {}
    try:
        timings = load_timings()
        tasks = []                                     # (estimated seconds, file, test IDs or None)
        for t in files:
            if t in SERIAL:
                continue
            k = parts_for(t, timings)
            ids = test_ids(t, py, fresh_env(scratch)) if k > 1 else []
            if len(ids) > 1:
                k = min(k, len(ids))
                tasks += [(timings[t] / k, t, ids[j::k]) for j in range(k)]
            else:
                tasks.append((timings.get(t, PART_SECONDS), t, None))
        tasks.sort(key=lambda x: -x[0])                # (the longest first: nothing long starts last)
        parts, work = {}, {}
        with ThreadPoolExecutor(max(1, a.jobs)) as pool:
            for r in pool.map(lambda x: run_one(x[1], scratch, py, a.timeout, x[2]), tasks):
                parts.setdefault(r[0], []).append(r)
        for t, rs in parts.items():                    # (a split file: its parts as one)
            rcs = [r[1] for r in rs]
            rc = "timeout" if "timeout" in rcs else next((x for x in rcs if x != 0), 0)
            out = "\n".join(r[2] for r in rs)
            if len(rs) > 1:
                ran = sum(int((re.findall(r"^Ran (\d+)", r[2], re.M) or ["0"])[-1]) for r in rs)
                out += f"\nRan {ran} tests in {len(rs)} parts\n" + ("OK" if rc == 0 else "FAILED")
            results[t] = (t, rc, out, max(r[3] for r in rs), rs[0][4])
            work[t] = sum(r[3] for r in rs)            # (the whole file's time, run alone)
        for t in files:
            if t in SERIAL:
                results[t] = run_one(t, scratch, py, a.timeout)
        total, failed = 0, False
        for t in files:
            _t, rc, out, secs, env = results[t]
            ran = re.findall(r"^Ran (\d+)", out, re.M)
            total += int(ran[-1]) if ran else 0
            last = (out.rstrip().splitlines() or [""])[-1]
            if rc == "timeout":
                last = f"STOPPED after {secs:.0f}s (time limit)"
            print(f"{t:<34} {ran[-1] if ran else 0:>4} tests  {last}  [{secs:.0f}s]")
            if rc != 0:
                failed = True
                for x in details(t, rc, out, env, py):
                    print(x)
        slow = sorted(((results[t][3], t) for t in files), reverse=True)[:5]
        print("slowest: " + ", ".join(f"{Path(t).name} {s:.0f}s" for s, t in slow))
        print(f"{total} tests in all")
        if not a.files and a.shard == "1/1":           # (a full run: its times for the next)
            keep = dict(timings, **{t: round(v, 1) for t, v in work.items()})
            for f in ([TIMINGS_CACHE] + ([TIMINGS] if a.save_timings else [])):
                try:
                    f.parent.mkdir(parents=True, exist_ok=True)
                    f.write_text(json.dumps(dict(sorted(keep.items())), indent=0) + "\n")
                except OSError:
                    pass
        return 1 if failed else 0
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
