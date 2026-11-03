# Seventeenth independent review (B): this test failed before its fix.
# Seventeenth independent review (B). Regression.
"""plan.Planner.album works the release's edition out ("rel": media and dates of the first
disc, plan.py ~726-742) from targets.get(f0) - the target BEFORE the tagger scripts, which
now run later, per file (Planner._scripted, line ~763). Before this version the engine ran
the scripts, so the edition came from the tags as they are written. With the common Picard
script $set(date,%originaldate%) (an original CD: date 1999, as the tags then say) and
naming.album_editions "edition_year", the album tag now becomes "Album [2005 CD]" - a reissue
edition from the date the script replaced - while the same plan writes DATE=1999-01-01; the
folder edition (naming.editions) is wrong the same way. The tags contradict each other."""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _review17b_common  # noqa: E402,F401

from test_engine import Base, FakeHttp, mb_release  # noqa: E402  (the repo's own test helpers)

MBID = "0a8e97fd-4f6a-4bd7-8e4b-6d3a0b2ad2f1"


class EditionFromUnscriptedTarget(Base):
    def test_album_edition_follows_the_scripted_date(self):
        d = self.album("Band", "Album")       # (mb_release: CD, date 2005-03-01, first released 1999-01-01)
        e = self.engine(FakeHttp(mb=[mb_release(MBID, "Band", "Album")]), naming={"album_editions": "edition_year"},
                        picard={"enable_tagger_scripts": True, "list_of_scripts": ["$set(date,%originaldate%)"]})
        e.force = {os.path.realpath(d): MBID}
        a = e.plan(d)["albums"][0]
        self.assertEqual(a["identification"]["status"], "identified", a["identification"])
        fe = a["files"][0]
        self.assertEqual(fe["changes"].get("date"), ["1999-01-01"])
        self.assertNotIn("album", fe["changes"],
                         "an original CD (DATE = ORIGINALDATE after the script) gets no reissue edition")


if __name__ == "__main__":
    unittest.main()
