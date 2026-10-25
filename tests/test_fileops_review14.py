# Fourteenth independent review of TagMender's file operations (code at 58d7769). Each test
# asserts the correct behaviour and fails at 58d7769.
#   cd /home/claude/tagmender && TAGMENDER_DISCOGS_PLUGIN=/home/claude/herculepirate/picard-discogs-enrichment \
#     TAGMENDER_COVERUP_DIR=/home/claude/herculepirate/picard-coverup python3 -W ignore <this file>
# The repository is found at $TM_REPO (default /home/claude/tagmender). R14_03 needs root
# (bind mounts); it is skipped otherwise.
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
import engine as eng_mod  # noqa: E402
import exclusions as ex  # noqa: E402
import executor  # noqa: E402
import plan as pl  # noqa: E402
import tagio  # noqa: E402
import watcher  # noqa: E402
from test_executor import Base, audio, target  # noqa: E402
from test_fileops_review7 import Lossy, tree  # noqa: E402
from test_fileops_review11 import FakeEngine  # noqa: E402
from test_fileops_review13 import ART_FILE, front_jpg  # noqa: E402

ALBUM = ["Some Artist/2001 - Some Album/01 a.flac", "Some Artist/2001 - Some Album/02 b.flac",
         "Some Artist/2001 - Some Album/rip.cue", "Some Artist/2001 - Some Album/rip.log"]


def undone(job) -> bool:
    return any(e.get("ev") == "undone" for e in executor.Journal(executor.job_dir(job)).read())


# ---------------------------------------------------------------------------------------
# R14-01. DATA LOSS (tag edits): undo deletes a copy whose tags were edited after the job
# when the editor kept the file's modification time. The verdict for a copy
# (executor._undo, executor.py:1628-1633) decides "changed after the job" from size and
# mtime only; the tags fingerprint (tags_sig) it compares for in-place, move and hard-link
# steps (executor.py:1636-1641) is never looked at for op "copy". Picard's "Preserve
# timestamps of tagged files" (which TagMender itself follows by default), Mp3tag's and
# foobar2000's "preserve file modification time" all keep mtime, and a tag edit inside the
# padding keeps the size: the user fixes titles/genres in the lossy library made by the
# "Lossy" copy profile, later undoes that copy job (say, to redo it with another profile) -
# the edited copies are deleted with the others, no PROBLEM line, nothing kept. The same
# edit to a moved or in-place track is detected and left alone.
class R14_01_UndoDeletesACopyEditedWithItsTimeKept(Lossy):
    def test_x(self):
        p = pl.build([self.proposal()], self.lossy())
        self.assertEqual(executor.Executor(p).run()["counts"], {"done": 1})
        copy = self.dest / "Real Artist" / "2001 - Real Album" / "01.F.mp3"
        st = os.stat(copy)
        tagio.write(copy, {"comment": ["my own note"], "genre": ["Trip Hop"]})   # edited in Picard ...
        os.utime(copy, ns=(st.st_atime_ns, st.st_mtime_ns))                       # ... timestamps kept
        self.assertEqual(os.stat(copy).st_size, st.st_size)
        out = executor.undo(p["job"])
        self.assertTrue(copy.exists(), f"the edited copy was deleted by undo: {out}")
        self.assertEqual(tagio.read(copy)["genre"], ["Trip Hop"])


