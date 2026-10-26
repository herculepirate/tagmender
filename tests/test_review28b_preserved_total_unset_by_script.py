"""A total Picard preserves ("Preserve these tags: totaltracks") is dropped from the number's
frame when a tagger script unsets or empties it: TRCK "1" / trkn (1, 0) / Track "1" where
Picard writes "1/9" / (1, 9) / "1/9".

plan.py:924-929 (Planner.album, the combined-pairs block): for a total in `_unset`
("$unset(totaltracks)" or "$set(totaltracks,)") it appends the total to `removals` -
`elif tot_ in unset_ and tot_ not in raw and tot_ not in removals and before.get(tot_)` -
without asking the field's policy, so a preserved (or locked) total is removed. Picard 3.0.2
(File.copy_metadata) puts the preserved tags back over whatever the scripts did, so the
file's own total 9 is written again ("1/9"). Checked end to end with Picard's code
(Track.update_file_metadata + ID3File/MP4File/APEv2File._save) on MP3, M4A and WavPack;
FLAC/WMA keep it. The user asked Picard to keep the file's totals and loses them.
Fails with TM_REPO=prev too (not a regression).
"""
import os
import unittest

import mutagen

import _review28b_common as c
import picard_config


class PreservedTotalUnset(unittest.TestCase):
    def setUp(self):
        ini = c.write_ini("[application]\nversion=3.0.2.final0\n\n[setting]\npreserved_tags=totaltracks\n")
        os.environ["TAGMENDER_PICARD_INI"] = str(ini)
        picard_config._cache.clear()

    def tearDown(self):
        os.environ["TAGMENDER_PICARD_INI"] = "/nonexistent/tm28B/Picard.ini"
        picard_config._cache.clear()

    def _run(self, ext, script):
        files = c.album(ext, 2)
        for f in files:
            c.pretag_numbers(f)                       # 5/9, disc 2/3
        p, res = c.run(files, scripts=[script])
        self.assertEqual(res.get("counts"), {"done": 1}, c.detail(p, res))
        return files

    def test_mp3_unset(self):
        files = self._run(".mp3", "$unset(totaltracks)")
        from mutagen.id3 import ID3
        self.assertEqual([str(ID3(str(f))["TRCK"]) for f in files], ["1/9", "2/9"])

    def test_mp3_set_empty(self):
        files = self._run(".mp3", "$set(totaltracks,)")
        from mutagen.id3 import ID3
        self.assertEqual([str(ID3(str(f))["TRCK"]) for f in files], ["1/9", "2/9"])

    def test_m4a_unset(self):
        files = self._run(".m4a", "$unset(totaltracks)")
        self.assertEqual([mutagen.File(str(f)).tags["trkn"][0] for f in files], [(1, 9), (2, 9)])


if __name__ == "__main__":
    unittest.main()
