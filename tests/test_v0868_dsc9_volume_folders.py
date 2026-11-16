# DSC-9 (docs/MATCHING_RULES.md): a folder holding a set's volumes in folders of their own
# ("Greatest Hits Volume I & II (2 Hybrid SACD)/Volume 1", "/Volume 2"), each tagged as an album of
# its own with no disc number, is one album when its name says it holds them. Before, each volume
# was planned as an album of its own: one was filed as "disc 3 of 4", the other tagged in place as
# half of the set. Synthetic data only.
import shutil
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_engine as te  # noqa: E402  (isolates XDG dirs first)
from test_v0821_engine_rules import SetHttp, flac  # noqa: E402
import engine  # noqa: E402
import match_albums as ma  # noqa: E402


def volume(parent, name, album_title, artist="Band V", disc=None, n=3, freq=300, titles=None, first=1, **extra):
    """A volume's folder: N tracks (10, 20, 30 s) numbered from 1, tagged as ALBUM_TITLE, with no
    disc number unless DISC is given."""
    for i in range(n):
        meta = dict(title=f"{titles or album_title} {i}", album=album_title, albumartist=artist, artist=artist,
                    track=str(first + i), **extra)
        if disc:
            meta["disc"] = disc
        flac(parent / name, f"{first + i:02d}.flac", 10 * (i % 3 + 1), freq=freq + 10 * i, **meta)
    return parent / name


def volume_set(lib, name="Band V - 1985 - Hits Volume I & II (2 Hybrid SACD 2017)", dirs=("Volume 1", "Volume 2"),
               artists=None, discs=None, extra=None, first=None):
    top = lib / "Band V" / name
    for k, d in enumerate(dirs):
        volume(top, d, f"Hits {'I' * (k + 1)}", (artists or ["Band V"] * 9)[k], disc=(discs or [None] * 9)[k],
               freq=300 + 300 * k, titles=("Alpha", "Beta", "Gamma")[k], first=(first or [1] * 9)[k],
               **(extra or [{}] * 9)[k])
    return top


def set_release(rid, artist, title, discs=("Alpha", "Beta")):
    """A MusicBrainz release of len(DISCS) CDs, three tracks each (10/20/30 s)."""
    rel = te.mb_release(rid, artist, title)
    rel["date"] = "2017-07-31"
    rel["media"] = [{"format": "CD", "position": n + 1, "tracks": [
        {"id": f"{rid}-{n}-t{i}", "position": i + 1, "title": f"{d} {i}", "length": ms,
         "recording": {"id": f"{rid}-{n}-r{i}", "title": f"{d} {i}"}}
        for i, ms in enumerate(te.LENS)]} for n, d in enumerate(discs)]
    return rel


