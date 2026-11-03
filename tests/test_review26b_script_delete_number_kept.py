"""A tagger script's $delete(discnumber) / $delete(tracknumber) on a file an earlier tagging
numbered ("2/3", "5/9") leaves the file's old number there. Picard 3.0.2 deletes the tag
(checked end to end with Picard's own code: DISCNUMBER / TPOS / TRCK gone, the file named
without the number). TagMender's own docs: "`$delete` removes the tag".

Cause: plan.py, the per-file loop - "for num_, tot_ in tagio.ID3_PAIRS: if num_ in removals
and raw.get(tot_) and num_ not in raw: raw[num_] = None". The number then is *in* the target
(as None), so diff_tags() no longer treats it as one of the removals (`f in extra_removals and
f not in target`), and an empty target value is skipped ("if not a ... continue"): nothing is
removed. In FLAC/Ogg this worked before the last fix round (regression); in MP3 the previous
version failed the album instead.
"""
import unittest

import mutagen

import _review26b_common as c


class DeleteNumber(unittest.TestCase):
    def _go(self, ext, script):
        files = c.album(ext, 2)
        for f in files:
            c.pretag_numbers(f)
        p, res = c.run(files, scripts=[script])
        self.assertEqual(res.get("counts"), {"done": 1}, c.detail(p, res))
        return [mutagen.File(str(f)) for f in files]

    def test_flac_delete_discnumber(self):
        for f in self._go(".flac", "$delete(discnumber)"):
            self.assertNotIn("discnumber", {k.lower() for k in f.tags.keys()},
                             f"Picard deletes DISCNUMBER; the file has {dict(f.tags)}")

    def test_ogg_delete_tracknumber(self):
        for f in self._go(".ogg", "$delete(tracknumber)"):
            self.assertNotIn("tracknumber", {k.lower() for k in f.tags.keys()},
                             f"Picard deletes TRACKNUMBER; the file has {dict(f.tags)}")

    def test_mp3_delete_discnumber(self):
        for f in self._go(".mp3", "$delete(discnumber)"):
            self.assertEqual(f.tags.getall("TPOS"), [], "Picard deletes TPOS (it was 2/3 before)")

    def test_mp3_delete_tracknumber(self):
        for f in self._go(".mp3", "$delete(tracknumber)"):
            self.assertEqual(f.tags.getall("TRCK"), [], "Picard deletes TRCK (it was 5/9 before)")


if __name__ == "__main__":
    unittest.main()
