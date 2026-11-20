# Searching MusicBrainz and Discogs harder before an album is given back (2026-11-19 review):
#   QRY-1: the title's words, not only its exact phrase ("..., Pt. 2" listed as "..., Part II").
#   QRY-2: an album of "Various" searched by its first track artist and its track count.
#   QRY-3: a barcode's or catalogue number's finds of other releases never end the search before
#          a hit titled as your album is among them.
#   TTL-1: part and volume numbers read as numbers; two parts of a series are two albums.
import sys
import unittest
from pathlib import Path
from urllib.parse import unquote

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import match_albums as ma  # noqa: E402
import match_core as mc  # noqa: E402

CFG = dict(mc.DEFAULT_CFG, preferred_countries=[])


def album(**kw):
    a = {"artist": "Mournful Congregation", "title": "The Exuviae of Gods, Pt. 2", "year": 2023, "discs": 1,
         "tracks": [{"disc": 1, "num": i + 1, "title": f"T{i}", "secs": 600.0} for i in range(3)],
         "barcodes": set(), "catnos": set()}
    a.update(kw)
    return a


class Spy:
    def __init__(self, answer=None):
        self.urls, self.answer = [], answer or (lambda url: {"releases": []})

    def get_json(self, url, host):
        self.urls.append(unquote(url))
        return self.answer(url)


class QRY1_TitleWords(unittest.TestCase):
    def test_QRY1_words_of_the_title_are_searched_too(self):
        spy = Spy()
        ma.mb_search(spy, album(), CFG)
        self.assertTrue(any('release:(exuviae AND gods) AND artist:"Mournful Congregation"' in u for u in spy.urls),
                        spy.urls)

    def test_QRY1_title_words(self):
        self.assertEqual(ma.title_words("The Exuviae of Gods, Pt. 2"), ["exuviae", "gods"])
        self.assertEqual(ma.title_words("Jimbo"), [])                     # (one word: the phrase search's)


class QRY2_VariousByTrackArtist(unittest.TestCase):
    def test_QRY2_the_first_track_artist_and_the_track_count(self):
        spy = Spy()
        a = album(artist="Various", title="The Minimalists Collection", track_artists=["Reich", "Gann"])
        ma.mb_search(spy, a, CFG)
        joined = "\n".join(spy.urls)
        self.assertIn('release:"The Minimalists Collection" AND artist:"Reich"', joined)
        self.assertIn("release:(minimalists AND collection) AND tracks:3", joined)


class QRY3_CodesDoNotEndTheSearch(unittest.TestCase):
    def test_QRY3_other_releases_found_by_a_number_do_not_stop_the_title_search(self):
        other = [{"id": f"x{i}", "title": f"Unrelated {i}", "media": [{"track-count": 3}]} for i in range(6)]

        def answer(url):
            if "query=" not in url:
                return None                                   # (a release's own page: not needed here)
            return {"releases": other} if "catno" in unquote(url) else {"releases": []}
        spy = Spy(answer)
        ma.mb_search(spy, album(catno_search=["71063-2"]), CFG)
        self.assertTrue(any('release:"The Exuviae of Gods, Pt. 2" AND artist:' in u for u in spy.urls))


class QRY4_SecondGroup(unittest.TestCase):
    def test_QRY4_the_next_group_titled_as_your_album_is_expanded(self):
        # the first hit is the same-titled single (group "rg-single", 1 track); the album's group second
        hits = [{"id": "s1", "title": "Band", "media": [{"track-count": 1}],
                 "release-group": {"id": "rg-single", "title": "Band"}},
                {"id": "a1", "title": "Band", "media": [{"track-count": 9}],
                 "release-group": {"id": "rg-album", "title": "Band"}}]
        asked = []

        def answer(url):
            u = unquote(url)
            if "release-group=" in u:
                rg = u.split("release-group=")[1].split("&")[0]
                asked.append(rg)
                if rg == "rg-album":
                    return {"releases": [{"id": "a2", "media": [{"track-count": 3}]}], "release-count": 1}
                return {"releases": [{"id": "s1", "media": [{"track-count": 1}]}], "release-count": 1}
            if "query=" in u:
                return {"releases": hits}
            return None
        ma.mb_search(Spy(answer), album(title="Band"), CFG)
        self.assertEqual(asked[:2], ["rg-single", "rg-album"])


