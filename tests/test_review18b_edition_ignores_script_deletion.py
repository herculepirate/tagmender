# Eighteenth independent review (B): this test failed before its fix.
# Eighteenth independent review (B).
"""Planner.album now works the edition out from the scripted targets ("decide the album's
edition"), but it only applies what a script SETS: the first disc's tags are computed as
diff_tags(b0, scripted_target, p, conf, []) - with no removals - so a field the tagger script
$delete-s (it is in the target's "~remove") keeps the file's old value in the edition.

A script deleting originaldate on a reissue the file already holds as "1971": the first plan
names the edition "2019 Vinyl" from the file's old original date while it removes that date;
the next plan, with the date gone, makes it "Vinyl" - the Album tag (and an edition folder)
changes again on the second run, so the plan does not settle after it is applied."""
import sys
import types
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _review18b_common  # noqa: E402,F401

import engine  # noqa: E402
import executor  # noqa: E402
import plan as pl  # noqa: E402
import tagio  # noqa: E402
from test_executor import Base, target  # noqa: E402  (the repo's own test helpers)

REL = dict(media=['12" Vinyl'], date=["2019-01-01"], originaldate=["1971-05-01"])


class EditionIgnoresScriptDeletion(Base):
    def _prop(self, prof):
        prop = self.proposal(targets=[target(1, "First", album="I", **REL), target(2, "Second", album="I", **REL)])
        engine.Engine.apply_level(types.SimpleNamespace(profile=prof, log=None, http=None), prop)
        return prop

    def test_plan_settles_when_a_script_deletes_the_original_date(self):
        for f in (self.f1, self.f2):                       # (tagged before: the original date is there)
            tagio.write(f, {"originaldate": ["1971-05-01"], "media": ['12" Vinyl']})
        prof = self.profile(naming={"album_editions": "edition_year"},
                            picard={"enable_tagger_scripts": True, "list_of_scripts": ["$delete(originaldate)"]})
        p1 = pl.build([self._prop(prof)], prof)
        fe1 = p1["albums"][0]["files"][0]
        self.assertIsNone(fe1["changes"].get("originaldate", "kept"), "the script deletes originaldate")
        executor.Executor(p1).run()
        album1 = tagio.read(self.f1)["album"]
        self.assertNotIn("originaldate", tagio.read(self.f1))
        p2 = pl.build([self._prop(prof)], prof)
        a2 = p2["albums"][0]
        self.assertEqual(a2["status"], "unchanged",
                         f"applied once, planned again: album {album1} -> "
                         f"{[fe['changes'].get('album') for fe in a2['files']]}")


if __name__ == "__main__":
    unittest.main()
