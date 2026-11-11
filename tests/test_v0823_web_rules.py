# Tests for WEB-1 (the web is searched also with Unresolved "leave"; what it finds is held:
# shown, unticked, applied only when ticked, never by a watched folder) and GNU-1 (gnudb's
# exact match for a one-disc album's table of contents is a web album: Partial tags only).
# Rule IDs: docs/MATCHING_RULES.md. Mocked answers, generated audio only.
import os as _os, tempfile as _tempfile  # noqa: E402
_os.environ["XDG_CONFIG_HOME"] = _tempfile.mkdtemp(prefix="tm-config-")
_os.environ["XDG_STATE_HOME"] = _tempfile.mkdtemp(prefix="tm-state-")
_os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_engine import Base  # noqa: E402
from test_web_partial import WebHttp, deezer_album  # noqa: E402

import engine  # noqa: E402
import executor  # noqa: E402
import match_albums as ma  # noqa: E402
import match_core as mc  # noqa: E402
import plan as pl  # noqa: E402
import profiles as pf  # noqa: E402
import watcher  # noqa: E402
import _v2_defaults  # noqa: E402
_v2_defaults.use()

TOC = "1 3 4650 150 900 2400"           # 10, 20 and 30 s tracks: Base.album's files
LEAVE = {"action": "leave", "move_to": "", "allow_partial": True}
MARK = {"action": "mark", "move_to": "", "allow_partial": True}


def gnudb_entry(titles=("T0", "T1", "T2"), exact=True, artist="Band G", title="Album G (CD1)"):
    return {"artist": artist, "title": title, "year": 2005, "genre": "Ambient", "tracks": list(titles),
            "offsets": [150, 900, 2400], "seconds": 62, "category": "misc", "discid": "0b003e03",
            "disc": 1, "exact": exact}


class GnudbHttp:
    """gnudb over CDDB: a query answered with CODE, the entry read back."""
    def __init__(self, code):
        did = ma.cddb_discid(TOC)
        self.query = (f"{code} Found matches, list follows (until terminating `.')\nmisc {did} Band G / Album G\n."
                      if code in (210, 211) else f"200 misc {did} Band G / Album G")
        self.read = (f"210 misc {did} CD database entry follows\n# xmcd\n#\n# Track frame offsets:\n#\t150\n"
                     f"#\t900\n#\t2400\n#\n# Disc length: 62 seconds\nDTITLE=Band G / Album G\nDYEAR=2005\n"
                     f"TTITLE0=T0\nTTITLE1=T1\nTTITLE2=T2\n.")

    def get_text(self, url, host, key=None):
        return self.query if "cddb+query" in url else self.read


class GNU1_ExactMatchOnly(unittest.TestCase):
    def test_exact_and_fuzzy_replies_are_told_apart(self):
        cfg = dict(mc.DEFAULT_CFG, preferred_countries=[])
        for code, exact in ((200, True), (210, True), (211, False)):
            found, notes = ma.gnudb_lookup(GnudbHttp(code), {"tocs": {1: TOC}}, cfg, "someone@example.org")
            self.assertEqual((len(found), notes), (1, []), code)
            self.assertIs(found[0]["exact"], exact, code)

    def test_more_exact_entries_than_are_read_make_none_exact(self):
        cfg = dict(mc.DEFAULT_CFG, preferred_countries=[])
        http = GnudbHttp(210)
        did = ma.cddb_discid(TOC)
        http.query = ("210 Found exact matches, list follows\n" +
                      "".join(f"{c} {did} Band G / Album G\n" for c in ("misc", "rock", "jazz", "data")) + ".")
        found, _notes = ma.gnudb_lookup(http, {"tocs": {1: TOC}}, cfg, "someone@example.org")
        self.assertTrue(found)
        self.assertFalse(any(e["exact"] for e in found))

    def test_the_matcher_hands_its_entries_on(self):
        out = {}
        ma.note_gnudb(out, {"gnudb": [gnudb_entry()]}, {"best": None})
        self.assertEqual(out["gnudb"][0]["title"], "Album G (CD1)")


