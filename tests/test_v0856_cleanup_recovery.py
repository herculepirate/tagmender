"""DIR-2: a crash during a move's empty-folder cleanup (DIR-1) is recovered like any other
unfinished album: the album is put back as it was before the job - its files, its junk files and
the empty folders the cleanup had removed (undo already made those again; recover did not)."""
import os as _os, tempfile as _tempfile  # noqa: E402
_os.environ.setdefault("XDG_CONFIG_HOME", _tempfile.mkdtemp(prefix="tm-config-"))
_os.environ.setdefault("XDG_STATE_HOME", _tempfile.mkdtemp(prefix="tm-state-"))
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import executor  # noqa: E402
import plan as pl  # noqa: E402
import tagio  # noqa: E402
from test_executor import Base, audio, target  # noqa: E402


class DIR2_CrashDuringCleanupIsRecovered(Base):
    def setUp(self):
        super().setUp()
        self.lib.mkdir(parents=True, exist_ok=True)
        self.tree = self.album / "Disc 2" / "Live albums" / "09 Songs"
        self.tree.mkdir(parents=True)
        (self.album / "Thumbs.db").write_text("junk")
        self.before = sorted(str(x.relative_to(self.album)) for x in self.album.rglob("*"))

    def crash_at(self, n):
        """Run the move, stopping hard at the n-th folder removal (KeyboardInterrupt: the
        process ends there, nothing below it runs)."""
        prof = self.profile(operation="move", destination=str(self.dest), naming={"enabled": True})
        p = pl.build([self.proposal(conf=99.0)], prof)
        real, calls = Path.rmdir, []

        def rmdir(path, *a, **k):
            calls.append(path)
            if len(calls) == n:
                raise KeyboardInterrupt
            return real(path, *a, **k)
        with mock.patch.object(Path, "rmdir", rmdir):
            with self.assertRaises(KeyboardInterrupt):
                executor.Executor(p).run()
        return p

    def assert_put_back(self, p, n):
        done = executor.recover(p["job"])
        self.assertTrue(any("put back as it was" in d for d in done), done)
        after = sorted(str(x.relative_to(self.album)) for x in self.album.rglob("*")) if self.album.exists() else []
        self.assertEqual(after, self.before, f"crash at removal {n}: the album as it was, empty folders too")
        self.assertEqual(tagio.read(self.f1)["album"], ["Some Album"])
        self.assertEqual(executor.pending_jobs(), [])

    def test_crash_in_the_empty_tree(self):
        for n in (1, 2, 3):                 # "09 Songs" gone, "Live albums" too, then "Disc 2"
            with self.subTest(n=n):
                self.setUp()
                self.assert_put_back(self.crash_at(n), n)

    def test_crash_at_the_album_folder(self):
        self.assert_put_back(self.crash_at(4), 4)       # its Thumbs.db saved, the folder itself next

    def test_crash_right_after_a_removal_before_its_journal_line(self):
        # (the folder is journaled before it goes: a crash between the two cannot lose it)
        prof = self.profile(operation="move", destination=str(self.dest), naming={"enabled": True})
        p = pl.build([self.proposal(conf=99.0)], prof)
        real, calls = Path.rmdir, []

        def rmdir(path, *a, **k):
            calls.append(path)
            real(path, *a, **k)
            if len(calls) == 2:
                raise KeyboardInterrupt
        with mock.patch.object(Path, "rmdir", rmdir):
            with self.assertRaises(KeyboardInterrupt):
                executor.Executor(p).run()
        self.assert_put_back(p, "2 (removed, not yet journaled)")

    def test_a_folder_given_a_file_since_is_not_disturbed(self):
        # recovery makes missing folders only: one made again since (with a file) is left as it is
        p = self.crash_at(4)                 # "Disc 2" and its tree removed
        (self.album / "Disc 2").mkdir()
        (self.album / "Disc 2" / "new.txt").write_text("mine")
        executor.recover(p["job"])
        self.assertEqual((self.album / "Disc 2" / "new.txt").read_text(), "mine")
        self.assertTrue(self.tree.is_dir())

    def test_a_finished_album_keeps_its_cleanup_and_undo_after_recover(self):
        # two albums: the first finishes (its empty tree goes), the second crashes in its cleanup
        other = self.lib / "Other Artist" / "1999 - Other"
        o1 = audio(other / "01 x.flac", 660, album="Other", title="x", artist="Other Artist")
        o2 = audio(other / "02 y.flac", 770, album="Other", title="y", artist="Other Artist")
        (other / "Empty" / "Deeper").mkdir(parents=True)
        o_before = sorted(str(x.relative_to(other)) for x in other.rglob("*"))
        prof = self.profile(operation="move", destination=str(self.dest), naming={"enabled": True})
        second = self.proposal(conf=99.0, source_dir=str(other), files=[str(o1), str(o2)],
                               targets=[target(1, "X", album="Other", artist="Other Artist", discogs_release_id=["778"]),
                                        target(2, "Y", album="Other", artist="Other Artist", discogs_release_id=["778"])],
                               release={"album": ["Other"], "albumartist": ["Other Artist"]}, release_id="778")
        p = pl.build([self.proposal(conf=99.0), second], prof)
        first_album = p["albums"][0]["source_dir"]
        real, calls = Path.rmdir, []

        def rmdir(path, *a, **k):
            calls.append(path)
            if str(path).startswith(str(other)) and len([c for c in calls if str(c).startswith(str(other))]) == 2:
                raise KeyboardInterrupt
            return real(path, *a, **k)
        with mock.patch.object(Path, "rmdir", rmdir):
            with self.assertRaises(KeyboardInterrupt):
                executor.Executor(p).run()
        executor.recover(p["job"])
        self.assertEqual(first_album, str(self.album), "the plan's order: the crash is in the second album")
        self.assertFalse(self.album.exists(), "the finished album's cleanup stands")
        self.assertEqual(sorted(str(x.relative_to(other)) for x in other.rglob("*")), o_before)
        executor.undo(p["job"])
        self.assertEqual(sorted(str(x.relative_to(self.album)) for x in self.album.rglob("*")), self.before)
        self.assertEqual(sorted(str(x.relative_to(other)) for x in other.rglob("*")), o_before)

    def test_finished_cleanup_then_undo_still_makes_them_again(self):
        prof = self.profile(operation="move", destination=str(self.dest), naming={"enabled": True})
        p = pl.build([self.proposal(conf=99.0)], prof)
        res = executor.Executor(p).run()
        self.assertEqual(res["counts"], {"done": 1}, res)
        self.assertFalse(self.album.exists())
        executor.undo(p["job"])
        after = sorted(str(x.relative_to(self.album)) for x in self.album.rglob("*"))
        self.assertEqual(after, self.before)


