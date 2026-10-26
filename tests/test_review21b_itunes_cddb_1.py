# Twenty-first independent review (B): this test failed before its fix.
# Twenty-first independent review (B). test_delete_keeps_frame: a regression (passes with
# TM_REPO=prev). test_set_as_picard: fails with prev too.
"""itunes_cddb_1 in an MP3 is not written or deleted as Picard 3.0.2 does.

tagio._set_id3 (the COMM branch, `is_comment(k) or k == "itunes_cddb_1"`) treats itunes_cddb_1
as the comment "iTunes_CDDB_1":

- $set(itunes_cddb_1,...): the `desc.lower()[:4] == "itun"` rule appends a NUL to every value
  (COMM text ["y", ""]). That rule is Picard's _save_comment_tag, for a "comment:iTun..." name;
  Picard routes itunes_cddb_1 to _save_itunes_cddb_1_tag (id3.py), which writes
  COMM(LATIN1, "eng", "iTunes_CDDB_1", text=values) - no NUL.
- $delete(itunes_cddb_1): TagMender deletes the COMM frame. Picard's _remove_single_tag has no
  case for itunes_cddb_1; it falls to _remove_custom_tag (TXXX:itunes_cddb_1 only), so the
  file's COMM:iTunes_CDDB_1 stays. TagMender removes data Picard keeps."""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _review21b_common as c  # noqa: E402

from mutagen.id3 import COMM, ID3  # noqa: E402

CDDB = "9D0B5C0B+212162+11+150+20345"


class ItunesCddb(unittest.TestCase):
    def mp3(self):
        d = Path(tempfile.mkdtemp())
        f = c.make(d / "lib" / "al" / "a.mp3")
        i = ID3()
        i.add(COMM(encoding=0, lang="eng", desc="iTunes_CDDB_1", text=[CDDB]))
        i.save(str(f))
        return f

    def test_delete_keeps_frame(self):
        f = self.mp3()
        _p, res, _p2 = c.run([f], ["$delete(itunes_cddb_1)"])
        self.assertEqual(res["counts"], {"done": 1})
        self.assertEqual([x.text for x in ID3(str(f)).getall("COMM:iTunes_CDDB_1")], [[CDDB]])

    def test_set_as_picard(self):
        f = self.mp3()
        _p, res, _p2 = c.run([f], ["$set(itunes_cddb_1,y)"])
        self.assertEqual(res["counts"], {"done": 1})
        frames = ID3(str(f)).getall("COMM:iTunes_CDDB_1")
        self.assertEqual([(x.lang, int(x.encoding), x.text) for x in frames], [("eng", 0, ["y"])])


if __name__ == "__main__":
    unittest.main()
