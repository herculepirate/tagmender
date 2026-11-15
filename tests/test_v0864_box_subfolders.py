# DSC-5 / BOX-1 (docs/MATCHING_RULES.md): a box set's folder naming its CD count ("Complete
# Box 13CD+DVD", "(2011, 7CD+DVD, Label, catalogue number)"), whose discs sit in folders named as
# albums ("01. (1980) - Alpha (2012) - KZ 1"), some holding two discs in disc folders
# ("CD3-4. 2005. Gamma (2CD, ...)/CD1" and "/CD2"), each disc tagged with its own number of the
# box, is one album: the box, disc N of N. Before, each sub-folder was an album of its own - its
# two-disc folders matched to the standalone 2CD release, its one-disc folders "track count
# differs" against the box or matched to the standalone album - and the box folder was planned
# with the discs it held directly only, the others "missing".
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_engine as te  # noqa: E402  (isolates XDG dirs first)
from test_v0821_engine_rules import SetHttp, flac  # noqa: E402
import engine  # noqa: E402
import match_albums as ma  # noqa: E402


def disc(parent, name, n, album, artist="Band R", total=None):
    """Disc n of a box: three tracks (10, 20, 30 s) titled "D<n>-<i>", tagged with disc n."""
    for i in range(3):
        meta = dict(title=f"D{n}-{i}", album=album, albumartist=artist, artist=artist, track=str(i + 1),
                    disc=str(n) if total is None else f"{n}/{total}")
        flac(parent / name, f"{n}{i + 1:02d}.flac", 10 * (i + 1), freq=300 + 100 * n + 10 * i, **meta)
    return parent / name


def box_release(rid, artist, title, cds, video=True):
    """A MusicBrainz box of `cds` CDs (three tracks each, "D<n>-<i>", 10/20/30 s) and a DVD-Video
    after them; its media carry titles of their own that are no folder's."""
    rel = te.mb_release(rid, artist, title)
    rel["date"] = "2011-05-01"
    rel["media"] = [{"format": "CD", "position": n, "title": f"Disc Title {n}", "tracks": [
        {"id": f"{rid}-{n}-t{i}", "position": i + 1, "title": f"D{n}-{i}", "length": ms,
         "recording": {"id": f"{rid}-{n}-r{i}", "title": f"D{n}-{i}"}}
        for i, ms in enumerate(te.LENS)]} for n in range(1, cds + 1)]
    if video:
        rel["media"].append({"format": "DVD-Video", "position": cds + 1, "tracks": [
            {"id": f"{rid}-v", "position": 1, "title": "Film", "length": 600000,
             "recording": {"id": f"{rid}-vr", "title": "Film", "video": True}}]})
    return rel


def ranged_box(lib):
    """Shape A: "CD1. ...", "CD2. ..." beside "CD3-4. .../CD1" and "/CD2" (discs 3 and 4)."""
    top = lib / "Band R" / "2011 - The Set (2011, 4CD+DVD, Label R, EU, 8869776)"
    disc(top, "CD1. 1984. Alpha (8869776-1, 5079)", 1, "The Set. CD1. Alpha (2011, 8869776-1)", total=4)
    disc(top, "CD2. 1987. Beta (8869776-2, 5095)", 2, "The Set. CD2. Beta (2011, 8869776-2)", total=4)
    gamma = top / "CD3-4. 2005. Gamma (2CD, 8869776-3, 9643)"
    disc(gamma, "CD1", 3, "The Set. CD3&4. Gamma 2CD (2011, 8869776-3)")
    disc(gamma, "CD2", 4, "The Set. CD3&4. Gamma 2CD (2011, 8869776-3)")
    return top


def numbered_box(lib, name="2012 - Band K Complete Box 3CD+DVD", numbers=(1, 2, 3), total=4, artist="Band K"):
    """Shape B: "01. (1980) - Alpha (2012) - KZ 1" and "02. (1986) - Beta 2CD/CD-1 (KZ 2)", "/CD-2
    (KZ 3)", every disc tagged as the box's album "COMPLETE BOX" with its own disc number."""
    top = lib / "Band K" / name
    a, b, c = numbers
    disc(top, "01. (1980) - Alpha (2012) - KZ 1", a, "COMPLETE BOX", artist, total=total)
    beta = top / "02. (1986) - Beta 2CD"
    disc(beta, "CD-1 (KZ 2)", b, "COMPLETE BOX", artist, total=total)
    disc(beta, "CD-2 (KZ 3)", c, "COMPLETE BOX", artist, total=total)
    return top


