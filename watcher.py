"""watcher.py - keeps watched folders processed, unattended, without a desktop session.

Folders come from the profiles' sources with "watch": true (the deepest profile wins
where roots overlap). Every interval_seconds the folders are scanned - polling, so it
works the same on local disks, NFS/SMB shares and removable drives, where change
notifications are unreliable; each scan is also the periodic reconciliation.

An album is processed when:
  - nothing in it changed for settle_minutes (copies and downloads have finished);
  - it holds no partial download (.part, .crdownload, .tmp, .!qB, ~ files, TagMender's
    own staging files);
  - its files differ from what TagMender last left there: after a job, the album's
    signature is stored, so TagMender's own writes never queue it again;
  - it is not remembered as Ignored (until its files or the rules change);
  - an unresolved album is retried after retry.after_days, at most retry.max_attempts
    times; a provider outage or throttling is retried sooner, with growing pauses.

An interrupted job found at start is recovered first. Profiles that are not
`unattended` only plan: the plans wait in ~/.local/state/tagmender/plans/ for
`tagmender apply`.
"""
from __future__ import annotations

import os
import sqlite3
import time
from pathlib import Path

import engine as eng_mod
import exclusions as ex
import executor
import logsetup
import plan as planmod
import profiles as pf

PARTIAL = (".part", ".crdownload", ".tmp", ".!qb", ".partial", ".download", ".opdownload")
PROVIDER_RETRY = [600, 1800, 3600, 4 * 3600, 12 * 3600, 24 * 3600]


def _db():
    ex.STATE_DIR.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(ex.STATE_DIR / "state.db", timeout=60)
    db.execute("CREATE TABLE IF NOT EXISTS processed (folder TEXT PRIMARY KEY, profile TEXT, signature TEXT,"
               " status TEXT, attempts INTEGER, next_try REAL, ts REAL, detail TEXT)")
    return db


def snapshot(folder: Path):
    """(signature, newest mtime, has a partial file)."""
    entries, newest, partial = [], 0.0, False
    for p in sorted(Path(folder).rglob("*")):
        if not p.is_file():
            continue
        name = p.name.lower()
        if name.endswith(PARTIAL) or name.startswith("~") or executor.STAGE_TAG in name:
            partial = True
            continue
        try:
            st = p.stat()
        except FileNotFoundError:
            continue
        entries.append(p)
        newest = max(newest, st.st_mtime)
    return ex.signature(entries), newest, partial


