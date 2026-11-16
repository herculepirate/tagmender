# DSC-6 (docs/MATCHING_RULES.md): a folder named after the albums it holds, joined ("1992 - The
# Stranger & An Innocent Man" holding "1977 - The Stranger" and "1983 - An Innocent Man"), each
# tagged as its own album, disc 1 of 1, is one album: the set they were issued in (a twofer).
# Before, each sub-folder was planned as its own album and matched to that album's standalone CD;
# the set's barcode on the scans beside them never counted. Synthetic data only.
import shutil
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_engine as te  # noqa: E402  (isolates XDG dirs first)
from test_v0821_engine_rules import SetHttp, flac  # noqa: E402
import engine  # noqa: E402
import match_albums as ma  # noqa: E402


def album(parent, name, album_title, artist="Band T", disc="1/1", first=1, n=3, freq=300, titles=None, **extra):
    """An album's folder: N tracks (10, 20, 30 s ...) numbered from FIRST, tagged as ALBUM_TITLE."""
    for i in range(n):
        meta = dict(title=f"{titles or album_title} {i}", album=album_title, albumartist=artist, artist=artist,
                    track=str(first + i), disc=disc, **extra)
        flac(parent / name, f"{first + i:02d}.flac", 10 * (i % 3 + 1), freq=freq + 10 * i, **meta)
    return parent / name


def twofer(lib, name="1992 - Alpha & Beta", artist="Band T", second_artist=None):
    top = lib / artist / name
    album(top, "1977 - Alpha", "Alpha", artist)
    album(top, "1983 - Beta", "Beta", second_artist or artist, freq=600)
    return top


def set_release(rid, artist, title, discs=("Alpha", "Beta")):
    """A MusicBrainz release of len(DISCS) CDs, three tracks each (10/20/30 s), titled as the albums."""
    rel = te.mb_release(rid, artist, title)
    rel["date"] = "1992-02"
    rel["media"] = [{"format": "CD", "position": n + 1, "tracks": [
        {"id": f"{rid}-{n}-t{i}", "position": i + 1, "title": f"{d} {i}", "length": ms,
         "recording": {"id": f"{rid}-{n}-r{i}", "title": f"{d} {i}"}}
        for i, ms in enumerate(te.LENS)]} for n, d in enumerate(discs)]
    return rel


