# Eleventh independent review of TagMender's file operations (code at afcc0ef). Each test
# asserts the correct behaviour and fails at afcc0ef.
#   cd /home/claude/tagmender && TAGMENDER_DISCOGS_PLUGIN=/home/claude/herculepirate/picard-discogs-enrichment \
#     TAGMENDER_COVERUP_DIR=/home/claude/herculepirate/picard-coverup python3 -W ignore <this file>
# R11_01.test_case_insensitive_share needs FUSE (/dev/fuse, fusermount3) and mfusepy (in
# tests/pylib/, or the folder $TM_FUSE_PYLIB names: pip install --target DIR mfusepy; it is
# skipped otherwise); this file then serves a
# case-insensitive, case-preserving filesystem itself (`python3 test_review11.py --ci-fuse
# BACKING MOUNTPOINT`), as an SMB share from a Windows/macOS server or an exFAT stick looks.
import os as _os, sys as _sys, tempfile as _tempfile
if "--ci-fuse" not in _sys.argv:
    _os.environ["XDG_CONFIG_HOME"] = _tempfile.mkdtemp(prefix="tm-config-")
    _os.environ["XDG_STATE_HOME"] = _tempfile.mkdtemp(prefix="tm-state-")
import errno
import os
import shutil
import subprocess
import sys
import time
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, os.environ.get("TM_FUSE_PYLIB", str(HERE / "pylib")))


# ---------------------------------------------------------------------------------------
# A case-insensitive, case-preserving passthrough filesystem (for R11_01)
# ---------------------------------------------------------------------------------------
def _serve_ci_fuse(backing, mountpoint):
    import mfusepy as fuse

    class CI(fuse.Operations):
        use_ns = True

        def __init__(self, root):
            self.root = os.path.realpath(root)

        def _real(self, path, last_may_be_new=False):
            cur = self.root
            parts = [p for p in path.split("/") if p]
            for k, comp in enumerate(parts):
                cand = os.path.join(cur, comp)
                if os.path.lexists(cand):
                    cur = cand
                    continue
                try:
                    names = os.listdir(cur)
                except OSError:
                    names = []
                hit = next((n for n in names if n.casefold() == comp.casefold()), None)
                if hit is None:
                    if k == len(parts) - 1 and last_may_be_new:
                        return cand
                    raise fuse.FuseOSError(errno.ENOENT)
                cur = os.path.join(cur, hit)
            return cur

        def _new(self, path):
            parent, name = os.path.split(path.rstrip("/"))
            rp = self._real(parent)
            if any(n.casefold() == name.casefold() for n in os.listdir(rp)):
                raise fuse.FuseOSError(errno.EEXIST)
            return os.path.join(rp, name)

        def getattr(self, path, fh=None):
            st = os.lstat(self._real(path))
            d = {k: getattr(st, k) for k in ("st_mode", "st_nlink", "st_uid", "st_gid", "st_size", "st_ino")}
            d.update(st_atime=st.st_atime_ns / 1e9, st_mtime=st.st_mtime_ns / 1e9, st_ctime=st.st_ctime_ns / 1e9)
            return d

        def readdir(self, path, fh):
            return [".", ".."] + os.listdir(self._real(path))

        def access(self, path, mode):
            if not os.access(self._real(path), mode):
                raise fuse.FuseOSError(errno.EACCES)

        def chmod(self, path, mode):
            os.chmod(self._real(path), mode)

        def chown(self, path, uid, gid):
            os.chown(self._real(path), uid, gid)

        def utimens(self, path, times=None):
            if times is None:
                os.utime(self._real(path))
            else:
                os.utime(self._real(path), ns=(int(times[0]), int(times[1])))

        def mkdir(self, path, mode):
            os.mkdir(self._new(path), mode)

        def rmdir(self, path):
            os.rmdir(self._real(path))

        def unlink(self, path):
            os.unlink(self._real(path))

        def symlink(self, target, source):
            os.symlink(source, self._new(target))

        def readlink(self, path):
            return os.readlink(self._real(path))

        def rename(self, old, new):
            ro = self._real(old)
            parent, name = os.path.split(new.rstrip("/"))
            rp = self._real(parent)
            hit = next((n for n in os.listdir(rp) if n.casefold() == name.casefold()), None)
            rn = os.path.join(rp, name)
            if hit is not None and os.path.join(rp, hit) != ro:
                os.replace(ro, os.path.join(rp, hit))
                if hit != name:
                    os.rename(os.path.join(rp, hit), rn)
                return
            os.rename(ro, rn)                          # a new name, or only its case changes

        def link(self, target, source):
            os.link(self._real(source), self._new(target))

        def statfs(self, path):
            st = os.statvfs(self._real(path))
            return {k: getattr(st, k) for k in ("f_bavail", "f_bfree", "f_blocks", "f_bsize", "f_favail",
                                                "f_ffree", "f_files", "f_flag", "f_frsize", "f_namemax")}

        def open(self, path, flags):
            return os.open(self._real(path), flags)

        def create(self, path, mode, fi=None):
            return os.open(self._real(path, last_may_be_new=True), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)

        def read(self, path, length, offset, fh):
            os.lseek(fh, offset, os.SEEK_SET)
            return os.read(fh, length)

        def write(self, path, buf, offset, fh):
            os.lseek(fh, offset, os.SEEK_SET)
            return os.write(fh, buf)

        def truncate(self, path, length, fh=None):
            with open(self._real(path), "r+b") as f:
                f.truncate(length)

        def flush(self, path, fh):
            return 0

        def release(self, path, fh):
            os.close(fh)

        def fsync(self, path, fdatasync, fh):
            os.fsync(fh)

    fuse.FUSE(CI(backing), mountpoint, foreground=True, use_ino=True, attr_timeout=0, entry_timeout=0,
              negative_timeout=0)


