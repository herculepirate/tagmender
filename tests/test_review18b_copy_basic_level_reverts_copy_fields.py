# Eighteenth independent review (B): this test failed before its fix.
# Eighteenth independent review (B).
"""plan.Planner._scripted now runs the tagger scripts on the release's FULL target (every field,
whatever the level writes) and then, for every field the level does not write, puts back
"the file's" value: new[k] = before[k]. But "before" is the SOURCE file's tags (the pre-pass
reads tagio.read(f) for the source), while for a copy profile whose copy exists already the
target is diffed against the COPY (the earlier-copy path). So at the Basic level every
release field the level leaves alone (label, catalogue number, Discogs IDs ...) is written
into the copy with the source's value: a copy's own edits of those fields are reverted on
every run, although Basic never writes them ("Lowering a level never removes anything:
fields a level does not write are left as they are"; "a copy's own edits stay the copy's").
Before this version only a field a script changed came back that way."""
import sys
import types
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _review18b_common  # noqa: E402,F401

import engine  # noqa: E402
import executor  # noqa: E402
import levels  # noqa: E402
import plan as pl  # noqa: E402
import tagio  # noqa: E402
from test_executor import Base  # noqa: E402  (the repo's own test helpers)


def _leveled(prof, prop):
    """As `tagmender run` leaves a proposal: Engine.apply_level (no measurements below Audiophile)."""
    engine.Engine.apply_level(types.SimpleNamespace(profile=prof, log=None, http=None), prop)
    return prop


class CopyBasicLevelRevertsCopyFields(Base):
    def test_copy_keeps_its_own_label_at_basic_level(self):
        tagio.write(self.f1, {"label": ["Source Label"]})
        tagio.write(self.f2, {"label": ["Source Label"]})
        prof = self.profile(operation="copy", destination=str(self.dest), tag_level="basic",
                            picard={"enable_tagger_scripts": True,
                                    "list_of_scripts": ["$set(_shelf,x)"]})   # (changes no field)
        p1 = pl.build([_leveled(prof, self.proposal())], prof)
        self.assertEqual(p1["albums"][0]["status"], "ready", p1["albums"][0]["conflicts"])
        executor.Executor(p1).run()
        copy1 = Path(p1["albums"][0]["files"][0]["dst"])
        self.assertTrue(copy1.is_file())
        tagio.write(copy1, {"label": ["My Own Label"]})          # the user's edit of the copy
        p2 = pl.build([_leveled(prof, self.proposal())], prof)
        fe = next(fe for fe in p2["albums"][0]["files"] if fe.get("copy_of") == str(self.f1))
        self.assertNotIn("label", fe["changes"],
                         f"Basic does not write label, yet the copy's label is to be changed: {fe['changes']}")


if __name__ == "__main__":
    unittest.main()
