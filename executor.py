"""executor.py - carries out a plan safely, keeps a journal, recovers, and undoes.

Every job has a folder, ~/.local/state/tagmender/jobs/<job>/, holding the plan, an
append-only journal (journal.jsonl, each line fsync'ed before the step it announces
continues), and the backups undo needs. Rotating logs never touch it.

How a file is changed - never in place:
  1. The source is checked against the plan (size, modification time): a file changed
     since planning is not touched, and its album is skipped.
  2. The source is copied to a hidden staging file next to its destination
     (.tm-<job>-<hash><ext>.part), after checking there is room for it.
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
import glob
import hashlib
import json
import os
import shutil
import sqlite3
import stat
import time
from contextlib import contextmanager
from pathlib import Path

import exclusions as ex
import logsetup
import payload
import plan as planmod
import tagio

STAGE_TAG = ".tm-"
JUNK = {".DS_Store", "desktop.ini", "Desktop.ini", "Thumbs.db"}   # picard/util/emptydir.py JUNK_FILES
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
        # ASCII (non-ASCII letters as \\u escapes): a line cut by a power loss can never end
        # inside a letter, and a file name that is not valid UTF-8 (surrogate escapes) is kept
        line = json.dumps(ev, ensure_ascii=True, default=str) + "\n"
        with open(self.path, "a+b") as fh:
            if fh.tell() > 0:                    # a line cut short by a crash: start a new one
                fh.seek(-1, os.SEEK_END)
                if fh.read(1) != b"\n":
                    line = "\n" + line
            fh.write(line.encode("utf-8"))
            fh.flush()
            os.fsync(fh.fileno())
        if ev.get("ev") in ("end", "recovered"):
            _mark_ended(self.path.parent)

    def read(self):
        if not self.path.exists():
            return []
        out = []
        for line in self.path.read_bytes().splitlines():
            try:
                out.append(json.loads(line.decode("utf-8")))
            except ValueError:                     # (UnicodeDecodeError too: cut inside a letter)
                pass                               # a line cut short by a crash
        return out


def busy_jobs(but=None) -> list[str]:
    """Unfinished jobs another process is running now (their job lock is held)."""
    out = []
    for j in pending_jobs():
        if j == but:
            continue
        try:
            with job_lock(j, active=False):
                pass
        except StepError:
            out.append(j)
    return out


ENDED = "ENDED"                  # in a job folder: its journal has its end (read no further)


def pending_jobs() -> list[str]:
    """Jobs whose journal has no end (interrupted, or running now). A finished job's folder
    is marked ENDED, so its journal - large for a library-wide job - is read once, not on
    every look (the watcher looks before every pass and every job)."""
    out = []
    if not jobs_dir().is_dir():
        return out
    for d in sorted(jobs_dir().iterdir()):
        if (d / ENDED).exists() or not d.is_dir():
            continue
        j = Journal(d).read()
        if j and not any(e.get("ev") in ("end", "recovered") for e in j):
            out.append(d.name)
        elif j:
            _mark_ended(d)
    return out


def _mark_ended(d: Path):
    try:
        (d / ENDED).touch()
    except OSError:
        pass


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


db_text = planmod.db_text       # a path as the state database can store it


def _shown(s) -> str:
    """Text for the state database to show (odd bytes of a file name as \\x.. escapes)."""
    return str(s).encode("utf-8", "surrogateescape").decode("utf-8", "backslashreplace")


def _record_album(job, album, status, label="", detail=""):
    db = _db()
    db.execute("INSERT INTO job_albums VALUES (?,?,?,?,?,?)",
               (job, db_text(album), status, _shown(label), _shown(detail), time.time()))
    db.commit()
    db.close()


def _record_copy(src, dst, job, remove=False):
    """Remember where a copy profile put each file (planning finds earlier copies by it)."""
    try:
        db = _db()
        if remove:
            db.execute("DELETE FROM copies WHERE src=? AND dst=?", (db_text(src), db_text(dst)))
        else:
            db.execute("INSERT OR REPLACE INTO copies VALUES (?,?,?,?)", (db_text(src), db_text(dst), job,
                                                                         time.time()))
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
    key = hashlib.sha1(os.path.realpath(str(path)).encode("utf-8", "surrogateescape")).hexdigest()
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
def job_lock(job, active=True):
    """One process at a time per job: applying, recovering or undoing it. While held, a shared
    lock on STATE_DIR/jobs.active says "a job is changing files" (job_running); active=False
    for a look that changes nothing (busy_jobs, prune)."""
    jd = job_dir(job)
    jd.mkdir(parents=True, exist_ok=True)
    with open(jd / ".lock", "w") as fh:
        try:
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise StepError(f"job {job} is being run by another TagMender process") from None
        try:
            if not active:
                yield
                return
            with open(ex.STATE_DIR / "jobs.active", "a") as act:
                fcntl.flock(act, fcntl.LOCK_SH)
                try:
                    yield
                finally:
                    fcntl.flock(act, fcntl.LOCK_UN)
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


def job_running() -> bool:
    """A job (apply, undo, recover) is changing files now, in any TagMender process or thread."""
    try:
        ex.STATE_DIR.mkdir(parents=True, exist_ok=True)
        with open(ex.STATE_DIR / "jobs.active", "a") as fh:
            try:
                fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return True
            fcntl.flock(fh, fcntl.LOCK_UN)
            return False
    except OSError:
        return False


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


def _like_parent(m: Path):
    """A folder just made takes the owner, group and mode of the folder it is in: a root job
    on a share (Unraid: nobody:users, 2775) makes no folder the share's users cannot use."""
    try:
        pst = os.stat(m.parent)
        _keep_owner(m, pst)
        os.chmod(m, stat.S_IMODE(pst.st_mode) & ~stat.S_ISVTX)
    except OSError:
        pass


def mkdirs_like_parent(d: Path):
    """d and the folders above it that are missing, each like its parent (undo, rollback
    and recover put removed folders back this way)."""
    d = Path(d)
    missing = []
    p = d
    while not os.path.lexists(p) and p != p.parent:
        missing.append(p)
        p = p.parent
    for m in reversed(missing):
        try:
            m.mkdir()
        except FileExistsError:
            continue
        _like_parent(m)


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
        _like_parent(m)
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
                                          for k, v in t.items()), ensure_ascii=False)
                        .encode("utf-8", "surrogatepass")).hexdigest()


def _pics_sig(path) -> str:
    """A fingerprint of a file's embedded pictures (type and content): undo leaves a cover
    the user put in after the job alone (the tags signature only counts pictures)."""
    try:
        import artwork
        pics = artwork._pictures(path)
    except Exception:
        return ""
    return hashlib.sha1(json.dumps([(t, hashlib.sha1(d).hexdigest()) for t, d in pics]).encode()).hexdigest()


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
    # the extension inside (how tagio.mutagen_file tells formats), ".part" last: no music
    # player, tagger or lister takes a staging copy for a track or a cover
    return Path(dst).parent / f"{STAGE_TAG}{job}-{h}{ext}.part"


