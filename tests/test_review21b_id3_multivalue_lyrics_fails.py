# Twenty-first independent review (B): this test failed before its fix.
# Twenty-first independent review (B). Fails with TM_REPO=prev too (not a regression).
"""Several lyrics values for an ID3v2.4 file (a tagger script's $setmulti(lyrics,...), or
"lyrics" and "lyrics:xxx" together, which tagio.stored() merges into one field) fail the album
on every run: "tag lyrics did not read back as written; album put back as it was".

tagio._set_id3 (the USLT branch) writes ONE frame with the values joined by "\n"; read() gives
that back as one value, and the planned ["a", "b"] never reads back. Picard 3.0.2
(_save_lyrics_tag) adds one USLT per value with the same language and description - mutagen
keeps the last - so the file gets USLT "b". (In an ID3v2.3 file stored() joins them with "/"
and TagMender writes "a/b"; Picard still writes "b".)"""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _review21b_common as c  # noqa: E402

from mutagen.id3 import ID3  # noqa: E402


class MultiValueLyrics(unittest.TestCase):
    def test_setmulti_lyrics(self):
        f = c.make(Path(tempfile.mkdtemp()) / "lib" / "al" / "a.mp3")
        _p, res, p2 = c.run([f], ["$setmulti(lyrics,a; b)"])
        self.assertEqual(res["counts"], {"done": 1}, res["albums"][0].get("detail"))
        self.assertEqual(p2["albums"][0]["status"], "unchanged")
        self.assertEqual([u.text for u in ID3(str(f)).getall("USLT")], ["b"])     # (as Picard)


if __name__ == "__main__":
    unittest.main()
