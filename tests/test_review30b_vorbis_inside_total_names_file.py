# Thirtieth independent review (B). Not a regression (fails with TM_REPO=prev too).
"""A Vorbis file (FLAC/Ogg/Opus) whose number field holds a total - DISCNUMBER "1/3",
TRACKNUMBER "1/9", as ffmpeg leaves an MP3's TPOS/TRCK when converting, or a file from another
edition - is NAMED with that old total when a tagger script unsets the totals (or the total is a
preserved tag):

  $unset(totaldiscs)$unset(totaltracks)    ->  "1-01 Track 1.flac" (default naming,
                                               $gt(%totaldiscs%,1) true from the "1/3")
  (also: $if($eq(%totaldiscs%,1),$unset(discnumber)$unset(totaldiscs)) on a single-disc
  release with "1/3", and preserved_tags=totaldiscs)

Picard 3.0.2 (checked end to end with its own code, r30B_work/j_inside.py) writes DISCNUMBER=1
and TRACKNUMBER=1 - it reads "1/3" as the number "1/3", which differs from the release's "1",
so it rewrites it - and names the file "01 Track 1.flac" (%totaldiscs% is empty: the script
unset it and Picard reads no total from "1/3").

The album is filed under different names than Picard gives it (and than TagMender itself gives
the same files once their numbers are rewritten - the name changes on a later run).

Cause: tagio.read splits a Vorbis "1/3" into discnumber 1 + totaldiscs 3; the naming values
come from that read (plan.py, the `names` built from `before` for a field the script unset or
a preserved one), where the inside total is still 3 - plan.py strips it (vorbis_totals'
`v_inside`, ~line 953) only from the diff, not from the naming values.

Fix: drop the inside totals (v_inside) from the values the file is named with, as Picard reads
them.
"""
import os
import sys
import unittest
from pathlib import Path

import mutagen

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _review30b_common as c  # noqa: E402

PICARD_DEFAULT = ("$if2(%albumartist%,%artist%)/\n$if(%albumartist%,%album%/,)\n"
                  "$if($gt(%totaldiscs%,1),$if($gt(%totaldiscs%,9),$num(%discnumber%,2),%discnumber%)-,)"
                  "$if($and(%albumartist%,%tracknumber%),$num(%tracknumber%,2) ,)"
                  "$if(%_multiartist%,%artist% - ,)%title%")


def numbered(files):
    for i, f in enumerate(files):
        m = mutagen.File(str(f))
        if m.tags is None:
            m.add_tags()
        m.tags["TRACKNUMBER"] = [f"{i + 1}/9"]
        m.tags["DISCNUMBER"] = ["1/3"]
        m.tags["TITLE"] = ["Old"]
        m.save()


class InsideTotalKept(unittest.TestCase):
    def test_unset_totals_named_without_disc(self):
        files = c.album(".flac", 2)
        numbered(files)
        p, res, dest = c.move_run(files, PICARD_DEFAULT, scripts=["$unset(totaldiscs)$unset(totaltracks)"])
        self.assertEqual(res.get("counts"), {"done": 1}, c.detail(p, res))
        self.assertEqual(c.names(dest), ["Pink Floyd/The Dark Side of the Moon/01 Track 1.flac",
                                         "Pink Floyd/The Dark Side of the Moon/02 Track 2.flac"])


if __name__ == "__main__":
    unittest.main()