class QRY6_CatnoSpellings(unittest.TestCase):
    def test_QRY6_other_spellings_when_the_number_found_nothing(self):
        spy = Spy()
        ma.mb_search(spy, album(catno_search=["TWI1006"]), CFG)
        self.assertTrue(any('catno:"TWI 1006" AND artist:"Mournful Congregation"' in u for u in spy.urls), spy.urls)

    def test_QRY6_not_when_the_number_found_something(self):
        def answer(url):
            if "query=" not in url:
                return None
            return {"releases": [{"id": "x", "title": "Y", "media": [{"track-count": 3}]}]} \
                if 'catno:"TWI1006"' in unquote(url) else {"releases": []}
        spy = Spy(answer)
        ma.mb_search(spy, album(catno_search=["TWI1006"]), CFG)
        self.assertFalse(any('catno:"TWI 1006"' in u for u in spy.urls))


class QRY7_DeezerUpc(unittest.TestCase):
    def run_it(self, secs):
        def answer(url):
            u = unquote(url)
            if "search/album" in u:
                return {"data": [{"id": 9, "nb_tracks": 3}]}
            if u.endswith("/album/9/tracks?limit=300"):
                return {"data": [{"id": i, "duration": s, "isrc": ""} for i, s in enumerate(secs)]}
            if u.endswith("/album/9"):
                return {"upc": "764593021421"}
            return {}
        a = album()
        ma.deezer_isrc_releases(Spy(answer), a)
        return a.get("deezer_upc")

    def test_QRY7_the_upc_of_the_album_whose_lengths_are_yours(self):
        self.assertEqual(self.run_it([600, 600, 600]), "764593021421")

    def test_QRY7_not_another_albums(self):
        self.assertIsNone(self.run_it([100, 200, 300]))


class QRY5_FolderTitle(unittest.TestCase):
    def test_QRY5_the_folders_title_is_searched_when_the_tag_says_another(self):
        spy = Spy()
        ma.mb_search(spy, album(title="Untitled", folder_title="Curse Of The Red River"), CFG)
        self.assertTrue(any('release:"Curse Of The Red River" AND artist:"Mournful Congregation"' in u
                            for u in spy.urls))

    def test_QRY5_folder_title(self):
        self.assertEqual(ma._folder_title_of("2010 - Curse Of The Red River", "Untitled"), "Curse Of The Red River")
        for folder in ("1999 - Hits 1999 [FLAC]", "Disc 1", "CD2"):
            self.assertEqual(ma._folder_title_of(folder, "Hits 1999"), "", folder)
        for folder in ("Pink Floyd - The Wall [FLAC]", "Pink Floyd - 1979 - The Wall", "[1979] The Wall",
                       "The Wall CD1"):
            self.assertEqual(ma._folder_title_of(folder, "The Wall", "Pink Floyd"), "", folder)


class FBK5_FingerprintMajority(unittest.TestCase):
    def run_it(self, per_track):
        from unittest import mock
        matches = [(i, {f"rec{i}"}, set(r)) for i, r in enumerate(per_track)]
        with mock.patch.object(ma, "acoustid_matches", return_value=matches):
            return ma.acoustid_releases(None, {"tracks": []})

    def test_FBK5_a_track_also_on_a_compilation_keeps_the_album(self):
        self.assertEqual(self.run_it([{"alb", "comp"}, {"alb"}, {"alb", "single"}]), {"alb"})
        self.assertEqual(self.run_it([{"alb"}, {"alb"}, {"comp"}]), {"alb"})       # (was: nothing)

    def test_FBK5_proof_only_for_a_release_holding_every_matched_track(self):
        from unittest import mock
        matches = [(0, {"r0"}, {"alb", "big"}), (1, {"r1"}, {"alb", "big"}), (2, {"r2"}, {"alb"})]
        a = {"tracks": []}
        with mock.patch.object(ma, "acoustid_matches", return_value=matches):
            self.assertEqual(ma.acoustid_releases(None, a), {"alb"})
        self.assertEqual(a["fp_proof"], {"alb"})
        matches = [(0, {"r0"}, {"alb"}), (1, {"r1"}, {"alb"}), (2, {"r2"}, {"comp"})]
        with mock.patch.object(ma, "acoustid_matches", return_value=matches):
            self.assertEqual(ma.acoustid_releases(None, a), {"alb"})          # (a candidate to compare)
        self.assertEqual(a["fp_proof"], set())                                # (but no proof)

    def test_FBK5_nothing_in_common_is_nothing(self):
        self.assertEqual(self.run_it([{"a"}, {"b"}, {"c"}]), set())


