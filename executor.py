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
        line = json.dumps(ev, ensure_ascii=False, default=str) + "\n"
        with open(self.path, "a+b") as fh:
            if fh.tell() > 0:                    # a line cut short by a crash: start a new one
                fh.seek(-1, os.SEEK_END)
                if fh.read(1) != b"\n":
                    line = "\n" + line
            fh.write(line.encode("utf-8"))
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


def busy_jobs(but=None) -> list[str]:
    """Unfinished jobs another process is running now (their job lock is held)."""
    out = []
    for j in pending_jobs():
        if j == but:
            continue
        try:
            with job_lock(j):
                pass
        except StepError:
            out.append(j)
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
    db.execute("CREATE TABLE IF NOT EXISTS copies (src TEXT, dst TEXT, job TEXT, ts REAL, PRIMARY KEY (src, dst))")
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


def _record_copy(src, dst, job, remove=False):
    """Remember where a copy profile put each file (planning finds earlier copies by it)."""
    try:
        db = _db()
        if remove:
            db.execute("DELETE FROM copies WHERE src=? AND dst=?", (str(src), str(dst)))
        else:
            db.execute("INSERT OR REPLACE INTO copies VALUES (?,?,?,?)", (str(src), str(dst), job, time.time()))
        db.commit()
        db.close()
    except sqlite3.Error:
        pass


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


@contextmanager
def job_lock(job):
    """One process at a time per job: applying, recovering or undoing it."""
    jd = job_dir(job)
    jd.mkdir(parents=True, exist_ok=True)
    with open(jd / ".lock", "w") as fh:
        try:
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise StepError(f"job {job} is being run by another TagMender process") from None
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


def _make_dirs(d: Path, roots, journal, album, created=None):
    """Create d below an existing root, journalling each folder created (undo removes them)."""
    missing = []
    p = Path(d)
    while not p.exists():
        missing.append(p)
        p = p.parent
    if missing and not _inside(p, roots):
        raise StepError(f"{d} would be created outside the profile's folders")
    for m in reversed(missing):
        journal.write(ev="dir_create_begin", album=album, path=str(m))
        m.mkdir()
        journal.write(ev="dir_created", album=album, path=str(m))
        if created is not None:
            created.append(m)


def _space_for(folder: Path, nbytes: int):
    free = shutil.disk_usage(str(folder)).free
    if free < nbytes + SPACE_MARGIN:
        raise StepError(f"not enough free space in {folder}: {free // 1048576} MiB free, "
                        f"{(nbytes + SPACE_MARGIN) // 1048576} MiB needed")


def _link_then_unlink(old: Path, new: Path, what: str):
    """old -> new by hard link, then remove old: new never overwrites anything. Where the
    filesystem has no hard links (SMB, FAT), a rename after checking new is free."""
    try:
        os.link(old, new)
    except FileExistsError:
        raise StepError(f"{new} {what}; not overwritten") from None
    except OSError as exc:
        if exc.errno not in (errno.EPERM, errno.ENOTSUP, errno.EOPNOTSUPP, errno.EXDEV, errno.ENOSYS,
                             errno.EMLINK):
            raise
        if new.exists():
            raise StepError(f"{new} {what}; not overwritten") from None
        os.rename(old, new)
        return
    try:
        os.unlink(old)
    except OSError:
        os.unlink(new)                    # the old name stays; take the new one away again
        raise


def _place(tmp: Path, dst: Path):
    """Put tmp at dst without ever overwriting an existing dst."""
    _link_then_unlink(tmp, dst, "appeared meanwhile")
    _fsync_dir(dst.parent)


def _tags_sig(path) -> str:
    """A fingerprint of a file's tags: undo leaves files whose tags were edited later alone."""
    try:
        t = tagio.read(path)
    except Exception:
        return ""
    return hashlib.sha1(json.dumps(sorted((k, [str(x) for x in v] if isinstance(v, list) else str(v))
                                          for k, v in t.items()), ensure_ascii=False).encode()).hexdigest()


def _keep_owner(path: Path, st):
    """Give a staged copy the original's owner and group where this user may (on a shared
    library, files often belong to another account); never an error."""
    try:
        if (os.stat(path).st_uid, os.stat(path).st_gid) != (st.st_uid, st.st_gid):
            os.chown(path, st.st_uid, st.st_gid)
    except OSError:
        try:
            os.chown(path, -1, st.st_gid)
        except OSError:
            pass


def _place_staged(stage: Path, dst: Path):
    """_place, and the staging copy is never left behind when it fails."""
    try:
        _place(stage, dst)
    except BaseException:
        if stage.exists() and not (dst.exists() and _same_file(stage, dst)):
            stage.unlink()
        raise


def _copy_fsync(src: Path, dst: Path):
    """A durable copy at dst, always a new file: an existing dst (a staging file left by a
    crash may be a hard link to a track) is unlinked first, never written through."""
    if os.path.lexists(dst):
        os.unlink(dst)
    with open(src, "rb") as a, open(dst, "xb") as b:
        shutil.copyfileobj(a, b, 1 << 20)
        b.flush()
        os.fsync(b.fileno())
    shutil.copystat(src, dst)


def _stage_name(dst: Path, job) -> Path:
    """A hidden staging file next to dst. Short whatever dst's name is: a name near the
    filesystem's 255-byte limit would not fit with a prefix and suffix added."""
    h = hashlib.sha1(Path(dst).name.encode("utf-8", "surrogateescape")).hexdigest()[:12]
    ext = Path(dst).suffix if len(Path(dst).suffix) <= 8 else ""
    return Path(dst).parent / f"{STAGE_TAG}{job}-{h}.part{ext}"   # the extension: how mutagen tells formats


def _old_stage_names(dst: Path, job):
    """Staging files named the way earlier versions named them (recover still finds them)."""
    h = hashlib.sha1(Path(dst).name.encode("utf-8", "surrogateescape")).hexdigest()[:12]
    return list(Path(dst).parent.glob(f".{Path(dst).name}{STAGE_TAG}{job}.part")) + \
        [p for p in [Path(dst).parent / f"{STAGE_TAG}{job}-{h}.part"] if os.path.lexists(p)]