if __name__ == "__main__" and "--ci-fuse" in sys.argv:
    i = sys.argv.index("--ci-fuse")
    _serve_ci_fuse(sys.argv[i + 1], sys.argv[i + 2])
    sys.exit(0)


REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "tests"))
import engine as eng_mod  # noqa: E402
import executor  # noqa: E402
import payload  # noqa: E402
import plan as pl  # noqa: E402
import tagio  # noqa: E402
import watcher  # noqa: E402
from test_executor import Base, audio, target  # noqa: E402
from test_fileops_review7 import LOSSY, Lossy, tree  # noqa: E402


def offline_engine(test, prof):
    """engine.Engine with identification stubbed (no network): grouping, planning and the
    paths as `tagmender run PATH` produces them."""
    e = eng_mod.Engine.__new__(eng_mod.Engine)
    e.profile, e.job, e.notes = prof, time.strftime("%Y%m%d-%H%M%S-") + os.urandom(2).hex(), {}

    def identify(g):
        ts = [target(1, "First") if f.name.startswith(("01", "1")) else target(2, "Second") for f in g.files]
        return test.proposal(source_dir=str(g.folder), files=[str(f) for f in g.files], targets=ts)
    e.identify = identify
    e.add_artwork = lambda prop: None
    e.apply_level = lambda prop: None
    return e


REAL_ALBUMS_IN = eng_mod.Engine.albums_in


class FakeEngine:
    """watcher.Watcher's engine, identifying without the network."""
    test = None

    def __init__(self, prof, log=None, **kw):
        self.profile, self.job = prof, "w" + os.urandom(3).hex()

    def albums_in(self, root):
        return REAL_ALBUMS_IN(self, root)

    def identify(self, g):
        ts = [target(1, "First") if f.name.startswith("01") else target(2, "Second") for f in g.files]
        return FakeEngine.test.proposal(source_dir=str(g.folder), files=[str(f) for f in g.files], targets=ts)


