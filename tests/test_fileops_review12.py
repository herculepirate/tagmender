# Twelfth independent review of TagMender's file operations (code at c2722f8). Each test
# asserts the correct behaviour and fails at c2722f8.
#   cd /home/claude/tagmender && TAGMENDER_DISCOGS_PLUGIN=/home/claude/herculepirate/picard-discogs-enrichment \
#     TAGMENDER_COVERUP_DIR=/home/claude/herculepirate/picard-coverup python3 -W ignore <this file>
# The repository is found at $TM_REPO (default /home/claude/tagmender). R12_07 needs root
# (it gives folders away to nobody:users, as on an Unraid share) and is skipped otherwise.
import os as _os, tempfile as _tempfile
_os.environ["XDG_CONFIG_HOME"] = _tempfile.mkdtemp(prefix="tm-config-")
_os.environ["XDG_STATE_HOME"] = _tempfile.mkdtemp(prefix="tm-state-")
import json
import os
import shutil
import sys
import time
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "tests"))
import exclusions as ex  # noqa: E402
import executor  # noqa: E402
import payload  # noqa: E402
import plan as pl  # noqa: E402
import tagio  # noqa: E402
import watcher  # noqa: E402
from test_executor import Base, audio, target  # noqa: E402
from test_fileops_review7 import LOSSY, Lossy, tree  # noqa: E402
from test_fileops_review11 import FakeEngine  # noqa: E402


class Crash(BaseException):
    """Stands for the process dying (power loss): nothing catches it."""


def latin1(folder: Path, name: bytes) -> Path:
    """A file name that is not valid UTF-8 (a Latin-1 zip unpacked on Linux), as Python
    hands it over: str with surrogate escapes."""
    return Path(os.fsdecode(os.fsencode(str(folder)) + b"/" + name))


# ---------------------------------------------------------------------------------------
# R12-01. The R11-03 fix (file names that are not valid UTF-8) stops at the executor: the
# PLANNER still encodes such names as strict UTF-8 and raises UnicodeEncodeError, which
# kills the whole plan - every album of `tagmender run ~/music` - before anything is shown:
#   - every copy profile, for any such TRACK name: Planner._earlier_copy (plan.py:774)
#     always builds the suffixed candidate with fnm.collision_suffix(str(src)), and
#     filenames.collision_suffix (filenames.py:93) does key.encode("utf-8");
#   - a move or copy of an album with such a COMPANION name ("Rückseite.jpg", "Björk -
#     Booklet.pdf" from the same Latin-1 archive): plan._fit_name (plan.py:263,
#     len(stem.encode("utf-8"))), called for every companion kept under its own name
#     (plan.py:701); the unresolved "move" action goes through _fit_name for every track
#     (plan.py:638), and a taken name through collision_suffix (plan.py:923), too.
# In-place jobs work since R11-03, so a user sees the "Lossy"/"Car" copy profile or the
# library organiser die with a traceback on the same files the default profile handles.
class R12_01_NonUtf8NamesCrashThePlanner(Lossy):
    def test_copy_profile(self):
        bad = latin1(self.album, b"02 Bj\xf6rk.mp3")
        os.rename(self.f2, bad)
        try:
            p = pl.build([self.proposal(files=[str(self.f1), str(bad)])], self.lossy())
        except UnicodeError as exc:
            self.fail(f"the copy plan crashed: {type(exc).__name__}: {exc}")
        self.assertEqual(executor.Executor(p).run()["counts"], {"done": 1})
        self.assertEqual(len([x for x in tree(self.dest) if x.endswith(".mp3")]), 2, tree(self.dest))

    def test_move_with_such_a_companion(self):
        latin1(self.album, b"R\xfcckseite.jpg").write_bytes(b"back")
        try:
            p = pl.build([self.proposal()], self.profile(operation="move", destination=str(self.dest)))
        except UnicodeError as exc:
            self.fail(f"the move plan crashed: {type(exc).__name__}: {exc}")
        self.assertEqual(executor.Executor(p).run()["counts"], {"done": 1})
        self.assertEqual(len(list((self.dest / "Real Artist" / "2001 - Real Album").iterdir())), 4,
                         tree(self.dest))


