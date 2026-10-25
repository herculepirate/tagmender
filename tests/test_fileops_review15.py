# Fifteenth independent review of TagMender's file operations (code at f2eaf39). Each test
# asserts the correct behaviour and fails at f2eaf39.
#   cd /home/claude/tagmender && TAGMENDER_DISCOGS_PLUGIN=/home/claude/herculepirate/picard-discogs-enrichment \
#     TAGMENDER_COVERUP_DIR=/home/claude/herculepirate/picard-coverup python3 -W ignore <this file>
# The repository is found at $TM_REPO (default /home/claude/tagmender). R15_04 needs root
# (bind mounts); it is skipped otherwise.
import os as _os, tempfile as _tempfile
_os.environ["XDG_CONFIG_HOME"] = _tempfile.mkdtemp(prefix="tm-config-")
_os.environ["XDG_STATE_HOME"] = _tempfile.mkdtemp(prefix="tm-state-")
import argparse
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import time
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "tests"))
import engine as eng_mod  # noqa: E402
import exclusions as ex  # noqa: E402
import executor  # noqa: E402
import plan as pl  # noqa: E402
import tagio  # noqa: E402
import tagmender  # noqa: E402
import watcher  # noqa: E402
from test_executor import Base, audio, target  # noqa: E402
from test_fileops_review7 import tree  # noqa: E402
from test_fileops_review11 import FakeEngine  # noqa: E402
from test_fileops_review14 import OfflineEngine  # noqa: E402

ALBUM = ["Some Artist/2001 - Some Album/01 a.flac", "Some Artist/2001 - Some Album/02 b.flac",
         "Some Artist/2001 - Some Album/rip.cue", "Some Artist/2001 - Some Album/rip.log"]
HOUR_AGO = time.time() - 3600


def undone(job) -> bool:
    return any(e.get("ev") == "undone" for e in executor.Journal(executor.job_dir(job)).read())


def job_that_changed(name) -> str:
    """The job whose plan wrote the file called NAME."""
    for d in sorted(executor.jobs_dir().iterdir()):
        plan = json.loads((d / "plan.json").read_text())
        if any(Path(fe["src"]).name == name and fe["op"] != "none" for a in plan["albums"] for fe in a["files"]):
            return d.name
    raise AssertionError(f"no job wrote {name}")


def age(*roots):
    """Every file below ROOTS an hour old: settled for the watcher, older than any undo."""
    for r in roots:
        for p in Path(r).rglob("*"):
            if p.is_file():
                os.utime(p, (HOUR_AGO, HOUR_AGO))


def watched(test, **kw):
    d = dict(sources=[{"path": str(test.lib), "watch": True}], unattended=True,
             watch={"settle_minutes": 0, "interval_seconds": 1, "workers": 1})
    d.update(kw)
    return test.profile(**d)


# ---------------------------------------------------------------------------------------
# R15-01. STUCK: once ANY later job has a step whose source is the path where this job put a
# track, a track deleted since blocks the undo of its whole album for ever, --force
# included - even when that later job has itself been undone, or its album was rolled back.
# executor._moved_later (executor.py:1604-1622) answers "moved by a later job" from the later
# journal's file/companion events alone (e.get("src") == path); it never looks at whether the
# later job was undone (an "undone" event), whether the step was rolled back
# (rollback_step / album_rolled_back), or whether the file is where that job put it.
# executor._undo (executor.py:1678-1682) makes that a HARD reason ("undo that one first -
# its album waits"), which --force never overrides (executor.py:1729-1737). The usual
# sequence: the Inbox profile moves an album into the library (job A); a re-organisation of
# the library (job B) is undone because its new names were wrong; the user then deletes a
# bonus track or a duplicate; `tagmender undo A` and `undo --force A` now always answer
# "02.S.flac was moved by a later job (B); undo that one first" - B is undone already, there
# is nothing left to undo first, and the rest of the album, its cue and log never go back.
# (R14-04 fixed this for a deletion with no later job.)
class R15_01_UndoneLaterJobBlocksUndoForEver(Base):
    def test_x(self):
        a = pl.build([self.proposal()], self.profile(operation="move", destination=str(self.dest)))
        self.assertEqual(executor.Executor(a).run()["counts"], {"done": 1})
        home = self.dest / "Real Artist" / "2001 - Real Album"
        dest2 = self.tmp / "dest2"
        dest2.mkdir()
        b = pl.build([self.proposal(source_dir=str(home), files=[str(home / "01.F.flac"), str(home / "02.S.flac")])],
                     self.profile(sources=[{"path": str(self.dest)}], operation="move", destination=str(dest2)))
        self.assertEqual(executor.Executor(b).run()["counts"], {"done": 1})
        executor.undo(b["job"])
        self.assertTrue(undone(b["job"]))
        self.assertTrue((home / "02.S.flac").exists())
        (home / "02.S.flac").unlink()                          # deleted by the user
        out = [executor.undo(a["job"])]
        out.append(executor.undo(a["job"], force=True))
        self.assertEqual(tree(self.lib), [ALBUM[0], ALBUM[2], ALBUM[3]], out)


