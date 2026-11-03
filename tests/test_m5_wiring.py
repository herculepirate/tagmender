"""Offline tests for the M5 follow-ups:

- Picard's preferred release types and formats reach the matcher from every caller (the
  engine, match_albums' CLI, validate.py, inbox.py) as the preferred countries do, and
  match_albums.process passes them to combine; with an ini like the user's (types "Other",
  formats @Invalid(), no release_type_scores) every pick is the same as before.
- mb_candidate fills release_group, release_types, media_formats and discogs_master (the
  release group's discogs.com/master/N link) from data already fetched; mb_search asks for the
  release group's links once per album, and only when a format is set (else only a cached
  answer is used); a group linking several masters links none, as engine._rg_master reads
  it; discogs_candidate fills master_id and format names, and the two built candidates are
  one album for combine through that link.
- The countries follow the profile's "picard" overrides and follow_picard, as the types and
  formats; an odd Picard.ini keeps the overrides.
- Partial: a track title MusicBrainz and Discogs spell differently is MusicBrainz's, in its
  own spelling, unless the file's own title is Discogs's (case aside only) (BTBAM - Automata
  I & II, "Condemed To The Gallows").
- Partial / album-only: a Vorbis total kept under one name ("TRACKTOTAL=03", "DISCTOTAL=01")
  is written plain, unless the profile (or Picard's preserved tags) keeps the field.

Synthetic data only; no network, no Picard.ini of this machine.
Run:  python3 tests/test_m5_wiring.py [plugin-dir]
"""
import os as _os, tempfile as _tempfile  # noqa: E402
_os.environ["XDG_CONFIG_HOME"] = _tempfile.mkdtemp(prefix="tm-config-")
_os.environ["XDG_STATE_HOME"] = _tempfile.mkdtemp(prefix="tm-state-")
_os.environ["TAGMENDER_TAGGER_INI"] = "/nonexistent"

import contextlib
import copy
import io
import json
import shutil
import sqlite3
import sys
import types
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "tests"))
PLUGIN_DIR = sys.argv.pop(1) if len(sys.argv) > 1 and not sys.argv[1].startswith("-") else None

import match_core as mc  # noqa: E402
import match_albums as ma  # noqa: E402
import tagger_settings as pc  # noqa: E402
import test_m5_release_prefs as m5  # noqa: E402  (its helpers: album, cand, run, USER_LIKE_INI)
import _v2_defaults  # noqa: E402  (written for the defaults before schema 3)
_v2_defaults.use()


def write_ini(text):
    return m5.write_ini(text)


def user_ini():
    return m5.write_ini(m5.USER_LIKE_INI)


class Wiring(unittest.TestCase):
    def tearDown(self):
        _os.environ["TAGMENDER_TAGGER_INI"] = "/nonexistent"
        pc._cache.clear()

    def test_read_like_the_countries(self):
        self.assertEqual(ma.picard_release_preferences(user_ini()),
                         {"preferred_release_types": ["Other"], "discouraged_release_types": [],
                          "preferred_release_formats": []})
        self.assertEqual(ma.picard_release_preferences("/nonexistent/Picard.ini"), pc.MATCH_DEFAULTS)
        # a profile's "picard" entries override, follow_picard off reads no Picard.ini
        self.assertEqual(ma.picard_release_preferences(user_ini(), overrides={"preferred_release_formats": ["CD"]})
                         ["preferred_release_formats"], ["CD"])
        self.assertEqual(ma.picard_release_preferences(user_ini(), follow_picard=False), pc.MATCH_DEFAULTS)

    def engine(self, data):
        import engine
        import profiles as pf
        d = _tempfile.mkdtemp(prefix="tm-cache-")
        self.addCleanup(shutil.rmtree, d, True)
        return engine.Engine(pf.from_dict(dict({"name": "t"}, **data)), cache=str(Path(d) / "c.sqlite"),
                             dm=object(), ocr="off")

    def keys(self, cfg):
        return tuple(cfg[k] for k in ("preferred_countries", "preferred_release_types",
                                      "discouraged_release_types", "preferred_release_formats"))

    def test_engine_cfg(self):
        _os.environ["TAGMENDER_TAGGER_INI"] = str(write_ini(CALLER_INI))
        e = self.engine({"picard": {"discouraged_release_types": ["Live"]}})
        self.assertEqual(self.keys(e.cfg), (["GB", "JP"], ["Other"], ["Live"], ["CD"]))
        # a profile's "picard" entry overrides the countries too, as the types and formats
        e = self.engine({"picard": {"preferred_release_countries": ["XE"]}})
        self.assertEqual(self.keys(e.cfg)[0], ["XE"])
        # follow_picard off: no Picard.ini, for the countries as for the types and formats
        e = self.engine({"follow_picard": False})
        self.assertEqual(self.keys(e.cfg), (mc.DEFAULT_CFG["preferred_countries"],
                                            *(pc.MATCH_DEFAULTS[k] for k in pc.MATCH_DEFAULTS)))
        self.assertEqual(list(pc.MATCH_DEFAULTS), ["preferred_release_types", "discouraged_release_types",
                                                   "preferred_release_formats"])

    def test_odd_ini_keeps_overrides(self):
        orig = pc.matching_preferences

        def odd(overrides=None, path=None, follow_picard=True):
            if follow_picard:
                raise ValueError("odd Picard.ini")
            return orig(overrides, path, follow_picard)
        pc.matching_preferences = odd
        try:
            got = ma.picard_release_preferences(user_ini(), overrides={"preferred_release_formats": ["CD"]})
        finally:
            pc.matching_preferences = orig
        self.assertEqual(got, dict(pc.MATCH_DEFAULTS, preferred_release_formats=["CD"]))

    def test_countries_like_the_types(self):
        ini = write_ini(CALLER_INI)
        self.assertEqual(ma.picard_preferred_countries(ini), ["GB", "JP"])
        self.assertEqual(ma.picard_preferred_countries(ini, overrides={"preferred_release_countries": ["us"]}),
                         ["US"])
        self.assertEqual(ma.picard_preferred_countries(ini, follow_picard=False), [])