# ---------------------------------------------------------------------------------------
# R11-01. DATA LOSS: a track the plan finds already at its destination, but under another
# spelling of the same path, is taken for a hard link and DELETED. plan.Planner.album
# (plan.py:633-634) sets fe["linked"] when dst exists, os.path.samefile(dst, src) and
# not _one_name(dst, src); _one_name (plan.py:248-256) says "one entry" only when the two
# paths fold alike AND the two FILE names are not both listed. The executor then treats
# the step as "relink_move" (executor.py:678-686) and _relink_step drops "the source's
# name" with os.unlink(src) (executor.py:796-799) - the only name the track has. With
# tags to change it first replaces dst (= src) with the tagged copy, then unlinks that.
# The job reports "done"; a tag snapshot is all the backup holds, so undo cannot bring
# the audio back. Re-running a move profile that organises a library (destination = the
# library) over an album already in its place - the normal, idempotent re-run - hits it:
#   - `cd /mnt/user/music && tagmender run --yes .` (a relative PATH: engine.albums_in
#     keeps it relative, the destination is absolute, so the two strings differ);
#   - a source or command-line path through a symlink (~/Music -> /mnt/nas/music) with
#     the destination given as the real path (also every watcher pass over such a root);
#   - on a case-insensitive share (SMB to a Windows/macOS/NAS server, an exFAT stick)
#     when the album or artist changes only in letter case ("2001 - Real album" ->
#     "2001 - Real Album"): the file names are identical, only a folder differs in case,
#     so both names are listed and _one_name answers "two hard links". (executor._case_only,
#     executor.py:385-396, has the same flaw; recover's "rename-move: linked, not yet
#     unlinked" and _undo_step's "linked back" branches would unlink in the same way.)
class R11_01_SameTrackUnderAnotherPathIsDeleted(Base):
    def organised(self):
        prof = self.profile(operation="move", destination=str(self.lib))
        p = offline_engine(self, prof).plan(self.lib)
        self.assertEqual(executor.Executor(p).run()["counts"], {"done": 1})
        self.home = self.lib / "Real Artist" / "2001 - Real Album"
        self.digests = {n: payload.digest(self.home / n) for n in ("01.F.flac", "02.S.flac")}
        return prof

    def check(self, res, folder=None):
        folder = folder or self.home
        got = {n: payload.digest(folder / n) if (folder / n).exists() else None for n in self.digests}
        self.assertEqual(got, self.digests, f"tracks deleted by a job that reported {res['counts']}; "
                                            f"left: {tree(self.lib)}")

    def test_rerun_with_a_relative_path(self):
        prof = self.organised()
        cwd = os.getcwd()
        os.chdir(self.lib)                                     # cd /music && tagmender run --yes .
        try:
            p = offline_engine(self, prof).plan(".")
            res = executor.Executor(p).run()
        finally:
            os.chdir(cwd)
        self.check(res)

    def test_rerun_through_a_symlinked_path(self):
        prof = self.organised()
        link = self.tmp / "Music"                              # ~/Music -> the library
        link.symlink_to(self.lib)
        p = offline_engine(self, prof).plan(link)
        res = executor.Executor(p).run()
        self.check(res)

    def test_case_insensitive_share(self):
        if not (os.path.exists("/dev/fuse") and shutil.which("fusermount3")):
            self.skipTest("needs FUSE")
        try:
            import mfusepy  # noqa: F401
        except (ImportError, OSError):
            self.skipTest("needs mfusepy (pip install --target pylib mfusepy)")
        back, mnt = self.tmp / "back", self.tmp / "share"
        back.mkdir()
        mnt.mkdir()
        proc = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "--ci-fuse", str(back), str(mnt)],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            for _ in range(100):
                if os.path.ismount(mnt):
                    break
                time.sleep(0.05)
            self.assertTrue(os.path.ismount(mnt))
            lib = mnt / "music"
            album = lib / "Real Artist" / "2001 - Real album"        # filed before the title's case was fixed
            f1 = audio(album / "01.F.flac", 440, album="Real album", title="First", artist="Real Artist")
            f2 = audio(album / "02.S.flac", 550, album="Real album", title="Second", artist="Real Artist")
            (album / "cover.jpg").write_bytes(b"front")
            self.digests = {n: payload.digest(album / n) for n in ("01.F.flac", "02.S.flac")}
            prof = self.profile(sources=[{"path": str(lib)}], operation="move", destination=str(lib))
            p = pl.build([self.proposal(source_dir=str(album), files=[str(f1), str(f2)])], prof)
            res = executor.Executor(p).run()
            self.home, self.lib = album, lib
            self.check(res)
        finally:
            subprocess.run(["fusermount3", "-u", str(mnt)], capture_output=True)
            proc.wait(timeout=10)