# ---------------------------------------------------------------------------------------
# R15-02. Undoing one album's job tells the watcher that OTHER albums were undone: their
# pending retries are lost for ever. executor._watcher_forgets (executor.py:1636-1639) marks
# every `processed` row whose key is the album folder OR starts with "<folder>#". The watcher
# keys every album of a folder that holds several - and every loose track at the top of a
# watched folder (an inbox with singles dropped into it) - as "<folder>#<hash of its own
# files>" (watcher.Watcher.key), so undoing the job of one loose track (or one album of a
# mixed folder) marks ALL of them "undone". Watcher.due then answers "undone" for each until
# its files get a newer modification time (watcher.py:132-133): a single the watcher found
# unresolved (retried after retry.after_days), one that met a provider outage or a share
# that was down (retried within minutes, or "waiting_destination"), is never looked at again.
class R15_02_UndoMarksOtherLooseTracksUndone(Base):
    class Engine(FakeEngine):
        calls = []

        def identify(self, g):
            f = g.files[0]
            type(self).calls.append(f.name)
            if f.name == "known.flac":
                return FakeEngine.test.proposal(source_dir=str(g.folder), files=[str(f)],
                                                targets=[target(1, "First")])
            return pl.Proposal(source_dir=str(g.folder), files=[str(f)], status="unresolved",
                               reason="no candidates found", reason_codes=["no_match"])

    def test_x(self):
        shutil.rmtree(self.lib / "Some Artist")
        audio(self.lib / "known.flac", 440, title="k", album="Known", artist="X")
        audio(self.lib / "other.flac", 550, title="o", album="Other", artist="Y")
        age(self.lib)
        prof = watched(self, unresolved={"action": "leave"}, retry={"after_days": 0, "max_attempts": 5})
        FakeEngine.test = self
        w = watcher.Watcher(profiles=[prof], once=True)
        with mock.patch.object(watcher.eng_mod, "Engine", self.Engine):
            w.tick()
            self.assertEqual(sorted(self.Engine.calls), ["known.flac", "other.flac"])
            executor.undo(job_that_changed("known.flac"))     # the single was matched wrongly
            self.Engine.calls.clear()
            w.tick()                                           # other.flac is due for its retry
        self.assertIn("other.flac", self.Engine.calls,
                      "the unresolved single next to the undone one is never retried")


# ---------------------------------------------------------------------------------------
# R15-03. STUCK: an in-place job one of whose tracks was deleted afterwards can never be
# undone completely, --force included. For op "tag" executor._undo has no "gone" case (the
# R14-04 fix covers moves only, executor.py:1678-1687; R14-10 companions; covers and copies
# were already taken as done when gone): _undo_step -> _restore (executor.py:532) stats the
# missing file, FileNotFoundError is a PROBLEM on this and every later run, the job stays
# "undone with problems (run undo again when they are fixed)" and `tagmender undo` exits 4
# for ever - nothing the user can fix short of recreating the track. Deleting a bonus track,
# a duplicate or a bad rip from a tagged album is common. (The same for the "tag" step of a
# copy profile's re-run when the copy was deleted since.)
class R15_03_DeletedInPlaceTrackKeepsUndoUnfinished(Base):
    def test_x(self):
        p = pl.build([self.proposal()], self.profile())
        self.assertEqual(executor.Executor(p).run()["counts"], {"done": 1})
        self.f2.unlink()                                       # deleted by the user
        out = [executor.undo(p["job"])]
        if not undone(p["job"]):
            out.append(executor.undo(p["job"], force=True))
        self.assertTrue(undone(p["job"]), out)
        self.assertEqual(tagio.read(self.f1)["album"], ["Some Album"])


