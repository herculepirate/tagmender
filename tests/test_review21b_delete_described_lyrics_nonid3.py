# Twenty-first independent review (B): this test failed before its fix.
# Twenty-first independent review (B). Fails with TM_REPO=prev too (not a regression).
"""$delete(lyrics:<desc>) in a tagger script leaves a FLAC / Ogg / Opus / MP4 / WMA file's
lyrics in place; Picard 3.0.2 removes them.

In those formats a described lyrics field has no place of its own: Picard writes "lyrics:desc"
into the one lyrics field (tagio.stored() now folds it there too), and its removal maps the
same way - VCommentFile._get_tag_name / MP4File._get_tag_name / ASFFile._get_tag_name turn
"lyrics:Verse" into LYRICS / ©lyr / WM/Lyrics, and _remove_deleted_tags deletes it. TagMender's
removal list goes through tagio.canonical(), which renames only ID3 names, so "lyrics:Verse"
matches nothing the file has and the lyrics stay."""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _review21b_common as c  # noqa: E402

import mutagen  # noqa: E402
from mutagen.mp4 import MP4  # noqa: E402


class DeleteDescribedLyrics(unittest.TestCase):
    def test_flac(self):
        f = c.make(Path(tempfile.mkdtemp()) / "lib" / "al" / "a.flac")
        m = mutagen.File(f)
        m["LYRICS"] = ["la la"]
        m.save()
        _p, res, p2 = c.run([f], ["$delete(lyrics:Verse)"])
        self.assertEqual(res["counts"], {"done": 1})
        self.assertNotIn("lyrics", c.tagio.read(f))

    def test_m4a(self):
        f = c.make(Path(tempfile.mkdtemp()) / "lib" / "al" / "a.m4a")
        m = MP4(f)
        if m.tags is None:
            m.add_tags()
        m["\xa9lyr"] = ["la la"]
        m.save()
        _p, res, p2 = c.run([f], ["$delete(lyrics:Verse)"])
        self.assertEqual(res["counts"], {"done": 1})
        self.assertNotIn("lyrics", c.tagio.read(f))


if __name__ == "__main__":
    unittest.main()
