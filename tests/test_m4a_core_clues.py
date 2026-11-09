"""Offline tests for the M4a core-clue rules of the other-pressing audit:

- R1: a format contradiction on the pick (hi-res audio and a CD, a rip log and a download,
  a CD rip and a vinyl or cassette, CD-quality tracks that are not whole sectors and a CD)
  keeps it from going automatic; the edition the folder names skips contradicted pressings.
- R2: a pressing picked by the country order, the earliest date or "listed twice" is a
  guess: the result goes to album_only, not auto (between MusicBrainz and Discogs too). A
  release your tags link to, or (not blind) the one they are tagged with, is kept.
- R5: per-track lengths break ties; versions of the format your copy is are fetched first.
- R8: whole CD sectors are not CD proof when a download of the album is whole sectors too.
- R12: a remaster is not a download hint; packaging variants are not one pressing twice.

The albums are synthetic, modelled on the audit's (no file of the library is read).
Run:  python3 tests/test_m4a_core_clues.py [plugin-dir]
"""
import os as _os, tempfile as _tempfile  # noqa: E402
_os.environ["XDG_CONFIG_HOME"] = _tempfile.mkdtemp(prefix="tm-config-")
_os.environ["XDG_STATE_HOME"] = _tempfile.mkdtemp(prefix="tm-state-")

import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
PLUGIN_DIR = _os.environ.get("TAGMENDER_DISCOGS_PLUGIN", "plugins_builtin/discogs")
if len(sys.argv) > 1 and not sys.argv[1].startswith("-"):
    PLUGIN_DIR = sys.argv.pop(1)          # the plugin folder: for the Discogs search test

import match_core as mc  # noqa: E402
import match_albums as ma  # noqa: E402

CFG = dict(mc.DEFAULT_CFG, preferred_countries=[])
CFG_US = dict(mc.DEFAULT_CFG, preferred_countries=["US"])
LENS = (200.0, 305.0, 410.0)


def album(lens=LENS, **kw):
    a = {"artist": "Artist Alpha", "title": "First Light", "year": 2010, "discs": 1,
         "tracks": [{"disc": 1, "num": i + 1, "title": f"T{i}", "secs": s} for i, s in enumerate(lens)],
         "barcodes": set(), "catnos": set()}
    a.update(kw)
    return a


def cand(lens=LENS, **kw):
    c = {"source": "mb", "id": "x1", "url": "u", "artist": "Artist Alpha", "title": "First Light",
         "year": 2010, "country": "CA", "label": "L", "catnos": set(), "barcodes": set(), "disc_count": 1,
         "tracks": [{"disc": 1, "title": f"T{i}", "secs": s} for i, s in enumerate(lens)]}
    c.update(kw)
    return c


def run(a, *cands, cfg=CFG, ocr=""):
    scored = [mc.score_candidate(a, c, cfg, ocr) for c in cands]
    r = mc.classify(scored, cfg)
    r["scored"] = scored
    return r


