# Twenty-first independent review (B): this test failed before its fix.
# Twenty-first independent review (B). A regression: passes with TM_REPO=prev.
"""An MP3 whose comment or lyrics frame has its language in capitals (COMM lang "ENG", USLT
lang "XXX", as some taggers write them) fails on every run with a tagger script that sets or
deletes "comment" / "lyrics": "tag comment did not read back as written; album put back as it
was".

tagio._comm_name (Picard's create_lang_desc_tag) compares the language case-insensitively, so
COMM("ENG", "") reads as "comment" and USLT("XXX", "") as "lyrics". But tagio._set_id3 now
deletes only the frames whose lang is exactly "eng" / "xxx" (tagio.py, the COMM and USLT
branches: `tags[x].lang == lang`): the "ENG" / "XXX" frame stays, reads back into the same
field, and the executor's read-back check fails. (prev deleted by description alone.)

Picard 3.0.2 itself keeps the capitalised frame beside its own (its read-back of $set(comment,
new) is ["old", "new"]); either way TagMender's plan and its write disagree, so the album never
gets tagged."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _review21b_common as c  # noqa: E402

import tempfile  # noqa: E402
from mutagen.id3 import COMM, ID3, USLT  # noqa: E402


class UppercaseLanguage(unittest.TestCase):
    def check(self, frame, script):
        d = Path(tempfile.mkdtemp())
        f = c.make(d / "lib" / "al" / "a.mp3")
        i = ID3()
        i.add(frame)
        i.save(str(f))
        _p, res, p2 = c.run([f], [script])
        self.assertEqual(res["counts"], {"done": 1}, res["albums"][0].get("detail"))
        self.assertEqual(p2["albums"][0]["status"], "unchanged")

    def test_delete_comment_eng_capitals(self):
        self.check(COMM(encoding=3, lang="ENG", desc="", text=["old"]), "$delete(comment)")

    def test_set_comment_eng_capitals(self):
        self.check(COMM(encoding=3, lang="ENG", desc="", text=["old"]), "$set(comment,new)")

    def test_set_lyrics_xxx_capitals(self):
        self.check(USLT(encoding=3, lang="XXX", desc="", text="old"), "$set(lyrics,new)")


if __name__ == "__main__":
    unittest.main()
