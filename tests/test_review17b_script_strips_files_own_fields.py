# Seventeenth independent review (B): this test failed before its fix.
# Seventeenth independent review (B).
"""engine.run_tagger_scripts strips the whitespace of EVERY value after each script (as
Picard's album loading does for the release's track metadata, picard/album.py
_finalize_loading_album). Now that the scripts run on the file's own tags too
(plan.Planner._scripted), a value only the file has ("calm " in MOOD) comes back as "calm",
and _scripted keeps it as "what a script changed" (tags.get(k) != v): the plan rewrites the
user's field although no script touches it. Picard runs the scripts on a file in
Track.update_file_metadata with run_scripts(metadata) - strip_whitespace=False - so the
file's own fields stay exactly as they are. Any enabled tagger script, even $set(_x,1),
triggers it; in a copy profile the source's stripped value also overwrites the copy's own
edit of that field."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _review17b_common  # noqa: E402,F401

import plan as pl  # noqa: E402
import tagio  # noqa: E402
from test_executor import Base, target  # noqa: E402  (the repo's own test helpers)


class ScriptStripsFilesOwnFields(Base):
    def test_unrelated_script_leaves_the_files_own_field_alone(self):
        tagio.write(self.f1, {"mood": ["calm "]})
        self.assertEqual(tagio.read(self.f1).get("mood"), ["calm "])
        prof = self.profile(picard={"enable_tagger_scripts": True, "list_of_scripts": ["$set(_x,1)"]})
        p = pl.build([self.proposal()], prof)
        fe = next(fe for fe in p["albums"][0]["files"] if fe["src"] == str(self.f1))
        self.assertNotIn("mood", fe["changes"], [r for r in fe["diff"] if r["field"] == "mood"])


if __name__ == "__main__":
    unittest.main()