# ---------------------------------------------------------------------------------------
# R11-02. `tagmender run PATH` with a relative PATH journals relative paths, so undo and
# recover act relative to whatever folder they are run from. engine.Engine.albums_in
# (engine.py:202) only expands "~"; the groups, the proposals, the plan (Planner.album),
# the journal and plan.json all carry "Some Artist/2001 - Some Album/01 a.flac".
# `cd /mnt/user/inbox && tagmender run --yes .` moves the album into the library; a later
# `tagmender undo JOB` from the home folder "puts back" the album into
# ~/Some Artist/2001 - Some Album/ (the inbox folder it came from stays gone), and
# reports success. The same relative src feeds collision_suffix(str(src)), so the
# "stable" suffix of a track changes with the working directory, and the copies table
# records relative sources that later runs from elsewhere never find.
class R11_02_RelativePathUndoneIntoTheWorkingDirectory(Base):
    def test_undo_from_another_folder(self):
        prof = self.profile(operation="move", destination=str(self.dest))
        cwd = os.getcwd()
        os.chdir(self.lib)
        try:
            p = offline_engine(self, prof).plan(".")
            self.assertEqual(executor.Executor(p).run()["counts"], {"done": 1})
        finally:
            os.chdir(cwd)
        elsewhere = self.tmp / "home"
        elsewhere.mkdir()
        os.chdir(elsewhere)
        try:
            out = executor.undo(p["job"])
        finally:
            os.chdir(cwd)
        self.assertEqual(tree(elsewhere), [], f"undo put the album into the working directory: {out}")
        self.assertTrue((self.album / "01 a.flac").exists() and (self.album / "02 b.flac").exists(),
                        f"not back where it came from: {tree(self.lib)}")


# ---------------------------------------------------------------------------------------
# R11-03. One file name that is not valid UTF-8 makes every job crash before it starts.
# Linux hands such names to Python as str with surrogate escapes ("Bj\udcf6rk"): names
# from Latin-1/CP437 zip archives unpacked on Linux, old FAT/ISO copies, Samba shares
# without "unix charset = UTF-8". TagMender means to handle them (_stage_name and
# _case_tmp hash names with "surrogateescape", executor.py:357, 400), but plan.save
# (plan.py:942, write_text(..., encoding="utf-8")) raises UnicodeEncodeError, and so
# would Journal.write (executor.py:84), the state database (_record_album, sqlite3 refuses
# surrogates) and file_lock (executor.py:197, .encode()). Executor._run saves plan.json
# first (executor.py:558), so the whole job - every album of `tagmender run ~/music` -
# dies with a traceback before any album, on every run (unresolved albums planned with
# TAGMENDER=Unresolved too); `tagmender plan --output` dies the same way, and the watcher
# records the album as a provider error and retries it for ever.
class R11_03_NonUtf8FileNameCrashesTheWholeJob(Base):
    def test_x(self):
        bad = Path(os.fsdecode(os.fsencode(self.album) + b"/02 Bj\xf6rk.flac"))
        os.rename(self.f2, bad)
        other = self.lib / "Other Artist" / "Other Album"
        o1 = audio(other / "01 x.flac", 660, album="Other", title="x", artist="Other Artist")
        p = pl.build([self.proposal(files=[str(self.f1), str(bad)]),
                      self.proposal(source_dir=str(other), files=[str(o1)], targets=[target(1, "First")],
                                    release_id="778")], self.profile())
        try:
            res = executor.Executor(p).run()
        except UnicodeError as exc:
            self.fail(f"the job crashed before any album: {type(exc).__name__}: {exc.reason} "
                      f"({exc.object[max(0, exc.start - 40):exc.end + 10]!r})")
        self.assertEqual(res["counts"], {"done": 2})
        self.assertEqual(tagio.read(bad)["album"], ["Real Album"])


