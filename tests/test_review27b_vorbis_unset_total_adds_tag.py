"""Last round's fix for "$unset(totaltracks)" covered ID3/MP4/APEv2/ASF only
(plan.py: `if not tagio.separate_totals(f):`), not FLAC and Ogg.

A FLAC/Ogg file an earlier tagging numbered (TRACKNUMBER=5, TRACKTOTAL=9, DISCNUMBER=2,
DISCTOTAL=3), re-tagged with the common script "$unset(totaltracks)$unset(totaldiscs)":
Picard 3.0.2 writes TRACKNUMBER/DISCNUMBER and leaves the file's TRACKTOTAL/DISCTOTAL as they
are (no total in the metadata: nothing written for it). TagMender adds TOTALTRACKS=9 and
TOTALDISCS=3 - fields the file never had, copied from the old TRACKTOTAL/DISCTOTAL - so the
file now holds every total twice. Checked end to end with Picard's own code
(Track.update_file_metadata + VCommentFile._save). Fails with TM_REPO=prev too (not a
regression; review-26 B test_flac_unset_totaltracks_adds_no_tag still fails).
"""
import unittest

import mutagen

import _review27b_common as c


class VorbisUnsetTotal(unittest.TestCase):
    def _keys(self, ext, script):
        files = c.album(ext, 2)
        for f in files:
            c.pretag_numbers(f)
        p, res = c.run(files, scripts=[script])
        self.assertEqual(res.get("counts"), {"done": 1}, c.detail(p, res))
        return [sorted(k.upper() for k in mutagen.File(str(f)).tags.keys()) for f in files]

    def test_flac(self):
        for keys in self._keys(".flac", "$unset(totaltracks)$unset(totaldiscs)"):
            self.assertNotIn("TOTALTRACKS", keys)
            self.assertNotIn("TOTALDISCS", keys)

    def test_ogg(self):
        for keys in self._keys(".ogg", "$unset(totaltracks)"):
            self.assertNotIn("TOTALTRACKS", keys)


if __name__ == "__main__":
    unittest.main()