class DSC5_BoxOfAlbumFolders(te.Base):
    def groups(self, where=None):
        return engine.Engine.albums_in(self.engine(te.FakeHttp()), where or self.lib)

    def shape(self, gs, top):
        return sorted((str(g.folder.relative_to(top)) if g.folder != top else ".", len(g.files)) for g in gs)

    def test_DSC5_the_cd_count_a_box_folder_names(self):
        n = engine.box_cd_count
        self.assertEqual(n("2012 - Kenso Complete Box 13CD+DVD"), 13)
        self.assertEqual(n("2011 - The Collection (2011, 7CD+DVD, Sony, Columbia, EU, UK, 88697763332)"), 7)
        self.assertEqual(n("Box [16 CD]"), 16)
        self.assertEqual(n("Set (4xSACD)"), 4)
        self.assertEqual(n("CD4-5. 2005. Ca Ira (2CD, 88697763332-4, 964392001)"), 2)
        self.assertEqual(n("CD4-5. 2005. Ca Ira (88697763332-4, 964392001)"), 0)   # (a disc word, no count)
        self.assertEqual(n("Album (1CD)"), 0)
        self.assertEqual(n("Boxsets & Compilations"), 0)
        self.assertEqual(n("Two Sets (2CD + 3CD)"), 0)                             # (two counts: none)

    def test_DSC5_box_folder_with_a_disc_range_folder_is_one_album(self):
        top = ranged_box(self.lib)
        gs = self.groups()
        self.assertEqual([(g.folder, len(g.files), g.others) for g in gs], [(top, 12, [])])
        album = ma.read_album(top, "Band R", top.name)
        self.assertEqual([t["disc"] for t in album["tracks"]], [1] * 3 + [2] * 3 + [3] * 3 + [4] * 3)

    def test_DSC5_box_of_numbered_album_folders_is_one_album(self):
        top = numbered_box(self.lib)
        gs = self.groups()
        self.assertEqual([(g.folder, len(g.files), g.others) for g in gs], [(top, 9, [])])

    def test_BOX1_MED17_DSC5_the_box_is_tagged_as_the_box_disc_n_of_n(self):
        # BOX-1: the box release, never the standalone album of one disc; MED-17: the DVD after
        # the CDs drops out of the total
        for make, artist, title in ((ranged_box, "Band R", "The Set"), (numbered_box, "Band K", "COMPLETE BOX")):
            with self.subTest(shape=make.__name__):
                top = make(self.lib)
                standalone = box_release("mb-alone", artist, "Gamma" if artist == "Band R" else "Beta", 2, video=False)
                for m in standalone["media"]:
                    for t in m["tracks"]:
                        t["title"] = t["recording"]["title"] = t["title"].replace("D1-", "D3-").replace("D2-", "D4-")
                eng = self.engine(SetHttp(mb=[box_release("mb-box", artist, title, 4 if artist == "Band R" else 3),
                                              standalone]))
                gs = eng.albums_in(self.lib)
                self.assertEqual([g.folder for g in gs], [top])
                p = eng.identify(gs[0])
                self.assertEqual((p.status, p.release_id), ("identified", "mb-box"), p.why)
                cds = 4 if artist == "Band R" else 3
                self.assertEqual([t["discnumber"][0] for t in p.targets],
                                 [str(n) for n in range(1, cds + 1) for _i in range(3)])
                self.assertEqual({t["totaldiscs"][0] for t in p.targets}, {str(cds)})
                import shutil
                shutil.rmtree(top)

    def test_DSC5_the_box_is_planned_as_one_album_and_filed_disc_by_disc(self):
        top = numbered_box(self.lib)
        dest = self.tmp / "library"
        dest.mkdir()
        eng = self.engine(SetHttp(mb=[box_release("mb-box", "Band K", "COMPLETE BOX", 3)]), operation="move",
                          destination=str(dest), naming={"preset": "tagmender", "template": ""})
        plan = eng.plan(top)
        self.assertEqual(len(plan["albums"]), 1, [a.get("source_dir") for a in plan["albums"]])
        a = plan["albums"][0]
        self.assertEqual(a["status"], "ready", a.get("notes"))
        self.assertEqual(a["identification"]["release_id"], "mb-box")
        dsts = [fe["dst"] for fe in a["files"]]
        self.assertEqual(len(set(dsts)), 9, dsts)
        self.assertTrue(all("~" not in Path(x).name for x in dsts), dsts)

    def test_DSC5_only_the_sub_folder_asked_for(self):
        top = numbered_box(self.lib)
        gs = self.groups(top / "02. (1986) - Beta 2CD")
        self.assertEqual([(g.folder.name, len(g.files)) for g in gs], [("02. (1986) - Beta 2CD", 6)])

    def test_DSC5_a_gap_or_a_repeat_in_the_disc_numbers_keeps_the_folders_apart(self):
        for numbers in ((1, 2, 2), (1, 2, 4), (2, 1, 3)):     # (a repeat, a gap, out of the folders' order)
            with self.subTest(numbers=numbers):
                top = numbered_box(self.lib, numbers=numbers)
                self.assertGreater(len(self.groups()), 1)
                import shutil
                shutil.rmtree(top)

    def test_DSC5_a_box_whose_discs_count_otherwise_stays_apart(self):
        top = numbered_box(self.lib, name="Band K Complete Box 4CD")     # (4 named, 3 discs)
        self.assertEqual(self.shape(self.groups(), top), [("01. (1980) - Alpha (2012) - KZ 1", 3),
                                                          ("02. (1986) - Beta 2CD", 6)])

    def test_DSC5_discs_saying_one_of_one_stay_apart(self):
        top = numbered_box(self.lib, total=1)
        self.assertGreater(len(self.groups()), 1)
        self.assertTrue(top.exists())

    def test_DSC5_two_artists_stay_apart(self):
        top = self.lib / "Various" / "Split Box (2CD)"
        disc(top, "01. Alpha", 1, "Split Box", "Band A")
        disc(top, "02. Beta", 2, "Split Box", "Band B")
        self.assertEqual(len(self.groups()), 2)

    def test_DSC5_an_artist_folder_of_albums_is_not_one_box(self):
        # an artist's folder (no CD count in its name) of albums each tagged as its own disc 1:
        # every album apart, as before - even when their disc numbers happen to count 1, 2, 3
        artist = self.lib / "Band N"
        for n, name in enumerate(("1980 - Alpha", "1982 - Beta", "1985 - Gamma"), 1):
            disc(artist, name, n, name[7:], "Band N")
        got = sorted((g.folder.name, len(g.files)) for g in self.groups())
        self.assertEqual(got, [("1980 - Alpha", 3), ("1982 - Beta", 3), ("1985 - Gamma", 3)])

    def test_DSC5_a_compilations_folder_is_not_one_box(self):
        # "Boxsets & Compilations (3CD sets)": a CD count in its name, but its albums are each
        # tagged disc 1 (and 2) of their own sets - never one album
        top = self.lib / "Band N" / "Boxsets & Compilations (3CD sets)"
        disc(top / "2001 - Hits", "CD1", 1, "Hits", "Band N")
        disc(top / "2001 - Hits", "CD2", 2, "Hits", "Band N")
        disc(top, "2004 - More Hits", 1, "More Hits", "Band N")
        got = sorted((g.folder.name, len(g.files)) for g in self.groups())
        self.assertEqual(got, [("2001 - Hits", 6), ("2004 - More Hits", 3)])

    def test_DSC5_a_box_folder_with_tracks_of_its_own_stays_as_before(self):
        top = numbered_box(self.lib)
        flac(top, "00.flac", 10, title="Intro", album="Bonus", albumartist="Band K", artist="Band K",
             track="1", disc="1")
        self.assertGreater(len(self.groups()), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