# A Picard.ini setting the countries, a type and a format (plain lists, as Picard 2 writes them)
CALLER_INI = ("[application]\nversion=3.0.2.final0\n\n[setting]\npreferred_release_countries=GB, JP\n"
              "preferred_release_types=Other\npreferred_release_formats=CD\n")
WANT = {"preferred_countries": ["GB", "JP"], "preferred_release_types": ["Other"],
        "discouraged_release_types": [], "preferred_release_formats": ["CD"]}


class _Stop(Exception):
    pass


class Callers(unittest.TestCase):
    """match_albums' CLI, validate.py and inbox.py, run with the matcher stubbed: the cfg they
    hand to Http (copied there, so nothing put in later counts) holds Picard.ini's
    preferences; process passes them to the last combine."""

    def setUp(self):
        self.tmp = Path(_tempfile.mkdtemp(prefix="tm-callers-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.ini = write_ini(CALLER_INI)
        self.seen = []

        def http(db, cfg, token):
            self.seen.append(copy.deepcopy(cfg))
            raise _Stop
        env = mock.patch.dict(_os.environ, {"TAGMENDER_TAGGER_INI": str(self.ini), "DISCOGS_TOKEN": "x"})
        env.start()
        self.addCleanup(env.stop)
        p = mock.patch.multiple(ma, Http=http, load_plugin=lambda d: None, plugin_files=lambda dm: [],
                                _script_version=lambda files=(): "abcdef12", check_discogs_token=lambda t: "",
                                fingerprint_ready=lambda: True, _PLUGIN_STAMP=ma._PLUGIN_STAMP)
        p.start()
        self.addCleanup(p.stop)
        self.addCleanup(pc._cache.clear)

    def picked(self):
        self.assertEqual(len(self.seen), 1)
        return {k: self.seen[0].get(k) for k in WANT}

    def test_match_albums_main(self):
        audit = self.tmp / "audit.csv"
        audit.write_text("FullPath,AlbumArtistFolder,AlbumFolder,Status\n")
        with mock.patch.object(sys, "argv", ["match_albums.py", str(audit), "--out-dir", str(self.tmp / "out")]), \
                contextlib.redirect_stdout(io.StringIO()), self.assertRaises(_Stop):
            ma.main()
        self.assertEqual(self.picked(), WANT)

    def test_validate_main(self):
        import validate as va
        (self.tmp / "lib").mkdir()
        with mock.patch.object(sys, "argv", ["validate.py", str(self.tmp / "lib"), "--out-dir",
                                             str(self.tmp / "out"), "--cache", "--journals", "--no-ocr"]), \
                contextlib.redirect_stdout(io.StringIO()), self.assertRaises(_Stop):
            va.main()
        self.assertEqual(self.picked(), WANT)

    def test_inbox_main(self):
        import inbox
        _os.environ["TAGMENDER_TAGGER_INI"] = "/nonexistent"      # (inbox reads its --picard-ini)
        pc._cache.clear()
        with mock.patch.object(ma, "_discogs_token", lambda: "x"), self.assertRaises(_Stop):
            inbox.main(["--inbox", str(self.tmp / "in"), "--library", str(self.tmp / "lib"),
                        "--out-dir", str(self.tmp / "out"), "--picard-ini", str(self.ini), "--once"])
        self.assertEqual(self.picked(), WANT)

    def test_process_passes_them_to_combine(self):
        if not shutil.which("ffmpeg"):
            self.skipTest("needs ffmpeg")
        import subprocess
        d = self.tmp / "Artist Alpha" / "2010 - First Light"
        d.mkdir(parents=True)
        for i in range(2):
            subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", f"sine=frequency={300 + 50 * i}:"
                            f"duration={5 * (i + 1)}", "-ac", "1", "-metadata", f"title=T{i}", "-metadata",
                            "album=First Light", "-metadata", "albumartist=Artist Alpha", "-metadata",
                            f"track={i + 1}", str(d / f"{i + 1:02d}.flac"), "-y"], check=True)
        row = {"AlbumArtistFolder": "Artist Alpha", "AlbumFolder": d.name, "FullPath": str(d), "Status": "untagged"}

        class Http:
            db = sqlite3.connect(":memory:")

            def get_json(self, url, host):
                return {}
        Http.db.execute("CREATE TABLE http_cache (url TEXT PRIMARY KEY, body TEXT)")
        seen, orig = [], ma._combine
        cfg = dict(mc.DEFAULT_CFG, **{k: v for k, v in WANT.items() if k != "preferred_countries"})
        with mock.patch.object(ma, "_combine", lambda *a: seen.append(a) or orig(*a)):
            ma.process(row, Http(), cfg, None, types.SimpleNamespace(ocr=False, use_shops=False, use_gnudb=False,
                                                                     acoustid="off", use_discogs=False))
        self.assertTrue(seen)
        self.assertEqual(seen[-1][2:], (mc.DEFAULT_CFG["preferred_countries"], mc.release_preferences(cfg)))
        self.assertEqual(seen[-1][3]["formats"], ["CD"])