def _same_file(a, b) -> bool:
    try:
        return os.path.samefile(a, b)
    except OSError:
        return False


def _case_only(a, b) -> bool:
    """a and b are one file under two spellings of one name (letter case or Unicode form on
    a filesystem that treats them alike), not two hard links with different names."""
    import unicodedata
    fa = unicodedata.normalize("NFC", str(a)).casefold()
    fb = unicodedata.normalize("NFC", str(b)).casefold()
    if str(a) == str(b) or fa != fb or not _same_file(a, b):
        return False
    try:                       # two entries listed: two hard links on a case-sensitive disk
        return not (Path(a).name in os.listdir(Path(a).parent) and Path(b).name in os.listdir(Path(b).parent))
    except OSError:
        return True


def _case_tmp(src: Path) -> Path:
    return src.parent / f"{STAGE_TAG}case-{hashlib.sha1(str(src).encode('utf-8', 'surrogateescape')).hexdigest()[:12]}"


def _rename_case(src: Path, dst: Path):
    """src -> dst where the two names differ only in letter case (or Unicode form) on a
    filesystem that treats them as one name: through a temporary name, as Picard does."""
    tmp = _case_tmp(src)
    os.rename(src, tmp)
    try:
        os.rename(tmp, dst)
    except OSError:
        os.rename(tmp, src)
        raise
    _fsync_dir(dst.parent)


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
    elif type(f).__name__ in ("OggVorbis", "OggOpus", "OggFLAC"):
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
    if fam in ("id3", "mp4", "vorbis") and src.suffix.lower() not in (".wav", ".aif", ".aiff", ".dsf", ".dff"):
        return {"kind": "tags", "rec": ta.backup_tags(src, blobs)}
    files = jd / "files"
    files.mkdir(exist_ok=True)
    dest = files / f"{n}{src.suffix}"
    _copy_fsync(src, dest)
    return {"kind": "file", "path": str(dest)}


def _restore(path: Path, backup: dict, jd: Path):
    """The file's tags (or the whole file) as backed up, through a staging copy that keeps
    the file's owner: a crash never leaves a half-written file."""
    import tag_albums as ta
    if backup.get("kind") == "tags" and _tags_sig_backup_free(path, backup):
        return                                    # the tags are as backed up: nothing to write
    stage = _stage_name(path, "undo-tags")
    st = os.stat(path)
    try:
        if backup["kind"] == "tags":
            _copy_fsync(path, stage)
            before = payload.digest(path)
            ta.restore_tags(stage, backup["rec"], jd / "blobs")
            if before is not None and payload.digest(stage) != before:
                raise StepError(f"{path.name}: audio data differed after restoring tags; left as it is")
            with open(stage, "rb+") as fh:
                os.fsync(fh.fileno())
        else:
            _copy_fsync(Path(backup["path"]), stage)
        _keep_owner(stage, st)
        os.replace(stage, path)
    except BaseException:
        stage.unlink(missing_ok=True)
        raise
    _fsync_dir(path.parent)


def _restore_text(saved: Path, path: Path):
    """A companion's saved original back in place of its rewritten text."""
    stage = _stage_name(path, "undo-put")
    try:
        _copy_fsync(saved, stage)
        if path.exists():
            _keep_owner(stage, os.stat(path))
        os.replace(stage, path)
    except BaseException:
        stage.unlink(missing_ok=True)
        raise