# ---------------------------------------------------------------------------------------
# R14-02. The R13-09 fix ("undo after the album was downloaded again into the inbox stripped
# the library album of its cover, log and cue") is switched off by ANY earlier undo of the
# job. executor._undo (executor.py:1608, 1624-1627) only treats "the source holds a file
# again" as a hard reason when `not prior_undo` - and prior_undo is true as soon as the job
# has an undo_begin, i.e. after any earlier undo run, whatever it did. The common sequence:
# `tagmender undo JOB` reports one track "edited after the job" and the album stays, with
# the advice "tagmender undo --force JOB puts what it can back anyway"; meanwhile the
# torrent client re-checks the seeding folder the move emptied and downloads it again;
# `tagmender undo --force JOB` then deletes the library album's cover.jpg, rip.log and
# (rewritten) rip.cue because identical files sit in the inbox (executor._undo_step,
# executor.py:1142-1146), and every track fails "cannot move back: ... exists"
# (executor.py:1187-1192): the library album is left with its music only - exactly
# R13-09. (An undo cut short is told apart by the files themselves: a source that is a
# copy of the destination; a fresh download holds the old tags.)
class R14_02_ReDownloadCheckOffAfterAnEarlierUndo(Base):
    def test_x(self):
        (self.album / "cover.jpg").write_bytes(b"front")
        download = self.tmp / "download"
        shutil.copytree(self.album, download)
        p = pl.build([self.proposal()], self.profile(operation="move", destination=str(self.dest)))
        self.assertEqual(executor.Executor(p).run()["counts"], {"done": 1})
        home = self.dest / "Real Artist" / "2001 - Real Album"
        tagio.write(home / "01.F.flac", {"comment": ["edited in Picard"]})
        first = executor.undo(p["job"])                        # the album stays (one track edited)
        self.assertTrue(any("--force" in x for x in first), first)
        shutil.copytree(download, self.album)                  # downloaded again into the inbox
        out = executor.undo(p["job"], force=True)
        if (home / "01.F.flac").exists():
            self.assertEqual(sorted(x.name for x in home.iterdir()),
                             ["01.F.flac", "02.S.flac", "cover.jpg", "rip.cue", "rip.log"],
                             f"the library album lost its companions: {out}")


# ---------------------------------------------------------------------------------------
# R14-03. Undo (and a plain move) fail for good where rename() and link() answer EXDEV
# although both folders report the same device number: two bind mounts of one filesystem
# (Docker volumes -v /mnt/user/downloads:/downloads -v /mnt/user/music:/music, an fstab bind)
# and mergerfs with its default path-preserving create policy (epmfs), the usual *arr pool.
# executor._same_fs (executor.py:1095-1099) compares st_dev and then executor._place_move ->
# _link_then_unlink (executor.py:324-343) catches EXDEV from os.link only to call
# os.rename, which raises EXDEV too; nothing falls back to the copy path that a move to
# another filesystem uses. Apply: an album whose files need no new tags (a re-organisation)
# fails every time. Undo: the job's moves were made by copy (they wrote tags), but undo of
# every moved track and companion goes the rename way (executor.py:1150-1152, 1193-1195) -
# each is a PROBLEM on every undo run, and since a rewritten cue goes back by copy, the
# album is left split: rip.cue in the inbox, the music and log in the library.
@unittest.skipUnless(os.geteuid() == 0, "needs root for bind mounts")
class R14_03_MovesAcrossBindMountsOfOneFilesystem(Base):
    def setUp(self):
        super().setUp()
        host = self.tmp / "host"
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
        self.assertEqual(os.stat(self.inbox).st_dev, os.stat(self.music).st_dev)

    def test_undo(self):
        p = pl.build([self.proposal(source_dir=str(self.album), files=[str(self.f1), str(self.f2)])],
                     self.profile(operation="move", destination=str(self.music)))
        self.assertEqual(executor.Executor(p).run()["counts"], {"done": 1})
        out = executor.undo(p["job"])
        self.assertEqual((tree(self.lib), tree(self.music)), (ALBUM, []), out)

    def test_move_without_new_tags(self):
        for f, t in ((self.f1, target(1, "First")), (self.f2, target(2, "Second"))):
            tagio.write(f, dict(t, tagmender=["Resolved"]))      # tagged already: only the move is new
        p = pl.build([self.proposal(source_dir=str(self.album), files=[str(self.f1), str(self.f2)])],
                     self.profile(operation="move", destination=str(self.music)))
        res = executor.Executor(p).run()
        self.assertEqual(res["counts"], {"done": 1}, res["albums"])


