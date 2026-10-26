# Twenty-third independent review (B). Fails with TM_REPO=prev too (not a regression).
"""WavPack (APEv2): the common tagger script $set(year,$left(%date%,4)) makes every album fail.

In APEv2 both "date" (TagMender's APE map, as Picard: "Year") and a custom "year" (k.title()
-> "Year") land on the same item. tagio._set_apev2 deletes "Year" for each field in turn, so
the second write replaces the first; the date reads back as "1973", not "1973-03-24", and the
executor stops: "track01.wv: tag date did not read back as written; album put back as it was".

Picard 3.0.2 end to end (same release, same script, APEv2File._save): one Year item with both
values, ['1973-03-24', '1973']; the album is tagged. (MP3, FLAC, M4A ... are fine: "year" is
a tag of its own there.)"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _review23b_common as c  # noqa: E402

import mutagen  # noqa: E402


class ApeYearScript(unittest.TestCase):
    def test_year_script_on_wavpack(self):
        files = c.album(".wv")
        _p, res = c.run(files, ["$set(year,$left(%date%,4))"])
        self.assertEqual(res["counts"], {"done": 1}, c.detail(res))
        self.assertIn("1973-03-24", list(mutagen.File(files[0])["Year"]))


if __name__ == "__main__":
    unittest.main()