class DSC9VolumeFolders(te.Base):
    def groups(self, where=None):
        return engine.Engine.albums_in(self.engine(te.FakeHttp()), where or self.lib)

    def apart(self):
        return sorted(g.folder.name for g in self.groups())

    def test_DSC9_volume_folders_of_a_set_named_so_are_one_album(self):
        top = volume_set(self.lib)
        gs = self.groups()
        self.assertEqual([(g.folder, len(g.files)) for g in gs], [(top, 6)])
        a = ma.read_album(top, "Band V", top.name)
        self.assertEqual([(t["disc"], t["num"]) for t in a["tracks"]],
                         [(1, 1), (1, 2), (1, 3), (2, 1), (2, 2), (2, 3)])

    def test_DSC9_the_set_is_identified_and_tagged_disc_n_of_2(self):
        top = volume_set(self.lib)
        eng = self.engine(SetHttp(mb=[set_release("mb-set", "Band V", "Hits")]))
        gs = eng.albums_in(self.lib)
        self.assertEqual([g.folder for g in gs], [top])
        p = eng.identify(gs[0])
        self.assertEqual((p.status, p.release_id), ("identified", "mb-set"), p.why)
        self.assertEqual([t["discnumber"][0] for t in p.targets], ["1"] * 3 + ["2"] * 3)
        self.assertEqual({t["totaldiscs"][0] for t in p.targets}, {"2"})

    def test_DSC9_the_name_says_the_volumes_or_their_count(self):
        for name in ("Hits Vol. 1-2", "Hits 1 + 2", "Hits Volumes 1 and 2", "Hits (2CD)", "Hits [2 Discs]",
                     "Hits (2xSACD)", "Hits I-II"):
            with self.subTest(name=name):
                top = volume_set(self.lib, name=name)
                self.assertEqual([g.folder for g in self.groups()], [top])
                shutil.rmtree(top)

    def test_DSC9_volume_folders_named_in_words_or_numerals(self):
        for dirs in (("Vol. I", "Vol. II"), ("Volume One", "Volume Two"), ("Vol 1", "Vol 2"),
                     ("Volume 1 - 1973-1977", "Volume 2 - 1978-1985"), ("Volume 01", "Volume 02")):
            with self.subTest(dirs=dirs):
                top = volume_set(self.lib, dirs=dirs)
                self.assertEqual([g.folder for g in self.groups()], [top])
                shutil.rmtree(top)

    def test_DSC9_a_series_folder_named_otherwise_stays_apart(self):
        for name in ("Now Music", "Hits (3CD)", "Hits Vol. 1-3", "Hits 2001", "Hits I & III", "Hits 1 & 2 (3CD)",
                     "Live (2CD) (1 & 3)"):
            with self.subTest(name=name):
                top = volume_set(self.lib, name=name)
                self.assertEqual(self.apart(), ["Volume 1", "Volume 2"])
                shutil.rmtree(top)

    def test_DSC9_a_gap_or_another_folder_name_stays_apart(self):
        for dirs in (("Volume 1", "Volume 3"), ("Volume 2", "Volume 3"), ("Volume 1", "Bonus"),
                     ("Volume 1", "Volume 1 Extra")):
            with self.subTest(dirs=dirs):
                top = volume_set(self.lib, dirs=dirs)
                self.assertEqual(len(self.groups()), 2)
                shutil.rmtree(top)

    def test_DSC9_two_artists_stay_apart(self):
        volume_set(self.lib, artists=("Band V", "Band W"))
        self.assertEqual(self.apart(), ["Volume 1", "Volume 2"])

    def test_DSC9_tracks_in_the_set_folder_itself_stay_apart(self):
        top = volume_set(self.lib)
        flac(top, "intro.flac", 10, title="Intro", album="Hits", albumartist="Band V", artist="Band V", track="1")
        gs = self.groups()
        self.assertFalse([g for g in gs if len({f.parent for f in g.files}) > 1], [(g.folder, g.files) for g in gs])

    def test_DSC9_disc_tags_numbering_it_otherwise_stay_apart(self):
        volume_set(self.lib, discs=("2", "1"))
        self.assertEqual(self.apart(), ["Volume 1", "Volume 2"])

    def test_DSC9_disc_tags_agreeing_or_repeating_join(self):
        for discs in (("1", "2"), ("1/2", "2/2"), ("1/1", "1/1"), ("1", None), ("2/2", "1/1")):
            with self.subTest(discs=discs):
                top = volume_set(self.lib, discs=discs)
                self.assertEqual([(g.folder, len(g.files)) for g in self.groups()], [(top, 6)])
                a = ma.read_album(top, "Band V", top.name)
                self.assertEqual(sorted({t["disc"] for t in a["tracks"]}), [1, 2])
                shutil.rmtree(top)

    def test_DSC9_one_volume_folder_planned_alone_stays_itself(self):
        top = volume_set(self.lib)
        gs = self.groups(top / "Volume 2")
        self.assertEqual([(g.folder.name, len(g.files)) for g in gs], [("Volume 2", 3)])

    def test_DSC9_volume_number_and_name_readers(self):
        vn = engine.volume_number
        self.assertEqual([vn(Path(x)) for x in ("Volume 1", "Vol. II", "vol 3", "Volume Ten", "Volume 2 (1978-1985)",
                                                "Volumes", "Volume 1 Extra", "Vol.ume 1", "Hits Volume 1")],
                         [1, 2, 3, 10, 2, 0, 0, 0, 0])
        self.assertEqual([vn(Path(x)) for x in ("Volume One", "Volume Three", "Vol. IX", "Volume 01")], [1, 3, 9, 1])
        nv = engine._names_volumes
        self.assertFalse(nv("Hits 1 & 2 (3CD)", 2))
        self.assertFalse(nv("Live (2CD) (1 & 3)", 2))
        self.assertTrue(nv("Hits (2-CD)", 2))
        self.assertTrue(nv("Greatest Hits Volume I & II (2 Hybrid SACD MFSL 2017)", 2))
        self.assertTrue(nv("Box (4CD)", 4))
        self.assertFalse(nv("Box (4CD)", 2))
        self.assertFalse(nv("1985 - Hits", 2))
        self.assertFalse(nv("2011 - 2 Live", 3))
        self.assertFalse(nv("Hits 2001-2", 2))

    def test_DSC9_volumes_numbered_in_words_are_discs_by_number_not_name_order(self):
        top = volume_set(self.lib, name="Hits (3CD)", dirs=("Volume One", "Volume Two", "Volume Three"))
        self.assertEqual([(g.folder, len(g.files)) for g in self.groups()], [(top, 9)])
        a = ma.read_album(top, "Band V", top.name)
        self.assertEqual(sorted({(t["disc"], t["title"].split()[0]) for t in a["tracks"]}),
                         [(1, "Alpha"), (2, "Beta"), (3, "Gamma")])

    def test_DSC9_a_series_pack_of_separate_releases_stays_apart(self):
        for extra in (({"date": "1999"}, {"date": "2003"}), ({"barcode": "5012345678900"}, {"barcode": "5012345678917"}),
                      ({"date": "1999", "barcode": "111111111111", "catalognumber": "none"},
                       {"date": "2003", "barcode": "222222222222", "catalognumber": "none"}),
                      ({"catalognumber": "none", "date": "1999"}, {"catalognumber": "none", "date": "2003"}),
                      ({"catalognumber": "NOW 1"}, {"catalognumber": "NOW 2"})):
            with self.subTest(extra=extra):
                top = volume_set(self.lib, name="Now Music Vol. 1-2", extra=extra)
                self.assertEqual(self.apart(), ["Volume 1", "Volume 2"])
                shutil.rmtree(top)

    def test_DSC9_one_barcode_on_both_volumes_is_the_set_whatever_their_dates(self):
        # (an earlier job tagged one volume as the 2017 pressing, the other kept 1985)
        top = volume_set(self.lib, extra=({"date": "1985", "barcode": "821797212168"},
                                          {"date": "2017-07-31", "barcode": "821797212168"}))
        self.assertEqual([(g.folder, len(g.files)) for g in self.groups()], [(top, 6)])

    def test_DSC9_a_total_of_another_release_not_written_by_tagmender_stays_apart(self):
        volume_set(self.lib, dirs=("Volume 1", "Volume II"), discs=("1/2", "3/4"))
        self.assertEqual(self.apart(), ["Volume 1", "Volume II"])

    def test_DSC9_an_earlier_jobs_disc_tags_are_no_numbering_of_the_set(self):
        # (Volume 1 tagged "1 of 2", Volume II tagged "3 of 4" by a release listing each disc's two layers)
        top = volume_set(self.lib, dirs=("Volume 1", "Volume II"), discs=("1/2", "3/4"),
                         extra=({"date": "1985", "barcode": "821797212168", "tagmender": "Resolved"},
                                {"date": "2017-07-31", "barcode": "821797212168", "tagmender": "Resolved"}))
        self.assertEqual([(g.folder, len(g.files)) for g in self.groups()], [(top, 6)])
        a = ma.read_album(top, "Band V", top.name)
        self.assertEqual([(t["disc"], t["num"]) for t in a["tracks"]],
                         [(1, 1), (1, 2), (1, 3), (2, 1), (2, 2), (2, 3)])

    def test_DSC9_one_disc_tag_on_every_volume_but_tracks_numbered_on_stays_apart(self):
        for discs, first in ((("1", "1"), (1, 4)), (("2", "2"), (1, 1))):
            with self.subTest(discs=discs):
                top = volume_set(self.lib, discs=discs, first=first)
                self.assertEqual(self.apart(), ["Volume 1", "Volume 2"])
                shutil.rmtree(top)

    def test_DSC9_a_total_other_than_the_volumes_stays_apart(self):
        for discs in (("1/3", "2/3"), ("1/1", "1/3")):
            with self.subTest(discs=discs):
                top = volume_set(self.lib, discs=discs)
                self.assertEqual(self.apart(), ["Volume 1", "Volume 2"])
                shutil.rmtree(top)

    def test_DSC9_a_volume_half_tagged_or_of_two_albums_or_untagged_stays_apart(self):
        top = volume_set(self.lib)
        flac(top / "Volume 2", "09.flac", 10, title="X", album="Hits II", albumartist="Band V", artist="Band V",
             track="9", disc="2")
        self.assertEqual(len(self.groups()), 2)
        shutil.rmtree(top)
        top = volume_set(self.lib)
        flac(top / "Volume 2", "09.flac", 10, title="X", album="Other", albumartist="Band V", artist="Band V", track="9")
        self.assertFalse([g for g in self.groups() if len({f.parent for f in g.files}) > 1])
        shutil.rmtree(top)
        top = self.lib / "Band V" / "Hits Volume I & II"
        volume(top, "Volume 1", "Hits I")
        flac(top / "Volume 2", "01.flac", 10)
        self.assertFalse([g for g in self.groups() if len({f.parent for f in g.files}) > 1])

    def test_DSC9_a_volume_with_disc_folders_inside_stays_apart(self):
        top = self.lib / "Band V" / "Hits Volume I & II"
        volume(top / "Volume 1", "CD1", "Hits I")
        volume(top, "Volume 2", "Hits II", freq=600)
        self.assertFalse([g for g in self.groups() if g.folder == top])

    def test_DSC9_the_watcher_waits_for_the_volumes_not_here_yet(self):
        top = self.lib / "Band V" / "Hits Volume I & II (2 Hybrid SACD 2017)"
        volume(top, "Volume 1", "Hits I")
        self.assertEqual(engine.box_still_arriving(top / "Volume 1"), "volume 2 of 2 is not here yet")
        volume(top, "Volume 2", "Hits II", freq=600)
        self.assertEqual(engine.box_still_arriving(top / "Volume 1"), "")
        other = self.lib / "Band V" / "Now Music"
        volume(other, "Volume 1", "Now 1")
        self.assertEqual(engine.box_still_arriving(other / "Volume 1"), "")
        pack = self.lib / "Band V" / "Now Music Vol. 1-2"
        volume(pack, "Volume 1", "Now 1", barcode="5012345678900")
        self.assertEqual(engine.box_still_arriving(pack / "Volume 1"), "")


if __name__ == "__main__":
    unittest.main()
