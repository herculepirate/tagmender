# Thirteenth independent review of TagMender's file operations (code at 87f5fa2). Each test
# asserts the correct behaviour and fails at 87f5fa2.
#   cd /home/claude/tagmender && TAGMENDER_DISCOGS_PLUGIN=/home/claude/herculepirate/picard-discogs-enrichment \
#     TAGMENDER_COVERUP_DIR=/home/claude/herculepirate/picard-coverup python3 -W ignore <this file>
# The repository is found at $TM_REPO (default /home/claude/tagmender). R13_04 and R13_08 need
# root (mounting tmpfs; giving files away to nobody:users as on an Unraid share), R13_06
# needs PySide6 (run offscreen); they are skipped otherwise.
import os as _os, tempfile as _tempfile
_os.environ["XDG_CONFIG_HOME"] = _tempfile.mkdtemp(prefix="tm-config-")
_os.environ["XDG_STATE_HOME"] = _tempfile.mkdtemp(prefix="tm-state-")
import os
import shutil
import subprocess
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
import plan as pl  # noqa: E402
import tagio  # noqa: E402
import watcher  # noqa: E402
from test_executor import Base, audio, target  # noqa: E402
from test_fileops_review7 import LOSSY, Lossy, tree  # noqa: E402
from test_fileops_review11 import FakeEngine  # noqa: E402


class Crash(BaseException):
    """Stands for the process dying (power loss, kill, Ctrl+C): nothing catches it."""


def front_jpg(tmp: Path) -> Path:
    from PIL import Image
    front = tmp / "front.jpg"
    Image.new("RGB", (600, 600), (10, 20, 30)).save(front)
    return front


ART_FILE = {"enabled": True, "embed_front": False, "files": True}


def crash_at_first_file_undo_step(test, job):
    """Run undo and die right after its first track was put back, before that is journalled."""
    real = executor.Journal.write

    def jw(j, **ev):
        if ev.get("ev") == "undo_step" and ev["key"][0] == "file":
            raise Crash()
        return real(j, **ev)
    with mock.patch.object(executor.Journal, "write", jw), test.assertRaises(Crash):
        executor.undo(job)


# ---------------------------------------------------------------------------------------
# R13-01. Undo of a copy whose destination already was a hard link of the source
# ("relink_copy": a lossy library made with `cp -al`, an *arr hard-link import) brings back
# a SOURCE the user deleted after the job. executor._undo_relink (executor.py:1199-1206)
# starts with `if not src.exists():` and, for relink_move and relink_copy alike, recreates
# src's folders (mkdirs_like_parent) and a full copy of the track there (_put_back, with the
# old tags restored), then links dst to it. A copy never touched its source (the docstring
# says so), and the verdict pre-pass looks at dst only. The user removes the album from the
# main library (or the download client deletes its seeding folder once the ratio is met)
# and keeps the lossy copy; `tagmender undo JOB` of the copy job - to get the lossy copy's
# old tags back - resurrects "Some Artist/2001 - Some Album/01 a.mp3 ..." in the source
# library, a new file the size of the track (no longer a hard link), and reports success.
class R13_01_RelinkCopyUndoResurrectsADeletedSource(Lossy):
    def test_x(self):
        home = self.dest / "Real Artist" / "2001 - Real Album"
        home.mkdir(parents=True)
        os.link(self.f1, home / "01.F.mp3")                    # the lossy library: hard links
        os.link(self.f2, home / "02.S.mp3")
        p = pl.build([self.proposal()], self.lossy())
        self.assertTrue(all(fe.get("linked") and fe["op"] == "copy" for fe in p["albums"][0]["files"]))
        self.assertEqual(executor.Executor(p).run()["counts"], {"done": 1})
        shutil.rmtree(self.lib / "Some Artist")                # removed from the main library
        out = executor.undo(p["job"])
        self.assertFalse((self.lib / "Some Artist").exists(),
                         f"undo of a copy brought a deleted source back: {tree(self.lib)} ({out})")
        self.assertEqual(tagio.read(home / "01.F.mp3")["album"], ["Some Album"], out)


