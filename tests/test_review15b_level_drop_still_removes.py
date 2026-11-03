# Fifteenth independent review (B): this test failed before its fix.
"""level_fields.drop is "never write these, at any level" (docs/PROFILES.md), and
levels.restrict drops those fields from every target - but not the "~remove" list that
mb_tags.release_tags (compilation on a release not by Various Artists) and the tagger scripts
($delete) put beside them. The planner then deletes a field the profile says TagMender never
writes: a dropped compilation flag is removed from the user's files all the same."""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _review15b_common  # noqa: E402,F401

from test_engine import Base, FakeHttp, mb_release  # noqa: E402  (the repo's own test helpers)

MBID = "0a8e97fd-4f6a-4bd7-8e4b-6d3a0b2ad2f1"


class DropMeansUntouched(Base):
    def plan_with(self, script=None, **tags):
        d = self.album("Band", "Album", **tags)
        prof = {"level_fields": {"add": [], "drop": ["compilation", "mood"]}}
        if script:
            prof["picard"] = {"enable_tagger_scripts": True, "list_of_scripts": [script]}
        e = self.engine(FakeHttp(mb=[mb_release(MBID, "Band", "Album")]), **prof)
        e.force = {os.path.realpath(d): MBID}
        return e.plan(d)["albums"][0]["files"][0]["changes"] or {}

    def test_dropped_compilation_is_not_deleted(self):
        ch = self.plan_with(compilation="1")
        self.assertNotIn("compilation", ch, "a field the profile never writes is deleted")

    def test_dropped_field_is_not_deleted_by_a_tagger_script(self):
        ch = self.plan_with("$delete(mood)", mood="calm")
        self.assertNotIn("mood", ch, "a field the profile never writes is deleted")


if __name__ == "__main__":
    unittest.main()
