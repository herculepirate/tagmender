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

    def test_a_country_bracket_whose_word_is_in_the_title_still_counts(self):
        # (review: the words of a bracket found anywhere in the title dropped it - "Made in Japan
        # (Japan)" lost its country; only a bracket the title holds as a bracket is the title's)
        for folder, title in (("1972 - Made in Japan (Japan)", "Made in Japan"),
                              ("Anarchy in the UK (UK)", "Anarchy in the UK"),
                              ("Live in Germany [Germany]", "Live in Germany")):
            _c, against = clues_of(folder, title=title, country="US")
            self.assertTrue([a for a in against if a.startswith("folder name says")], (folder, against))



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


import engine  # noqa: E402


class NAM10_SortFollowsName(unittest.TestCase):
    MB = {"albumartist": ["Philip Glass"], "albumartistsort": ["Glass, Philip"],
          "artist": ["Philip Glass"], "artistsort": ["Glass, Philip"]}
    DG = {"albumartist": ["Tara Hugo Sings Philip Glass"], "albumartistsort": ["Tara Hugo Sings Philip Glass"],
          "artist": ["Tara Hugo Sings Philip Glass"], "artistsort": ["Tara Hugo Sings Philip Glass"]}

    def merged(self):
        m = {"albumartist": ["Philip Glass"], "albumartistsort": ["Tara Hugo Sings Philip Glass"],
             "artist": ["Philip Glass"], "artistsort": ["Tara Hugo Sings Philip Glass"]}
        w = {"albumartist": "musicbrainz", "albumartistsort": "discogs", "artist": "musicbrainz", "artistsort": "discogs"}
        return m, w

    def test_sort_of_the_name_written(self):
        m, w = engine.sorts_follow_names(*self.merged(), {"musicbrainz": self.MB, "discogs": self.DG})
        self.assertEqual(m["albumartistsort"], ["Glass, Philip"])
        self.assertEqual(m["artistsort"], ["Glass, Philip"])
        self.assertEqual(w["albumartistsort"], "musicbrainz")

    def test_no_sort_of_that_source_none_written(self):
        mb = {k: v for k, v in self.MB.items() if not k.endswith("sort")}
        m, _w = engine.sorts_follow_names(*self.merged(), {"musicbrainz": mb, "discogs": self.DG})
        self.assertNotIn("albumartistsort", m)
        self.assertNotIn("artistsort", m)

    def test_same_name_in_both_nothing_changes(self):
        dg = dict(self.DG, albumartist=["Philip Glass"], artist=["Philip Glass"],
                  albumartistsort=["Philip Glass"], artistsort=["Philip Glass"])
        m = {"albumartist": ["Philip Glass"], "albumartistsort": ["Philip Glass"],
             "artist": ["Philip Glass"], "artistsort": ["Philip Glass"]}
        w = dict.fromkeys(m, "discogs")
        out, _w = engine.sorts_follow_names(dict(m), dict(w), {"musicbrainz": self.MB, "discogs": dg})
        self.assertEqual(out, m)


from unittest import mock  # noqa: E402
import match_albums as ma  # noqa: E402
import plan as pl  # noqa: E402
import test_v0847_tag3_one_release as t3  # noqa: E402


class TAG3_OwnEarlierPickOneRun(t3.te.Base):
    """BS&T 1968 [SACD]: the clashing IDs are TagMender's own earlier (wrong) pick; the matcher
    left it because the copy points elsewhere (TAG-1: your own catalogue number, here in the folder
    name) without FIL-1 proof (one length off). One run must write the new release; it took two
    (album only first) before. (A helper, not a subclass: the TAG-3 suite's tests run in their own file.)"""
    FOLDER = "1991 - Album T (CAT-901)"

    def setUp(self):
        super().setUp()
        self.h = t3.TAG3_Engine("test_TAG3_unproven_below_threshold_is_album_only_with_reason")
        self.h.album, self.h.engine = self.album, self.engine

    def owned(self, d, mb, every=True):
        files = sorted(str(f) for f in Path(d).rglob("*.flac"))
        idx = {f: ({mb}, set()) for f in (files if every else files[:1])}
        return mock.patch.object(ma, "_own_index", return_value=idx)

    def run_it(self, d, idx_patch):
        http = t3.te.FakeHttp(mb=[t3._mb(t3.OLD_MB)], dg=[t3._dg(904, durs=("0:10", "0:20", "0:45"))])
        with idx_patch:
            return self.h.ident(d, http)

    def test_own_ids_replaced_in_one_run(self):
        d = self.h.make(folder=self.FOLDER, MUSICBRAINZ_ALBUMID=t3.OLD_MB, date="1983")
        e, p = self.run_it(d, self.owned(d, t3.OLD_MB))
        self.assertEqual(p.tier, "exact", (p.why, p.notes))
        self.assertTrue(getattr(p, "release_proven_over", None), p.notes)
        self.assertTrue(any("TagMender's own earlier pick" in n for n in p.notes), p.notes)
        ch = pl.build([p], e.profile)["albums"][0]["files"][0]["changes"]
        self.assertIsNone(ch.get("musicbrainz_albumid", "kept"), "the earlier pick's ID goes in the same run")
        self.assertEqual(ch.get("discogs_release_id"), ["904"])

    def test_ids_of_another_tagger_still_album_only(self):
        d = self.h.make(folder=self.FOLDER, MUSICBRAINZ_ALBUMID=t3.OLD_MB, date="1983")
        _e, p = self.run_it(d, mock.patch.object(ma, "_own_index", return_value={}))
        self.assertEqual(p.tier, "album_only", p.why)

    def test_owned_on_some_files_only_is_no_own_pick(self):
        d = self.h.make(folder=self.FOLDER, MUSICBRAINZ_ALBUMID=t3.OLD_MB, date="1983")
        _e, p = self.run_it(d, self.owned(d, t3.OLD_MB, every=False))
        self.assertNotEqual(getattr(p, "release_proven_over", None) or [], ["musicbrainz_albumid"], p.notes)