def _put_back(data_from: Path, path: Path, owner_of=None):
    """A copy of data_from at path (which must not exist), through a staging copy."""
    stage = _stage_name(path, "undo-put")
    try:
        _copy_fsync(data_from, stage)
        if owner_of is not None:
            _keep_owner(stage, owner_of)
        _place(stage, path)
    except BaseException:
        stage.unlink(missing_ok=True)
        raise


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
        self.created = []
        self.preserve_times = bool(plan.get("preserve_timestamps"))

    def run(self) -> dict:
        with job_lock(self.job):
            return self._run()

    def _run(self) -> dict:
        others = [j for j in pending_jobs() if j != self.job]
        if others:
            busy = busy_jobs(but=self.job)
            raise StepError(f"job {busy[0]} is running in another TagMender process; try again when it ends"
                            if busy else f"job {others[0]} was interrupted; run `tagmender recover` first")
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
            try:
                status, label, detail = self.album(i, a)
            except Exception as exc:                  # never one album's surprise ends the job
                self.log.exception("album %s could not be started", a.get("source_dir"))
                status, label, detail = "conflict", "Unavailable", f"could not be started: {exc}"
            counts[status] = counts.get(status, 0) + 1
            self.results.append({"album": a["source_dir"], "status": status, "label": label, "detail": detail})
            _record_album(self.job, a["source_dir"], status, label, detail)
        _sweep_stages(self.plan, self.job, self.log)
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
        # every file still as planned, destination still there (the same share, still mounted)
        droot = a.get("dest_root")
        if droot:
            try:
                same = os.stat(droot["path"]).st_dev == droot["dev"]
            except OSError:
                same = False
            if not same:
                return "conflict", "Unavailable", f"destination {droot['path']} is not the one planned (unmounted?)"
        for fe in a["files"] + a["companions"]:
            fp = fe["fingerprint"]
            try:
                st = os.stat(fe["src"])
            except FileNotFoundError:
                return "conflict", "Changed", f"{fe['src']} is gone since the plan was made"
            except OSError as exc:                       # a stale share handle, permissions, I/O
                return "conflict", "Unavailable", f"{fe['src']} cannot be read now ({exc.strerror})"
            # an edit that kept size and time still changes the change time (audio files only:
            # a companion hard-linked elsewhere changes it when another album moves; inode
            # numbers are left out, some shares do not keep them)
            changed = (st.st_size, st.st_mtime_ns) != (fp["size"], fp["mtime_ns"]) or \
                (fe in a["files"] and "ctime_ns" in fp and st.st_ctime_ns != fp["ctime_ns"])
            if changed:
                return "conflict", "Changed", f"{Path(fe['src']).name} changed since the plan was made"
        self.journal.write(ev="album_begin", album=i, source=a["source_dir"])
        done_steps = []
        self.created = []
        try:
            for j, fe in enumerate(a["files"]):
                if fe["op"] == "none" or Path(fe["src"]) in dec.excluded:
                    continue
                self.file_step(i, j, fe, a, done_steps)
            if dec.excluded and a["companions"]:
                self.journal.write(ev="note", album=i, text="a file of the album is excluded now; "
                                                            "its companion files stay where they are")
            else:
                for j, ce in enumerate(a["companions"]):
                    self.companion_step(i, j, ce, done_steps)
            if a.get("artwork") and a["artwork"].get("file"):
                self.cover_file(i, a, done_steps)
            for sc in a.get("sidecars") or []:
                if Path(sc["src"]) not in dec.excluded:
                    self.new_file(i, sc, done_steps)
            if (self.plan["operation"] == "move" or any(fe["op"] == "move" for fe in a["files"])) and \
                    (self.plan.get("profile_data") or {}).get("remove_empty_dirs", True):
                self.cleanup_dirs(i, a)
        except Exception as exc:                                  # roll the album back, then carry on
            self.log.error("album %s failed: %s", a["source_dir"], exc)
            self.journal.write(ev="album_failed", album=i, error=str(exc))
            problems = self.rollback(done_steps)
            for d in reversed(self.created):                  # folders made for this album, if empty
                try:
                    d.rmdir()
                except OSError:
                    pass
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
        _check_target(dst, self.roots)
        if op == "tag" and Path(src).is_symlink():
            raise StepError(f"refusing to write through a symbolic link: {src}")
        with file_lock(src):
            src_digest = payload.digest(src)
            if src_digest is None and (changes or embed) and not self.allow_unverified:
                raise StepError(f"{src.name}: its audio data cannot be verified after writing tags "
                                "(format not recognised and ffmpeg unavailable); not written")
            n = f"{i}-{j}"
            linked = bool(fe.get("linked")) and dst.exists() and _same_file(src, dst)
            if linked:
                op = "relink_" + op                       # dst is already a hard link to src
            backup = _backup(src, self.jd, n) if op in ("tag", "move", "relink_move", "relink_copy") else None
            self.journal.write(ev="file", album=i, idx=j, op=op, src=str(src), dst=str(dst), state="begin",
                               digest=src_digest, backup=backup, changed=bool(changes or embed))
            if linked:
                self._relink_step(i, j, op, src, dst, changes, embed, art, backup, src_digest, done_steps)
                return
            if op == "move" and not changes and not embed and _same_fs(src, dst.parent if dst.parent.exists()
                                                                       else self._existing(dst.parent)):
                _make_dirs(dst.parent, self.roots, self.journal, i, self.created)
                _place_move(src, dst)                     # same filesystem: a rename, nothing copied
                self.journal.write(ev="file", album=i, idx=j, state="committed", moved_by="rename",
                                   tags_sig=_tags_sig(dst))
                self.journal.write(ev="file", album=i, idx=j, state="source_removed")
                done_steps.append(("file", i, j, op, str(src), str(dst), backup, src_digest))
                return
            if op in ("copy", "move"):
                _make_dirs(dst.parent, self.roots, self.journal, i, self.created)
            stage = _stage_name(dst, self.job)
            _space_for(dst.parent, os.path.getsize(src))
            try:
                _copy_fsync(src, stage)
                if src_digest is None and not changes and not embed and \
                        payload.file_sha256(stage) != payload.file_sha256(src):
                    raise StepError(f"{src.name}: copy did not verify")
                src_st = os.stat(src)
                _keep_owner(stage, src_st)
                if changes:
                    tagio.write(stage, changes)
                if embed:
                    embed_front(stage, Path(art["front"]).read_bytes(),
                                "image/png" if str(art["front"]).lower().endswith(".png") else "image/jpeg")
                if self.preserve_times:
                    os.utime(stage, ns=(src_st.st_atime_ns, src_st.st_mtime_ns))
                after = payload.digest(stage)
                if src_digest is not None and after != src_digest:
                    raise StepError(f"{src.name}: audio data differed after writing tags; original kept")
                got = tagio.read(stage)
                for k, v in changes.items():
                    if (got.get(k) or []) != (v or []):
                        raise StepError(f"{src.name}: tag {k} did not read back as written")
                with open(stage, "rb+") as fh:
                    os.fsync(fh.fileno())
            except BaseException:
                stage.unlink(missing_ok=True)
                raise
            self.journal.write(ev="file", album=i, idx=j, state="staged", stage=str(stage))
            case_only = op == "move" and _case_only(src, dst)
            if op == "tag" or case_only:
                try:
                    os.replace(stage, src)
                except BaseException:
                    stage.unlink(missing_ok=True)
                    raise
                _fsync_dir(src.parent)
                if case_only:
                    done_steps.append(("file", i, j, "tag", str(src), str(src), backup, src_digest))
                    _rename_case(src, dst)
                    done_steps.pop()
            else:
                try:
                    _place(stage, dst)
                except Exception:
                    self.journal.write(ev="file", album=i, idx=j, state="place_failed")
                    stage.unlink(missing_ok=True)
                    raise
            st = os.stat(dst)
            self.journal.write(ev="file", album=i, idx=j, state="committed", size=st.st_size,
                               mtime_ns=st.st_mtime_ns, tags_sig=_tags_sig(dst))
            done_steps.append(("file", i, j, op, str(src), str(dst), backup, src_digest))
            if op == "copy":
                _record_copy(src, dst, self.job)
            if op == "move" and not case_only:
                if src_digest is not None and payload.digest(dst) != src_digest:
                    raise StepError(f"{dst.name}: new file did not verify; source kept")
                os.unlink(src)
                _fsync_dir(src.parent)
                self.journal.write(ev="file", album=i, idx=j, state="source_removed")

    def _relink_step(self, i, j, op, src, dst, changes, embed, art, backup, src_digest, done_steps):
        """The destination already is this file under another name (a hard link). A move
        just drops the source's name; new tags go to the destination as a new file."""
        if changes or embed:
            stage = _stage_name(dst, self.job)
            try:
                _copy_fsync(src, stage)
                _keep_owner(stage, os.stat(src))
                if changes:
                    tagio.write(stage, changes)
                if embed:
                    embed_front(stage, Path(art["front"]).read_bytes(),
                                "image/png" if str(art["front"]).lower().endswith(".png") else "image/jpeg")
                if self.preserve_times:
                    st0 = os.stat(src)
                    os.utime(stage, ns=(st0.st_atime_ns, st0.st_mtime_ns))
                if src_digest is not None and payload.digest(stage) != src_digest:
                    raise StepError(f"{src.name}: audio data differed after writing tags; original kept")
                got = tagio.read(stage)
                for k, v in changes.items():
                    if (got.get(k) or []) != (v or []):
                        raise StepError(f"{src.name}: tag {k} did not read back as written")
                with open(stage, "rb+") as fh:
                    os.fsync(fh.fileno())
                self.journal.write(ev="file", album=i, idx=j, state="staged", stage=str(stage))
                os.replace(stage, dst)
            except BaseException:
                stage.unlink(missing_ok=True)
                raise
            _fsync_dir(dst.parent)
        st = os.stat(dst)
        self.journal.write(ev="file", album=i, idx=j, state="committed", size=st.st_size,
                           mtime_ns=st.st_mtime_ns, tags_sig=_tags_sig(dst))
        done_steps.append(("file", i, j, op, str(src), str(dst), backup, src_digest))
        if op == "relink_copy":
            _record_copy(src, dst, self.job)
        if op == "relink_move":
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
        if op == "rewrite":                       # a cue sheet that stays, naming renamed files
            stage = _stage_name(src, self.job)
            self.journal.write(ev="companion", album=i, idx=j, op=op, src=str(src), dst=str(dst), state="begin")
            try:
                saved = self._stage_companion(i, j, ce, src, dst, stage)
                _keep_owner(stage, os.stat(src))
            except BaseException:
                stage.unlink(missing_ok=True)
                raise
            self.journal.write(ev="companion", album=i, idx=j, op=op, src=str(src), dst=str(dst), state="staged",
                               saved=str(saved))
            try:
                os.replace(stage, src)
            except BaseException:
                stage.unlink(missing_ok=True)
                raise
            _fsync_dir(src.parent)
            st = os.stat(src)
            self.journal.write(ev="companion", album=i, idx=j, state="committed", size=st.st_size,
                               mtime_ns=st.st_mtime_ns)
            done_steps.append(("companion", i, j, op, str(src), str(dst), str(saved), None))
            return
        if op == "move" and _case_only(src, dst):         # "01 a.lrc" -> "01 A.lrc" on a share
            saved = None
            if ce.get("rewrite"):
                saved = self.jd / "companions" / f"{i}-{j}{src.suffix}"
                saved.parent.mkdir(exist_ok=True)
                _copy_fsync(src, saved)
            self.journal.write(ev="companion", album=i, idx=j, op=op, src=str(src), dst=str(dst), state="begin",
                               saved=str(saved) if saved else None)
            if ce.get("rewrite"):
                stage = _stage_name(src, self.job)
                try:
                    with open(stage, "x", encoding=ce["rewrite"]["encoding"], newline="") as fh:
                        fh.write(ce["rewrite"]["text"])
                        fh.flush()
                        os.fsync(fh.fileno())
                    shutil.copystat(src, stage)
                    _keep_owner(stage, os.stat(src))
                    os.replace(stage, src)
                except BaseException:
                    stage.unlink(missing_ok=True)
                    raise
            try:
                _rename_case(src, dst)
            except BaseException:
                if saved:
                    _restore_text(saved, src)
                raise
            st = os.stat(dst)
            self.journal.write(ev="companion", album=i, idx=j, state="committed", size=st.st_size,
                               mtime_ns=st.st_mtime_ns)
            self.journal.write(ev="companion", album=i, idx=j, state="source_removed")
            done_steps.append(("companion", i, j, op, str(src), str(dst), str(saved) if saved else None, None))
            return
        _make_dirs(dst.parent, self.roots, self.journal, i, self.created)
        stage = _stage_name(dst, self.job)
        saved = None
        self.journal.write(ev="companion", album=i, idx=j, op=op, src=str(src), dst=str(dst), state="begin")
        try:
            saved = self._stage_companion(i, j, ce, src, dst, stage)
        except BaseException:
            stage.unlink(missing_ok=True)
            raise
        self.journal.write(ev="companion", album=i, idx=j, op=op, src=str(src), dst=str(dst), state="staged",
                           saved=str(saved) if saved else None)
        try:
            _place(stage, dst)
        except Exception:
            self.journal.write(ev="companion", album=i, idx=j, state="place_failed")
            if stage.exists() and not (dst.exists() and _same_file(stage, dst)):
                stage.unlink()
            raise
        st = os.stat(dst)
        self.journal.write(ev="companion", album=i, idx=j, state="committed", size=st.st_size,
                           mtime_ns=st.st_mtime_ns)
        done_steps.append(("companion", i, j, op, str(src), str(dst), saved, None))
        if op == "move":
            os.unlink(src)
            self.journal.write(ev="companion", album=i, idx=j, state="source_removed")

    def _stage_companion(self, i, j, ce, src, dst, stage):
        saved = None
        if ce.get("rewrite"):
            saved = self.jd / "companions" / f"{i}-{j}{src.suffix}"
            saved.parent.mkdir(exist_ok=True)
            _copy_fsync(src, saved)
            with open(stage, "w", encoding=ce["rewrite"]["encoding"], newline="") as fh:
                fh.write(ce["rewrite"]["text"])
                fh.flush()
                os.fsync(fh.fileno())
            shutil.copystat(src, stage)
        else:
            _space_for(dst.parent, os.path.getsize(src))
            _copy_fsync(src, stage)
            if payload.file_sha256(stage) != payload.file_sha256(src):
                raise StepError(f"{src.name}: copy did not verify")
        _keep_owner(stage, os.stat(src))
        return saved

    def cover_file(self, i, a, done_steps):
        dst = Path(a["artwork"]["file"])
        _check_target(dst, self.roots)
        if dst.exists():
            return
        front = Path(a["artwork"]["front"])
        self.journal.write(ev="create_begin", kind="cover_file", album=i, path=str(dst),
                           sha256=payload.file_sha256(front))
        stage = _stage_name(dst, self.job)
        try:
            _copy_fsync(front, stage)
        except BaseException:
            stage.unlink(missing_ok=True)
            raise
        _place_staged(stage, dst)
        st = os.stat(dst)
        self.journal.write(ev="cover_file", album=i, path=str(dst), size=st.st_size, mtime_ns=st.st_mtime_ns)
        done_steps.append(("cover", i, 0, "create", "", str(dst), None, None))

    def new_file(self, i, sc, done_steps):
        """A new text file next to the music (time-synced lyrics); never over an existing one."""
        dst = Path(sc["dst"])
        _check_target(dst, self.roots)
        if dst.exists():
            return
        data = sc["text"].encode("utf-8")
        self.journal.write(ev="create_begin", kind="new_file", album=i, path=str(dst),
                           sha256=hashlib.sha256(data).hexdigest())
        stage = _stage_name(dst, self.job)
        try:
            with open(stage, "wb") as fh:
                fh.write(data)
                fh.flush()
                os.fsync(fh.fileno())
        except BaseException:
            stage.unlink(missing_ok=True)
            raise
        _place_staged(stage, dst)
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
                _undo_step(step, self.jd, rolling_back=True)
                key = ["created", step[5]] if step[0] == "cover" else [step[0], step[1], step[2]]
                self.journal.write(ev="rollback_step", key=key)
            except Exception as exc:
                problems.append(str(exc))
        return problems


