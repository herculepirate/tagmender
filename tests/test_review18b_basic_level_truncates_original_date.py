# Eighteenth independent review (B): this test failed before its fix.
# Eighteenth independent review (B).
"""At the Basic level (originaldate is not a Basic field) with tagger scripts on, the plan now
truncates an MP3's original release date to its year when the tag is saved as ID3v2.3
(Picard's "write_id3v23", or an ID3v2.3 file kept as such).

Cause: plan.Planner._scripted runs the scripts on the release's full target and then, for
each field the level does not write, puts the file's own value into the target
(new[k] = list(before[k])). The planner then passes the target through tagio.stored(),
which makes an ID3v2.3 original date a year ("1999-01-02" -> "1999"), so diff_tags sees
originaldate "changed" and the write gets originaldate in its changes: tagio.save_id3 is
told keep_original_date=False and converts TDOR to TORY (year only). Before this version
the field was not in the target at all, so the full TDOR was kept, as save_id3 means to
("an original date ... that is not being changed is kept as it is"). The same happens to
every multi-valued field Basic never writes (joined with "/"), shown as changes."""
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
from test_executor import Base, audio, target  # noqa: E402  (the repo's own test helpers)


class BasicLevelTruncatesOriginalDate(Base):
    def setUp(self):
        super().setUp()
        self.m1 = audio(self.album / "01 a.mp3", 440, album="Some Album", title="a", artist="Some Artist")
        self.m2 = audio(self.album / "02 b.mp3", 550, album="Some Album", title="b", artist="Some Artist")
        for f in (self.m1, self.m2):                       # (ffmpeg writes ID3v2.4: TDOR keeps the full date)
            tagio.write(f, {"originaldate": ["1999-01-02"]})
            assert tagio.read(f)["originaldate"] == ["1999-01-02"]

    def _prop(self, prof):
        prop = self.proposal(files=[str(self.m1), str(self.m2)],
                             targets=[target(1, "First", originaldate=["1999-01-02"]),
                                      target(2, "Second", originaldate=["1999-01-02"])])
        engine.Engine.apply_level(types.SimpleNamespace(profile=prof, log=None, http=None), prop)
        return prop

    def test_basic_level_leaves_original_date_alone(self):
        prof = self.profile(tag_level="basic",
                            picard={"write_id3v23": True, "enable_tagger_scripts": True,
                                    "list_of_scripts": ["$set(_shelf,x)"]})      # (changes no field)
        p = pl.build([self._prop(prof)], prof)
        a = p["albums"][0]
        self.assertEqual(a["status"], "ready", a["conflicts"])
        ch = a["files"][0]["changes"]
        self.assertNotIn("originaldate", ch, f"Basic does not write originaldate: {ch}")
        executor.Executor(p).run()
        self.assertEqual(tagio.read(self.m1).get("originaldate"), ["1999-01-02"],
                         "the original date lost its month and day at a level that never writes it")


if __name__ == "__main__":
    unittest.main()