# ---------------------------------------------------------------------------------------
# R13-02. Undo splits an album when one of its COMPANION files was changed after the job:
# the music moves back, the changed file stays alone in the library folder. R10-05/R11-04
# made "an album moves back whole, or not at all" for tracks (executor._undo, verdict and
# `staying`, executor.py:1567-1597), but a companion or a cover/lyrics file the job made
# that changed since only becomes a PROBLEM (executor.py:1622-1626, 1610-1612) while every
# track of the album is still put back. foobar2000 with ESLyric/OpenLyrics updates the
# ".lrc" next to a track when it is played; a cue sheet fixed in a text editor, a cover
# replaced by a better scan: `tagmender undo JOB` puts the album back into the inbox and
# leaves "Real Artist/2001 - Real Album/01.F.lrc" (and its folders) in the library, the
# lyrics no longer next to their track. Running undo again changes nothing. (For a moved,
# not rewritten companion, moving it back with its edit would lose nothing.)
class R13_02_ChangedCompanionStaysAloneAfterUndo(Base):
    def test_x(self):
        (self.album / "01 a.lrc").write_text("[00:00.00]la la\n")
        p = pl.build([self.proposal()], self.profile(operation="move", destination=str(self.dest)))
        self.assertEqual(executor.Executor(p).run()["counts"], {"done": 1})
        home = self.dest / "Real Artist" / "2001 - Real Album"
        time.sleep(0.01)
        (home / "01.F.lrc").write_text("[00:00.00]la la la (fixed)\n")   # the lyrics plugin updated it
        out = executor.undo(p["job"])
        together = ((home / "01.F.lrc").exists() and (home / "01.F.flac").exists()) or \
            ((self.album / "01 a.lrc").exists() and (self.album / "01 a.flac").exists())
        self.assertTrue(together, f"the album was split: library {tree(self.dest)}, inbox {tree(self.lib)} ({out})")
        lrc = home / "01.F.lrc" if (home / "01.F.lrc").exists() else self.album / "01 a.lrc"
        self.assertIn("(fixed)", lrc.read_text(), "the edit was lost")


# ---------------------------------------------------------------------------------------
# R13-03. The watcher waits retry.after_days (30 by default) for an unresolved album whose
# unresolved.move_to share was not mounted, instead of moving it as soon as the share is
# back. Watcher.tick (watcher.py:219-224) gives "unresolved" precedence over the plan's
# "blocked": the album is recorded "unresolved" with next_try = now + after_days, so neither
# the "waiting_destination" path (R11-06/R12-05) nor the short back-off of "blocked" applies.
# An Inbox profile (unresolved action "move" to "_unresolved" on the NAS) during a NAS
# reboot: every album that arrives and is not identified stays in the inbox, unmoved and
# unmarked (a blocked album gets no TAGMENDER=Unresolved either), for a month - and each
# such wait uses up one of retry.max_attempts. Unattended and plan-only alike.
class UnresolvedEngine(FakeEngine):
    calls = []

    def identify(self, g):
        UnresolvedEngine.calls.append(str(g.folder))
        return pl.Proposal(source_dir=str(g.folder), files=[str(f) for f in g.files], status="unresolved",
                           reason="no candidate", reason_codes=["no_candidate"])


class R13_03_UnresolvedMoveWaitsDaysForItsShare(Base):
    def test_x(self):
        old = time.time() - 3600
        for f in self.album.iterdir():
            os.utime(f, (old, old))
        held = self.tmp / "nas" / "_unresolved"                # the share is not there yet
        prof = self.profile(sources=[{"path": str(self.lib), "watch": True}], unattended=True,
                            operation="move", destination=str(self.dest),
                            unresolved={"action": "move", "move_to": str(held)},
                            watch={"settle_minutes": 1, "interval_seconds": 1, "workers": 1})
        w = watcher.Watcher(profiles=[prof], once=True)
        with mock.patch.object(watcher.eng_mod, "Engine", UnresolvedEngine):
            w.tick()
            held.mkdir(parents=True)                           # the NAS is up again
            w.tick()
            w.tick()
        self.assertTrue([x for x in tree(self.tmp / "nas") if x.endswith(".flac")],
                        f"not moved once _unresolved was back: inbox {tree(self.lib)}")


