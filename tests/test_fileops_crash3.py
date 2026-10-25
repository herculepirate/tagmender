# Multi-album jobs, the watcher during a running job, journal damage, linked companions,
# re-runs after an interruption, failures of the share: cases from the fourth independent
# review of the file operations, each of which failed before its fix.

import os as _os, tempfile as _tempfile
_os.environ["XDG_CONFIG_HOME"] = _tempfile.mkdtemp(prefix="tm-config-")
_os.environ["XDG_STATE_HOME"] = _tempfile.mkdtemp(prefix="tm-state-")
import sys
from pathlib import Path
REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "tests"))
import executor  # noqa
import plan as pl  # noqa
import tagio, payload  # noqa
from test_executor import Base, audio, target, SHM, CROSS_FS  # noqa
from test_fileops_crash import die, in_child, parts  # noqa


def hidden(*roots):
    return sorted(str(p) for r in roots for p in Path(r).rglob("*") if ".tm-" in p.name)


def listing(root):
    return sorted(str(p.relative_to(root)) for p in Path(root).rglob("*"))

import unittest
import os
import shutil
from unittest import mock



# ---- test_r4_01_disc_folder_flattened.py
# R4-01. An album whose tracks sit in a disc folder ("CD1") and whose naming template
# puts them straight into the album folder: plan._companions' overlaps() drops every
# subfolder lying below a folder the album moves into, so the disc folder is never
# walked. Its lyrics, cue sheet and log stay behind in CD1 (the .lrc no longer next to
# its track, the cue naming files that are gone). With any other album folder name
# the same companions do move along.
import unittest
import engine as eng_mod


class Review4_01(Base):
    def make(self, album):
        cd = album / "CD1"
        a = audio(cd / "01 a.flac", 500, album="Real Album")
        b = audio(cd / "02 b.flac", 600, album="Real Album")
        (cd / "01 a.lrc").write_text("[00:00.00]la")
        (cd / "rip.cue").write_text('FILE "01 a.flac" WAVE\nFILE "02 b.flac" WAVE\n')
        (cd / "rip.log").write_text("EAC")
        return a, b

    def test_disc_folder_companions_follow_tracks_into_the_album_folder(self):
        album = self.lib / "Real Artist" / "2001 - Real Album"     # already the template's folder name
        a, b = self.make(album)
        # the engine makes the album folder (not CD1) the album's source_dir
        g = eng_mod.Engine.albums_in(type("E", (), {"profile": self.profile()})(), self.lib)
        self.assertIn(album, [x.folder for x in g])
        p = pl.build([self.proposal(source_dir=str(album), files=[str(a), str(b)])],
                     self.profile(operation="move", destination=str(self.lib)))
        res = executor.Executor(p).run()
        self.assertEqual(res["counts"], {"done": 1}, res)
        self.assertEqual(sorted(x.name for x in album.iterdir() if x.is_file()),
                         ["01.F.flac", "01.F.lrc", "02.S.flac", "rip.cue", "rip.log"], listing(album))
        self.assertFalse((album / "CD1").exists(), listing(album))

    def test_same_album_under_another_folder_name_takes_them(self):      # for comparison: passes
        album = self.lib / "Real Artist" / "Real Album (rip)"
        a, b = self.make(album)
        p = pl.build([self.proposal(source_dir=str(album), files=[str(a), str(b)])],
                     self.profile(operation="move", destination=str(self.lib)))
        executor.Executor(p).run()
        final = self.lib / "Real Artist" / "2001 - Real Album"
        self.assertTrue((final / "01.F.lrc").exists(), listing(self.lib))




# ---- test_r4_02_album_folder_given_on_command_line.py
# R4-02. `tagmender run|plan PATH` builds the plan with extra_roots=[PATH] (engine.plan),
# and plan.album() puts _companions in shallow mode for an album whose folder is one of
# the plan's roots. Run on an album folder itself, its subfolders (Scans, artwork, the
# per-disc folders' cue sheets and logs) are never looked at: the music moves, they stay
# behind in the old album folder (which cleanup never removes, being a root). Run on
# the artist folder, the same album takes them along.
import unittest