class UserIniUnchanged(unittest.TestCase):
    """With the user's settings, candidates built as the builders now build them (release
    group, types, formats, the master link; a Discogs one has no types) pick exactly what
    they picked before."""

    def shapes(self):
        a = m5.album()
        mb = dict(release_group="rg1", discogs_master="m1", release_types=["Album"])
        other = dict(mb, release_types=["Album", "Other"])
        return a, [
            (m5.cand(id="cd1", media_formats=["CD"], **mb),
             m5.cand(id="lp1", formats={"vinyl"}, media_formats=['12" Vinyl'], **mb)),
            (m5.cand(id="de", **mb), m5.cand(id="us", country="US", **mb), m5.cand(id="gb", country="GB", **mb)),
            (m5.cand(id="dig", formats={"digital"}, media_formats=["Digital Media"], **mb),
             m5.cand(id="cd", media_formats=["CD"], **mb)),
            # (a release group's types are the group's: every pressing of one album has the same,
            # so the user's "Other" never tells two of them apart)
            (m5.cand(id="ep", release_types=["EP"], release_group="rg1", discogs_master="m1"),
             m5.cand(id="ep2", country="JP", release_types=["EP"], release_group="rg1", discogs_master="m1")),
            # the user's preferred type itself, on the MusicBrainz side; Discogs's side has none
            (m5.cand(id="oth1", media_formats=["CD"], **other),
             m5.cand(id="oth2", country="JP", media_formats=["CD"], **other)),
            # types that differ: a pressing whose group is not known (types unknown, not one
            # album known), and an "Other" one in another group (not one album)
            (m5.cand(id="nogroup", release_group="", release_types=[]), m5.cand(id="oth", **other)),
            (m5.cand(id="alb", release_group="rg2", discogs_master="m2", release_types=["Album"]),
             m5.cand(id="oth", country="JP", **other)),
        ]

    @staticmethod
    def discogs(c):
        # as discogs_candidate builds one: its master and format names, no release group or types
        d = {k: v for k, v in c.items() if k not in ("release_types", "discogs_master", "release_group")}
        return dict(d, source="discogs", master_id=c.get("discogs_master") or "m1", media_formats=["CD"])

    def test_same_picks(self):
        before = dict(mc.DEFAULT_CFG)
        after = dict(mc.DEFAULT_CFG, **ma.picard_release_preferences(user_ini()))
        self.assertEqual(after["preferred_release_types"], ["Other"])
        a, shapes = self.shapes()
        for cands in shapes:
            r0, r1 = m5.run(a, *cands, cfg=before), m5.run(a, *cands, cfg=after)
            self.assertEqual((r0["status"], r0["best"]["cand"]["id"], r0["why"], r0["best"]["notes"]),
                             (r1["status"], r1["best"]["cand"]["id"], r1["why"], r1["best"]["notes"]))
            mb = m5.run(a, cands[0], cfg=before)
            dg = m5.run(a, self.discogs(cands[1]), cfg=before)
            self.assertNotIn("release_types", dg["best"]["cand"])
            c0 = ma._combine(copy.deepcopy(mb), copy.deepcopy(dg), before["preferred_countries"])
            c1 = ma._combine(copy.deepcopy(mb), copy.deepcopy(dg), after["preferred_countries"],
                             mc.release_preferences(after))
            self.assertEqual((c0["status"], c0["best"]["cand"]["id"], c0["best"]["notes"]),
                             (c1["status"], c1["best"]["cand"]["id"], c1["best"]["notes"]))
            self.assertFalse(any(n in str(c1) for n in mc.FALLBACK_NOTES), cands)

    def test_what_stops_the_types(self):
        # the user's "Other" scores apart only where types are known on both and the two are
        # one album: same_album and the unknown guard are what keep it out above
        prefs = mc.release_preferences(dict(mc.DEFAULT_CFG, **ma.picard_release_preferences(user_ini())))
        a, shapes = self.shapes()
        cfg = dict(mc.DEFAULT_CFG)
        for cands in shapes[5:]:
            pool = [mc.score_candidate(a, c, cfg) for c in copy.deepcopy(cands)]
            self.assertIsNone(mc.prefer_type_and_format(pool, prefs), cands)
        one = [mc.score_candidate(a, dict(c, release_group="rg1", discogs_master="m1",
                                          release_types=c["release_types"] or ["Album"]), cfg)
               for c in copy.deepcopy(shapes[6])]
        pick, why = mc.prefer_type_and_format(one, prefs)
        self.assertEqual(pick["cand"]["id"], "oth")
        self.assertIn(mc.FALLBACK_NOTES[0], str(why))

    def test_a_format_preference_does_decide(self):
        # (the wiring is live: the same tie with a format set is decided, as a guess)
        a, shapes = self.shapes()
        cfg = dict(mc.DEFAULT_CFG, preferred_countries=["DE"], preferred_release_formats=['12" Vinyl'])
        r = m5.run(a, *shapes[0], cfg=cfg)
        self.assertEqual((r["best"]["cand"]["id"], r["status"]), ("lp1", "review"))


