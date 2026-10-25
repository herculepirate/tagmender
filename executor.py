"""executor.py - carries out a plan safely, keeps a journal, recovers, and undoes.

Every job has a folder, ~/.local/state/tagmender/jobs/<job>/, holding the plan, an
append-only journal (journal.jsonl, each line fsync'ed before the step it announces
continues), and the backups undo needs. Rotating logs never touch it.

How a file is changed - never in place:
  1. The source is checked against the plan (size, modification time): a file changed
     since planning is not touched, and its album is skipped.
  2. The source is copied to a hidden staging file next to its destination
     (.NAME.tm-<job>.part), after checking there is room for it.
  3. Tags (and a front cover) are written to the staging copy, then read back; its audio
     data is compared with the source's (payload.digest). Any difference: the staging
     copy is deleted and the original stays as it was.
  4. Commit: in place, the staging copy replaces the original with one atomic rename;
     to another folder, it is linked in under its new name only if nothing has that name
     (an existing file is never overwritten). A move removes the source only after
     the new file is verified.
  5. Tag backups (or, for formats the tag backup does not cover, a copy of the file) are
     taken first, so the job can be undone.

Multi-file operations are not atomic: a crash can leave an album half-done. The journal
says exactly how far each file got; `recover` deletes stray staging files and finishes
moves whose new file was already verified. If a file fails inside an album, the files of
that album already changed are put back straight away, so albums are not left half-tagged.

Writes stay inside the plan's roots (its profile's sources and destination): a path
that resolves elsewhere (a symlink pointing out, "..") is refused. A destination root
that is not there (an unmounted share) is never created.
"""
from __future__ import annotations

import errno
import fcntl
import hashlib
import json
import os
import shutil
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

import exclusions as ex
import logsetup
import payload
import plan as planmod
import tagio

STAGE_TAG = ".tm-"
SPACE_MARGIN = 16 * 1024 * 1024


class StepError(RuntimeError):
    pass


def jobs_dir() -> Path:
    return ex.STATE_DIR / "jobs"


def job_dir(job) -> Path:
    return jobs_dir() / str(job)


# --------------------------------------------------------------------------
# journal
# --------------------------------------------------------------------------
class Journal:
    def __init__(self, folder: Path):
        self.folder = folder
        self.path = folder / "journal.jsonl"
        folder.mkdir(parents=True, exist_ok=True)

    def write(self, **ev):
        ev["t"] = time.time()
        with open(self.path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(ev, ensure_ascii=False, default=str) + "\n")
            fh.flush()
            os.fsync(fh.fileno())

    def read(self):
        if not self.path.exists():
            return []
        out = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            try:
                out.append(json.loads(line))
            except ValueError:
                pass                               # a line cut short by a crash
        return out


def pending_jobs() -> list[str]:
    out = []
    if not jobs_dir().is_dir():
        return out
    for d in sorted(jobs_dir().iterdir()):
        j = Journal(d).read()
        if j and not any(e.get("ev") in ("end", "recovered") for e in j):
            out.append(d.name)
    return out


# --------------------------------------------------------------------------
# job history (state database; separate from logs)
# --------------------------------------------------------------------------
def _db():
    ex.STATE_DIR.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(ex.STATE_DIR / "state.db", timeout=60)
    db.execute("CREATE TABLE IF NOT EXISTS jobs (job TEXT PRIMARY KEY, profile TEXT, started REAL, ended REAL,"
               " status TEXT, summary TEXT)")
    db.execute("CREATE TABLE IF NOT EXISTS job_albums (job TEXT, album TEXT, status TEXT, label TEXT,"
               " detail TEXT, ts REAL)")
    return db


