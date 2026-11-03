# Seventeenth independent review (B): this test failed before its fix.
# Seventeenth independent review (B).
"""plan.Planner._scripted builds the scripts' metadata as {**file's tags, **release's tags}
and keeps the release's deletions ("~remove": compilation on a release not by Various
Artists, mb_tags) aside. Picard (Track.update_file_metadata, clear_existing_tags off):
metadata = Metadata(file.metadata); metadata.update(self.orig_metadata) - and
Metadata.update applies the other's deleted_tags (picard/metadata.py
_update_from_metadata: "for tag in other.deleted_tags: self._del(tag)"); album.py
_load_tracks did `del mm['compilation']`. So in Picard a script sees no %compilation% on such
a release; TagMender's script still sees the file's stale COMPILATION=1, and
$if(%compilation%,$set(grouping,Compilation)) writes GROUPING=Compilation while the same plan
removes COMPILATION - tags Picard would not write."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _review17b_common  # noqa: E402,F401

import plan as pl  # noqa: E402
import tagio  # noqa: E402
from test_executor import Base, target  # noqa: E402  (the repo's own test helpers)


class ScriptSeesDeletedCompilation(Base):
    def test_release_deletion_hidden_from_scripts(self):
        for f in (self.f1, self.f2):
            tagio.write(f, {"compilation": ["1"]})
        prof = self.profile(picard={"enable_tagger_scripts": True,
                                    "list_of_scripts": ["$if(%compilation%,$set(grouping,Compilation))"]})
        tg = [dict(target(1, "First"), **{"~remove": ["compilation"]}),
              dict(target(2, "Second"), **{"~remove": ["compilation"]})]
        p = pl.build([self.proposal(targets=tg)], prof)
        fe = next(fe for fe in p["albums"][0]["files"] if fe["src"] == str(self.f1))
        self.assertIsNone(fe["changes"].get("compilation", "kept"), "the release's deletion still applies")
        self.assertNotIn("grouping", fe["changes"],
                         "the script saw the COMPILATION the release deletes (Picard's does not)")


if __name__ == "__main__":
    unittest.main()