class GNU1_GnudbAlbum(Base):
    def setUp(self):
        super().setUp()
        self.d = self.album("Band G", "Album G")

    def run_web(self, entry, tocs=None, http=None, unresolved=MARK):
        eng = self.engine(http or WebHttp([]), unresolved=unresolved,
                          providers={"web": {"enabled": True, "shops": False, "gnudb": True}})
        album = ma.read_album(str(self.d), "Band G", self.d.name)
        album["tocs"] = tocs if tocs is not None else {1: TOC}
        files = [t["path"] for t in album["tracks"]]
        base = dict(source_dir=str(self.d), files=files, provider_errors={})
        return eng._web({"gnudb": [entry]}, album, files, base)

    def test_an_exact_gnudb_entry_gives_partial_tags_and_no_release(self):
        p = self.run_web(gnudb_entry())
        self.assertIsNotNone(p)
        self.assertEqual((p.status, p.tier, p.provider, p.release_id), ("identified", "partial", "web:gnudb", ""))
        t = p.targets[1]
        self.assertEqual((t["album"], t["albumartist"], t["title"]), (["Album G"], ["Band G"], ["T1"]))
        self.assertEqual(t["tagmender_source"], ["https://gnudb.org/cd/mi0b003e03"])
        for k in ("label", "barcode", "media", "catalognumber", "releasecountry", "discogs_release_id",
                  "musicbrainz_albumid"):
            self.assertNotIn(k, t)

    def test_a_fuzzy_gnudb_match_is_a_clue_only(self):
        self.assertIsNone(self.run_web(gnudb_entry(exact=False)))

    def test_track_titles_that_disagree_write_nothing(self):
        self.assertIsNone(self.run_web(gnudb_entry(titles=("Other", "Names", "Here"))))

    def test_one_disc_only(self):
        self.assertIsNone(self.run_web(gnudb_entry(), tocs={1: TOC, 2: TOC}))

    def test_lengths_that_disagree_write_nothing(self):
        e = dict(gnudb_entry(), offsets=[150, 3000, 4000], seconds=90)
        self.assertIsNone(self.run_web(e))

    def test_another_track_count_writes_nothing(self):
        e = dict(gnudb_entry(titles=("T0", "T1")), offsets=[150, 900], seconds=32)
        self.assertIsNone(self.run_web(e, tocs={1: "1 2 2400 150 900"}))

    def test_another_artist_writes_nothing(self):
        self.assertIsNone(self.run_web(gnudb_entry(artist="Someone Else")))


