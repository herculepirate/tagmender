# Thirtieth independent review (B). REGRESSION: passes with TM_REPO=prev (review-30/prev), fails
# with the review-30 src snapshot.
"""A Vorbis file whose track/disc number holds its total ("5/9", "2/3" - as ffmpeg leaves an
MP3's TRCK/TPOS when converting to FLAC/Ogg/Opus, and as some older taggers write) gets that
OLD total written back as TRACKTOTAL/TOTALTRACKS (DISCTOTAL/TOTALDISCS) when a tagger script
deletes or unsets the total:

  $if($eq(%totaldiscs%,1),$delete(discnumber)$delete(totaldiscs))   (the usual "no disc number on
  single-disc releases" script) on DISCNUMBER "1/1"  -> DISCNUMBER deleted but TOTALDISCS=1
                                                        DISCTOTAL=1 ADDED (Picard: none)
  $delete(totaltracks)$delete(totaldiscs)            -> TOTALTRACKS=9 TRACKTOTAL=9
                                                        TOTALDISCS=3 DISCTOTAL=3 written
  $unset(totaltracks) with totaltracks preserved     -> TOTALTRACKS=9 TRACKTOTAL=9 written
  the same $delete, number unchanged ("1/3", "1/9"    -> DISCNUMBER "1/3", TRACKNUMBER "1/9"
  on track 1 of disc 1)                                  left as they are (Picard: 1 and 1)

The release has 2 tracks on 1 disc, so the files end up saying "track 1 of 9, disc 1 of 3".
Picard 3.0.2 (checked end to end with its own code, r30B_work/fixes.py) writes TRACKNUMBER=1,
DISCNUMBER=1 and no total fields at all: it reads "5/9" as the number "5/9" with no total
(vorbis.py _load), so there is nothing to keep, and $delete of a field the file does not have
removes nothing.

Cause: this round's change in plan.py (~line 951) takes the inside total out of `before`
(`before_d`), so diff_tags no longer plans a removal or a value for the total; tagio.write then
fills the pair from `current = read(path)` (tagio.py ~1157 and _pair() ~1070), and read() splits
"5/9" into totaltracks=9 - which _write_pair (~1281-1287) writes into both total fields because
the number changed and the total is not in `keep`. When the number itself is unchanged ("1/3" on
disc 1), nothing about the pair is in the changes any more, so _write_pair (~1272) never
rewrites the "1/3" field and the old total stays inside it.

Fix: in tagio.write/_write_pair, for a Vorbis pair whose total is only inside the number field
(`inside` is already computed there), use no total unless the changes give one; or have
plan.py pass such totals as removals / keep=() consistently.
"""
import os
import sys
import unittest
from pathlib import Path

import mutagen

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _review30b_common as c  # noqa: E402


def ffmpeg_like(files):
    for f in files:
        m = mutagen.File(str(f))
        if m.tags is None:
            m.add_tags()
        m.tags["TRACKNUMBER"] = ["5/9"]
        m.tags["DISCNUMBER"] = ["2/3"]
        m.tags["TITLE"] = ["Old"]
        m.save()


TOTALS = ("TOTALTRACKS", "TRACKTOTAL", "TOTALDISCS", "DISCTOTAL")


class VorbisInsideTotal(unittest.TestCase):
    def tearDown(self):
        try:
            os.remove(os.environ["TAGMENDER_PICARD_INI"])
        except OSError:
            pass

    def check(self, ext, scripts, ini=None, totals=TOTALS):
        if ini:
            Path(os.environ["TAGMENDER_PICARD_INI"]).write_text(ini, encoding="utf-8")
        files = c.album(ext, 2)
        ffmpeg_like(files)
        p, res = c.run(files, scripts=scripts)
        self.assertEqual(res.get("counts"), {"done": 1}, c.detail(p, res))
        for i, f in enumerate(files):
            tags = mutagen.File(str(f)).tags
            self.assertEqual(list(tags.get("TRACKNUMBER") or []), [str(i + 1)])
            got = {k: list(tags[k]) for k in totals if k in tags}
            self.assertEqual(got, {}, f"{f.name}: the old total from '5/9'/'2/3' written back "
                                      f"(Picard writes no total): {got}")

    def test_delete_totals_flac(self):
        self.check(".flac", ["$delete(totaltracks)$delete(totaldiscs)"])

    def test_delete_totals_opus(self):
        self.check(".opus", ["$delete(totaltracks)$delete(totaldiscs)"])

    def test_unset_preserved_total_ogg(self):
        self.check(".ogg", ["$unset(totaltracks)"],
                   ini="[application]\nversion=3.0.2\n[setting]\npreserved_tags=totaltracks\n",
                   totals=("TOTALTRACKS", "TRACKTOTAL"))      # (the disc total is the release's 1)

    def test_delete_totals_number_with_total_rewritten(self):
        for ext in (".flac", ".ogg"):
            files = c.album(ext, 2)
            for i, f in enumerate(files):
                m = mutagen.File(str(f))
                m.tags["TRACKNUMBER"], m.tags["DISCNUMBER"] = [f"{i + 1}/9"], ["1/3"]
                m.save()
            p, res = c.run(files, scripts=["$delete(totaldiscs)$delete(totaltracks)"])
            self.assertEqual(res.get("counts"), {"done": 1}, c.detail(p, res))
            tags = mutagen.File(str(files[0])).tags
            self.assertEqual((list(tags["DISCNUMBER"]), list(tags["TRACKNUMBER"])), (["1"], ["1"]),
                             f"{ext}: the old totals kept inside the numbers (Picard writes 1 and 1)")

    def test_single_disc_script_deletes_disc(self):
        # the common "no disc number on single-disc releases" script, on DISCNUMBER "1/1"
        files = c.album(".flac", 2)
        for f in files:
            m = mutagen.File(str(f))
            m.tags["DISCNUMBER"] = ["1/1"]
            m.save()
        p, res = c.run(files, scripts=["$if($eq(%totaldiscs%,1),$delete(discnumber)$delete(totaldiscs))"])
        self.assertEqual(res.get("counts"), {"done": 1}, c.detail(p, res))
        tags = mutagen.File(str(files[0])).tags
        got = {k: list(tags[k]) for k in ("DISCNUMBER", "TOTALDISCS", "DISCTOTAL") if k in tags}
        self.assertEqual(got, {}, "Picard deletes DISCNUMBER and writes no disc total")


if __name__ == "__main__":
    unittest.main()
