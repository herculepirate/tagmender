# Twenty-ninth independent review (B). Fails with TM_REPO=prev too (not a regression).
"""FLAC/Ogg/Opus: with a total in "Preserve these tags", a file whose number field holds
"5/9" (TRACKNUMBER=5/9, no TRACKTOTAL - as some taggers write it) keeps 9 as a "preserved"
total in TagMender. Picard 3.0.2 reads no total from such a file (VCommentFile._load keeps
tracknumber "5/9" as it is and has no totaltracks), so there is nothing to preserve and it
writes the release's total.

Checked end to end with Picard's own code, a 2-track single-disc release, files with
TRACKNUMBER=5/9 and DISCNUMBER=2/3, preserved_tags=totaltracks (then =totaldiscs):
  Picard:    TRACKTOTAL=2 TOTALTRACKS=2          | DISCTOTAL=1 TOTALDISCS=1, named "01 Hello.flac"
  TagMender: TRACKTOTAL=9 TOTALTRACKS=9          | DISCTOTAL=3 TOTALDISCS=3, named "1-01 Hello.flac"
(the default naming script then treats the single disc as one of three).

Cause: tagio.read gives a Vorbis "5/9" number as tracknumber 5 and totaltracks 9; the planner's
preserve policy (plan.py, diff_tags / the policy checks around plan.py:935 and the naming
fields at plan.py:~1010-1020) keeps that derived total as if the file held it. Picard's
preserved tags are the file's own fields as Picard loads them.

Fix: for the preserve policy on Vorbis, use the file's own TRACKTOTAL/TOTALTRACKS
(DISCTOTAL/TOTALDISCS) fields only, not a total taken from inside the number.
"""
import os
import sys
import unittest
from pathlib import Path

import mutagen

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _review29b_common as c  # noqa: E402
import picard_config  # noqa: E402


class SlashNumberPreservedTotal(unittest.TestCase):
    def tearDown(self):
        os.environ["TAGMENDER_PICARD_INI"] = "/nonexistent/tm29B/Picard.ini"
        picard_config._cache.clear()

    def _go(self, ext, preserved):
        ini = c.write_ini(f"[application]\nversion=3.0.2.final0\n\n[setting]\npreserved_tags={preserved}\n")
        os.environ["TAGMENDER_PICARD_INI"] = str(ini)
        picard_config._cache.clear()
        files = c.album(ext, 2)
        for f in files:
            m = mutagen.File(str(f))
            if m.tags is None:
                m.add_tags()
            m.tags["TRACKNUMBER"], m.tags["DISCNUMBER"] = ["5/9"], ["2/3"]
            m.save()
        p, res = c.run(files)
        self.assertEqual(res.get("counts"), {"done": 1}, c.detail(p, res))
        return [{k.upper(): v for k, v in mutagen.File(str(f)).tags.items()} for f in files]

    def test_flac_totaltracks(self):
        for t in self._go(".flac", "totaltracks"):
            self.assertEqual((t.get("TRACKTOTAL"), t.get("TOTALTRACKS")), (["2"], ["2"]),
                             "Picard: the file has no total of its own to preserve")

    def test_ogg_totaldiscs(self):
        for t in self._go(".ogg", "totaldiscs"):
            self.assertEqual((t.get("DISCTOTAL"), t.get("TOTALDISCS")), (["1"], ["1"]))


if __name__ == "__main__":
    unittest.main()