def _old_stage_names(dst: Path, job):
    """Staging files named the way earlier versions named them (recover still finds them)."""
    h = hashlib.sha1(Path(dst).name.encode("utf-8", "surrogateescape")).hexdigest()[:12]
    d, ext = Path(dst).parent, Path(dst).suffix if len(Path(dst).suffix) <= 8 else ""
    return list(d.glob(f".{glob.escape(Path(dst).name)}{STAGE_TAG}{job}.part")) + \
        [p for p in dict.fromkeys([d / f"{STAGE_TAG}{job}-{h}.part", d / f"{STAGE_TAG}{job}-{h}.part{ext}"])
         if os.path.lexists(p)]


def _all_stage_names(dst: Path, job) -> list:
    """The staging files of dst for this job that exist, under this and earlier names."""
    return [s for s in [_stage_name(dst, job)] if os.path.lexists(s)] + _old_stage_names(dst, job)


def _same_file(a, b) -> bool:
    try:
        return os.path.samefile(a, b)
    except OSError:
        return False


def _case_only(a, b) -> bool:
    """a and b are one directory entry under two spellings (letter case or Unicode form on
    a filesystem that treats them alike, or a relative or symlinked folder), not two hard
    links: renaming one to the other must go through a temporary name, and unlinking either
    would delete the file."""
    a, b = Path(a), Path(b)
    if str(a) == str(b) or not _same_file(a, b) or not _same_file(a.parent, b.parent):
        return False
    if a.name == b.name:
        return True
    import unicodedata
    if unicodedata.normalize("NFC", a.name).casefold() != unicodedata.normalize("NFC", b.name).casefold():
        return False
    try:                       # both spellings listed: two hard links on a case-sensitive disk
        names = os.listdir(a.parent)
    except OSError:
        return True
    return not (a.name in names and b.name in names)


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
def _image_mime(data: bytes) -> str | None:
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    return None


def _embed_image(art: dict):
    """(bytes, MIME type) of the picture to embed, or None when it is gone (a staging folder
    pruned, a scan moved since the plan) or is no JPEG/PNG: the album is tagged without it."""
    for p in (art.get("embed"), art.get("front")):
        if not p:
            continue
        try:
            data = Path(p).read_bytes()
        except OSError:
            continue
        mime = _image_mime(data)
        if mime:
            return data, mime
    return None


ARTIST_TYPE = 8                  # picture type "Artist/performer" (ID3 APIC, FLAC and Vorbis pictures)


def _artist_image(a: dict):
    """(bytes, MIME type) of the artist photo to embed, or None (none planned, or gone)."""
    ap = a.get("artist_photo") or {}
    return _embed_image({"embed": ap.get("embed"), "front": ap.get("image")}) if ap else None


def _embeds(a: dict, dst) -> bool:
    """Something is embedded into this track: the front cover, the artist photo."""
    art = a.get("artwork") or {}
    front = bool(art.get("front")) and dst in (art.get("embed_into") or []) and _embed_image(art) is not None
    artist = dst in ((a.get("artist_photo") or {}).get("embed_into") or []) and _artist_image(a) is not None
    return front or artist


def _embed_all(stage, a: dict, dst):
    art = a.get("artwork") or {}
    if art.get("front") and dst in (art.get("embed_into") or []) and _embed_image(art) is not None:
        embed_front(stage, *_embed_image(art), replace=dst in (art.get("embed_replace") or []))
    if dst in ((a.get("artist_photo") or {}).get("embed_into") or []) and _artist_image(a) is not None:
        ap = a["artist_photo"]
        embed_picture(stage, *_artist_image(a), ptype=ARTIST_TYPE, desc=ap.get("mark") or "",
                      replace=dst in (ap.get("replace") or []))


def _ours(desc) -> bool:
    import artwork
    return str(desc or "").startswith(artwork.ARTIST_MARK)


def embed_picture(path, data: bytes, mime="image/jpeg", ptype=ARTIST_TYPE, desc="", replace=False):
    """Add a picture of another type than the front (the artist photo) - never in place of one
    the track has: only to a track with no picture of that type. replace: in place of the
    artist picture TagMender embedded before (its description says so); one of the user's
    own stays. MP4 has no picture types (every picture there is a cover): nothing is added."""
    import base64
    from mutagen.flac import FLAC, Picture
    from mutagen.id3 import APIC, ID3
    from mutagen.mp4 import MP4
    f = tagio.mutagen_file(path)
    if isinstance(f, MP4):
        return
    if isinstance(f, FLAC):
        if replace and any(p.type == ptype and _ours(p.desc) for p in f.pictures):
            keep = [p for p in f.pictures if not (p.type == ptype and _ours(p.desc))]
            f.clear_pictures()
            for p in keep:
                f.add_picture(p)
        if any(p.type == ptype for p in f.pictures):
            return
        pic = Picture()
        pic.type, pic.mime, pic.data, pic.desc = ptype, mime, data, desc
        f.add_picture(pic)
    elif isinstance(getattr(f, "tags", None), ID3) or type(f).__name__ in ("MP3", "AIFF", "WAVE"):
        if f.tags is None:
            f.add_tags()
        if replace:
            for x in list(f.tags.getall("APIC")):
                if x.type == ptype and _ours(x.desc):
                    del f.tags[x.HashKey]
        if any(x.type == ptype for x in f.tags.getall("APIC")):
            return
        taken = {x.desc for x in f.tags.getall("APIC")}
        desc = desc or "Artist"
        while desc in taken:                              # (APIC frames are told apart by description)
            desc += " "
        f.tags.add(APIC(encoding=3, mime=mime, type=ptype, desc=desc, data=data))
    elif type(f).__name__ in ("OggVorbis", "OggOpus", "OggFLAC"):
        old = list(f.tags.get("METADATA_BLOCK_PICTURE", []) or [])
        keep = []
        for v in old:
            try:
                p = Picture(base64.b64decode(v))
            except Exception:
                keep.append(v)
                continue
            if replace and p.type == ptype and _ours(p.desc):
                continue
            keep.append(v)
            if p.type == ptype:
                return
        pic = Picture()
        pic.type, pic.mime, pic.data, pic.desc = ptype, mime, data, desc
        f.tags["METADATA_BLOCK_PICTURE"] = keep + [base64.b64encode(pic.write()).decode("ascii")]
    else:
        return
    if isinstance(getattr(f, "tags", None), ID3):
        tagio.save_id3(f, f.tags)                         # (a v2.3 tag stays valid v2.3)
    else:
        f.save()


def _front_index(types) -> int | None:
    """Which picture is the front cover: the one typed so, else one typed "other" (a rip that
    left the type so) - the one TagMender measured (artwork.embedded_front). None: there is
    no front (a back cover or booklet alone stays, the new front is added beside it)."""
    types = list(types)
    for want in (3, 0):
        if want in types:
            return types.index(want)
    return None


