"""Re-tagging files an earlier tagging numbered "5/9" (disc "2/3") with a tagger script that
unsets a total or a number: TagMender combines what is left with the file's OLD value.

1. $unset(totaltracks) - a common script to write the track number without its total:
   Picard 3.0.2 writes TRCK "1" (no total in the metadata, so none in the frame);
   TagMender writes "1/9" - the new number with the old file's total. Same in MP4 (trkn
   (1, 9) where Picard writes (1, 0)), APEv2 and ASF. In FLAC/Ogg TagMender adds a
   TOTALTRACKS=9 the file did not have (Picard leaves the file's TRACKTOTAL as it is).
2. $unset(tracknumber) (or $set(tracknumber,)): Picard writes no TRCK at all - the frame
   stays "5/9"; TagMender writes "5/2": the file's old number with the release's total.

Cause: tagio.write -> _pair(current, changes, num, tot) fills the part a script removed
from the file's current value; and tagio.stored (the last round's "number from the file"
branch: `have = read(path); n_ok = bool(have.get(num))`) keeps the new total because the
FILE has a number. Picard only writes TRCK/TPOS/trkn/disk from the number and total in the
metadata, and never writes them when the number is missing.
Checked end to end with Picard's own code (Track.update_file_metadata + the format's _save).
"""
import unittest

import mutagen

import _review26b_common as c


class UnsetTotal(unittest.TestCase):
    def _go(self, ext, script):
        files = c.album(ext, 2)
        for f in files:
            c.pretag_numbers(f)
        p, res = c.run(files, scripts=[script])
        self.assertEqual(res.get("counts"), {"done": 1}, c.detail(p, res))
        return [mutagen.File(str(f)) for f in files]

    def test_mp3_unset_totaltracks(self):
        got = [str(f.tags["TRCK"]) for f in self._go(".mp3", "$unset(totaltracks)")]
        self.assertEqual(got, ["1", "2"], "Picard writes the number alone; not the old file's total 9")

    def test_m4a_unset_totaltracks(self):
        got = [f.tags["trkn"][0] for f in self._go(".m4a", "$unset(totaltracks)")]
        self.assertEqual(got, [(1, 0), (2, 0)], "Picard writes trkn (n, 0); not the old file's total 9")

    @unittest.skip("accepted: a Vorbis file's totals are rewritten together (TOTALTRACKS beside TRACKTOTAL, the same value)")
    def test_flac_unset_totaltracks_adds_no_tag(self):
        for f in self._go(".flac", "$unset(totaltracks)"):
            self.assertNotIn("TOTALTRACKS", [k.upper() for k in f.tags.keys()],
                             "the file had TRACKTOTAL=9 only; Picard adds no TOTALTRACKS")

    def test_mp3_unset_tracknumber_keeps_frame(self):
        got = [str(f.tags["TRCK"]) for f in self._go(".mp3", "$unset(tracknumber)")]
        self.assertEqual(got, ["5/9", "5/9"], "Picard writes no TRCK without a number: the file's stays")


if __name__ == "__main__":
    unittest.main()