# ---------------------------------------------------------------------------------------
# R13-04. A saved plan (`tagmender plan --output`, or the plans a watched profile that is not
# unattended leaves in ~/.local/state/tagmender/plans) can no longer be applied once the
# destination share was mounted again with another device number. Executor.album
# (executor.py:670-677) compares os.stat(dest).st_dev with the number recorded at planning
# time; NFS, CIFS and FUSE mounts (Unraid's /mnt/user is shfs/FUSE) get an anonymous device
# number when mounted, USB disks one by detection order, so after a reboot, an array
# stop/start or an autofs remount the number often differs. _undo_unreachable says exactly
# this ("not its device number: a share remounted after a reboot may get another",
# executor.py:1534) but apply does not: every move/copy album of the plan is refused
# "destination ... is not the one planned (unmounted?)" while the share is mounted. And a
# plan-only watcher recorded the album "planned": it is never planned again (Watcher.due
# retries neither "planned" nor a refused apply) until its files change.
@unittest.skipUnless(os.geteuid() == 0, "needs root to mount tmpfs")
class R13_04_SavedPlanRefusedAfterRemount(Base):
    def test_x(self):
        share, other = self.tmp / "share", self.tmp / "other"
        share.mkdir()
        other.mkdir()
        subprocess.run(["mount", "-t", "tmpfs", "none", str(share)], check=True)
        try:
            (share / "music").mkdir()
            p = pl.build([self.proposal()], self.profile(operation="move", destination=str(share / "music")))
            pl.save(p, self.tmp / "plan.json")
            subprocess.run(["umount", str(share)], check=True)       # a reboot ...
            subprocess.run(["mount", "-t", "tmpfs", "none", str(other)], check=True)   # another mount first
            subprocess.run(["mount", "-t", "tmpfs", "none", str(share)], check=True)
            (share / "music").mkdir()                                # the same (empty) library folder
            res = executor.Executor(pl.load(self.tmp / "plan.json")).run()
            self.assertEqual(res["counts"], {"done": 1}, res["albums"])
        finally:
            subprocess.run(["umount", str(share)], capture_output=True)
            subprocess.run(["umount", str(other)], capture_output=True)


# ---------------------------------------------------------------------------------------
# R13-05. `recover` of an interrupted COPY job is refused while the SOURCE share is down,
# though putting a copy album back only removes copies at the destination. The R12-04 share
# check (executor._recover, executor.py:1315-1321) tests the folder of every src and dst of
# the unfinished albums - R12-10 exempted the sources of copies from undo's check
# (executor.py:1540-1541) but not here. Power lost while the "Lossy"/"Car" profile copies
# from the NAS to a local folder or a stick; the laptop starts away from home (or before the
# NAS): `tagmender recover` and the watcher (every tick, watcher.py:167-174) refuse, so the
# job stays pending and EVERY new job of every profile is refused ("job ... was interrupted;
# run `tagmender recover` first", executor.py:612-616) and the watcher processes nothing at
# all until that NAS is reachable again.
class R13_05_RecoverOfACopyNeedsTheSourceShare(Lossy):
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
        real = executor.Journal.write

        def jw(j, **ev):
            if ev.get("ev") == "file" and ev.get("state") == "begin" and ev.get("idx") == 1:
                raise Crash()                                  # power lost after the first copy
            return real(j, **ev)
        with mock.patch.object(pl, "MOUNT_PARENTS", (str(media),)), \
                mock.patch.object(pl.os.path, "ismount", lambda p: on["v"] and str(p) == str(mp)):
            p = pl.build([self.proposal(source_dir=str(album), files=files)], prof)
            with mock.patch.object(executor.Journal, "write", jw), self.assertRaises(Crash):
                executor.Executor(p).run()
            self.assertEqual(len(tree(self.dest)), 1)
            os.rename(mp, self.tmp / "unplugged")              # the NAS is not reachable
            mp.mkdir()
            on["v"] = False
            try:
                out = executor.recover(p["job"])
            except executor.StepError as exc:
                out = [f"refused: {exc}"]
        self.assertEqual(tree(self.dest), [], f"the partial copy stays: {out}")
        self.assertEqual(executor.pending_jobs(), [], f"the job blocks every other job: {out}")


