"""Offline tests for Picard's preferred release types and medium formats (M5):

- tagger_settings.matching_preferences reads preferred_release_types, discouraged_release_types
  and preferred_release_formats from Picard.ini (one name in Qt's binary form, a list,
  @Invalid() for none, an enabled option profile), Picard 2's release_type_scores converted as
  Picard 3 converts them, and a profile's "picard" overrides.
- match_core uses them only as the last tie-break between pressings nothing else told apart,
  after the country order (choose_pressing, combine), and only between pressings known to be
  one album (one MusicBrainz release group or Discogs master): never a score, never automatic,
  never between different albums (across the two databases: the release group's Discogs master
  link). Formats are compared by name only when every tied entry is a MusicBrainz release that
  names them, else by kind, and a kind never stands for a sub-format (SACD is not "a CD", a 7"
  not '12" Vinyl'), on either side. A discouraged type is never picked.
- With an ini like the user's (preferred_release_formats=@Invalid(), no release_type_scores,
  preferred_release_types "Other"), the matcher picks exactly what it picked before.

Synthetic data only; no Picard.ini of this machine is read.
Run:  python3 tests/test_m5_release_prefs.py [plugin-dir]
"""
import os as _os, tempfile as _tempfile  # noqa: E402
_os.environ["XDG_CONFIG_HOME"] = _tempfile.mkdtemp(prefix="tm-config-")
_os.environ["XDG_STATE_HOME"] = _tempfile.mkdtemp(prefix="tm-state-")
_os.environ["TAGMENDER_TAGGER_INI"] = "/nonexistent"

import copy
import pickle
import struct
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
if len(sys.argv) > 1 and not sys.argv[1].startswith("-"):
    sys.argv.pop(1)          # the plugin folder: not needed here

import match_core as mc  # noqa: E402
import tagger_settings as pc  # noqa: E402

LENS = (301.0, 255.0, 412.0, 198.0)
# The user's Picard.ini lines for Preferred Releases (Picard 3.0.2; no release_type_scores)
USER_LIKE_INI = ('[application]\nversion=3.0.2.final0\n\n[setting]\n'
                 'discouraged_release_types=@Invalid()\n'
                 'preferred_release_countries=@Invalid()\n'
                 'preferred_release_formats=@Invalid()\n'
                 'preferred_release_types=@Variant(\\0\\0\\0\\t\\0\\0\\0\\x1\\0\\0\\0\\n\\0\\0\\0\\n'
                 '\\0O\\0t\\0h\\0\\x65\\0r)\n')


def write_ini(text):
    p = Path(_tempfile.mkdtemp(prefix="tm-ini-")) / "Picard.ini"
    p.write_text(text)
    pc._cache.clear()
    return p


def qt_escape(b: bytes) -> str:
    return "".join(f"\\x{x:x}" for x in b)


def pyqt5_pairs(pairs) -> str:
    """A list of Python tuples as PyQt5's QSettings writes it: a QVariantList of PyQt_PyObject
    pickles (Picard 2's release_type_scores)."""
    out = struct.pack(">IBI", 9, 0, len(pairs))
    for pair in pairs:
        data = pickle.dumps(tuple(pair), protocol=2)
        name = b"PyQt_PyObject\0"
        out += struct.pack(">IBI", 127, 0, len(name)) + name + struct.pack(">I", len(data)) + data
    return "@Variant(" + qt_escape(out) + ")"


def qt_variant(v) -> bytes:
    """One value as Qt's QDataStream writes a QVariant (QSettings' @Variant form): bool,
    int, double, text, list of texts (QStringList), list, map."""
    def qstr(t):
        b = t.encode("utf-16-be")
        return struct.pack(">I", len(b)) + b
    if isinstance(v, bool):
        return struct.pack(">IBB", 1, 0, int(v))
    if isinstance(v, int):
        return struct.pack(">IBi", 2, 0, v)
    if isinstance(v, float):
        return struct.pack(">IBd", 6, 0, v)
    if isinstance(v, str):
        return struct.pack(">IB", 10, 0) + qstr(v)
    if isinstance(v, list) and all(isinstance(x, str) for x in v):
        return struct.pack(">IBI", 11, 0, len(v)) + b"".join(qstr(x) for x in v)
    if isinstance(v, list):
        return struct.pack(">IBI", 9, 0, len(v)) + b"".join(qt_variant(x) for x in v)
    return struct.pack(">IBI", 8, 0, len(v)) + b"".join(qstr(k) + qt_variant(x) for k, x in v.items())


