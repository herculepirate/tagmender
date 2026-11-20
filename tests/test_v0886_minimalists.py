# The Minimalists (Mode 214/215, 2 CDs): tagged "Various" with each composer as the track
# artist, the discs titled "The Minimalists CD1: ..." and "... CD2: ...". MusicBrainz lists
# the release dated 2016 with "mode 214/215"; Discogs 14703366 the same pressing dated 2009
# with "mode 214/15", its works as index tracks whose movements list no lengths. Nothing was
# written: the discs were two albums, "Various" was the artist compared, the two entries were
# not paired, Discogs's lengths were not read, and MusicBrainz's 2016 date replaced the 2009
# one your rip log and Discogs agree on.
#   VAR-1 (album artist Various: the track artists), CAT-9 ("/" ranges), TRK-9 (work lengths),
#   DAT-4 (the paired Discogs pressing's date, when MusicBrainz's is after your rip log).
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import engine  # noqa: E402
import match_albums as ma  # noqa: E402
import match_core as mc  # noqa: E402

CFG = dict(mc.DEFAULT_CFG, preferred_countries=[])
LENS = (346.0, 232.0, 312.0, 227.0, 291.0, 466.0, 1270.0, 1031.0, 3096.0, 585.0, 247.0)
COMPOSERS = ("Reich",) * 5 + ("Gann",) * 2 + ("Andriessen", "Riley", "Lang", "Adams")


def album(**kw):
    a = {"artist": "Various", "title": "The Minimalists", "year": 2009, "discs": 2,
         "track_artists": ["Reich", "Gann", "Andriessen", "Riley", "Lang", "Adams"],
         "tracks": [{"disc": 1 if i < 8 else 2, "num": (i % 8) + 1, "title": f"T{i}", "secs": s}
                    for i, s in enumerate(LENS)],
         "barcodes": set(), "catnos": set()}
    a.update(kw)
    return a


def mb():
    return {"source": "mb", "id": "mb-min", "url": "u", "year": 2016, "country": "US", "label": "Mode",
            "artist": "Reich, Riley, Andriessen, Gann, Adams, Lang; Orkest de Volharding, Jussi Jaatinen",
            "title": "The Minimalists", "catnos": {mc.norm_code("mode 214/215")}, "barcodes": set(),
            "formats": {"cd"}, "disc_count": 2,
            "tracks": [{"disc": 1 if i < 8 else 2, "title": f"T{i}", "secs": s} for i, s in enumerate(LENS)]}


def discogs_tracks(fits=True):
    works = ((0, 5, 1400.0 if fits else 1600.0), (5, 7, 1739.0))
    out = []
    for i, s in enumerate(LENS):
        t = {"disc": 1 if i < 8 else 2, "title": f"T{i}", "secs": 0 if i < 7 else s}
        for k, (a, b, whole) in enumerate(works):
            if a <= i < b:
                t.update(index=a, work_secs=whole)
        out.append(t)
    return out


def discogs(fits=True):
    return {"source": "discogs", "id": "14703366", "url": "u", "year": 2009, "country": "US", "label": "Mode",
            "artist": "Orkest De Volharding, Jussi Jaatinen", "artist_alt": "Orkest De Volharding & Jussi Jaatinen",
            "track_artists": ["Steve Reich", "Kyle Gann", "Louis Andriessen", "Terry Riley", "David Lang",
                              "John Adams"],
            "title": "The Minimalists", "catnos": {mc.norm_code("mode 214/15")}, "barcodes": {"764593021421"},
            "formats": {"cd"}, "disc_count": 2, "tracks": discogs_tracks(fits)}


class VAR1_VariousAlbumArtist(unittest.TestCase):
    def test_VAR1_the_track_artists_named_in_the_credit_are_the_albums(self):
        sc = mc.score_candidate(album(), mb(), CFG)
        self.assertGreaterEqual(sc["artist_sim"], 0.85, sc["notes"])
        self.assertIn("album artist Various: your track artists are the release's", sc["notes"])

    def test_VAR1_and_the_track_artists_a_discogs_release_credits(self):
        sc = mc.score_candidate(album(), discogs(), CFG)
        self.assertGreaterEqual(sc["artist_sim"], 0.85, sc["notes"])

    def test_VAR1_without_track_artists_various_is_compared_as_before(self):
        sc = mc.score_candidate(album(track_artists=[]), mb(), CFG)
        self.assertLess(sc["artist_sim"], 0.85)

    def test_VAR1_track_artists_not_in_the_credit_do_not_help(self):
        sc = mc.score_candidate(album(track_artists=["Queen", "ABBA"]), mb(), CFG)
        self.assertLess(sc["artist_sim"], 0.85)