# ---------------------------------------------------------------------------------------
# R11-04. The R10-05 fix ("an album whose music stays keeps its cover, scans, log and cue
# with it") is per album for companions but per TRACK for the music. executor._undo puts
# an album in `staying` as soon as ONE of its tracks stays (executor.py:1467), keeps all
# its companions and covers at the destination and does not recreate its source folders -
# then moves every OTHER track back anyway (executor.py:1506-1519, each judged alone).
# Rating one song in foobar2000, or fixing one title in Picard, after a move and before
# undo: undo leaves that track with cover.jpg, rip.cue and rip.log in the new folder and
# moves the rest of the album back into a recreated old folder without them (the cue at
# the destination still names the new file names of tracks that are gone). Running undo
# again changes nothing; the album stays split until `undo --force`.
class R11_04_UndoSplitsAnAlbumWithOneEditedTrack(Base):
    def test_x(self):
        (self.album / "cover.jpg").write_bytes(b"front")
        p = pl.build([self.proposal()], self.profile(operation="move", destination=str(self.dest)))
        self.assertEqual(executor.Executor(p).run()["counts"], {"done": 1})
        tagio.write(Path(p["albums"][0]["files"][0]["dst"]), {"rating": ["5"]})      # one song rated
        out = executor.undo(p["job"])
        tracks = [x for x in list(self.lib.rglob("*.flac")) + list(self.dest.rglob("*.flac"))]
        folders = {x.parent for x in tracks}
        self.assertEqual(len(folders), 1, f"album split: lib {tree(self.lib)}, dest {tree(self.dest)}; {out}")
        self.assertTrue((next(iter(folders)) / "cover.jpg").exists(), "cover not with the music")


# ---------------------------------------------------------------------------------------
# R11-05. The R10-06 fix is incomplete: _undo_unreachable (executor.py:1403-1425) checks
# only an album's destination root and, for moves, its source share. An IN-PLACE job (the
# default operation; artwork files on by default) on a library share under /mnt or /media
# is undone while the share is down: every track fails ("No such file", undo_partial),
# but the cover.jpg and .lrc files the job created are "gone", so _undo journals them as
# undone (executor.py:1477-1483: did("created") also when the file does not exist). Once
# the share is back, undo restores the tracks and reports success - and the job's
# cover.jpg (and lyrics files) stay in the album for good.
class R11_05_InPlaceUndoWhileTheShareIsDown(Base):
    def test_x(self):
        media = self.tmp / "media"
        mp = media / "nas"
        mp.mkdir(parents=True)
        state = {"on": True}
        root = mp / "music"
        shutil.move(str(self.lib), str(root))
        album = root / "Some Artist" / "2001 - Some Album"
        files = [str(album / "01 a.flac"), str(album / "02 b.flac")]
        front = self.tmp / "front.jpg"
        front.write_bytes(b"\xff\xd8\xff\xe0 a jpeg")
        prof = self.profile(sources=[{"path": str(root)}])
        with mock.patch.object(pl, "MOUNT_PARENTS", (str(media),)), \
                mock.patch.object(pl.os.path, "ismount", lambda p: state["on"] and str(p) == str(mp)):
            p = pl.build([self.proposal(source_dir=str(album), files=files, artwork={"front": str(front)})], prof)
            self.assertTrue(p["albums"][0]["artwork"]["file"])
            self.assertEqual(executor.Executor(p).run()["counts"], {"done": 1})
            self.assertTrue((album / "cover.jpg").exists())
            os.rename(mp, self.tmp / "unplugged")                  # the share drops
            mp.mkdir()
            state["on"] = False
            try:
                first = executor.undo(p["job"])
            except executor.StepError as exc:
                first = [f"refused: {exc}"]
            mp.rmdir()                                            # and comes back
            os.rename(self.tmp / "unplugged", mp)
            state["on"] = True
            try:
                second = executor.undo(p["job"])
            except executor.StepError as exc:
                second = [f"refused: {exc}"]
        self.assertFalse((album / "cover.jpg").exists(), f"the job's cover stays after undo: {first} / {second}")
        self.assertEqual(tagio.read(album / "01 a.flac")["album"], ["Some Album"])


# ---------------------------------------------------------------------------------------
# R11-06. The watcher never retries an album that was blocked or failed. Watcher.tick
# records the executor's outcome as the album's status (watcher.py:196-198: "blocked",
# "failed", "conflict"), and Watcher.due only retries "unresolved" and "provider_error"
# (watcher.py:126-129); anything else waits until the album's own files change. A copy
# profile to a USB stick or NAS share ("Car", "Lossy") whose destination is not mounted
# when an album arrives plans it "blocked" (destination not available) - and the album
# is never copied after the stick is plugged in again. The same for an album that failed
# for a passing reason (disk full, the share dropping for a moment, a file locked by
# another TagMender process): watched folders are never processed again.
class R11_06_WatcherNeverRetriesBlockedAlbums(Lossy):
    def test_x(self):
        old = time.time() - 3600
        for f in [self.album, *self.album.iterdir()]:
            os.utime(f, (old, old))                              # settled long ago
        FakeEngine.test = self
        stick = self.tmp / "stick"
        prof = self.profile(operation="copy", destination=str(stick), formats=LOSSY,
                            sources=[{"path": str(self.lib), "watch": True}], unattended=True,
                            watch={"settle_minutes": 1, "interval_seconds": 1, "workers": 1})
        w = watcher.Watcher(profiles=[prof], once=True)
        with mock.patch.object(watcher.eng_mod, "Engine", FakeEngine):
            first = w.tick()
            self.assertEqual(list(first.values()), ["blocked"])
            stick.mkdir()                                        # the stick is plugged in
            w.tick()
            w.tick()
        self.assertEqual(len([x for x in tree(stick) if x.endswith(".mp3")]), 2,
                         f"never copied once the destination was back: {tree(stick)}")