class DSC6Twofer(te.Base):
    def groups(self, where=None):
        return engine.Engine.albums_in(self.engine(te.FakeHttp()), where or self.lib)

    def test_DSC6_a_folder_named_after_its_two_albums_is_one(self):
        top = twofer(self.lib)
        gs = self.groups()
        self.assertEqual([(g.folder, len(g.files)) for g in gs], [(top, 6)])
        a = ma.read_album(top, "Band T", top.name)
        self.assertEqual([(t["disc"], t["num"]) for t in a["tracks"]],
                         [(1, 1), (1, 2), (1, 3), (2, 1), (2, 2), (2, 3)])

    def test_DSC6_the_set_is_identified_and_tagged_disc_n_of_2(self):
        top = twofer(self.lib)
        standalone = set_release("mb-alpha", "Band T", "Alpha", discs=("Alpha",))
        eng = self.engine(SetHttp(mb=[set_release("mb-set", "Band T", "Alpha & Beta"), standalone]))
        gs = eng.albums_in(self.lib)
        self.assertEqual([g.folder for g in gs], [top])
        p = eng.identify(gs[0])
        self.assertEqual((p.status, p.release_id), ("identified", "mb-set"), p.why)
        self.assertEqual([t["discnumber"][0] for t in p.targets], ["1"] * 3 + ["2"] * 3)
        self.assertEqual({t["totaldiscs"][0] for t in p.targets}, {"2"})

    def test_DSC6_joined_with_and_plus_and_in_any_order(self):
        for name in ("1992 - Beta and Alpha", "Alpha + Beta (2CD) [Label]", "ALPHA & beta"):
            with self.subTest(name=name):
                top = twofer(self.lib, name=name)
                self.assertEqual([g.folder for g in self.groups()], [top])
                shutil.rmtree(top)

    def test_DSC6_an_edition_in_brackets_on_a_title_aside(self):
        top = self.lib / "Band T" / "1988 - Alpha + Beta"
        album(top, "1974 - Alpha [1986 Reissue]", "Alpha [1986 Reissue]")
        album(top, "1978 - Beta [1984 Reissue]", "Beta (1984 Reissue)", freq=600)
        self.assertEqual([g.folder for g in self.groups()], [top])

    def test_DSC6_a_folder_named_otherwise_stays_apart(self):
        for name in ("Band T - Collection", "1992 - Alpha & Gamma", "1992 - Alpha", "Alpha & Beta & Gamma"):
            with self.subTest(name=name):
                top = twofer(self.lib, name=name)
                self.assertEqual(sorted(g.folder.name for g in self.groups()), ["1977 - Alpha", "1983 - Beta"])
                shutil.rmtree(top)

    def test_DSC6_two_artists_stay_apart(self):
        twofer(self.lib, second_artist="Band U")
        self.assertEqual(sorted(g.folder.name for g in self.groups()), ["1977 - Alpha", "1983 - Beta"])

    def test_DSC6_a_folder_with_tracks_of_its_own_stays_apart(self):
        top = twofer(self.lib)
        album(self.lib / "Band T", top.name, "Gamma", freq=900)
        self.assertNotIn(top, [g.folder for g in self.groups()] and
                         [g.folder for g in self.groups() if len(g.files) == 6])

    def test_DSC6_only_when_the_set_folder_is_asked_for(self):
        top = twofer(self.lib)
        gs = self.groups(top / "1983 - Beta")
        self.assertEqual([g.folder for g in gs], [top / "1983 - Beta"])

    def test_DSC6_a_bonus_folder_numbered_on_stays_on_the_album_disc(self):
        top = self.lib / "Band V" / "Delta"
        album(top, "", "Delta", "Band V")
        album(top, "Bonus", "Delta", "Band V", first=4, n=2, freq=700)
        a = ma.read_album(top, "Band V", "Delta")
        self.assertEqual({t["disc"] for t in a["tracks"]}, {1})

    def test_DSC6_disc_folders_tagged_one_and_two_keep_their_tags(self):
        top = self.lib / "Band V" / "Epsilon"
        album(top, "CD2", "Epsilon", "Band V", disc="1/2")
        album(top, "CD1", "Epsilon", "Band V", disc="2/2", freq=800, titles="Zeta")
        a = ma.read_album(top, "Band V", "Epsilon")
        # (the tags say which disc each folder is, whatever the folders' order)
        self.assertEqual([t["disc"] for t in a["tracks"]], [1, 1, 1, 2, 2, 2])
        self.assertEqual({t["title"] for t in a["tracks"] if t["disc"] == 1}, {"Epsilon 0", "Epsilon 1", "Epsilon 2"})


def classics_box(lib, names=("Disc 1 1974 - Alpha [1997 Reissue]", "Disc 02", "Disc 3 - Gamma (Classics #2)"),
                 artists=("Band W", "Band W", "Band W"), name="2014 - Classics #2"):
    """DSC-7: an "Original Album Classics" box - its discs in folders named "Disc N ...", each
    tagged as its own album (one as the box's disc 2 already)."""
    top = lib / "Band W" / name
    titles = ("Alpha [1997 Reissue]", "Classics #2", "Gamma (Classics #2)")
    discs = ("1/1", "2/3", "01/01")
    for k, (n, t, d, a) in enumerate(zip(names, titles, discs, artists)):
        album(top, n, t, a, disc=d, freq=300 + 200 * k)
    return top


