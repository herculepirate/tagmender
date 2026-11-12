# 0.8.42 review B: NAM-8 (a "featuring"/"with" credit names its lead; a person with a group no
# database lists members for leads it; MusicBrainz's answer stands through a Discogs outage),
# NAM-7 (initials keep their full stops as separators), WEB-2 (nested templates; a section on an
# earlier disc; an edition's bonus disc; LP sides from 1) and MOV-4 (discs as integers, a
# duplicate brings no new disc, a claim only once an album is placed). Synthetic data, no network.
import os as _os, tempfile as _tempfile  # noqa: E401
_os.environ.setdefault("XDG_CONFIG_HOME", _tempfile.mkdtemp(prefix="tm-config-"))
_os.environ.setdefault("XDG_STATE_HOME", _tempfile.mkdtemp(prefix="tm-state-"))
_os.environ.setdefault("TAGMENDER_TAGGER_INI", "/nonexistent")

import sys  # noqa: E402
import tempfile  # noqa: E402
import unittest  # noqa: E402
from pathlib import Path  # noqa: E402
from unittest import mock  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import album_artist as aa  # noqa: E402
import plan as pl  # noqa: E402
import profiles as pf  # noqa: E402
import web_tag as w  # noqa: E402

PROF = {"album_artist": {"lead": True, "bands": True, "file_under": {}}}
PERSON = lambda: aa.Members((), "person")        # noqa: E731
GROUP_NO_DATA = lambda: aa.Members((), "group")  # noqa: E731


def tags(names, ids, credit, dg=None):
    t = {"albumartist": [credit], "albumartists": names, "musicbrainz_albumartistid": ids}
    if dg:
        t["discogs_albumartist_id"] = dg
    return t


def mb(table):
    return lambda i: table.get(i)


