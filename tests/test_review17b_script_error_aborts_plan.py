# Seventeenth independent review (B): this test failed before its fix.
# Seventeenth independent review (B).
"""plan.Planner._scripted -> engine.run_tagger_scripts catches only picard_script.ScriptError.
A tagger script that fails with another exception - $delprefix() without its argument is a
TypeError, $pad(x,99999999999,y) a MemoryError (naming.render already turns exactly these
into a TemplateError: "($delprefix(), $pad(x,1e12,y) ...)") - now raises out of
Planner.album, so plan.build raises and the WHOLE plan (every album of the run, the
library scan, the watcher's batch) is lost. Before this version the scripts ran in the
engine, where the same error made that one album unresolved and the rest of the run went on.
A script that fails only for some files ($if(%genre%,$delprefix())) is enough."""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _review17b_common  # noqa: E402,F401

import plan as pl  # noqa: E402
from test_executor import Base, target  # noqa: E402  (the repo's own test helpers)


class ScriptErrorAbortsPlan(Base):
    def test_one_failing_script_does_not_lose_the_whole_plan(self):
        prof = self.profile(picard={"enable_tagger_scripts": True,
                                    "list_of_scripts": ["$if($eq(%title%,Second),$set(x,$delprefix()))"]})
        try:
            p = pl.build([self.proposal(targets=[target(1, "First"), target(2, "Second")])], prof)
        except Exception as exc:                       # noqa: BLE001
            self.fail(f"the whole plan failed because a tagger script failed on one file: "
                      f"{type(exc).__name__}: {exc}")
        self.assertEqual(len(p["albums"]), 1)


if __name__ == "__main__":
    unittest.main()