# ---------------------------------------------------------------------------------------
# R12-02. One file name that is not valid UTF-8 anywhere below a watched folder stops the
# WATCHER for good. Watcher.tick calls self.due() outside its per-album try
# (watcher.py:191); due() calls snapshot() (watcher.py:118), which hashes every file
# path with exclusions.signature (exclusions.py:406: f"{p}|..".encode() - strict UTF-8).
# The UnicodeEncodeError leaves tick() and Watcher.run (no try either), so `tagmender
# watch` exits with a traceback; restarted by systemd/Docker it dies at the same folder on
# its first pass, every time. Albums sorted after that folder are never processed, and a
# Thumbs.db or "Rückseite.jpg" is enough (snapshot looks at every file, not only audio).
# The R11-03 notes expected the watcher to retry such an album; it never gets that far.
class R12_02_NonUtf8NameStopsTheWatcher(Base):
    def test_x(self):
        latin1(self.album, b"R\xfcckseite.jpg").write_bytes(b"back")
        old = time.time() - 3600
        for f in [self.album, *self.album.iterdir()]:
            os.utime(f, (old, old))
        FakeEngine.test = self
        prof = self.profile(sources=[{"path": str(self.lib), "watch": True}], unattended=True,
                            watch={"settle_minutes": 1, "interval_seconds": 1, "workers": 1})
        w = watcher.Watcher(profiles=[prof], once=True)
        with mock.patch.object(watcher.eng_mod, "Engine", FakeEngine):
            try:
                done = w.tick()
            except UnicodeError as exc:
                self.fail(f"the watcher died: {type(exc).__name__}: {exc}")
        self.assertEqual(list(done.values()), ["done"], done)
        self.assertEqual(tagio.read(self.f1)["album"], ["Real Album"])


# ---------------------------------------------------------------------------------------
# R12-03. An album excluded by a rule whose matching track has a name that is not valid
# UTF-8 is reported "Unavailable / could not be started" instead of "Ignored", and the
# history never records it. Executor.album (executor.py:629-631) logs the exclusion with
# exclusions.State.log_raw (executor.py:630), which stores `detail` (the rule's "matched": the track's file
# name, exclusions.py:378) as it is (exclusions.py:490) - R11-03 gave State.log the hex
# encoding (_t) but not log_raw - and sqlite refuses the surrogates. Every run of such a
# library ends "partial" (exit code 2, a cron job reports a failure) for an album that
# was only meant to be left alone.
class R12_03_ExcludedNonUtf8AlbumReportedUnavailable(Base):
    def test_x(self):
        ex._write(ex.rules_file(), {"rules": [{"id": "keep-out", "name": "Keep out", "target": "album",
                                               "action": "ignore",
                                               "when": {"field": "album", "op": "contains", "value": "Some"}}]})
        bad = latin1(self.album, b"01 Bj\xf6rk.flac")
        os.rename(self.f1, bad)
        p = pl.build([self.proposal(files=[str(bad), str(self.f2)])], self.profile())
        self.assertEqual(p["albums"][0]["status"], "excluded")
        res = executor.Executor(p).run()
        self.assertEqual(res["counts"], {"excluded": 1}, res["albums"])
        self.assertEqual(res["status"], "done")