class WEB1_LeaveStillSearchesTheWeb(Base):
    def found(self, unresolved):
        d = self.album("Band W", "Album W")
        http = WebHttp([deezer_album(21, "Band W", "Album W", [10, 20, 30])])
        eng = self.engine(http, unresolved=unresolved,
                          providers={"web": {"enabled": True, "shops": False, "gnudb": False}})
        return eng, eng.identify(eng.albums_in(d)[0])

    def test_leave_holds_what_the_web_finds(self):
        eng, p = self.found(LEAVE)
        self.assertEqual((p.status, p.tier, p.provider), ("identified", "partial", "web:Deezer"))
        self.assertIn("leave", p.held)
        a = pl.build([p], eng.profile)["albums"][0]
        self.assertEqual(a["status"], "ready")
        self.assertTrue(pl.held(a))
        self.assertEqual(a["notes"][0], p.held)
        self.assertIn("[HELD]", pl.preview(dict(pl.build([p], eng.profile), albums=[a])))
        # ticked: no longer held
        a["held_ok"] = True
        self.assertFalse(pl.held(a))

    def test_mark_applies_it_as_before(self):
        eng, p = self.found(MARK)
        self.assertEqual((p.tier, p.held), ("partial", ""))
        a = pl.build([p], eng.profile)["albums"][0]
        self.assertFalse(pl.held(a))
        self.assertNotIn("held", a)

    def test_one_disc_or_version_of_a_set_is_never_held(self):
        d = self.album("Band W", "Album W")
        http = WebHttp([deezer_album(22, "Band W", "Album W", [10, 20, 30])])
        eng = self.engine(http, unresolved=LEAVE,
                          providers={"web": {"enabled": True, "shops": False, "gnudb": False}})
        eng._disc_alone = True             # (as _per_disc and _version_alone set it)
        self.assertEqual(eng.identify(eng.albums_in(d)[0]).status, "unresolved")

    def test_allow_partial_off_still_writes_nothing(self):
        _eng, p = self.found(dict(LEAVE, allow_partial=False))
        self.assertEqual(p.status, "unresolved")

    def test_a_held_album_is_never_applied_unticked(self):
        eng, p = self.found(LEAVE)
        a = pl.build([p], eng.profile)["albums"][0]
        ex = executor.Executor.__new__(executor.Executor)
        status, label, detail = ex.album(0, a)
        self.assertEqual((status, label), ("skipped", "Held"))
        self.assertIn("leave", detail)

    def test_the_watcher_keeps_it_unresolved(self):
        eng, p = self.found(LEAVE)
        a = pl.build([p], eng.profile)["albums"][0]
        w = watcher.Watcher.__new__(watcher.Watcher)
        self.assertEqual(w._status_of(a, p, "skipped", eng.profile), "unresolved")

    def test_ticking_it_in_the_preview_applies_it(self):
        from PySide6.QtWidgets import QApplication
        from gui.models import PlanModel
        _app = QApplication.instance() or QApplication([])
        eng, p = self.found(LEAVE)
        a = pl.build([p], eng.profile)["albums"][0]
        m = PlanModel()
        m.set_plan({"albums": [a]})
        self.assertTrue(PlanModel.left_out(a))
        m.set_left_out([a], False)
        self.assertFalse(PlanModel.left_out(a))
        self.assertFalse(pl.held(a))
        m.set_left_out([a], True)
        self.assertTrue(PlanModel.left_out(a))
        self.assertTrue(pl.held(a))


def mc_album(lens, **kw):
    a = {"artist": "Artist Alpha", "title": "First Light", "year": 2003, "discs": 1,
         "tracks": [{"disc": 1, "num": i + 1, "title": f"T{i}", "secs": s} for i, s in enumerate(lens)],
         "barcodes": set(), "catnos": set()}
    a.update(kw)
    return a


def mc_cand(lens, **kw):
    c = {"source": "mb", "id": "x1", "url": "u", "artist": "Artist Alpha", "title": "First Light",
         "year": 2003, "country": "", "label": "L", "catnos": set(), "barcodes": set(), "disc_count": 1,
         "formats": {"cd"}, "tracks": [{"disc": 1, "title": f"T{i}", "secs": s} for i, s in enumerate(lens)]}
    c.update(kw)
    return c


class EVI2_TagCountryNoneOfThemIs(unittest.TestCase):
    """EVI-2: your tags' release country that is no tied pressing's (another tagger wrote GB;
    the album came out in DE and RU only) tells nothing apart: the exact disc ID decides, or
    the country order does (Pallas - The Blinding Darkness)."""
    LENS = (300.0, 400.0, 500.0)
    CFG = dict(mc.DEFAULT_CFG, preferred_countries=[])

    def tied(self, **album_kw):
        a = mc_album(self.LENS, tag_country="GB", **album_kw)
        return [mc.score_candidate(a, mc_cand(self.LENS, id=i, country=c), self.CFG)
                for i, c in (("de", "DE"), ("ru", "RU"))]

    def test_the_exact_disc_id_decides(self):
        tied = self.tied(toc_releases={"de", "ru"}, exact_toc_releases={"de"})
        self.assertTrue(all(s["clues_against"] for s in tied))
        pick = mc.choose_pressing(tied)
        self.assertIs(pick[0], tied[0])                     # (the caller's own entry)
        self.assertIn("disc TOC", pick[1])

    def test_the_country_order_decides_otherwise(self):
        tied = self.tied()
        self.assertIsNone(mc.choose_pressing(tied))
        pick = mc.choose_pressing(tied, ["CA", "US", "XE", "GB", "DE"])
        self.assertIs(pick[0], tied[0])

    def test_a_tag_country_one_pressing_has_still_counts(self):
        a = mc_album(self.LENS, tag_country="GB")
        tied = [mc.score_candidate(a, mc_cand(self.LENS, id=i, country=c), self.CFG)
                for i, c in (("gb", "GB"), ("de", "DE"))]
        pick = mc.choose_pressing(tied, ["DE"])
        self.assertEqual(pick[0]["cand"]["id"], "gb")