class DIR2_SiblingsAndSharedParents(Base):
    """One disc folder of a box moved out (pilot 1, album 05: Kayak's box, its other discs
    beside it). The cleanup removes that folder only: the box folder, the other discs, their
    files and an empty disc folder of the box stay as they are - after a finished job, after a
    crash in the cleanup and its recovery, and after undo. Cleanup changes no file's contents."""

    def setUp(self):
        super().setUp()
        import shutil
        self.box = self.lib / "Some Artist" / "2017 - The Box"
        self.album_disc = self.box / "2001 Some Album"
        shutil.move(str(self.album), str(self.album_disc))
        self.f1, self.f2 = self.album_disc / "01 a.flac", self.album_disc / "02 b.flac"
        self.album = self.album_disc
        self.sib = self.box / "2003 Next Album"
        self.s1 = audio(self.sib / "01 s.flac", 880, album="Next", title="s", artist="Some Artist")
        (self.sib / "Disc 2").mkdir()                       # the sibling's own empty folder
        (self.box / "Booklet.pdf").write_bytes(b"%PDF-1.4 box booklet")
        (self.box / "2005 Not Ripped Yet").mkdir()          # an empty disc folder of the box
        (self.album / "Thumbs.db").write_text("junk")
        import payload
        self.payload = payload
        self.keep = self.snapshot()

    def snapshot(self):
        """Every path outside the moved disc folder, with each file's bytes' digest."""
        import hashlib
        out = {}
        for x in sorted((self.lib / "Some Artist").rglob("*")):
            if x == self.album_disc or self.album_disc in x.parents:
                continue
            out[str(x.relative_to(self.lib))] = hashlib.sha256(x.read_bytes()).hexdigest() if x.is_file() else "dir"
        return out

    def plan(self):
        prof = self.profile(operation="move", destination=str(self.dest), naming={"enabled": True})
        return pl.build([self.proposal(conf=99.0, source_dir=str(self.album_disc),
                                       files=[str(self.f1), str(self.f2)])], prof)

    def test_a_finished_move_removes_only_its_own_folder(self):
        audio_before = self.payload.digest(self.s1)
        p = self.plan()
        res = executor.Executor(p).run()
        self.assertEqual(res["counts"], {"done": 1}, res)
        self.assertFalse(self.album_disc.exists(), "its own folder, emptied, is removed")
        self.assertEqual(self.snapshot(), self.keep, "box, siblings, their files and empty folders untouched")
        self.assertEqual(self.payload.digest(self.s1), audio_before)
        import json
        evs = [json.loads(x) for x in (executor.job_dir(p["job"]) / "journal.jsonl").read_text().splitlines() if x]
        self.assertEqual([e["path"] for e in evs if e.get("ev") == "dir_removed"], [str(self.album_disc)],
                         "the only folder removed, journaled")

    def test_a_crash_in_its_cleanup_leaves_the_siblings_alone(self):
        for n in (1, 2):                     # at its own folder; past it (nothing more to remove)
            with self.subTest(n=n):
                self.tearDown()
                self.setUp()
                before = sorted(str(x.relative_to(self.album_disc)) for x in self.album_disc.rglob("*"))
                p = self.plan()
                real, calls = Path.rmdir, []

                def rmdir(path, *a, **k):
                    calls.append(path)
                    if len(calls) == n:
                        raise KeyboardInterrupt
                    return real(path, *a, **k)
                with mock.patch.object(Path, "rmdir", rmdir):
                    try:
                        executor.Executor(p).run()
                    except KeyboardInterrupt:
                        pass
                self.assertEqual(calls[:1], [self.album_disc], "the only folder the cleanup removes")
                executor.recover(p["job"])
                self.assertEqual(self.snapshot(), self.keep)
                if n == 1:
                    after = sorted(str(x.relative_to(self.album_disc)) for x in self.album_disc.rglob("*"))
                    self.assertEqual(after, before, "the album put back as it was")

    def test_undo_restores_the_disc_and_leaves_the_siblings(self):
        before = sorted(str(x.relative_to(self.album_disc)) for x in self.album_disc.rglob("*"))
        p = self.plan()
        executor.Executor(p).run()
        (self.sib / "added later.txt").write_text("mine")          # a change since the job
        self.keep[str((self.sib / "added later.txt").relative_to(self.lib))] = \
            __import__("hashlib").sha256(b"mine").hexdigest()
        executor.undo(p["job"])
        self.assertEqual(sorted(str(x.relative_to(self.album_disc)) for x in self.album_disc.rglob("*")), before)
        self.assertEqual(self.snapshot(), self.keep)


if __name__ == "__main__":
    unittest.main()