# ---------------------------------------------------------------------------------------
# R13-06. The desktop app's "Recover interrupted jobs" (gui.Jobs.recover, gui.py:621-627)
# calls executor.recover without catching StepError, unlike Jobs.undo (gui.py:614-618). Since
# R12-04 recover raises when a share is down, and it always did for a job another process is
# running - and pending_jobs() also lists running jobs, so the watcher's job in progress is
# announced "A job was interrupted. Recover it under Jobs." (gui.py:1330) and enables the
# button. Clicking it: the exception leaves the Qt slot (printed to stderr only), the output
# box stays empty, nothing says why, and the list is not reloaded.
class R13_06_GuiRecoverSaysNothing(Base):
    def test_x(self):
        try:
            os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
            import gui
        except ImportError as exc:
            self.skipTest(f"no PySide6: {exc}")
        gui.QApplication.instance() or gui.QApplication([])
        p = pl.build([self.proposal()], self.profile())
        real = executor.Journal.write

        def jw(j, **ev):
            if ev.get("ev") == "album_begin":
                raise Crash()
            return real(j, **ev)
        with mock.patch.object(executor.Journal, "write", jw), self.assertRaises(Crash):
            executor.Executor(p).run()
        page = gui.Jobs(None)
        with executor.job_lock(p["job"]):                     # the watcher is recovering/running it
            try:
                page.recover()
            except executor.StepError as exc:
                self.fail(f"the Recover button raised: {exc}")
        self.assertTrue(page.out.toPlainText().strip(), "nothing shown")


# ---------------------------------------------------------------------------------------
# R13-07. Undoing an older job before a newer one that moved the same album leaves the
# older job's cover.jpg and rewritten cue sheet in the library for good. The first undo
# cannot find the album where it put it (the newer job re-organised it), yet it
#   - journals the cover.jpg it made as removed because it is "gone" (executor._undo,
#     executor.py:1607-1613: did("created") also when the file is not there - R11-05 made
#     that safe for an unmounted share only), and
#   - "puts back" the rewritten rip.cue from its saved original (executor._undo_step,
#     executor.py:1143-1148: _put_back(original) while dst is missing) and journals it done.
# Then undo of the newer job moves cover.jpg and the job's rip.cue back to the older job's
# folder, and the older job's second undo skips both as done: "Real Artist/2001 - Real
# Album/" stays in the library holding cover.jpg and a cue naming tracks that are gone,
# while the inbox has the album (and its original rip.cue). A user reverting a library
# re-organisation (`naming` preset changed, job B) and the inbox import before it (job A)
# naturally undoes A first; undo promises it "can simply be run again".
class R13_07_UndoOutOfOrderLeavesCoverAndCue(Base):
    def test_x(self):
        front = front_jpg(self.tmp)
        pa = pl.build([self.proposal(artwork={"front": str(front), "source": "test"})],
                      self.profile(operation="move", destination=str(self.dest), artwork=ART_FILE))
        self.assertEqual(executor.Executor(pa).run()["counts"], {"done": 1})
        x = self.dest / "Real Artist" / "2001 - Real Album"
        self.assertTrue((x / "cover.jpg").exists())
        pb = pl.build([self.proposal(source_dir=str(x), files=[str(x / "01.F.flac"), str(x / "02.S.flac")])],
                      self.profile(operation="move", destination=str(self.dest), sources=[{"path": str(self.dest)}],
                                   naming={"preset": "picard"}, artwork=ART_FILE))
        self.assertEqual(executor.Executor(pb).run()["counts"], {"done": 1})
        out = [executor.undo(pa["job"]), executor.undo(pb["job"]), executor.undo(pa["job"])]
        self.assertEqual(tree(self.dest), [], f"left in the library: {out}")
        self.assertEqual(tree(self.lib), ["Some Artist/2001 - Some Album/01 a.flac",
                                          "Some Artist/2001 - Some Album/02 b.flac",
                                          "Some Artist/2001 - Some Album/rip.cue",
                                          "Some Artist/2001 - Some Album/rip.log"])


