# 0.8.87: NAM-13 - the album's folder follows its album artist (docs/MATCHING_RULES.md):
# - Yelena Eckemoff - Nocturnal Animals: tagged from MusicBrainz ("Yelena Eckemoff, Arild Andersen,
#   Thomas Strønen", three ALBUMARTISTS), then from Discogs ("Yelena Eckemoff"): the three names were
#   left in the file and named the folder "Arild Andersen & Thomas Strønen & Yelena Eckemoff";
# - Bill Bruford's Earthworks - Live at the Schauburg Bremen 1987: the album artist edited to one
#   name, the five credited names left in ALBUMARTISTS naming the folder.
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_executor import Base, audio, target  # noqa: E402  (isolates XDG dirs first)

import naming  # noqa: E402
import plan as pl  # noqa: E402
import profiles as pf  # noqa: E402
import tagio  # noqa: E402

THREE = ["Yelena Eckemoff", "Arild Andersen", "Thomas Strønen"]
FIVE = ["Bill Bruford’s Earthworks", "Django Bates", "Iain Ballamy", "Bill Bruford", "Mick Hutton"]
# (the user's own folder script: collaborators sorted A to Z)
SORTED = ("$set(_folderartist,%albumartist%)\n"
          "$if($gt($lenmulti(%_albumartists%),1),$set(_folderartist,$join($sortmulti(%_albumartists%), & )))\n"
          "$if2(%_folderartist%,%artist%)/%album%")


class NamesAgree(unittest.TestCase):
    def test_NAM13_names_of_the_credit_agree(self):
        self.assertTrue(naming.names_agree(["Yelena Eckemoff, Arild Andersen, Thomas Strønen"], THREE))
        self.assertTrue(naming.names_agree(["Prince & The Revolution"], ["Prince", "The Revolution"]))
        self.assertTrue(naming.names_agree(FIVE, FIVE))

    def test_NAM13_another_credits_names_do_not(self):
        self.assertFalse(naming.names_agree(["Yelena Eckemoff"], THREE))
        self.assertFalse(naming.names_agree(["Bill Bruford"], FIVE))

    def test_NAM13_the_folder_is_the_album_artists_when_the_names_are_another_credits(self):
        for tmpl in (SORTED, "$join(%_albumartists_sorted%, & )/%album%"):
            got = naming.render(tmpl, {"albumartist": ["Yelena Eckemoff"], "albumartists": THREE,
                                       "album": ["Nocturnal Animals"]})
            self.assertEqual(got[0], "Yelena Eckemoff", tmpl)

    def test_NAM13_a_collaboration_still_sorts_its_names(self):
        got = naming.render(SORTED, {"albumartist": ["Thomas Strønen & Arild Andersen"],
                                     "albumartists": ["Thomas Strønen", "Arild Andersen"], "album": ["X"]})
        self.assertEqual(got[0], "Arild Andersen & Thomas Strønen")


class NamesFollow(Base):
    def test_NAM13_a_new_album_artist_replaces_the_names_left_from_another_source(self):
        f = audio(self.lib / "In" / "Y" / "01.flac", albumartists="; ".join(THREE))
        before = tagio.read(f)
        before["albumartists"] = THREE                     # (as an earlier MusicBrainz tagging left them)
        changes, _rows = pl.diff_tags(before, {"albumartist": ["Yelena Eckemoff"]}, pf.default(), 100.0)
        self.assertEqual(changes.get("albumartists"), ["Yelena Eckemoff"])

    def test_NAM13_names_that_agree_are_left(self):
        before = {"albumartists": THREE}
        changes, _rows = pl.diff_tags(before, {"albumartist": ["Yelena Eckemoff, Arild Andersen, Thomas Strønen"]},
                                      pf.default(), 100.0)
        self.assertNotIn("albumartists", changes)

    def test_NAM13_your_edit_of_the_album_artist_names_the_folder_and_the_names(self):
        prof = self.profile(operation="move", destination=str(self.dest),
                            naming={"preset": "custom", "template": SORTED + "/%title%"})
        tg = [target(i, f"T{i}", artist=FIVE[0], albumartist=list(FIVE), albumartists=list(FIVE)) for i in (1, 2)]
        edit = {"files": [str(self.f1), str(self.f2)], "tags": {"albumartist": ["Bill Bruford"]},
                "file_tags": {}, "cover": None}
        p = pl.build([self.proposal(targets=tg)], prof, edits={os.path.realpath(self.album): [edit]})
        a = p["albums"][0]
        dirs = {str(Path(fe["dst"]).parent.relative_to(self.dest)) for fe in a["files"]}
        self.assertEqual(dirs, {"Bill Bruford/Real Album"})
        for fe in a["files"]:
            self.assertEqual(fe["changes"].get("albumartists"), ["Bill Bruford"])
            self.assertEqual(fe["changes"].get("albumartist"), ["Bill Bruford"])