def _record_job(job, **kw):
    db = _db()
    row = db.execute("SELECT job FROM jobs WHERE job=?", (job,)).fetchone()
    if row:
        sets = ", ".join(f"{k}=?" for k in kw)
        db.execute(f"UPDATE jobs SET {sets} WHERE job=?", (*kw.values(), job))
    else:
        db.execute("INSERT INTO jobs (job, profile, started, ended, status, summary) VALUES (?,?,?,?,?,?)",
                   (job, kw.get("profile"), kw.get("started"), kw.get("ended"), kw.get("status"),
                    kw.get("summary")))
    db.commit()
    db.close()


def _record_album(job, album, status, label="", detail=""):
    db = _db()
    db.execute("INSERT INTO job_albums VALUES (?,?,?,?,?,?)", (job, album, status, label, detail, time.time()))
    db.commit()
    db.close()


def jobs(limit=50):
    db = _db()
    rows = db.execute("SELECT job, profile, started, ended, status, summary FROM jobs ORDER BY started DESC LIMIT ?",
                      (limit,)).fetchall()
    db.close()
    return rows


def job_albums(job):
    db = _db()
    rows = db.execute("SELECT album, status, label, detail, ts FROM job_albums WHERE job=? ORDER BY ts", (job,)).fetchall()
    db.close()
    return rows


# --------------------------------------------------------------------------
# small safe file operations
# --------------------------------------------------------------------------
@contextmanager
def file_lock(path):
    """No two TagMender processes change the same file at once."""
    d = ex.STATE_DIR / "locks"
    d.mkdir(parents=True, exist_ok=True)
    key = hashlib.sha1(os.path.realpath(str(path)).encode()).hexdigest()
    with open(d / key, "w") as fh:
        try:
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise StepError(f"{path} is being changed by another TagMender process") from None
        try:
            yield
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


