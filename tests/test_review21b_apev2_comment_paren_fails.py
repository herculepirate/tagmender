# Twenty-first independent review (B): this test failed before its fix.
# Twenty-first independent review (B). test_plain_comment_* : a regression (pass with
# TM_REPO=prev). test_described_set_keeps_plain: fails with prev too.
"""APEv2 (WavPack, Monkey's Audio, Musepack ...): a plain comment whose text ends in "(...)"
without a space before it ("Recorded live(2001)") breaks every comment write.

tagio.read now splits an APEv2 Comment as Picard's apev2.py does (_split_ape_comment: at the
last " (" - so "Recorded live(2001)" is a PLAIN comment), but the writer still classifies the
values with the Vorbis rule: tagio._set_performers (tagio.py, `keep = [v for v in old if
_role(_split_performer(v, base)[0]) ...]`) sees "Recorded live(2001)" as the description
"2001", so:

- $delete(comment) / $set(comment,new): the value is kept as "not plain", reads back as the
  plain comment, and the executor fails the album ("tag comment did not read back as
  written; album put back as it was") - on every run. Picard 3.0.2 replaces / removes it.
- $set(comment:2001,x): the user's plain comment "Recorded live(2001)" is dropped silently
  (the plan shows only "comment:2001" added). Picard 3.0.2 keeps it beside "x (2001)"."""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _review21b_common as c  # noqa: E402

import mutagen  # noqa: E402

OWN = ["plain", "Recorded live(2001)"]


class ApeCommentParen(unittest.TestCase):
    def wv(self):
        d = Path(tempfile.mkdtemp())
        f = c.make(d / "lib" / "al" / "a.wv")
        m = mutagen.File(f)
        if m.tags is None:
            m.add_tags()
        m["Comment"] = OWN
        m.save()
        # (two plain comments, as Picard and tagio.read read them now)
        return f

    def test_plain_comment_delete(self):
        f = self.wv()
        _p, res, p2 = c.run([f], ["$delete(comment)"])
        self.assertEqual(res["counts"], {"done": 1}, res["albums"][0].get("detail"))
        self.assertEqual(p2["albums"][0]["status"], "unchanged")

    def test_plain_comment_set(self):
        f = self.wv()
        _p, res, p2 = c.run([f], ["$set(comment,new)"])
        self.assertEqual(res["counts"], {"done": 1}, res["albums"][0].get("detail"))
        self.assertEqual(p2["albums"][0]["status"], "unchanged")

    def test_described_set_keeps_plain(self):
        f = self.wv()
        _p, res, _p2 = c.run([f], ["$set(comment:2001,x)"])
        self.assertEqual(res["counts"], {"done": 1})
        self.assertIn("Recorded live(2001)", list(mutagen.File(f)["Comment"]))


if __name__ == "__main__":
    unittest.main()