# NAM-1b: a band credited before its line-up ("Bill Bruford's Earthworks - Django Bates, Iain Ballamy,
# Bill Bruford, Mick Hutton", MusicBrainz release 2f5df193): the band, never the five names or a member
import album_artist as aa  # noqa: E402

CREDIT = "Bill Bruford’s Earthworks - Django Bates, Iain Ballamy, Bill Bruford, Mick Hutton"
IDS = ["band", "db", "ib", "bb", "mh"]
LEAD = {"album_artist": {"lead": True, "bands": True, "file_under": {}}}


def members(mbid):
    if mbid == "band":
        return aa.Members([("Bill Bruford", "Bruford, Bill", "bb"), ("Mick Hutton", "", "mh")], kind="group")
    return aa.Members([], kind="person")


class BandThenLineup(unittest.TestCase):
    def test_NAM1b_the_band_before_its_lineup_is_the_album_artist(self):
        for t in ({"albumartist": list(FIVE), "albumartists": list(FIVE), "musicbrainz_albumartistid": IDS,
                   "~albumartist_credit": [CREDIT]},
                  {"albumartist": [CREDIT], "albumartists": list(FIVE), "musicbrainz_albumartistid": IDS}):
            got = aa.choose(t, LEAD, members, lambda d: None)
            self.assertEqual(got[:3], ("Bill Bruford’s Earthworks", "", "band"))

    def test_NAM1b_no_member_credited_is_no_lineup(self):
        def none_of_them(mbid):
            return aa.Members([("Someone Else", "", "x")], kind="group") if mbid == "band" else aa.Members([])
        t = {"albumartist": [CREDIT], "albumartists": list(FIVE), "musicbrainz_albumartistid": IDS}
        self.assertFalse(aa.band_then_lineup(CREDIT, FIVE, IDS, [], none_of_them, None))
        self.assertNotEqual((aa.choose(t, LEAD, none_of_them, lambda d: None) or ("",))[0], "Bill Bruford’s Earthworks")

    def test_NAM1b_partners_and_guests_are_no_lineup(self):
        names = ["Pat Metheny", "Lyle Mays", "Nana Vasconcelos"]
        self.assertFalse(aa.band_then_lineup("Pat Metheny & Lyle Mays with Nana Vasconcelos", names))
        self.assertFalse(aa.band_then_lineup("Pat Metheny - Lyle Mays with Nana Vasconcelos", names))
        self.assertFalse(aa.band_then_lineup("Artist - Title", ["Artist", "Title"]))



# ALB-5: Agalloch - Ashes Against the Grain (a declipped mix): Discogs's six pressings tie, and so do
# MusicBrainz's - MusicBrainz's release comes along for the album's own fields
from unittest import mock  # noqa: E402

import match_albums as ma  # noqa: E402
import match_core as mc  # noqa: E402


def _res(source, rid, title="Ashes Against the Grain", n=8, status="review", **cand):
    c = dict(source=source, id=rid, title=title, tracks=[{}] * n, **cand)
    return {"status": status, "best": {"cand": c, "artist_sim": 1.0}}


class AlbumOnlyTwin(unittest.TestCase):
    TIED = {"tied": [{"source": "mb", "id": "m1"}, {"source": "mb", "id": "m2"}], "media": "", "no_lengths": False}

    def twin(self, final, mb_res):
        with mock.patch.object(mc, "album_only", return_value=dict(self.TIED)), \
                mock.patch.object(ma, "tie_source", return_value="cd"):
            return ma.album_only_twin(final, mb_res, {"tied": []}, {}, dict(mc.DEFAULT_CFG))

    def test_ALB5_musicbrainz_tied_pressings_of_the_same_album_come_along(self):
        got = self.twin(_res("discogs", "757872", master_id="12394"), _res("mb", "m1", discogs_master="12394"))
        self.assertEqual(got, {"mb_id": "m1", "tied": self.TIED["tied"]})

    def test_ALB5_another_album_or_a_sure_musicbrainz_pick_does_not(self):
        self.assertIsNone(self.twin(_res("discogs", "1"), _res("mb", "m1", title="The Mantle")))
        self.assertIsNone(self.twin(_res("discogs", "1"), _res("mb", "m1", n=9)))
        self.assertIsNone(self.twin(_res("discogs", "1", master_id="5"), _res("mb", "m1", discogs_master="6")))
        self.assertIsNone(self.twin(_res("discogs", "1"), _res("mb", "m1", status="auto")))
        self.assertIsNone(self.twin(_res("mb", "m0"), _res("mb", "m1")))


