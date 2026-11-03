"""M4A: a tagger script that sets a total to 0 ("$set(totaldiscs,0)", "$set(totaltracks,0)")
leaves the file's old total in trkn/disk - (1, 3) where Picard writes (1, 0).

tagio.py:883-885 (the MP4 normalisation in the write path) turns a total that is not a
positive number into None ("a total that is not, 0" says the comment), and the plan then
treats the field as having no value, so it is not among the changes and _write_pair
(tagio.py:1121-1126) takes the file's current total (3) for the pair. Picard 3.0.2
(MP4File._save: `int(metadata['totaldiscs'])`) writes disk (1, 0): no total. MP3, FLAC,
WavPack and WMA match Picard here (TPOS "1/0" ...). Checked end to end with Picard's code.
The disc set shows "disc 1 of 3" (the old file's total) after a script cleared it.
Fails with TM_REPO=prev too (not a regression).
"""
import unittest

import mutagen

import _review28b_common as c


class Mp4ZeroTotal(unittest.TestCase):
    def test_zero_totals(self):
        files = c.album(".m4a", 2)
        for f in files:
            c.pretag_numbers(f)                    # trkn (5, 9), disk (2, 3)
        p, res = c.run(files, scripts=["$set(totaldiscs,0)$set(totaltracks,0)"])
        self.assertEqual(res.get("counts"), {"done": 1}, c.detail(p, res))
        tags = [mutagen.File(str(f)).tags for f in files]
        self.assertEqual([t["disk"][0] for t in tags], [(1, 0), (1, 0)])
        self.assertEqual([t["trkn"][0] for t in tags], [(1, 0), (2, 0)])


if __name__ == "__main__":
    unittest.main()