def profile_ini(settings: dict, setting_lines: str = "") -> str:
    """A Picard 3 Picard.ini with one enabled option profile holding these settings."""
    pid = "3f0d6a49-7b8e-4b0c-9a56-3a1d8f0f2c11"
    prof = {"enabled": True, "id": pid, "position": 0, "title": "Mine"}
    return ("[application]\nversion=3.0.2.final0\n\n[profiles]\n"
            f"user_profile_settings=@Variant({qt_escape(qt_variant({pid: settings}))})\n"
            f"user_profiles=@Variant({qt_escape(qt_variant(prof))})\n\n[setting]\n" + setting_lines)


def album(**kw):
    a = {"artist": "Ahab", "title": "The Divinity of Oceans", "year": 2009, "discs": 1,
         "tracks": [{"disc": 1, "num": i + 1, "title": f"T{i}", "secs": s} for i, s in enumerate(LENS)],
         "barcodes": set(), "catnos": set()}
    a.update(kw)
    return a


def cand(**kw):
    c = {"source": "mb", "id": "x1", "url": "u", "artist": "Ahab", "title": "The Divinity of Oceans",
         "year": 2009, "date": "2009", "country": "DE", "label": "Napalm", "catnos": set(),
         "barcodes": set(), "disc_count": 1, "formats": {"cd"}, "release_group": "rg-ahab",
         "tracks": [{"disc": 1, "title": f"T{i}", "secs": s} for i, s in enumerate(LENS)]}
    c.update(kw)
    return c


def cfg_with(**kw):
    return dict(mc.DEFAULT_CFG, preferred_countries=["CA", "US"], **kw)


def run(a, *cands, cfg):
    scored = [mc.score_candidate(a, c, cfg) for c in copy.deepcopy(cands)]
    r = mc.classify(scored, cfg)
    r["scored"] = scored
    return r