class QRY4_SecondMaster(unittest.TestCase):
    def test_QRY4_the_next_master_titled_as_your_album_is_expanded(self):
        from unittest import mock
        dm = mock.Mock()
        dm.split_discogs_title = lambda t: ("Mournful Congregation", "The Exuviae of Gods, Pt. 2")
        dm.clean_artist_name = lambda a: a
        results = [{"id": 1, "title": "x", "master_id": 10}, {"id": 2, "title": "x", "master_id": 20}]
        asked = []

        def answer(url):
            u = unquote(url)
            if "database/search" in u:
                return {"results": results}
            if "/masters/" in u:
                asked.append(u.split("/masters/")[1].split("/")[0])
                return {"versions": [{"id": 100 + len(asked)}]}
            return {"id": u.rsplit("/", 1)[1]}
        tracks = {"1": 1, "2": 1, "101": 1, "102": 3}
        with mock.patch.object(ma, "discogs_candidate",
                               lambda det, dm: {"id": det["id"], "tracks": [{}] * tracks.get(str(det["id"]), 1),
                                                "catnos": set(), "barcodes": set(), "formats": set()}):
            ma.discogs_search(Spy(answer), album(), dict(CFG, discogs_detail_fetch=2), dm)
        self.assertEqual(asked[:2], ["10", "20"])


class QRY8_TrackTitle(unittest.TestCase):
    def test_QRY8_a_distinctive_track_title_when_nothing_is_titled_as_the_album(self):
        from unittest import mock
        dm = mock.Mock()
        dm.split_discogs_title = lambda t: ("Mournful Congregation", t)
        dm.clean_artist_name = lambda a: a
        fetched = []

        def answer(url):
            u = unquote(url)
            if "track=" in u:
                return {"results": [{"id": 77, "title": "The Real Album Name"}]}
            if "database/search" in u:
                return {"results": []}
            if "/releases/" in u:
                fetched.append(u.rsplit("/", 1)[1])
                return {"id": 77}
            return None
        a = album(title="Wrong Tag", tracks=[{"disc": 1, "num": 1, "title": "A Long Distinctive Song Title",
                                               "secs": 600.0}, {"disc": 1, "num": 2, "title": "Intro", "secs": 60.0}])
        spy = Spy(answer)
        with mock.patch.object(ma, "discogs_candidate", lambda det, dm: {"id": det["id"], "tracks": [], "catnos": set(),
                                                                       "barcodes": set(), "formats": set()}):
            ma.discogs_search(spy, a, CFG, dm)
        self.assertTrue(any("track=A Long Distinctive Song Title" in u for u in spy.urls))
        self.assertIn("77", fetched)


class QRY9_NearCount(unittest.TestCase):
    def test_QRY9_versions_near_your_count_named_by_your_edition_words_first(self):
        hits = [{"id": "h1", "title": "The Silent Circus", "media": [{"track-count": 10}],
                 "release-group": {"id": "rg", "title": "The Silent Circus"}}]
        versions = [{"id": "v2003", "date": "2003", "media": [{"track-count": 10}]},
                    {"id": "v2006", "date": "2006", "media": [{"track-count": 10}]},
                    {"id": "v2011", "date": "2011", "media": [{"track-count": 10}]},
                    {"id": "v2020", "date": "2020", "disambiguation": "2020 remix / remaster",
                     "media": [{"track-count": 10}]}]
        fetched = []

        def answer(url):
            u = unquote(url)
            if "release-group=" in u:
                return {"releases": versions, "release-count": 4}
            if "query=" in u:
                return {"releases": hits}
            fetched.append(u.split("/release/")[1].split("?")[0])
            return None
        a = album(title="The Silent Circus (2020 Remix / Remaster)", artist="Between the Buried and Me",
                  tracks=[{"disc": 1, "num": i + 1, "title": f"T{i}", "secs": 200.0} for i in range(11)])
        ma.mb_search(Spy(answer), a, CFG)
        self.assertIn("v2020", fetched)
        self.assertEqual(ma._edition_words({"title": "The Silent Circus (2020 Remix / Remaster)"}),
                         {"2020", "remix", "remaster"})