class NAM8(unittest.TestCase):
    def test_NAM8_featuring_is_a_lead_credit(self):
        t = tags(["Robin Trower", "Sari Schorr"], ["rt", "ss"], "Robin Trower featuring Sari Schorr")
        got = aa.choose(t, PROF, mb({"rt": PERSON(), "ss": PERSON()}), lambda d: aa.NOT_ASKED)
        self.assertEqual(got[:3], ("Robin Trower", "", "rt"))
        for credit in ("A feat. B", "A ft. B", "A Feat B", "A with B", "A presents B", "A Introducing B",
                       "A (feat. B)"):
            got = aa.choose(tags(["A", "B"], ["a", "b"], credit), PROF, mb({"a": PERSON(), "b": PERSON()}))
            self.assertEqual(got[0], "A", credit)

    def test_NAM8_a_discogs_featuring_credit_too(self):
        t = {"albumartist": ["Robin Trower Featuring Sari Schorr"],
             "~discogs_albumartists": ["Robin Trower", "Sari Schorr"], "~discogs_albumartist_id": ["1", "2"]}
        got = aa.choose(t, PROF, None, lambda d: [])
        self.assertEqual(got[0], "Robin Trower")

    def test_NAM8_equal_joins_keep_the_whole_credit(self):
        two = mb({"a": PERSON(), "b": PERSON()})
        for credit in ("A & B", "A and B", "A, B", "A / B", "A x B", "A vs. B"):
            self.assertIsNone(aa.choose(tags(["A", "B"], ["a", "b"], credit), PROF, two), credit)
        # (a name holding a lead word is no join: "Withers" is not "with")
        self.assertIsNone(aa.choose(tags(["Bill Withers", "B"], ["a", "b"], "Bill Withers & B"), PROF, two))

    def test_NAM8_partners_before_a_guest_keep_the_whole_credit(self):
        # (review C: "Pat Metheny & Lyle Mays with Nana Vasconcelos" never cut to Pat Metheny)
        three = mb({"a": PERSON(), "b": PERSON(), "c": PERSON()})
        t = tags(["Pat Metheny", "Lyle Mays", "Nana Vasconcelos"], ["a", "b", "c"],
                 "Pat Metheny & Lyle Mays with Nana Vasconcelos")
        self.assertIsNone(aa.choose(t, PROF, three))
        t = tags(["A", "B", "C"], ["a", "b", "c"], "A feat. B & C")
        self.assertEqual(aa.choose(t, PROF, three)[0], "A")

    def test_NAM8_a_person_with_a_group_without_member_data_leads(self):
        t = tags(["Thomas Strønen", "Time Is A Blind Guide"], ["ts", "band"], "Thomas Strønen & Time Is A Blind Guide")
        got = aa.choose(t, PROF, mb({"ts": PERSON(), "band": GROUP_NO_DATA()}), lambda d: aa.NOT_ASKED)
        self.assertEqual(got[0], "Thomas Strønen")
        # (the first artist's kind unreachable: not known - the files keep their album artist)
        got = aa.choose(t, PROF, mb({"band": GROUP_NO_DATA()}), lambda d: aa.NOT_ASKED)
        self.assertEqual(got, aa.UNKNOWN)

    def test_NAM8_two_groups_with_known_members_and_two_persons_stay_whole(self):
        t = tags(["Calexico", "Iron & Wine"], ["c", "iw"], "Calexico and Iron & Wine")
        members = mb({"c": aa.Members([("Joey Burns", "", "jb")], "group"),
                      "iw": aa.Members([("Sam Beam", "", "sb")], "group")})
        self.assertIsNone(aa.choose(t, PROF, members))
        t = tags(["Paolo Fresu", "Omar Sosa"], ["pf", "os"], "Paolo Fresu & Omar Sosa")
        self.assertIsNone(aa.choose(t, PROF, mb({"pf": PERSON(), "os": PERSON()})))
        # (one group credit named after its equal members: leader_of keeps it whole)
        t = {"albumartist": ["Jimmy Page & Robert Plant"], "musicbrainz_albumartistid": ["pp"]}
        duo = mb({"pp": aa.Members([("Jimmy Page", "", "jp"), ("Robert Plant", "", "rp")], "group")})
        self.assertIsNone(aa.choose(t, PROF, duo))
        t = {"albumartist": ["Tom Petty and the Heartbreakers"], "musicbrainz_albumartistid": ["th"]}
        band = mb({"th": aa.Members([("Tom Petty", "Petty, Tom", "tp"), ("Mike Campbell", "", "mc")], "group")})
        self.assertEqual(aa.choose(t, PROF, band)[:3], ("Tom Petty", "Petty, Tom", "tp"))

    def test_NAM8_musicbrainz_answer_stands_when_discogs_is_down(self):
        t = tags(["Paolo Fresu", "Omar Sosa"], ["pf", "os"], "Paolo Fresu & Omar Sosa", dg=["1", "2"])
        self.assertIsNone(aa.choose(t, PROF, mb({"pf": PERSON(), "os": PERSON()}), lambda d: None))
        # (no database answered for the other artist: not known)
        self.assertEqual(aa.choose(t, PROF, lambda i: None, lambda d: None), aa.UNKNOWN)
        # (Discogs answers for the other act when MusicBrainz cannot be reached: the lead's band)
        got = aa._equals(["Lead", "Band"], ["l", "b"], ["1", "2"], lambda i: None,
                         lambda d: [("Lead", "", "", "1")] if d == "2" else [])
        self.assertIs(got, False)

    def test_NAM8_a_musicbrainz_404_is_not_known_not_a_person(self):
        class Http:
            def __init__(self, det):
                self.det = det

            def get_json(self, url, host):
                return self.det
        self.assertEqual(aa.band_members(Http(None), "x"), aa.NOT_ASKED)
        self.assertEqual(aa.band_members(Http("oops"), "x"), aa.NOT_ASKED)
        got = aa.band_members(Http({"type": "Person"}), "x")
        self.assertEqual((list(got), got.kind), ([], "person"))
        got = aa.band_members(Http({"type": "Group", "relations": []}), "x")
        self.assertEqual((list(got), got.kind), ([], "group"))
        got = aa.band_members(Http({"type": "Group", "relations": [
            {"type": "member of band", "direction": "backward", "artist": {"name": "M", "id": "m"}}]}), "x")
        self.assertEqual((list(got), got.kind), ([("M", "", "m")], "group"))


