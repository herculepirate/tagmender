# Fifteenth independent review (B): this test failed before its fix.
"""plan.py, copy profile, an album copied by an earlier run: the planner brings the copy's tags
up to date with diff_tags(have, target, ..., []) - with no removals. What Picard would delete
(a tagger script's $delete, compilation on a release not by Various Artists) is worked out
for the source but never applied to the copy: a script added since the first copy, or the
first copy's compilation flag, stays on the copy for good, while the same run in place or
moving deletes it."""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _review15b_common  # noqa: E402,F401

from test_engine import Base, FakeHttp, mb_release  # noqa: E402  (the repo's own test helpers)
import executor  # noqa: E402
import tagio  # noqa: E402

MBID = "0a8e97fd-4f6a-4bd7-8e4b-6d3a0b2ad2f1"


class CopyRerun(Base):
    def test_a_tagger_script_delete_reaches_the_copy(self):
        d = self.album("Band", "Album", mood="calm")
        dest = self.tmp / "dest"
        dest.mkdir()
        kw = dict(operation="copy", destination=str(dest), sources=[{"path": str(self.lib)}])
        http = FakeHttp(mb=[mb_release(MBID, "Band", "Album")])
        p = self.engine(http, **kw).plan(d)
        self.assertEqual(executor.Executor(p).run()["counts"], {"done": 1})
        copies = [Path(fe["dst"]) for fe in p["albums"][0]["files"]]
        self.assertEqual(tagio.read(copies[0]).get("mood"), ["calm"])
        # the user turns on a tagger script deleting the mood, and runs the profile again
        e = self.engine(http, picard={"enable_tagger_scripts": True, "list_of_scripts": ["$delete(mood)"]}, **kw)
        p2 = e.plan(d)
        fe = p2["albums"][0]["files"][0]
        self.assertEqual(fe.get("copy_of"), str(sorted(d.glob("*.flac"))[0]), fe)
        self.assertIn("mood", fe["changes"] or {}, "the copy keeps the tag the script deletes")


if __name__ == "__main__":
    unittest.main()