class ReadingPicardIni(unittest.TestCase):
    def test_user_like_ini(self):
        ini = write_ini(USER_LIKE_INI)
        self.assertEqual(pc.matching_preferences(path=ini),
                         {"preferred_release_types": ["Other"], "discouraged_release_types": [],
                          "preferred_release_formats": []})
        # no countries either: TagMender's own order stays (match_albums.picard_preferred_countries)
        self.assertEqual(pc.tag_settings(path=ini)["preferred_release_countries"], [])

    def test_no_ini_is_picards_default(self):
        self.assertEqual(pc.matching_preferences(path=Path("/nonexistent/Picard.ini")), pc.MATCH_DEFAULTS)

    def test_lists(self):
        ini = write_ini('[application]\nversion=3.0.2.final0\n\n[setting]\n'
                        'preferred_release_formats=CD, Digital Media, "12\\" Vinyl"\n'
                        'preferred_release_types=Album, EP\ndiscouraged_release_types=Live, Compilation\n')
        self.assertEqual(pc.matching_preferences(path=ini),
                         {"preferred_release_types": ["Album", "EP"],
                          "discouraged_release_types": ["Live", "Compilation"],
                          "preferred_release_formats": ["CD", "Digital Media", '12" Vinyl']})

    def test_picard2_release_type_scores(self):
        scores = [("Album", 0.9), ("Single", 0.5), ("EP", 0.7), ("Compilation", 0.0), ("Live", 0.2)]
        ini = write_ini('[application]\nversion=2.9.2\n\n[setting]\nrelease_type_scores='
                        + ", ".join(pyqt5_pairs([p]) for p in scores) + "\n")
        got = pc.matching_preferences(path=ini)
        self.assertEqual(got["preferred_release_types"], ["Album", "EP"])
        self.assertEqual(got["discouraged_release_types"], ["Compilation", "Live"])
        # Picard 2's default (every type 0.5) is no preference
        ini = write_ini('[application]\nversion=2.9.2\n\n[setting]\nrelease_type_scores='
                        + pyqt5_pairs([("Album", 0.5), ("Other", 0.5)]) + "\n")
        self.assertEqual(pc.matching_preferences(path=ini), pc.MATCH_DEFAULTS)
        # Picard 2's own form: ONE QVariantList holding every pair
        ini = write_ini('[setting]\nrelease_type_scores='
                        + pyqt5_pairs([("Album", 0.9), ("EP", 0.7), ("Live", 0.1)]) + "\n")
        got = pc.matching_preferences(path=ini)
        self.assertEqual((got["preferred_release_types"], got["discouraged_release_types"]),
                         (["Album", "EP"], ["Live"]))
        self.assertEqual(pc._release_type_scores(pyqt5_pairs([("Album", 0.5), ("Other", 0.5)])),
                         [("Album", 0.5), ("Other", 0.5)])
        # the old text form
        ini = write_ini('[setting]\nrelease_type_scores=Album 0.9 Single 0.5 Compilation 0.1\n')
        got = pc.matching_preferences(path=ini)
        self.assertEqual((got["preferred_release_types"], got["discouraged_release_types"]),
                         (["Album"], ["Compilation"]))

    def test_picard3_lists_win_over_old_scores(self):
        ini = write_ini('[application]\nversion=3.0.2.final0\n\n[setting]\npreferred_release_types=EP\n'
                        'release_type_scores=Album 0.9\n')
        self.assertEqual(pc.matching_preferences(path=ini)["preferred_release_types"], ["EP"])

    def test_formats_alone_still_read_old_scores(self):
        # a format list on its own says nothing about types: Picard 2's scores still count
        ini = write_ini('[setting]\npreferred_release_formats=CD\nrelease_type_scores=Album 0.9 Live 0.1\n')
        self.assertEqual(pc.matching_preferences(path=ini),
                         {"preferred_release_types": ["Album"], "discouraged_release_types": ["Live"],
                          "preferred_release_formats": ["CD"]})

    def test_bad_values_are_no_names(self):
        for raw in (5, 0.5, None, {"Album": 1}):
            self.assertEqual(pc._release_type_scores(raw), [])
        self.assertEqual(pc._names(["CD", None, 5, {"x": 1}, " Vinyl "]), ["CD", "Vinyl"])

    def test_option_profile(self):
        # an enabled option profile wins over [setting]'s @Invalid()
        ini = write_ini(profile_ini({"preferred_release_formats": ["CD"]},
                                    "preferred_release_formats=@Invalid()\n"))
        self.assertEqual(pc.matching_preferences(path=ini)["preferred_release_formats"], ["CD"])
        # a profile's preferred_release_types: Picard 2's release_type_scores is not read
        ini = write_ini(profile_ini({"preferred_release_types": ["EP"]},
                                    "release_type_scores=Album 0.9 Live 0.1\n"))
        got = pc.matching_preferences(path=ini)
        self.assertEqual((got["preferred_release_types"], got["discouraged_release_types"]), (["EP"], []))
        # a profile's release_type_scores, when nothing sets the lists
        ini = write_ini(profile_ini({"release_type_scores": [["Album", 0.9], ["Live", 0.1]]}))
        got = pc.matching_preferences(path=ini)
        self.assertEqual((got["preferred_release_types"], got["discouraged_release_types"]), (["Album"], ["Live"]))

    def test_follow_picard_off(self):
        ini = write_ini(USER_LIKE_INI)
        self.assertEqual(pc.matching_preferences(path=ini, follow_picard=False), pc.MATCH_DEFAULTS)
        got = pc.matching_preferences({"preferred_release_formats": ["CD"]}, path=ini, follow_picard=False)
        self.assertEqual(got, dict(pc.MATCH_DEFAULTS, preferred_release_formats=["CD"]))

    def test_profile_overrides(self):
        ini = write_ini(USER_LIKE_INI)
        got = pc.matching_preferences({"preferred_release_formats": ["Digital Media"]}, path=ini)
        self.assertEqual(got["preferred_release_formats"], ["Digital Media"])
        self.assertEqual(got["preferred_release_types"], ["Other"])
        self.assertEqual(pc.validate_tag_settings({"preferred_release_formats": ["CD"],
                                                   "discouraged_release_types": []}), [])
        self.assertTrue(pc.validate_tag_settings({"preferred_release_types": "Album"}))