class TRK10_SameTrackTwice(unittest.TestCase):
    def test_TRK10_a_featured_artist_and_accents_apart_one_track(self):
        k = ma._track_key
        p = Path("x.opus")
        self.assertEqual(k(p, {"title": "Le Détenteur [feat. Youri Raymond]"}), k(p, {"title": "Le Detenteur"}))
        self.assertEqual(k(p, {"title": "Omnipresent Perception [feat. Christian Donaldson]"}),
                         k(p, {"title": "Omnipresent Perception"}))
        self.assertNotEqual(k(p, {"title": "Injustice Revealed (demo version)"}), k(p, {"title": "Injustice Revealed"}))
        self.assertNotEqual(k(p, {"title": "Звезда (Remix)"}), k(p, {"title": "Луна (Remix)"}))   # (letters kept)
        self.assertNotEqual(k(p, {"title": "Song (with Strings)"}), k(p, {"title": "Song"}))

    def run_drop(self, tracks):
        from unittest import mock
        files = [Path(f"/x/{n}") for n, *_ in tracks]
        info = {f: {"title": t, "num": num, "secs": secs, "disc": 0, "has_ids": False}
                for f, (_n, t, num, secs) in zip(files, tracks)}
        with mock.patch.object(Path, "stat", lambda self: mock.Mock(st_mtime=0, st_size=0)):
            return [f.name for f in ma._drop_duplicate_tracks(files, info)[0]]

    def test_TRK10_another_encode_of_one_track_in_one_folder(self):
        # (The Aura: tracks 4 and 6 twice, a second apart)
        kept = self.run_drop([("04.a.opus", "Omnipresent Perception", 4, 371.5),
                              ("04.b.opus", "Omnipresent Perception [feat. C]", 4, 370.6),
                              ("05.opus", "Injustice Revealed", 5, 235.0)])
        self.assertEqual(len(kept), 2)

    def test_TRK10_a_flat_two_disc_folders_track_ones_both_stay(self):
        kept = self.run_drop([("01.a.flac", "Song", 1, 200.0), ("02.a.flac", "Other", 2, 300.0),
                              ("01.b.flac", "Song [feat. X]", 1, 201.0), ("02.b.flac", "Else", 2, 250.0)])
        self.assertEqual(len(kept), 4)


class TRK11_CreditBrackets(unittest.TestCase):
    def test_TRK11_a_covers_credit_and_a_bonus_mark_apart(self):
        self.assertTrue(mc.track_titles_agree("Your Mistake [Agnostic Front cover] [bonus track]", "Your Mistake"))
        self.assertTrue(mc.track_titles_agree("Dog Day Sunrise [Head of David cover]", "Dog Day Sunrise"))

    def test_TRK11_a_remix_or_another_song_stays_apart(self):
        self.assertFalse(mc.track_titles_agree("Undercover (Remix)", "Undercover"))
        self.assertFalse(mc.track_titles_agree("Song [bonus track]", "Other Song"))


class TTL1_PartNumbers(unittest.TestCase):
    def test_TTL1_one_part_however_written(self):
        self.assertEqual(mc.title_sim("The Exuviae of Gods, Pt. 2", "The Exuviae of Gods, Part II"), 1.0)
        self.assertEqual(mc.title_sim("Greatest Hits Vol. 3", "Greatest Hits Volume III"), 1.0)

    def test_TTL1_another_part_is_another_album(self):
        self.assertLessEqual(mc.title_sim("The Exuviae of Gods, Pt. 2", "The Exuviae of Gods, Part I"), 0.6)
        self.assertLessEqual(mc.title_sim("Symphony No. 5", "Symphony No. 9"), 0.6)

    def test_TTL1_a_coupling_in_either_order_is_one(self):
        self.assertEqual(mc.title_sim("Symphony No. 5 & Piano Concerto No. 4",
                                      "Piano Concerto No. 4 & Symphony No. 5"), 1.0)

    def test_TTL1_words_that_only_look_like_numerals(self):
        for t in ("Nov", "Novi", "No Civil", "No One Knows", "Lil Wayne"):
            self.assertEqual(mc.part_numbers(t), (), t)

    def test_TTL1_words_that_are_no_numbered_part_stay(self):
        self.assertEqual(mc.title_sim("Part of Me", "Part of Me"), 1.0)
        self.assertEqual(mc.part_numbers("Part of Me"), ())


if __name__ == "__main__":
    unittest.main()