# ---------------------------------------------------------------------------------------
# R11-07. Cue sheets naming the tracks as ".wav" are not rewritten. EAC's "Create CUE
# sheet - multiple WAV files" (and most rip tools) write FILE "01 Title.wav" WAVE even
# when the tracks are compressed to FLAC; foobar2000 and CUETools find the track by its
# name with the extension swapped. plan._cue_rewrites (plan.py:408-420) looks entries up
# by the exact old file name only, so such a cue is moved unchanged: after the rename
# ("01 a.flac" -> "01.F.flac") it names files that are not there under any extension, and
# the rip's cue no longer fits its tracks (decision 10: "cue sheets and playlists are
# rewritten"). The same for a playlist or cue sheet whose extension differs only from
# the encoder's choice (".wav" in the cue of ".wv"/".ape" rips).
class R11_07_EacWavCueNotRewritten(Base):
    def test_x(self):
        (self.album / "rip.cue").write_text('FILE "01 a.wav" WAVE\n  TRACK 01 AUDIO\n    INDEX 01 00:00:00\n'
                                            'FILE "02 b.wav" WAVE\n  TRACK 02 AUDIO\n    INDEX 01 00:00:00\n')
        p = pl.build([self.proposal()], self.profile(operation="move", destination=str(self.dest)))
        self.assertEqual(executor.Executor(p).run()["counts"], {"done": 1})
        text = (self.dest / "Real Artist" / "2001 - Real Album" / "rip.cue").read_text()
        self.assertNotIn('"01 a.', text, text)
        self.assertIn('"01.F.', text, text)
        self.assertIn('"02.S.', text, text)


# ---------------------------------------------------------------------------------------
# R11-08. Undoing a copy profile's re-run and then its first run leaves every copy behind.
# A copy profile run again after the source's tags changed brings the copies' tags up to
# date (op "tag" on the copy, plan.py:615-624). Undoing that re-run restores the copy's
# old tags through executor._restore (a staged copy and os.replace, executor.py:480-494),
# which gives the copy a new modification time. Undoing the first run then judges each
# copy by size and modification time only (executor.py:1456-1458), finds it "changed
# after the job" and leaves it - every copy, its cover and cue stay in the lossy library
# or on the stick for good (`undo --force` does not help: that test ignores force). The
# copies carry exactly the tags that first run wrote (their tags_sig is journalled too).
class R11_08_CopyUndoneAfterItsReRunWasUndone(Lossy):
    def test_x(self):
        p1 = pl.build([self.proposal()], self.lossy())
        self.assertEqual(executor.Executor(p1).run()["counts"], {"done": 1})
        prop = self.proposal(targets=[target(1, "First", genre=["Ambient"]), target(2, "Second", genre=["Ambient"])])
        p2 = pl.build([prop], self.lossy())
        self.assertEqual(sorted(fe["op"] for fe in p2["albums"][0]["files"]), ["tag", "tag"])
        self.assertEqual(executor.Executor(p2).run()["counts"], {"done": 1})
        out2 = executor.undo(p2["job"])
        self.assertFalse([o for o in out2 if o.startswith("PROBLEM")], out2)
        out1 = executor.undo(p1["job"])
        self.assertEqual(tree(self.dest), [], f"copies left behind: {out1}")


