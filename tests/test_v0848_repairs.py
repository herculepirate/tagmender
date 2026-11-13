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

if __name__ == "__main__":
    unittest.main()