def _sweep_stages(plan: dict, job, log=None) -> list:
    """Staging copies of this job that a failed clean-up (a share dropping for a moment)
    left behind: removed, best effort."""
    out = []
    for a in plan.get("albums") or []:
        paths = set()
        for x in (a.get("files") or []) + (a.get("companions") or []) + (a.get("sidecars") or []):
            paths.update({x.get("src"), x.get("dst")})
        if (a.get("artwork") or {}).get("file"):
            paths.add(a["artwork"]["file"])
        for p in filter(None, paths):
            s = _stage_name(Path(p), job)
            try:
                if os.path.lexists(s):
                    s.unlink()
                    out.append(str(s))
            except OSError as exc:
                if log:
                    log.warning("could not remove staging copy %s: %s", s, exc)
    return out


def _same_fs(a: Path, b: Path) -> bool:
    try:
        return os.stat(a).st_dev == os.stat(b).st_dev
    except OSError:
        return False


def _place_move(src: Path, dst: Path):
    if _case_only(src, dst):                              # only the letter case changes
        _rename_case(src, dst)
        _fsync_dir(src.parent)
        return
    _link_then_unlink(src, dst, "exists")
    _fsync_dir(dst.parent)
    _fsync_dir(src.parent)


def _undo_step(step, jd, rolling_back=False):
    kind, _i, _j, op, src, dst, extra, digest = step
    src, dst = Path(src) if src else None, Path(dst)
    if kind == "cover":
        dst.unlink(missing_ok=True)
        return
    if kind == "companion":
        if op == "rewrite":                             # a sheet rewritten where it was
            stage = _stage_name(src, "undo-put")
            try:
                _copy_fsync(Path(extra), stage)
                if src.exists():
                    _keep_owner(stage, os.stat(src))
                os.replace(stage, src)
            except BaseException:
                stage.unlink(missing_ok=True)
                raise
            return
        if op == "copy":
            dst.unlink(missing_ok=True)
            return
        original = Path(extra) if extra else dst       # what belongs back at src
        if src.exists() and _case_only(src, dst):       # a case-only rename on a share
            _rename_case(dst, src)
            if extra:
                _restore_text(Path(extra), src)
            return
        if src.exists() and dst.exists() and _same_file(src, dst) and not _case_only(src, dst):
            dst.unlink()                                # linked back, not yet unlinked: finish
            return
        if src.exists():
            if dst.exists() and not _same_file(src, dst) and \
                    (rolling_back or payload.file_sha256(src) == payload.file_sha256(original)):
                dst.unlink()                            # the source is (back) in place already
                return
            if rolling_back or not dst.exists():
                return
            raise StepError(f"cannot move back: {src} exists")
        src.parent.mkdir(parents=True, exist_ok=True)
        if not extra and _same_fs(dst, src.parent):
            _place_move(dst, src)
        else:
            _put_back(original, src, os.stat(dst) if dst.exists() else None)
            dst.unlink(missing_ok=True)
        return
    backup = extra
    if op in ("relink_move", "relink_copy"):
        _undo_relink(src, dst, backup, digest, jd)
        return
    if op == "tag":
        _restore(src, backup, jd)
    elif op == "copy":
        dst.unlink(missing_ok=True)
        _record_copy(src, dst, None, remove=True)
    elif op == "move":
        if src.exists() and dst.exists() and _same_file(src, dst) and not _case_only(src, dst):
            dst.unlink()                                 # linked back, not yet unlinked: finish
            if backup:
                _restore(src, backup, jd)
            return
        if src.exists() and not dst.exists() and (digest is None or payload.digest(src) == digest):
            if backup:                                   # moved back before an interruption
                _restore(src, backup, jd)
            return
        if src.exists() and dst.exists() and not _same_file(src, dst) and \
                payload.file_sha256(src) == payload.file_sha256(dst):
            dst.unlink()                                 # copied back before an interruption
            if backup:
                _restore(src, backup, jd)
            return
        if src.exists() and _case_only(src, dst):                     # a case-only rename
            _rename_case(dst, src)
            if backup:
                _restore(src, backup, jd)
            return
        if src.exists():
            if rolling_back and dst.exists() and not _same_file(src, dst) and \
                    (digest is None or payload.digest(src) == digest):
                dst.unlink()                                 # the source was never removed
                return
            raise StepError(f"cannot move back: {src} exists")
        src.parent.mkdir(parents=True, exist_ok=True)
        if _same_fs(dst, src.parent):
            _place_move(dst, src)
        else:
            _put_back(dst, src, os.stat(dst))
            dst.unlink()
        if backup:
            _restore(src, backup, jd)


