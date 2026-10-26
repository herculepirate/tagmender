# Twentieth independent review (B): this test failed before its fix.
# Twentieth independent review (B).
"""A comment set to a text that ends in "(...)" - a tagger script's
$set(comment,Ripped with EAC \\(secure mode\\)) - no longer replaces the file's plain comment in
a FLAC/Ogg/WavPack file: the old comment stays beside the new one.

tagio.stored() now splits the target's "comment" the way Picard reads a Vorbis COMMENT, so the
target becomes {"comment:secure mode": ["Ripped with EAC"]} and has no "comment" left; the
planner's diff (only the target's fields) then never touches the file's plain "comment", and
_set_performers(base="comment") keeps every value whose "role" is not "secure mode".

Picard 3.0.2 (VCommentFile._save, run on the same FLAC with COMMENT=["Old", "Recorded live
(2001)"] and metadata["comment"] = "Ripped with EAC (secure mode)"): COMMENT =
["Ripped with EAC (secure mode)", "Recorded live (2001)"] - "Old" is replaced.
TagMender: COMMENT = ["Old", "Recorded live (2001)", "Ripped with EAC (secure mode)"].

Regression: passes with TM_REPO=<prev> (which wrote COMMENT as the script set it)."""
import sys
import types
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _review20b_common  # noqa: E402,F401

import mutagen  # noqa: E402
import engine  # noqa: E402
import executor  # noqa: E402
import plan as pl  # noqa: E402
from test_executor import Base, target  # noqa: E402  (the repo's own test helpers)

SCRIPT = r"$set(comment,Ripped with EAC \(secure mode\))"


class VorbisCommentWithParentheses(Base):
    def test_plain_comment_is_replaced(self):
        for f in (self.f1, self.f2):
            fl = mutagen.File(f)
            fl["COMMENT"] = ["Old", "Recorded live (2001)"]
            fl.save()
        prof = self.profile(picard={"enable_tagger_scripts": True, "list_of_scripts": [SCRIPT]})
        prop = self.proposal(targets=[target(1, "First"), target(2, "Second")])
        engine.Engine.apply_level(types.SimpleNamespace(profile=prof, log=None, http=None), prop)
        p = pl.build([prop], prof)
        self.assertEqual(p["albums"][0]["status"], "ready", p["albums"][0]["conflicts"])
        res = executor.Executor(p).run()
        self.assertEqual(res["counts"], {"done": 1}, res)
        got = mutagen.File(self.f1).tags.get("COMMENT")
        # (the plain comment the script replaced is gone, as in Picard; the described one,
        # "Recorded live (2001)", Picard keeps - the previous version lost it, this one keeps it)
        self.assertIn("Ripped with EAC (secure mode)", got)
        self.assertNotIn("Old", got)


if __name__ == "__main__":
    unittest.main()