# ---------------------------------------------------------------------------------------
# R13-08. On an Unraid share (TagMender runs as root; share files and folders belong to
# nobody:users, 0777/0666) the cover.jpg and the .lrc lyrics files a job creates belong to
# root:root (0644), while every track, companion and - since R11-11/R12-07 - every folder
# takes the share's owner. Executor.cover_file and Executor.new_file (executor.py:984-1027)
# copy/write a staging file and place it, with no _keep_owner and no owner from the folder.
# The share's users then cannot overwrite them over SMB/NFS: Picard's "save cover art"
# with "overwrite", foobar2000's lyrics plugins and Mp3tag get "access denied" for exactly
# these files. (Junk files put back by undo, shutil.copy2 at executor.py:1640, likewise.)
@unittest.skipUnless(os.geteuid() == 0, "needs root to give files away")
class R13_08_CreatedCoverAndLyricsOwnedByRoot(Base):
    def test_x(self):
        for d in (self.dest, self.lib, self.lib / "Some Artist", self.album, *self.album.iterdir()):
            os.chown(d, 99, 100)                               # nobody:users
        os.chmod(self.dest, 0o777)
        p = pl.build([self.proposal(artwork={"front": str(front_jpg(self.tmp)), "source": "test"},
                                    sidecars={str(self.f1): "[00:00.00]la la"})],
                     self.profile(operation="move", destination=str(self.dest), artwork=ART_FILE))
        self.assertEqual(executor.Executor(p).run()["counts"], {"done": 1})
        home = self.dest / "Real Artist" / "2001 - Real Album"
        owners = {x.name: (os.stat(x).st_uid, os.stat(x).st_gid) for x in home.iterdir()}
        self.assertEqual(owners, {n: (99, 100) for n in owners})


# ---------------------------------------------------------------------------------------
# R13-09. Undo strips the library album of its cover, log and cue when the album was
# downloaded again into the inbox after the job, and leaves its tracks. executor._undo puts
# companions back before tracks (executor.py:1614-1632): for each, _undo_step finds an
# identical file at the source and deletes the job's file at the destination ("the source
# is (back) in place already", executor.py:1135-1139; for the rewritten cue it compares with
# the saved original). Then every track fails "cannot move back: ... exists"
# (executor.py:1180-1185) - a step-time error, not a verdict, so "the album moves back whole,
# or not at all" never applies. A torrent client that re-checks a seeding folder the move
# emptied and downloads it again does exactly this. Result: the library album keeps its
# music without cover.jpg, rip.log and the cue sheet naming its new files; the user, told
# the inbox copy is in the way, deletes it - and the album has no cover/log/cue anywhere.
class R13_09_UndoStripsTheLibraryAlbumOfItsCompanions(Base):
    def test_x(self):
        (self.album / "cover.jpg").write_bytes(b"front")
        download = self.tmp / "download"
        shutil.copytree(self.album, download)
        p = pl.build([self.proposal()], self.profile(operation="move", destination=str(self.dest)))
        self.assertEqual(executor.Executor(p).run()["counts"], {"done": 1})
        shutil.copytree(download, self.album)                  # downloaded again into the inbox
        out = executor.undo(p["job"])
        home = self.dest / "Real Artist" / "2001 - Real Album"
        if (home / "01.F.flac").exists():
            self.assertEqual(sorted(x.name for x in home.iterdir()),
                             ["01.F.flac", "02.S.flac", "cover.jpg", "rip.cue", "rip.log"],
                             f"the library album lost its companions: {out}")


# ---------------------------------------------------------------------------------------
# R13-10. An interrupted undo cannot be finished by running it again, as undo promises
# ("each step is journalled as it is done, so an undo that was interrupted ... can simply
# be run again", executor.undo). Killed (Ctrl+C, power loss) after a track's old tags were
# put back (the os.replace in _restore / _undo_relink) and before its undo_step is
# journalled, the next undo's verdict (executor._undo, executor.py:1585-1589) compares the
# track's tags with the tags the JOB wrote (tags_sig) - they differ, because the undo
# already restored them - and calls it "edited after the job; left as they are (tagmender
# undo --force ...)". Nothing compares them with the backup.
#   - in place: that track is a PROBLEM on every later undo; the job is never "undone"
#     (exit code 4 for ever) unless the user, wrongly told the file was edited, uses --force;
#   - a hard-linked album (relink_move, relink_copy - the *arr layout): the track counts as
#     changed, so the whole album "stays" (R11-04) - the other tracks keep the job's tags, and
#     with relink_move the album is left split between the inbox and the library.
# Found by tests/fuzz/fuzz_crash.py's own kill points with a stricter check (an undo where
# nothing was edited must end without PROBLEM lines) and a scenario where every track is
# already hard-linked at its destination.
class R13_10_InterruptedUndoCannotBeFinished(Base):
    def test_in_place(self):
        p = pl.build([self.proposal()], self.profile())
        self.assertEqual(executor.Executor(p).run()["counts"], {"done": 1})
        crash_at_first_file_undo_step(self, p["job"])
        out = executor.undo(p["job"])
        self.assertFalse([o for o in out if o.startswith("PROBLEM")], out)
        self.assertEqual([tagio.read(f)["album"] for f in (self.f1, self.f2)], [["Some Album"]] * 2)

    def test_hard_linked_album(self):
        prof = self.profile(operation="move", destination=str(self.dest))
        for fe in pl.build([self.proposal()], prof)["albums"][0]["files"]:
            d = Path(fe["dst"])                                # the *arr import: hard links already there
            d.parent.mkdir(parents=True, exist_ok=True)
            os.link(fe["src"], d)
        p = pl.build([self.proposal()], prof)
        self.assertTrue(all(fe.get("linked") for fe in p["albums"][0]["files"]))
        self.assertEqual(executor.Executor(p).run()["counts"], {"done": 1})
        crash_at_first_file_undo_step(self, p["job"])
        out = executor.undo(p["job"])
        self.assertFalse([o for o in out if o.startswith("PROBLEM")], out)
        self.assertEqual(tree(self.lib), ["Some Artist/2001 - Some Album/01 a.flac",
                                          "Some Artist/2001 - Some Album/02 b.flac",
                                          "Some Artist/2001 - Some Album/rip.cue",
                                          "Some Artist/2001 - Some Album/rip.log"], out)
        self.assertEqual(tagio.read(self.dest / "Real Artist" / "2001 - Real Album" / "01.F.flac")["album"],
                         ["Some Album"])