def _undo_relink(src: Path, dst: Path, backup, digest, jd):
    """Before the job src and dst were one file under two names: make them so again, with
    the old tags."""
    if not src.exists():
        if dst.exists() and _tags_sig_backup_free(dst, backup):
            os.link(dst, src)                       # nothing was written: give src its name back
            return
        src.parent.mkdir(parents=True, exist_ok=True)
        _put_back(dst, src, os.stat(dst))
        if backup:
            _restore(src, backup, jd)
    elif digest is not None and payload.digest(src) != digest:
        raise StepError(f"{src} holds different audio now; left alone")
    elif backup and not _tags_sig_backup_free(src, backup):
        _restore(src, backup, jd)                    # an earlier, interrupted undo got this far
    if dst.exists() and _same_file(src, dst):
        return
    stage = _stage_name(dst, "undo-link")
    if os.path.lexists(stage):
        os.unlink(stage)
    os.link(src, stage)
    try:
        os.replace(stage, dst)
    except BaseException:
        stage.unlink(missing_ok=True)
        raise


def _tags_sig_backup_free(dst: Path, backup) -> bool:
    """True when dst still is the very file the job started from (no tags were written)."""
    if not backup or backup.get("kind") != "tags":
        return False
    try:
        return _same_backup(dst, backup)
    except Exception:
        return False


