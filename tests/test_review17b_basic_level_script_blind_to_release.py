# Seventeenth independent review (B): this test failed before its fix.
# Seventeenth independent review (B). Regression.
"""The tagger scripts moved from the engine to the plan, but engine.Engine.apply_level still
cuts each target to the level's fields (levels.restrict, engine.py ~782) BEFORE the plan runs
the scripts (plan.Planner._scripted). At tag_level "basic" a script therefore no longer sees
the release's other fields: $if($in(%releasetype%,live),$set(album,%album% [Live])) sees no
RELEASETYPE and leaves ALBUM as it is. Before this version (scripts in the engine, before
the level) the album was "Album [Live]", as Picard makes it. The docstring of _scripted
("Fields the profile's level leaves alone stay as they are") and the release notes say the
level is applied after the scripts. The same for %label%, %media%, %catalognumber% ...: a
basic profile's scripts and the naming variables they $set differ from Picard's."""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _review17b_common  # noqa: E402,F401

from test_engine import Base, FakeHttp, mb_release  # noqa: E402  (the repo's own test helpers)

MBID = "0a8e97fd-4f6a-4bd7-8e4b-6d3a0b2ad2f1"


class BasicLevelScriptBlindToRelease(Base):
    def test_basic_level_script_reads_the_releases_type(self):
        d = self.album("Band", "Album")
        e = self.engine(FakeHttp(mb=[mb_release(MBID, "Band", "Album")]), tag_level="basic",
                        picard={"enable_tagger_scripts": True,
                                "list_of_scripts": ["$if($in(%releasetype%,live),$set(album,%album% [Live]))"]})
        e.force = {os.path.realpath(d): MBID}
        a = e.plan(d)["albums"][0]
        self.assertEqual(a["identification"]["status"], "identified", a["identification"])
        fe = a["files"][0]
        self.assertNotIn("releasetype", fe["changes"])          # (basic: not written)
        self.assertEqual(fe["changes"].get("album"), ["Album [Live]"], fe["diff"])


if __name__ == "__main__":
    unittest.main()
