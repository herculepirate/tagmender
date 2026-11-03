# Sixteenth independent review (B): this test failed before its fix.
# Sixteenth independent review (B). Fails on src, passes on prev (a regression).
"""plan.diff_tags: the new replace-threshold guard for Picard's removals ("~remove":
compilation, a tagger script's $delete) also swallows a field the PROFILE says to remove.
A field_policy "remove" deletes the field at any confidence (diff_tags' "remove" branch and its
closing loop never look at the confidence) - but when the same field is also in ~remove, the
new guard ("kept (confidence below replace threshold)") runs first and `continue`s, and the
closing loop skips it because it is in `fields`. So "compilation": "remove" stops removing
compilation on any match below replace_min_confidence of a release not by Various Artists
(mb_tags puts compilation in ~remove on every such release)."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _review16b_common  # noqa: E402,F401

import plan as pl  # noqa: E402
import tagio  # noqa: E402
from test_executor import Base, target  # noqa: E402  (the repo's own test helpers)


class RemovePolicyBelowThreshold(Base):
    def test_profile_remove_policy_still_removes_below_threshold(self):
        for f in (self.f1, self.f2):
            tagio.write(f, {"compilation": ["1"], "comment": ["junk"]})
        prof = self.profile(thresholds={"organize_min_confidence": 99, "replace_min_confidence": 99},
                            field_policy={"default": "replace",
                                          "fields": {"compilation": "remove", "comment": "remove"}})
        tg = [dict(target(i, t), **{"~remove": ["compilation"]}) for i, t in ((1, "First"), (2, "Second"))]
        p = pl.build([self.proposal(conf=96.0, targets=tg)], prof)
        ch = p["albums"][0]["files"][0]["changes"]
        # the profile's "remove" applies at any confidence: comment (not in ~remove) goes ...
        self.assertIn("comment", ch)
        self.assertIsNone(ch["comment"])
        # ... and so must compilation, which the profile says to remove just the same
        self.assertIn("compilation", ch, "the profile's remove policy is ignored for a field Picard also removes")
        self.assertIsNone(ch["compilation"])


if __name__ == "__main__":
    unittest.main()
