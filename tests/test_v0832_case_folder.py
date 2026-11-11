# NAM-5 (docs/MATCHING_RULES.md): on a share that sees one name in two letter cases (SMB/CIFS),
# the naming script's "1997 - The Very Best of 10cc" finds the folder "1997 - The Very Best Of
# 10cc" already there. The plan uses the folder as it is spelled on disk - before, every file
# was "taken" (CIFS gives the other spelling another inode) and got a " ~1a2b" name beside
# itself. The share is simulated: Path.is_dir folds letter case here.
import os as _os, tempfile as _tempfile  # noqa: E401
_os.environ["XDG_CONFIG_HOME"] = _tempfile.mkdtemp(prefix="tm-config-")
_os.environ["XDG_STATE_HOME"] = _tempfile.mkdtemp(prefix="tm-state-")
_os.environ.setdefault("TAGMENDER_TAGGER_INI", "/nonexistent")

import os  # noqa: E402
import sys  # noqa: E402
import tempfile  # noqa: E402
import unittest  # noqa: E402
from pathlib import Path  # noqa: E402
from unittest import mock  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import plan as pl  # noqa: E402
import profiles as pf  # noqa: E402

_real_is_dir = Path.is_dir


def folded_is_dir(self):
    """is_dir as a case-insensitive share answers it."""
    if _real_is_dir(self):
        return True
    parent = self.parent
    if not _real_is_dir(parent):
        return False
    want = self.name.casefold()
    return any(n.casefold() == want and _real_is_dir(parent / n) for n in os.listdir(parent))


class NAM5(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="tm-nam5-"))
        (self.root / "10cc" / "1997 - The Very Best Of 10cc").mkdir(parents=True)
        self.planner = pl.Planner(pf.default())

    def test_NAM5_a_folder_differing_only_in_case_is_used_as_spelled_on_disk(self):
        want = self.root / "10cc" / "1997 - The Very Best of 10cc" / "01 Song.flac"
        with mock.patch.object(Path, "is_dir", folded_is_dir):
            got = self.planner._spelled(want, self.root)
        self.assertEqual(got, self.root / "10cc" / "1997 - The Very Best Of 10cc" / "01 Song.flac")

    def test_NAM5_a_case_sensitive_disk_spells_a_new_folder_as_the_script_does(self):
        prof = pf.default()
        prof["filenames"]["compat"] = "linux"                         # (Windows names fold case anyway)
        self.planner = pl.Planner(prof)
        want = self.root / "10cc" / "1997 - The Very Best of 10cc" / "01 Song.flac"
        got = self.planner._spelled(want, self.root)                 # (no folding: a second folder)
        self.assertEqual(got.parent.name, "1997 - The Very Best of 10cc")
        same = self.root / "10cc" / "1997 - The Very Best Of 10cc" / "01 Song.flac"
        self.assertEqual(self.planner._spelled(same, self.root), same)


class NAM6(unittest.TestCase):
    """NAM-6: MusicBrainz writes "King’s X", Discogs "King's X": one artist folder, as it is
    spelled on disk."""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="tm-nam6-"))
        (self.root / "King's X" / "1988 - Out of the Silent Planet").mkdir(parents=True)

    def test_NAM6_a_typographic_apostrophe_finds_the_plain_folder(self):
        for compat in ("linux", "windows"):
            prof = pf.default()
            prof["filenames"]["compat"] = compat
            got = pl.Planner(prof)._spelled(self.root / "King’s X" / "1990 - Faith Hope Love" / "01.flac", self.root)
            self.assertEqual(got.parent.parent.name, "King's X", compat)
            self.assertEqual(got.parent.name, "1990 - Faith Hope Love")

    def test_NAM6_one_plan_keeps_its_first_spelling(self):
        planner = pl.Planner(pf.default())
        first = planner._spelled(self.root / "Guns N’ Roses" / "A" / "01.flac", self.root)
        second = planner._spelled(self.root / "Guns N' Roses" / "B" / "01.flac", self.root)
        self.assertEqual(first.parent.parent.name, second.parent.parent.name)

    def test_NAM6_other_names_stay_apart(self):
        got = pl.Planner(pf.default())._spelled(self.root / "Kings X" / "A" / "01.flac", self.root)
        self.assertEqual(got.parent.parent.name, "Kings X")