class FormatContradiction(unittest.TestCase):
    """R1"""

    def test_hi_res_audio_against_the_only_cd(self):
        # Ahab - The Coral Tombs: 7 tracks at 24/48, no log; only the Brazilian CD listed
        lens = (300.0, 420.0, 510.0, 600.0, 380.0, 460.0, 720.0)
        a = album(lens, audio={"hires": True, "cd_frames": False, "not_cd": False})
        r = run(a, cand(lens, source="discogs", id="26464862", formats={"cd"}))
        self.assertEqual(r["status"], "review")
        self.assertIn("hi-res audio cannot come from this CD", r["why"])

    def test_rip_log_and_sectors_against_a_download(self):
        # Bell Witch - Mirror Reaper: 16/44.1, whole sectors, an EAC log; a File/MP3 entry
        a = album(rip_log_toc=True, audio={"hires": False, "cd_frames": True, "not_cd": False})
        r = run(a, cand(source="discogs", id="d1", formats={"digital"}))
        self.assertEqual(r["status"], "review")
        self.assertIn("your folder has a CD rip log, so not this digital release", r["why"])

    def test_rip_log_against_a_download_after_an_earlier_clue(self):
        # the log check is not an elif after not_cd: CD-quality tracks that are not whole
        # sectors give the download a clue, and the CD log still speaks against it
        a = album(rip_log_toc=True, audio={"hires": False, "cd_frames": False, "not_cd": True})
        r = run(a, cand(source="discogs", id="d1", formats={"digital"}))
        self.assertIn("your folder has a CD rip log, so not this digital release", r["best"]["clues_against"])
        self.assertIn("your folder has a CD rip log, so not this digital release", r["why"])
        self.assertNotEqual(r["status"], "auto")

    def test_hi_res_audio_overrides_a_stale_rip_log(self):
        # Cynic - Traced in Air: 24-bit files beside a 2008 CD log, the 2019 Remixed download
        a = album(rip_log_toc=True, audio={"hires": True, "cd_frames": False, "not_cd": False})
        r = run(a, cand(source="discogs", id="web", formats={"digital"}))
        self.assertEqual(mc.format_contradictions(r["best"]), [])
        self.assertEqual(r["status"], "auto", r["why"])
        r = run(a, cand(id="lp", formats={"vinyl"}))
        self.assertEqual(mc.format_contradictions(r["best"]), [])

    def test_hi_res_sacd_rip_against_its_sacd(self):
        # a 24/88.2 rip of an SACD: media_kind calls the SACD a CD, which still carries it
        a = album(folder="2004 - First Light [SACD]", media_hint=mc.media_hint("2004 - First Light [SACD]"),
                  audio={"hires": True, "cd_frames": False, "not_cd": False})
        r = run(a, cand(id="sacd", formats={"cd"}, hires_media=True))
        self.assertNotIn("hi-res audio cannot come from this CD", r["best"]["clues_against"])
        self.assertEqual(r["status"], "auto", r["why"])
        # the folder says SACD, but the only release listed is the plain CD: it cannot be
        r = run(a, cand(id="cd", formats={"cd"}))
        self.assertIn("hi-res audio cannot come from this CD", r["best"]["clues_against"])
        self.assertNotEqual(r["status"], "auto")
        # the plain CD and the hybrid SACD tied: the country order never picks the plain CD
        r = run(a, cand(id="cd", country="US", formats={"cd"}),
                cand(id="sacd", country="JP", formats={"cd"}, hires_media=True), cfg=CFG_US)
        self.assertNotEqual((r["status"], r["best"]["cand"]["id"]), ("auto", "cd"))
        # no folder name: the release's own format names it an SACD
        a = album(audio={"hires": True, "cd_frames": False, "not_cd": False})
        self.assertEqual(run(a, cand(id="sacd", formats={"cd"}, hires_media=True))["status"], "auto")
        self.assertTrue(mc.hires_media(["Hybrid SACD"]) and mc.hires_media(["SHM-SACD"]))
        self.assertFalse(mc.hires_media(["CD", "SHM-CD"]))

    def test_candidate_builders_name_an_sacd(self):
        # Discogs: a "CD" format whose descriptions say Hybrid, SACD; MusicBrainz: "Hybrid SACD"
        dm = ma.load_plugin(_os.path.expanduser(PLUGIN_DIR))

        def discogs(rid, fmt):
            return {"id": rid, "title": "First Light", "year": 2010, "country": "JP", "uri": f"u/{rid}",
                    "artists": [{"name": "Artist Alpha"}], "labels": [{"name": "L", "catno": f"C{rid}"}],
                    "formats": [fmt], "identifiers": [],
                    "tracklist": [{"type_": "track", "position": str(i + 1), "title": f"T{i}",
                                   "duration": f"{int(x) // 60}:{int(x) % 60:02d}"} for i, x in enumerate(LENS)]}

        def mb(rid, fmt):
            return {"id": rid, "title": "First Light", "date": "2010", "country": "JP",
                    "artist-credit": [{"name": "Artist Alpha", "artist": {"name": "Artist Alpha"}}],
                    "media": [{"format": fmt, "tracks": [{"title": f"T{i}", "length": int(x * 1000)}
                                                         for i, x in enumerate(LENS)]}]}
        sacd = ma.discogs_candidate(discogs(3, {"name": "CD", "qty": "1", "descriptions": ["Hybrid", "SACD"]}), dm)
        plain = ma.discogs_candidate(discogs(4, {"name": "CD", "qty": "1", "descriptions": ["Album"]}), dm)
        self.assertEqual((sacd["formats"], sacd["hires_media"], plain["hires_media"]), ({"cd"}, True, False))
        self.assertTrue(ma.mb_candidate(mb("m1", "Hybrid SACD"))["hires_media"])
        self.assertFalse(ma.mb_candidate(mb("m2", "CD"))["hires_media"])
        # a hi-res copy, no folder hint: the SACD is no contradiction, the plain CD is
        a = album(audio={"hires": True, "cd_frames": False, "not_cd": False})
        r = run(a, sacd)
        self.assertNotIn("hi-res audio cannot come from this CD", r["best"]["clues_against"])
        self.assertEqual(r["status"], "auto", r["why"])
        self.assertIn("hi-res audio cannot come from this CD", run(a, plain)["best"]["clues_against"])

    def test_cd_rip_against_vinyl(self):
        # BTBAM - The Great Misdirect: a CD rip matched to the Craft 2LP
        a = album(rip_log_toc=True, audio={"hires": False, "cd_frames": True, "not_cd": False})
        r = run(a, cand(id="lp", formats={"vinyl"}))
        self.assertEqual(r["status"], "review")
        self.assertIn("your folder has a CD rip log, so not this vinyl release", r["why"])
        a = album(audio={"hires": False, "cd_frames": True, "not_cd": False})
        r = run(a, cand(id="mc", formats={"cassette"}))
        self.assertIn("your folder has tracks that are whole CD sectors, so not this cassette release", r["why"])
        # the CD of it is still automatic
        self.assertEqual(run(a, cand(id="cd", formats={"cd"}))["status"], "auto")

    def test_download_against_a_cd_whose_barcode_was_read(self):
        # BTBAM - Colors: not whole sectors; the OCR read the CD's barcode off a packaging scan
        a = album(audio={"hires": False, "cd_frames": False, "not_cd": True})
        r = run(a, cand(id="cd", formats={"cd"}, barcodes={"746105035126"}), ocr="0 746105 035126")
        self.assertTrue(r["best"]["code"].startswith("barcode"), r["best"]["code"])
        self.assertEqual(r["status"], "auto", r["why"])                # (the barcode names the release)
        self.assertTrue(mc.format_contradictions(r["best"]))

    def test_hi_res_vinyl_rip_stays_allowed(self):
        a = album(folder="2014 - X [Vinyl Rip 24bit 96khz]", media_hint="vinyl",
                  audio={"hires": True, "cd_frames": False, "not_cd": False})
        r = run(a, cand(id="lp", formats={"vinyl"}))
        self.assertEqual(r["status"], "auto", r["why"])

    def test_edition_skips_a_contradicted_pressing(self):
        # Slayer - Seasons In The Abyss [RM 2015]: 24/192; the 2015 SHM-CD and the 2015 download
        a = album(year=1990, pressing_year=2015, folder="1990 - Seasons In The Abyss [RM 2015]",
                  audio={"hires": True, "cd_frames": False, "not_cd": False})
        shm = cand(id="shm", year=2015, country="JP", formats={"cd"})
        web = cand(id="web", year=2015, country="XW", formats={"digital"})
        old = cand(id="cd90", year=1990, country="US", formats={"cd"})
        pick = mc.choose_pressing([mc.score_candidate(a, c, CFG) for c in (shm, web, old)])
        self.assertEqual(pick[0]["cand"]["id"], "web")
        # the download not listed: the SHM-CD is never "the 2015 edition your folder names"
        r = run(a, shm, old)
        self.assertNotIn("edition your folder names", " ".join(r["best"]["notes"]))
        self.assertNotEqual(r["status"], "auto")


