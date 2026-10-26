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
`unattended` only plan: the plans wait in ~/.local/state/tagmender/plans/ for approval
(`tagmender watch --approve`, or Apply on the app's Watched folders page).
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
import re as _re
# rsync's temporary file: ".01 Track.flac.Xy12Ab" next to where 01 Track.flac will be
_RSYNC_TMP = _re.compile(r"^\..+\.[A-Za-z0-9]{1,5}\.[A-Za-z0-9]{6}$")


def _partial(name: str) -> bool:
    n = name.lower()
    return n.endswith(PARTIAL) or n.startswith(("~$", ".~lock.")) or bool(_RSYNC_TMP.match(name))


def _changed_at(st) -> float:
    return st.st_mtime
PROVIDER_RETRY = [600, 1800, 3600, 4 * 3600, 12 * 3600, 24 * 3600]
# outcomes tried again later: a provider outage, an unresolved album, and an album that
# failed or was blocked for what may be a passing reason (a share dropping, a locked file)
RETRIED = ("unresolved", "provider_error", "failed", "conflict", "blocked")


def _db():
    ex.STATE_DIR.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(ex.STATE_DIR / "state.db", timeout=60)
    db.execute("CREATE TABLE IF NOT EXISTS processed (folder TEXT PRIMARY KEY, profile TEXT, signature TEXT,"
               " status TEXT, attempts INTEGER, next_try REAL, ts REAL, detail TEXT)")
    return db


def _files_key(folder, files) -> str:
    import hashlib
    h = hashlib.sha1("\n".join(sorted(map(str, files))).encode("utf-8", "surrogateescape")).hexdigest()[:12]
    return planmod.db_text(f"{folder}#{h}")


def album_key(folder: Path, files) -> str:
    """Watcher.key for an album known by its folder and files (as undo knows it): the folder,
    or the folder and its files when the folder holds other music too."""
    mine = {str(f) for f in files}
    from engine import is_disc_dir
    others = []
    try:                                  # (the folder's own music: in it and in its disc folders)
        for p in Path(folder).iterdir():
            kids = [p] if p.is_file() else ([x for x in p.rglob("*") if x.is_file()] if is_disc_dir(p) else [])
            others += [x for x in kids if x.suffix.lower() in ex.AUDIO_EXT and not x.name.startswith(".")
                       and str(x) not in mine]
    except OSError:
        others = []
    return _files_key(folder, files) if others else planmod.db_text(folder)


def shape(folder: Path, files=None, **how) -> str:
    """Which audio files there are, their sizes and their tags (not their times): what undo
    leaves is compared with this - a track added, removed or retagged changes it (tags
    rewritten inside their padding keep the size), a touched time not."""
    import hashlib
    import json
    import tagio
    h = hashlib.sha1()
    paths = sorted(Path(x) for x in files) if files is not None else sorted(Path(folder).rglob("*"))
    for p in paths:
        if p.suffix.lower() in ex.AUDIO_EXT and executor.STAGE_TAG not in p.name:
            try:
                tags = tagio.read(p, **how)
            except Exception:
                tags = {}
            try:
                h.update(f"{p}|{p.stat().st_size}|".encode("utf-8", "surrogateescape"))
            except OSError:
                continue
            h.update(json.dumps(sorted((k, [str(x) for x in v] if isinstance(v, list) else str(v))
                                       for k, v in tags.items()), ensure_ascii=True).encode() + b"\n")
    return "shape:" + h.hexdigest()[:16]


def shape_in(folder: Path, files, signature) -> bool:
    """The folder's shape is one recorded (as this version reads tags, or as earlier versions
    did: an M4A's track 0, a user tag spelled like a field merged into it)."""
    sigs = set((signature or "").split("|"))
    return any(shape(folder, files, **how) in sigs for how in
               ({"mp4_zero": z, "split_custom": sc} if (sc, z) != (True, True) else {}
                for sc in (True, "deferred", False) for z in (True, False)))


def snapshot(folder: Path, files=None, deep=True):
    """(signature, newest mtime, has a partial file) of a folder, or only of `files` (an album
    is judged by its own files: loose tracks at a watched root not by everything below the
    root, one album of a mixed folder not by the other). A download in progress is looked
    for in the folder - below it too when deep (an album folder; not a watched root)."""
    entries, newest, partial = [], 0.0, False
    if files is not None:                # (anything new in the folder is "still changing" too)
        if deep:                          # (an album folder; a watched root's time is everyone's)
            try:
                newest = os.stat(folder).st_mtime
            except OSError:
                pass
        try:
            around = Path(folder).rglob("*") if deep else Path(folder).iterdir()
            for p in around:
                n = p.name.lower()
                if executor.STAGE_TAG in n:
                    continue
                if _partial(p.name):
                    partial = True
                    break
                try:
                    # a file, or a folder: a copy that keeps the files' own times (cp -a,
                    # rsync -a, file managers) still changes the folder's time as files arrive
                    if deep and (p.is_file() or p.is_dir()):
                        newest = max(newest, _changed_at(p.stat()))
                    elif not deep and p.suffix.lower() in ex.AUDIO_EXT and p.is_file():
                        # (loose tracks at a watched root: one still being copied - its tags not
                        # readable yet, grouped on its own - holds the others back too)
                        newest = max(newest, _changed_at(p.stat()))
                except OSError:
                    pass
        except OSError:
            pass
    for p in sorted(Path(x) for x in files) if files is not None else sorted(Path(folder).rglob("*")):
        if not p.is_file():
            continue
        name = p.name.lower()
        if executor.STAGE_TAG in name:                     # TagMender's own staging files
            continue
        if _partial(p.name):
            partial = True
            continue
        try:
            st = p.stat()
        except FileNotFoundError:
            continue
        entries.append(p)
        newest = max(newest, _changed_at(st))
    return ex.signature(entries), newest, partial


class Watcher:
    def __init__(self, profiles=None, once=False, log=None):
        self.fixed = profiles is not None        # else the profiles are read again on every pass
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

    @staticmethod
    def key(g) -> str:
        """What the watcher remembers an album by: its folder, and in a folder holding
        several albums, which files are this album's."""
        if not g.others:
            return planmod.db_text(g.folder)
        return _files_key(g.folder, g.files)

    @staticmethod
    def destination_ready(prof) -> bool:
        """The folders a copy or move profile files albums into are there (mounted)."""
        roots = []
        if prof["operation"] in ("copy", "move"):
            roots.append(prof["destination"])
        if (prof.get("unresolved") or {}).get("action") == "move":
            roots.append(prof["unresolved"].get("move_to"))
        return all(r and Path(r).expanduser().is_dir() and planmod.mounted(Path(r).expanduser()) for r in roots)

    def due(self, folder: Path, prof, db, key=None, files=None, members=None, deep=True) -> tuple[bool, str]:
        key = key or planmod.db_text(folder)
        sig, newest, partial = snapshot(folder, files, deep)
        if partial:
            return False, "partial download inside"
        if time.time() - newest < prof["watch"]["settle_minutes"] * 60:
            return False, "still changing"
        row = db.execute("SELECT signature, status, attempts, next_try, ts FROM processed WHERE folder=?",
                         (key,)).fetchone()
        rules = ex.load_rules(prof.name)
        st = ex.State()
        # (only an ignored folder is signed: a watched root's loose track never walks the whole root)
        if st.ignored_item(folder) and \
                st.still_ignored(folder, ex.signature(ex.audio_files(folder)), ex.rules_hash(rules)):
            return False, "ignored (unchanged)"
        if not row or row[1] != "undone":
            # (an album undone while it was the folder's only music is remembered by the folder,
            # one undone beside other music by the folder and its files: music dropped beside it
            # since, or taken away, changes which - the undo, if it is newer than this key's own
            # record, still holds)
            alts = {planmod.db_text(folder)} | ({_files_key(folder, files)} if files else set())
            for alt in sorted(alts - {key}):
                other = db.execute("SELECT signature, status, ts FROM processed WHERE folder=?", (alt,)).fetchone()
                if other and other[1] == "undone" and (not row or (other[2] or 0) >= (row[4] or 0)) and \
                        shape_in(folder, files, other[0]):
                    return False, "undone"
        if not row:
            return True, "new"
        signature, status, attempts, next_try, ts = row
        if status == "undone":                       # a job undone by hand is not done again by itself
            return (True, "changed since the undo") if not shape_in(folder, files, signature) \
                else (False, "undone")
        if signature != sig:
            return True, "changed"
        if status == "excluded":                     # looked at again when the rules or an override change
            detail = db.execute("SELECT detail FROM processed WHERE folder=?", (key,)).fetchone()[0]
            if detail != ex.rules_hash(rules):
                return True, "rules changed"
            ovr = ex.State().overrides()
            if any(ex._overridden(x, ovr) for x in [folder, *(members or files or [])]):
                return True, "process anyway"          # the folder, or one of its loose tracks
            return False, "excluded (unchanged)"
        if status == "waiting_destination":          # planned while the stick/share was out
            return (True, "destination back") if self.destination_ready(prof) else (False, status)
        if status in RETRIED and (attempts or 0) < prof["retry"]["max_attempts"] \
                and time.time() >= (next_try or 0):
            return True, "retry"
        return False, status

    def record(self, db, folder, prof, status, attempts, detail="", key=None, files=None, deep=True):
        sig, _n, _p = snapshot(folder, files, deep) if Path(folder).exists() else ("gone", 0, False)
        if status in ("provider_error", "failed", "conflict", "blocked"):
            nxt = time.time() + PROVIDER_RETRY[min(attempts, len(PROVIDER_RETRY) - 1)]
        elif status == "unresolved":
            nxt = time.time() + prof["retry"]["after_days"] * 86400
        else:
            nxt = 0
        db.execute("INSERT OR REPLACE INTO processed VALUES (?,?,?,?,?,?,?,?)",
                   (key or planmod.db_text(folder), prof.name, sig, status, attempts, nxt, time.time(),
                    planmod.db_text(detail)))
        db.commit()

    def filed(self, db, entry, prof, status):
        """Remember an album where a job filed it, as TagMender left it: a library inside the
        watched folder (or a watched library the album was moved within) does not take what
        was just filed for a new drop and identify it again."""
        if status not in ("done", "unchanged"):
            return
        dst = [Path(fe["dst"]) for fe in entry.get("files") or []
               if Path(fe["dst"]).suffix.lower() in ex.AUDIO_EXT]
        if not dst or {str(p) for p in dst} == {fe["src"] for fe in entry["files"]}:
            return                                     # tagged where it was: its own record covers it
        try:
            folder = Path(os.path.commonpath([str(p.parent) for p in dst]))
        except ValueError:
            return
        from engine import is_disc_dir
        if is_disc_dir(folder):
            folder = folder.parent
        if not all(p.exists() for p in dst):
            return
        self.record(db, folder, prof, status, 1, entry.get("label", ""), key=album_key(folder, dst), files=dst)

    def tick(self):
        """One pass over every watched root. Returns {folder: what happened}. One pass at a
        time across processes: the app watching while the background service runs too only
        lets one of them look (the other's pass is skipped, not queued)."""
        with pass_lock(blocking=False) as held:
            if not held:
                self.skipped = "another TagMender process is checking the watched folders"
                self.log.info("watch: %s; this pass is skipped", self.skipped)
                return {}
            self.skipped = ""
            return self._tick()

    @staticmethod
    def library_inside(root: Path, prof) -> list:
        """The profile's library (and unresolved folder) where it lies inside the watched folder:
        what is already there - filed by TagMender or not - is never taken for a new drop."""
        out = []
        for d in ([prof["destination"]] if prof["operation"] in ("copy", "move") else []) + \
                ([prof["unresolved"].get("move_to")] if (prof.get("unresolved") or {}).get("action") == "move"
                 else []):
            if not d:
                continue
            d = Path(os.path.realpath(os.path.expanduser(d)))
            r = Path(os.path.realpath(root))
            if d != r and r in d.parents:
                out.append(d)
        return out

    def _tick(self):
        done = {}
        db = _db()
        busy = executor.busy_jobs()
        if busy:                                          # another process is changing files now
            self.log.info("job %s is running in another process; this pass waits", busy[0])
            db.close()
            return done
        for job in executor.pending_jobs():
            try:
                executor.recover(job)
                self.log.warning("recovered interrupted job %s", job)
            except executor.StepError as exc:            # started meanwhile in another process
                self.log.info("job %s: %s", job, exc)
                db.close()
                return done
        seen = set()
        self.stop_pass = False
        for root, prof in self.roots():
            if self.stop or self.stop_pass:
                break
            if not root.is_dir():
                self.log.warning("watched folder %s is not available (unmounted?)", root)
                continue
            e = eng_mod.Engine(prof, log=self.log)
            libs = self.library_inside(root, prof)
            for g in (e.albums_in(root, skip=libs) if libs else e.albums_in(root)):
                if self.stop:
                    break
                real = Path(os.path.realpath(g.folder))
                if any(real == lb or lb in real.parents for lb in libs):
                    continue                               # already in the library
                key = (str(g.folder), tuple(sorted(map(str, g.files))))
                if key in seen:
                    continue
                seen.add(key)
                k = self.key(g)
                deep = os.path.realpath(g.folder) != os.path.realpath(root)
                scope = g.files                            # the album's own files (see snapshot)
                try:
                    go, why = self.due(g.folder, prof, db, k, scope, g.files, deep)
                except Exception:                              # one album never stops the watcher
                    self.log.exception("watch: %s could not be looked at", g.folder)
                    continue
                if not go:
                    continue
                if executor.job_running():                 # a plan being applied, an undo: it may be this album
                    self.log.info("watch: a job is changing files; the rest of this pass waits")
                    self.stop_pass = True
                    break
                prev = db.execute("SELECT attempts FROM processed WHERE folder=?", (k,)).fetchone()
                attempts = (prev[0] or 0) + 1 if prev and why == "retry" else 1
                try:
                    prop = e.identify(g)
                    for step in ("add_artwork", "apply_level"):   # as `tagmender run` does
                        if hasattr(e, step):
                            getattr(e, step)(prop)
                    n_jobs = getattr(self, "_n", 0) + 1
                    self._n = n_jobs
                    plan = planmod.build([prop], prof, e.job + f"-{n_jobs}", extra_roots=[root])
                    a = plan["albums"][0]
                    if prof["unattended"]:
                        res = executor.Executor(plan, log=self.log).run()
                        outcome = res["albums"][0]["status"] if res["albums"] else a["status"]
                    elif a["status"] == "blocked":                # nothing to apply: planned again later
                        outcome = "blocked"
                    elif not planmod.has_work(a):                 # nothing to change: no plan to keep
                        outcome = a["status"]
                    else:
                        plan["watch"] = {"key": k, "folder": planmod.db_text(g.folder),
                                         "files": [planmod.db_text(f) for f in scope], "deep": deep}
                        pdir = plans_dir()
                        pdir.mkdir(parents=True, exist_ok=True)
                        planmod.save(plan, pdir / f"{plan['job']}.json")
                        superseded = _supersede(k, but=plan["job"])   # one plan per album waits: this one
                        outcome = "planned"
                        if superseded:
                            self.log.info("%s: plan %s replaces %s", g.folder, plan["job"], ", ".join(superseded))
                    errs = prop.provider_errors or {}
                    retryable = prop.status == "unresolved" and any(
                        v in ("outage", "rate_limited", "invalid_response") for v in errs.values())
                    status = ("excluded" if a["status"] == "excluded" or outcome == "excluded" else
                              "provider_error" if retryable else
                              "blocked" if a["status"] == "blocked" or outcome == "blocked" else
                              "unresolved" if prop.status == "unresolved" else outcome)
                    shown = status
                    if status == "blocked" and not self.destination_ready(prof):
                        status = "waiting_destination"      # tried again once it is back
                    self.record(db, g.folder, prof, status, attempts,
                                ex.rules_hash(ex.load_rules(prof.name)) if status == "excluded" else a.get("label", ""),
                                key=k, files=scope, deep=deep)
                    if prof["unattended"]:
                        self.filed(db, a, prof, outcome)
                    done[str(g.folder)] = shown
                    self.log.info("%s: %s (%s)", g.folder, status, why)
                except Exception as exc:                       # one album never stops the watcher
                    self.log.exception("watch: %s failed", g.folder)
                    self.record(db, g.folder, prof, "provider_error", attempts, str(exc)[:200], key=k, files=scope, deep=deep)
                    done[str(g.folder)] = "error"
        db.close()
        return done

    def run(self):
        """Watch until stopped. One watching process at a time (the app's background
        watcher, the systemd service, a `tagmender watch` in a terminal): a second one says
        so and ends. A folder added or a profile changed is seen at the next pass."""
        with run_lock() as held:
            if not held:
                self.log.warning("another TagMender is already watching (pid %s); not starting a second",
                                 watching_pid() or "?")
                self.already = True
                return
            self.already = False
            started = time.time()
            while not self.stop:
                if stop_requested(started):
                    self.log.info("watch: asked to stop")
                    break
                if not self.fixed:
                    try:
                        self.profiles = self._watched_profiles()
                    except Exception:                       # a profile being saved: the last set
                        self.log.exception("watch: profiles could not be read; using the last ones")
                if self.profiles:
                    self.tick()
                else:
                    self.log.info("no profile watches a folder; waiting")
                if self.once:
                    return
                interval = min((p["watch"]["interval_seconds"] for p in self.profiles), default=300)
                end = time.time() + max(1, interval)
                while not self.stop and time.time() < end:
                    if stop_requested(started):
                        self.log.info("watch: asked to stop")
                        self.stop = True
                        break
                    time.sleep(min(1, max(0, end - time.time())))


def _stop_file() -> Path:
    return ex.STATE_DIR / "watch.stop"


def request_stop():
    """Ask the process watching now to stop (a signal cannot reach it from another Flatpak
    instance: each has its own process numbers)."""
    ex.STATE_DIR.mkdir(parents=True, exist_ok=True)
    _stop_file().write_text(str(time.time()))


def stop_requested(since: float) -> bool:
    """A stop was asked for after `since` (the watcher's start); the request is used up."""
    f = _stop_file()
    try:
        asked = float(f.read_text().strip() or 0)
    except (OSError, ValueError):
        return False
    if asked < since:
        return False
    try:
        f.unlink()
    except OSError:
        pass
    return True


def _run_lock_file() -> Path:
    return ex.STATE_DIR / "watching.pid"


class run_lock:
    """Held while a process watches; the file names its pid (for Stop)."""

    def __enter__(self):
        import fcntl
        ex.STATE_DIR.mkdir(parents=True, exist_ok=True)
        self.fh = open(_run_lock_file(), "a+")
        for n in range(20):                  # (watching_pid's brief look is not another watcher)
            try:
                fcntl.flock(self.fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if n == 19:
                    self.fh.close()
                    self.fh = None
                    return False
                time.sleep(0.1)
        self.fh.seek(0)
        self.fh.truncate()
        self.fh.write(str(os.getpid()))
        self.fh.flush()
        return True

    def __exit__(self, *exc):
        if self.fh is not None:
            import fcntl
            try:
                self.fh.seek(0)
                self.fh.truncate()
                fcntl.flock(self.fh, fcntl.LOCK_UN)
            finally:
                self.fh.close()
        return False


def watching_pid():
    """The pid of the process watching now, or None."""
    import fcntl
    try:
        with open(_run_lock_file(), "a+") as fh:
            try:
                fcntl.flock(fh, fcntl.LOCK_SH | fcntl.LOCK_NB)
            except BlockingIOError:
                fh.seek(0)
                txt = fh.read().strip()
                return int(txt) if txt.isdigit() else -1
            fcntl.flock(fh, fcntl.LOCK_UN)
            return None
    except OSError:
        return None


def plans_dir() -> Path:
    return ex.STATE_DIR / "plans"


def pending_plans() -> list[dict]:
    """Plans a watched folder made for a profile that asks first, not yet applied or
    dismissed, oldest first: {"path", "job", "profile", "albums": [(source, label, to)]}."""
    out = []
    try:
        paths = sorted(plans_dir().glob("*.json"), key=lambda x: x.stat().st_mtime)
    except OSError:
        return out
    for x in paths:
        if (executor.jobs_dir() / x.stem / "journal.jsonl").exists():     # applied
            continue
        try:
            p = planmod.load(x)
        except Exception:                                # unreadable: shown, so it can be dismissed
            out.append({"path": x, "job": x.stem, "profile": "?", "albums": [], "error": True})
            continue
        albums = []
        for a in p.get("albums") or []:
            dst = sorted({str(Path(fe["dst"]).parent) for fe in a.get("files") or []})
            albums.append((a.get("source_dir", ""), a.get("label") or a.get("status", ""),
                           dst[0] if len(dst) == 1 else os.path.commonpath(dst) if dst else ""))
        out.append({"path": x, "job": p.get("job", x.stem), "profile": p.get("profile", ""), "albums": albums})
    return out


class pass_lock:
    """Held while the watched folders are looked at, or a waiting plan is applied: a pass
    never looks at an album half moved by an approval."""

    def __init__(self, blocking=False):
        self.blocking = blocking

    def __enter__(self):
        import fcntl
        ex.STATE_DIR.mkdir(parents=True, exist_ok=True)
        self.fh = open(ex.STATE_DIR / "watch.lock", "w")
        try:
            fcntl.flock(self.fh, fcntl.LOCK_EX | (0 if self.blocking else fcntl.LOCK_NB))
        except BlockingIOError:
            self.fh.close()
            self.fh = None
            return False
        return True

    def __exit__(self, *exc):
        if self.fh is not None:
            import fcntl
            fcntl.flock(self.fh, fcntl.LOCK_UN)
            self.fh.close()
        return False


def _supersede(key, but=None) -> list:
    """Set aside the waiting plans made earlier for the album known by key."""
    out = []
    for x in pending_plans():
        if x["job"] == but:
            continue
        try:
            w = planmod.load(x["path"]).get("watch") or {}
        except Exception:
            continue
        if w.get("key") == key:
            dismiss_pending(x["path"], "superseded")
            out.append(x["job"])
    return out


def apply_pending(path, log=None) -> dict:
    """Carry out a plan waiting for approval (after any pass looking at the watched folders
    now), and remember the outcome: where each album was filed, and - when it could not be
    carried out (a drive out, a file changed) - the album is planned again later."""
    path = Path(path)
    log = log or logsetup.setup("watch")
    with pass_lock(blocking=True):
        p = planmod.load(path)
        res = executor.Executor(p, log=log).run()
        try:
            prof = pf.load(p.get("profile") or "default")
        except pf.ProfileError:
            prof = None
        if prof is None:
            return res
        db = _db()
        try:
            w = Watcher([prof], once=True, log=log)
            meta = p.get("watch") or {}
            for r in res.get("albums") or []:
                a = p["albums"][r["index"]]
                w.filed(db, a, prof, r["status"])
                if not meta.get("key"):
                    continue
                folder = planmod.db_path(meta["folder"])
                files = [planmod.db_path(f) for f in meta.get("files") or []]
                if r["status"] in ("excluded", "skipped"):
                    continue                              # a rule says leave it: it stays "planned"
                status = r["status"] if r["status"] in ("done", "unchanged") else \
                    "failed" if r["status"] == "failed" else "conflict"
                w.record(db, folder, prof, status, 1, r.get("detail") or r.get("label", ""),
                         key=meta["key"], files=files, deep=meta.get("deep", True))
        finally:
            db.close()
    return res


def dismiss_pending(path, why="dismissed") -> Path:
    """Set a waiting plan aside (kept in plans/dismissed/, never deleted); the album stays
    as it is and is not planned again until its files change."""
    path = Path(path)
    to = plans_dir() / why
    to.mkdir(parents=True, exist_ok=True)
    dest = to / path.name
    n = 1
    while dest.exists():
        dest = to / f"{path.stem}.{n}{path.suffix}"
        n += 1
    os.replace(path, dest)
    return dest


def recent(limit=50) -> list[tuple]:
    """What the watcher did last: (folder, profile, status, when, label), newest first."""
    try:
        db = _db()
        rows = db.execute("SELECT folder, profile, status, ts, detail FROM processed ORDER BY ts DESC LIMIT ?",
                          (limit,)).fetchall()
        db.close()
    except sqlite3.Error:
        return []
    out = []
    for key, prof, status, ts, detail in rows:
        folder = str(planmod.db_path(key))
        if "#" in folder and len(folder.rsplit("#", 1)[1]) == 12:     # one album of a mixed folder
            folder = folder.rsplit("#", 1)[0]
        label = "" if status == "excluded" else (detail or "")         # (an excluded row holds a rules hash)
        out.append((folder, prof, status, ts, label))
    return out


# ---- the background service (Linux: a systemd user service) -------------------
SERVICE = "tagmender-watch.service"


def service_file() -> Path:
    return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "systemd" / "user" / SERVICE


def service_text(python=None, script=None) -> str:
    """The unit for `tagmender watch --nice`. It is not sandboxed: it reads and writes every
    folder you can (home, /media, /mnt, /run/media, /srv, shares), as the app does - a
    ProtectSystem sandbox made any other library read-only and, where unprivileged user
    namespaces are off (Ubuntu 24.04), kept the service from starting. Which folders it
    watches comes from the profiles, read again on every pass."""
    import sys
    here = Path(__file__).resolve().parent
    if python is None and script is None and os.environ.get("APPIMAGE"):
        start = _unit_quote(os.environ["APPIMAGE"])      # the AppImage itself: its mount path changes each run
    elif python is None and script is None and here == Path("/usr/lib/tagmender") \
            and os.access("/usr/bin/tagmender", os.X_OK):
        start = "/usr/bin/tagmender"                     # the .deb
    else:
        start = f"{_unit_quote(python or sys.executable)} -u {_unit_quote(script or str(here / 'tagmender.py'))}"
    return "\n".join([
        "# Written by TagMender (Watched folders > Run in the background); written again when you press",
        "# it. The watched folders and what is done with them are the profiles' settings, not this file's.",
        "[Unit]",
        "Description=TagMender watched folders (identify, tag and file new music)",
        "After=network-online.target",
        "Wants=network-online.target",
        "",
        "[Service]",
        "Type=simple",
        *[f"Environment={_unit_quote(k + '=' + os.environ[k])}" for k in _SERVICE_ENV if os.environ.get(k)],
        "EnvironmentFile=-%h/.config/tagmender/env",       # (keys: DISCOGS_TOKEN=..., mode 600; never in this file)
        f"ExecStart={start} watch --nice",
        "Restart=on-failure",
        "RestartSec=60",
        "Nice=10",
        "IOSchedulingClass=idle",
        "NoNewPrivileges=true",
        "",
        "[Install]",
        "WantedBy=default.target",
        ""])


# where the plugins and Picard's settings are, as this session knows them (a service has none of
# the shell's environment); keys are never written here
_SERVICE_ENV = ("TAGMENDER_COVERUP_DIR", "TAGMENDER_DISCOGS_PLUGIN", "TAGMENDER_PICARD_INI", "TAGMENDER_PROFILE")


def _unit_quote(s: str) -> str:
    s = str(s).replace("%", "%%")
    if any(c in s for c in " \t\"'\\"):
        return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'
    return s


def _systemctl(*args, run=None):
    import subprocess
    run = run or subprocess.run
    try:
        r = run(["systemctl", "--user", *args], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError) as exc:
        return 127, str(exc)
    return r.returncode, (r.stdout or "") + (r.stderr or "")


FLATPAK_ID = "io.github.herculepirate.TagMender"


def in_flatpak() -> bool:
    return Path("/.flatpak-info").exists()


def service_supported() -> bool:
    """Watching after the app closes: a systemd user service, or in the Flatpak the
    Background portal (a Flatpak cannot install a systemd service)."""
    import shutil
    import sys
    if not sys.platform.startswith("linux"):
        return False
    return in_flatpak() or shutil.which("systemctl") is not None


def _autostart_file() -> Path:
    # where the Background portal puts it: the host's ~/.config/autostart (the Flatpak has
    # --filesystem=host, so the real home is visible; XDG_CONFIG_HOME is the sandbox's own)
    return Path.home() / ".config" / "autostart" / f"{FLATPAK_ID}.desktop"


def _gvariant(v) -> str:
    """Python value as GVariant text (gdbus call's argument format)."""
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, str):
        return "'" + v.replace("\\", "\\\\").replace("'", "\\'") + "'"
    if isinstance(v, list):
        return "[" + ", ".join(_gvariant(x) for x in v) + "]" if v else "@as []"
    if isinstance(v, dict):
        return "{" + ", ".join(f"{_gvariant(k)}: <{_gvariant(x)}>" for k, x in v.items()) + "}" if v \
            else "@a{sv} {}"
    raise TypeError(type(v))


def _portal_background(autostart: bool, call=None) -> str:
    """Ask the Background portal to start `tagmender watch --nice` at login (or not), with
    gdbus (in every Flatpak runtime). Returns "" or what went wrong."""
    opts = {"reason": "Keep the watched folders processed when TagMender is closed",
            "autostart": autostart, "commandline": ["tagmender", "watch", "--nice"], "dbus-activatable": False}
    if call is not None:
        return call(opts)
    import shutil
    import subprocess
    if not shutil.which("gdbus"):
        return "gdbus is not available"
    try:
        r = subprocess.run(["gdbus", "call", "--session", "--dest", "org.freedesktop.portal.Desktop",
                            "--object-path", "/org/freedesktop/portal/desktop", "--method",
                            "org.freedesktop.portal.Background.RequestBackground", "", _gvariant(opts)],
                           capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError) as exc:
        return str(exc)
    return "" if r.returncode == 0 else (r.stderr or r.stdout).strip()[:300]


def _start_detached():
    """Start `tagmender watch --nice` on its own. In the Flatpak through the Flatpak portal
    (flatpak-spawn): a new instance of the app's sandbox, which outlives the app - a process
    started inside the app's own sandbox ends with it."""
    import shutil
    import subprocess
    import sys
    if in_flatpak() and shutil.which("flatpak-spawn"):
        cmd = ["flatpak-spawn", "tagmender", "watch", "--nice"]
    else:
        cmd = [sys.executable, "-u", str(Path(__file__).resolve().parent / "tagmender.py"), "watch", "--nice"]
    subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     start_new_session=True)


