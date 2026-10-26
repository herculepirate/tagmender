# Fifteenth independent review (B): this test failed before its fix.
"""plan.diff_tags: an existing value is replaced only at replace_min_confidence or more ("kept
(confidence below replace threshold)") - but the removals Picard would make (~remove:
compilation on a release not by Various Artists, a tagger script's $delete) go through the
"remove" policy, which never looks at the confidence. A match too uncertain to change the
album title still deletes the file's compilation flag (and whatever a tagger script deletes)."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _review15b_common  # noqa: E402,F401

import plan as pl  # noqa: E402
import tagio  # noqa: E402
from test_executor import Base, target  # noqa: E402  (the repo's own test helpers)


class RemoveBelowThreshold(Base):
    def test_low_confidence_keeps_compilation_as_it_keeps_the_album(self):
        for f in (self.f1, self.f2):
            tagio.write(f, {"compilation": ["1"]})
        prof = self.profile(thresholds={"organize_min_confidence": 99, "replace_min_confidence": 99})
        tg = [dict(target(i, t), **{"~remove": ["compilation"]}) for i, t in ((1, "First"), (2, "Second"))]
        p = pl.build([self.proposal(conf=96.0, targets=tg)], prof)
        ch = p["albums"][0]["files"][0]["changes"]
        self.assertNotIn("album", ch, "the album title is kept (confidence below the replace threshold)")
        self.assertNotIn("compilation", ch, "...but the compilation flag is deleted at the same confidence")


if __name__ == "__main__":
    unittest.main()