class Watcher:
    def __init__(self, profiles=None, once=False, log=None):
        self.profiles = profiles if profiles is not None else self._watched_profiles()
        self.once = once
        self.log = log or logsetup.setup("watch")
        self.stop = False

    @staticmethod
    def _watched_profiles():
        out = []
        for n in pf.names():
            try:
                p = pf.load(n)
            except pf.ProfileError:
                continue
            if any(s.get("watch") for s in p["sources"]):
                out.append(p)
        return out

    def roots(self):
        """(root, profile), deepest first, so overlapping roots go to the most specific profile."""
        pairs = [(Path(s["path"]).expanduser(), p) for p in self.profiles for s in p["sources"] if s.get("watch")]
        return sorted(pairs, key=lambda rp: -len(str(rp[0])))

    def due(self, folder: Path, prof, db) -> tuple[bool, str]:
        sig, newest, partial = snapshot(folder)
        if partial:
            return False, "partial download inside"
        if time.time() - newest < prof["watch"]["settle_minutes"] * 60:
            return False, "still changing"
        row = db.execute("SELECT signature, status, attempts, next_try FROM processed WHERE folder=?",
                         (str(folder),)).fetchone()
        rules = ex.load_rules(prof.name)
        if ex.State().still_ignored(folder, ex.signature(ex.audio_files(folder)), ex.rules_hash(rules)):
            return False, "ignored (unchanged)"
        if not row:
            return True, "new"
        signature, status, attempts, next_try = row
        if signature != sig:
            return True, "changed"
        if status in ("unresolved", "provider_error") and (attempts or 0) < prof["retry"]["max_attempts"] \
                and time.time() >= (next_try or 0):
            return True, "retry"
        return False, status

    def record(self, db, folder, prof, status, attempts, detail=""):
        sig, _n, _p = snapshot(folder) if Path(folder).exists() else ("gone", 0, False)
        if status == "provider_error":
            nxt = time.time() + PROVIDER_RETRY[min(attempts, len(PROVIDER_RETRY) - 1)]
        elif status == "unresolved":
            nxt = time.time() + prof["retry"]["after_days"] * 86400
        else:
            nxt = 0
        db.execute("INSERT OR REPLACE INTO processed VALUES (?,?,?,?,?,?,?,?)",
                   (str(folder), prof.name, sig, status, attempts, nxt, time.time(), detail))
        db.commit()

    def tick(self):
        """One pass over every watched root. Returns {folder: what happened}."""
        done = {}
        db = _db()
        for job in executor.pending_jobs():
            self.log.warning("recovering interrupted job %s", job)
            executor.recover(job)
        seen = set()
        for root, prof in self.roots():
            if not root.is_dir():
                self.log.warning("watched folder %s is not available (unmounted?)", root)
                continue
            e = eng_mod.Engine(prof, log=self.log)
            for g in e.albums_in(root):
                if self.stop:
                    break
                key = (str(g.folder), tuple(sorted(map(str, g.files))))
                if key in seen:
                    continue
                seen.add(key)
                go, why = self.due(g.folder, prof, db)
                if not go:
                    continue
                prev = db.execute("SELECT attempts FROM processed WHERE folder=?", (str(g.folder),)).fetchone()
                attempts = (prev[0] or 0) + 1 if prev and why == "retry" else 1
                try:
                    prop = e.identify(g)
                    plan = planmod.build([prop], prof, e.job + f"-{len(done)}", extra_roots=[root])
                    a = plan["albums"][0]
                    if prof["unattended"]:
                        res = executor.Executor(plan, log=self.log).run()
                        outcome = res["albums"][0]["status"] if res["albums"] else a["status"]
                    else:
                        pdir = ex.STATE_DIR / "plans"
                        pdir.mkdir(parents=True, exist_ok=True)
                        planmod.save(plan, pdir / f"{plan['job']}.json")
                        outcome = "planned"
                    errs = prop.provider_errors or {}
                    retryable = prop.status == "unresolved" and any(
                        v in ("outage", "rate_limited", "invalid_response") for v in errs.values())
                    status = ("provider_error" if retryable else
                              "unresolved" if prop.status == "unresolved" else
                              "excluded" if a["status"] == "excluded" else outcome)
                    self.record(db, g.folder, prof, status, attempts, a.get("label", ""))
                    done[str(g.folder)] = status
                    self.log.info("%s: %s (%s)", g.folder, status, why)
                except Exception as exc:                       # one album never stops the watcher
                    self.log.exception("watch: %s failed", g.folder)
                    self.record(db, g.folder, prof, "provider_error", attempts, str(exc)[:200])
                    done[str(g.folder)] = "error"
        db.close()
        return done

    def run(self):
        interval = min((p["watch"]["interval_seconds"] for p in self.profiles), default=300)
        if not self.profiles:
            self.log.warning("no profile has a watched source (sources: [{\"path\": ..., \"watch\": true}])")
            return
        while not self.stop:
            self.tick()
            if self.once:
                return
            for _ in range(int(interval)):
                if self.stop:
                    return
                time.sleep(1)


def lower_priority():
    """Be a polite background process: lowest CPU priority, idle I/O class when available."""
    try:
        os.nice(10)
    except OSError:
        pass
    try:
        import subprocess
        subprocess.run(["ionice", "-c3", "-p", str(os.getpid())], capture_output=True, timeout=5)
    except Exception:
        pass