def embed_front(path, data: bytes, mime="image/jpeg", replace=False):
    """Add the front cover; replace: in place of the track's own (smaller) front cover, every
    other picture kept."""
    import base64
    from mutagen.flac import FLAC, Picture
    from mutagen.id3 import APIC, ID3
    from mutagen.mp4 import MP4, MP4Cover
    f = tagio.mutagen_file(path)
    if isinstance(f, FLAC):
        pic = Picture()
        pic.type, pic.mime, pic.data = 3, mime, data
        if replace:
            keep = list(f.pictures)
            k = _front_index(p.type for p in keep)
            if k is not None:
                del keep[k]
            f.clear_pictures()
            for p in [pic] + keep:
                f.add_picture(p)
        else:
            f.add_picture(pic)
    elif isinstance(f, MP4):
        if f.tags is None:
            f.add_tags()
        fmt = MP4Cover.FORMAT_PNG if mime == "image/png" else MP4Cover.FORMAT_JPEG
        old = list(f.tags.get("covr", []))
        f.tags["covr"] = [MP4Cover(data, fmt)] + old[1:] if replace and old else old + [MP4Cover(data, fmt)]
    elif isinstance(getattr(f, "tags", None), ID3) or type(f).__name__ in ("MP3", "AIFF", "WAVE"):
        if f.tags is None:
            f.add_tags()
        desc = ""
        if replace:
            apics = f.tags.getall("APIC")
            k = _front_index(a.type for a in apics)
            if k is not None:
                desc = apics[k].desc                     # its place: never another picture's
                del f.tags[apics[k].HashKey]
        taken = {a.desc for a in f.tags.getall("APIC")}
        while desc in taken:                              # (APIC frames are told apart by description)
            desc = (desc or "Front") + " "
        f.tags.add(APIC(encoding=3, mime=mime, type=3, desc=desc, data=data))
    elif type(f).__name__ in ("OggVorbis", "OggOpus", "OggFLAC"):
        pic = Picture()
        pic.type, pic.mime, pic.data = 3, mime, data
        new = base64.b64encode(pic.write()).decode("ascii")
        old = list(f.tags.get("METADATA_BLOCK_PICTURE", []) or [])      # (the other pictures always stay)
        if old and replace:
            types = []
            for v in old:
                try:
                    types.append(Picture(base64.b64decode(v)).type)
                except Exception:
                    types.append(None)
            k = _front_index(types)
            if k is not None:
                del old[k]
        f.tags["METADATA_BLOCK_PICTURE"] = [new] + old
    else:
        raise tagio.Unsupported(f"no front-cover embedding for {type(f).__name__}")
    if isinstance(getattr(f, "tags", None), ID3):
        tagio.save_id3(f, f.tags)                         # an ID3v2.3 tag stays valid v2.3
    else:
        f.save()


# --------------------------------------------------------------------------
# backups
# --------------------------------------------------------------------------
def _backup(src: Path, jd: Path, n: int, writes: bool = True) -> dict:
    """Tag snapshot when the tag backup covers the format, else a copy of the file - or,
    for a format backed up whole when the step writes nothing (a plain move), only a note
    of that: the file is never rewritten, so neither its undo nor a copy is needed."""
    import tag_albums as ta
    blobs = jd / "blobs"
    blobs.mkdir(exist_ok=True)
    try:
        fam = tagio.family(tagio.open_file(src))
    except tagio.Unsupported:
        fam = None
    if fam in ("id3", "mp4", "vorbis") and src.suffix.lower() not in (".wav", ".aif", ".aiff", ".dsf", ".dff"):
        return {"kind": "tags", "rec": ta.backup_tags(src, blobs)}
    if not writes:
        return {"kind": "unwritten"}
    files = jd / "files"
    files.mkdir(exist_ok=True)
    _space_for(files, os.path.getsize(src))      # (the state folder may be on a small system disk)
    dest = files / f"{n}{src.suffix}"
    _copy_fsync(src, dest)
    return {"kind": "file", "path": str(dest)}


def _restore(path: Path, backup: dict, jd: Path):
    """The file's tags (or the whole file) as backed up, through a staging copy that keeps
    the file's owner: a crash never leaves a half-written file."""
    import tag_albums as ta
    if backup.get("kind") == "unwritten" or \
            (backup.get("kind") == "tags" and _tags_sig_backup_free(path, backup)):
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
        if _keeps_times(jd):              # the job kept the file's time: so does putting it back
            os.utime(stage, ns=(st.st_atime_ns, st.st_mtime_ns))
        os.replace(stage, path)
    except BaseException:
        stage.unlink(missing_ok=True)
        raise
    _fsync_dir(path.parent)


def _keeps_times(jd: Path) -> bool:
    """The job's plan kept modification times (preserve_timestamps)."""
    try:
        with open(Path(jd) / "plan.json", encoding="utf-8") as fh:
            return bool(json.load(fh).get("preserve_timestamps"))
    except (OSError, ValueError):
        return False


def _rewrite_ready(rw: dict) -> bool:
    """A rewrite from a staged file (a larger cover) still has that file, unchanged."""
    try:
        return payload.file_sha256(Path(rw["from"])) == rw.get("sha256")
    except OSError:
        return False