# ---------------------------------------------------------------------------------------
# R13-11. A loose track in a watched folder (a single dropped into the inbox root, or next
# to an artist's album folders) is judged by EVERYTHING below that folder. Its group's
# folder is the root, and Watcher.due -> snapshot(folder) (watcher.py:50-66, 117-121) walks
# the whole tree with rglob: the signature, the newest modification time and "a partial
# download inside" are those of the entire inbox. So
#   - while any download is in progress anywhere in the inbox (".part", ".!qB" ...), the
#     loose track is "partial download inside" and never processed - an inbox that is
#     always downloading never gets to it;
#   - every album that arrives (or leaves) changes the loose track's signature: an
#     unresolved single is identified again (MusicBrainz, AcoustID, web lookups) on every
#     tick after anything changed, instead of after retry.after_days.
class R13_11_LooseTrackJudgedByTheWholeInbox(Base):
    def setUp(self):
        super().setUp()
        self.single = audio(self.lib / "01 single.flac", 660, title="single", artist="Some Artist")
        old = time.time() - 3600
        for f in [self.single, *self.album.iterdir()]:
            os.utime(f, (old, old))
        self.prof = self.profile(sources=[{"path": str(self.lib), "watch": True}], unattended=True,
                                 unresolved={"action": "leave"},
                                 watch={"settle_minutes": 1, "interval_seconds": 1, "workers": 1})

    def test_partial_download_elsewhere(self):
        dl = self.lib / "Other Artist" / "New Album"
        dl.mkdir(parents=True)
        (dl / "01 x.flac.part").write_bytes(b"partial")      # a download in progress elsewhere
        FakeEngine.test = self
        w = watcher.Watcher(profiles=[self.prof], once=True)
        with mock.patch.object(watcher.eng_mod, "Engine", FakeEngine):
            done = w.tick()
        self.assertIn(str(self.lib), done, f"the loose track waits for the whole inbox: {done}")

    def test_identified_again_when_anything_changes(self):
        UnresolvedEngine.calls = []
        w = watcher.Watcher(profiles=[self.prof], once=True)
        with mock.patch.object(watcher.eng_mod, "Engine", UnresolvedEngine):
            w.tick()
            self.assertEqual(UnresolvedEngine.calls.count(str(self.lib)), 1)
            new = audio(self.lib / "Third Artist" / "Album" / "01 y.flac", 770, title="y", album="Y")
            os.utime(new, (time.time() - 3600,) * 2)            # another album arrived (settled)
            w.tick()
        self.assertEqual(UnresolvedEngine.calls.count(str(self.lib)), 1,
                         f"the unresolved loose track was identified again: {UnresolvedEngine.calls}")