def _same_backup(dst: Path, backup) -> bool:
    import tempfile
    import tag_albums as ta
    with tempfile.TemporaryDirectory() as d:
        return ta.backup_tags(dst, d) == backup["rec"]


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
    """Bring an interrupted job to a clean state. Returns what was done, in words.

    Albums are never left half-done: an album that was in progress when the job stopped,
    or whose rollback was cut short, is put back as it was before the job (run the plan
    again afterwards). To know what to put back, the files say how far each step got: a
    destination that is the staging copy (linked, staging name not yet removed), or a
    staging copy gone with the destination there, was committed. Cover and lyrics files
    count as the job's only when they hold exactly what it was writing. Staging copies,
    leftovers of an interrupted undo and folders made for nothing are removed. For a job
    that ended, only stray staging copies are looked for."""
    jd = job_dir(job)
    journal = Journal(jd)
    if not journal.read():
        raise StepError(f"no job {job}")
    with job_lock(job):
        events = journal.read()
        if any(e.get("ev") in ("end", "recovered") for e in events):
            swept = []
            try:
                swept = _sweep_stages(json.loads((jd / "plan.json").read_text(encoding="utf-8")), job)
            except (OSError, ValueError):
                pass
            return [f"removed unfinished copy {s}" for s in swept] or \
                [f"job {job} finished normally; nothing to recover"]
        return _recover(job, jd, journal, events)


def _clear_undo_leftovers(files: dict, comps: dict) -> list:
    """Case-only renames cut short (forwards, or backwards by an undo or rollback) and the
    staging files of an undo or rollback that was cut short: put right before anything
    else touches these files."""
    done = []
    for f in list(files.values()) + list(comps.values()):
        if "src" not in f or "dst" not in f:
            continue
        src, dst = Path(f["src"]), Path(f["dst"])
        if _case_tmp(src).exists() and not src.exists():
            os.rename(_case_tmp(src), src)
            done.append(f"{src.name}: case-only rename undone")
        if _case_tmp(dst).exists() and not dst.exists() and not src.exists():
            os.rename(_case_tmp(dst), dst)
            done.append(f"{dst.name}: case-only rename put back where the undo found it")
        for p in {src, dst}:
            for tag in ("undo-put", "undo-tags", "undo-link", "undo"):
                s = _stage_name(p, tag)
                if os.path.lexists(s):
                    s.unlink()
                    done.append(f"removed unfinished copy {s}")
    return done