def _write_rewrite(rw: dict, src: Path, stage: Path):
    """stage holds what src becomes: a cue sheet's new text, or the larger cover (its new
    content, the old file's permissions; its time is now)."""
    if rw.get("from"):
        _copy_fsync(Path(rw["from"]), stage)
        if payload.file_sha256(stage) != rw.get("sha256"):
            raise StepError(f"{src.name}: the new cover did not copy intact")
        shutil.copymode(src, stage)
        return
    with open(stage, "x", encoding=rw["encoding"], newline="") as fh:
        fh.write(rw["text"])
        fh.flush()
        os.fsync(fh.fileno())
    shutil.copystat(src, stage)


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
        # folders under /mnt or /media that are no mounted share now (a plain folder there):
        # undo and recover must not take them for a share that went away
        local = sorted({os.path.realpath(d) for a in self.plan["albums"]
                        for d in (a.get("source_dir"), (a.get("dest_root") or {}).get("path")) if d
                        and not planmod.mounted(d)})
        self.journal.write(ev="begin", job=self.job, profile=self.plan["profile"], unmounted=local)
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
            self.results.append({"album": a["source_dir"], "index": i, "status": status, "label": label,
                                 "detail": detail})
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
                if "mount" in droot:     # the same mount point (a share remounted after a reboot
                    same = os.path.isdir(droot["path"]) and planmod.mounted(droot["path"]) and \
                        planmod.mount_point(droot["path"]) == droot["mount"]   # may get another device number)
                else:
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
        # the album's folders take their cover, log and cue along: only while they hold just
        # this album's music (a track added since would stay behind alone)
        planned = {fe["src"] for fe in a["files"]} | {fe["copy_of"] for fe in a["files"] if fe.get("copy_of")} \
            | {s_.get("file") for s_ in a.get("skipped") or []}
        for d in sorted({str(Path(c["src"]).parent) for c in a["companions"] if c.get("op") != "rewrite"}):
            try:
                extra = sorted(n for n in os.listdir(d) if Path(n).suffix.lower() in ex.AUDIO_EXT
                               and not n.startswith(".") and os.path.join(d, n) not in planned)
            except OSError:
                continue
            if extra:
                return "conflict", "Changed", f"{extra[0]} was added to {Path(d).name} since the plan was made; plan again"
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
        embed = _embeds(a, fe["dst"])
        if op == "tag" and not changes and not embed:
            return                          # only the cover was new, and it is gone: nothing to write
        _check_target(dst, self.roots)
        if op == "tag" and Path(src).is_symlink():
            raise StepError(f"refusing to write through a symbolic link: {src}")
        with file_lock(src):
            src_digest = payload.digest(src)
            if src_digest is None and (changes or embed) and not self.allow_unverified:
                raise StepError(f"{src.name}: its audio data cannot be verified after writing tags "
                                "(format not recognised and ffmpeg unavailable); not written")
            n = f"{i}-{j}"
            if dst.exists() and planmod.same_entry(src, dst):
                # the very file under another spelling of its path (a plan from an older
                # version, or a folder renamed since): there is nothing to move, and
                # "dropping the source's name" would delete the only one
                if op == "move" and Path(src).name == Path(dst).name:
                    op, dst = "tag", src
                    if not changes and not embed:
                        return
            linked = bool(fe.get("linked")) and dst.exists() and _same_file(src, dst) and \
                not planmod.same_entry(src, dst)
            if linked:
                op = "relink_" + op                       # dst is already a hard link to src
            backup = _backup(src, self.jd, n, writes=bool(changes or embed)) \
                if op in ("tag", "move", "relink_move", "relink_copy") else None
            self.journal.write(ev="file", album=i, idx=j, op=op, src=str(src), dst=str(dst), state="begin",
                               digest=src_digest, backup=backup, changed=bool(changes or embed))
            if linked:
                self._relink_step(i, j, op, src, dst, changes, embed, a, backup, src_digest, done_steps)
                return
            if op == "move" and not changes and not embed and _same_fs(src, dst.parent if dst.parent.exists()
                                                                       else self._existing(dst.parent)):
                _make_dirs(dst.parent, self.roots, self.journal, i, self.created)
                try:
                    _place_move(src, dst)                 # same filesystem: a rename, nothing copied
                except OSError as exc:
                    if exc.errno != errno.EXDEV:          # bind mounts / mergerfs of one disk: copy
                        raise
                else:
                    self.journal.write(ev="file", album=i, idx=j, state="committed", moved_by="rename",
                                       tags_sig=_tags_sig(dst), pics_sig=_pics_sig(dst))
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
                    tagio.write(stage, changes, **_write_extras(fe))
                if embed:
                    _embed_all(stage, a, fe["dst"])
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
                               mtime_ns=st.st_mtime_ns, tags_sig=_tags_sig(dst), pics_sig=_pics_sig(dst))
            done_steps.append(("file", i, j, op, str(src), str(dst), backup, src_digest))
            if op == "copy":
                _record_copy(src, dst, self.job)
            if op == "move" and not case_only:
                if src_digest is not None and payload.digest(dst) != src_digest:
                    raise StepError(f"{dst.name}: new file did not verify; source kept")
                os.unlink(src)
                _fsync_dir(src.parent)
                self.journal.write(ev="file", album=i, idx=j, state="source_removed")

    def _relink_step(self, i, j, op, src, dst, changes, embed, a, backup, src_digest, done_steps):
        """The destination already is this file under another name (a hard link). A move
        just drops the source's name; new tags go to the destination as a new file."""
        if changes or embed:
            stage = _stage_name(dst, self.job)
            try:
                _copy_fsync(src, stage)
                _keep_owner(stage, os.stat(src))
                if changes:
                    tagio.write(stage, changes, **_write_extras(a["files"][j] if j < len(a.get("files") or []) else {}))
                if embed:
                    _embed_all(stage, a, str(dst))
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
                           mtime_ns=st.st_mtime_ns, tags_sig=_tags_sig(dst), pics_sig=_pics_sig(dst))
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
        rw = ce.get("rewrite") or {}
        if rw.get("from") and not _rewrite_ready(rw):   # the larger cover is gone (staging pruned)
            self.journal.write(ev="note", album=i, text=f"{src.name} kept: the cover planned for it is gone")
            if op == "rewrite":
                return
            ce = dict(ce, rewrite=None)                 # moved with the album as it is
        if op == "rewrite":                       # a cue sheet that stays, naming renamed files
            stage = _stage_name(src, self.job)
            self.journal.write(ev="companion", album=i, idx=j, op=op, src=str(src), dst=str(dst), state="begin",
                               **({"artist": True} if rw.get("artist") else {}))
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
                    _write_rewrite(ce["rewrite"], src, stage)
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
            _write_rewrite(ce["rewrite"], src, stage)
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
        if not front.exists():              # a scan of the album ("Scans/Front.jpg"), moved with it just now
            front = next((Path(c["dst"]) for c in a.get("companions") or []
                          if c["src"] == str(front) and Path(c["dst"]).exists()), front)
        if not front.exists():              # gone since the plan (staging pruned): no cover file, never a failure
            self.journal.write(ev="note", album=i, text=f"no {dst.name}: the cover planned ({front}) is gone")
            return
        self.journal.write(ev="create_begin", kind="cover_file", album=i, path=str(dst),
                           sha256=payload.file_sha256(front))
        stage = _stage_name(dst, self.job)
        try:
            _copy_fsync(front, stage)
            _keep_owner(stage, os.stat(dst.parent))     # the album folder's owner (a root job on a share)
        except BaseException:
            stage.unlink(missing_ok=True)
            raise
        _place_staged(stage, dst)
        st = os.stat(dst)
        self.journal.write(ev="cover_file", album=i, path=str(dst), size=st.st_size, mtime_ns=st.st_mtime_ns)
        done_steps.append(("cover", i, 0, "create", "", str(dst), None, None))

    def new_file(self, i, sc, done_steps):
        """A new file beside the music - time-synced lyrics, the artist photo in the artist
        folder; never over an existing one."""
        dst = Path(sc["dst"])
        _check_target(dst, self.roots)
        if dst.exists():
            return
        if sc.get("from"):                          # a picture (the artist photo), staged at plan time
            try:
                data = Path(sc["from"]).read_bytes()
            except OSError:
                data = None
            if data is None or hashlib.sha256(data).hexdigest() != sc.get("sha256"):
                self.journal.write(ev="note", album=i, text=f"no {dst.name}: the picture planned for it is gone")
                return
        else:
            data = sc["text"].encode("utf-8")
        self.journal.write(ev="create_begin", kind="new_file", album=i, path=str(dst),
                           sha256=hashlib.sha256(data).hexdigest())
        stage = _stage_name(dst, self.job)
        try:
            with open(stage, "wb") as fh:
                fh.write(data)
                fh.flush()
                os.fsync(fh.fileno())
            _keep_owner(stage, os.stat(dst.parent))
        except BaseException:
            stage.unlink(missing_ok=True)
            raise
        _place_staged(stage, dst)
        st = os.stat(dst)
        # (kind "artist": the artist folder's photo, shared by every album there - one changed
        # later never keeps an album from going back)
        self.journal.write(ev="new_file", album=i, path=str(dst), size=st.st_size, mtime_ns=st.st_mtime_ns,
                           **({"kind": sc["kind"]} if sc.get("kind") else {}))
        if sc.get("kind") == "artist" and sc.get("download"):
            import artwork
            artwork.remember_written(dst, hashlib.sha256(data).hexdigest())     # (a download, not the user's)
        done_steps.append(("cover", i, 0, "create", "", str(dst), None, None))

    def cleanup_dirs(self, i, a):
        """Remove source folders this album's move left empty (never a profile root). As
        Picard's "delete empty directories", a folder holding only what Windows and macOS
        leave everywhere (JUNK) counts as empty; those files are kept in the job folder, so
        undo puts them back."""
        lib = self.plan.get("library_roots")
        if lib is None:                                  # (a plan from before library_roots)
            stops = {r.rstrip(os.sep) for r in self.roots}
        else:
            # never removed: the profile's own folders, every other profile's folders, your
            # home folder and mount parents, and a folder named on the command line - unless
            # it is this album's own folder (`tagmender run Inbox/Album`: removed once empty,
            # as Picard does, and nothing above it unless it lies inside a profile's folders)
            lib = [r.rstrip(os.sep) for r in lib]
            stops = set(lib) | _protected_folders()
            own = os.path.realpath(a["source_dir"]).rstrip(os.sep)
            for r in self.roots:
                r = r.rstrip(os.sep)
                if r in stops:
                    continue
                if r == own:
                    if not any(r.startswith(x + os.sep) for x in lib):
                        stops.add(os.path.dirname(r))
                else:
                    stops.add(r)
        roots = stops
        for d in sorted({str(Path(fe["src"]).parent) for fe in a["files"] + a["companions"]},
                        key=len, reverse=True):
            p = Path(d)
            while p.exists() and os.path.realpath(str(p)) not in roots and p != p.parent and \
                    _inside(p, self.roots):
                try:
                    inside = list(p.iterdir())
                    if any(x.name not in JUNK or not x.is_file() or x.is_symlink() for x in inside):
                        break
                    for x in inside:
                        keep = self.jd / "junk" / hashlib.sha1(str(x).encode("utf-8", "surrogateescape")
                                                               ).hexdigest()[:16] / x.name
                        keep.parent.mkdir(parents=True, exist_ok=True)
                        self.journal.write(ev="junk_removed", album=i, path=str(x), saved=str(keep))
                        shutil.move(str(x), str(keep))
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
            try:
                for s in _all_stage_names(Path(p), job):
                    s.unlink()
                    out.append(str(s))
            except OSError as exc:
                if log:
                    log.warning("could not remove a staging copy of %s: %s", p, exc)
    return out


