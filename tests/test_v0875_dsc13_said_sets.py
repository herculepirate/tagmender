# DSC-13 (docs/MATCHING_RULES.md): a folder whose name says it is a set - a CD count ("14 CD",
# even one fewer than its disc folders: the box's own bonus disc) or box words ("Box Set",
# "BoxSet", "Complete Albums Collection") - holding disc folders numbered 1 to N, each tagged as
# that disc's own album with no disc tag, is one album: the box. Before, Billy Joel - The Complete
# Albums Collection (2011) 14 CD Box Set (15 folders "CD01 Cold Spring Harbor" .. "CD15 Collected
# Additional Masters") was 15 albums, each matched alone to a standalone reissue or another box.
# Shapes from the library: that box (no disc tags, one disc's album artist "Billy Joel (Richard
# Joo)"); John Mayer (BoxSet, 5CD, ...) (each disc tagged 01/01, one "John Mayer Trio");
# Broselmaschine - It Was 50 Years Ago Today [5CD] (artist credited four ways). Guards: 7CD_Arvo
# Part (discs II..X, numbers not 1..N), Joy Division - Complete Studio Recordings (a fan set: a
# Bonus folder deeper, several artists), an artist's folder of numbered albums, a standalone
# "1 of 1" release, a series' volumes in a folder that says no set.
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_engine as te  # noqa: E402  (isolates XDG dirs first)
from test_v0821_engine_rules import SetHttp, flac  # noqa: E402
from test_v0864_box_subfolders import box_release  # noqa: E402
import engine  # noqa: E402
import match_albums as ma  # noqa: E402


def disc(parent, name, n, album, artist, disc_tag="", **extra):
    """Disc n of a set: three tracks (10, 20, 30 s) titled "D<n>-<i>", tagged as album ALBUM."""
    for i in range(3):
        meta = dict(title=f"D{n}-{i}", album=album, albumartist=artist, artist=artist, track=str(i + 1), **extra)
        if disc_tag:
            meta["disc"] = disc_tag
        flac(parent / name, f"{i + 1:02d}.flac", 10 * (i + 1), freq=300 + 100 * n + 10 * i, **meta)
    return parent / name


def complete_albums(lib, name="Band J - The Complete Albums Collection (2011) 3 CD Box Set Deluxe  Edition FLAC",
                    folders=("CD01 Alpha", "CD02 Beta", "CD03 Gamma", "CD04 Collected Masters"),
                    artists=None, **extra):
    """Shape A (Billy Joel): disc folders "CD01 Title" .., one more than the name's count (the
    box's bonus disc), each tagged as its own album, no disc tags, date 2011."""
    top = lib / name
    for n, f in enumerate(folders, 1):
        who = (artists or {}).get(n, "Band J")
        disc(top, f, n, f.split(" ", 1)[1], who, date="2011", **extra)
    return top


def boxset_5cd(lib, artists=("Band M", "Band M", "Band M Trio")):
    """Shape B (John Mayer): "CD1 - 2001 - Band M - Alpha" .., each tagged 01/01, no IDs."""
    top = lib / "Band M" / "2013 - Band M - Band M (BoxSet, 3CD, EU, Sony, 88883708872)"
    for n, (year, title) in enumerate((("2001", "Alpha"), ("2003", "Beta"), ("2005", "Try")), 1):
        disc(top, f"CD{n} - {year} - {artists[n - 1]} - {title}", n, title, artists[n - 1], disc_tag="01/01",
             date=year)
    return top


def bracket_5cd(lib):
    """Shape C (Broselmaschine): "[3CD]", "CD1 - Title (1971)", the artist credited otherwise on
    each disc, every credit naming the folder's artist."""
    top = lib / "Band B - It Was 50 Years Ago Today (2018, MiG)[3CD]"
    disc(top, "CD1 - Band B (1971)", 1, "Band B", "Band B")
    disc(top, "CD2 - Peter X & die Band B (1976)", 2, "Peter X & die Band B", "Peter X & die Band B")
    disc(top, "CD3 -  Peter X's Band B - Graublau (1985)", 3, "Graublau", "Peter X's Band B")
    return top