def _fsync_dir(d):
    try:
        fd = os.open(str(d), os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    except OSError:
        pass


def _inside(path, roots) -> bool:
    if not roots:
        return True
    real = os.path.realpath(str(path))
    return any(real == r or real.startswith(r.rstrip(os.sep) + os.sep) for r in roots)


def _check_target(dst: Path, roots):
    """Refuse '..', writing through a symlink, and anything whose nearest existing folder
    resolves outside the plan's roots (a symlinked folder pointing elsewhere)."""
    if ".." in Path(dst).parts:
        raise StepError(f"refusing a path with '..': {dst}")
    if Path(dst).is_symlink():
        raise StepError(f"refusing to write through a symbolic link: {dst}")
    p = Path(dst).parent
    while not p.exists() and p != p.parent:
        p = p.parent
    if not _inside(p, roots):
        raise StepError(f"{dst} is outside the folders this profile may change")


def _make_dirs(d: Path, roots, journal, album):
    """Create d below an existing root, journalling each folder created (undo removes them)."""
    missing = []
    p = Path(d)
    while not p.exists():
        missing.append(p)
        p = p.parent
    if missing and not _inside(p, roots):
        raise StepError(f"{d} would be created outside the profile's folders")
    for m in reversed(missing):
        m.mkdir()
        journal.write(ev="dir_created", album=album, path=str(m))


def _space_for(folder: Path, nbytes: int):
    free = shutil.disk_usage(str(folder)).free
    if free < nbytes + SPACE_MARGIN:
        raise StepError(f"not enough free space in {folder}: {free // 1048576} MiB free, "
                        f"{(nbytes + SPACE_MARGIN) // 1048576} MiB needed")


def _place(tmp: Path, dst: Path):
    """Put tmp at dst without ever overwriting an existing dst."""
    try:
        os.link(tmp, dst)
        os.unlink(tmp)
    except FileExistsError:
        raise StepError(f"{dst} appeared meanwhile; not overwritten") from None
    except OSError as exc:
        if exc.errno not in (errno.EPERM, errno.ENOTSUP, errno.EOPNOTSUPP, errno.EXDEV, errno.ENOSYS):
            raise
        if dst.exists():                                      # filesystem without hard links (SMB, FAT)
            raise StepError(f"{dst} appeared meanwhile; not overwritten") from None
        os.rename(tmp, dst)
    _fsync_dir(dst.parent)


def _copy_fsync(src: Path, dst: Path):
    with open(src, "rb") as a, open(dst, "wb") as b:
        shutil.copyfileobj(a, b, 1 << 20)
        b.flush()
        os.fsync(b.fileno())
    shutil.copystat(src, dst)


def _stage_name(dst: Path, job) -> Path:
    return dst.parent / f".{dst.name}{STAGE_TAG}{job}.part"


# --------------------------------------------------------------------------
# artwork embedding (front cover)
# --------------------------------------------------------------------------
def embed_front(path, data: bytes, mime="image/jpeg"):
    import base64
    import mutagen
    from mutagen.flac import FLAC, Picture
    from mutagen.id3 import APIC, ID3
    from mutagen.mp4 import MP4, MP4Cover
    f = mutagen.File(path)
    if isinstance(f, FLAC):
        pic = Picture()
        pic.type, pic.mime, pic.data = 3, mime, data
        f.add_picture(pic)
    elif isinstance(f, MP4):
        if f.tags is None:
            f.add_tags()
        fmt = MP4Cover.FORMAT_PNG if mime == "image/png" else MP4Cover.FORMAT_JPEG
        f.tags["covr"] = list(f.tags.get("covr", [])) + [MP4Cover(data, fmt)]
    elif isinstance(getattr(f, "tags", None), ID3) or type(f).__name__ in ("MP3", "AIFF", "WAVE"):
        if f.tags is None:
            f.add_tags()
        f.tags.add(APIC(encoding=3, mime=mime, type=3, desc="", data=data))
    elif type(f).__name__ in ("OggVorbis", "OggOpus"):
        pic = Picture()
        pic.type, pic.mime, pic.data = 3, mime, data
        f.tags["METADATA_BLOCK_PICTURE"] = [base64.b64encode(pic.write()).decode("ascii")]
    else:
        raise tagio.Unsupported(f"no front-cover embedding for {type(f).__name__}")
    f.save()


# --------------------------------------------------------------------------
# backups
# --------------------------------------------------------------------------
def _backup(src: Path, jd: Path, n: int) -> dict:
    """Tag snapshot when the tag backup covers the format, else a copy of the file."""
    import tag_albums as ta
    blobs = jd / "blobs"
    blobs.mkdir(exist_ok=True)
    try:
        fam = tagio.family(tagio.open_file(src))
    except tagio.Unsupported:
        fam = None
    if fam in ("id3", "mp4", "vorbis"):
        return {"kind": "tags", "rec": ta.backup_tags(src, blobs)}
    files = jd / "files"
    files.mkdir(exist_ok=True)
    dest = files / f"{n}{src.suffix}"
    _copy_fsync(src, dest)
    return {"kind": "file", "path": str(dest)}


def _restore(path: Path, backup: dict, jd: Path):
    import tag_albums as ta
    if backup["kind"] == "tags":
        ta.restore_tags(path, backup["rec"], jd / "blobs")
    else:
        stage = path.parent / f".{path.name}{STAGE_TAG}undo.part"
        _copy_fsync(Path(backup["path"]), stage)
        os.replace(stage, path)


# --------------------------------------------------------------------------
# executing
# --------------------------------------------------------------------------
class Executor:
    def __init__(self, plan: dict, log=None, cancel=None, progress=None):
        self.plan = plan
        self.job = plan["job"]
        self.jd = job_dir(self.job)
        self.journal = Journal(self.jd)
        self.roots = plan.get("roots") or []
        self.allow_unverified = bool((plan.get("profile_data") or {}).get("allow_unverified_writes"))
        self.log = log or logsetup.setup("apply", job=self.job)
        self.cancel = cancel or (lambda: False)
        self.progress = progress or (lambda *a: None)
        self.rules = None
        self.results = []

    def run(self) -> dict:
        if self.journal.read():
            raise StepError(f"job {self.job} has already run (use a new plan, or `tagmender undo {self.job}`)")
        planmod.save(self.plan, self.jd / "plan.json")
        self.journal.write(ev="begin", job=self.job, profile=self.plan["profile"])
        _record_job(self.job, profile=self.plan["profile"], started=time.time(), status="running")
        prof = self.plan["profile"]
        self.rules = ex.load_rules(prof if prof != "default" else None)
        counts = {}
        albums = self.plan["albums"]
        for i, a in enumerate(albums):
            if self.cancel():
                self.journal.write(ev="cancelled", album=i)
                counts["cancelled"] = counts.get("cancelled", 0) + len(albums) - i
                break
            self.progress(i, len(albums), a["source_dir"])
            status, label, detail = self.album(i, a)
            counts[status] = counts.get(status, 0) + 1
            self.results.append({"album": a["source_dir"], "status": status, "label": label, "detail": detail})
            _record_album(self.job, a["source_dir"], status, label, detail)
        final = "done" if not any(k in counts for k in ("failed", "cancelled")) else "partial"
        self.journal.write(ev="end", status=final, counts=counts)
        _record_job(self.job, ended=time.time(), status=final, summary=json.dumps(counts))
        self.log.info("job %s %s: %s", self.job, final, counts)
        return {"job": self.job, "status": final, "counts": counts, "albums": self.results}

    # ---- one album --------------------------------------------------------
    def album(self, i, a):
        if a["status"] in ("excluded", "skipped", "blocked", "unchanged"):
            label = a.get("label") or a["status"]
            detail = (f"rule '{a['rule']['name']}'" if a.get("rule") else "; ".join(a.get("conflicts") or []))
            if a["status"] == "excluded":
                ex.State().log_raw(self.job, a["source_dir"], a["label"], a["rule"]["id"], a["rule"]["name"],
                                   a["rule"]["matched"])
            return a["status"], label, detail
        # rules again, now (they may have changed since the plan was made)
        files = [Path(fe["src"]) for fe in a["files"]]
        dec = ex.evaluate(files, self.rules, release=a.get("release") or None, overrides=ex.State().overrides())
        whole = dec.summary()
        if whole is not None:
            self.journal.write(ev="album_cancelled", album=i, rule=whole.rule.id, label=whole.label)
            ex.State().log(self.job, a["source_dir"], whole, "rule matched before writing; pending operations cancelled")
            return "excluded", whole.label, f"rule '{whole.rule.name}' (matched before writing)"
        # every file still as planned, destination still there
        for fe in a["files"] + a["companions"]:
            fp = fe["fingerprint"]
            try:
                st = os.stat(fe["src"])
            except FileNotFoundError:
                return "conflict", "Changed", f"{fe['src']} is gone since the plan was made"
            if (st.st_size, st.st_mtime_ns) != (fp["size"], fp["mtime_ns"]):
                return "conflict", "Changed", f"{Path(fe['src']).name} changed since the plan was made"
        self.journal.write(ev="album_begin", album=i, source=a["source_dir"])
        done_steps = []
        try:
            for j, fe in enumerate(a["files"]):
                if fe["op"] == "none" or Path(fe["src"]) in dec.excluded:
                    continue
                self.file_step(i, j, fe, a, done_steps)
            for j, ce in enumerate(a["companions"]):
                self.companion_step(i, j, ce, done_steps)
            if a.get("artwork") and a["artwork"].get("file"):
                self.cover_file(i, a, done_steps)
            for sc in a.get("sidecars") or []:
                if Path(sc["src"]) not in dec.excluded:
                    self.new_file(i, sc, done_steps)
            if self.plan["operation"] == "move" or any(fe["op"] == "move" for fe in a["files"]):
                self.cleanup_dirs(i, a)
        except Exception as exc:                                  # roll the album back, then carry on
            self.log.error("album %s failed: %s", a["source_dir"], exc)
            self.journal.write(ev="album_failed", album=i, error=str(exc))
            problems = self.rollback(done_steps)
            self.journal.write(ev="album_rolled_back", album=i, problems=problems)
            return "failed", "Failed", f"{exc}" + (f"; rollback problems: {problems}" if problems else
                                                   "; album put back as it was")
        self.journal.write(ev="album_done", album=i)
        return "done", "Done", ""

    def file_step(self, i, j, fe, a, done_steps):
        src, dst, op = Path(fe["src"]), Path(fe["dst"]), fe["op"]
        changes = fe.get("changes") or {}
        art = a.get("artwork") or {}
        embed = bool(art.get("front")) and fe["dst"] in (art.get("embed_into") or [])
        if op in ("copy", "move"):
            _check_target(dst, self.roots)
        with file_lock(src):
            src_digest = payload.digest(src)
            if src_digest is None and (changes or embed) and not self.allow_unverified:
                raise StepError(f"{src.name}: its audio data cannot be verified after writing tags "
                                "(format not recognised and ffmpeg unavailable); not written")
            n = f"{i}-{j}"
            backup = _backup(src, self.jd, n) if op in ("tag", "move") else None
            self.journal.write(ev="file", album=i, idx=j, op=op, src=str(src), dst=str(dst), state="begin",
                               digest=src_digest, backup=backup)
            if op == "move" and not changes and not embed and _same_fs(src, dst.parent if dst.parent.exists()
                                                                       else self._existing(dst.parent)):
                _make_dirs(dst.parent, self.roots, self.journal, i)
                _place_move(src, dst)                     # same filesystem: a rename, nothing copied
                self.journal.write(ev="file", album=i, idx=j, state="committed", moved_by="rename")
                self.journal.write(ev="file", album=i, idx=j, state="source_removed")
                done_steps.append(("file", i, j, op, str(src), str(dst), backup, src_digest))
                return
            if op in ("copy", "move"):
                _make_dirs(dst.parent, self.roots, self.journal, i)
            stage = _stage_name(dst, self.job)
            _space_for(dst.parent, os.path.getsize(src))
            _copy_fsync(src, stage)
            try:
                if changes:
                    tagio.write(stage, changes)
                if embed:
                    embed_front(stage, Path(art["front"]).read_bytes(),
                                "image/png" if str(art["front"]).lower().endswith(".png") else "image/jpeg")
                after = payload.digest(stage)
                if src_digest is not None and after != src_digest:
                    raise StepError(f"{src.name}: audio data differed after writing tags; original kept")
                got = tagio.read(stage)
                for k, v in changes.items():
                    if (got.get(k) or []) != (v or []):
                        raise StepError(f"{src.name}: tag {k} did not read back as written")
                with open(stage, "rb+") as fh:
                    os.fsync(fh.fileno())
            except Exception:
                stage.unlink(missing_ok=True)
                raise
            self.journal.write(ev="file", album=i, idx=j, state="staged", stage=str(stage))
            if op == "tag":
                os.replace(stage, src)
                _fsync_dir(src.parent)
            else:
                try:
                    _place(stage, dst)
                except Exception:
                    stage.unlink(missing_ok=True)
                    raise
            st = os.stat(dst)
            self.journal.write(ev="file", album=i, idx=j, state="committed", size=st.st_size,
                               mtime_ns=st.st_mtime_ns)
            done_steps.append(("file", i, j, op, str(src), str(dst), backup, src_digest))
            if op == "move":
                if src_digest is not None and payload.digest(dst) != src_digest:
                    raise StepError(f"{dst.name}: new file did not verify; source kept")
                os.unlink(src)
                _fsync_dir(src.parent)
                self.journal.write(ev="file", album=i, idx=j, state="source_removed")

    def _existing(self, p: Path) -> Path:
        while not p.exists() and p != p.parent:
            p = p.parent
        return p

    def companion_step(self, i, j, ce, done_steps):
        src, dst, op = Path(ce["src"]), Path(ce["dst"]), ce["op"]
        _check_target(dst, self.roots)
        _make_dirs(dst.parent, self.roots, self.journal, i)
        stage = _stage_name(dst, self.job)
        saved = None
        if ce.get("rewrite"):
            saved = self.jd / "companions" / f"{i}-{j}{src.suffix}"
            saved.parent.mkdir(exist_ok=True)
            _copy_fsync(src, saved)
            stage.write_text(ce["rewrite"]["text"], encoding=ce["rewrite"]["encoding"])
            shutil.copystat(src, stage)
        else:
            _copy_fsync(src, stage)
            if payload.file_sha256(stage) != payload.file_sha256(src):
                stage.unlink(missing_ok=True)
                raise StepError(f"{src.name}: copy did not verify")
        self.journal.write(ev="companion", album=i, idx=j, op=op, src=str(src), dst=str(dst), state="staged",
                           saved=str(saved) if saved else None)
        _place(stage, dst)
        self.journal.write(ev="companion", album=i, idx=j, state="committed")
        done_steps.append(("companion", i, j, op, str(src), str(dst), saved, None))
        if op == "move":
            os.unlink(src)
            self.journal.write(ev="companion", album=i, idx=j, state="source_removed")

    def cover_file(self, i, a, done_steps):
        dst = Path(a["artwork"]["file"])
        _check_target(dst, self.roots)
        if dst.exists():
            return
        stage = _stage_name(dst, self.job)
        _copy_fsync(Path(a["artwork"]["front"]), stage)
        _place(stage, dst)
        st = os.stat(dst)
        self.journal.write(ev="cover_file", album=i, path=str(dst), size=st.st_size, mtime_ns=st.st_mtime_ns)
        done_steps.append(("cover", i, 0, "create", "", str(dst), None, None))

    def new_file(self, i, sc, done_steps):
        """A new text file next to the music (time-synced lyrics); never over an existing one."""
        dst = Path(sc["dst"])
        _check_target(dst, self.roots)
        if dst.exists():
            return
        stage = _stage_name(dst, self.job)
        with open(stage, "w", encoding="utf-8") as fh:
            fh.write(sc["text"])
            fh.flush()
            os.fsync(fh.fileno())
        _place(stage, dst)
        st = os.stat(dst)
        self.journal.write(ev="new_file", album=i, path=str(dst), size=st.st_size, mtime_ns=st.st_mtime_ns)
        done_steps.append(("cover", i, 0, "create", "", str(dst), None, None))

    def cleanup_dirs(self, i, a):
        """Remove source folders this album's move left empty (never a profile root)."""
        roots = {r.rstrip(os.sep) for r in self.roots}
        for d in sorted({str(Path(fe["src"]).parent) for fe in a["files"] + a["companions"]},
                        key=len, reverse=True):
            p = Path(d)
            while p.exists() and os.path.realpath(str(p)) not in roots and p != p.parent:
                try:
                    if any(p.iterdir()):
                        break
                    p.rmdir()
                    self.journal.write(ev="dir_removed", album=i, path=str(p))
                except OSError:
                    break
                p = p.parent

    def rollback(self, steps) -> list:
        problems = []
        for step in reversed(steps):
            try:
                _undo_step(step, self.jd)
            except Exception as exc:
                problems.append(str(exc))
        return problems


def _same_fs(a: Path, b: Path) -> bool:
    try:
        return os.stat(a).st_dev == os.stat(b).st_dev
    except OSError:
        return False


def _place_move(src: Path, dst: Path):
    try:
        os.link(src, dst)
        os.unlink(src)
    except FileExistsError:
        raise StepError(f"{dst} exists; not overwritten") from None
    except OSError:
        if dst.exists():
            raise StepError(f"{dst} exists; not overwritten") from None
        os.rename(src, dst)
    _fsync_dir(dst.parent)
    _fsync_dir(src.parent)


def _undo_step(step, jd):
    kind, _i, _j, op, src, dst, extra, digest = step
    src, dst = Path(src) if src else None, Path(dst)
    if kind == "cover":
        dst.unlink(missing_ok=True)
        return
    if kind == "companion":
        if op == "move":
            if not src.exists():
                src.parent.mkdir(parents=True, exist_ok=True)
                if extra:                                      # rewritten: the original was saved
                    _copy_fsync(Path(extra), src)
                    dst.unlink(missing_ok=True)
                else:
                    _place_move(dst, src) if _same_fs(dst, src.parent) else (_copy_fsync(dst, src), dst.unlink())
        else:
            dst.unlink(missing_ok=True)
        return
    backup = extra
    if op == "tag":
        _restore(src, backup, jd)
    elif op == "copy":
        dst.unlink(missing_ok=True)
    elif op == "move":
        if src.exists():
            raise StepError(f"cannot move back: {src} exists")
        src.parent.mkdir(parents=True, exist_ok=True)
        if _same_fs(dst, src.parent):
            _place_move(dst, src)
        else:
            stage = src.parent / f".{src.name}{STAGE_TAG}undo.part"
            _copy_fsync(dst, stage)
            _place(stage, src)
            dst.unlink()
        if backup:
            _restore(src, backup, jd)


# --------------------------------------------------------------------------
# recovery and undo
# --------------------------------------------------------------------------
def _replay(events):
    """Last known state of every file and companion step, with its details merged."""
    files, comps = {}, {}
    for e in events:
        if e.get("ev") == "file":
            k = (e["album"], e["idx"])
            files.setdefault(k, {}).update({x: y for x, y in e.items() if x not in ("ev", "t")})
        elif e.get("ev") == "companion":
            k = (e["album"], e["idx"])
            comps.setdefault(k, {}).update({x: y for x, y in e.items() if x not in ("ev", "t")})
    return files, comps


def recover(job) -> list[str]:
    """Bring an interrupted job to a clean state. Returns what was done, in words."""
    jd = job_dir(job)
    journal = Journal(jd)
    events = journal.read()
    if not events:
        raise StepError(f"no job {job}")
    if any(e.get("ev") in ("end", "recovered") for e in events):
        return [f"job {job} finished normally; nothing to recover"]
    files, comps = _replay(events)
    done = []
    for (a, j), f in sorted(files.items()):
        stage = Path(f["stage"]) if f.get("stage") else None
        if f["state"] in ("begin", "staged"):
            for s in ([stage] if stage else []) + list(Path(f["dst"]).parent.glob(f".{Path(f['dst']).name}{STAGE_TAG}{job}.part")):
                if s and s.exists():
                    s.unlink()
                    done.append(f"removed unfinished copy {s}")
            done.append(f"{Path(f['src']).name}: left as it was before the job")
        elif f["state"] == "committed" and f.get("op") == "move":
            src, dst = Path(f["src"]), Path(f["dst"])
            if src.exists() and dst.exists() and (f.get("digest") is None or payload.digest(dst) == f["digest"]):
                src.unlink()
                journal.write(ev="file", album=a, idx=j, state="source_removed")
                done.append(f"{src.name}: move finished (new file verified, source removed)")
    for (a, j), c in sorted(comps.items()):
        if c["state"] == "staged":
            for s in Path(c["dst"]).parent.glob(f".{Path(c['dst']).name}{STAGE_TAG}{job}.part"):
                s.unlink()
                done.append(f"removed unfinished copy {s}")
        elif c["state"] == "committed" and c.get("op") == "move" and Path(c["src"]).exists() and Path(c["dst"]).exists():
            Path(c["src"]).unlink()
            done.append(f"{Path(c['src']).name}: move finished")
    journal.write(ev="recovered", actions=done)
    _record_job(job, ended=time.time(), status="recovered")
    return done or ["nothing was half-done"]


def undo(job) -> list[str]:
    """Put back everything the job changed, newest first. Files changed since by someone
    else are left alone and reported."""
    jd = job_dir(job)
    journal = Journal(jd)
    events = journal.read()
    if not events:
        raise StepError(f"no job {job}")
    if any(e.get("ev") == "undone" for e in events):
        raise StepError(f"job {job} was already undone")
    if not any(e.get("ev") in ("end", "recovered") for e in events):
        raise StepError(f"job {job} did not finish; run `tagmender recover {job}` first")
    rolled = {e["album"] for e in events if e.get("ev") == "album_rolled_back"}
    files, comps = _replay(events)
    out, problems = [], []
    covers = [e for e in events if e.get("ev") in ("cover_file", "new_file") and e["album"] not in rolled]
    for e in reversed(covers):
        p = Path(e["path"])
        if p.exists() and (os.stat(p).st_size, os.stat(p).st_mtime_ns) == (e["size"], e["mtime_ns"]):
            p.unlink()
            out.append(f"removed {p}")
    for (a, j), c in sorted(comps.items(), reverse=True):
        if a in rolled or c["state"] not in ("committed", "source_removed"):
            continue
        try:
            _undo_step(("companion", a, j, c["op"], c["src"], c["dst"], c.get("saved"), None), jd)
            out.append(f"put back {Path(c['src']).name}")
        except Exception as exc:
            problems.append(f"{c['src']}: {exc}")
    removed = [e for e in events if e.get("ev") == "dir_removed"]
    for e in removed:
        Path(e["path"]).mkdir(parents=True, exist_ok=True)
    for (a, j), f in sorted(files.items(), reverse=True):
        if a in rolled or f["state"] not in ("committed", "source_removed"):
            continue
        dst = Path(f["dst"])
        if f.get("op") == "copy" and dst.exists() and f.get("size") is not None and \
                (os.stat(dst).st_size, os.stat(dst).st_mtime_ns) != (f["size"], f["mtime_ns"]):
            problems.append(f"{dst} was changed after the job; left in place")
            continue
        check = Path(f["src"]) if f.get("op") == "tag" else dst
        if f.get("digest") and check.exists() and payload.digest(check) != f["digest"]:
            problems.append(f"{check} holds different audio now; left alone")
            continue
        try:
            _undo_step(("file", a, j, f["op"], f["src"], f["dst"], f.get("backup"), f.get("digest")), jd)
            out.append(f"put back {Path(f['src']).name}")
        except Exception as exc:
            problems.append(f"{f['src']}: {exc}")
    for e in reversed([e for e in events if e.get("ev") == "dir_created"]):
        p = Path(e["path"])
        try:
            p.rmdir()
        except OSError:
            pass
    journal.write(ev="undone", restored=len(out), problems=problems)
    _record_job(job, status="undone" if not problems else "undone with problems")
    return out + [f"PROBLEM: {p}" for p in problems]


def prune(days: int) -> list[str]:
    """Delete the backups of finished jobs older than DAYS (their undo goes with them)."""
    out = []
    cutoff = time.time() - days * 86400
    if not jobs_dir().is_dir():
        return out
    for d in jobs_dir().iterdir():
        ev = Journal(d).read()
        if not ev or not any(e.get("ev") in ("end", "recovered", "undone") for e in ev):
            continue
        if ev[-1]["t"] < cutoff:
            for sub in ("blobs", "files", "companions"):
                shutil.rmtree(d / sub, ignore_errors=True)
            (d / "PRUNED").write_text(time.strftime("%Y-%m-%d %H:%M"))
            out.append(d.name)
    return out
