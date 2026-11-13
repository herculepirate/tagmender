"""CAT-2: a catalogue number in three groups ("[A&M_75021 5350 2]", A&M / PolyGram / Universal
US) is read from a folder name - Temple Of The Dog's A&M CD was left album only, its number
unread. CTY-1: a release country that is a rip group's mark ("PMEDIA" as country, label and
comment) goes when the album is tagged and its release names no country."""
import os as _os, tempfile as _tempfile  # noqa: E402
_os.environ.setdefault("XDG_CONFIG_HOME", _tempfile.mkdtemp(prefix="tm-config-"))
_os.environ.setdefault("XDG_STATE_HOME", _tempfile.mkdtemp(prefix="tm-state-"))
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import match_core as mc  # noqa: E402
import plan as pl  # noqa: E402
import tagio  # noqa: E402
from test_executor import Base, target  # noqa: E402


class CAT2_ThreeGroupCatalogueNumber(unittest.TestCase):
    def test_three_groups_read(self):
        for text, want in (("1991 - Temple Of The Dog [A&M_75021 5350 2]", "75021 5350 2"),
                           ("Album [A&M 75021-5350-2]", "75021 5350 2"),
                           ("Album [31454 0123-2]", "31454 0123 2")):
            self.assertIn(want, mc.catno_hints(text), text)

    def test_other_numbers_unchanged(self):
        self.assertEqual(mc.catno_hints("[Kitaro 20bit RM DOMO 71063-2]"), ["DOMO 71063-2"])
        self.assertEqual(mc.catno_hints("[479 7050]"), ["479 7050"])
        self.assertEqual(mc.catno_hints("[24-96 2005 Remaster]"), [])
        self.assertEqual(mc.catno_hints("Live 1996 [2CD]"), [])


class CTY1_RipGroupMarkIsNoCountry(Base):
    def setUp(self):
        super().setUp()
        for f in (self.f1, self.f2):
            tagio.write(f, {"releasecountry": ["PMEDIA"], "comment": ["PMEDIA"]})

    def country_change(self, **kw):
        fe = pl.build([self.proposal(**kw)], self.profile())["albums"][0]["files"][0]
        return fe["changes"].get("releasecountry", "unchanged")

    def test_the_mark_goes_when_the_release_names_no_country(self):
        self.assertIsNone(self.country_change())

    def test_a_release_country_replaces_it(self):
        self.assertEqual(self.country_change(targets=[target(1, "First", releasecountry=["XW"]),
                                                      target(2, "Second", releasecountry=["XW"])]), ["XW"])

    def test_a_real_country_name_stays(self):
        for f in (self.f1, self.f2):
            tagio.write(f, {"releasecountry": ["Bulgaria"], "comment": ["ripped by me"]})
        self.assertEqual(self.country_change(), "unchanged")

    def test_a_word_the_files_carry_nowhere_else_stays(self):
        for f in (self.f1, self.f2):
            tagio.write(f, {"releasecountry": ["PMEDIA"], "comment": [""]})
        self.assertEqual(self.country_change(), "unchanged")

    def test_kept_by_your_policy(self):
        prof = self.profile(field_policy={"default": "replace", "fields": {"releasecountry": "preserve"}})
        fe = pl.build([self.proposal()], prof)["albums"][0]["files"][0]
        self.assertNotIn("releasecountry", fe["changes"])


if __name__ == "__main__":
    unittest.main()