class DSC13_SaidSets(te.Base):
    def groups(self, where=None):
        return engine.Engine.albums_in(self.engine(te.FakeHttp()), where or self.lib)

    def shape(self, where=None):
        return sorted((g.folder.name, len(g.files)) for g in self.groups(where))

    # ---- the shapes -----------------------------------------------------------------------------
    def test_DSC13_complete_albums_collection_box_is_one_album(self):
        top = complete_albums(self.lib, artists={2: "Band J (Pianist X)"})
        self.assertEqual(self.shape(), [(top.name, 12)])
        album = ma.read_album(top, "Band J", top.name)
        self.assertEqual([t["disc"] for t in album["tracks"]], [1] * 3 + [2] * 3 + [3] * 3 + [4] * 3)
        self.assertEqual([t["num"] for t in album["tracks"]], [1, 2, 3] * 4)

    def test_DSC13_boxset_with_discs_each_tagged_one_of_one_is_one_album(self):
        top = boxset_5cd(self.lib)
        self.assertEqual(self.shape(), [(top.name, 9)])
        album = ma.read_album(top, "Band M", top.name)
        self.assertEqual([t["disc"] for t in album["tracks"]], [1] * 3 + [2] * 3 + [3] * 3)

    def test_DSC13_box_of_its_artists_credits_is_one_album(self):
        top = bracket_5cd(self.lib)
        self.assertEqual(self.shape(), [(top.name, 9)])

    def test_DSC13_the_box_is_found_from_the_artists_folder_too(self):
        top = boxset_5cd(self.lib)
        self.assertEqual(self.shape(self.lib / "Band M"), [(top.name, 9)])

    def test_DSC13_the_box_is_matched_as_one_release_of_its_discs(self):
        top = complete_albums(self.lib)
        rel = box_release("mb-box", "Band J", "The Complete Albums Collection", 4, video=False)
        alone = box_release("mb-alpha", "Band J", "Alpha", 1, video=False)
        eng = self.engine(SetHttp(mb=[rel, alone]))
        [g] = eng.albums_in(self.lib)
        self.assertEqual(g.folder, top)
        p = eng.identify(g)
        self.assertEqual((p.status, p.release_id), ("identified", "mb-box"), p.why)
        self.assertEqual([t["discnumber"][0] for t in p.targets], [str(n) for n in range(1, 5) for _i in range(3)])
        self.assertEqual([t["tracknumber"][0] for t in p.targets], ["1", "2", "3"] * 4)
        self.assertEqual({t["totaldiscs"][0] for t in p.targets}, {"4"})

    def test_DSC13_the_name_says_a_set(self):
        said = engine._BOX_WORDS.search
        for name in ("Billy Joel - The Complete Albums Collection (2011)", "X (BoxSet, 5CD)", "X Box Set",
                     "VA - The classical chillout box - 2003", "Joy Division - Complete Studio Recordings",
                     "Band - The Complete Columbia Collection"):
            self.assertTrue(said(name), name)
        for name in ("Billy Joel. My Lives. Part 1", "Arvo Part - The Collection - 2021", "Drops (2001-2004)",
                     "Madlib - Madlib Medicine Show-The Brick [2012]", "Boxer - Alpha"):
            self.assertFalse(said(name), name)

    # ---- the guards -----------------------------------------------------------------------------
    def test_DSC13_disc_numbers_not_one_to_n_stay_apart(self):
        # (7CD_Arvo Part: CD-02, CD-03, CD-06 .. - discs of a series, some missing)
        top = self.lib / "3CD_Band P (Radio)"
        for n in (2, 3, 6):
            disc(top, f"CD-{n:02d}", n, f"Band P {'I' * n}", "Band P")
        self.assertEqual(len(self.groups()), 3)

    def test_DSC13_a_fan_set_with_a_deeper_bonus_folder_stays_apart(self):
        # (Joy Division - Complete Studio Recordings (MRB-001 v1): sessions, then "Bonus/<date> - ...")
        top = self.lib / "Band D - Complete Studio Recordings (MRB-001 v1)"
        disc(top, "01 - Demo session (1977)", 1, "Demo session", "Band D")
        disc(top, "02 - First session (1978)", 2, "First session", "Band D")
        disc(top / "Bonus", "1979-09-15 - TV Studios", 3, "TV Studios", "Band D")
        self.assertEqual(len(self.groups()), 3)

    def test_DSC13_two_artists_stay_apart(self):
        top = self.lib / "Band D - Complete Studio Recordings"
        disc(top, "01 - Demo session", 1, "Demo session", "Warsaw")
        disc(top, "02 - First session", 2, "First session", "Band D")
        self.assertEqual(len(self.groups()), 2)

    def test_DSC13_various_artists_only_in_a_folder_named_box_or_compilation(self):
        top = self.lib / "Hits (2CD)"
        disc(top, "CD1 - Summer", 1, "Summer Hits", "Various Artists")
        disc(top, "CD2 - Winter", 2, "Winter Hits", "Various Artists")
        self.assertEqual(len(self.groups()), 2)
        shutil.rmtree(top)
        top = self.lib / "Hits Box (2CD)"
        disc(top, "CD1 - Summer", 1, "Summer Hits", "Various Artists")
        disc(top, "CD2 - Winter", 2, "Winter Hits", "Various Artists")
        self.assertEqual(self.shape(), [(top.name, 6)])

    def test_DSC13_a_count_the_folders_do_not_fit_stays_apart(self):
        complete_albums(self.lib, name="Band J - Albums (6CD)")      # (4 folders: neither 6 nor 7)
        self.assertEqual(len(self.groups()), 4)

    def test_DSC13_a_standalone_release_one_of_one_with_its_id_stays_apart(self):
        top = boxset_5cd(self.lib)
        flac(top / "CD2 - 2003 - Band M - Beta", "04.flac", 10, title="D2-3", album="Beta", albumartist="Band M",
             artist="Band M", track="4", disc="1/1", barcode="0123456789012")
        self.assertGreater(len(self.groups()), 1)

    def test_DSC13_a_disc_tag_against_the_folders_number_stays_apart(self):
        top = self.lib / "Band J - Box Set (3CD)"
        disc(top, "CD1 Alpha", 1, "Alpha", "Band J")
        disc(top, "CD2 Beta", 2, "Beta", "Band J", disc_tag="3")
        disc(top, "CD3 Gamma", 3, "Gamma", "Band J")
        self.assertEqual(len(self.groups()), 3)

    def test_DSC13_an_artists_folder_of_numbered_albums_stays_apart(self):
        # ("Band N - Complete Albums" is an artist's folder by its words: "01. Alpha" .. are its albums)
        top = self.lib / "Band N - Complete Albums"
        for n, t in enumerate(("Alpha", "Beta", "Gamma"), 1):
            disc(top, f"0{n}. {t}", n, t, "Band N")
        self.assertEqual(len(self.groups()), 3)

    def test_DSC13_a_folder_with_tracks_of_its_own_stays_apart(self):
        top = boxset_5cd(self.lib)
        flac(top, "00.flac", 10, title="Intro", album="Intro", albumartist="Band M", artist="Band M", track="1")
        self.assertGreater(len(self.groups()), 1)

    def test_DSC13_DSC7_a_series_volumes_in_a_folder_saying_no_set_stay_apart(self):
        # ("Drops (2001-2004)/Disc 1" tagged "Drops 1", "/Disc 2" "Drops 2": DSC-7's guard stands)
        top = self.lib / "Band S" / "Drops (2001-2004)"
        disc(top, "Disc 1", 1, "Drops 1", "Band S")
        disc(top, "Disc 2", 2, "Drops 2", "Band S")
        self.assertEqual(len(self.groups()), 2)

    def test_DSC13_an_excluded_disc_keeps_the_box_apart(self):
        # (the box is never one album with a disc you left out: each disc stays its own album)
        top = complete_albums(self.lib)
        eng = self.engine(te.FakeHttp(), exclude_paths=[str(top / "CD02 Beta") + "*"])
        gs = eng.albums_in(self.lib)
        self.assertTrue(gs)
        self.assertTrue(all(len({Path(f).parent for f in g.files}) == 1 for g in gs),
                        [(g.folder.name, len(g.files)) for g in gs])


if __name__ == "__main__":
    import unittest
    unittest.main(verbosity=2)