class FallbackTieBreaks(unittest.TestCase):
    """R2"""

    def book_of_kings(self, **kw):
        # Mournful Congregation - The Book Of Kings: one disc ID on Osmose (FR) and 20 Buck Spin (US)
        a = album(**dict(dict(toc_releases={"osmose", "spin"}, rip_log_toc=True,
                              audio={"hires": False, "cd_frames": True, "not_cd": False}), **kw))
        return a, (cand(id="osmose", country="FR", label="Osmose Productions", date="2011-02-28",
                        formats={"cd"}, catnos={"opcd264"}),
                   cand(id="spin", country="US", label="20 Buck Spin", date="2011-02-28",
                        formats={"cd"}, catnos={"spin045"}))

    def test_country_order_is_album_only(self):
        a, cs = self.book_of_kings()
        r = run(a, *cs, cfg=CFG_US)
        self.assertEqual(r["best"]["cand"]["id"], "spin")
        self.assertNotEqual(r["status"], "auto")
        self.assertTrue(all(mc._TIE_WHY.match(w) for w in r["why"]), r["why"])
        only = mc.album_only(r, CFG_US, source="cd")
        self.assertIsNotNone(only)
        self.assertEqual({t["id"] for t in only["tied"]}, {"osmose", "spin"})

    def test_original_pressing_is_album_only(self):
        a, (fr, us) = self.book_of_kings()
        us = dict(us, date="2011-06-14")
        r = run(a, fr, us)
        self.assertEqual(r["best"]["cand"]["id"], "osmose")
        self.assertIn("label's original pressing", " ".join(r["best"]["notes"]))
        self.assertEqual(r["status"], "review")
        self.assertIsNotNone(mc.album_only(r, CFG))

    def test_the_release_in_your_tags_is_kept(self):
        # Cult of Luna - Salvation: the files carry the GB pressing's MusicBrainz ID, no link
        a, cs = self.book_of_kings(tag_release_mb={"spin"})
        r = run(a, *cs, cfg=dict(CFG, preferred_countries=["FR"]))
        self.assertEqual((r["status"], r["best"]["cand"]["id"]), ("auto", "spin"), r["why"])
        self.assertIn("tagged with", " ".join(r["best"]["notes"]))
        # Book of Kings: tagged Osmose, the country order says US
        a, cs = self.book_of_kings(tag_release_mb={"osmose"})
        r = run(a, *cs, cfg=CFG_US)
        self.assertEqual((r["status"], r["best"]["cand"]["id"]), ("auto", "osmose"), r["why"])
        # a blind run blanks those IDs: the country order is a guess again
        a, cs = self.book_of_kings(tag_release_mb=set())
        r = run(a, *cs, cfg=dict(CFG, preferred_countries=["FR"]))
        self.assertNotEqual(r["status"], "auto")
        self.assertIsNotNone(mc.album_only(r, CFG, source="cd"))
        # a Discogs ID in the tags, a Discogs pressing
        a, (fr, us) = self.book_of_kings(tag_release_discogs={"2952"})
        r = run(a, dict(fr, source="discogs", id="1111"), dict(us, source="discogs", id="2952"), cfg=dict(
            CFG, preferred_countries=["FR"]))
        self.assertEqual((r["status"], str(r["best"]["cand"]["id"])), ("auto", "2952"), r["why"])

    def test_a_tagged_release_your_copy_contradicts_is_not_kept(self):
        a, (fr, us) = self.book_of_kings(tag_release_mb={"spin"})
        r = run(a, fr, dict(us, formats={"vinyl"}), cfg=dict(CFG, preferred_countries=["FR"]))
        self.assertNotEqual((r["status"], r["best"]["cand"]["id"]), ("auto", "spin"))

    def test_combine_guess_between_databases_is_album_only(self):
        # MusicBrainz sure of Osmose, Discogs of 20 Buck Spin, nothing about the copy for either
        a, (fr, us) = self.book_of_kings(toc_releases=set())
        mb = run(a, fr)
        dg = run(a, dict(us, source="discogs"))
        self.assertEqual((mb["status"], dg["status"]), ("auto", "auto"))
        out = mc.combine(mb, dg, ["US"])
        self.assertEqual((out["status"], out["best"]["cand"]["id"]), ("review", "spin"))
        self.assertTrue(all(mc._TIE_WHY.match(w) for w in out["why"]), out["why"])
        only = mc.album_only(out, CFG_US, source="cd")
        self.assertEqual({t["id"] for t in only["tied"]}, {"osmose", "spin"})
        # the files tagged with the MusicBrainz one: it is kept
        a, (fr, us) = self.book_of_kings(toc_releases=set(), tag_release_mb={"osmose"})
        out = mc.combine(run(a, fr), run(a, dict(us, source="discogs")), ["US"])
        self.assertEqual((out["status"], out["best"]["cand"]["id"]), ("auto", "osmose"))

    def test_the_tagged_release_is_kept(self):
        # not blind: the files link to the 20 Buck Spin release, which is among the tied
        a, cs = self.book_of_kings(link_releases={"spin"})
        r = run(a, *cs, cfg=dict(CFG, preferred_countries=["FR"]))
        self.assertEqual((r["status"], r["best"]["cand"]["id"]), ("auto", "spin"), r["why"])