def _same_fs(a: Path, b: Path) -> bool:
    try:
        return os.stat(a).st_dev == os.stat(b).st_dev
    except OSError:
        return False


def _try_rename_back(dst: Path, src: Path) -> bool:
    """_place_move(dst, src); False when the two folders are one disk under two mounts
    (bind mounts, Docker volumes, mergerfs) that refuse a rename between them: copy instead."""
    try:
        _place_move(dst, src)
        return True
    except OSError as exc:
        if exc.errno != errno.EXDEV:
            raise
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
        mkdirs_like_parent(src.parent)
        if not extra and _same_fs(dst, src.parent) and _try_rename_back(dst, src):
            return
        _put_back(original, src, os.stat(dst) if dst.exists() else None)
        dst.unlink(missing_ok=True)
        return
    backup = extra
    if op in ("relink_move", "relink_copy"):
        _undo_relink(src, dst, backup, digest, jd, op)
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
        mkdirs_like_parent(src.parent)
        if not (_same_fs(dst, src.parent) and _try_rename_back(dst, src)):
            _put_back(dst, src, os.stat(dst))
            dst.unlink()
        if backup:
            _restore(src, backup, jd)


def _undo_relink(src: Path, dst: Path, backup, digest, jd, op="relink_move"):
    """Before the job src and dst were one file under two names: make them so again, with
    the old tags. (A copy never wrote its source: a source edited since keeps its edits, a
    source deleted since stays deleted - the copy just gets its old tags back.)"""
    if op == "relink_copy" and not src.exists():
        if dst.exists() and backup:
            _restore(dst, backup, jd)
        return
    if not src.exists():
        if dst.exists() and _tags_sig_backup_free(dst, backup):
            try:
                os.link(dst, src)                   # nothing was written: give src its name back
                return
            except OSError as exc:                  # one disk under two mounts (bind, mergerfs):
                if exc.errno != errno.EXDEV:        # no link between them - a copy then
                    raise
                mkdirs_like_parent(src.parent)
                _put_back(dst, src, os.stat(dst))
                return
        mkdirs_like_parent(src.parent)
        _put_back(dst, src, os.stat(dst))
        if backup:
            _restore(src, backup, jd)
    elif digest is not None and payload.digest(src) != digest:
        raise StepError(f"{src} holds different audio now; left alone")
    elif op != "relink_copy" and backup and not _tags_sig_backup_free(src, backup):
        _restore(src, backup, jd)                    # an earlier, interrupted undo got this far
    if dst.exists() and _same_file(src, dst):
        return
    if dst.exists() and _same_bytes_file(src, dst):
        return                                       # (a copy of src already, across two mounts)
    stage = _stage_name(dst, "undo-link")
    if os.path.lexists(stage):
        os.unlink(stage)
    try:
        os.link(src, stage)
    except OSError as exc:
        if exc.errno != errno.EXDEV:
            raise
        _copy_fsync(src, stage)                      # one disk under two mounts: a copy of src
        if dst.exists():
            _keep_owner(stage, os.stat(dst))
    try:
        os.replace(stage, dst)
    except BaseException:
        stage.unlink(missing_ok=True)
        raise


def _matches_backup(path: Path, backup) -> bool:
    """path holds what the job's backup of it holds (its tags, or the whole file)."""
    if not backup:
        return False
    try:
        if backup.get("kind") == "tags":
            return _same_backup(path, backup)
        if backup.get("kind") == "file" and backup.get("path"):
            return payload.file_sha256(path) == payload.file_sha256(Path(backup["path"]))
    except Exception:
        return False
    return False


def _same_bytes_file(a: Path, b: Path) -> bool:
    try:
        return os.path.getsize(a) == os.path.getsize(b) and payload.file_sha256(a) == payload.file_sha256(b)
    except OSError:
        return False


def _tags_sig_backup_free(dst: Path, backup) -> bool:
    """True when dst still is the very file the job started from (no tags were written)."""
    if backup and backup.get("kind") == "unwritten":
        return True
    if not backup or backup.get("kind") != "tags":
        return False
    try:
        return _same_backup(dst, backup)
    except Exception:
        return False


def _write_extras(fe) -> dict:
    """tagio.write's keywords from a plan entry: the WAV file's RIFF INFO values, and what
    else saving the file does (Picard's ID3v2 version, ID3v1, tags removed)."""
    out = {}
    if fe.get("riff"):
        out["riff"] = fe["riff"]
    if fe.get("format"):
        out["fmt"] = fe["format"]
    return out