def _recover(job, jd, journal, events):
    files, comps = _replay(events)
    failed = {e["album"] for e in events if e.get("ev") == "album_failed"}
    rolled = {e["album"] for e in events if e.get("ev") == "album_rolled_back"}
    finished = {e["album"] for e in events if e.get("ev") == "album_done"}
    begun = {e["album"] for e in events if e.get("ev") == "album_begin"}
    rolled_steps = {tuple(e["key"]) for e in events if e.get("ev") == "rollback_step"}
    done = []

    def stages(path):
        out = [_stage_name(Path(path), job)] + _old_stage_names(Path(path), job)
        return [s for s in out if s.exists()]

    def clear(*paths):
        """Remove the staging files of these paths: (one existed, one was a path itself)."""
        had = linked = False
        for path in dict.fromkeys(str(p) for p in paths):
            for s in stages(path):
                had = True
                if Path(path).exists() and _same_file(s, path):
                    linked = True
                else:
                    done.append(f"removed unfinished copy {s}")
                s.unlink()
        return had, linked

    # 1. case-only renames and undo/rollback staging files cut short; this job's staging files
    done += _clear_undo_leftovers(files, comps)
    fstate = {k: clear(f["dst"], f["src"]) for k, f in files.items() if "dst" in f}
    cstate = {k: clear(c["dst"], c["src"]) for k, c in comps.items() if "dst" in c}
    created = {e["path"]: e for e in events if e.get("ev") in ("cover_file", "new_file")}
    for e in events:
        if e.get("ev") == "create_begin":
            _had, linked = clear(e["path"])
            p = Path(e["path"])
            if e["path"] in created or not p.exists():
                continue
            if linked or (e.get("sha256") and payload.file_sha256(p) == e["sha256"]):
                st = os.stat(p)
                created[e["path"]] = {"ev": e["kind"], "album": e["album"], "path": str(p),
                                      "size": st.st_size, "mtime_ns": st.st_mtime_ns}
                journal.write(**created[e["path"]], recovered=True)

    # 2. what each step of an unfinished album had done; a file at the destination counts as
    #    the job's only when it holds what the job was writing there
    try:
        planned = json.loads((jd / "plan.json").read_text(encoding="utf-8"))["albums"]
    except (OSError, ValueError, KeyError):
        planned = []

    def ours_file(a, j, dst, digest):
        try:
            fe = planned[a]["files"][j]
        except (IndexError, KeyError, TypeError):
            return False
        if digest is not None and payload.digest(dst) != digest:
            return False
        try:
            got = tagio.read(dst)
        except Exception:
            return False
        return all((got.get(k) or []) == (v or []) for k, v in (fe.get("changes") or {}).items()
                   if v is not None)

    def ours_companion(a, j, src, dst):
        try:
            ce = planned[a]["companions"][j]
        except (IndexError, KeyError, TypeError):
            return False
        if ce.get("rewrite"):
            want = hashlib.sha256(ce["rewrite"]["text"].encode(ce["rewrite"]["encoding"])).hexdigest()
        else:
            want = payload.file_sha256(src) if src.exists() else None
        return want is not None and payload.file_sha256(dst) == want
    unfinished = (begun - finished - rolled) | (failed - rolled)
    for (a, j), f in sorted(files.items()):
        if a not in unfinished or f["state"] not in ("begin", "staged") or "dst" not in f:
            continue
        src, dst, op = Path(f["src"]), Path(f["dst"]), f.get("op")
        had, linked = fstate[(a, j)]
        staged = f["state"] == "staged"
        if op == "tag":
            committed = staged and not had                      # the replace had happened
        elif op in ("relink_move", "relink_copy"):
            committed = (staged and not had) or (op == "relink_move" and not src.exists())
        elif op == "move" and src != dst and src.exists() and dst.exists() and _same_file(src, dst) \
                and not _case_only(src, dst):
            committed = True                                    # rename-move: linked, not yet unlinked
        else:
            committed = linked or (staged and not had and dst.exists() and ours_file(a, j, dst, f.get("digest"))) \
                or (op == "move" and dst.exists() and not src.exists())
        if committed:
            journal.write(ev="file", album=a, idx=j, state="committed", recovered=True)
            f["state"] = "committed"
    for (a, j), c in sorted(comps.items()):
        if a not in unfinished or c["state"] not in ("begin", "staged") or "dst" not in c:
            continue
        had, linked = cstate[(a, j)]
        if c["state"] == "staged" and Path(c["dst"]).exists() and \
                (linked or (not had and ours_companion(a, j, Path(c["src"]), Path(c["dst"])))):
            journal.write(ev="companion", album=a, idx=j, state="committed", recovered=True)
            c["state"] = "committed"

    # 3. unfinished albums are put back as they were
    for a in sorted(unfinished):
        problems = []
        steps = [("file", a, j, f.get("op"), f["src"], f["dst"], f.get("backup"), f.get("digest"))
                 for (aa, j), f in files.items() if aa == a and f["state"] in ("committed", "source_removed")]
        steps += [("companion", a, j, c.get("op"), c["src"], c["dst"], c.get("saved"), None)
                  for (aa, j), c in comps.items() if aa == a and c["state"] in ("committed", "source_removed")]
        for step in sorted(steps, key=lambda s: (s[0] != "companion", s[2]), reverse=True):
            if (step[0], step[1], step[2]) in rolled_steps:
                continue
            try:
                _undo_step(step, jd, rolling_back=True)
                journal.write(ev="rollback_step", key=[step[0], step[1], step[2]])
            except Exception as exc:
                problems.append(f"{step[4]}: {exc}")
        for c in created.values():
            if c["album"] == a and ("created", c["path"]) not in rolled_steps:
                p = Path(c["path"])
                if p.exists() and (os.stat(p).st_size, os.stat(p).st_mtime_ns) == (c["size"], c["mtime_ns"]):
                    p.unlink()
                journal.write(ev="rollback_step", key=["created", c["path"]])
        for ev in reversed([x for x in events if x.get("ev") in ("dir_created", "dir_create_begin")
                            and x["album"] == a]):
            try:
                Path(ev["path"]).rmdir()
            except OSError:
                pass
        journal.write(ev="album_rolled_back", album=a, problems=problems, recovered=True)
        done.append(f"album {a} ({_album_name(jd, a)}): put back as it was before the job" +
                    (f" (problems: {problems})" if problems else "") + "; run the plan again to do it")

    # 4. folders whose creation was cut short
    made = {x["path"] for x in events if x.get("ev") == "dir_created"}
    for x in events:
        if x.get("ev") == "dir_create_begin" and x["path"] not in made:
            try:
                Path(x["path"]).rmdir()
            except OSError:
                pass
    journal.write(ev="recovered", actions=done)
    _record_job(job, ended=time.time(), status="recovered")
    return done or ["nothing was half-done"]


def _album_name(jd: Path, i: int) -> str:
    try:
        return json.loads((jd / "plan.json").read_text(encoding="utf-8"))["albums"][i]["source_dir"]
    except Exception:
        return "?"


def undo(job, force=False) -> list[str]:
    """Put back everything the job changed, newest first. Files changed since by someone
    else (different audio, or tags edited after the job) are left alone and reported;
    force=True puts tags back over later edits too. Each step is journalled as it is
    done, so an undo that was interrupted or met a problem can simply be run again."""
    jd = job_dir(job)
    journal = Journal(jd)
    if not journal.read():
        raise StepError(f"no job {job}")
    with job_lock(job):
        events = journal.read()
        if any(e.get("ev") == "undone" for e in events):
            raise StepError(f"job {job} was already undone")
        if not any(e.get("ev") in ("end", "recovered") for e in events):
            raise StepError(f"job {job} did not finish; run `tagmender recover {job}` first")
        if (jd / "PRUNED").exists():
            raise StepError(f"job {job} can no longer be undone: its backups were pruned on "
                            f"{(jd / 'PRUNED').read_text().strip()}")
        busy = busy_jobs(but=job)
        if busy:
            raise StepError(f"job {busy[0]} is running in another TagMender process; undo when it ends")
        return _undo(job, jd, journal, events, force)