class TIE3_OneUncontradicted(unittest.TestCase):
    LENS = (300.0, 400.0, 500.0)
    CFG = dict(mc.DEFAULT_CFG, preferred_countries=[])

    def test_discogs_uk_and_eu_are_gb_and_xe(self):
        self.assertEqual((mc.country_code("UK"), mc.country_code("EU"), mc.country_code("DE")), ("GB", "XE", "DE"))

    def test_the_country_order_finds_a_discogs_uk_pressing(self):
        a = mc_album(self.LENS)
        tied = [mc.score_candidate(a, mc_cand(self.LENS, id=i, country=c), self.CFG)
                for i, c in (("uk", "UK"), ("de", "Germany"))]
        self.assertEqual(mc.choose_pressing(tied, ["GB", "DE"])[0]["cand"]["id"], "uk")

    def test_flac_files_are_not_the_mp3_download(self):
        a = mc_album(self.LENS, audio={"lossless": True, "not_cd": True})
        flac = mc_cand(self.LENS, id="flac", source="discogs", formats={"digital"}, file_codecs={"flac"})
        mp3 = mc_cand(self.LENS, id="mp3", source="discogs", country="UK", formats={"digital"},
                      file_codecs={"mp3"})
        tied = [mc.score_candidate(a, c, self.CFG) for c in (mp3, flac)]
        self.assertIn("MP3 only", " ".join(tied[0]["clues_against"]))
        pick = mc.choose_pressing(tied, ["GB"])
        self.assertEqual(pick[0]["cand"]["id"], "flac")
        self.assertIn("format", pick[1])
        # the one left is contradicted too (hi-res files, a CD): nothing is picked on it
        a = mc_album(self.LENS, audio={"lossless": True, "hires": True})
        cd = mc_cand(self.LENS, id="cd", formats={"cd"})
        tied = [mc.score_candidate(a, c, self.CFG) for c in (mp3, cd)]
        pick = mc.choose_pressing(tied)
        self.assertFalse(pick and pick[0]["cand"]["id"] == "cd" and "format" in pick[1]
                         and "this one is not" in pick[1]["format"], pick)
        # lossy files: the MP3 download is not contradicted
        a = mc_album(self.LENS, audio={"lossless": False})
        self.assertFalse(mc.score_candidate(a, mp3, self.CFG)["clues_against"])


class MED10_PressedDiscsAndTheEU(unittest.TestCase):
    LENS = (300.0, 400.0, 500.0)
    CFG = dict(mc.DEFAULT_CFG, preferred_countries=[])
    MATRIX = "398478 @1 MTS5503 WWW.M-T-S.CO.UK"

    def test_made_in_the_eu_is_not_against_a_uk_pressing(self):
        cand = mc_cand(self.LENS, country="UK")
        agree, against = mc.pressing_clues("Made in the EU", cand)
        self.assertFalse([a for a in against if "made in" in a], against)
        agree, against = mc.pressing_clues("Made in the EU", dict(cand, country="Russia"))
        self.assertTrue([a for a in against if "made in" in a], against)

    def test_made_in_the_eu_still_agrees_with_a_europe_pressing(self):
        agree, _against = mc.pressing_clues("Made in the EU", mc_cand(self.LENS, country="Europe"))
        self.assertIn("country", agree)

    def test_a_pressed_matrix_is_no_cd_r(self):
        a = mc_album(self.LENS, ring_text=self.MATRIX)
        jewel = mc_cand(self.LENS, id="jewel", source="discogs", country="UK", media_formats=["CD"],
                        matrix=[("Matrix / Runout", self.MATRIX)])
        cdr = mc_cand(self.LENS, id="cdr", source="discogs", country="UK", media_formats=["CD-R"])
        tied = []
        for c in (cdr, jewel):
            sc = mc.score_candidate(a, c, self.CFG)
            sc["clues"], sc["clues_against"] = mc.pressing_clues("", c, self.MATRIX)
            tied.append(sc)
        self.assertIn("matrix", tied[1]["clues"])
        pick = mc.choose_pressing(tied, ["GB"])
        self.assertIs(pick[0], tied[1])
        self.assertEqual(tied[0]["clues_against"], [])          # (the caller's entries untouched)

    def test_the_hub_line_further_out_is_read(self):
        import inspect
        self.assertIn("(0.33, 0.42, False)", inspect.getsource(ma._hub_strips))