def _rec_key(rec):
    """A tag backup record compared without the order of its tags (mutagen lists Vorbis
    comment keys from a set: the order differs between two reads of one file)."""
    rec = dict(rec or {})
    tags = dict(rec.get("tags") or {})
    for k in ("vorbis", "mp4"):
        if k in tags:
            tags[k] = sorted(json.dumps(x, sort_keys=True, default=str) for x in tags[k])
    rec["tags"] = tags
    return json.dumps(rec, sort_keys=True, default=str)


def _same_backup(dst: Path, backup) -> bool:
    import tempfile
    import tag_albums as ta
    with tempfile.TemporaryDirectory() as d:
        old = backup["rec"] or {}
        # (a job from before TagMender kept the trailers: its record, as it made them)
        legacy = "v" not in old
        now = ta.backup_tags(dst, d, legacy=legacy)
        return _rec_key(now) == _rec_key(old)


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
                for s in _all_stage_names(p, tag):
                    s.unlink()
                    done.append(f"removed unfinished copy {s}")
    return done


def _recover(job, jd, journal, events):
    files, comps = _replay(events)
    # what a crash left is read from the files: never while a share holding them is down
    # (the album would look "not moved yet", be put back on the system disk under the empty
    # mount point, and the job be marked recovered)
    finished_ = {e["album"] for e in events if e.get("ev") in ("album_done", "album_rolled_back")}
    paths = [p for (a, _j), f in list(files.items()) + list(comps.items()) if a not in finished_
             for p in ((f.get("dst"),) if f.get("op") == "copy" else (f.get("src"), f.get("dst"))) if p]
    paths += [e["path"] for e in events if e.get("ev") in ("create_begin", "cover_file", "new_file")
              and e.get("album") not in finished_]
    for p in dict.fromkeys(str(Path(p).parent) for p in paths):
        if _down(p, events):
            raise StepError(f"the share holding {p} is not mounted; nothing was recovered - "
                            f"recover again when it is back")
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
        if ce.get("rewrite") and ce["rewrite"].get("from"):
            # the larger cover - or, its staged file gone at apply time, the old one moved as it was
            got = payload.file_sha256(dst)
            return got is not None and (got == ce["rewrite"].get("sha256") or
                                        (src != dst and src.exists() and got == payload.file_sha256(src)))
        elif ce.get("rewrite"):
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
        for x in events:                 # .DS_Store / Thumbs.db of a folder its clean-up removed
            if x.get("ev") == "junk_removed" and x["album"] == a:
                saved, path = Path(x["saved"]), Path(x["path"])
                try:
                    if saved.exists() and not os.path.lexists(path):
                        mkdirs_like_parent(path.parent)
                        shutil.copy2(saved, path)
                        _keep_owner(path, os.stat(path.parent))
                except OSError as exc:
                    problems.append(f"{path}: {exc}")
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


def _down(folder, events) -> bool:
    """folder's share is not mounted now - and it was when the job ran (a plain folder
    under /mnt is never mounted: that is how it was)."""
    if planmod.mounted(folder):
        return False
    real = os.path.realpath(str(folder))
    local = next((e.get("unmounted") or [] for e in events if e.get("ev") == "begin"), [])
    return not any(real == u or real.startswith(u.rstrip(os.sep) + os.sep) for u in local)


def _undo_unreachable(jd, files, comps, rolled, events=()) -> str | None:
    """Why undo cannot run now: a share the job wrote to or took files from is not mounted
    (or its folder is gone). Undo would otherwise find the copies "gone" and
    forget them, or put an album back onto the system disk under an empty mount point."""
    try:
        albums = json.loads((jd / "plan.json").read_text(encoding="utf-8"))["albums"]
    except (OSError, ValueError, KeyError):
        return None
    touched = {a for (a, _j), f in list(files.items()) + list(comps.items())
               if f.get("state") in ("committed", "source_removed") and a not in rolled}
    touched |= {e["album"] for e in events if e.get("ev") in ("cover_file", "new_file") and e["album"] not in rolled}
    for i in sorted(touched):
        if i >= len(albums):
            continue
        a = albums[i]
        root = a.get("dest_root") or {}
        if root.get("path"):
            r = Path(root["path"])              # (not its device number: a share remounted
            if not r.is_dir() or _down(r, events):   # after a reboot may get another)
                return f"the destination {r} is not available (unmounted?)"
        if a.get("source_dir") and _down(a["source_dir"], events) and \
                any(f.get("op") != "copy" for (x, _j), f in files.items() if x == i):
            return f"the share holding {a['source_dir']} is not mounted"
    paths = [p for (a, _j), f in list(files.items()) + list(comps.items()) if a in touched
             for p in ((f.get("dst"),) if f.get("op") == "copy" else (f.get("src"), f.get("dst"))) if p]
    paths += [e["path"] for e in events if e.get("ev") in ("cover_file", "new_file") and e["album"] in touched]
    for p in dict.fromkeys(str(Path(p).parent) for p in paths):
        if _down(p, events):                             # a file on a share that is not mounted now
            return f"the share holding {p} is not mounted"
    return None


def _moved_later(path: Path, job) -> str | None:
    """The later job that moved or renamed path (it was the source of one of its steps)."""
    p = str(path)
    root = jobs_dir()
    mine = Journal(root / job).read()
    started = mine[0].get("t", 0) if mine else 0
    try:
        names = sorted(d.name for d in root.iterdir() if d.name != job)
    except OSError:
        return None
    for name in names:                    # (by when each job began: two jobs may share a second)
        events = Journal(root / name).read()
        if not events or events[0].get("t", 0) <= started or any(e.get("ev") == "undone" for e in events):
            continue
        back = {tuple(e["key"]) for e in events if e.get("ev") in ("undo_step", "rollback_step")}
        rolled = {e["album"] for e in events if e.get("ev") == "album_rolled_back" and not e.get("problems")}
        for e in events:
            if e.get("ev") in ("file", "companion") and e.get("src") == p and e.get("op") != "copy" \
                    and e.get("src") != e.get("dst") and e.get("album") not in rolled and \
                    (e["ev"], e.get("album"), e.get("idx")) not in back:
                return name                # (a step it undid or rolled back put the file back)
    return None


def _protected_folders() -> set:
    """Folders a clean-up never removes: every profile's sources, destination and
    unresolved folder, the home folder, and the folders shares are mounted under."""
    out = {os.path.realpath(os.path.expanduser("~"))} | {m.rstrip(os.sep) for m in planmod.MOUNT_PARENTS}
    try:
        import profiles as pf
        for n in pf.names():
            try:
                p = pf.load(n)
            except Exception:
                continue
            for d in [s.get("path") for s in p["sources"]] + [p["destination"], p["unresolved"].get("move_to")]:
                if d:
                    out.add(os.path.realpath(os.path.expanduser(d)).rstrip(os.sep))
    except Exception:
        pass
    return out