class DSC7NumberedDiscFolders(te.Base):
    def groups(self, where=None):
        return engine.Engine.albums_in(self.engine(te.FakeHttp()), where or self.lib)

    def test_DSC7_disc_folders_numbered_one_to_n_are_one_set(self):
        top = classics_box(self.lib)
        gs = self.groups()
        self.assertEqual([(g.folder, len(g.files)) for g in gs], [(top, 9)])
        a = ma.read_album(top, "Band W", top.name)
        self.assertEqual([t["disc"] for t in a["tracks"]], [1] * 3 + [2] * 3 + [3] * 3)

    def test_DSC7_the_set_is_identified_as_the_box(self):
        top = classics_box(self.lib)
        box = set_release("mb-box", "Band W", "Classics #2", discs=("Alpha", "Classics #2", "Gamma"))
        alone = set_release("mb-alpha", "Band W", "Alpha", discs=("Alpha",))
        eng = self.engine(SetHttp(mb=[box, alone]))
        gs = eng.albums_in(self.lib)
        self.assertEqual([g.folder for g in gs], [top])
        p = eng.identify(gs[0])
        self.assertEqual((p.status, p.release_id), ("identified", "mb-box"), p.why)
        self.assertEqual([t["discnumber"][0] for t in p.targets], ["1"] * 3 + ["2"] * 3 + ["3"] * 3)
        self.assertEqual({t["totaldiscs"][0] for t in p.targets}, {"3"})

    def test_DSC7_a_gap_in_the_numbers_keeps_them_apart(self):
        classics_box(self.lib, names=("Disc 1 - Alpha", "Disc 02", "Disc 4 - Gamma"))
        # (DSC-10 holds them as one group of three albums, each planned alone unless a release claims them)
        self.assertEqual(sum(len(g.trial) or 1 for g in self.groups()), 3)

    def test_DSC7_two_artists_keep_them_apart(self):
        classics_box(self.lib, artists=("Band W", "Band W", "Band X"))
        self.assertGreater(len(self.groups()), 1)

    def test_DSC7_a_series_whose_discs_do_not_name_the_set_stays_apart(self):
        top = self.lib / "Various" / "Drops (2001-2004)"
        album(top, "Disc 1", "Drops 1", "Various")
        album(top, "Disc 2", "Drops 2", "Various", freq=600)
        self.assertEqual(len(self.groups()), 2)

    def test_DSC7_folders_not_named_as_discs_stay_apart(self):
        classics_box(self.lib, names=("1974 - Alpha", "Disc 02", "Disc 3 - Gamma"))
        self.assertGreater(len(self.groups()), 1)

    def test_DSC7_a_box_folder_with_tracks_of_its_own_stays_as_before(self):
        top = classics_box(self.lib)
        album(top.parent, top.name, "Delta", "Band W", freq=1400)
        self.assertNotIn(9, [len(g.files) for g in self.groups()])


class DSC8OneDiscsCodes(te.Base):
    def test_DSC8_a_barcode_on_one_disc_of_a_twofer_is_not_the_sets(self):
        top = self.lib / "Band T" / "1992 - Alpha & Beta"
        album(top, "1977 - Alpha", "Alpha")
        album(top, "1983 - Beta", "Beta", freq=600, barcode="074643883726", catalognumber="CK 38837")
        a = ma.read_album(top, "Band T", top.name)
        self.assertEqual(a["barcodes"], set())
        self.assertNotIn("ck38837", a["catnos"])
        self.assertIn("74643883726", a["disc_only_codes"])

    def test_DSC8_a_sets_barcode_tagged_on_its_first_disc_only_is_kept(self):
        top = self.lib / "Band T" / "Gamma"
        album(top, "CD1", "Gamma", disc="1/2", barcode="5013929020160")
        album(top, "CD2", "Gamma", disc="2/2", freq=600, titles="Delta")
        a = ma.read_album(top, "Band T", "Gamma")
        self.assertEqual(a["barcodes"], {"5013929020160"})


class DSC8Titles(te.Base):
    def test_DSC8_a_sets_barcode_with_disc_words_in_its_titles_is_kept(self):
        for t1, t2 in (("Gamma (Disc 1)", "Gamma (Disc 2)"), ("Gamma", "Gamma (Bonus Disc)")):
            with self.subTest(titles=(t1, t2)):
                top = self.lib / "Band T" / "Gamma"
                album(top, "CD1", t1, disc="1/2", barcode="5013929020160")
                album(top, "CD2", t2, disc="2/2", freq=600, titles="Delta")
                a = ma.read_album(top, "Band T", "Gamma")
                self.assertEqual(a["barcodes"], {"5013929020160"})
                self.assertEqual(a["disc_only_codes"], [])
                shutil.rmtree(top)


class DSC8Subtitles(te.Base):
    def test_DSC8_discs_with_subtitles_of_one_set_keep_its_barcode(self):
        top = self.lib / "Band T" / "Gamma"
        album(top, "CD1", "Gamma: Dawn to Dusk", disc="1/2", barcode="5013929020160")
        album(top, "CD2", "Gamma: Twilight to Starlight", disc="2/2", freq=600, titles="Delta")
        a = ma.read_album(top, "Band T", "Gamma")
        self.assertEqual(a["barcodes"], {"5013929020160"})