# ---------------------------------------------------------------------------------------
# R13-12. `tagmender jobs prune` never removes the artwork fetched for a plan that the
# watcher saved and that was applied (or planned again since). prune keeps staging/<job>
# for every plan file in ~/.local/state/tagmender/plans (executor.py:1697-1706, "plans still
# waiting to be applied keep theirs"), but nothing ever removes a plan file from there -
# not `tagmender apply`, not a re-plan of the same album - so every plan a plan-only
# watcher ever made counts as waiting for ever. Each holds the album's front cover (up to
# artwork.max_bytes, 20 MB by default): a library processed by a plan-only watcher keeps
# a copy of every cover in the state folder (on Unraid the RAM-backed root filesystem,
# unless moved) - what R10-08 set out to stop.
class R13_12_PruneKeepsArtworkOfAppliedPlans(Base):
    def test_x(self):
        job = "20260101-000000-abcd"
        staging = ex.STATE_DIR / "staging" / job
        staging.mkdir(parents=True)
        front = staging / "0123456789abcdef.jpg"
        front.write_bytes(front_jpg(self.tmp).read_bytes())
        p = pl.build([self.proposal(artwork={"front": str(front), "source": "coverup"})], self.profile(),
                     job_id=f"{job}-1")
        (ex.STATE_DIR / "plans").mkdir(parents=True)
        pl.save(p, ex.STATE_DIR / "plans" / f"{job}-1.json")    # as the watcher saves it
        import tagmender
        with mock.patch("sys.stdout"):
            code = tagmender.main(["apply", "--yes", str(ex.STATE_DIR / "plans" / f"{job}-1.json")])
        self.assertEqual(code, 0)                                # `tagmender apply --yes` of it
        self.assertEqual(tagio.read(self.f1)["album"], ["Real Album"])
        old = time.time() - 90 * 86400
        for x in (front, staging):
            os.utime(x, (old, old))
        out = executor.prune(30)
        self.assertFalse(staging.exists(), f"artwork of an applied plan kept: {out}")


# ---------------------------------------------------------------------------------------
# R13-13. On a case-insensitive share (SMB to a Windows/macOS/NAS server, an exFAT stick) a
# per-track companion whose new name differs from its old one only in letter case is never
# renamed: "01.f.flac" becomes "01.F.flac", its lyrics stay "01.f.lrc". plan.Planner.album
# (plan.py:700-703) looks at the companion's new name, finds it "exists" - it is the very
# same file under the other spelling - and skips it as "copied there by an earlier run"
# (_same_bytes compares the file with itself). Executor.companion_step has a branch for
# exactly this rename ("01 a.lrc" -> "01 A.lrc" on a share, executor.py:906, R4-09), which
# the planner never lets run. Lyrics, per-track cue sheets and logs keep the old case next
# to renamed tracks: a player or server reading the share's disk directly (Navidrome on the
# NAS, any case-sensitive client) no longer pairs them with their track.
HERE = Path(__file__).resolve().parent