# ---------------------------------------------------------------------------------------
# R14-04. A track the user deleted after a move job blocks the undo of the rest of its
# album for ever, --force included. Since 58d7769 executor._undo (executor.py:1620-1623)
# takes a moved track found neither where the job put it nor where it was for "moved by a
# later job? undo that one first" and makes it a HARD reason, which --force never
# overrides (executor.py:1662-1668). No later job exists - the user removed a bonus track
# or a duplicate from the library - so there is nothing to undo first: the remaining
# tracks, cover, log and cue never go back, every undo prints the same advice, and
# `undo --force`, which promises "what can go back does", does nothing. (Before 58d7769 the
# rest of the album went back.) Telling a later job's move from a deletion is possible: the
# later job's journal names the path as its source.
class R14_04_DeletedTrackBlocksUndoEvenWithForce(Base):
    def test_x(self):
        p = pl.build([self.proposal()], self.profile(operation="move", destination=str(self.dest)))
        self.assertEqual(executor.Executor(p).run()["counts"], {"done": 1})
        (self.dest / "Real Artist" / "2001 - Real Album" / "02.S.flac").unlink()   # deleted by the user
        executor.undo(p["job"])
        out = executor.undo(p["job"], force=True)
        self.assertEqual(tree(self.lib), [ALBUM[0], ALBUM[2], ALBUM[3]], out)


# ---------------------------------------------------------------------------------------
# R14-05. The watcher re-does a job as soon as it has been undone. After a job it stores the
# album's signature "so TagMender's own writes never queue it again" (watcher.py docstring,
# Watcher.record), but an undo is also TagMender's own write and is never recorded: the
# restored files' new size/mtime make Watcher.due (watcher.py:132-133) answer "changed",
# and once settle_minutes have passed the album is identified again and the same plan is
# applied again - same release, same moves. The usual reason to undo a job of an
# unattended Inbox/Library profile is a wrong match; `tagmender undo JOB` (or Undo in the
# desktop app) is reverted by the background service a few minutes later, every time.
class R14_05_WatcherRedoesAnUndoneJob(Base):
    def test_x(self):
        prof = self.profile(sources=[{"path": str(self.lib), "watch": True}], unattended=True,
                            watch={"settle_minutes": 0, "interval_seconds": 1, "workers": 1})
        FakeEngine.test = self
        w = watcher.Watcher(profiles=[prof], once=True)
        with mock.patch.object(watcher.eng_mod, "Engine", FakeEngine):
            self.assertEqual(list(w.tick().values()), ["done"])
            job = executor.jobs(1)[0][0]
            time.sleep(1.1)                                    # (the undo comes later than the job)
            executor.undo(job)
            self.assertEqual(tagio.read(self.f1)["album"], ["Some Album"])
            again = w.tick()
        self.assertEqual(tagio.read(self.f1)["album"], ["Some Album"], f"undone job applied again: {again}")


# ---------------------------------------------------------------------------------------
# R14-06. The watcher (and `tagmender retry`) plan with less than the profile asks for:
# Watcher.tick (watcher.py:203-206) and cmd_retry (tagmender.py:657-658) call
# Engine.identify and planmod.build directly, skipping Engine.add_artwork and
# Engine.apply_level, which Engine.proposals runs for `tagmender plan|run` and the desktop
# app (engine.py:541-543). So every album an unattended watcher (or a plan-only watcher's
# saved plan) processes gets
#   - no cover: nothing embedded, no cover.jpg, whatever "artwork" says;
#   - every field the providers returned, whatever "tag_level" ("basic" writes label,
#     catalogue number, IDs ...) and "level_fields.drop" say (a field the user said never to
#     write is written), and no Audiophile measurements or .lrc lyrics files.
# The same album gives a different result through `tagmender run` and through the inbox
# service the shipped Inbox profile sets up.
class OfflineEngine(eng_mod.Engine):
    """The real engine (artwork, levels, planning) with identification stubbed (no network)."""
    test = None

    def __init__(self, prof, log=None, **kw):
        self.profile, self.job, self.notes, self.log = prof, "w" + os.urandom(3).hex(), {}, log
        self.rules, self._art, self.http, self.token, self.ocr = ex.load_rules(prof.name), None, None, "", False

    def identify(self, g):
        ts = [target(1, "First") if f.name.startswith("01") else target(2, "Second") for f in g.files]
        return OfflineEngine.test.proposal(source_dir=str(g.folder), files=[str(f) for f in g.files], targets=ts)