# ---------------------------------------------------------------------------------------
# R12-04. `recover` (run by hand, and by the watcher at the start of EVERY tick,
# watcher.py:169-171) has no share check: R10-06/R11-05 taught undo to refuse while a
# share is unmounted (executor._undo_unreachable), but recover rolls an unfinished album
# back regardless. Power lost in the middle of a move from a NAS share (inbox ->
# library); the client reboots before the NAS, so the share's mount point under /mnt or
# /media is empty when the watcher starts:
#   - _undo_step (executor.py:1158) recreates the album's source folders on the SYSTEM
#     DISK under the empty mount point, then fails to find the moved track;
#   - the failure is only a "problem": the album is journalled rolled back, the job
#     "recovered" (executor._recover, executor.py:1282-1435), and recover never looks at it again ("finished
#     normally; nothing to recover").
# When the share is back the album stays half-done - the first track in the library with
# its new name and tags, the rest in the inbox - which recover promises never to leave
# ("Albums are never left half-done"); the next watcher tick then plans the remnant as an
# album of its own. A copy job to a USB stick that was unplugged after the power cut
# loses track of its partial copies the same way.
class R12_04_RecoverWhileTheShareIsDown(Base):
    def test_x(self):
        media = self.tmp / "media"
        mp = media / "nas"
        mp.mkdir(parents=True)
        on = {"v": True}
        src_root, music = mp / "downloads", mp / "music"
        shutil.move(str(self.lib), str(src_root))
        music.mkdir()
        album = src_root / "Some Artist" / "2001 - Some Album"
        files = [str(album / "01 a.flac"), str(album / "02 b.flac")]
        prof = self.profile(sources=[{"path": str(src_root)}], operation="move", destination=str(music))
        real = executor.Journal.write

        def jw(j, **ev):
            if ev.get("ev") == "file" and ev.get("state") == "begin" and ev.get("idx") == 1:
                raise Crash()                                  # power lost after the first track
            return real(j, **ev)
        with mock.patch.object(pl, "MOUNT_PARENTS", (str(media),)), \
                mock.patch.object(pl.os.path, "ismount", lambda p: on["v"] and str(p) == str(mp)):
            p = pl.build([self.proposal(source_dir=str(album), files=files)], prof)
            with mock.patch.object(executor.Journal, "write", jw), self.assertRaises(Crash):
                executor.Executor(p).run()
            os.rename(mp, self.tmp / "unplugged")              # after the reboot the NAS is not up yet
            mp.mkdir()
            on["v"] = False
            try:
                first = executor.recover(p["job"])
            except executor.StepError as exc:
                first = [f"refused: {exc}"]
            on_system_disk = sorted(str(x.relative_to(mp)) for x in mp.rglob("*"))
            shutil.rmtree(mp)                                  # the share mounts again
            os.rename(self.tmp / "unplugged", mp)
            on["v"] = True
            try:
                second = executor.recover(p["job"])
            except executor.StepError as exc:
                second = [f"refused: {exc}"]
        self.assertEqual(on_system_disk, [], f"written under the empty mount point: {first}")
        self.assertEqual(sorted(x for x in tree(mp) if x.endswith(".flac")),
                         ["downloads/Some Artist/2001 - Some Album/01 a.flac",
                          "downloads/Some Artist/2001 - Some Album/02 b.flac"],
                         f"album left half-done: {first} / {second}")


# ---------------------------------------------------------------------------------------
# R12-05. The R11-06 fix covers unattended profiles only. A watched profile that is NOT
# unattended (the documented default: plans wait in ~/.local/state/tagmender/plans for
# `tagmender apply`) records every album as "planned" (watcher.py:205-209, 213-215), whatever
# the plan says. A copy profile ("Car", "Lossy") whose stick or share is not mounted when
# an album arrives saves a plan in which the album is "blocked" - nothing to apply - and,
# being "planned", the album is never planned again once the destination is back
# (Watcher.due: "planned" is neither RETRIED nor "waiting_destination"). It is never
# copied until its own files change.
class R12_05_PlanOnlyWatcherNeverReplansABlockedAlbum(Lossy):
    def test_x(self):
        old = time.time() - 3600
        for f in [self.album, *self.album.iterdir()]:
            os.utime(f, (old, old))
        FakeEngine.test = self
        stick = self.tmp / "stick"
        prof = self.profile(operation="copy", destination=str(stick), formats=LOSSY,
                            sources=[{"path": str(self.lib), "watch": True}], unattended=False,
                            watch={"settle_minutes": 1, "interval_seconds": 1, "workers": 1})
        w = watcher.Watcher(profiles=[prof], once=True)
        with mock.patch.object(watcher.eng_mod, "Engine", FakeEngine):
            w.tick()
            stick.mkdir()                                       # the stick is plugged in
            w.tick()
            w.tick()
        waiting = [json.loads(x.read_text()) for x in sorted((ex.STATE_DIR / "plans").glob("*.json"))]
        ready = [a for p in waiting for a in p["albums"] if a["status"] == "ready" and pl.has_work(a)]
        self.assertTrue(ready, f"no plan to copy the album once the destination was back: "
                               f"{[[a['status'] for a in p['albums']] for p in waiting]}")