class TieBreak(unittest.TestCase):
    def pair(self):
        return cand(id="cd1"), cand(id="lp1", formats={"vinyl"})

    def test_format_breaks_a_tie_after_the_country_order(self):
        a = album()
        cfg = cfg_with(preferred_release_formats=["Vinyl", "CD"])
        tied = [mc.score_candidate(a, c, cfg) for c in self.pair()]
        self.assertIsNone(mc.choose_pressing(tied, ["CA", "US"]))                 # before: undecided
        pick = mc.choose_pressing(tied, ["CA", "US"], mc.release_preferences(cfg))
        self.assertEqual(pick[0]["cand"]["id"], "lp1")
        self.assertIn("format preference", pick[1])
        # a sized vinyl by kind: unknown (the vinyl may be a 7")
        cfg = cfg_with(preferred_release_formats=['12" Vinyl', "CD"])
        tied = [mc.score_candidate(a, c, cfg) for c in self.pair()]
        self.assertIsNone(mc.choose_pressing(tied, ["CA", "US"], mc.release_preferences(cfg)))
        # Picard's own format names per medium, when the entry carries them
        cd, lp = cand(id="cd1", media_formats=["CD"]), cand(id="lp1", formats={"vinyl"}, media_formats=['12" Vinyl'])
        tied = [mc.score_candidate(a, c, cfg) for c in (cd, lp)]
        self.assertEqual(mc.choose_pressing(tied, ["US"], mc.release_preferences(cfg))[0]["cand"]["id"], "lp1")

    def test_country_order_comes_first(self):
        a = album()
        cfg = cfg_with(preferred_release_formats=['12" Vinyl'])
        tied = [mc.score_candidate(a, c, cfg) for c in (cand(id="cd1", country="US"),
                                                         cand(id="lp1", formats={"vinyl"}))]
        pick = mc.choose_pressing(tied, ["CA", "US"], mc.release_preferences(cfg))
        self.assertEqual((pick[0]["cand"]["id"], list(pick[1])), ("cd1", ["country preference"]))

    def test_release_types(self):
        a = album()
        cfg = cfg_with(discouraged_release_types=["Compilation"])
        tied = [mc.score_candidate(a, c, cfg) for c in (cand(id="c", release_types=["Album", "Compilation"]),
                                                         cand(id="a", release_types=["Album"]))]
        pick = mc.choose_pressing(tied, ["US"], mc.release_preferences(cfg))
        self.assertEqual((pick[0]["cand"]["id"], list(pick[1])), ("a", ["release type preference"]))
        # types come before formats (Picard weighs them more)
        cfg = cfg_with(preferred_release_types=["EP", "Album"], preferred_release_formats=["CD"])
        tied = [mc.score_candidate(a, c, cfg) for c in (cand(id="cd", release_types=["Album"]),
                                                         cand(id="ep", release_types=["EP"], formats={"vinyl"}))]
        self.assertEqual(mc.choose_pressing(tied, ["US"], mc.release_preferences(cfg))[0]["cand"]["id"], "ep")

    def test_unknown_or_equal_decides_nothing(self):
        a = album()
        prefs = mc.release_preferences(cfg_with(preferred_release_types=["Other"],
                                                preferred_release_formats=["Vinyl"]))
        cfg = cfg_with()
        for cands in ((cand(id="1"), cand(id="2", formats=set())),                     # one lists no format
                      (cand(id="1", release_types=["Album"]), cand(id="2", formats={"cd"}, release_types=["Album"])),
                      (cand(id="1"), cand(id="2"))):
            tied = [mc.score_candidate(a, c, cfg) for c in cands]
            self.assertIsNone(mc.choose_pressing(tied, ["US"], prefs))

    def test_never_between_tied_albums(self):
        # two albums that tie on score (same tracks): Album vs Compilation, a studio album
        # vs a Live one; the preference never picks between them
        a = album()
        for prefs_cfg, x, y in (
                (dict(preferred_release_types=["Compilation"]),
                 dict(id="studio", release_types=["Album"]),
                 dict(id="comp", release_types=["Album", "Compilation"], release_group="rg-best-of")),
                (dict(preferred_release_formats=["Vinyl"]),
                 dict(id="cd1"), dict(id="lp1", formats={"vinyl"}, release_group=None, master_id="m1")),
                (dict(preferred_release_formats=["Vinyl"]),       # neither says which album it is
                 dict(id="cd1", release_group=None), dict(id="lp1", formats={"vinyl"}, release_group=None))):
            cfg = cfg_with(**prefs_cfg)
            tied = [mc.score_candidate(a, c, cfg) for c in (cand(**x), cand(**y))]
            self.assertIsNone(mc.choose_pressing(tied, ["US"], mc.release_preferences(cfg)))
            self.assertEqual(run(a, cand(**x), cand(**y), cfg=cfg)["best"]["cand"]["id"],
                             run(a, cand(**x), cand(**y), cfg=cfg_with())["best"]["cand"]["id"])
        # one Discogs master is one album too
        cfg = cfg_with(preferred_release_formats=["Vinyl"])
        tied = [mc.score_candidate(a, c, cfg) for c in (cand(id="cd1", release_group=None, master_id="m1"),
                                                         cand(id="lp1", formats={"vinyl"}, release_group=None,
                                                              master_id="m1"))]
        self.assertEqual(mc.choose_pressing(tied, ["US"], mc.release_preferences(cfg))[0]["cand"]["id"], "lp1")

    def test_sub_formats_are_unknown_by_kind(self):
        # a kind cannot tell a plain CD from an SACD: with SACD preferred over vinyl, a CD
        # pressing is unknown, never "the SACD"
        for pref in (["SACD", "Vinyl"], ["SACD", '12" Vinyl'], ["Hybrid SACD", "CD", "Vinyl"],
                     ['7" Vinyl', "CD", '12" Vinyl']):
            self.assertIsNone(mc.release_format_score({"formats": {"cd"}} if "SACD" in pref[0]
                                                      else {"formats": {"vinyl"}}, pref))
        self.assertIsNone(mc.release_format_score({"formats": {"cd"}}, ["SACD", "Vinyl"]))
        self.assertEqual(mc.release_format_score({"formats": {"vinyl"}}, ["SACD", "Vinyl"]), 0.5)
        self.assertEqual(mc.release_format_score({"formats": {"digital"}}, ["CD", "Vinyl"]), 0.0)
        self.assertIsNone(mc.release_format_score({"formats": {"cd", "vinyl"}}, ["CD", "Vinyl"]))
        a = album()
        cfg = cfg_with(preferred_release_formats=["SACD", "Vinyl"])
        tied = [mc.score_candidate(a, c, cfg) for c in self.pair()]
        self.assertIsNone(mc.choose_pressing(tied, ["US"], mc.release_preferences(cfg)))
        # by name, an SACD is told from a CD
        tied = [mc.score_candidate(a, c, cfg) for c in (cand(id="cd1", media_formats=["CD"]),
                                                         cand(id="sacd", media_formats=["SACD"]))]
        self.assertEqual(mc.choose_pressing(tied, ["US"], mc.release_preferences(cfg))[0]["cand"]["id"], "sacd")

    def test_a_pressing_naming_a_sub_format_is_unknown_by_kind(self):
        # an SACD is no "CD" and a 7" no '12" Vinyl' (Picard scores both 0 by name)
        self.assertIsNone(mc.release_format_score({"formats": {"cd"}, "media_formats": ["SACD"]},
                                                  ["CD", "Vinyl"], by_name=False))
        self.assertIsNone(mc.release_format_score({"formats": {"vinyl"}, "media_formats": ['7" Vinyl']},
                                                  ["Vinyl", "CD"], by_name=False))
        # a kind that is not preferred scores 0 whatever its sub-format
        self.assertEqual(mc.release_format_score({"formats": {"cd"}, "media_formats": ["SACD"]},
                                                 ["Vinyl"], by_name=False), 0.0)
        # Discogs's generic names
        self.assertEqual(mc.release_format_score({"formats": {"digital"}, "media_formats": ["File"]},
                                                 ["Digital Media", "CD"], by_name=False), 1.0)
        self.assertIsNone(mc.release_format_score({"formats": {"vinyl"}}, ['12" Vinyl', "CD"]))
        a = album()
        for pref, x, y in ((["CD", "Vinyl"], dict(id="sacd", media_formats=["SACD"]),
                            dict(id="lp1", formats={"vinyl"})),
                           (["CD"], dict(id="sacd", media_formats=["SACD"]),
                            dict(id="lp1", formats={"vinyl"})),
                           (["CD", "Vinyl"], dict(id="sacd", media_formats=["SACD"]),     # mixed sources
                            dict(id="lp1", formats={"vinyl"}, media_formats=['12" Vinyl'], source="discogs",
                                 release_group=None, master_id="m1", )),
                           (['12" Vinyl', "CD"], dict(id="single", formats={"vinyl"}, media_formats=['7" Vinyl']),
                            dict(id="cd1"))):
            cfg = cfg_with(preferred_release_formats=pref)
            if y.get("source") == "discogs":
                x = dict(x, discogs_master="m1")
            tied = [mc.score_candidate(a, c, cfg) for c in (cand(**x), cand(**y))]
            self.assertIsNone(mc.choose_pressing(tied, ["US"], mc.release_preferences(cfg)), (pref, x["id"]))

    def test_names_only_between_musicbrainz_pressings(self):
        # Discogs names a format "Vinyl" (the size in its descriptions), Picard's setting uses
        # MusicBrainz's '12" Vinyl': two Discogs pressings are compared by kind
        a = album()
        cfg = cfg_with(preferred_release_formats=['12" Vinyl', "CD"])
        dg = dict(source="discogs", release_group=None, master_id="m1")
        tied = [mc.score_candidate(a, c, cfg) for c in (cand(id="lp1", formats={"vinyl"}, media_formats=["Vinyl"], **dg),
                                                         cand(id="cd1", media_formats=["CD"], **dg))]
        self.assertIsNone(mc.choose_pressing(tied, ["US"], mc.release_preferences(cfg)))   # never "the CD"
        cfg = cfg_with(preferred_release_formats=["Vinyl", "CD"])
        tied = [mc.score_candidate(a, c, cfg) for c in (cand(id="lp1", formats={"vinyl"}, media_formats=["Vinyl"], **dg),
                                                         cand(id="cd1", media_formats=["CD"], **dg))]
        self.assertEqual(mc.choose_pressing(tied, ["US"], mc.release_preferences(cfg))[0]["cand"]["id"], "lp1")
        # one from each database, both vinyl, named differently: by kind, still tied
        cfg = cfg_with(preferred_release_formats=["Vinyl", '12" Vinyl', "CD"])
        tied = [mc.score_candidate(a, c, cfg) for c in (
            cand(id="mb-lp", formats={"vinyl"}, media_formats=['12" Vinyl'], release_group=None, master_id="m1"),
            cand(id="dg-lp", formats={"vinyl"}, media_formats=["Vinyl"], **dg))]
        self.assertIsNone(mc.choose_pressing(tied, ["US"], mc.release_preferences(cfg)))

    def test_discouraged_never_wins_on_format(self):
        # Picard skips a discouraged type; another pressing's unknown types do not bring it back
        a = album()
        dg = dict(source="discogs", release_group=None, master_id="m1")
        cfg = cfg_with(discouraged_release_types=["Compilation"], preferred_release_formats=["Vinyl"])
        for other in (dict(id="b"), dict(id="b", release_types=["Album"]),
                      dict(id="b", release_types=["Compilation"])):
            tied = [mc.score_candidate(a, c, cfg) for c in (
                cand(id="comp", formats={"vinyl"}, release_types=["Compilation"], **dg), cand(**dict(dg, **other)))]
            pick = mc.choose_pressing(tied, ["US"], mc.release_preferences(cfg))
            self.assertTrue(pick is None or pick[0]["cand"]["id"] == "b", other)
        # two of an allowed type tied on type, a discouraged vinyl beside them: the formats of
        # the allowed two decide
        cfg = cfg_with(discouraged_release_types=["Live"], preferred_release_formats=["Vinyl", "CD"])
        tied = [mc.score_candidate(a, c, cfg) for c in (
            cand(id="live", formats={"vinyl"}, release_types=["Live"], **dg),
            cand(id="cd1", release_types=["Album"], **dg),
            cand(id="dig", formats={"digital"}, release_types=["Album"], **dg))]
        self.assertEqual(mc.choose_pressing(tied, ["US"], mc.release_preferences(cfg))[0]["cand"]["id"], "cd1")

    def test_one_scale_for_the_whole_tie(self):
        # one entry with Discogs's name "Vinyl", one with only its kind: both are compared
        # by kind, so two vinyl pressings stay tied
        a = album()
        cfg = cfg_with(preferred_release_formats=["CD", '12" Vinyl'])
        tied = [mc.score_candidate(a, c, cfg) for c in (cand(id="v1", formats={"vinyl"}, media_formats=["Vinyl"]),
                                                         cand(id="v2", formats={"vinyl"}))]
        self.assertIsNone(mc.choose_pressing(tied, ["US"], mc.release_preferences(cfg)))

    def test_no_country_order_no_preference(self):
        # match_albums switches the country order off until every kind of evidence was tried;
        # the types and formats wait with it
        a = album()
        cfg = cfg_with(preferred_release_formats=["Vinyl"])
        tied = [mc.score_candidate(a, c, cfg) for c in self.pair()]
        self.assertIsNone(mc.choose_pressing(tied, [], mc.release_preferences(cfg)))

    def test_evidence_beats_preference(self):
        a = album(barcodes={"9006180000000"})
        cfg = cfg_with(preferred_release_formats=["Vinyl"])
        r = run(a, cand(id="cd1", barcodes={"9006180000000"}), cand(id="lp1", formats={"vinyl"}), cfg=cfg)
        self.assertEqual(r["best"]["cand"]["id"], "cd1")

    def test_never_a_score_never_automatic(self):
        a = album()
        plain, pref = cfg_with(), cfg_with(preferred_release_formats=["Vinyl"], preferred_release_types=["Album"])
        c1, c2 = self.pair()
        for c in (c1, c2, cand(id="t", release_types=["Album"])):
            self.assertEqual(mc.score_candidate(a, c, plain)["confidence"],
                             mc.score_candidate(a, c, pref)["confidence"])
        r = run(a, c1, c2, cfg=pref)
        # TIE-1: your preferred formats, the last tie-break, pick it and it is tagged in full
        self.assertEqual((r["best"]["cand"]["id"], r["status"]), ("lp1", "auto"), r["why"])

    def test_never_between_albums(self):
        # a clearly better album is never passed over for a preferred format or type
        a = album()
        cfg = cfg_with(preferred_release_formats=["Vinyl"], preferred_release_types=["Live"])
        other = cand(id="live", title="Live at Hellfest", formats={"vinyl"}, release_types=["Live"],
                     tracks=[{"disc": 1, "title": f"L{i}", "secs": s + 40} for i, s in enumerate(LENS)])
        r = run(a, cand(id="cd1", release_types=["Album"]), other, cfg=cfg)
        self.assertEqual(r["best"]["cand"]["id"], "cd1")
        self.assertEqual(r, run(a, cand(id="cd1", release_types=["Album"]), other, cfg=cfg_with()))

    def test_combine_between_mb_and_discogs(self):
        a = album()
        cfg = cfg_with()
        # MusicBrainz names a release group, Discogs a master: no key of their own is shared
        dg_shape = dict(id="dg-cd", source="discogs", release_group=None, master_id="m1")
        mb = run(a, cand(id="mb-lp", formats={"vinyl"}), cfg=cfg)
        dg = run(a, cand(**dg_shape), cfg=cfg)
        prefs = mc.release_preferences(cfg_with(preferred_release_formats=["Vinyl"]))
        out = mc.combine(copy.deepcopy(mb), copy.deepcopy(dg), ["US"], prefs)
        self.assertEqual(out["best"]["cand"]["id"], "dg-cd")
        self.assertFalse(any("your preferred" in n for n in out["best"]["notes"]))
        # the release group's Discogs master link ("discogs_master") says they are one album
        mb = run(a, cand(id="mb-lp", formats={"vinyl"}, discogs_master="m1"), cfg=cfg)
        self.assertEqual((mb["status"], dg["status"]), ("auto", "auto"))
        self.assertEqual(mc.combine(copy.deepcopy(mb), copy.deepcopy(dg), ["US"])["best"]["cand"]["id"], "dg-cd")
        out = mc.combine(copy.deepcopy(mb), copy.deepcopy(dg), ["US"], prefs)
        self.assertEqual((out["best"]["cand"]["id"], out["status"]), ("mb-lp", "auto"))       # TIE-1
        # nothing points to vinyl: it is the CD (no_media_evidence), no guess and no preference
        out = mc.combine(copy.deepcopy(mb), copy.deepcopy(dg), ["US"],
                         mc.release_preferences(cfg_with(preferred_release_formats=["CD"])))
        self.assertEqual((out["best"]["cand"]["id"], out["status"]), ("dg-cd", "auto"))
        # not known to be the same album: no preference
        dg2 = run(a, cand(**dict(dg_shape, master_id="m9")), cfg=cfg)
        out = mc.combine(copy.deepcopy(mb), copy.deepcopy(dg2), ["US"], prefs)
        self.assertEqual(out["best"]["cand"]["id"], "dg-cd")
        self.assertFalse(any("your preferred" in n for n in out["best"]["notes"]))
        # the link is only across the two databases: two release groups are two albums
        self.assertFalse(mc.same_album([{"cand": cand(release_group="rg1", discogs_master="m1")},
                                        {"cand": cand(release_group="rg2", discogs_master="m1")}]))
        # without a country order, Discogs first as before
        self.assertEqual(mc.combine(copy.deepcopy(mb), copy.deepcopy(dg), None, prefs)["best"]["cand"]["id"], "dg-cd")