# ---------------------------------------------------------------------------------------
# R11-09. Undo and rollback ignore "preserve timestamps". With the profile (or Picard's
# own setting, followed by default) keeping modification times, the job writes tags and
# keeps each file's mtime (executor.py:713-714). Undo puts the old tags back through
# executor._restore (executor.py:473-498): copy, mutagen save, os.replace - and nothing
# puts the mtime back, so every file of an undone album now carries the time of the undo.
# Players and tools ordering by date (foobar2000/MusicBee "date modified", rsync and
# backup jobs, a lossy copy profile deciding what changed) see the whole album as new;
# a user who chose to preserve timestamps gets them overwritten by undo (and by every
# rollback of a failed album).
class R11_09_UndoOverwritesPreservedTimestamps(Base):
    def test_x(self):
        for f in (self.f1, self.f2):
            os.utime(f, (1_600_000_000, 1_600_000_000))
        p = pl.build([self.proposal()], self.profile(preserve_timestamps=True))
        self.assertEqual(executor.Executor(p).run()["counts"], {"done": 1})
        self.assertEqual([int(os.stat(f).st_mtime) for f in (self.f1, self.f2)], [1_600_000_000] * 2)
        executor.undo(p["job"])
        self.assertEqual(tagio.read(self.f1)["album"], ["Some Album"])
        self.assertEqual([int(os.stat(f).st_mtime) for f in (self.f1, self.f2)], [1_600_000_000] * 2)


# ---------------------------------------------------------------------------------------
# R11-10. "Delete empty directories" is not Picard's: a folder holding only the junk files
# Windows Explorer and macOS put everywhere stays. executor.Executor.cleanup_dirs
# (executor.py:962) stops at any entry (`any(p.iterdir())`), and plan._companions never
# takes hidden files or Thumbs.db / desktop.ini (not companion extensions) along. Picard
# - whose option decision 10 follows - treats a folder holding only .DS_Store,
# desktop.ini, Desktop.ini or Thumbs.db as empty and removes it (picard/util/emptydir.py,
# JUNK_FILES, is_empty_dir / rm_empty_dir, metabrainz/picard@98e1ad1). On an SMB share
# browsed from Windows or a Mac, every album a move profile takes out of the inbox or
# reorganises leaves its old folder (and its artist folder) behind.
class R11_10_JunkOnlyFoldersLeftBehind(Base):
    def test_x(self):
        (self.album / ".DS_Store").write_bytes(b"\0\0\0\1Bud1")
        (self.album / "Thumbs.db").write_bytes(b"\xd0\xcf\x11\xe0")
        p = pl.build([self.proposal()], self.profile(operation="move", destination=str(self.dest)))
        self.assertEqual(executor.Executor(p).run()["counts"], {"done": 1})
        self.assertFalse(self.album.exists(), f"old album folder left: {tree(self.lib)}")

    def test_undo_puts_the_junk_back(self):
        (self.album / ".DS_Store").write_bytes(b"\0\0\0\1Bud1")
        p = pl.build([self.proposal()], self.profile(operation="move", destination=str(self.dest)))
        self.assertEqual(executor.Executor(p).run()["counts"], {"done": 1})
        out = executor.undo(p["job"])
        self.assertFalse([o for o in out if o.startswith("PROBLEM")], out)
        self.assertEqual((self.album / ".DS_Store").read_bytes(), b"\0\0\0\1Bud1")


# ---------------------------------------------------------------------------------------
# R11 (suspicion, confirmed here): folders made at the destination kept the owner and mode
# TagMender runs as (root on Unraid: root-owned 0755 album folders the share's users cannot
# write to), while files keep the original's owner. A new folder now takes its parent's.
class R11_11_NewFoldersTakeTheShareOwner(Base):
    def test_x(self):
        if os.geteuid() != 0:
            self.skipTest("needs root to give files away")
        os.chown(self.dest, 99, 100)                       # nobody:users
        os.chmod(self.dest, 0o2775)
        p = pl.build([self.proposal()], self.profile(operation="move", destination=str(self.dest)))
        self.assertEqual(executor.Executor(p).run()["counts"], {"done": 1})
        for d in (self.dest / "Real Artist", self.dest / "Real Artist" / "2001 - Real Album"):
            st = os.stat(d)
            self.assertEqual((st.st_uid, st.st_gid, st.st_mode & 0o7777), (99, 100, 0o2775), d)


if __name__ == "__main__":
    unittest.main(verbosity=2)