class NAM7(unittest.TestCase):
    def test_NAM7_initials_keep_their_stops_as_separators(self):
        k = lambda s: pl._loose_key(s, True)   # noqa: E731
        self.assertEqual(k("M.I.A."), k("M I A"))
        self.assertEqual(k("M.I.A."), k("M.I.A"))
        self.assertNotEqual(k("M.I.A."), k("MIA."))
        self.assertNotEqual(k("M.I.A."), k("MIA"))
        self.assertEqual(k("MIA."), k("MIA"))
        self.assertNotEqual(k("t.A.T.u."), k("Tatu"))
        self.assertEqual(k("Mr. Mister"), k("Mr Mister"))
        self.assertEqual(k("Dr. Dre"), k("Dr Dre"))
        self.assertEqual(k("Blood, Sweat & Tears"), k("Blood Sweat And Tears"))

    def test_NAM7_mia_and_m_i_a_are_two_folders(self):
        root = Path(tempfile.mkdtemp(prefix="tm-nam7b-"))
        for n in ("2005 - Arular", "2007 - Kala"):
            (root / "M.I.A." / n).mkdir(parents=True)
        for compat in ("windows", "linux"):
            prof = pf.default()
            prof["filenames"]["compat"] = compat
            got = pl.Planner(prof)._spelled(root / "MIA" / "2004 - X" / "01.flac", root, artist_level=True)
            self.assertEqual(got.parent.parent.name, "MIA", compat)
            got = pl.Planner(prof)._spelled(root / "M I A" / "2004 - X" / "01.flac", root, artist_level=True)
            self.assertEqual(got.parent.parent.name, "M.I.A.", compat)


def B(ns, head=""):
    return (ns[0], [(n, f"T{n}", 60.0) for n in ns], head)


class WEB2(unittest.TestCase):
    def test_WEB2_nested_and_display_templates(self):
        self.assertEqual(w._unwrap_templates("{{nowrap|{{lang|fr|Chanson}} d'amour}}"), "Chanson d'amour")
        self.assertEqual(w._unwrap_templates("Opus {{abbr|No.|Number}} 1"), "Opus No. 1")
        self.assertEqual(w._unwrap_templates("{{nihongo|Snow|雪|yuki}}"), "Snow")
        self.assertEqual(w._unwrap_templates("{{lang|ja|雪}}"), "雪")
        self.assertEqual(w._unwrap_templates("{{ruby|雪|ゆき}} Song"), "雪 Song")
        self.assertEqual(w._unwrap_templates("Song{{r|ref1}}"), "Song")
        self.assertEqual(w._unwrap_templates("Song{{R|ref1|p=3}}"), "Song")
        self.assertEqual(w._unwrap_templates("Overture{{rp|4}}"), "Overture")       # (review C)
        self.assertEqual(w._unwrap_templates("Song{{sfnp|Smith|2001}}"), "Song")
        text = ("{{Track listing\n| title1 = {{nowrap|{{lang|fr|Chanson}} d'amour}}\n| length1 = 3:00\n"
                "| title2 = [[A (song)|A]] {{abbr|No.|Number}} 2\n| length2 = 4:00\n}}")
        blocks = w._wiki_blocks(text)
        self.assertEqual([(n, t, s) for n, t, s in blocks[0][1]], [(1, "Chanson d'amour", 180.0), (2, "A No. 2", 240.0)])

    def test_WEB2_a_section_continuing_an_earlier_disc_goes_on_it(self):
        eds = w._wiki_editions([B([1, 2], "Disc one"), B([3], "Disc one bonus tracks (2010 reissue)"),
                                B([1, 2, 3], "Disc two")])
        self.assertEqual([[(d, n) for d, n, _t, _s in e] for e in eds],
                         [[(1, 1), (1, 2), (2, 1), (2, 2), (2, 3)],
                          [(1, 1), (1, 2), (1, 3), (2, 1), (2, 2), (2, 3)]])

    def test_WEB2_an_editions_bonus_disc_leaves_a_standard_candidate(self):
        for head in ("Deluxe edition bonus disc", "Bonus disc", "2010 reissue"):
            eds = w._wiki_editions([B([1, 2, 3]), B([1, 2], head)])
            self.assertEqual([sorted({d for d, *_x in e}) for e in eds], [[1], [1, 2]], head)
            self.assertEqual(len(eds[1]), 5)
        # (a real second disc stays one edition)
        for head in ("Disc two", "CD 2"):
            eds = w._wiki_editions([B([1, 2, 3], "Disc one"), B([1, 2], head)])
            self.assertEqual([len(e) for e in eds], [5], head)

    def test_WEB2_lp_sides_numbered_from_one_are_one_disc(self):
        eds = w._wiki_editions([B([1, 2], "Side one"), B([1, 2], "Side two")])
        self.assertEqual([[(d, n) for d, n, _t, _s in e] for e in eds], [[(1, 1), (1, 2), (1, 3), (1, 4)]])
        # (a double LP: sides three and four are the second disc)
        eds = w._wiki_editions([B([1, 2], "Side one"), B([1, 2], "Side two"), B([1, 2], "Side three"),
                                B([1, 2], "Side four")])
        self.assertEqual([(d, n) for d, n, _t, _s in eds[0]],
                         [(1, 1), (1, 2), (1, 3), (1, 4), (2, 1), (2, 2), (2, 3), (2, 4)])
        eds = w._wiki_editions([B([1, 2], "Side A"), B([1, 2], "Side B")])
        self.assertEqual(len(eds), 1)
        self.assertEqual(len(eds[0]), 4)


