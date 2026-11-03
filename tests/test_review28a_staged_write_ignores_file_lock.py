# Twenty-eighth independent review (A): enrich.py and coverup_batch.py write a track while a
# TagMender job is changing it - and now bring a track a job just moved away back to its old
# place (the album is then in the library twice; the job's undo cannot put its files back).
# executor.file_lock is "No two TagMender processes change the same file at once": every job
# step takes it. executor.staged_write - which this round made the writer of enrich, the
# CoverUp batch and their undo - takes no lock. Run `tagmender enrich ... --apply` (or the
# CoverUp batch) while the background watcher files an album, or a `tagmender run`:
#  * the job holds the track's lock, yet enrich writes it (test 1);
#  * a move job that moves the track while enrich has it staged: staged_write's final
#    os.replace(stage, path) re-creates the track at its old path - a full copy of the track
#    left in the inbox/source folder beside the moved album (test 2). The previous version's
#    in-place write failed instead (the file was gone).
# Test 1: not a regression. Test 2: regression (passes with TM_REPO=prev).
# Fix: take executor.file_lock(path) in staged_write around copy-edit-replace, and before
# os.replace check the path is still the file that was copied (same inode/size/mtime as `st`;
# else StepError "changed meanwhile; left as it is").
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _review28a_common as c  # noqa: E402
import _review25a_common as c25  # noqa: E402
import enrich as en  # noqa: E402
import mutagen  # noqa: E402


class StagedWriteLock(c25.Case):
    def test_enrich_waits_for_the_job_that_holds_the_track(self):
        track = self.album(self.lib / "Band" / "2001 - Album", "Album", "Band", n=1)[0]
        before = c.sha(track)
        with c25.executor.file_lock(track):             # (a job is writing or moving this track)
            try:
                en.put(track, {"GENRE": "Rock"})
            except Exception:
                pass                                     # (refusing is right)
            self.assertEqual(c.sha(track), before, "enrich wrote a track a running job holds")

    def test_track_moved_by_a_job_meanwhile_is_not_brought_back(self):
        files = self.album(self.lib / "Band" / "2001 - Album", "Album", "Band", n=2)
        p = self.plan(self.profile("move"))
        state = {"done": False}

        def job_now():
            if not state["done"]:
                state["done"] = True
                c25.executor.Executor(p).run()            # the watcher's job, while enrich writes
        if hasattr(en, "_put"):
            orig = en._put
            en._put = lambda stage, values: (job_now(), orig(stage, values))[1]
            self.addCleanup(setattr, en, "_put", orig)
        else:                                            # (the previous version: in place)
            orig = mutagen.File
            mutagen.File = lambda *a, **k: (job_now(), orig(*a, **k))[1]
            self.addCleanup(setattr, mutagen, "File", orig)
        try:
            en.put(files[0], {"GENRE": "Rock"})
        except Exception:
            pass
        self.assertTrue(state["done"])
        # (the job is refused while enrich holds the track - or moves it, and enrich's write is
        # then refused): every track is in one place, never at both
        allf = [f for r, _d, fs in os.walk(self.root) for f in fs if f.endswith(".flac")]
        self.assertEqual(len(allf), len(files), f"a track is in two places: {sorted(allf)}")


if __name__ == "__main__":
    unittest.main()