class ExactLengths(unittest.TestCase):
    """R5"""

    def test_lengths_to_the_millisecond(self):
        # Bell Witch - Demo 2011: the 2011 download and a reissue 30 ms shorter on every track
        lens = (600.123, 700.456, 800.789)
        a = album(lens)
        r = run(a, cand(lens, id="a", formats={"digital"}),
                cand(tuple(x - 0.030 for x in lens), id="b", formats={"digital"}))
        self.assertEqual(r["best"]["cand"]["id"], "a")
        self.assertEqual(r["runner_up"]["cand"]["id"], "b")
        self.assertIn("millisecond", r["best"]["clues"].get("track lengths", "") +
                      " ".join(r["best"]["notes"]))
        self.assertEqual(r["status"], "auto", r["why"])

    def test_close_on_every_track_beats_off_on_some(self):
        # BTBAM - Alaska: the 2020 remix fits every track, the 2005 mix is 2-3 s off on some
        files = (234.81, 440.61, 503.67)
        a = album(files)
        remix = cand((235, 441, 504), id="remix", formats={"digital"})
        mix05 = cand((237.2, 442.9, 503.67), id="2005", formats={"digital"})
        tied = [mc.score_candidate(a, c, CFG) for c in (mix05, remix)]
        self.assertIn("exact lengths", mc._relative_clues(tied)[id(tied[1])])      # (M4b: weight 2)
        self.assertEqual(run(a, mix05, remix)["best"]["cand"]["id"], "remix")

    def test_one_track_off_does_not_decide(self):
        # two entries of one recording, one with a track typed 2 s off: not another mix
        files = (234.81, 440.61, 503.67)
        a = album(files)
        tied = [mc.score_candidate(a, c, CFG) for c in
                (cand((237.2, 440.61, 503.67), id="typo", formats={"digital"}),
                 cand((235, 441, 504), id="ok", formats={"digital"}))]
        self.assertIsNone(mc.exact_lengths(tied))

    def test_whole_second_lengths_never_decide_to_the_millisecond(self):
        a = album((200.0, 305.0, 410.0))
        tied = [mc.score_candidate(a, cand(id="a"), CFG),
                mc.score_candidate(a, cand((199.97, 304.97, 409.97), id="b"), CFG)]
        self.assertIsNone(mc.exact_lengths(tied))

    def test_versions_of_your_copys_format_are_fetched_first(self):
        hires = album(audio={"hires": True, "cd_frames": False, "not_cd": False})
        dl = ma.version_priority(hires, formats=["File", "FLAC"])
        cd = ma.version_priority(hires, formats=["CD", "Album"])
        self.assertLess(dl, cd)
        nc = album(audio={"hires": False, "cd_frames": False, "not_cd": True})
        self.assertLess(ma.version_priority(nc, formats=["File"]), ma.version_priority(nc, formats=["CD"]))
        rip = album(audio={"hires": False, "cd_frames": True, "not_cd": False})
        self.assertLess(ma.version_priority(rip, formats=["CD"]), ma.version_priority(rip, formats=["File"]))
        self.assertEqual(ma.fetch_kinds(album()), set())
        # Cynic - Traced in Air: 24-bit files beside a 2008 CD log, so a TOC: the download first
        stale = album(rip_log_toc=True, tocs={1: "1 3 69000 150"},
                      audio={"hires": True, "cd_frames": False, "not_cd": False})
        self.assertEqual(ma.copy_source(stale), "cd")
        self.assertEqual(ma.fetch_kinds(stale), {"digital", "vinyl"})
        self.assertLess(ma.version_priority(stale, formats=["File", "FLAC"]),
                        ma.version_priority(stale, formats=["CD", "Album"]))


