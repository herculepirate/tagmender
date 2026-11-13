"""0.8.48 repairs: FLD-1 (a folder bracket that is part of the release's title is no pressing
evidence), and the other focused fixes of this release (added below as they land)."""
import os as _os, tempfile as _tempfile  # noqa: E402
_os.environ.setdefault("XDG_CONFIG_HOME", _tempfile.mkdtemp(prefix="tm-config-"))
_os.environ.setdefault("XDG_STATE_HOME", _tempfile.mkdtemp(prefix="tm-state-"))
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import match_core as mc  # noqa: E402
from test_v0823_web_rules import mc_album, mc_cand  # noqa: E402

LENS = (300.0, 400.0, 500.0)


def clues_of(folder, **cand):
    clues, against = {}, []
    mc.file_clues(mc_album(LENS, folder=folder), mc_cand(LENS, **cand), clues, against)
    return clues, against


class FLD1_TitleBracketIsNoCountry(unittest.TestCase):
    TITLE = "The Sessions Box Set (United Kingdom & Ireland 2022)"
    FOLDER = "2022 - The Sessions Box Set (United Kingdom & Ireland 2022)"

    def test_bracket_of_the_releases_own_title_says_no_country(self):
        _clues, against = clues_of(self.FOLDER, title=self.TITLE, country="Germany")
        self.assertFalse([a for a in against if a.startswith("folder name says")], against)

    def test_the_same_bracket_still_counts_when_the_title_lacks_it(self):
        _clues, against = clues_of(self.FOLDER, title="The Sessions Box Set", country="Germany")
        self.assertTrue([a for a in against if a.startswith("folder name says")], against)

    def test_a_pressing_bracket_still_counts(self):
        clues, against = clues_of("Album [Century Media, 9976158, Germany]", title="Album", country="Germany")
        self.assertIn("country", clues)
        _c, against = clues_of("Album [Century Media, 9976158, Germany]", title="Album", country="US")
        self.assertTrue([a for a in against if a.startswith("folder name says")], against)



class NAM9_InitialsOfACreditedName(unittest.TestCase):
    CREDIT = "Tania Maria & Niels-Henning Ørsted Pedersen"

    def test_initials_are_the_credited_name(self):
        for tag in ("Tania Maria & NHØP", "Tania Maria & NHOP"):
            self.assertEqual(mc.missing_collaborators(tag, [self.CREDIT]), [], tag)
            self.assertGreaterEqual(mc.artist_sim(tag, self.CREDIT), 0.95, tag)

    def test_no_unsupported_matches(self):
        # capitals that spell no credited name, a band name in capitals, an ordinary short name
        self.assertEqual(mc.missing_collaborators("Tania Maria & XYZ", [self.CREDIT]), ["XYZ"])
        self.assertLess(mc.artist_sim("Tania Maria & XYZ", self.CREDIT), 0.85)
        self.assertLess(mc.artist_sim("ABBA", "Agnetha Bjorn Benny Anni-Frid"), 0.85)
        self.assertEqual(mc.expand_initials("Can", "Can"), "Can")
        self.assertEqual(mc.expand_initials("JJ & Bob", "Jean Jacques Milteau; Bob Smith"), "JJ & Bob")

    def test_two_credited_names_with_the_same_initials_decide_nothing(self):
        self.assertEqual(mc.expand_initials("A & JS", "A; John Smith; Jane Stone"), "A & JS")


if __name__ == "__main__":
    unittest.main()