def ci_share(test):
    """A case-insensitive, case-preserving FUSE share (tests/test_fileops_review11.py serves it)."""
    if not (os.path.exists("/dev/fuse") and shutil.which("fusermount3")):
        test.skipTest("needs FUSE")
    lib = os.environ.get("TM_FUSE_PYLIB", str(HERE.parent / "review11" / "pylib"))
    if not (Path(lib) / "mfusepy.py").exists() and not (REPO / "tests" / "pylib" / "mfusepy.py").exists():
        test.skipTest("needs mfusepy (TM_FUSE_PYLIB)")
    back, mnt = test.tmp / "back", test.tmp / "share"
    back.mkdir()
    mnt.mkdir()
    proc = subprocess.Popen([sys.executable, str(REPO / "tests" / "test_fileops_review11.py"), "--ci-fuse",
                             str(back), str(mnt)], env=dict(os.environ, TM_FUSE_PYLIB=lib),
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def stop():
        subprocess.run(["fusermount3", "-u", str(mnt)], capture_output=True)
        proc.wait(timeout=10)
    test.addCleanup(stop)
    for _ in range(100):
        if os.path.ismount(mnt):
            break
        time.sleep(0.05)
    test.assertTrue(os.path.ismount(mnt))
    return mnt


class R13_13_CaseOnlyCompanionRenameSkippedOnAShare(Base):
    def test_x(self):
        lib = ci_share(self) / "music"
        album = lib / "Real Artist" / "2001 - Real Album"
        f1 = audio(album / "01.f.flac", 440, album="Real Album", title="First", artist="Real Artist")
        f2 = audio(album / "02.s.flac", 550, album="Real Album", title="Second", artist="Real Artist")
        (album / "01.f.lrc").write_text("[00:00.00]la la\n")
        prof = self.profile(sources=[{"path": str(lib)}], operation="move", destination=str(lib))
        p = pl.build([self.proposal(source_dir=str(album), files=[str(f1), str(f2)])], prof)
        self.assertEqual(executor.Executor(p).run()["counts"], {"done": 1})
        self.assertEqual(sorted(os.listdir(album)), ["01.F.flac", "01.F.lrc", "02.S.flac"],
                         [(c["src"], c["dst"]) for c in p["albums"][0]["companions"]] or p["albums"][0]["notes"])


# ---------------------------------------------------------------------------------------
# R13-14. The R12-04 share check in recover (executor._recover, executor.py:1315-1321) only
# looks at the src/dst of track and companion steps. An album whose only work is a new
# cover.jpg or .lrc file (an in-place re-run with artwork on, over albums already tagged:
# every track "none") has no such step, so recover goes ahead with the NAS still down after
# the power cut: it cannot see the half-made file (executor.py:1351-1361 find nothing),
# journals the album "put back as it was before the job" and the job "recovered". When the
# share is back, the album holds what the job left - its cover.jpg, untracked so undo never
# removes it, or (cut before the rename) a hidden ".tm-<job>-<hash>.jpg.part" copy that
# nothing removes any more (the job is finished; undo has no step for it; a later move of
# the album leaves its folder behind, cleanup_dirs seeing the hidden file).
class R13_14_RecoverIgnoresCoverOnlyAlbumsOnADownShare(Base):
    def run_case(self, cut_before_rename):
        media = self.tmp / "media"
        mp = media / "nas"
        mp.mkdir(parents=True)
        on = {"v": True}
        root = mp / "music"
        shutil.move(str(self.lib), str(root))
        album = root / "Some Artist" / "2001 - Some Album"
        f1, f2 = album / "01 a.flac", album / "02 b.flac"
        t1, t2 = target(1, "First"), target(2, "Second")
        for f, t in ((f1, t1), (f2, t2)):
            tagio.write(f, dict(t, tagmender=["Resolved"]))       # tagged already: only the cover is new
        real = executor.Journal.write

        def jw(j, **ev):
            if cut_before_rename and ev.get("ev") == "create_begin":
                real(j, **ev)
                executor._stage_name(Path(ev["path"]), j.folder.name).write_bytes(b"\xff\xd8 half a cover")
                raise Crash()
            if not cut_before_rename and ev.get("ev") == "cover_file":
                raise Crash()                                     # placed, not yet journalled
            return real(j, **ev)
        with mock.patch.object(pl, "MOUNT_PARENTS", (str(media),)), \
                mock.patch.object(pl.os.path, "ismount", lambda p: on["v"] and str(p) == str(mp)):
            p = pl.build([self.proposal(source_dir=str(album), files=[str(f1), str(f2)], targets=[t1, t2],
                                        artwork={"front": str(front_jpg(self.tmp)), "source": "test"})],
                         self.profile(sources=[{"path": str(root)}], artwork=ART_FILE))
            self.assertEqual([fe["op"] for fe in p["albums"][0]["files"]], ["none", "none"])
            with mock.patch.object(executor.Journal, "write", jw), self.assertRaises(Crash):
                executor.Executor(p).run()
            os.rename(mp, self.tmp / "unplugged")              # after the reboot the NAS is not up yet
            mp.mkdir()
            on["v"] = False
            out = []
            try:
                out.append(executor.recover(p["job"]))
            except executor.StepError as exc:
                out.append(f"refused: {exc}")
            shutil.rmtree(mp)                                  # the share mounts again
            os.rename(self.tmp / "unplugged", mp)
            on["v"] = True
            if executor.pending_jobs():
                out.append(executor.recover(p["job"]))
            out.append(executor.undo(p["job"]))
        return album, out

    def test_cover_placed(self):
        album, out = self.run_case(cut_before_rename=False)
        self.assertEqual(sorted(os.listdir(album)), ["01 a.flac", "02 b.flac", "rip.cue", "rip.log"],
                         f"recovered and undone, yet: {out}")

    def test_cut_before_the_rename(self):
        album, out = self.run_case(cut_before_rename=True)
        self.assertEqual(sorted(os.listdir(album)), ["01 a.flac", "02 b.flac", "rip.cue", "rip.log"],
                         f"recovered and undone, yet: {out}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