class SearchHttp:
    """A Discogs search whose results are CDs of one master (Slayer - Seasons In The Abyss
    [RM 2015]: 24/192 files, no format in the folder name); `download` adds a File/FLAC hit."""
    def __init__(self, download=False, versions=None):
        self.download = download
        self.versions = versions or {"versions": []}
        self.urls = []

    def get_json(self, url, host):
        self.urls.append(url)
        if "/database/search" in url:
            hits = [{"id": 1, "title": "Artist Alpha - First Light", "year": "2010", "master_id": 7}]
            return {"results": hits + ([{"id": 2, "title": "Artist Alpha - First Light", "year": "2015",
                                         "master_id": 7}] if self.download else [])}
        if "/masters/7/versions" in url:
            return self.versions
        rid = int(url.rsplit("/", 1)[1])
        fmt = {"name": "File", "descriptions": ["FLAC"]} if rid == 2 else {"name": "CD", "qty": "1"}
        return {"id": rid, "title": "First Light", "year": 2010, "country": "US", "uri": f"u/{rid}",
                "artists": [{"name": "Artist Alpha"}], "labels": [{"name": "L", "catno": "C1"}],
                "formats": [fmt], "identifiers": [],
                "tracklist": [{"type_": "track", "position": str(i + 1), "title": f"T{i}",
                               "duration": f"{int(x) // 60}:{int(x) % 60:02d}"} for i, x in enumerate(LENS)]}