# DSC-2d: the joined set is compared and searched by the name every disc's album artist credits
class SetArtist(unittest.TestCase):
    def infos(self, *credits):
        return [(None, {"artist": c, "disc_eff": n + 1}) for n, c in enumerate(credits)]

    E1 = "Francesco Tristano, Chilly Gonzales, Alice Sara Ott; Alice Sara Ott"
    E2 = "Johann Sebastian Bach, Chilly Gonzales; Alice Sara Ott"

    def test_DSC2d_the_performer_the_folder_names_is_the_sets_artist(self):
        got = ma.set_artist(self.infos(self.E1, self.E2), "Alice Sara Ott - Echoes of Life - 2023")
        self.assertEqual(got, "Alice Sara Ott")

    def test_DSC2d_no_folder_artist_or_an_ordinary_album_gives_none(self):
        self.assertEqual(ma.set_artist(self.infos(self.E1, self.E2)), "")        # (two names shared)
        self.assertEqual(ma.set_artist(self.infos("Bob Marley & The Wailers", "Bob Marley"),
                                       "Bob Marley & The Wailers - Live"), "")
        self.assertEqual(ma.set_artist(self.infos("Queen", "ABBA"), "Queen - Hits"), "")
        self.assertEqual(ma.set_artist(self.infos("Queen", "Queen"), "Queen - Hits"), "")


class NamesAgreeReview(unittest.TestCase):
    def test_NAM13_names_in_any_script_and_any_hyphen_agree(self):
        self.assertTrue(naming.names_agree(["坂本龍一 & David Sylvian"], ["坂本龍一", "David Sylvian"]))
        self.assertTrue(naming.names_agree(["JAY-Z & Kanye West"], ["JAY\u2010Z", "Kanye West"]))
        self.assertTrue(naming.names_agree(["Wieland Kuijken, Piet Kuijken"],
                                           ["Wieland Kuijken (violoncello)", "Piet Kuijken (clavecin)"]))

    def test_NAM13_standardized_names_beside_a_credited_spelling_are_left(self):
        self.assertTrue(naming.names_agree(["Tchaikovsky; Ashkenazy"],
                                           ["Pyotr Ilyich Tchaikovsky", "Vladimir Ashkenazy"]))

    def test_NAM13_the_tag_value_scripts_read_is_left(self):
        c = naming.Ctx({"albumartist": ["Yelena Eckemoff"], "albumartists": THREE})
        self.assertEqual(c.tags["albumartists"], THREE)
        self.assertEqual(c.tags["_albumartists_sorted"], ["Yelena Eckemoff"])


# A Discogs track title repeated around " | " (Alice Sara Ott - Echoes of Life, Discogs 33658254)
class OnceTitle(unittest.TestCase):
    def test_a_title_repeated_around_a_bar_is_written_once(self):
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "plugins_builtin" / "discogs"))
        import discogs_mapping as dm
        self.assertEqual(dm.once_title("In The Beginning Was | In The Beginning Was"), "In The Beginning Was")
        self.assertEqual(dm.once_title("Part One | Part Two"), "Part One | Part Two")
        self.assertEqual(dm.extract_track_fields({"title": "A | a"}, 1, 1)["title"], "A")


# MOV-5b (decided 2026-11-20): an identified album scoring below organize_min_confidence is filed by
# default; Extra-careful matching still keeps it where it is
class FileBelowScore(Base):
    def dirs(self, **extra):
        d = {"name": "t", "sources": [{"path": str(self.lib)}], "operation": "move", "destination": str(self.dest)}
        d.update(extra)
        p = pl.build([self.proposal(conf=91.0)], pf.from_dict(d))
        return {str(Path(fe["dst"]).parent) for fe in p["albums"][0]["files"]}

    def test_MOV5b_an_album_below_the_score_is_filed_by_default(self):
        self.assertTrue(pf.DEFAULTS["file_below_score"])
        self.assertTrue(all(x.startswith(str(self.dest)) for x in self.dirs()), self.dirs())

    def test_MOV5b_switched_off_or_extra_careful_it_stays_where_it_is(self):
        for extra in ({"file_below_score": False}, {"matching": {"extra_careful": True}}):
            self.assertEqual(self.dirs(**extra), {str(self.album)}, extra)

    def test_MOV5b_a_partial_album_nobody_asked_for_is_not_moved(self):
        d = {"name": "t", "sources": [{"path": str(self.lib)}], "operation": "move", "destination": str(self.dest)}
        p = pl.build([self.proposal(conf=91.0, tier="partial", provider="web")], pf.from_dict(d))
        a = p["albums"][0]
        self.assertEqual({str(Path(fe["dst"]).parent) for fe in a["files"]}, {str(self.album)})

    def test_MOV5b_the_setting_must_be_true_or_false(self):
        self.assertIn("file_below_score must be true or false", pf.validate({"name": "t", "file_below_score": "yes"}))


if __name__ == "__main__":
    unittest.main()