class Review4_02(Base):
    def setUp(self):
        super().setUp()
        (self.album / "Scans").mkdir()
        (self.album / "Scans" / "back.jpg").write_bytes(b"back")

    def final(self):
        return self.dest / "Real Artist" / "2001 - Real Album"

    def test_run_on_the_album_folder_takes_its_scans(self):
        prof = self.profile(operation="move", destination=str(self.dest))
        p = pl.build([self.proposal()], prof, extra_roots=[self.album])       # as engine.plan(self.album)
        executor.Executor(p).run()
        self.assertTrue((self.final() / "Scans" / "back.jpg").exists(),
                        f"left behind: {listing(self.album)}")

    def test_run_on_the_artist_folder_takes_them(self):                # for comparison: passes
        prof = self.profile(operation="move", destination=str(self.dest))
        p = pl.build([self.proposal()], prof, extra_roots=[self.album.parent])
        executor.Executor(p).run()
        self.assertTrue((self.final() / "Scans" / "back.jpg").exists())




# ---- test_r4_03_new_job_over_interrupted_job.py
# R4-03. Nothing stops a new job while an interrupted one is still pending: Executor.run
# (and `tagmender run/apply`, which never look at pending_jobs()) plans and moves the
# same album again. Killed after the first track's new file was committed (source not
# yet removed), a re-run sees that file at the destination, plans the source to
# "01.F ~xxxx.flac" next to it and moves it there: the album now holds the track twice.
# recover then marks the first job's move finished, and undo of both jobs (either
# order) cannot remove the duplicate ("cannot move back: ... exists").
import os
import unittest
from unittest import mock


class Review4_03(Base):
    def test_rerun_before_recover_leaves_no_duplicate(self):
        prof = self.profile(operation="move", destination=str(self.dest))
        p1 = pl.build([self.proposal()], prof)
        real, f1 = os.unlink, str(self.f1)

        def child():
            def unl(p, *a, **k):
                if str(p) == f1:
                    die()                       # new file committed, source not yet removed
                return real(p, *a, **k)
            with mock.patch.object(executor.os, "unlink", unl):
                executor.Executor(p1).run()
        self.assertEqual(in_child(child), 9)
        p2 = pl.build([self.proposal()], prof)          # the user simply runs it again
        try:
            executor.Executor(p2).run()
            ran = True
        except executor.StepError:
            ran = False                                  # refusing is a correct answer too
        self.assertFalse(ran, "a new job ran on top of an interrupted one")
        executor.recover(p1["job"])
        flacs = sorted(x.name for x in self.dest.rglob("*.flac"))
        self.assertEqual(flacs, [], flacs)                          # the interrupted album was put back
        self.assertTrue(self.f1.exists() and self.f2.exists())
        if ran:
            out = executor.undo(p2["job"]) + executor.undo(p1["job"])
            self.assertFalse([o for o in out if o.startswith("PROBLEM")], out)
            self.assertEqual(list(self.dest.rglob("*.flac")), [])




# ---- test_r4_04_watcher_during_running_job.py
# R4-04. Watcher.tick() tries to recover every pending job; one that is running in another
# process (a CLI `apply`, holding its job_lock) raises StepError, which is only logged -
# and the watcher then plans and runs the very album that job is half-way through.
# file_lock only covers one file for the length of one step, so nothing stops it: the
# watcher moves the tracks the CLI job has not reached yet (and the cue and log, the
# cue rewritten for those tracks only), the CLI job then fails on them and puts its
# first track back. The album ends split across two folders, its cue sheet naming a
# file that is not next to it.
import os
import time
import unittest
from unittest import mock
import engine as eng_mod
import watcher