class VersionFetch(unittest.TestCase):
    """R5: a hi-res copy whose search finds only CDs asks the master for its other versions."""

    @classmethod
    def setUpClass(cls):
        cls.dm = ma.load_plugin(_os.path.expanduser(PLUGIN_DIR))

    def test_only_cds_found_fetches_the_master(self):
        a = album(media_hint="", audio={"hires": True, "cd_frames": False, "not_cd": False},
                  catno_search=[], search_labels=[])
        h = SearchHttp()
        ma.discogs_search(h, a, CFG, self.dm)
        self.assertTrue(any("/masters/7/versions" in u for u in h.urls), h.urls)

    def test_a_stale_rip_log_beside_hi_res_still_fetches_the_master(self):
        a = album(media_hint="", rip_log_toc=True, tocs={1: "1 3 69000 150"},
                  audio={"hires": True, "cd_frames": False, "not_cd": False},
                  catno_search=[], search_labels=[])
        h = SearchHttp()
        ma.discogs_search(h, a, CFG, self.dm)
        self.assertTrue(any("/masters/7/versions" in u for u in h.urls), h.urls)

    def test_a_download_found_needs_no_master(self):
        # the master's listing is read (is the download the only one? R7), but none of
        # its versions is fetched
        a = album(media_hint="", audio={"hires": True, "cd_frames": False, "not_cd": False},
                  catno_search=[], search_labels=[])
        listing = {"pagination": {"pages": 1, "items": 2}, "versions": [
            {"id": 1, "format": "CD, Album"}, {"id": 2, "major_formats": ["File"]}]}
        h = SearchHttp(download=True, versions=listing)
        out = ma.discogs_search(h, a, CFG, self.dm)
        fetched = {u.rsplit("/", 1)[1] for u in h.urls if "/releases/" in u}
        self.assertEqual(fetched, {"1", "2"}, h.urls)
        self.assertTrue(any("/masters/7/versions" in u for u in h.urls), h.urls)
        # the counts reach the candidates (R7: the only download of its master)
        web = next(c for c in out if str(c["id"]) == "2")
        self.assertEqual(web["formats"], {"digital"})
        self.assertEqual(web.get("master_digital"), 1)
        # a listing of more than one page: unknown
        h = SearchHttp(download=True, versions=dict(listing, pagination={"pages": 2, "items": 140}))
        out = ma.discogs_search(h, a, CFG, self.dm)
        self.assertNotIn("master_digital", next(c for c in out if str(c["id"]) == "2"))

    def test_musicbrainz_group_counts_reach_the_candidates(self):
        # the release-group browse is a full listing with one Digital Media entry: the
        # download fetched from it is the only one of its group (R7)
        a = album(catno_search=[], search_labels=[])

        def rel(rid, fmt):
            return {"id": rid, "title": "First Light", "country": "XW" if fmt != "CD" else "CA",
                    "track-count": len(LENS), "media": [{"format": fmt, "track-count": len(LENS)}],
                    "release-group": {"id": "rg"}}
        group = [rel("web", "Digital Media"), rel("cd", "CD")]

        class H:
            def __init__(self, count):
                self.count = count

            def get_json(self, url, host):
                if "/release/?query=" in url:
                    return {"releases": group}
                if "release-group=" in url:
                    return {"release-count": self.count, "releases": group}
                rid = url.split("/release/")[1].split("?")[0]
                r = next(g for g in group if g["id"] == rid)
                return dict(r, media=[dict(r["media"][0], tracks=[{"title": f"T{i}", "length": int(x * 1000)}
                                                                   for i, x in enumerate(LENS)])])
        out = ma.mb_search(H(2), a, CFG)
        web = next(c for c in out if c["id"] == "web")
        self.assertEqual(web["formats"], {"digital"})
        self.assertEqual(web.get("group_digital"), 1)
        # a group of more releases than were listed: unknown
        out = ma.mb_search(H(140), a, CFG)
        self.assertNotIn("group_digital", next(c for c in out if c["id"] == "web"))