def _watcher_forgets(jd: Path):
    """The watcher must not do an undone job again by itself. Each album's record says
    "undone", with the signatures of its files as the undo left them: the watcher looks at
    it again only once those change (a track added, removed or edited)."""
    try:
        plan = json.loads((jd / "plan.json").read_text(encoding="utf-8"))
        db = sqlite3.connect(ex.STATE_DIR / "state.db", timeout=60)
    except (OSError, ValueError, sqlite3.Error):
        return
    try:
        import watcher
        db.execute("CREATE TABLE IF NOT EXISTS processed (folder TEXT PRIMARY KEY, profile TEXT, signature TEXT,"
                   " status TEXT, attempts INTEGER, next_try REAL, ts REAL, detail TEXT)")
        for a in plan.get("albums") or []:
            files = [Path(fe["src"]) for fe in a.get("files") or [] if not fe.get("copy_of")]
            if not files:
                continue
            folder = Path(a["source_dir"])
            # the files the watcher's group of this album holds: the plan's, those it skipped,
            # and the same tracks in another format beside them (left out by identification)
            group = set(files) | {Path(s_["file"]) for s_ in a.get("skipped") or [] if s_.get("file")}
            stems = {(p.parent, p.stem) for p in group}
            for d in {p.parent for p in group}:
                try:
                    group |= {x for x in d.iterdir() if x.is_file() and x.suffix.lower() in ex.AUDIO_EXT
                              and (x.parent, x.stem) in stems}
                except OSError:
                    pass
            group = sorted(group)
            key = watcher.album_key(folder, group)
            sigs = {watcher.shape(folder, files), watcher.shape(folder, group)}
            db.execute("INSERT OR REPLACE INTO processed VALUES (?,?,?,?,?,?,?,?)",
                       (key, plan.get("profile") or "", "|".join(sorted(sigs)), "undone", 0, 0, time.time(), ""))
        db.commit()
    except Exception:                    # the watcher's memory is a convenience: never fail an undo
        pass
    finally:
        db.close()