# ---------------------------------------------------------------------------------------
# R12-06. DATA LOSS (tag edits): undo of a copy whose destination already was a hard
# link to the source ("relink_copy": a library hard-linked from the seeding folder by
# Lidarr/*arr, or made with `cp -al`) puts the SOURCE's old tags back over edits made to
# the source after the job. In executor._undo_relink (executor.py:1181-1182) the branch
# "an earlier, interrupted undo got this far" restores the tag backup onto src whenever
# src's tags differ from it. That fits relink_move (where undo recreates src), but a
# relink_copy never writes src at all, so for it the difference can only be the user's
# own later edits - a genre fixed, a rating set in foobar2000 or Picard on the source
# library. The undo pre-pass (executor.py:1520-1542) only looks at dst, so nothing warns;
# undo reports success and the edits are gone (the plain undo, no --force).
class R12_06_RelinkCopyUndoRevertsLaterSourceEdits(Lossy):
    def test_x(self):
        home = self.dest / "Real Artist" / "2001 - Real Album"
        home.mkdir(parents=True)
        os.link(self.f1, home / "01.F.mp3")                    # hard-linked copies already there
        os.link(self.f2, home / "02.S.mp3")
        p = pl.build([self.proposal()], self.lossy())
        self.assertTrue(all(fe.get("linked") and fe["op"] == "copy" for fe in p["albums"][0]["files"]))
        self.assertEqual(executor.Executor(p).run()["counts"], {"done": 1})
        tagio.write(self.f1, {"genre": ["Shoegaze"]})          # edited in the source library later
        out = executor.undo(p["job"])
        self.assertEqual(tagio.read(self.f1).get("genre"), ["Shoegaze"],
                         f"undo put old tags over a later edit of a file the job never wrote: {out}")


# ---------------------------------------------------------------------------------------
# R12-07. The R11-11 fix (a folder TagMender makes takes its parent's owner, group and
# mode) covers the job's own folders only (_make_dirs). Undo recreates the source folders
# the move had removed with a plain mkdir (executor._undo, executor.py:1583-1585, and
# _undo_step's src.parent.mkdir(parents=True), executor.py:1115 and 1158, _undo_relink 1175; rollback and
# recover go through the same _undo_step). TagMender run as root on an Unraid share
# (nobody:users, 0777/2775): undo puts the album back into folders owned by root with
# mode 0755 - the share's users can no longer add, rename or delete anything in the album
# or artist folder over SMB, exactly what R11-11 set out to stop. The folders were not
# "put back as they were".
class R12_07_UndoRecreatesFoldersWithoutTheShareOwner(Base):
    def test_x(self):
        if os.geteuid() != 0:
            self.skipTest("needs root to give folders away")
        for d in (self.lib, self.lib / "Some Artist", self.album):
            os.chown(d, 99, 100)                              # nobody:users
            os.chmod(d, 0o2775)
        p = pl.build([self.proposal()], self.profile(operation="move", destination=str(self.dest)))
        self.assertEqual(executor.Executor(p).run()["counts"], {"done": 1})
        self.assertFalse(self.album.exists())
        out = executor.undo(p["job"])
        self.assertFalse([o for o in out if o.startswith("PROBLEM")], out)
        for d in (self.lib / "Some Artist", self.album):
            st = os.stat(d)
            self.assertEqual((st.st_uid, st.st_gid, oct(st.st_mode & 0o7777)), (99, 100, oct(0o2775)), d)


# ---------------------------------------------------------------------------------------
# R12-08. Undo leaves the job's destination folders behind once Windows or macOS has
# looked at them. Since c2722f8 a folder holding only .DS_Store / Thumbs.db /
# desktop.ini counts as empty when a move cleans up (Picard's JUNK_FILES), but undo
# removes the folders the job made with a bare rmdir (executor._undo, executor.py:
# 1605-1611; ENOTEMPTY is ignored silently). Browsing the new album folder from a Mac
# (Finder writes .DS_Store into every folder it opens on an SMB share) or from Windows
# Explorer (Thumbs.db next to cover.jpg) between the job and the undo is enough: undo
# reports success and "Real Artist/2001 - Real Album/" stays in the library holding only
# that file - an empty album folder (and artist folder) in the library for good.
class R12_08_UndoLeavesJunkOnlyFolders(Base):
    def test_x(self):
        p = pl.build([self.proposal()], self.profile(operation="move", destination=str(self.dest)))
        self.assertEqual(executor.Executor(p).run()["counts"], {"done": 1})
        (self.dest / "Real Artist" / "2001 - Real Album" / ".DS_Store").write_bytes(b"\0\0\0\1Bud1")
        out = executor.undo(p["job"])
        self.assertFalse([o for o in out if o.startswith("PROBLEM")], out)
        self.assertEqual(sorted(x.name for x in self.album.iterdir()), ["01 a.flac", "02 b.flac", "rip.cue", "rip.log"])
        self.assertFalse((self.dest / "Real Artist").exists(), f"left in the library: {tree(self.dest)}")


