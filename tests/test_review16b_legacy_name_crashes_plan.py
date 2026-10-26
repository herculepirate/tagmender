# Sixteenth independent review (B): this test failed before its fix.
# Sixteenth independent review (B).
"""plan.py, copy profile: naming.render now raises TemplateError for a naming script that fails
at run time (TypeError, MemoryError ...), and the planner turns that into a conflict for the
album - but only around the first render. The second render, of the "legacy" name
(_legacy_names(merged): an album artist like "Simon & Garfunkel" with no ALBUMARTISTS tag), is
not guarded, and it renders WITHOUT Picard's hidden variables (they are only in `names`). A
script that reads a hidden variable - here %_totalalbumtracks%, which every MusicBrainz match
carries - and fails without it, passes the first render and raises from the second: the
exception leaves Planner.build (no per-album guard) and the whole plan - every album of the
run - fails, instead of one album becoming a conflict."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _review16b_common  # noqa: E402,F401

import plan as pl  # noqa: E402
from test_executor import Base, target  # noqa: E402  (the repo's own test helpers)

TEMPLATE = "%albumartist%/$if(%_totalalbumtracks%,%album%,$delprefix())/$num(%tracknumber%,2) %title%"


class LegacyNameCrash(Base):
    def test_plan_survives_a_script_failing_without_hidden_variables(self):
        prof = self.profile(operation="copy", destination=str(self.dest),
                            naming={"preset": "tagmender", "template": TEMPLATE})
        tg = [dict(target(i, t, artist="Simon & Garfunkel"), **{"~totalalbumtracks": ["2"]})
              for i, t in ((1, "First"), (2, "Second"))]
        try:
            p = pl.build([self.proposal(targets=tg)], prof)
        except Exception as exc:                      # noqa: BLE001
            self.fail(f"the whole plan failed: {type(exc).__name__}: {exc}")
        a = p["albums"][0]
        # the name Picard gives it (the hidden variable is there): no conflict at all
        self.assertEqual(a["status"], "ready", a["conflicts"])
        self.assertEqual(Path(a["files"][0]["dst"]).relative_to(self.dest).as_posix(),
                         "Simon & Garfunkel/Real Album/01 First.flac")


if __name__ == "__main__":
    unittest.main()