class SectorsNotProof(unittest.TestCase):
    """R8: Voivod - Lost Machine - Live: the download's lengths are whole sectors too."""
    ALIGNED = (470.6, 300.2, 201.0)                 # 35295, 22515 and 15075 sectors of 1/75 s
    # 35296, 22516 and 15076 sectors, rounded to the ms (not multiples of 40 ms)
    ROUNDED = (470.613, 300.213, 201.013)

    def lost_machine(self, dl_lens=ALIGNED, **kw):
        a = album(self.ALIGNED, audio={"hires": False, "cd_frames": True, "not_cd": False}, **kw)
        return a, cand(dl_lens, id="dl", country="XW", formats={"digital"}), \
            cand(self.ALIGNED, id="cd", country="XE", formats={"cd"})

    def test_aligned_download_takes_the_cd_clue_away(self):
        a, dl, cd = self.lost_machine()
        r = run(a, dl, cd)
        for s in r["scored"]:
            self.assertNotEqual((s.get("clues") or {}).get("format"), mc.SECTOR_CLUE)
        self.assertNotEqual(r["status"], "auto")

    def test_album_only_keeps_the_download_and_the_cd(self):
        a, dl, cd = self.lost_machine()
        r = run(a, dl, cd)
        self.assertEqual(ma.tie_source(a, r), "")
        only = mc.album_only(r, CFG, ma.tie_source(a, r))
        self.assertIsNotNone(only, r["why"])
        self.assertEqual({t["id"] for t in only["tied"]}, {"dl", "cd"})
        # a rip log or scans back the CD: it stays a CD rip
        self.assertEqual(ma.tie_source(dict(a, rip_log_toc=True), r), "cd")
        self.assertEqual(ma.tie_source(dict(a, scans={"physical": True}), r), "cd")

    def test_sectors_rounded_to_the_millisecond(self):
        a = album(self.ROUNDED, audio={"hires": False, "cd_frames": True, "not_cd": False})
        r = run(a, cand(self.ROUNDED, id="dl", country="XW", formats={"digital"}),
                cand(self.ROUNDED, id="cd", country="XE", formats={"cd"}))
        for s in r["scored"]:
            self.assertNotEqual((s.get("clues") or {}).get("format"), mc.SECTOR_CLUE)
        self.assertNotEqual(r["status"], "auto")

    def test_scans_back_the_cd(self):
        a, dl, cd = self.lost_machine(scans={"physical": True, "why": "a back cover scan"})
        r = run(a, dl, cd)
        mine = next(s for s in r["scored"] if s["cand"]["id"] == "cd")
        self.assertEqual(mine["clues"].get("format"), mc.SECTOR_CLUE)
        self.assertIn("physical", mine["clues"])

    def test_a_download_off_the_sectors_keeps_it(self):
        a, dl, cd = self.lost_machine(dl_lens=(470.61, 300.21, 201.01))
        r = run(a, dl, cd)
        self.assertEqual((r["status"], r["best"]["cand"]["id"]), ("auto", "cd"), r["why"])
        self.assertEqual(r["best"]["clues"].get("format"), mc.SECTOR_CLUE)

    def test_a_rip_log_backs_the_cd(self):
        a, dl, cd = self.lost_machine(rip_log_toc=True)
        r = run(a, dl, cd)
        self.assertEqual((r["status"], r["best"]["cand"]["id"]), ("auto", "cd"), r["why"])
        self.assertEqual(r["best"]["clues"].get("format"), mc.SECTOR_LOG_CLUE)


