"""Regression from the last fix round: a movement total is dropped in MP4 and APEv2 files when
a tagger script removes the movement number.

plan.py's new block (`if not tagio.separate_totals(f): ... for num_, tot_ in tagio.ID3_PAIRS:
if (num_ in removals or num_ in unset_) and num_ not in raw: raw.pop(tot_, None)`) treats every
format but Vorbis as keeping each total inside its number's field. That holds for ID3 (MVIN
"2/5") but not for the movement in MP4 (separate atoms ©mvi / ©mvc) or APEv2 (MOVEMENT /
MOVEMENTTOTAL items) - and tagio.ID3_PAIRS includes ("movementnumber", "movementtotal").

"$delete(movementnumber)$set(movementtotal,5)": Picard 3.0.2 writes ©mvc 5 (MP4) and
MOVEMENTTOTAL 5 (WavPack); TagMender writes no movement total at all. Checked end to end with
Picard's own code. Passes with TM_REPO=<review-27/prev>.
"""
import unittest

import mutagen

import _review27b_common as c

SCRIPT = "$delete(movementnumber)$set(movementtotal,5)"


class MovementTotal(unittest.TestCase):
    def _run(self, ext):
        files = c.album(ext, 1)
        p, res = c.run(files, scripts=[SCRIPT])
        self.assertEqual(res.get("counts"), {"done": 1}, c.detail(p, res))
        return mutagen.File(str(files[0]))

    def test_m4a(self):
        f = self._run(".m4a")
        self.assertEqual(list(f.tags.get("\xa9mvc", [])), [5])

    def test_wavpack(self):
        f = self._run(".wv")
        self.assertEqual(str(f.tags.get("MOVEMENTTOTAL", "")), "5")


if __name__ == "__main__":
    unittest.main()
