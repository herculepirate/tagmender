# Eighteenth independent review (B): this test failed before its fix.
# Eighteenth independent review (B).
"""Planner.album runs the tagger scripts on every kept file before it decides what the profile
does with the album, so a script that fails marks the album "blocked" even when the plan
would never write it: a copy profile skips an album below organize_min_confidence ("not
copied"; status "skipped"), and the scripts' result is never used. As "blocked" the album is
counted in the app's "Needs attention", `tagmender run` exits with EXIT_PARTIAL, and the
watcher retries it (RETRIED includes "blocked": identified on the web again, up to
retry.max_attempts) - for an album nothing would be done to."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _review18b_common  # noqa: E402,F401

import plan as pl  # noqa: E402
from test_executor import Base, target  # noqa: E402  (the repo's own test helpers)


class ScriptErrorBlocksSkippedCopy(Base):
    def test_low_confidence_copy_is_skipped_not_blocked(self):
        prof = self.profile(operation="copy", destination=str(self.dest),
                            picard={"enable_tagger_scripts": True,
                                    "list_of_scripts": ["$if($eq(%title%,Second),$set(x,$delprefix()))"]})
        p = pl.build([self.proposal(conf=80.0, targets=[target(1, "First"), target(2, "Second")])], prof)
        a = p["albums"][0]
        self.assertEqual(a["status"], "skipped", a["conflicts"] or a["notes"])


if __name__ == "__main__":
    unittest.main()
