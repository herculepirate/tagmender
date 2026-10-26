# Twenty-fifth independent review (B). Fails with TM_REPO=prev too (not a regression).
"""A tagger script that removes the disc number but not the disc total - the common
"$if($eq(%totaldiscs%,1),$unset(discnumber))" for single-disc albums - or the track number
but not the track total ($delete(tracknumber), $set(tracknumber,)) makes every MP3, WAV,
AIFF, M4A, WavPack/APE and WMA album fail: "track01.mp3: tag totaldiscs did not read back
as written; album put back as it was". Nothing is tagged, moved or renamed.

Cause: the total has no tag of its own in these formats - ID3 TPOS/TRCK, MP4 disk/trkn,
APEv2 Disc/Track and WM/PartOfSet hold "number/total" - so with no number tagio.write
(_write_pair, tagio.py:1064-) writes no total, and the executor's read-back check
(executor: the written fields must read back as planned) finds totaldiscs/totaltracks
missing and rolls the whole album back. plan.py plans totaldiscs as a field the file will
hold although it cannot hold it without the number. Picard writes the pair only when the
number is there (ID3File._save: TPOS only from discnumber; MP4: disk only with
discnumber), silently dropping the lone total.

Picard 3.0.2 end to end, a one-disc release, MP3, tagger script
$if($eq(%totaldiscs%,1),$unset(discnumber)): TRCK "1/2", no TPOS, everything else written.
M4A: trkn (1, 2), no disk. TagMender: the album fails and is put back."""
import sys
import unittest
from pathlib import Path

import mutagen

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _review25b_common as c  # noqa: E402

SINGLE_DISC = "$if($eq(%totaldiscs%,1),$unset(discnumber))"


class NumberRemovedTotalKept(unittest.TestCase):
    def run_one(self, ext, script):
        files = c.album(ext, 2)
        _p, res = c.run(files, [script])
        self.assertEqual(res["counts"], {"done": 1}, c.detail(res))
        return mutagen.File(str(files[0]))

    def test_mp3_unset_discnumber(self):
        f = self.run_one(".mp3", SINGLE_DISC)
        self.assertEqual(str(f.tags["TRCK"]), "1/2")
        self.assertNotIn("TPOS", f.tags)
        self.assertEqual(str(f.tags["TIT2"]), "Track 1")

    def test_m4a_unset_discnumber(self):
        f = self.run_one(".m4a", SINGLE_DISC)
        self.assertEqual(f.tags["trkn"], [(1, 2)])
        self.assertNotIn("disk", f.tags)

    def test_wavpack_unset_discnumber(self):
        f = self.run_one(".wv", SINGLE_DISC)
        self.assertEqual(str(f.tags["Track"]), "1/2")
        self.assertNotIn("Disc", f.tags)

    def test_mp3_delete_tracknumber(self):
        f = self.run_one(".mp3", "$delete(tracknumber)")
        self.assertNotIn("TRCK", f.tags)
        self.assertEqual(str(f.tags["TPOS"]), "1/1")


if __name__ == "__main__":
    unittest.main()
