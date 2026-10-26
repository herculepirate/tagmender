# Nineteenth independent review (B): this test failed before its fix.
# Nineteenth independent review (B).
"""A tagger script that $delete-s media, with "edition in the Album tag" on: the plan takes the
edition for the Album tag from the file's OLD media while it removes that media.

plan.Planner.album works the release's edition out from the first disc with the target's
removals now (rel has no media once the script deletes it), but the per-file edition
(tag_ed = naming.edition({**before, **target, **rel_tags})) then has no "~edition_media"
and falls back to the file's own "media" - the value being deleted. Run 1 writes
"I [Vinyl]" and deletes media; run 2 (no media left) wants "I" again: the plan never settles."""
import sys
import types
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _review19b_common  # noqa: E402,F401

import engine  # noqa: E402
import executor  # noqa: E402
import plan as pl  # noqa: E402
import tagio  # noqa: E402
from test_executor import Base, target  # noqa: E402  (the repo's own test helpers)

REL = dict(media=['12" Vinyl'], date=["2019-01-01"], originaldate=["1971-05-01"])


class EditionFromDeletedMedia(Base):
    def _prop(self, prof):
        prop = self.proposal(targets=[target(1, "First", album="I", **REL), target(2, "Second", album="I", **REL)])
        engine.Engine.apply_level(types.SimpleNamespace(profile=prof, log=None, http=None), prop)
        return prop

    def test_plan_settles_when_a_script_deletes_media(self):
        for f in (self.f1, self.f2):                       # (tagged before: the media is there)
            tagio.write(f, {"media": ['12" Vinyl']})
        prof = self.profile(naming={"album_editions": "edition"},
                            picard={"enable_tagger_scripts": True, "list_of_scripts": ["$delete(media)"]})
        p1 = pl.build([self._prop(prof)], prof)
        a1 = p1["albums"][0]
        self.assertEqual(a1["status"], "ready", a1["conflicts"])
        fe1 = a1["files"][0]
        self.assertIn("media", fe1["changes"], "the script deletes media")
        self.assertIsNone(fe1["changes"]["media"])
        executor.Executor(p1).run()
        album1 = tagio.read(self.f1)["album"]
        self.assertNotIn("media", tagio.read(self.f1))
        p2 = pl.build([self._prop(prof)], prof)
        a2 = p2["albums"][0]
        self.assertEqual(a2["status"], "unchanged",
                         f"applied once, planned again: album {album1} -> "
                         f"{[fe['changes'].get('album') for fe in a2['files']]}")


if __name__ == "__main__":
    unittest.main()