def service_status(run=None) -> str:
    """"running", "stopped", "failed" or "not installed"."""
    if in_flatpak():
        if watching_pid():
            return "running"
        return "stopped" if _autostart_file().exists() else "not installed"
    if not service_file().exists():
        return "not installed"
    code, out = _systemctl("is-active", SERVICE, run=run)
    out = out.strip().splitlines()[-1] if out.strip() else ""
    return "running" if out == "active" else "failed" if out == "failed" else "stopped"


def install_service(run=None, portal=None, spawn=None, **kw) -> str:
    """Start watching now and at every login, when TagMender is closed too. Returns
    "running" or what went wrong, for the user."""
    if in_flatpak():
        err = _portal_background(True, portal)
        if err:
            return f"The Background portal refused: {err}"
        try:                                   # a Stop asked for just before: no longer wanted
            _stop_file().unlink()
        except OSError:
            pass
        if not watching_pid():
            (spawn or _start_detached)()
        return "running"
    f = service_file()
    f.parent.mkdir(parents=True, exist_ok=True)
    text = service_text(**kw)
    if f.exists() and f.read_text(errors="replace") != text:
        os.replace(f, f.with_name(f.name + ".old"))
    tmp = f.with_name(f".{f.name}.tmp")
    tmp.write_text(text)
    os.replace(tmp, f)
    for args in (("daemon-reload",), ("enable", "--now", SERVICE), ("restart", SERVICE)):
        code, out = _systemctl(*args, run=run)
        if code != 0:
            return f"systemctl --user {' '.join(args)} failed: {out.strip()[:300]}"
    return "running"


def stop_service(run=None, portal=None) -> str:
    """Stop watching in the background and stop it starting at login; the unit file stays
    (Run in the background starts it again)."""
    if in_flatpak():
        err = _portal_background(False, portal)
        if watching_pid():
            request_stop()
        return f"The Background portal refused: {err}" if err else "stopped"
    code, out = _systemctl("disable", "--now", SERVICE, run=run)
    return "stopped" if code == 0 else f"systemctl --user disable failed: {out.strip()[:300]}"


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
