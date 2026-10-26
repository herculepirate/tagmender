# Sixteenth independent review (A): this test failed before its fix.
# Sixteenth independent review (A). (Not a regression: the previous version does the same.)
# An MP3 with no tag at all, tagged by a job and then undone, keeps an empty ID3v2 tag: its
# backup record says format "" (a mutagen MP3 with no tags is falsy - see
# test_a16_untagged_mp3_trailer_not_undone.py), and undo restores on a staging copy whose name
# ends in ".part", so restore_tags takes its generic branch: f.add_tags(), clear(), save().
# Undo is not exact: the file has an ID3v2 tag (and is larger) where it had none.
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _review16a_common import Run, bare_mp3, executor  # noqa: E402


class UntaggedMp3GainsId3OnUndo(Run):
    def test_undo_leaves_no_id3_tag(self):
        self.fs = [bare_mp3(self.alb / f"0{n} x.mp3", 440 + 100 * n) for n in (1, 2)]
        orig = [f.read_bytes() for f in self.fs]
        p, res, before, prof = self.run_plan({})
        out = executor.undo(res["job"])
        self.assertNotEqual(self.fs[0].read_bytes()[:3], b"ID3", f"an ID3v2 tag is left after undo: {out}")
        self.assertEqual([f.read_bytes() for f in self.fs], orig)


if __name__ == "__main__":
    unittest.main()