class R14_06_WatcherSkipsArtworkAndTagLevel(Base):
    def watch(self, **kw):
        prof = self.profile(sources=[{"path": str(self.lib), "watch": True}], unattended=True,
                            watch={"settle_minutes": 0, "interval_seconds": 1, "workers": 1}, **kw)
        OfflineEngine.test = self
        with mock.patch.object(watcher.eng_mod, "Engine", OfflineEngine):
            return watcher.Watcher(profiles=[prof], once=True).tick()

    def test_tag_level(self):
        done = self.watch(tag_level="basic", artwork={"enabled": False})
        self.assertEqual(list(done.values()), ["done"])
        t = tagio.read(self.f1)
        self.assertEqual(t["album"], ["Real Album"])
        self.assertFalse({"label", "catalognumber", "discogs_release_id"} & set(t),
                         f"a Basic profile's watcher wrote {sorted(t)}")

    def test_artwork(self):
        import artwork
        front = front_jpg(self.tmp)
        with mock.patch.object(artwork, "chain", lambda *a, **k: []), \
                mock.patch.object(artwork, "find_front", lambda *a, **k: {"front": str(front), "source": "test"}):
            done = self.watch(artwork=ART_FILE)
        self.assertEqual(list(done.values()), ["done"])
        self.assertTrue((self.album / "cover.jpg").exists(), sorted(os.listdir(self.album)))


# ---------------------------------------------------------------------------------------
# R14-07. An unresolved loose track at the top of a source folder (a single dropped into the
# inbox) is moved into a copy of the inbox's own location below unresolved.move_to:
# "<move_to>/<inbox's parent>/<inbox>/01 x.flac". plan.Planner._below_source
# (plan.py:867-878) looks for the source folder that HOLDS the album folder with
# `real.startswith(r + os.sep)`, which excludes the source folder itself, and falls back to
# "its last two folders". The caller (plan.py:644-645) expects an empty path for this case.
# With the shipped Inbox profile (inbox ~/Music/Inbox, move_to ~/Music/Inbox/_unresolved)
# a single lands in "Inbox/_unresolved/Music/Inbox/"; on Unraid (inbox /mnt/user/inbox) in
# "_unresolved/user/inbox/"; every album folder of the inbox goes to "_unresolved/<album>".
class R14_07_LooseUnresolvedTrackNestedUnderTheInboxPath(Base):
    def test_x(self):
        inbox = self.tmp / "Music" / "Inbox"
        single = audio(inbox / "01 x.flac", 660, title="x")
        hold = inbox / "_unresolved"
        hold.mkdir()
        prof = self.profile(sources=[{"path": str(inbox), "watch": True}], operation="move",
                            destination=str(self.dest), unresolved={"action": "move", "move_to": str(hold)})
        prop = pl.Proposal(source_dir=str(inbox), files=[str(single)], status="unresolved",
                           reason="nothing found", reason_codes=["other"])
        a = pl.build([prop], prof)["albums"][0]
        self.assertEqual([fe["dst"] for fe in a["files"]], [str(hold / "01 x.flac")], a["conflicts"])