# ---------------------------------------------------------------------------------------
# R15-04. STUCK: undo (and rollback) of a hard-linked step fails for ever between two bind
# mounts of one filesystem - the R14-03 setup (Docker -v /mnt/user/downloads:/downloads
# -v /mnt/user/music:/music, an fstab bind, mergerfs) - when the library already holds hard
# links of the downloads (made by Lidarr/Sonarr through one /data mount, the TRaSH-guides
# layout, or on the host). The planner sees the links (same device and inode) and plans
# "relink_move": the move only drops the downloads' names. Undo, executor._undo_relink
# (executor.py:1227 os.link(dst, src) and 1242 os.link(src, stage)), can only re-link, and
# link() between the two mounts gives EXDEV: every undo run reports
# "[Errno 18] Invalid cross-device link" for every track. Without new tags the seeding folder
# keeps its cue and log but never gets its tracks back; with new tags the downloads get the
# old tags back while the library copies keep the job's tags for good. R14-03 added the
# EXDEV fallback (a copy) to the rename steps only.
@unittest.skipUnless(os.geteuid() == 0, "needs root for bind mounts")
class R15_04_RelinkUndoAcrossBindMounts(Base):
    def setUp(self):
        super().setUp()
        host = self.host = self.tmp / "host"
        (host / "downloads").mkdir(parents=True)
        (host / "music").mkdir()
        self.inbox, self.music = self.tmp / "downloads", self.tmp / "music"
        self.inbox.mkdir()
        self.music.mkdir()
        for src, mnt in ((host / "downloads", self.inbox), (host / "music", self.music)):
            subprocess.run(["mount", "--bind", str(src), str(mnt)], check=True)
            self.addCleanup(subprocess.run, ["umount", str(mnt)])
        shutil.move(str(self.lib), str(self.inbox / "lib"))
        self.lib = self.inbox / "lib"
        self.album = self.lib / "Some Artist" / "2001 - Some Album"
        self.f1, self.f2 = self.album / "01 a.flac", self.album / "02 b.flac"

    def job(self):
        lib = self.host / "music" / "Real Artist" / "2001 - Real Album"    # hard links made on the host
        lib.mkdir(parents=True)
        os.link(self.host / "downloads" / self.f1.relative_to(self.inbox), lib / "01.F.flac")
        os.link(self.host / "downloads" / self.f2.relative_to(self.inbox), lib / "02.S.flac")
        p = pl.build([self.proposal(source_dir=str(self.album), files=[str(self.f1), str(self.f2)])],
                     self.profile(operation="move", destination=str(self.music)))
        self.assertEqual([fe.get("linked") for fe in p["albums"][0]["files"]], [True, True])
        self.assertEqual(executor.Executor(p).run()["counts"], {"done": 1})
        out = [executor.undo(p["job"])]
        if not undone(p["job"]):
            out.append(executor.undo(p["job"]))
        return p, out

    def test_move_without_new_tags(self):
        for f, t in ((self.f1, target(1, "First")), (self.f2, target(2, "Second"))):
            tagio.write(f, dict(t, tagmender=["Resolved"]))
        p, out = self.job()
        self.assertEqual(tree(self.lib), ALBUM, out)
        self.assertTrue(undone(p["job"]), out)

    def test_move_with_new_tags(self):
        p, out = self.job()
        self.assertTrue(undone(p["job"]), out)
        self.assertEqual(tree(self.lib), ALBUM, out)
        lib = self.music / "Real Artist" / "2001 - Real Album"
        self.assertEqual([tagio.read(lib / n)["album"] for n in ("01.F.flac", "02.S.flac")],
                         [["Some Album"]] * 2, "the library kept the job's tags")


# ---------------------------------------------------------------------------------------
# R15-05. `tagmender retry PATH` plans whole folders again, not the unresolved albums: an
# album is planned twice (or more), and albums that were never unresolved are identified and
# planned again too. cmd_retry (tagmender.py:648-661) collects the FOLDER of every
# unresolved group and then plans everything e.albums_in(folder) finds there. A loose track
# at the top of PATH (a single in the inbox) has PATH itself as its folder, so the whole
# inbox or library is planned - once per unresolved single; an unresolved album in a mixed
# folder brings the folder's other albums; an unresolved album with another album nested
# inside it ("Main/Bonus") brings that one, which is planned a second time when it is
# unresolved too. The plan holds the same files in two albums: applied, the second entry of
# each is refused ("changed since the plan was made" / "gone"), so the run ends with
# conflicts and exit code 4, and resolved albums are re-matched and re-tagged by a command
# meant for the unresolved ones.
class R15_05_RetryPlansFoldersTwice(Base):
    def test_x(self):
        audio(self.lib / "x.flac", 440, title="x", album="X", artist="X", tagmender="Unresolved")
        audio(self.lib / "y.flac", 550, title="y", album="Y", artist="Y", tagmender="Unresolved")
        prof = self.profile(artwork={"enabled": False})        # "Some Album" is not unresolved

        class E(OfflineEngine):
            def identify(s, g):
                return pl.Proposal(source_dir=str(g.folder), files=[str(f) for f in g.files], status="unresolved",
                                   reason="no candidates found", reason_codes=["no_match"])
        out = self.tmp / "retry.json"
        args = argparse.Namespace(path=str(self.lib), code=None, profile_opt=None, profile=None, apply=False,
                                  yes=True, json=False, output=str(out))
        OfflineEngine.test = self
        with mock.patch.object(eng_mod, "Engine", E), mock.patch.object(tagmender, "_profile", lambda a: prof):
            tagmender.cmd_retry(args)
        planned = sorted(Path(fe["src"]).name for a in json.loads(out.read_text())["albums"] for fe in a["files"])
        self.assertEqual(planned, ["x.flac", "y.flac"])