class BuildersToCombine(unittest.TestCase):
    """Candidates as mb_candidate and discogs_candidate build them: the release group's
    discogs.com/master/N link makes the MusicBrainz and the Discogs pick one album."""

    def picks(self, relations):
        if not PLUGIN_DIR:
            self.skipTest("needs the Discogs Enrichment plugin folder")
        dm = ma.load_plugin(PLUGIN_DIR)
        rel = mb_release(rg={"id": "rg1", "primary-type": "Album", "relations": relations}, fmt="Digital Media")
        mbc = ma.mb_candidate(rel)
        dgc = ma.discogs_candidate({"id": 5, "title": "The Divinity of Oceans", "year": 2009, "country": "Germany",
                                    "artists": [{"name": "Ahab", "id": 1}], "master_id": 77,
                                    "tracklist": [{"position": str(i + 1), "title": f"T{i}",
                                                   "duration": "%d:%02d" % divmod(int(s), 60)}
                                                  for i, s in enumerate(m5.LENS)],
                                    "formats": [{"name": "CD", "qty": "1"}]}, dm)
        a = m5.album(folder="Ahab - The Divinity of Oceans")
        cfg = dict(mc.DEFAULT_CFG, preferred_countries=["DE"], preferred_release_formats=["CD"])
        mb, dg = m5.run(a, mbc, cfg=cfg), m5.run(a, dgc, cfg=cfg)
        return mbc, dgc, ma._combine(mb, dg, cfg["preferred_countries"], mc.release_preferences(cfg))

    def test_master_link_makes_one_album(self):
        mbc, dgc, out = self.picks([dict(MASTER_REL, url={"resource": "https://www.discogs.com/master/77"})])
        self.assertEqual((mbc["discogs_master"], dgc["master_id"]), ("77", "77"))
        self.assertTrue(mc.same_album([{"cand": mbc}, {"cand": dgc}]))
        # (the CD, Discogs's, over MusicBrainz's download: a guess, so not automatic)
        self.assertEqual((out["status"], out["best"]["cand"]["source"]), ("review", "discogs"))
        self.assertTrue(any(mc.FALLBACK_NOTES[1] in n for n in out["best"]["notes"]), out["best"]["notes"])

    def test_no_link_no_format_note(self):
        mbc, dgc, out = self.picks([])
        self.assertFalse(mc.same_album([{"cand": mbc}, {"cand": dgc}]))
        self.assertFalse(any(mc.FALLBACK_NOTES[1] in n for n in out["best"]["notes"]))

    def test_two_masters_link_none(self):
        two = [dict(MASTER_REL, url={"resource": f"https://www.discogs.com/master/{m}"}) for m in ("111", "222")]
        self.assertEqual(ma.mb_group_master({"relations": two}), "")
        self.assertEqual(ma.mb_group_master({"relations": two[:1] * 2}), "111")
        mbc, dgc, out = self.picks(two + [dict(MASTER_REL, url={"resource": "https://www.discogs.com/master/77"})])
        self.assertEqual(mbc["discogs_master"], "")
        self.assertFalse(any(mc.FALLBACK_NOTES[1] in n for n in out["best"]["notes"]))

    def test_engine_reads_the_link_alike(self):
        import engine
        two = {"relations": [dict(MASTER_REL, url={"resource": f"https://www.discogs.com/master/{m}"})
                             for m in ("111", "222")]}
        e = types.SimpleNamespace(http=types.SimpleNamespace(get_json=lambda url, host: two))
        self.assertEqual(engine.Engine._rg_master(e, "rg1"), "")
        e.http.get_json = lambda url, host: {"relations": [MASTER_REL]}
        self.assertEqual(engine.Engine._rg_master(e, "rg1"), "4711")