class DSC8BoxNamedByItsDiscs(te.Base):
    def box(self, name, set_title, bracket):
        top = self.lib / "Band W" / name
        album(top, "Disc 01", set_title, "Band W", disc="1/3", barcode="886919685428", catalognumber="88691968542")
        album(top, "Disc 02", set_title, "Band W", disc="2/3", freq=600, titles="Beta",
              barcode="886919685428", catalognumber="88691968542")
        album(top, "Disc 3 - Gamma", f"Gamma ({bracket})", "Band W", disc="01/01", freq=900)
        return top

    def test_DSC8_a_boxs_own_barcode_on_the_discs_titled_as_the_box_is_kept(self):
        # (the rest titled "Gamma (Classics)": the box's name in their brackets)
        top = self.box("Band W Box", "Classics", "Classics")
        a = ma.read_album(top, "Band W", top.name)
        self.assertEqual(a["barcodes"], {"886919685428"})
        self.assertEqual(a["disc_only_codes"], [])

    def test_DSC8_a_numbered_box_named_in_brackets_or_by_its_folder(self):
        for name, bracket in (("Band W Box", "Classics #2"), ("2012 - Classics", "Remastered")):
            with self.subTest(name=name, bracket=bracket):
                top = self.box(name, "Classics", bracket)
                a = ma.read_album(top, "Band W", top.name)
                self.assertEqual(a["barcodes"], {"886919685428"})
                shutil.rmtree(top)

    def test_DSC8_a_twofers_folder_joining_its_titles_names_neither(self):
        top = self.lib / "Band T" / "1992 - Alpha & Beta"
        album(top, "1977 - Alpha", "Alpha")
        album(top, "1983 - Beta", "Beta (Remastered)", freq=600, barcode="074643883726")
        a = ma.read_album(top, "Band T", top.name)
        self.assertEqual(a["barcodes"], set())

    def test_DSC8_a_twofers_barcode_stays_one_discs_whatever_the_brackets_or_folder_say(self):
        cases = (("1992 - Live & Beta", "Live", "Beta (Live at Budokan)"),
                 ("1992 - Live & Beta", "Live", "Beta (Live)"),
                 ("1992 - Live & Beta", "Live", "Beta (Live 1975)"),
                 ("1992 - Demos & Beta", "Demos", "Beta (Demos)"),
                 ("1992 - Gold & Beta", "Gold", "Beta (Gold Edition)"),
                 ("1992 - Alpha & Beta", "Alpha", "Beta (Alpha Sessions)"),
                 ("1992 - Alpha", "Alpha", "Beta"),
                 ("1992 - The Stranger", "The Stranger", "An Innocent Man"))
        for name, t1, t2 in cases:
            for on in ("A", "B"):
                with self.subTest(name=name, titles=(t1, t2), barcode_on=on):
                    top = self.lib / "Band T" / name
                    code = dict(barcode="074643883726")
                    album(top, "A", t1, **(code if on == "A" else {}))
                    album(top, "B", t2, freq=600, **(code if on == "B" else {}))
                    a = ma.read_album(top, "Band T", top.name)
                    self.assertEqual(a["barcodes"], set())
                    self.assertIn("74643883726", a["disc_only_codes"])
                    shutil.rmtree(top)

    def test_DSC8_a_three_album_set_named_in_one_bracket_names_no_box(self):
        # (a bracket on one disc only, its title on one disc: one disc's word, not the set's)
        top = self.lib / "Band T" / "1995 - Live & Beta & Gamma"
        album(top, "A", "Live", barcode="074643883726")
        album(top, "B", "Beta (Live)", freq=600)
        album(top, "C", "Gamma", freq=900)
        a = ma.read_album(top, "Band T", top.name)
        self.assertEqual(a["barcodes"], set())