# ---------------------------------------------------------------------------------------
# R15-06. After an undo the watcher misses every change that does not bring a NEWER file.
# Watcher.due (watcher.py:132-133) decides "changed since the undo" only from the newest
# modification time below the folder (newest > the undo's time); the album's signature,
# which tells added, removed and replaced files apart, is not looked at for "undone" (and
# the record still holds the signature of before the undo). A file copied into the folder
# keeps its old modification time when it is moved in with mv, copied with Windows Explorer
# or Finder over SMB, rsync -a, cp -p or unpacked from an archive; a deleted file changes no
# time at all. The user undoes a wrong match of an incomplete album, adds the missing track
# (or removes the stray one) - and the watcher never processes the album again.
class R15_06_WatcherMissesAFileAddedAfterTheUndo(Base):
    def test_x(self):
        age(self.lib)
        FakeEngine.test = self
        w = watcher.Watcher(profiles=[watched(self)], once=True)
        with mock.patch.object(watcher.eng_mod, "Engine", FakeEngine):
            self.assertEqual(list(w.tick().values()), ["done"])
            executor.undo(executor.jobs(1)[0][0])
            self.assertEqual(tagio.read(self.f1)["album"], ["Some Album"])
            age(self.lib)                                       # (restored files: no newer time)
            self.assertEqual(w.tick(), {})                      # the undone job is not redone
            extra = audio(self.album / "03 c.flac", 660, album="Some Album", title="c", artist="Some Artist")
            os.utime(extra, (HOUR_AGO, HOUR_AGO))               # moved in with its own (older) time
            again = w.tick()
        self.assertEqual(list(again.values()), ["done"], "the album with its new track was never looked at")


# ---------------------------------------------------------------------------------------
# R15-07. The R14-05 fix ("undo marks the watcher's record") only works when the watcher has
# a record. executor._watcher_forgets (executor.py:1638-1639) only UPDATEs existing
# `processed` rows; a job that did not come from the watcher - `tagmender run --yes` on a
# watched inbox, or the desktop app's Apply on an album dropped onto it before the watcher's
# settle_minutes (10 by default) had passed - leaves none. Undone, the album is back in the
# inbox with its old modification times, Watcher.due finds no row and answers "new"
# (watcher.py:129-130), and an unattended profile applies the very match the user just
# undid.
class R15_07_WatcherRedoesAManualJobUndone(Base):
    def test_x(self):
        age(self.lib)
        prof = watched(self, operation="move", destination=str(self.dest))
        p = pl.build([self.proposal()], prof)                  # tagmender run / the desktop app
        self.assertEqual(executor.Executor(p).run()["counts"], {"done": 1})
        executor.undo(p["job"])
        self.assertEqual(tree(self.lib), ALBUM)
        FakeEngine.test = self
        with mock.patch.object(watcher.eng_mod, "Engine", FakeEngine):
            done = watcher.Watcher(profiles=[prof], once=True).tick()
        self.assertEqual(tree(self.lib), ALBUM, f"the undone job was done again by the watcher: {done}")


# ---------------------------------------------------------------------------------------
# R15-08. A plan applied after a track was added to the album's folder splits the album:
# the planned tracks leave with the cover, log, cue and scans, the new track stays behind
# alone in the old folder. Executor.album (executor.py:683-697) checks that every PLANNED
# file is still as it was, but not that the folder holds nothing new: plan._companions
# keeps an album's companions where they are when its folder holds music that does not move
# with it ("folder holds other music too", plan.py:363-364), yet at apply time the
# companions planned when the folder held only two tracks go anyway. The desktop app
# previews, the user sees the album is a track short, copies the missing one into the
# folder and clicks Apply; a plan-only watcher's plan waits in plans/ for days while the
# download completes - the library album then lacks a track and the old folder keeps a
# lone track (its cue now naming files in the library).
class R15_08_PlanAppliedAfterATrackWasAdded(Base):
    def test_x(self):
        p = pl.build([self.proposal()], self.profile(operation="move", destination=str(self.dest)))
        audio(self.album / "03 c.flac", 660, album="Some Album", title="c", artist="Some Artist")
        res = executor.Executor(p).run()
        self.assertEqual(tree(self.dest), [], f"the album was split: {res['albums']}; left: {tree(self.lib)}")