class SmallRules(unittest.TestCase):
    """R12"""

    def test_a_remaster_is_not_a_download(self):
        # Pantera - The Great Southern Trendkill [2016 RM 20th Annv Edn]: whole CD sectors
        folder = "1996 - The Great Southern Trendkill [2016 RM 20th Annv Edn]"
        a = album(folder=folder, media_hint=mc.media_hint(folder), pressing_year=2016,
                  audio={"hires": False, "cd_frames": True, "not_cd": False})
        self.assertEqual(a["media_hint"], "")
        r = run(a, cand(id="dl", year=2016, formats={"digital"}), cand(id="cd", year=2016, formats={"cd"}))
        self.assertEqual((r["status"], r["best"]["cand"]["id"]), ("auto", "cd"), r["why"])

    def test_packaging_variant_is_not_one_pressing_listed_twice(self):
        # Candlemass - Dactylis Glomerata: GMR's O-card edition and the same CD without it
        common = dict(source="discogs", catnos={"gmrcd9004"}, barcodes={"7350006762123"},
                      country="SE", formats={"cd"}, label="GMR Music Group")
        a = album()
        tied = [mc.score_candidate(a, cand(id="8910755", **common), CFG),
                mc.score_candidate(a, cand(id="25872463", notes="Same as 8910755 but without the O-card.",
                                           **common), CFG)]
        self.assertIsNone(mc.same_pressing_listed_twice(tied))
        self.assertEqual(mc.packaging_variant(tied[1]["cand"]), "without the O-card")
        # the packaging your scans show decides between such variants
        a = album(packaging={"digipak"})
        tied = [mc.score_candidate(a, cand(id="jc", packaging="jewel", **common), CFG),
                mc.score_candidate(a, cand(id="dp", packaging="digipak", **common), CFG)]
        pick = mc.same_pressing_listed_twice(tied)
        self.assertEqual(pick[0]["cand"]["id"], "dp")
        self.assertIn("packaging", pick[1])
        # the scans show a digipak, but the jewel case fits every track length: a person decides
        a = album((200.0, 305.0, 410.0), packaging={"digipak"})
        tied = [mc.score_candidate(a, cand((200.0, 305.0, 410.0), id="jc", packaging="jewel", **common), CFG),
                mc.score_candidate(a, cand((200.0, 307.5, 412.5), id="dp", packaging="digipak", **common), CFG)]
        self.assertIsNone(mc.same_pressing_listed_twice(tied))
        # no evidence: still undecided
        tied = [mc.score_candidate(album(), cand(id="jc", packaging="jewel", **common), CFG),
                mc.score_candidate(album(), cand(id="dp", packaging="digipak", **common), CFG)]
        self.assertIsNone(mc.same_pressing_listed_twice(tied))


if __name__ == "__main__":
    unittest.main()