def _undo(job, jd, journal, events, force):
    # albums put back completely by their rollback are skipped; where the rollback met a
    # problem, undo does the steps it did not finish
    rolled = {e["album"] for e in events if e.get("ev") == "album_rolled_back" and not e.get("problems")}
    rolled_steps = {tuple(e["key"]) for e in events if e.get("ev") == "rollback_step"}
    already = {tuple(e["key"]) for e in events if e.get("ev") == "undo_step"}
    files, comps = _replay(events)
    out, problems = [], []
    gone = _undo_unreachable(jd, files, comps, rolled, events)
    if gone:
        raise StepError(f"{gone}; nothing was undone - undo again when it is back")
    _clear_undo_leftovers(files, comps)                  # an earlier undo cut short

    def did(*key):
        journal.write(ev="undo_step", key=list(key))

    # which tracks stay where the job put them, first: an album moves back whole or not at
    # all, with its cover, scans, log and cue. Two kinds of reason: a change made since the
    # job (--force overrides it) and a state undo cannot put right (never overridden)
    touched_by_undo = {tuple(e["key"]) for e in events if e.get("ev") == "undo_file_begin"}
    rolling = {e["album"] for e in events if e.get("ev") in ("album_failed", "album_rolled_back")}
    journal.write(ev="undo_begin")
    verdict, hard, gone = {}, set(), set()
    for (a, j), f in sorted(files.items(), reverse=True):
        if a in rolled or f["state"] not in ("committed", "source_removed") or ("file", a, j) in already \
                or ("file", a, j) in rolled_steps:
            continue
        op, src, dst = f.get("op"), Path(f["src"]), Path(f["dst"])
        check = src if op == "tag" else dst
        if not check.exists() and op == "move" and src.exists():
            check = src                                 # an interrupted undo had moved it back
        why = None
        if op == "tag" and not os.path.lexists(src):
            later = _moved_later(src, job)
            if later:
                why = f"{src} was moved by a later job ({later}); undo that one first"
            else:
                gone.add((a, j))                       # deleted since: no tags to put back
        elif op in ("move", "relink_move") and not dst.exists() and not src.exists():
            later = _moved_later(dst, job)
            if later:
                why = f"{dst} was moved by a later job ({later}); undo that one first - its album waits"
                hard.add(a)
            elif force:
                gone.add((a, j))                       # nothing to put back; the rest goes back
            else:
                why = (f"{dst} is gone (deleted after the job?); the rest of its album stays "
                       f"(tagmender undo --force {job} puts the rest back)")
        elif op in ("move", "relink_move") and src.exists() and dst.exists() and not _same_file(src, dst) and \
                (a, j) not in touched_by_undo and a not in rolling:
            # (an earlier undo or the album's rollback, cut short while putting this very file
            # back, made it)
            why = f"{src} holds a file again (downloaded again?); the album stays where the job put it"
            hard.add(a)
        elif op == "copy" and dst.exists() and f.get("size") is not None and not force and (
                ((os.stat(dst).st_size, os.stat(dst).st_mtime_ns) != (f["size"], f["mtime_ns"]) and
                 not (f.get("tags_sig") and _tags_sig(dst) == f["tags_sig"] and
                      (not f.get("digest") or payload.digest(dst) == f["digest"]))) or
                (f.get("tags_sig") and _tags_sig(dst) != f["tags_sig"]) or
                (f.get("pics_sig") and _pics_sig(dst) != f["pics_sig"])):
            # (a copy whose only change is its time - a later job's undo put its tags back - is
            # still the copy; one with other tags is not, even when the editor kept its time)
            why = f"{dst} was changed after the job; left in place"
        elif f.get("digest") and check.exists() and payload.digest(check) != f["digest"]:
            why = f"{check} holds different audio now; left alone"
        elif op in ("tag", "move", "relink_move", "relink_copy") and f.get("tags_sig") and \
                check == (src if op == "tag" else dst) and check.exists() and not force and \
                (_tags_sig(check) != f["tags_sig"] or
                 (f.get("pics_sig") and _pics_sig(check) != f["pics_sig"])) and \
                not _matches_backup(check, f.get("backup")):
            # (tags as the backup has them: an undo cut short had put them back already)
            why = (f"{check}: its tags or pictures were edited after the job; left as they are "
                   f"(tagmender undo --force {job} puts the old tags back anyway)")
        verdict[(a, j)] = (why, check)
    staying = {a for (a, _j), (why, _c) in verdict.items() if why and files[(a, _j)].get("op") != "tag"}
    # a cue sheet the job rewrote, or a cover/lyrics file it made, changed since: putting the
    # album back would lose the edit or leave the file behind - the album stays (--force: back)
    for (a, j), c in comps.items():
        if a in rolled or c["state"] not in ("committed", "source_removed") or ("companion", a, j) in already \
                or not c.get("saved") or c.get("size") is None:
            continue
        cdst = Path(c["dst"])
        if c.get("artist"):
            continue                   # (the artist folder's artist.jpg never holds an album back)
        if cdst.exists() and (os.stat(cdst).st_size, os.stat(cdst).st_mtime_ns) != (c["size"], c["mtime_ns"]):
            staying.add(a)
            problems.append(f"{cdst} was changed after the job (tagmender undo --force {job} puts the "
                            f"album back anyway, with the file as the job found it)")
    for e in events:
        if e.get("ev") in ("cover_file", "new_file") and e["album"] not in rolled and e.get("kind") != "artist" and \
                ("created", e["path"]) not in already and Path(e["path"]).exists() and \
                (os.stat(e["path"]).st_size, os.stat(e["path"]).st_mtime_ns) != (e["size"], e["mtime_ns"]) and \
                any(f.get("op") in ("move", "relink_move") for (x, _j), f in files.items() if x == e["album"]):
            staying.add(e["album"])
            problems.append(f"{e['path']} was changed after the job; the album stays with it")
    for (a, j), (why, check) in list(verdict.items()):   # an album moves back whole, or not at all
        if a in staying and not why and (not force or a in hard) and files[(a, j)].get("op") != "tag":
            verdict[(a, j)] = (f"{check}: stays with the rest of its album (see above; tagmender undo "
                               f"--force {job} puts what it can back anyway)" if a not in hard else
                               f"{check}: stays with the rest of its album (see above)", check)
    if force:                            # --force: what can go back does; the changed tracks stay
        staying = set(hard)

    covers = [e for e in events if e.get("ev") in ("cover_file", "new_file") and e["album"] not in rolled]
    for e in reversed(covers):
        p = Path(e["path"])
        if ("created", str(p)) in already or ("created", str(p)) in rolled_steps:
            continue
        if e["album"] in staying:
            problems.append(f"{p} stays with the album's music, which stays where the job put it")
            continue
        if p.exists() and (os.stat(p).st_size, os.stat(p).st_mtime_ns) == (e["size"], e["mtime_ns"]):
            p.unlink()
            out.append(f"removed {p}")
        elif p.exists():
            problems.append(f"{p} was changed after the job; left in place")
            continue
        elif _moved_later(p, job):
            problems.append(f"{p} was moved by a later job; undo that one first")
            continue
        did("created", str(p))
    for (a, j), c in sorted(comps.items(), reverse=True):
        if a in rolled or c["state"] not in ("committed", "source_removed") or ("companion", a, j) in already \
                or ("companion", a, j) in rolled_steps:
            continue
        cdst = Path(c["dst"])
        if c.get("artist") and c.get("size") is not None and cdst.exists() and \
                (os.stat(cdst).st_size, os.stat(cdst).st_mtime_ns) != (c["size"], c["mtime_ns"]):
            # (the user's photo the job put in place of TagMender's download, changed since: theirs)
            out.append(f"{cdst} was changed after the job; kept as it is")
            did("companion", a, j)
            continue
        if a in staying:
            problems.append(f"{cdst} stays with the album's music, which stays where the job put it")
            continue
        if c.get("op") in ("move", "copy") and not c.get("saved") and not os.path.lexists(cdst) and \
                not Path(c["src"]).exists():
            later = _moved_later(cdst, job)
            if later:
                problems.append(f"{cdst} was moved by a later job ({later}); undo that one first")
            else:                                        # deleted since: nothing to put back
                out.append(f"{cdst.name} is gone (deleted after the job?); nothing to put back")
                did("companion", a, j)
            continue
        # (a moved companion changed since - lyrics a player updated - goes back with its edit;
        # a copy changed since is no longer only the job's: it stays)
        if c.get("op") == "copy" and c.get("size") is not None and cdst.exists() and not force and \
                (os.stat(cdst).st_size, os.stat(cdst).st_mtime_ns) != (c["size"], c["mtime_ns"]):
            problems.append(f"{cdst} was changed after the job; left as it is "
                            f"(tagmender undo --force {job} removes it anyway)")
            continue
        try:
            _undo_step(("companion", a, j, c["op"], c["src"], c["dst"], c.get("saved"), None), jd)
            out.append(f"put back {Path(c['src']).name}")
            did("companion", a, j)
        except Exception as exc:
            problems.append(f"{c['src']}: {exc}")
    removed = [e for e in events if e.get("ev") == "dir_removed" and e.get("album") not in staying]
    for e in removed:
        mkdirs_like_parent(Path(e["path"]))
    for e in events:                     # .DS_Store / Thumbs.db of the folders removed: back in them
        if e.get("ev") == "junk_removed" and e.get("album") not in staying and e["album"] not in rolled:
            saved, path = Path(e["saved"]), Path(e["path"])
            if saved.exists() and not os.path.lexists(path) and path.parent.is_dir():
                shutil.copy2(saved, path)
                _keep_owner(path, os.stat(path.parent))
    for (a, j), f in sorted(files.items(), reverse=True):
        if (a, j) not in verdict:
            continue
        why, check = verdict[(a, j)]
        if why:
            problems.append(why)
            continue
        if (a, j) in gone:
            out.append(f"{Path(f['dst']).name} is gone (deleted after the job?); nothing to put back")
            did("file", a, j)
            continue
        try:
            journal.write(ev="undo_file_begin", key=[a, j])
            with file_lock(check if check.exists() else Path(f["src"])):
                _undo_step(("file", a, j, f["op"], f["src"], f["dst"], f.get("backup"), f.get("digest")), jd)
            out.append(f"put back {Path(f['src']).name}")
            did("file", a, j)
        except Exception as exc:
            problems.append(f"{f['src']}: {exc}")
    for e in reversed([e for e in events if e.get("ev") == "dir_created"]):
        p = Path(e["path"])
        try:
            inside = list(p.iterdir()) if p.is_dir() else []
            if inside and all(x.name in JUNK and x.is_file() and not x.is_symlink() for x in inside):
                for x in inside:                         # only what Finder/Explorer left: as Picard
                    keep = jd / "junk" / ("undo-" + hashlib.sha1(str(x).encode("utf-8", "surrogateescape")
                                                                 ).hexdigest()[:16]) / x.name
                    keep.parent.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(x), str(keep))
            p.rmdir()
        except OSError as exc:
            if exc.errno not in (errno.ENOTEMPTY, errno.EEXIST, errno.ENOENT):
                problems.append(f"{p}: could not remove the folder the job made ({exc.strerror})")
    _watcher_forgets(jd)
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
    for d in (jobs_dir().iterdir() if jobs_dir().is_dir() else []):
        try:
            with job_lock(d.name, active=False):   # (backups only: no music changes)
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
    # artwork the engine fetched for plans (STATE_DIR/staging/<job>): a plan that old is
    # refused anyway once its files changed, and the folder otherwise grows with every run
    staging = ex.STATE_DIR / "staging"
    waiting = []                             # plans still waiting to be applied keep theirs
    try:
        for x in (ex.STATE_DIR / "plans").glob("*.json"):
            if (jobs_dir() / x.stem / "journal.jsonl").exists():   # applied: its job has the plan
                if x.stat().st_mtime < cutoff:
                    x.unlink()
                    out.append(f"plans/{x.name}")
                continue
            waiting.append(x.stem)
    except OSError:
        pass
    if staging.is_dir():
        for d in staging.iterdir():
            try:
                if any(w == d.name or w.startswith(d.name + "-") for w in waiting):
                    continue
                if d.is_dir() and not d.is_symlink() and d.stat().st_mtime < cutoff and \
                        not any(x.stat().st_mtime >= cutoff for x in d.rglob("*")):
                    shutil.rmtree(d, ignore_errors=True)
                    out.append(f"staging/{d.name}")
            except OSError:
                continue
    return out