def mb_release(rid="r1", rg=None, fmt="CD"):
    rel = {"id": rid, "title": "The Divinity of Oceans", "date": "2009", "country": "DE",
           "artist-credit": [{"name": "Ahab", "artist": {"name": "Ahab", "sort-name": "Ahab"}, "joinphrase": ""}],
           "label-info": [], "relations": [],
           "media": [{"format": fmt, "tracks": [{"title": f"T{i}", "length": int(s * 1000)}
                                                for i, s in enumerate(m5.LENS)]}]}
    if rg is not None:
        rel["release-group"] = rg
    return rel


MASTER_REL = {"type": "discogs", "target-type": "url", "url": {"resource": "https://www.discogs.com/master/4711"}}


class Builders(unittest.TestCase):
    def test_mb_candidate(self):
        c = ma.mb_candidate(mb_release(rg={"id": "rg1", "primary-type": "Album", "secondary-types": ["Live"],
                                           "relations": [MASTER_REL]}))
        self.assertEqual((c["release_group"], c["release_types"], c["discogs_master"], c["media_formats"]),
                         ("rg1", ["Album", "Live"], "4711", ["CD"]))
        c = ma.mb_candidate(mb_release())             # a release lookup without its group
        self.assertEqual((c["release_group"], c["release_types"], c["discogs_master"]), ("", [], ""))
        c = ma.mb_candidate(mb_release(fmt=None))     # a medium naming no format: names unknown
        self.assertEqual(c["media_formats"], [])

    def test_discogs_format_names(self):
        rel = {"formats": [{"name": "Vinyl", "descriptions": ['12"', "LP"]}, {"name": "CD", "descriptions": ["HDCD"]},
                           {"name": "File", "descriptions": ["FLAC"]}, {"name": "CDr"},
                           {"name": "SACD", "descriptions": ["Hybrid"]}]}
        self.assertEqual(ma.discogs_format_names(rel), ['12" Vinyl', "HDCD", "File", "CD-R", "Hybrid SACD"])
        self.assertEqual(ma.discogs_format_names({"formats": [{"name": ""}]}), [])

    def mb_search(self, cfg, cached=False):
        db = sqlite3.connect(":memory:")
        db.execute("CREATE TABLE http_cache (url TEXT PRIMARY KEY, body TEXT)")
        rg_url = "https://musicbrainz.org/ws/2/release-group/rg1?inc=url-rels&fmt=json"
        if cached:
            db.execute("INSERT INTO http_cache VALUES (?, ?)", (rg_url, json.dumps({"relations": [MASTER_REL]})))
        asked = []

        class Http:
            def __init__(self):
                self.db = db

            def get_json(self, url, host):
                asked.append(url)
                if "/release/?query=" in url:
                    return {"releases": [{"id": "r1", "track-count": 4,
                                          "release-group": {"id": "rg1", "primary-type": "Album"}}]}
                if "release?release-group=" in url:
                    return {"releases": [{"id": "r1", "media": [{"format": "CD", "track-count": 4}]},
                                         {"id": "r2", "media": [{"format": '12" Vinyl', "track-count": 4}]}],
                            "release-count": 2}
                if url.startswith(rg_url.split("?")[0]):
                    return {"relations": [MASTER_REL]}
                if "/release/r" in url:
                    rid = url.split("/release/")[1].split("?")[0]
                    return mb_release(rid, fmt="CD" if rid == "r1" else '12" Vinyl')
                return None
        a = m5.album(folder="Ahab - The Divinity of Oceans")
        out = ma.mb_search(Http(), a, cfg)
        return {c["id"]: c for c in out}, [u for u in asked if "release-group/" in u]

    def test_mb_search_release_group(self):
        cands, asked = self.mb_search(dict(mc.DEFAULT_CFG))
        self.assertEqual(asked, [], "no request when nothing reads the master link")
        self.assertEqual({k: (c["release_group"], c["release_types"], c["discogs_master"]) for k, c in cands.items()},
                         {"r1": ("rg1", ["Album"], ""), "r2": ("rg1", ["Album"], "")})
        cands, asked = self.mb_search(dict(mc.DEFAULT_CFG), cached=True)
        self.assertEqual((asked, cands["r2"]["discogs_master"]), ([], "4711"))
        cands, asked = self.mb_search(dict(mc.DEFAULT_CFG, preferred_release_formats=["CD"]))
        self.assertEqual(len(asked), 1, "one request per album, not per pressing")
        self.assertEqual({c["discogs_master"] for c in cands.values()}, {"4711"})
        # the user's settings (types only) ask nothing
        cands, asked = self.mb_search(dict(mc.DEFAULT_CFG, **ma.picard_release_preferences(user_ini())))
        self.assertEqual(asked, [])
        # a discouraged type alone asks nothing either: it never decides between a
        # MusicBrainz and a Discogs pick (a Discogs pick has no types)
        cands, asked = self.mb_search(dict(mc.DEFAULT_CFG, discouraged_release_types=["Live"]))
        self.assertEqual(asked, [])

    def test_discogs_candidate(self):
        if not PLUGIN_DIR:
            self.skipTest("needs the Discogs Enrichment plugin folder")
        dm = ma.load_plugin(PLUGIN_DIR)
        rel = {"id": 5, "title": "X", "artists": [{"name": "Ahab", "id": 1}], "master_id": 77,
               "tracklist": [{"position": "1", "title": "T0", "duration": "5:01"}],
               "formats": [{"name": "Vinyl", "qty": "1", "descriptions": ['7"']}]}
        c = ma.discogs_candidate(rel, dm)
        self.assertEqual((c["master_id"], c["media_formats"]), ("77", ['7" Vinyl']))
        self.assertEqual(ma.discogs_candidate(dict(rel, master_id=None), dm)["master_id"], "")


