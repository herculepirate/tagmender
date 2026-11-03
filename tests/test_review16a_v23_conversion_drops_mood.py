# Sixteenth independent review (A): this test failed before its fix.
# Sixteenth independent review (A). New with Picard's "Write ID3v2.3" setting (not in prev).
# tagio.save_id3, making a v2.4 tag v2.3 (file_options {"v2": 3}), calls update_to_v23(),
# which deletes the frames ID3v2.3 does not have - TMOO (mood) and TDRL (release date) among
# them. TagMender writes those two as TXXX:MOOD / TXXX:RELEASEDATE in a v2.3 tag (write()'s
# "~v23:" path), but only for a field in the changes: a mood or release date the file holds
# and the plan does not change (the user's own, from another tagger; or the same value) is
# dropped without a word in the preview - and the next plan is "unchanged", so it never comes
# back. Picard writes every field of the file: with write_id3v23 it saves them as TXXX:MOOD
# and TXXX:RELEASEDATE (picard/formats/id3.py, write_id3v23 branch of _save_standard_tag).
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _review16a_common import Run, bare_mp3, pl  # noqa: E402

import tagio  # noqa: E402
from mutagen.id3 import ID3, TDRL, TIT2, TMOO  # noqa: E402


class V23ConversionDropsMood(Run):
    def test_mood_and_release_date_survive_the_conversion(self):
        self.fs = [bare_mp3(self.alb / f"0{n} x.mp3", 440 + 100 * n) for n in (1, 2)]
        for f in self.fs:
            i = ID3()
            i.add(TIT2(encoding=3, text=["Old"]))
            i.add(TMOO(encoding=3, text=["Happy"]))
            i.add(TDRL(encoding=3, text=["2001-05-01"]))
            i.save(str(f), v2_version=4)
        self.assertEqual(tagio.read(self.fs[0])["mood"], ["Happy"])
        p, res, before, prof = self.run_plan({"write_id3v23": True})
        self.assertEqual(ID3(str(self.fs[0])).version[:2], (2, 3))
        rows = [r for r in p["albums"][0]["files"][0]["diff"] if r["field"] in ("mood", "releasedate")]
        self.assertEqual(rows, [])                                    # (the plan says nothing about them)
        after = tagio.read(self.fs[0])
        self.assertEqual((after.get("mood"), after.get("releasedate")), (["Happy"], ["2001-05-01"]),
                         "the file's mood and release date are gone after it was made ID3v2.3")
        again = pl.build([self.prop()], prof)
        self.assertEqual(again["albums"][0]["status"], "unchanged")


if __name__ == "__main__":
    unittest.main()
