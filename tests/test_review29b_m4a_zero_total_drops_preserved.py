# Twenty-ninth independent review (B). REGRESSION: passes with TM_REPO=prev (last round's
# "M4A zero totals" fix).
"""M4A: a total that "Preserve these tags" keeps is thrown away when a tagger script sets it
to 0. Picard 3.0.2 keeps the file's total (preserved tags are put back after the scripts).

Checked end to end with Picard's own code: Picard.ini preserved_tags=totaltracks, an M4A
numbered trkn (5, 9), the tagger script $set(totaltracks,0)$set(totaldiscs,0):
  Picard:          trkn (1, 9)
  TagMender now:   trkn (1, 0)
  TagMender prev:  trkn (1, 9)   (as Picard)
(the same with preserved_tags=totaldiscs and disk (2, 3) -> Picard (1, 3), TagMender (1, 0)).

Cause: plan.py:940-945, added last round - "a value the format holds as none - an M4A total
0: the file's goes" - appends the field to `removals` whenever stored() makes it None and the
file has one, without the `p.policy(k_) not in ("preserve", "lock", "fill_missing")` check
every other removal in that loop makes (compare plan.py:935-937). The removal then wins over
the preserved value.

Fix: skip a field whose policy is preserve, lock or fill_missing (keep the file's value) in
that loop.
"""
import os
import sys
import unittest
from pathlib import Path

import mutagen

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _review29b_common as c  # noqa: E402
import picard_config  # noqa: E402


class M4AZeroTotalPreserved(unittest.TestCase):
    def tearDown(self):
        os.environ["TAGMENDER_PICARD_INI"] = "/nonexistent/tm29B/Picard.ini"
        picard_config._cache.clear()

    def _go(self, preserved, atom):
        ini = c.write_ini(f"[application]\nversion=3.0.2.final0\n\n[setting]\npreserved_tags={preserved}\n")
        os.environ["TAGMENDER_PICARD_INI"] = str(ini)
        picard_config._cache.clear()
        files = c.album(".m4a", 2)
        for f in files:
            c.pretag_numbers(f)                     # trkn (5, 9), disk (2, 3)
        p, res = c.run(files, scripts=["$set(totaltracks,0)$set(totaldiscs,0)"])
        self.assertEqual(res.get("counts"), {"done": 1}, c.detail(p, res))
        return [mutagen.File(str(f)).tags[atom][0] for f in files]

    def test_preserved_totaltracks(self):
        self.assertEqual(self._go("totaltracks", "trkn"), [(1, 9), (2, 9)], "Picard keeps the preserved total")

    def test_preserved_totaldiscs(self):
        self.assertEqual(self._go("totaldiscs", "disk"), [(1, 3), (1, 3)], "Picard keeps the preserved total")


if __name__ == "__main__":
    unittest.main()