# ---------------------------------------------------------------------------------------
# R15-09. The desktop app's "Process anyway" on a loose track lifts every rule for the whole
# folder it lies in - the inbox or library root - for good. gui.Overview.process_anyway
# (gui.py:556-560) calls ex.State().allow(a["source_dir"]), and the source_dir of a loose
# track (a single at the top of the inbox or a library) is that root. From then on
# exclusions.evaluate (exclusions.py:360, 367) skips every rule for every file below it:
# the audiobooks, podcasts, "[no vox]" rips and Various Artists sets the user's rules keep
# out are identified, tagged and moved by the next `tagmender run` and by the watcher, and
# State.allow also forgets every remembered ignore below it (exclusions.py:470-471). The
# R14-09 fix made the watcher honour a per-FILE override; the app still never makes one.
# (Noted in passing in R14-09's comment; not fixed by f2eaf39.)
class R15_09_GuiProcessAnywayLiftsRulesForTheWholeInbox(Base):
    def test_x(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        import gui
        ex._write(ex.rules_file(), {"rules": [{"id": "ab", "name": "audiobooks", "action": "ignore", "target": "file",
                                               "when": {"all": [{"field": "genre", "op": "contains",
                                                                 "value": "Audiobook"}]}}]})
        single = audio(self.lib / "01 single.flac", 660, title="single", artist="Some Artist", genre="Audiobook")
        book = audio(self.lib / "A Book" / "01 chapter.flac", 770, title="ch", album="A Book", genre="Audiobook")
        prop = pl.Proposal(source_dir=str(self.lib), files=[str(single)], status="unresolved", reason="x")
        entry = pl.build([prop], self.profile())["albums"][0]
        self.assertEqual(entry["status"], "excluded")
        ui = mock.Mock()
        ui.selected.return_value = entry
        gui.Overview.process_anyway(ui)                          # the button, on the loose track
        rules = ex.load_rules()
        self.assertFalse(ex.evaluate([single], rules, overrides=ex.State().overrides()).excluded)
        self.assertTrue(ex.evaluate([book], rules, overrides=ex.State().overrides()).excluded,
                        "every rule is lifted for the whole folder the loose track lies in")


# ---------------------------------------------------------------------------------------
# R15-10. The R14-09 fix covers loose tracks at a watched ROOT only: "process anyway" for a
# file of an album that shares its folder with another album (a mixed folder: "Downloads/",
# "misc/", an artist folder holding a few singles) still never reaches the watcher.
# Watcher.tick passes the album's files to Watcher.due only for a group at the root
# (watcher.py:195, scope), and Watcher.due tests the override against the folder and those
# files (watcher.py:141); for any other mixed folder it tests the folder alone, so
# `tagmender rules allow FILE` is answered "excluded (unchanged)" on every pass, while
# `tagmender run` (exclusions.evaluate, per file) honours it.
class R15_10_ProcessAnywayInAMixedFolderIgnoredByWatcher(Base):
    def test_x(self):
        ex._write(ex.rules_file(), {"rules": [{"id": "ab", "name": "audiobooks", "action": "ignore", "target": "file",
                                               "when": {"all": [{"field": "genre", "op": "contains",
                                                                 "value": "Audiobook"}]}}]})
        mixed = self.lib / "Downloads"
        book = audio(mixed / "01 book.flac", 660, title="b", album="Book", artist="A", genre="Audiobook")
        audio(mixed / "01 song.flac", 770, title="s", album="Song", artist="B")
        FakeEngine.test = self
        w = watcher.Watcher(profiles=[watched(self)], once=True)
        with mock.patch.object(watcher.eng_mod, "Engine", FakeEngine):
            w.tick()
            self.assertEqual(tagio.read(book)["album"], ["Book"])          # excluded by the rule
            ex.State().allow(book)                                         # tagmender rules allow FILE
            again = w.tick()
        self.assertEqual(tagio.read(book)["album"], ["Real Album"], f"not processed: {again}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