class WEB2_TitleSearchLastResort(unittest.TestCase):
    """WEB-2: the album's own name searched on the web once, last; only links are kept."""
    CFG = dict(mc.DEFAULT_CFG, preferred_countries=[], web_search={"engines": ["marginalia"]})
    PAGE = ("https://www.discogs.com/release/13883123-Brand-X-Live\n"
            "https://musicbrainz.org/release/a3b90699-2f29-453b-b41a-38ff46ed566f\n"
            "https://brandx.bandcamp.com/album/live-rites-of-spring/\nhttps://example.org/review")

    def run_step(self, final=None, cfg=None, album=None):
        album = album or {"artist": "Brand X", "title": "Live - The Rites Of Spring Festival 2018", "tracks": []}
        out, asked, fetched = {}, [], []

        def search(http, query, cfg=None, notes=None):
            asked.append(query)
            return self.PAGE, "marginalia"

        def fetch(http, dm, ids, source, have):
            fetched.append((source, list(ids)))
            return []
        with mock.patch.object(ma, "web_search", search), mock.patch.object(ma, "_fetch_linked", fetch):
            r = ma.title_web_step(None, album, cfg or self.CFG, None, None, None, final or {"status": "review"},
                                  "", True, True, out, [])
        return r, out, asked, fetched, album

    def test_links_only_and_once(self):
        _r, out, asked, fetched, album = self.run_step()
        title = mc.search_title("Live - The Rites Of Spring Festival 2018")
        self.assertEqual(asked, [f'"{title}" "Brand X"'])
        self.assertEqual(dict(fetched), {"discogs": ["13883123"], "mb": ["a3b90699-2f29-453b-b41a-38ff46ed566f"]})
        self.assertEqual(out["web_bandcamp"], ["https://brandx.bandcamp.com/album/live-rites-of-spring"])
        self.assertTrue(album["web_titled"])
        # searched once only
        _r, _o, asked2, _f, _a = self.run_step(album=album)
        self.assertEqual(asked2, [])

    def test_a_band_named_x_is_searched_whole(self):
        self.assertEqual(mc.artist_parts("Brand X"), ["Brand X"])
        self.assertEqual(mc.artist_parts("Malcolm X"), ["Malcolm X"])
        self.assertEqual(mc.artist_parts("Artist A x Artist B"), ["Artist A", "Artist B"])

    def test_no_engine_no_search(self):
        _r, _o, asked, _f, _a = self.run_step(cfg=dict(self.CFG, web_search={"engines": []}))
        self.assertEqual(asked, [])

    def test_a_sure_pick_is_not_searched(self):
        _r, _o, asked, _f, _a = self.run_step(final={"status": "auto"})
        self.assertEqual(asked, [])


class WEB2_BandcampFromTheSearch(Base):
    def test_a_linked_bandcamp_page_is_a_web_album(self):
        d = self.album("Band B", "Album B")
        eng = self.engine(WebHttp([]), unresolved=MARK,
                          providers={"web": {"enabled": True, "shops": False, "gnudb": False,
                                             "sources": ["bandcamp"]}})
        album = ma.read_album(str(d), "Band B", d.name)
        files = [t["path"] for t in album["tracks"]]
        cand = {"source": "bandcamp", "id": "b", "url": "https://bandb.bandcamp.com/album/album-b",
                "artist": "Band B", "artist_alt": "", "title": "Album B", "year": 2005, "date": "2005",
                "country": "", "label": "", "catnos": set(), "barcodes": set(), "disc_count": 1,
                "tracks": [{"title": f"T{i}", "secs": s, "num": i + 1, "disc": 1} for i, s in enumerate((10, 20, 30))],
                "formats": {"digital"}, "digital_only": True, "genre": [], "cover": ""}
        with mock.patch("web_tag.bandcamp_album", lambda http, u: cand if "bandb" in u else None), \
                mock.patch("web_tag.bandcamp", lambda http, a, t: []):
            p = eng._web({"web_bandcamp": ["https://bandb.bandcamp.com/album/album-b"]}, album, files,
                         dict(source_dir=str(d), files=files, provider_errors={}))
        self.assertIsNotNone(p)
        self.assertEqual((p.tier, p.provider), ("partial", "web:Bandcamp"))