class SettleTitles(unittest.TestCase):
    """engine.Engine._settle_titles on its own (no files, no network)."""

    def engine(self, pool_titles=None, use_mb=True):
        import engine
        asked = []

        class Http:
            def get_json(self, url, host):
                asked.append(url)
                return {"media": [{"tracks": [{"title": t} for t in pool_titles or []]}]}
        e = types.SimpleNamespace(use_mb=use_mb, http=Http())
        e._pool_mb_titles = lambda *a: engine.Engine._pool_mb_titles(e, *a)
        return (lambda *a: engine.Engine._settle_titles(e, *a)), asked

    def test_discogs_only_keeps_your_spelling(self):
        settle, asked = self.engine(["Condemned to the Gallows", "Voice of Trespass"])
        files = [{"title": "Condemned To The Gallows"}, {"title": "Voice Of Trespass"}, {"title": "Gloom"}]
        targets = [{"title": ["Condemed To The Gallows"]}, {"title": ["Voice of Trespass"]}, {"title": ["Glom"]}]
        notes = []
        settle(targets, files, None, {"targets": targets}, [("mb", "r1", 80.0)], {}, notes)
        # (a candidate MusicBrainz release has your file's title: yours, never its spelling)
        self.assertEqual([t["title"] for t in targets],
                         [["Condemned To The Gallows"], ["Voice of Trespass"], ["Glom"]])
        self.assertEqual(len(asked), 1)
        self.assertEqual(notes, ["MusicBrainz and Discogs spell 1 track title(s) differently: your files' own "
                                 "kept (as a MusicBrainz release among the candidates has it)"])

    def test_discogs_only_at_most_six_lookups(self):
        settle, asked = self.engine(["Something Else"])
        targets = [{"title": ["Condemed To The Gallows"]}]
        pool = [("mb", f"r{k}", 80.0) for k in range(8)] + [("discogs", "d1", 80.0)]
        settle(targets, [{"title": "Condemned To The Gallows"}], None, {"targets": targets}, pool, {}, [])
        self.assertEqual((targets[0]["title"], len(asked)), (["Condemed To The Gallows"], 6))
        # (found on the first: no more asked)
        settle, asked = self.engine(["Condemned to the Gallows"])
        targets = [{"title": ["Condemed To The Gallows"]}]
        settle(targets, [{"title": "Condemned To The Gallows"}], None, {"targets": targets}, pool, {}, [])
        self.assertEqual((targets[0]["title"], len(asked)), (["Condemned To The Gallows"], 1))

    def test_discogs_only_nothing_asked_when_titles_agree(self):
        settle, asked = self.engine(["X"])
        targets = [{"title": ["Voice of Trespass"]}]
        settle(targets, [{"title": "Voice Of Trespass"}], None, {"targets": targets}, [("mb", "r1", 80.0)], {}, [])
        self.assertEqual((targets[0]["title"], asked), (["Voice of Trespass"], []))

    def test_musicbrainz_first(self):
        settle, asked = self.engine()
        targets = [{"title": ["Condemned to the Gallows"]}, {"title": ["Colors"]}, {"title": ["Ants"]},
                   {"title": ["Colors"]}]
        mb = {"targets": targets}
        dg = {"titles": {0: "Condemed To The Gallows", 1: "Colours", 2: "Ants", 3: "Colours"}}
        files = [{"title": "Condemned To The Gallows"}, {"title": "colours"}, {"title": ""}, {"title": "Colöurs"}]
        notes = []
        settle(targets, files, mb, dg, [], {}, notes)
        # yours where it is either one's title (folded: case, accents, punctuation aside), as
        # your files spell it; else MusicBrainz's
        self.assertEqual([t["title"] for t in targets],
                         [["Condemned To The Gallows"], ["colours"], ["Ants"], ["Colöurs"]])
        self.assertEqual(notes, ["MusicBrainz and Discogs spell 3 track title(s) differently: "
                                 "your files' own kept"])
        targets = [{"title": ["Colors"]}]
        settle(targets, [{"title": "Kolors"}], {"targets": targets}, {"titles": {0: "Colours"}}, [], {}, [])
        self.assertEqual(targets[0]["title"], ["Colors"])
        self.assertEqual(asked, [])

    def test_your_folded_one_over_musicbrainz_spelling(self):
        # MusicBrainz "Déjà Vu", Discogs "Deja Vu (Remastered)", yours "deja vu": yours (it is
        # MusicBrainz's, folded); a title that is neither one's: MusicBrainz's
        settle, _ = self.engine()
        self.assertNotEqual(mc.fold("Déjà Vu"), mc.fold("Deja Vu (Remastered)"))
        targets = [{"title": ["Déjà Vu"]}]
        settle(targets, [{"title": "deja vu"}], {"targets": targets}, {"titles": {0: "Deja Vu (Remastered)"}},
               [], {}, [])
        self.assertEqual(targets[0]["title"], ["deja vu"])
        targets = [{"title": ["Déjà Vu"]}]
        settle(targets, [{"title": "Deja Vous"}], {"targets": targets}, {"titles": {0: "Deja Vu (Remastered)"}},
               [], {}, [])
        self.assertEqual(targets[0]["title"], ["Déjà Vu"])