# ---------------------------------------------------------------------------------------
# R12-09. Undo's own advice does not work. Since c2722f8 an album "moves back whole, or
# not at all": when one track stays, every other track gets "stays with the rest of its
# album (a track of it changed after the job; tagmender undo --force JOB puts the album
# back anyway)" (executor.py:1543-1547). But when the track stays because it "holds
# different audio now" (re-ripped or replaced with a better copy, MP3Gain/"apply
# ReplayGain to file content" run on it), --force changes nothing: that verdict ignores
# force (executor.py:1535-1536) and still puts the album in `staying`. Following the
# advice, `undo --force` prints the same refusal again; the album can never be undone,
# and undo keeps saying how it could be.
class R12_09_UndoForceAdviceDoesNothing(Base):
    def test_x(self):
        p = pl.build([self.proposal()], self.profile(operation="move", destination=str(self.dest)))
        self.assertEqual(executor.Executor(p).run()["counts"], {"done": 1})
        home = self.dest / "Real Artist" / "2001 - Real Album"
        os.replace(audio(self.tmp / "rerip.flac", 880, title="First"), home / "01.F.flac")   # replaced
        first = executor.undo(p["job"])
        advice = [o for o in first if "undo --force" in o and "02.S.flac" in o]
        forced = executor.undo(p["job"], force=True)
        if advice:                                   # it said --force would put the album back
            self.assertTrue((self.album / "02 b.flac").exists(),
                            f"undo advised --force, which did nothing: {advice[0]} / then {forced}")


# ---------------------------------------------------------------------------------------
# R12-10. Undo of a COPY job is refused while the SOURCE share is unmounted, though undoing
# a copy only removes the copies. _undo_unreachable deliberately exempts copies from the
# source-share check (executor.py:1489-1491, `f.get("op") != "copy"`), but the per-path
# check added in c2722f8 (executor.py:1492-1497) tests the folder of every src too.
# The source library on a NAS (/mnt/nas/music), a copy profile to a local "Lossy" folder
# or a USB stick: away from home (laptop off the LAN), or with the NAS down, `tagmender
# undo JOB` says "the share holding /mnt/nas/music/... is not mounted; nothing was
# undone" - the copies cannot be taken off the stick until the NAS is reachable again.
class R12_10_CopyUndoNeedsTheSourceShare(Lossy):
    def test_x(self):
        media = self.tmp / "media"
        mp = media / "nas"
        mp.mkdir(parents=True)
        on = {"v": True}
        root = mp / "music"
        shutil.move(str(self.lib), str(root))
        album = root / "Some Artist" / "2001 - Some Album"
        files = [str(album / "01 a.mp3"), str(album / "02 b.mp3")]
        prof = self.profile(sources=[{"path": str(root)}], operation="copy", destination=str(self.dest),
                            formats=LOSSY)
        with mock.patch.object(pl, "MOUNT_PARENTS", (str(media),)), \
                mock.patch.object(pl.os.path, "ismount", lambda p: on["v"] and str(p) == str(mp)):
            p = pl.build([self.proposal(source_dir=str(album), files=files)], prof)
            self.assertEqual(executor.Executor(p).run()["counts"], {"done": 1})
            self.assertEqual(len(tree(self.dest)), 3)
            os.rename(mp, self.tmp / "unplugged")              # the NAS is not reachable
            mp.mkdir()
            on["v"] = False
            try:
                out = executor.undo(p["job"])
            except executor.StepError as exc:
                out = [f"refused: {exc}"]
        self.assertEqual(tree(self.dest), [], f"the copies stay: {out}")


# ---------------------------------------------------------------------------------------
# R12 (suspicion, confirmed here): an in-place job on a plain folder under /mnt or /media
# (not a mount point: a library kept on the system disk there) applied fine, but undo was
# refused for ever ("not mounted"). Folders that were no mounted share when the job ran are
# journalled; undo and recover only refuse a share that went away.
class R12_11_PlainFolderUnderMntUndoable(Base):
    def test_x(self):
        media = self.tmp / "media"
        root = media / "music"
        media.mkdir()
        shutil.move(str(self.lib), str(root))
        album = root / "Some Artist" / "2001 - Some Album"
        files = [str(album / "01 a.flac"), str(album / "02 b.flac")]
        with mock.patch.object(pl, "MOUNT_PARENTS", (str(media),)), \
                mock.patch.object(pl.os.path, "ismount", lambda p: False):
            p = pl.build([self.proposal(source_dir=str(album), files=files)], self.profile(sources=[{"path": str(root)}]))
            self.assertEqual(executor.Executor(p).run()["counts"], {"done": 1})
            out = executor.undo(p["job"])
        self.assertFalse([o for o in out if o.startswith("PROBLEM")], out)
        self.assertEqual(tagio.read(album / "01 a.flac")["album"], ["Some Album"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