class DSC67Arriving(te.Base):
    def test_DSC6_one_album_of_a_twofer_waits_for_the_other(self):
        top = self.lib / "Band T" / "1992 - Alpha & Beta"
        album(top, "1977 - Alpha", "Alpha")
        self.assertEqual(engine.box_still_arriving(top / "1977 - Alpha"), "Beta is not here yet")
        album(top, "1983 - Beta", "Beta", freq=600)
        self.assertEqual(engine.box_still_arriving(top / "1977 - Alpha"), "")

    def test_DSC7_discs_whose_totals_count_more_wait(self):
        top = self.lib / "Band W" / "Classics"
        album(top, "Disc 1 - Alpha (Classics)", "Alpha (Classics)", disc="1/3")
        album(top, "Disc 2 - Beta (Classics)", "Beta (Classics)", disc="2/3", freq=600)
        self.assertEqual(engine.box_still_arriving(top / "Disc 2 - Beta (Classics)"), "disc 3 of 3 is not here yet")

    def test_DSC7_asked_about_the_sets_folder_as_the_watcher_does(self):
        top = self.lib / "Band W" / "Classics"
        album(top, "Disc 1 - Alpha (Classics)", "Alpha (Classics)", disc="1/5")
        album(top, "Disc 2 - Beta (Classics)", "Beta (Classics)", disc="2/5", freq=600)
        gs = engine.Engine.albums_in(self.engine(te.FakeHttp()), self.lib)
        self.assertEqual([g.folder for g in gs], [top])
        self.assertEqual(engine.box_still_arriving(gs[0].folder), "discs 3, 4, 5 of 5 are not here yet")

    def test_DSC6_scans_beside_the_albums_do_not_stop_the_wait(self):
        top = self.lib / "Band T" / "1992 - Alpha & Beta"
        album(top, "1977 - Alpha", "Alpha")
        (top / "Scans").mkdir()
        (top / "Scans" / "back.jpg").write_bytes(b"x")
        self.assertEqual(engine.box_still_arriving(top / "1977 - Alpha"), "Beta is not here yet")

    def test_DSC7_nothing_says_how_many_nothing_waits(self):
        top = self.lib / "Band W" / "Classics"
        album(top, "Disc 1 - Alpha (Classics)", "Alpha (Classics)", disc="01/01")
        self.assertEqual(engine.box_still_arriving(top / "Disc 1 - Alpha (Classics)"), "")


class LEN1DiscByDisc(unittest.TestCase):
    def setUp(self):
        import match_core as mc
        self.mc = mc
        self.cfg = dict(mc.DEFAULT_CFG, preferred_countries=[])

    def score(self, short):
        mc = self.mc
        lens = [(1, 211), (1, 311), (1, 291), (1, 458), (2, 246), (2, 318), (2, 218), (2, 258), (2, 231), (2, 197)]
        lens = lens[:self.cut] if getattr(self, "cut", None) else lens
        alb = {"artist": "Band T", "title": "Alpha & Beta", "year": 1992, "discs": 2, "barcodes": set(),
               "catnos": set(), "tracks": [{"disc": d, "num": k + 1, "title": f"T{k}", "secs": float(s)}
                                           for k, (d, s) in enumerate(lens)]}
        cand = {"source": "mb", "id": "x", "url": "u", "artist": "Band T", "title": "Alpha & Beta", "year": 1992,
                "country": "XE", "label": "L", "catnos": set(), "barcodes": set(), "disc_count": 2, "matrix": [],
                "tracks": [{"disc": d, "title": f"T{k}", "secs": s - (short(k, s) if d == 2 else 0)}
                           for k, (d, s) in enumerate(lens)]}
        return mc.score_candidate(alb, cand, self.cfg)

    def test_LEN1_one_disc_of_sleeve_times_agrees(self):
        s = self.score(lambda k, secs: 5 + k % 3)
        self.assertTrue(any(n.startswith("disc 2: every length listed") for n in s["notes"]), s["notes"])
        self.assertEqual(s["confidence"], self.score(lambda k, secs: 0)["confidence"])

    def test_LEN1_four_percent_ten_seconds_at_most(self):
        st = self.mc.sleeve_times
        base = [(245.7, 240.0), (217.8, 212.0), (258.1, 252.0), (231.1, 225.0)]
        self.assertTrue(st(base + [(236.4, 228.0)]))           # 8.4 s on 228 s: a sleeve's rounding
        self.assertFalse(st(base + [(46.0, 40.0)]))            # 6 s on a 40 s interlude: another edit
        self.assertFalse(st(base + [(311.0, 300.0)]))          # 11 s: more than 10 s

    def test_LEN1_a_short_disc_of_four_tracks_is_not_excused(self):
        self.cut = 8                                            # (disc 2: four tracks)
        s = self.score(lambda k, secs: 6)
        self.assertFalse(any(n.startswith("disc 2: every length listed") for n in s["notes"]), s["notes"])

    def test_LEN1_a_disc_of_another_edit_still_differs(self):
        s = self.score(lambda k, secs: 40 if k == 6 else 5)        # (one track 40 s short: another edit)
        self.assertFalse(any(n.startswith("disc 2: every length listed") for n in s["notes"]), s["notes"])


if __name__ == "__main__":
    unittest.main()