class Review4_04(Base):
    def test_watcher_leaves_an_album_alone_while_another_job_works_on_it(self):
        old = time.time() - 3600
        for f in self.album.iterdir():
            os.utime(f, (old, old))                      # settled long ago
        test = self
        real_albums_in = eng_mod.Engine.albums_in

        class FakeEngine:                                # identification without the network
            def __init__(self, prof, log=None, **kw):
                self.profile, self.job = prof, "w" + os.urandom(3).hex()

            def albums_in(self, root):
                return real_albums_in(self, root)

            def identify(self, g):
                ts = [target(1, "First") if "01" in f.name else target(2, "Second") for f in g.files]
                return test.proposal(source_dir=str(g.folder), files=[str(f) for f in g.files], targets=ts)
        prof = self.profile(operation="move", destination=str(self.dest),
                            sources=[{"path": str(self.lib), "watch": True}], unattended=True,
                            watch={"settle_minutes": 1, "interval_seconds": 1, "workers": 1})
        cli = pl.build([self.proposal()], prof)          # the user's `tagmender apply`
        w = watcher.Watcher(profiles=[prof], once=True)
        real_step = executor.Executor.file_step

        def step(ex_, i, j, fe, a, done):
            real_step(ex_, i, j, fe, a, done)
            if ex_.job == cli["job"] and j == 0:         # the watcher's interval comes round now
                with mock.patch.object(watcher.eng_mod, "Engine", FakeEngine):
                    w.tick()
        with mock.patch.object(executor.Executor, "file_step", step):
            executor.Executor(cli).run()
        tracks = sorted(str(x.relative_to(self.tmp)) for x in self.tmp.rglob("*.flac")
                        if "state" not in x.parts)
        folders = {str(Path(t).parent) for t in tracks}
        self.assertEqual(len(folders), 1, f"album split: {tracks}")




# ---- test_r4_05_recover_adopts_a_file_that_appeared.py
# R4-05. A destination that another program filled after planning makes _place fail;
# the failure handlers (file_step's `except`, _place_staged) delete the staging copy,
# then album() journals album_failed and rolls back. Killed in between (power loss,
# SIGKILL, Ctrl-C), the journal says "staged", the staging copy is gone and the
# destination exists - exactly what recover step 3 takes for "committed". It then
#   - for a companion: deletes its source without comparing anything (the rip log is
#     gone), and undo moves the other program's file into the album as "rip.log";
#   - for a track whose stranger has the same audio (another download of the release):
#     deletes the source, and undo moves the stranger into the old folder.
import shutil
import unittest
from unittest import mock


class Review4_05(Base):
    def kill_after_failed_place(self, p, name):
        real = getattr(executor, name)

        def fn(stage, dst):
            try:
                real(stage, dst)
            except BaseException:
                stage.unlink(missing_ok=True)            # as the handlers do
                die()                                    # before album_failed is journalled

        def child():
            with mock.patch.object(executor, name, fn):
                executor.Executor(p).run()
        self.assertEqual(in_child(child), 9)

    def test_companion_source_survives_recover_and_undo(self):
        p = pl.build([self.proposal()], self.profile(operation="move", destination=str(self.dest)))
        log_dst = Path(next(c["dst"] for c in p["albums"][0]["companions"] if c["src"].endswith("rip.log")))
        log_dst.parent.mkdir(parents=True)
        log_dst.write_text("someone else's log")
        self.kill_after_failed_place(p, "_place")
        executor.recover(p["job"])
        self.assertTrue((self.album / "rip.log").exists(), "recover deleted the album's log")
        executor.undo(p["job"])
        self.assertEqual((self.album / "rip.log").read_text(), "EAC log")
        self.assertEqual(log_dst.read_text(), "someone else's log")

    def test_track_source_survives_recover_and_undo(self):
        p = pl.build([self.proposal()], self.profile(operation="move", destination=str(self.dest)))
        d2 = Path(p["albums"][0]["files"][1]["dst"])
        d2.parent.mkdir(parents=True)
        shutil.copyfile(self.f2, d2)                     # the same recording, from another download
        tagio.write(d2, {"comment": ["the other download"]})
        before = tagio.read(self.f2)
        self.kill_after_failed_place(p, "_place")
        executor.recover(p["job"])
        self.assertTrue(self.f2.exists(), "recover deleted the album's own track")
        executor.undo(p["job"])
        self.assertEqual(tagio.read(self.f2), before)
        self.assertTrue(d2.exists() and tagio.read(d2).get("comment") == ["the other download"],
                        "undo took the other download away from where it was put")