class PlainTotals(unittest.TestCase):
    """Partial and album-only albums: a file's total ("TRACKTOTAL=03", under one Vorbis name or
    both, or ID3's "1/03") is written plain ("3") when the file is written, as an exact match
    writes the release's; an exact match's own total is as before."""

    def plan_tags(self, tier, targets_extra=None, both=False, ext=".opus", **profile_kw):
        import mutagen
        import _review29b_common as c
        import executor
        import plan as pl
        if not shutil.which("ffmpeg"):
            self.skipTest("needs ffmpeg")
        files = c.album(ext, 2)
        for i, f in enumerate(files):
            m = mutagen.File(str(f))
            if m.tags is None:
                m.add_tags()
            if ext == ".mp3":
                from mutagen.id3 import TIT2, TRCK, TPOS
                m.tags.add(TIT2(encoding=3, text=["old"]))
                m.tags.add(TRCK(encoding=3, text=["%02d/02" % (i + 1)]))
                m.tags.add(TPOS(encoding=3, text=["1/01"]))
                m.save()
                continue
            m.tags["TITLE"], m.tags["TRACKNUMBER"], m.tags["TRACKTOTAL"] = ["old"], ["%02d" % (i + 1)], ["02"]
            m.tags["DISCNUMBER"], m.tags["DISCTOTAL"] = ["1"], ["01"]
            if both:                                 # (under both names: not Picard's one-name case)
                m.tags["TOTALTRACKS"], m.tags["TOTALDISCS"] = ["02"], ["01"]
            m.save()
        targets = [dict({"title": [f"Track {i + 1}"], "album": ["A"], "albumartist": ["B"], "artist": ["B"]},
                        **(targets_extra or {})) for i in range(2)]
        c.fresh_state()
        prof = c.profile(Path(files[0]).parent.parent, **profile_kw)
        prop = pl.Proposal(source_dir=str(Path(files[0]).parent), files=[str(f) for f in files], targets=targets,
                           provider="musicbrainz", confidence=100.0, margin=50.0, tier=tier,
                           release={"album": ["A"], "albumartist": ["B"]})
        p = pl.build([prop], prof)
        self.rows = [r for a in p["albums"] for f in a.get("files") or [] for r in f.get("diff") or []
                     if r["field"] in ("totaltracks", "totaldiscs")]
        res = executor.Executor(p).run()
        self.assertEqual(res.get("counts"), {"done": 1}, c.detail(p, res))
        return [{k.upper(): v for k, v in mutagen.File(str(f)).tags.items()} for f in files]

    def totals(self, tags):
        return (tags.get("TRACKTOTAL"), tags.get("TOTALTRACKS"), tags.get("DISCTOTAL"), tags.get("TOTALDISCS"))

    def test_partial_and_album_only(self):
        for tier in ("partial", "album_only"):
            for tags in self.plan_tags(tier):
                self.assertEqual(self.totals(tags), (["2"], ["2"], ["1"], ["1"]), tier)
            self.assertTrue(self.rows and all(r["action"].endswith(", leading zero dropped") for r in self.rows),
                            self.rows)
            self.assertIn("TOTALDISCS and DISCTOTAL", {r["action"] for r in self.rows if r["field"] == "totaldiscs"}
                          .pop())

    def test_both_names_and_id3(self):
        # (a total under both Vorbis names, and an ID3 one in "01/02": plain too)
        for tier in ("partial", "album_only"):
            for tags in self.plan_tags(tier, both=True):
                self.assertEqual(self.totals(tags), (["2"], ["2"], ["1"], ["1"]), tier)
            self.assertTrue(self.rows and all("leading zero dropped" in r["action"] for r in self.rows), self.rows)
            for i, tags in enumerate(self.plan_tags(tier, ext=".mp3")):
                self.assertEqual((str(tags["TRCK"]), str(tags["TPOS"])), (f"{i + 1:02d}/2", "1/1"), tier)
        for tags in self.plan_tags("exact", both=True):
            self.assertEqual(self.totals(tags), (["02"], ["02"], ["01"], ["01"]))
        fp = {"default": "replace", "fields": {"totaltracks": "preserve", "totaldiscs": "preserve"}}
        for tags in self.plan_tags("partial", both=True, field_policy=fp):
            self.assertEqual(self.totals(tags), (["02"], ["02"], ["01"], ["01"]))

    def test_preserved_total_as_it_is(self):
        # (a total the profile keeps - preserve, or fill_missing as Picard's preserved tags
        # become - is written as the file has it, padding and all)
        for pol in ("preserve", "fill_missing"):
            fp = {"default": "replace", "fields": {"totaltracks": pol, "totaldiscs": pol}}
            for tags in self.plan_tags("partial", field_policy=fp):
                self.assertEqual(self.totals(tags), (["02"], ["02"], ["01"], ["01"]), pol)
            self.assertFalse(any("leading zero" in r["action"] for r in self.rows), self.rows)

    def test_exact_as_before(self):
        for tags in self.plan_tags("exact"):
            self.assertEqual(self.totals(tags), (["02"], ["02"], ["01"], ["01"]))

    def test_plain_number(self):
        import plan as pl
        self.assertEqual([pl._plain_number(x) for x in ("03", "3", "0", "00", " 07 ", "3a", "")],
                         ["3", "3", "0", "0", "7", "3a", ""])


if __name__ == "__main__":
    unittest.main()