class UserLikeIniUnchanged(unittest.TestCase):
    """The user's settings (types "Other", formats @Invalid(), no release_type_scores) pick
    what the matcher picked before, in every tie shape."""

    def test_same_picks(self):
        prefs = pc.matching_preferences(path=write_ini(USER_LIKE_INI))
        before = dict(mc.DEFAULT_CFG)
        after = dict(mc.DEFAULT_CFG, **prefs)
        a = album()
        shapes = [
            (cand(id="cd1"), cand(id="lp1", formats={"vinyl"})),
            (cand(id="de"), cand(id="us", country="US"), cand(id="gb", country="GB")),
            (cand(id="old", date="2009-03-02"), cand(id="new", date="2014")),
            (cand(id="a", release_types=["Album"]), cand(id="b", release_types=["Album"], formats={"digital"})),
            (cand(id="x"), cand(id="y", country="JP", formats={"cd", "vinyl"})),
        ]
        for cands in shapes:
            r0, r1 = run(a, *cands, cfg=before), run(a, *cands, cfg=after)
            self.assertEqual((r0["status"], r0["best"]["cand"]["id"], r0["why"], r0["best"]["notes"]),
                             (r1["status"], r1["best"]["cand"]["id"], r1["why"], r1["best"]["notes"]))
            mb = run(a, cands[0], cfg=before)
            dg = run(a, dict(cands[1], source="discogs"), cfg=before)
            c0 = mc.combine(copy.deepcopy(mb), copy.deepcopy(dg), before["preferred_countries"])
            c1 = mc.combine(copy.deepcopy(mb), copy.deepcopy(dg), after["preferred_countries"],
                            mc.release_preferences(after))
            self.assertEqual((c0["status"], c0["best"]["cand"]["id"]), (c1["status"], c1["best"]["cand"]["id"]))


if __name__ == "__main__":
    unittest.main()