class CAT9_SlashRanges(unittest.TestCase):
    def test_CAT9_two_spellings_of_one_sets_numbers_agree(self):
        self.assertTrue(mc.catnos_agree(["mode 214/215"], ["mode 214/15"]))
        self.assertTrue(mc.catnos_agree(["mode 214/215"], ["mode 215"]))

    def test_CAT9_other_numbers_do_not(self):
        self.assertFalse(mc.catnos_agree(["mode 214/15"], ["mode 216"]))
        self.assertFalse(mc.catnos_agree(["ABC 12/3"], ["ABC 12"]))       # (3 is before 12: no range)

    def test_CAT9_the_two_entries_are_one_pressing(self):
        self.assertTrue(mc.same_pressing(mb(), discogs()))


class TRK9_WorkLengths(unittest.TestCase):
    def test_TRK9_movements_agree_through_their_works_length(self):
        sc = mc.score_candidate(album(), discogs(), CFG)
        self.assertEqual(sc["dur_frac"], 1.0, sc["notes"])
        self.assertIn("durations 11/11 agree", sc["notes"])

    def test_TRK9_a_work_whose_files_add_up_otherwise_disagrees(self):
        sc = mc.score_candidate(album(), discogs(fits=False), CFG)
        self.assertLess(sc["dur_frac"], 1.0, sc["notes"])

    def test_TRK9_read_from_a_discogs_index_track(self):
        import companions
        dm = ma.load_plugin(str(companions.discogs_dir()))
        rel = {"id": 1, "title": "W", "artists": [{"name": "X"}], "formats": [{"name": "CD", "qty": "1"}],
               "tracklist": [{"type_": "index", "position": "", "title": "City Life", "duration": "23:20",
                              "sub_tracks": [{"type_": "track", "position": f"1-{k}", "title": f"M{k}",
                                              "duration": ""} for k in (1, 2)]},
                             {"type_": "track", "position": "1-3", "title": "Other", "duration": "4:07"}]}
        tracks = ma.discogs_candidate(rel, dm)["tracks"]
        self.assertEqual([(t["secs"], t.get("work_secs")) for t in tracks], [(0, 1400), (0, 1400), (247, None)])


class DAT4_DateOfThePairedPressing(unittest.TestCase):
    MB = {"date": ["2016-04-01"], "originaldate": ["2016-04-01"], "originalyear": ["2016"],
          "catalognumber": ["mode 214/215"], "releasecountry": ["US"]}
    DG = {"discogs_released": ["2009"], "date": ["2009"], "catalognumber": ["mode 214/15"],
          "releasecountry": ["US"], "barcode": ["764593021421"]}

    def run_it(self, log=2009, sources=None, **dg):
        merged = {k: v for k, v in self.MB.items() if "date" in k or "year" in k}
        where = dict.fromkeys(merged, "musicbrainz")
        engine._rip_dated(merged, where, dict(self.DG, **dg), log, sources or {}, mb=self.MB)
        return merged, where

    def test_DAT4_musicbrainz_after_your_rip_log_discogs_not(self):
        merged, where = self.run_it()
        self.assertEqual(merged, {"date": ["2009"], "originaldate": ["2009"], "originalyear": ["2009"]})
        self.assertEqual(where["date"], "discogs")

    def test_DAT4_no_rip_log_or_discogs_later_too_keeps_musicbrainz(self):
        self.assertEqual(self.run_it(log=None)[0]["date"], ["2016-04-01"])
        self.assertEqual(self.run_it(discogs_released=["2012"])[0]["date"], ["2016-04-01"])

    def test_DAT4_the_pressings_own_date_not_its_masters(self):
        # (Date set to "original": Discogs's "date" is the master's first release - not this pressing's)
        self.assertEqual(self.run_it(discogs_released=["2012"], date=["1998"])[0]["date"], ["2016-04-01"])

    def test_DAT4_another_pressing_is_never_taken(self):
        self.assertEqual(self.run_it(catalognumber=["mode 300"])[0]["date"], ["2016-04-01"])
        self.assertEqual(self.run_it(releasecountry=["DE"])[0]["date"], ["2016-04-01"])

    def test_DAT4_your_field_sources_choice_stands(self):
        self.assertEqual(self.run_it(sources={"date": ["musicbrainz"]})[0]["date"], ["2016-04-01"])


class DSC15b_Guard(unittest.TestCase):
    def test_DSC15b_two_mixes_of_one_short_title_stay_apart(self):
        t = engine._album_title
        self.assertNotEqual(t({"album": ["Mix CD 1: Pete Tong"]}), t({"album": ["Mix CD 1: Sasha"]}))
        self.assertEqual(t({"album": ["The CD 2: x"]}), "the cd 2: x")


if __name__ == "__main__":
    unittest.main()
