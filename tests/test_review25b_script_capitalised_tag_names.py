# Twenty-fifth independent review (B). Fails with TM_REPO=prev too (not a regression).
# Decided: a script's $set(Title,X) sets the title (the fields TagMender writes are not
# case-sensitive; docs/PICARD_COVERAGE.md) - and the file is named from that same title.
"""A tagger script that spells a tag name with capitals - $set(GENRE,Jazz), $set(Title,X),
$set(ALBUM,Y), as foobar2000 users write field names - overwrites the standard tag in
TagMender. Picard 3.0.2's script names are case-sensitive: "GENRE" is not "genre", so it
writes a separate user tag and leaves the standard one as it was:

  MP3, $set(Title,X):   Picard TIT2 "Track 1" + TXXX:Title "X"   TagMender TIT2 "X"
  MP3, $set(GENRE,Jazz) on a file with TCON "Rock":
                        Picard TCON "Rock" + TXXX:GENRE "Jazz"  TagMender TCON "Jazz"
  FLAC, $set(TITLE,X):  Picard TITLE = "Track 1" and "X"       TagMender TITLE = "X"

TagMender is not even consistent with itself: it names the file from %title% = "Track 1"
(as Picard does, the naming script being case-sensitive since the last round) but writes
"X" as the title. The coverage table says "names are case-sensitive as in Picard".

Cause: plan.Planner._scripted (plan.py:661-699) hands the script's "Title" on as it is,
but tagio.stored / tagio._norm key every field with tagio.field_name (tagio.py:549-556,
k.lower()), so "Title" and "title" become one field and the script's value replaces the
release's; the spelling kept by the last round (tagio.spellings) covers only a file's own
TXXX names, never names a script sets."""
import sys
import unittest
from pathlib import Path

import mutagen
from mutagen.id3 import ID3, TCON

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _review25b_common as c  # noqa: E402


class CapitalisedNamesInScripts(unittest.TestCase):
    def test_mp3_title(self):
        files = c.album(".mp3", 1)
        _p, res = c.run(files, ["$set(Title,X)"])
        self.assertEqual(res["counts"], {"done": 1}, c.detail(res))
        t = ID3(str(files[0]))
        self.assertEqual(str(t["TIT2"]), "X")
        self.assertNotIn("TXXX:Title", t)

    def test_mp3_genre(self):
        files = c.album(".mp3", 1)
        t = ID3()
        t.add(TCON(encoding=3, text=["Rock"]))
        t.save(str(files[0]))
        _p, res = c.run(files, ["$set(GENRE,Jazz)"])
        self.assertEqual(res["counts"], {"done": 1}, c.detail(res))
        t = ID3(str(files[0]))
        self.assertEqual(list(t["TCON"].text), ["Jazz"])
        self.assertNotIn("TXXX:GENRE", t)

    def test_flac_title(self):
        files = c.album(".flac", 1)
        _p, res = c.run(files, ["$set(TITLE,X)"])
        self.assertEqual(res["counts"], {"done": 1}, c.detail(res))
        f = mutagen.File(str(files[0]))
        self.assertEqual(f.tags["TITLE"], ["X"])


if __name__ == "__main__":
    unittest.main()
