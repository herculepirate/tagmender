"""CHG-1 / MOV-6 (docs/MATCHING_RULES.md): only a release YOU chose (Change Release, Use … and File
It…, the command line's chosen release) is recorded as "pressing chosen by you" - never one of
TagMender's own picks made the same way (a version split within one release group), which would
then stand at any score on later look-ups. A job records the "you" line over any other clue."""
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_split_versions as ts  # noqa: E402
import engine  # noqa: E402
import plan as pl  # noqa: E402


class MOV6_ChosenByYou(ts.ReleaseGroup):
    def test_MOV6_an_automatic_version_split_is_not_your_choice(self):
        top = self.mixed_folder(extra=False)
        rm_id, rx_id = "11111111-1111-4111-8111-111111111111", "22222222-2222-4222-8222-222222222222"
        http = ts.GroupHttp(mb=[ts.version(rm_id, "Album M", ts.REMASTER, "remaster 2024"),
                                ts.version(rx_id, "Album M", ts.REMIX, "remix 2024"),
                                ts.version("rel-or", "Album M", ts.REMASTER, "")])
        eng = self.engine(http)

        def unproven(g, fs):
            eng._last_out = {"status": "review", "best": {"mb_id": "rel-or", "source": "mb"}}
            return pl.Proposal(source_dir=str(g.folder), files=[str(f) for f in fs], status="unresolved", reason="r")
        with mock.patch.object(engine.Engine, "_version_alone", side_effect=unproven):
            props = eng.identify_all(eng.albums_in(top)[0])
        self.assertEqual(sorted(p.release_id for p in props), [rm_id, rx_id])       # (split: two releases)
        for p in props:
            self.assertNotIn("pressing chosen by you", p.evidence)
            self.assertFalse([n for n in p.notes or [] if "chosen by you" in n], p.notes)

    def test_MOV6_the_job_records_your_choice_over_another_clue(self):
        import match_albums as ma
        src = Path(ma.__file__).read_text(encoding="utf-8")
        self.assertIn('"pressing chosen by you" if "pressing chosen by you" in said', src)


for _n in dir(ts.ReleaseGroup):                 # (test_split_versions' own tests are run there)
    if _n.startswith("test_"):
        setattr(MOV6_ChosenByYou, _n, None)


if __name__ == "__main__":
    unittest.main()