# ---------------------------------------------------------------------------------------
# R14-08. Undoing an in-place job before a later job that moved the same album leaves the
# cover.jpg (or .lrc) the in-place job made in the album for good - R13-07 for in-place
# jobs. 58d7769 made "the album was moved by a later job" a hard reason only for moved
# tracks (executor._undo, executor.py:1620-1623); an in-place ("tag") track that is gone
# from where the job tagged it gives no reason to stay, so the album does not "stay" and
# the cover loop (executor.py:1670-1684) journals the cover.jpg as done because it is not
# there - it moved with the album. Undo of the later (move) job brings the album and the
# cover.jpg back, and the next undo of the in-place job restores the tags but skips the
# cover as already removed. A user reverting an in-place pass of the Library profile (with
# artwork on) and the re-organisation after it, oldest first, keeps a cover.jpg the job
# made and the undo said nothing about.
class R14_08_UndoOutOfOrderLeavesInPlaceCover(Base):
    def test_x(self):
        a = pl.build([self.proposal(artwork={"front": str(front_jpg(self.tmp)), "source": "test"})],
                     self.profile(artwork=ART_FILE))
        self.assertEqual(executor.Executor(a).run()["counts"], {"done": 1})
        self.assertTrue((self.album / "cover.jpg").exists())
        b = pl.build([self.proposal()], self.profile(operation="move", destination=str(self.dest)))
        self.assertEqual(executor.Executor(b).run()["counts"], {"done": 1})
        out = [executor.undo(a["job"]), executor.undo(b["job"])]
        if not undone(a["job"]):
            out.append(executor.undo(a["job"]))
        self.assertEqual(tree(self.lib), ALBUM, out)


# ---------------------------------------------------------------------------------------
# R14-09. "Process anyway" for a loose track at a watched root never reaches the watcher.
# The R13-11 fix judges such a track by its own files, but Watcher.due (watcher.py:134-140)
# still decides "process anyway" for an excluded album with ex._overridden(FOLDER, ...),
# and the folder of a loose track is the watched root. `tagmender rules allow FILE` (the
# only override that does not lift the rules for the whole inbox) is honoured by planning
# (exclusions.evaluate tests each file) but the watcher answers "excluded (unchanged)" on
# every pass until the file itself changes. (The desktop app's "Process anyway" calls
# State().allow(source_dir), i.e. the whole watched root for such a track, which lifts
# every rule for everything in the inbox.)
class R14_09_ProcessAnywayForALooseTrackIgnoredByWatcher(Base):
    def test_x(self):
        ex._write(ex.rules_file(), {"rules": [{"id": "ab", "name": "audiobooks", "action": "ignore", "target": "file",
                                               "when": {"all": [{"field": "genre", "op": "contains",
                                                                 "value": "Audiobook"}]}}]})
        single = audio(self.lib / "01 single.flac", 660, title="single", artist="Some Artist", genre="Audiobook")
        prof = self.profile(sources=[{"path": str(self.lib), "watch": True}], unattended=True,
                            watch={"settle_minutes": 0, "interval_seconds": 1, "workers": 1})
        FakeEngine.test = self
        w = watcher.Watcher(profiles=[prof], once=True)
        with mock.patch.object(watcher.eng_mod, "Engine", FakeEngine):
            self.assertEqual(w.tick().get(str(self.lib)), "excluded")
            ex.State().allow(single)                           # tagmender rules allow ".../01 single.flac"
            again = w.tick()
        self.assertEqual(tagio.read(single).get("album"), ["Real Album"], f"not processed: {again}")


# ---------------------------------------------------------------------------------------
# R14-10. A moved companion the user deleted after the job keeps the job from ever being
# undone. executor._undo_step (executor.py:1150-1155) has no case for a companion found
# neither at its destination nor at its source: _put_back copies from the missing file,
# raises FileNotFoundError, and that is a PROBLEM on this and every later undo ("undone
# with problems (run undo again when they are fixed)", exit code 4) - nothing the user can
# fix short of recreating the file. A cover/lyrics file the job made and a copy are taken
# as done when they are gone (executor.py:1678-1684, 1164). Deleting the .log, .txt or a
# duplicate scan from an imported album is common.
class R14_10_DeletedCompanionKeepsUndoUnfinished(Base):
    def test_x(self):
        p = pl.build([self.proposal()], self.profile(operation="move", destination=str(self.dest)))
        self.assertEqual(executor.Executor(p).run()["counts"], {"done": 1})
        (self.dest / "Real Artist" / "2001 - Real Album" / "rip.log").unlink()     # deleted by the user
        out = [executor.undo(p["job"])]
        if not undone(p["job"]):
            out.append(executor.undo(p["job"]))
        self.assertTrue(undone(p["job"]), out)
        self.assertEqual(tree(self.lib), ALBUM[:3])


if __name__ == "__main__":
    unittest.main(verbosity=2)
