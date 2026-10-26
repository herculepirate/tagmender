# Twenty-third independent review (A): an undo cut short (power cut, Ctrl+C, the app killed)
# right after it put back a file the job rewrote where it lies - the album's small cover.jpg
# replaced by the larger cover (artwork.replace "larger_only", the default), or the cue sheet
# whose file names a rename updated - and before the journal recorded that step. Run again,
# undo takes the file it put back itself for an edit made by the user since the job: it reports
# a PROBLEM ("... was changed after the job"), keeps the album where the job put it, and marks
# the job "undone with problems" - every later undo says the same; only --force gets it back.
# For the renamed album, the tracks stay renamed while the cue sheet has the old names again.
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _review23a_common import Case, changed, executor, jpeg, snapshot  # noqa: E402


class Killed(BaseException):
    pass


def cut_short_after(kind):
    """Journal.write that dies just before the first undo step of this kind is recorded."""
    real = executor.Journal.write
    fired = []

    def write(self, **ev):
        if ev.get("ev") == "undo_step" and (ev.get("key") or [None])[0] == kind and not fired:
            fired.append(ev)
            raise Killed()
        return real(self, **ev)
    return mock.patch.object(executor.Journal, "write", write)


class UndoCutShort(Case):
    def undo_twice(self, job, kind):
        with cut_short_after(kind):
            with self.assertRaises(Killed):
                executor.undo(job)
        return executor.undo(job)

    def test_in_place_small_cover_replaced(self):
        album = self.lib / "Artist A" / "2001 - First"
        self.album(album, "First", "Artist A")
        jpeg(album / "cover.jpg", (60, 60, 60), (300, 300))           # too small: replaced
        before = snapshot(self.root)
        p = self.plan(self.profile())
        self.assertTrue(any(c["op"] == "rewrite" for c in p["albums"][0]["companions"]), p["albums"][0])
        self.run_plan(p)
        out = self.undo_twice(p["job"], "companion")
        self.assertEqual([x for x in out if x.startswith("PROBLEM")], [])
        self.assertEqual(changed(before, snapshot(self.root)), [])

    def test_album_renamed_in_the_library_cue_rewritten(self):
        album = self.lib / "Artist A" / "First"
        files = self.album(album, "First", "Artist A")
        (album / "First.cue").write_text("".join(f'FILE "{f.name}" WAVE\n  TRACK 0{i + 1} AUDIO\n'
                                                 f'    INDEX 01 00:00:00\n' for i, f in enumerate(files)))
        prof = self.profile("move", destination=str(self.lib))          # a library organised into itself
        filed = Path(self.plan(prof)["albums"][0]["files"][0]["dst"]).parent
        os.rename(album, filed)                                         # (already in its folder)
        before = snapshot(self.root)
        p = self.plan(prof)
        a = p["albums"][0]
        self.assertEqual([c["op"] for c in a["companions"]], ["rewrite"])
        self.assertTrue(all(fe["op"] == "move" for fe in a["files"]))
        self.run_plan(p)
        out = self.undo_twice(p["job"], "companion")
        self.assertEqual([x for x in out if x.startswith("PROBLEM")], [])
        self.assertEqual(changed(before, snapshot(self.root)), [])


if __name__ == "__main__":
    unittest.main()