class NAM11_LatinSideOfTwoLanguageTitles(unittest.TestCase):
    def test_latin_side_chosen(self):
        self.assertEqual(engine.latin_side('「森の組曲」 = "Forest" Suite'), '"Forest" Suite')
        self.assertEqual(engine.latin_side(["空 = Sky"]), ["Sky"])

    def test_others_unchanged(self):
        for v in ("Björk", "小瀬村晶", "Sky = Ciel", "Ólafur Arnalds"):
            self.assertEqual(engine.latin_side(v), v, v)

    def test_only_when_asked(self):
        t = {"album": ['「森の組曲」 = "Forest" Suite'], "title": ["空 = Sky"]}
        self.assertEqual(engine.latin_titles(dict(t), {}), t)
        out = engine.latin_titles(dict(t), {"translate_album_titles": True, "translate_track_titles": True})
        self.assertEqual(out, {"album": ['"Forest" Suite'], "title": ["Sky"]})


import test_v0830_rules as r830  # noqa: E402


class TIE4_SharedBarcodeProvesNoBootleg(unittest.TestCase):
    """Gary Moore - Dark Days in Paradise: a counterfeit "Europe" CD listing the official barcode
    (and its track lengths) beat the official pressings, which list no lengths, because the
    barcode on the scans "proved" it. A barcode an official release agreeing with the copy
    carries too proves no bootleg."""
    BAR = "724384416522"
    OCR = "Printed in Holland\n7 24384 41652 2"

    def scored(self, with_official=True):
        a = r830.album(r830.FILES)
        boot = dict(r830.cand(r830.FILES), id="boot", unofficial=True, country="Europe", barcodes={self.BAR})
        official = dict(r830.cand(r830.FILES), id="official", country="Europe", barcodes={self.BAR},
                        tracks=[dict(t, secs=0.0) for t in r830.cand(r830.FILES)["tracks"]])
        cands = (boot, official) if with_official else (boot,)
        return [mc.score_candidate(a, c, r830.CFG, self.OCR) for c in cands]

    def test_official_sharing_the_barcode_wins_or_the_bootleg_is_doubted(self):
        sc = self.scored()
        # (as scored for real: the official Discogs entries list no lengths - 84.2 - and the
        # counterfeit does - 97.9)
        sc = [dict(sc[0], confidence=97.9), dict(sc[1], confidence=84.2)]
        self.assertTrue(mc.barcode_proves(sc[0]) and mc.barcode_proves(sc[1]), [s.get("code") for s in sc])
        r = mc.classify(sc, r830.CFG)
        boot_auto = r["status"] == "auto" and r["best"]["cand"]["id"] == "boot"
        self.assertFalse(boot_auto, (r["status"], r["best"]["cand"]["id"], r.get("why")))

    def test_a_bootleg_alone_with_its_own_barcode_still_proven(self):
        sc = self.scored(with_official=False)
        self.assertEqual(mc.unofficial_why(sc[0], sc), [])


import naming  # noqa: E402


class ED7_BootlegIsNoRemaster(unittest.TestCase):
    def test_unofficial_gets_no_remaster_word(self):
        dates = {"date": ["1991"], "originaldate": ["1990"]}
        self.assertEqual(naming.edition(dict(dates, media=["CD, Album, Unofficial Release"])), "")
        self.assertEqual(naming.edition(dict(dates, media=["CD"], releasestatus=["bootleg"])), "")
        self.assertEqual(naming.edition(dict(dates, media=["CD, Album, Unofficial Release"], **{"~edition_hd": ["1"]})), "HD")

    def test_official_reissue_unchanged(self):
        self.assertEqual(naming.edition({"date": ["1991"], "originaldate": ["1990"], "media": ["CD, Album"]}), "1991 Remaster")


if __name__ == "__main__":
    unittest.main()