class NAM7(unittest.TestCase):
    """NAM-7: "Blood, Sweat & Tears" (MusicBrainz), "Blood, Sweat And Tears" (Discogs) and
    "Blood Sweat & Tears" (a sleeve's credit) are one artist folder: the one holding the most."""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="tm-nam7-"))
        for n in ("1968 - A", "1969 - B", "1970 - C"):
            (self.root / "Blood, Sweat & Tears" / n).mkdir(parents=True)
        (self.root / "Blood, Sweat And Tears" / "2013 - Rare").mkdir(parents=True)
        self.planner = pl.Planner(pf.default())

    def spelled(self, *parts):
        return self.planner._spelled(self.root.joinpath(*parts), self.root, artist_level=True)

    def test_NAM7_every_spelling_goes_to_the_folder_holding_most(self):
        for name in ("Blood, Sweat And Tears", "Blood Sweat & Tears", "Blood, Sweat + Tears", "Blood, Sweat & Tears"):
            got = self.spelled(name, "2013 - Rare", "01.flac")
            self.assertEqual(got.parent.parent.name, "Blood, Sweat & Tears", name)

    def test_NAM7_other_names_and_album_folders_stay_as_they_are(self):
        self.assertEqual(self.spelled("Blood Sweat Tears", "A", "01.flac").parent.parent.name, "Blood Sweat Tears")
        self.assertEqual(self.spelled("Andy", "A", "01.flac").parent.parent.name, "Andy")
        (self.root / "Blood, Sweat & Tears" / "3 and 4").mkdir()
        got = self.spelled("Blood, Sweat & Tears", "3 & 4", "01.flac")
        self.assertEqual(got.parent.name, "3 & 4")                              # (album folders: not folded)
        got = self.planner._spelled(self.root / "Blood Sweat And Tears - A" / "01.flac", self.root)
        self.assertEqual(got.parent.name, "Blood Sweat And Tears - A")          # (no artist level: not folded)
        got = self.planner._spelled(self.root / "Blood, Sweat And Tears" / "A" / "01.flac", self.root)
        self.assertEqual(got.parent.parent.name, "Blood, Sweat And Tears")      # (not called as the artist's)

    def test_NAM7_letter_case_and_full_stops(self):
        prof = pf.default()
        prof["filenames"]["compat"] = "linux"                         # (Windows names fold case anyway)
        self.planner = pl.Planner(prof)
        (self.root / "Air" / "A").mkdir(parents=True)
        (self.root / "R.E.M" / "A").mkdir(parents=True)
        self.assertEqual(self.spelled("AIR", "B", "01.flac").parent.parent.name, "AIR")   # (two acts on Linux)
        self.assertEqual(self.spelled("REM", "B", "01.flac").parent.parent.name, "R.E.M")

    def test_NAM7_a_new_artist_keeps_the_scripts_spelling_for_the_whole_plan(self):
        a = self.spelled("Simon And Garfunkel", "A", "01.flac")
        b = self.spelled("Simon & Garfunkel", "B", "01.flac")
        self.assertEqual((a.parent.parent.name, b.parent.parent.name), ("Simon And Garfunkel",) * 2)

class MOV2(unittest.TestCase):
    """MOV-2: a second copy of one of the album's tracks ("005.TUILMOMF.flac" beside "005.TU.flac",
    Allan Holdsworth - Metal Fatigue) is no other album's music: the folder's scans, log and
    cover go with the album; the copy is left where it is."""

    def setUp(self):
        self.src = Path(tempfile.mkdtemp(prefix="tm-mov2-")) / "1985 - Metal Fatigue"
        (self.src / "Scans").mkdir(parents=True)
        for n in ("001.MF.flac", "005.TU.flac", "005.TUILMOMF.flac", "album.log", "cover.jpg", "Scans/back.jpg"):
            (self.src / n).write_bytes(b"x")
        self.dst = self.src.parent / "1985 - Metal Fatigue [1995 Reissue]"
        self.moved = {str(self.src / n): str(self.dst / n.lstrip("0")) for n in ("001.MF.flac", "005.TU.flac")}

    def facts(self, table):
        return mock.patch.object(pl, "_audio_facts", lambda f: table.get(Path(f).name))

    def test_MOV2_a_second_copy_of_a_track_is_its_audio_again(self):
        group = [str(self.src / n) for n in ("001.MF.flac", "005.TU.flac", "005.TUILMOMF.flac")]
        same = {"001.MF.flac": (300.0, "aa", "metal fatigue"), "005.TU.flac": (850.53, "bb", "the un-merry-go-round"),
                "005.TUILMOMF.flac": (850.53, "bb", "the un-merry-go-round")}
        with self.facts(same):
            self.assertEqual(pl.second_copies(group, self.moved, self.src), [self.src / "005.TUILMOMF.flac"])
        with self.facts(dict(same, **{"005.TUILMOMF.flac": (851.0, "bb", "x")})):
            self.assertEqual(pl.second_copies(group, self.moved, self.src), [])   # (another length)
        with self.facts(dict(same, **{"005.TUILMOMF.flac": (850.53, "cc", "the un-merry-go-round")})):
            self.assertEqual(pl.second_copies(group, self.moved, self.src), [])   # (other audio, one length)
        no_md5 = {k: (v[0], "", v[2]) for k, v in same.items()}
        with self.facts(dict(no_md5, **{"005.TUILMOMF.flac": (850.53, "", "bonus")})):
            self.assertEqual(pl.second_copies(group, self.moved, self.src),       # (names: one begins the other)
                             [self.src / "005.TUILMOMF.flac"])
        bonus = self.src / "Bonus Song.flac"
        bonus.write_bytes(b"x")
        with self.facts(dict(no_md5, **{"Bonus Song.flac": (850.53, "", "bonus song")})):
            self.assertEqual(pl.second_copies([str(bonus)] + group[:2], self.moved, self.src), [])

    def test_MOV2_the_copys_own_files_stay_with_it(self):
        (self.src / "005.TUILMOMF.lrc").write_bytes(b"x")
        comps, _why = pl._companions(self.src, self.moved, pf.default(), everything=True,
                                     copies=[self.src / "005.TUILMOMF.flac"])
        self.assertNotIn("005.TUILMOMF.lrc", {c.name for c, _d in comps})

    def test_MOV2_the_folder_goes_with_the_album_the_copy_stays(self):
        prof = pf.default()
        comps, why = pl._companions(self.src, self.moved, prof, everything=True)
        self.assertEqual(why, pl.SHARED_FOLDER_NOTE)                           # (before: the folder stays)
        comps, why = pl._companions(self.src, self.moved, prof, everything=True,
                                    copies=[self.src / "005.TUILMOMF.flac"])
        self.assertFalse(why)
        gone = {c.name for c, _d in comps}
        self.assertTrue({"album.log", "cover.jpg", "back.jpg"} <= gone, gone)
        self.assertNotIn("005.TUILMOMF.flac", gone)


if __name__ == "__main__":
    unittest.main(verbosity=2)