def _undo(job, jd, journal, events, force):
    # albums put back completely by their rollback are skipped; where the rollback met a
    # problem, undo does the steps it did not finish
    rolled = {e["album"] for e in events if e.get("ev") == "album_rolled_back" and not e.get("problems")}
    rolled_steps = {tuple(e["key"]) for e in events if e.get("ev") == "rollback_step"}
    already = {tuple(e["key"]) for e in events if e.get("ev") == "undo_step"}
    files, comps = _replay(events)
    out, problems = [], []
    _clear_undo_leftovers(files, comps)                  # an earlier undo cut short

    def did(*key):
        journal.write(ev="undo_step", key=list(key))

    covers = [e for e in events if e.get("ev") in ("cover_file", "new_file") and e["album"] not in rolled]
    for e in reversed(covers):
        p = Path(e["path"])
        if ("created", str(p)) in already or ("created", str(p)) in rolled_steps:
            continue
        if p.exists() and (os.stat(p).st_size, os.stat(p).st_mtime_ns) == (e["size"], e["mtime_ns"]):
            p.unlink()
            out.append(f"removed {p}")
        elif p.exists():
            problems.append(f"{p} was changed after the job; left in place")
            continue
        did("created", str(p))
    for (a, j), c in sorted(comps.items(), reverse=True):
        if a in rolled or c["state"] not in ("committed", "source_removed") or ("companion", a, j) in already \
                or ("companion", a, j) in rolled_steps:
            continue
        cdst = Path(c["dst"])
        if c.get("size") is not None and cdst.exists() and not force and \
                (os.stat(cdst).st_size, os.stat(cdst).st_mtime_ns) != (c["size"], c["mtime_ns"]):
            problems.append(f"{cdst} was changed after the job; left as it is "
                            f"(tagmender undo --force {job} puts it back anyway)")
            continue
        try:
            _undo_step(("companion", a, j, c["op"], c["src"], c["dst"], c.get("saved"), None), jd)
            out.append(f"put back {Path(c['src']).name}")
            did("companion", a, j)
        except Exception as exc:
            problems.append(f"{c['src']}: {exc}")
    removed = [e for e in events if e.get("ev") == "dir_removed"]
    for e in removed:
        Path(e["path"]).mkdir(parents=True, exist_ok=True)
    for (a, j), f in sorted(files.items(), reverse=True):
        if a in rolled or f["state"] not in ("committed", "source_removed") or ("file", a, j) in already \
                or ("file", a, j) in rolled_steps:
            continue
        dst = Path(f["dst"])
        if f.get("op") == "copy" and dst.exists() and f.get("size") is not None and \
                (os.stat(dst).st_size, os.stat(dst).st_mtime_ns) != (f["size"], f["mtime_ns"]):
            problems.append(f"{dst} was changed after the job; left in place")
            continue
        check = Path(f["src"]) if f.get("op") == "tag" else dst
        if not check.exists() and f.get("op") == "move" and Path(f["src"]).exists():
            check = Path(f["src"])                      # an interrupted undo had moved it back
        if f.get("digest") and check.exists() and payload.digest(check) != f["digest"]:
            problems.append(f"{check} holds different audio now; left alone")
            continue
        if f.get("op") in ("tag", "move", "relink_move", "relink_copy") and f.get("tags_sig") and check == (Path(f["src"]) if f["op"] == "tag"
                                                                                 else dst) \
                and check.exists() and _tags_sig(check) != f["tags_sig"] and not force:
            problems.append(f"{check}: its tags were edited after the job; left as they are "
                            f"(tagmender undo --force {job} puts the old tags back anyway)")
            continue
        try:
            with file_lock(check if check.exists() else Path(f["src"])):
                _undo_step(("file", a, j, f["op"], f["src"], f["dst"], f.get("backup"), f.get("digest")), jd)
            out.append(f"put back {Path(f['src']).name}")
            did("file", a, j)
        except Exception as exc:
            problems.append(f"{f['src']}: {exc}")
    for e in reversed([e for e in events if e.get("ev") == "dir_created"]):
        p = Path(e["path"])
        try:
            p.rmdir()
        except OSError as exc:
            if exc.errno not in (errno.ENOTEMPTY, errno.EEXIST, errno.ENOENT):
                problems.append(f"{p}: could not remove the folder the job made ({exc.strerror})")
    if problems:
        journal.write(ev="undo_partial", restored=len(out), problems=problems)
        _record_job(job, status="undone with problems (run undo again when they are fixed)")
    else:
        journal.write(ev="undone", restored=len(out), problems=[])
        _record_job(job, status="undone")
    return out + [f"PROBLEM: {p}" for p in problems]


def prune(days: int) -> list[str]:
    """Delete the backups of finished jobs older than DAYS (their undo goes with them)."""
    out = []
    cutoff = time.time() - days * 86400
    if not jobs_dir().is_dir():
        return out
    for d in jobs_dir().iterdir():
        try:
            with job_lock(d.name):
                ev = Journal(d).read()
                if not ev or not any(e.get("ev") in ("end", "recovered", "undone") for e in ev):
                    continue
                if ev[-1]["t"] < cutoff and not (d / "PRUNED").exists():
                    for sub in ("blobs", "files", "companions"):
                        shutil.rmtree(d / sub, ignore_errors=True)
                    (d / "PRUNED").write_text(time.strftime("%Y-%m-%d %H:%M"))
                    out.append(d.name)
        except StepError:                                      # in use: next time
            continue
    return out