class EVI2_ADownloadWithoutACatalogueNumber(unittest.TestCase):
    """John Mayer - Paradise Valley: the CD sleeve bundled with the 24-bit download prints the
    CD's catalogue number; the download's entry lists none: unknown, not another number."""

    def report(self, formats):
        dl = {"cand": {"id": "dl", "source": "discogs", "catnos": set(), "barcodes": set(), "formats": formats}}
        cd = {"cand": {"id": "cd", "source": "discogs", "catnos": {mc.norm_code("SICP 3887")}, "barcodes": set(),
                       "formats": {"cd"}}}
        album = {"barcodes": set(), "printed_text": ""}
        return ma.scans_report(dl, album, "Columbia\nSICP-3887", [{"scored": [dl, cd]}], True)

    def test_the_download_is_not_against_it(self):
        self.assertNotIn("catalogue_number", self.report({"digital"})["against"])

    def test_a_download_with_the_cds_barcode_is_not_proven(self):
        dl = {"cand": {"id": "dl", "source": "discogs", "catnos": set(), "barcodes": {"886443927087"},
                       "formats": {"digital"}}}
        cd = {"cand": {"id": "cd", "source": "discogs", "catnos": {mc.norm_code("SICP 3887")},
                       "barcodes": {"886443927087"}, "formats": {"cd"}}}
        album = {"barcodes": set(), "printed_text": "", "ocr_barcodes": {"886443927087"}}
        r = ma.scans_report(dl, album, "SICP-3887", [{"scored": [dl, cd]}], True)
        self.assertNotEqual(r["state"], "proven")
        self.assertNotIn("catalogue_number", r["against"])

    def test_a_cd_entry_without_its_number_still_is(self):
        self.assertIn("catalogue_number", self.report({"cd"})["against"])


class BAR1_ProvenOnTheScansIsFiled(unittest.TestCase):
    """Pallas - The Sentinel [VinylRip]: barcode and catalogue number printed on the scans, 4 of
    6 vinyl lengths agreeing, 93.6: filed (organize_min_confidence 95)."""

    def prop(self, **scans):
        p = pl.Proposal(source_dir="/x", files=[], status="identified", tier="exact", confidence=93.6,
                        most_lengths=scans.pop("most_lengths", True))
        p.scans = dict({"state": "proven", "found": ["barcode", "catalogue_number"], "against": [],
                        "printed": True}, **scans)
        return p

    def test_filed(self):
        prof = pf.from_dict({"name": "t"})
        self.assertIn("barcode or catalogue number", pl._files_on_evidence(self.prop(), prof, 93.6))

    def test_not_when_the_scans_show_something_against_it(self):
        prof = pf.from_dict({"name": "t"})
        self.assertEqual(pl._files_on_evidence(self.prop(against=["country"]), prof, 93.6), "")
        self.assertEqual(pl._files_on_evidence(self.prop(printed=False), prof, 93.6), "")
        # (fewer than half the lengths agree, or tracks missing: another master or edition)
        self.assertEqual(pl._files_on_evidence(self.prop(most_lengths=False), prof, 93.6), "")
        self.assertEqual(pl._files_on_evidence(self.prop(), prof, 89.0), "")
        self.assertEqual(pl._files_on_evidence(self.prop(), pf.from_dict(
            {"name": "t", "matching": {"extra_careful": True}}), 93.6), "")


if __name__ == "__main__":
    unittest.main()