class MOV4(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="tm-mov4b-")).resolve()
        self.filed = self.root / "Artist" / "2013 - Set"
        (self.filed / "Disc 01").mkdir(parents=True)
        (self.filed / "Disc 01" / "101.flac").write_bytes(b"x")
        self.other = self.root / "Artist" / "2013 - Set copy"
        self.other.mkdir(parents=True)

    def prop(self, *discs):
        return pl.Proposal(source_dir=str(self.other), files=[str(self.other / "x.flac")], status="identified",
                           tier="exact", targets=[{"musicbrainz_albumid": ["rel"], "discnumber": [d], "album": ["Set"]}
                                                  for d in discs])

    def test_MOV4_disc_numbers_are_integers(self):
        self.assertEqual(pl._prop_discs(self.prop("01", "1/2", "02")), {1, 2})
        self.assertEqual(pl._prop_discs(self.prop("A", "B")), {1})          # (non-numeric: ignored)
        tagged = {"musicbrainz_albumid": ["rel"], "discnumber": ["01"]}
        with mock.patch.object(pl.tagio, "read", lambda p: tagged), \
                mock.patch.object(pl, "_ids_of", lambda t: {"mb:" + t["musicbrainz_albumid"][0]}):
            self.assertTrue(pl._same_release_there(self.filed, frozenset({"mb:rel"}), set(), {1}))
            self.assertTrue(pl._same_release_there(self.filed, frozenset({"mb:rel"}), set(), {"1"}))

    def test_MOV4_a_full_set_is_not_held_where_the_library_has_disc_one(self):
        tagged = {"musicbrainz_albumid": ["rel"], "discnumber": ["1"]}
        with mock.patch.object(pl.tagio, "read", lambda p: tagged), \
                mock.patch.object(pl, "_ids_of", lambda t: {"mb:" + t["musicbrainz_albumid"][0]}):
            self.assertFalse(pl._same_release_there(self.filed, frozenset({"mb:rel"}), set(), {1, 2}))
            planner = pl.Planner(pf.default())
            dst = self.filed / "Disc 01" / "101.flac"
            self.assertEqual(planner._own_album_dir(dst, self.prop("1", "2"), [], {}), dst)   # (joins: BOX-2)
            with self.assertRaises(pl._DuplicateCopy) as got:
                pl.Planner(pf.default())._own_album_dir(dst, self.prop("01"), [], {})
            self.assertIn("is filed in", str(got.exception))

    def test_MOV4_a_plan_claim_covers_only_its_discs_and_says_planned(self):
        planner = pl.Planner(pf.default())
        new = self.root / "Artist" / "2013 - New"
        a = pl.Proposal(source_dir=str(self.root / "inA"), files=[str(self.root / "inA" / "1.flac")], status="identified",
                        tier="exact", targets=[{"musicbrainz_albumid": ["rel2"], "discnumber": ["1"], "album": ["New"]}])
        dst = new / "101.flac"
        with mock.patch.object(pl, "_ids_of", lambda t: {"mb:" + t["musicbrainz_albumid"][0]}):
            planner._own_album_dir(dst, a, [], {})
            full = pl.Proposal(source_dir=str(self.root / "inB"), files=[str(self.root / "inB" / "1.flac")],
                               status="identified", tier="exact",
                               targets=[{"musicbrainz_albumid": ["rel2"], "discnumber": [d], "album": ["New"]}
                                        for d in ("1", "2")])
            self.assertEqual(planner._own_album_dir(dst, full, [], {}), dst)     # (brings disc 2: joins)
            again = pl.Proposal(source_dir=str(self.root / "inC"), files=[str(self.root / "inC" / "1.flac")],
                                status="identified", tier="exact",
                                targets=[{"musicbrainz_albumid": ["rel2"], "discnumber": ["01"], "album": ["New"]}])
            with self.assertRaises(pl._DuplicateCopy) as got:
                planner._own_album_dir(dst, again, [], {})
            self.assertIn("planned to be filed in", str(got.exception))
            # (an album that ends up not placed gives its claims up)
            planner._unclaim_album_dirs(a)
            planner._unclaim_album_dirs(full)
            self.assertEqual(planner._own_album_dir(dst, again, [], {}), dst)


if __name__ == "__main__":
    unittest.main(verbosity=2)
