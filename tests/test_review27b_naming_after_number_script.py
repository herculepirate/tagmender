"""Regression from the last fix round (plan.py, the new `if not tagio.separate_totals(f):`
block in Planner.album): file names of re-tagged files whose tagger script removes a disc
number or a total come out differently from Picard 3.0.2.

Picard names a file from the file's ORIGINAL tags overlaid with the new metadata
(File._script_to_filename: metadata.copy(self.orig_metadata); metadata.update(file_metadata)).
The files here carry the numbers an earlier tagging left (TRCK "5/9", TPOS "2/3"; FLAC
TRACKTOTAL/DISCTOTAL) and the release has one disc; naming script: Picard's default.

1. "$delete(discnumber)" - Picard: the metadata keeps totaldiscs 1 -> "01 Track 1". TagMender
   now drops the release's totaldiscs (raw.pop(tot_) because the number was removed), so the
   name takes the file's old total 3 with no disc number: "-01 Track 1". The same with
   "$unset(discnumber)": Picard "01 Track 1", TagMender "2-01 Track 1" (old number and total).
2. "$unset(totaltracks)$unset(totaldiscs)" - Picard: the totals are not in the new metadata,
   so the file's old totaldiscs 3 is used: "1-01 Track 1". TagMender now puts "totaldiscs"
   into the removals (removals.append(tot_)), the planned change {totaldiscs: None} pops it
   from the values the name is made from: "01 Track 1".

The tags written are right in both cases (checked end to end with Picard's own code:
Track.update_file_metadata + the format's _save + File.make_filename); only the names
moved. Both pass with TM_REPO=<review-27/prev>.
"""
import unittest

import _review27b_common as c

PICARD_DEFAULT = ("$if2(%albumartist%,%artist%)/\n$if(%albumartist%,%album%/,)\n"
                  "$if($gt(%totaldiscs%,1),$if($gt(%totaldiscs%,9),$num(%discnumber%,2),%discnumber%)-,)"
                  "$if($and(%albumartist%,%tracknumber%),$num(%tracknumber%,2) ,)$if(%_multiartist%,%artist% - ,)"
                  "%title%")
ALBUM = "Pink Floyd/The Dark Side of the Moon/"


class NamingAfterNumberScript(unittest.TestCase):
    def _names(self, ext, script):
        files = c.album(ext, 2)
        for f in files:
            c.pretag_numbers(f)
        p, res, dest = c.move_run(files, PICARD_DEFAULT, scripts=[script])
        self.assertEqual(res.get("counts"), {"done": 1}, c.detail(p, res))
        return c.names(dest)

    def test_mp3_delete_discnumber(self):
        self.assertEqual(self._names(".mp3", "$delete(discnumber)"),
                         [ALBUM + "01 Track 1.mp3", ALBUM + "02 Track 2.mp3"])

    def test_m4a_delete_discnumber(self):
        self.assertEqual(self._names(".m4a", "$delete(discnumber)"),
                         [ALBUM + "01 Track 1.m4a", ALBUM + "02 Track 2.m4a"])

    def test_mp3_unset_discnumber(self):
        # Picard: the file's old disc number 2 with the release's totaldiscs 1 -> no disc prefix;
        # TagMender drops the release's total (raw.pop) and names with the file's old "2/3": "2-01"
        self.assertEqual(self._names(".mp3", "$unset(discnumber)"),
                         [ALBUM + "01 Track 1.mp3", ALBUM + "02 Track 2.mp3"])

    def test_mp3_unset_totals(self):
        self.assertEqual(self._names(".mp3", "$unset(totaltracks)$unset(totaldiscs)"),
                         [ALBUM + "1-01 Track 1.mp3", ALBUM + "1-02 Track 2.mp3"])

    def test_m4a_unset_totals(self):
        self.assertEqual(self._names(".m4a", "$unset(totaltracks)$unset(totaldiscs)"),
                         [ALBUM + "1-01 Track 1.m4a", ALBUM + "1-02 Track 2.m4a"])


if __name__ == "__main__":
    unittest.main()
