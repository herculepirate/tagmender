# Sixteenth independent review (B): this test failed before its fix.
# Sixteenth independent review (B).
"""gui.Library.choose_release stores {"ref": link, "files": engine.plan_entry_files(row)}, and
engine.forced_for uses a choice only for the album sharing the most files with it (at least
one). plan_entry_files reads the files from the plan entry's "files" and "skipped" lists - but
the planner leaves both empty for an album it stops early: a copy profile's match below
organize_min_confidence ("Skipped", "not copied"), an album a rule excludes on its identified
metadata ("excluded": the files are only in "excluded_files"), an album blocked before its
files are listed. Those are the very albums the user picks "Use another release..." for (a
doubtful match; a wrong release that a rule then ignored). The choice names no file, matches
no album and is dropped without a word: the next preview identifies the album as before."""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _review16b_common  # noqa: E402,F401

import engine  # noqa: E402
import exclusions as ex  # noqa: E402
import plan as pl  # noqa: E402
from test_executor import Base  # noqa: E402  (the repo's own test helpers)

REF = "https://musicbrainz.org/release/0a8e97fd-4f6a-4bd7-8e4b-6d3a0b2ad2f1"


class ChooseReleaseForStoppedAlbum(Base):
    def chosen(self, prof, prop):
        a = pl.build([prop], prof)["albums"][0]
        e = engine.Engine(prof)
        # what gui.py choose_release() stores for this row
        e.force = {os.path.realpath(a["source_dir"]): [{"ref": REF, "files": engine.plan_entry_files(a)}]}
        g = engine.Group(folder=self.album, files=[self.f1, self.f2])
        return a, e.forced_for(g, alone=True)

    def test_copy_below_organize_confidence(self):
        prof = self.profile(operation="copy", destination=str(self.dest))      # organize_min_confidence 95
        a, ref = self.chosen(prof, self.proposal(conf=90.0))
        self.assertEqual(a["status"], "skipped", a)            # "confidence 90.0 is below ...: not copied"
        self.assertEqual(ref, REF, "the release chosen for the skipped album is dropped")

    def test_album_a_rule_ignored_on_its_identified_release(self):
        ex._write(ex.rules_file(), {"rules": [{"id": "x", "name": "no Real Album", "action": "ignore",
                                               "target": "album", "when": {"all": [
                                                   {"field": "album", "op": "contains", "value": "Real Album"}]}}]})
        a, ref = self.chosen(self.profile(), self.proposal())
        self.assertEqual(a["status"], "excluded", a)
        self.assertEqual(ref, REF, "the release chosen for the ignored album is dropped")


if __name__ == "__main__":
    unittest.main()
