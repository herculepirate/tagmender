# DSC-2 (docs/MATCHING_RULES.md): a set's disc folders ("CD1" ... "CD4" in "Holy Diver [2022
# RM DLX 4CD]") whose discs each carry their own album title (the remix, the live show, the
# outtakes) are one release: before, each disc was an album of its own and was matched - and
# filed - apart from the others, one disc to another release's folder.
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_engine as te  # noqa: E402  (isolates XDG dirs and Picard.ini first)
from test_v0821_engine_rules import SetHttp, disc_folder, flac, two_cd_release  # noqa: E402
import engine  # noqa: E402
import match_albums as ma  # noqa: E402


class DSC2_DiscsWithTitlesOfTheirOwn(te.Base):
    def groups(self):
        return engine.Engine.albums_in(self.engine(te.FakeHttp()), self.lib)

    def test_DSC2_disc_folders_titled_apart_are_one_set(self):
        box = self.lib / "Band B" / "Album B [2CD]"
        disc_folder(box, "CD1", 1, "Album B [Remix]", "Band B")
        disc_folder(box, "CD2", 2, "Album B Live In Fresno", "Band B")
        self.assertEqual([(g.folder.name, len(g.files), g.others) for g in self.groups()], [("Album B [2CD]", 6, [])])

    def test_DSC2_the_set_is_matched_as_its_release(self):
        box = self.lib / "Band B" / "Album B [2CD]"
        disc_folder(box, "CD1", 1, "Album B [Remix]", "Band B")
        disc_folder(box, "CD2", 2, "Album B Live", "Band B")
        eng = self.engine(SetHttp(mb=[two_cd_release("mb-b", "Band B", "Album B")]))
        groups = eng.albums_in(self.lib)
        p = eng.identify(groups[0])
        self.assertEqual((p.status, p.tier, p.release_id), ("identified", "exact", "mb-b"), p.why)
        self.assertEqual([t["discnumber"][0] for t in p.targets], ["1"] * 3 + ["2"] * 3)

    def test_DSC2_discs_without_a_disc_number_stay_apart(self):
        box = self.lib / "Band B"
        for n, album in ((1, "Album F"), (2, "Album G")):
            for i in range(2):
                flac(box / f"CD{n}", f"{i + 1:02d}.flac", 10, title=f"T{i}", album=album, albumartist="Band B",
                     artist="Band B", track=str(i + 1))
        self.assertEqual(len(self.groups()), 2)

    def test_DSC2_disc_numbers_written_01_and_1_of_4(self):
        box = self.lib / "Band B" / "Album B [2CD]"
        disc_folder(box, "CD1", 1, "Album B [Remix]", "Band B", total=4)
        disc_folder(box, "CD2", 2, "Album B Live", "Band B")
        self.assertEqual(len(self.groups()), 1)

    def test_DSC2_an_untagged_disc_stays_apart(self):
        box = self.lib / "Band B" / "Album B [3CD]"
        disc_folder(box, "CD1", 1, "Album B [Remix]", "Band B")
        disc_folder(box, "CD2", 2, "Album B Live", "Band B")
        flac(box / "CD3", "01.flac", 10, title="X", track="1")
        self.assertGreater(len(self.groups()), 1)

    def test_DSC2_the_set_is_searched_by_the_words_its_discs_share(self):
        P = Path
        infos = [(P("/b/CD1/01.flac"), {"album": "Holy Diver [2022 Barresi RMX]"}),
                 (P("/b/CD2/01.flac"), {"album": "Holy Diver Live In Fresno CA 1983"}),
                 (P("/b/CD3/01.flac"), {"album": "Holy Diver Outtakes, Singles & B-Sides"})]
        self.assertEqual(ma._set_title(infos, "Holy Diver [2022 RM DLX 4CD]"), "Holy Diver")
        apart = [(P("/b/CD1/01.flac"), {"album": "The Wall"}), (P("/b/CD2/01.flac"), {"album": "The Final Cut"})]
        self.assertEqual(ma._set_title(apart, "Box"), "Box")                    # ("The" alone: the folder's)
        one = [(P("/b/CD1/01.flac"), {"album": "Album"}), (P("/b/CD2/01.flac"), {"album": "album"})]
        self.assertEqual(ma._set_title(one, "Box"), "")                         # (one title: the tags')
        self.assertEqual(ma._set_title(infos[:1], "Box"), "")

    def test_DSC2_two_artists_albums_stay_apart(self):
        box = self.lib / "Split"
        disc_folder(box, "CD1", 1, "Album B", "Band B")
        disc_folder(box, "CD2", 2, "Album C", "Band C")
        self.assertEqual(len(self.groups()), 2)

    def test_DSC2_two_folders_saying_one_disc_stay_apart(self):
        box = self.lib / "Band B" / "Two Albums"
        disc_folder(box, "CD1", 1, "Album B", "Band B")
        disc_folder(box, "CD2", 1, "Album D", "Band B")
        self.assertEqual(len(self.groups()), 2)

    def test_DSC2_an_album_with_tracks_of_its_own_beside_a_disc_folder_stays_apart(self):
        top = self.lib / "Band B" / "Album B"
        flac(top, "01.flac", 10, title="T0", album="Album B", albumartist="Band B", artist="Band B", track="1")
        disc_folder(top, "CD2", 2, "Album E", "Band B")
        self.assertEqual(len(self.groups()), 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