# ---- test_r4_06_torn_journal_line.py
# R4-06. Journal.read() skips "a line cut short by a crash", but Journal.write() appends
# right after it, so the next event - the first one recover writes - lands on the same
# line and is skipped too. Cut while journalling an in-place track's "committed": recover
# re-journals it ("undo can put it back"), that line is unreadable, and undo, finding
# the track still "staged", restores nothing and reports the job undone with no problems.
import json
import os
import time
import unittest
from unittest import mock


class Review4_06(Base):
    def test_event_after_a_torn_line_is_kept(self):
        before = tagio.read(self.f1)
        p = pl.build([self.proposal()], self.profile())
        real = executor.Journal.write

        def jw(j, **ev):
            if ev.get("ev") == "file" and ev.get("state") == "committed" and ev.get("idx") == 0:
                ev["t"] = time.time()
                line = json.dumps(ev) + "\n"
                with open(j.path, "a") as fh:             # power lost half-way through the line
                    fh.write(line[:len(line) // 2])
                    fh.flush()
                    os.fsync(fh.fileno())
                die()
            return real(j, **ev)

        def child():
            with mock.patch.object(executor.Journal, "write", jw):
                executor.Executor(p).run()
        self.assertEqual(in_child(child), 9)
        executor.recover(p["job"])
        out = executor.undo(p["job"])
        self.assertEqual(tagio.read(self.f1), before, out)




# ---- test_r4_07_companion_already_linked.py
# R4-07. A companion whose destination already is a hard link to it (an *arr import that
# links the log next to the tracks, which the planner handles for tracks as "linked"):
# _unique() accepts it (same file), but companion_step has no "linked" case, so
# _place finds the name taken ("appeared meanwhile") and the whole album fails and is
# rolled back - on every run, since the next plan is the same.
import os
import unittest


class Review4_07(Base):
    def test_album_with_a_linked_log_is_applied(self):
        prof = self.profile(operation="move", destination=str(self.dest))
        p0 = pl.build([self.proposal()], prof)
        for fe in p0["albums"][0]["files"] + p0["albums"][0]["companions"]:
            d = Path(fe["dst"])
            d.parent.mkdir(parents=True, exist_ok=True)
            if not fe["src"].endswith(".cue"):
                os.link(fe["src"], d)
        p = pl.build([self.proposal()], prof)
        self.assertEqual(p["albums"][0]["status"], "ready")
        res = executor.Executor(p).run()
        self.assertEqual(res["counts"], {"done": 1}, res["albums"][0]["detail"])




# ---- test_r4_08_replan_after_recover.py
# R4-08. recover deliberately finishes an interrupted album only as far as it got (the
# first track moved, the rest still in the old folder), so the next run - the watcher
# does it on its next tick, right after recovering - plans what is left as an album of
# its own. _cue_rewrites only knows the files of that plan, so the cue sheet that now
# joins the moved tracks still names the first track by its old name: a cue sheet in the
# new album folder pointing at "01 a.flac", which is not there (it is "01.F.flac").
import unittest
from unittest import mock


class Review4_08(Base):
    def test_cue_sheet_names_every_track_after_the_album_is_completed(self):
        prof = self.profile(operation="move", destination=str(self.dest))
        p1 = pl.build([self.proposal()], prof)
        real = executor.Executor.file_step

        def child():
            def step(ex_, i, j, fe, a, done):
                if j == 1:
                    die()                                  # power lost after the first track
                return real(ex_, i, j, fe, a, done)
            with mock.patch.object(executor.Executor, "file_step", step):
                executor.Executor(p1).run()
        self.assertEqual(in_child(child), 9)
        executor.recover(p1["job"])
        # what is left of the album is identified and moved on the next run
        p2 = pl.build([self.proposal()], prof)                        # recover put the album back: run it again
        res = executor.Executor(p2).run()
        self.assertEqual(res["counts"], {"done": 1}, res)
        final = self.dest / "Real Artist" / "2001 - Real Album"
        cue = (final / "rip.cue").read_text()
        for line in cue.splitlines():
            if line.startswith("FILE"):
                name = line.split('"')[1]
                self.assertTrue((final / name).exists(), f"{name} named in the cue is not there:\n{cue}"
                                                         f"\n{listing(final)}")




# ---- test_r4_09_case_only_companion_stage.py
# R4-09. A cue sheet or playlist named after its track whose new name differs only in
# letter case ("01.f.cue" -> "01.F.cue" on an SMB share): companion_step's case-only
# branch writes the rewritten text to _stage_name(src) - the staging name hashed from
# the OLD name - while recover (and _clear_undo_leftovers) look only for
# _stage_name(dst) and the undo-* names. Killed before the os.replace, the hidden
# ".tm-<job>-<hash>.part" stays in the album folder through recover and undo.
# (The share's case-insensitivity is simulated by patching _case_only during the run.)
import shutil
import unittest
from unittest import mock


class Review4_09(Base):
    def test_no_hidden_file_after_recover_and_undo(self):
        final = self.lib / "Real Artist" / "2001 - Real Album"
        final.mkdir(parents=True)
        t1 = dict(target(1, "First"), tagmender=["Resolved"])
        t2 = dict(target(2, "Second"), tagmender=["Resolved"])
        a, b = final / "01.f.flac", final / "02.S.flac"
        shutil.move(str(self.f1), a)
        shutil.move(str(self.f2), b)
        tagio.write(a, t1)
        tagio.write(b, t2)
        (final / "01.f.cue").write_text('FILE "01.f.flac" WAVE\n  TRACK 01 AUDIO\n')
        p = pl.build([self.proposal(files=[str(a), str(b)], source_dir=str(final), targets=[t1, t2])],
                     self.profile(operation="move", destination=str(self.lib)))
        comp = p["albums"][0]["companions"]
        self.assertEqual([(Path(c["dst"]).name, bool(c.get("rewrite"))) for c in comp], [("01.F.cue", True)], comp)
        stage = executor._stage_name(final / "01.f.cue", p["job"])
        real_replace = executor.os.replace

        def replace(s, d, *x, **k):
            if Path(s) == stage:
                die()
            return real_replace(s, d, *x, **k)

        def case_only(x, y):                       # what an SMB share answers for these names
            return str(x) != str(y) and str(x).casefold() == str(y).casefold()

        def child():
            with mock.patch.object(executor, "_case_only", case_only), \
                    mock.patch.object(executor.os, "replace", replace):
                executor.Executor(p).run()
        self.assertEqual(in_child(child), 9)
        executor.recover(p["job"])
        self.assertEqual(hidden(self.lib), [], "after recover")
        executor.undo(p["job"])
        self.assertEqual(hidden(self.lib), [], "after undo")




# ---- test_r4_10_stage_left_when_share_drops.py
# R4-10. The share drops for a moment while a track is committed: the link of the
# staging copy to its new name fails (EIO), and so does the clean-up unlink of the
# staging copy in file_step's `except`. The album is rolled back and the job ends
# normally ("partial"), so recover says there is nothing to recover; undo's
# _clear_undo_leftovers only looks for undo-* staging names. A hidden full copy of the
# track (.tm-<job>-<hash>.part) stays in the library folder for good. (Found by an
# error-injection variant of the crash fuzzers: review4/fuzz_raise.py.)
import errno
import unittest
from unittest import mock


class Review4_10(Base):
    def test_no_hidden_copy_after_the_share_comes_back(self):
        p = pl.build([self.proposal()], self.profile(operation="move", destination=str(self.dest)))
        real_link, real_unlink = executor.os.link, executor.os.unlink
        down = {"on": False}

        def link(a, b, *x, **k):
            if ".tm-" in Path(a).name:
                down["on"] = True                         # the share goes away here...
            if down["on"]:
                raise OSError(errno.EIO, "Input/output error")
            return real_link(a, b, *x, **k)

        def unlink(a, *x, **k):
            if down["on"]:
                down["on"] = False                        # ...and is back right after
                raise OSError(errno.EIO, "Input/output error")
            return real_unlink(a, *x, **k)
        with mock.patch.object(executor.os, "link", link), mock.patch.object(executor.os, "unlink", unlink):
            res = executor.Executor(p).run()
        self.assertEqual(res["counts"], {"failed": 1})
        executor.recover(p["job"])
        executor.undo(p["job"])
        self.assertEqual(hidden(self.lib, self.dest), [])



if __name__ == "__main__":
    unittest.main()
