"""Offline tests for match_core.py and the match_albums.py pipeline.

Run:  python3 test_match.py [plugin-dir]
Needs mutagen and ffmpeg (ffmpeg only to build the tiny synthetic library).
"""
import copy
import csv
import json
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent   # the scripts sit above tests/
sys.path.insert(0, str(HERE))
import match_core as mc  # noqa: E402
import match_albums as ma  # noqa: E402

PLUGIN_DIR = sys.argv[1] if len(sys.argv) > 1 else "~/Documents/Github/picard-discogs-enrichment"
CFG = dict(mc.DEFAULT_CFG)


def album(**kw):
    a = {"artist": "Artist Alpha", "title": "First Light", "year": 2010, "discs": 1,
         "tracks": [{"disc": 1, "num": i + 1, "title": f"T{i}", "secs": s}
                    for i, s in enumerate((200.0, 305.0, 410.0))],
         "barcodes": set(), "catnos": set()}
    a.update(kw)
    return a


def cand(**kw):
    c = {"source": "mb", "id": "x1", "url": "u", "artist": "Artist Alpha", "title": "First Light",
         "year": 2010, "country": "CA", "label": "L", "catnos": set(), "barcodes": set(),
         "disc_count": 1,
         "tracks": [{"disc": 1, "title": f"T{i}", "secs": s} for i, s in enumerate((200, 305, 410))]}
    c.update(kw)
    return c


class Core(unittest.TestCase):
    def test_similarity_helpers(self):
        self.assertGreater(mc.artist_sim("Max Corbacho; Bruno Sanfilippo", "Bruno Sanfilippo & Max Corbacho"), 0.95)
        self.assertGreater(mc.artist_sim("The Cure", "Cure"), 0.95)
        self.assertLess(mc.artist_sim("Artist Alpha", "Totally Different"), 0.5)
        self.assertGreater(mc.title_sim("First Light (Remastered 2019)", "First Light"), 0.95)
        self.assertGreater(mc.title_sim("Fate_ The Best Of", "Fate: The Best Of"), 0.9)

    def test_perfect_single_candidate_is_auto(self):
        r = mc.classify([mc.score_candidate(album(), cand(), CFG)], CFG)
        self.assertEqual(r["status"], "auto", r["why"])

    def test_two_identical_pressings_go_to_review(self):
        a = mc.score_candidate(album(), cand(id="p1"), CFG)
        b = mc.score_candidate(album(), cand(id="p2"), CFG)
        r = mc.classify([a, b], CFG)
        self.assertEqual(r["status"], "review")
        self.assertTrue(any("runner-up" in w for w in r["why"]))

    def test_year_alone_does_not_pick_a_pressing_by_default(self):
        """Folder years follow originaldate, so they cannot tell pressings apart."""
        a = mc.score_candidate(album(), cand(id="p1", year=2010), CFG)
        b = mc.score_candidate(album(), cand(id="p2", year=2015), CFG)
        r = mc.classify([a, b], CFG)
        self.assertEqual(r["best"]["cand"]["id"], "p1")      # still ranked first
        self.assertEqual(r["status"], "review", r["why"])

    def test_year_tiebreak_can_be_switched_on(self):
        cfg = dict(CFG, auto_min_margin_year_decided=3.0)
        a = mc.score_candidate(album(), cand(id="p1", year=2010), cfg)
        b = mc.score_candidate(album(), cand(id="p2", year=2015), cfg)
        self.assertEqual(mc.classify([a, b], cfg)["status"], "auto")

    def test_numeric_catalogue_number_is_not_matched_from_ocr_text(self):
        c = cand(catnos={"2003"})
        s = mc.score_candidate(album(), c, CFG, ocr_text="Recorded 2003, Berlin")
        self.assertEqual(s["code"], "")

    def test_barcode_found_in_ocr_text_with_spaces(self):
        c = cand(barcodes={"0801837013429"})
        s = mc.score_candidate(album(), c, CFG, ocr_text="0 801837 01342 9  Made in EU")
        self.assertEqual(s["code"], "barcode (OCR)")

    def test_extra_bonus_track_is_a_near_miss_review(self):
        c = cand(tracks=cand()["tracks"] + [{"disc": 1, "title": "Bonus", "secs": 99}])
        r = mc.classify([mc.score_candidate(album(), c, CFG)], CFG)
        self.assertEqual(r["status"], "review")
        self.assertIn("track count differs", r["why"])

    def test_missing_durations_never_auto(self):
        c = cand(tracks=[{"disc": 1, "title": f"T{i}", "secs": 0} for i in range(3)])
        r = mc.classify([mc.score_candidate(album(), c, CFG)], CFG)
        self.assertNotEqual(r["status"], "auto")
        self.assertIn("durations unavailable", r["why"])

    def test_wrong_durations_not_auto(self):
        c = cand(tracks=[{"disc": 1, "title": "x", "secs": s} for s in (100, 500, 90)])
        r = mc.classify([mc.score_candidate(album(), c, CFG)], CFG)
        self.assertNotEqual(r["status"], "auto")

    def test_wrong_artist_not_auto(self):
        r = mc.classify([mc.score_candidate(album(), cand(artist="Someone Else Entirely"), CFG)], CFG)
        self.assertNotEqual(r["status"], "auto")

    def test_barcode_match_decides_without_durations(self):
        c = cand(barcodes={"0123456789012"}, tracks=[{"disc": 1, "title": "x", "secs": 0}] * 3)
        a = album(barcodes={"0123456789012"})
        r = mc.classify([mc.score_candidate(a, c, CFG)], CFG)
        self.assertEqual(r["status"], "auto", r["why"])

    def test_barcode_mismatch_blocks_auto(self):
        c = cand(barcodes={"999"})
        r = mc.classify([mc.score_candidate(album(barcodes={"123"}), c, CFG)], CFG)
        self.assertNotEqual(r["status"], "auto")

    def test_tolerance_scales_for_long_tracks(self):
        long_a = album(tracks=[{"disc": 1, "num": 1, "title": "t", "secs": 1210.0}])
        long_c = cand(tracks=[{"disc": 1, "title": "t", "secs": 1200}])
        s = mc.score_candidate(long_a, long_c, CFG)
        self.assertEqual(s["dur_frac"], 1.0)   # 10 s off on a 20-minute track

    def test_rip_format_and_label_brackets_do_not_hurt_title(self):
        self.assertEqual(mc.search_title("This is America! [24bit 96khz]"), "This is America!")
        self.assertEqual(mc.search_title("Blade Runner - Esper Definitive Collectors Edition [4CD]"),
                         "Blade Runner - Esper Definitive Collectors Edition")
        self.assertEqual(mc.search_title("Dome Of The Rock [Act 144]"), "Dome Of The Rock")
        self.assertEqual(mc.search_title("Nattesne (Eilean 85)"), "Nattesne")
        self.assertGreater(mc.title_sim("This is America! [24bit 96khz]", "This Is America!"), 0.95)
        self.assertGreater(mc.title_sim("Dome Of The Rock [Act 144]", "Dome of the Rock"), 0.95)
        self.assertEqual(mc.search_title("[Untitled]"), "[Untitled]")   # never empties a title

    def test_rip_and_format_tags_are_stripped(self):
        tags = ["[2CD]", "[*Vinyl Rip*]", "(Vinyl Rip)", "[Vinyl]", "[LP]", "[3LP]", "[Cassette Rip]",
                "[Tape]", "[MP3 320]", "[V0]", "[320kbps]", "[DVD]", "[HDCD]", "[Digital]", "[WEB]",
                "[24-96]", "[24bit 96khz]", "[CD+DVD]", "[Boxset]", "[ape]", "[aiff]", "[Rip]",
                "[FLAC]", "[DSD64]", "[SACD-R]", "[Lossless]", "[4CD]",
                "(Disc 1)", "[Disc 2]", "(disc one)", "[CD1]", "(CD 2)", "[Disk 3]", "(Disc II)"]
        for tag in tags:
            self.assertEqual(mc.strip_edition(f"Some Album {tag}"), "Some Album", tag)

    def test_real_title_parts_are_kept(self):
        for t in ["Album [No Vox]", "Album (Live)", "Album (Live at the Roxy)", "Album (Part II)",
                  "Album (Discovery)", "Album (Discography)"]:
            self.assertEqual(mc.strip_edition(t), t)
        self.assertEqual(mc.strip_edition("Vinyl Countdown"), "Vinyl Countdown")   # not bracketed
        self.assertEqual(mc.strip_edition("The Rip Tide"), "The Rip Tide")

    def test_nothing_found(self):
        self.assertEqual(mc.classify([], CFG)["status"], "nomatch")

    def test_combine_prefers_mb_auto_then_discogs_auto(self):
        good = mc.classify([mc.score_candidate(album(), cand(), CFG)], CFG)
        dgood = mc.classify([mc.score_candidate(album(), cand(source="discogs", id="d"), CFG)], CFG)
        weak = mc.classify([mc.score_candidate(album(), cand(artist="Nope", title="Nope"), CFG)], CFG)
        self.assertEqual(mc.combine(good, dgood)["best"]["cand"]["source"], "mb")
        self.assertEqual(mc.combine(weak, dgood)["best"]["cand"]["source"], "discogs")

    def test_ocr_text_can_confirm_catalogue_number(self):
        c = cand(catnos={"abc123"})
        s = mc.score_candidate(album(), c, CFG, ocr_text="Cat. No. ABC-123 Made in Canada")
        self.assertEqual(s["code"], "catalogue number (OCR)")


# --------------------------------------------------------------------------
# End-to-end with canned API responses
# --------------------------------------------------------------------------
def mb_release(rid, artist, title, year, lens_ms, country="CA"):
    return {"id": rid, "title": title, "date": str(year), "country": country,
            "artist-credit": [{"name": artist, "joinphrase": ""}],
            "label-info": [], "media": [{"format": "CD", "tracks": [
                {"title": f"T{i}", "length": ms} for i, ms in enumerate(lens_ms)]}]}


def mb_hit(rel):
    return {"id": rel["id"], "track-count": sum(len(m["tracks"]) for m in rel["media"])}


def dg_release(rid, artist, title, year, durs):
    return {"id": rid, "title": title, "year": year, "uri": f"https://www.discogs.com/release/{rid}",
            "artists": [{"name": artist}], "labels": [{"name": "Lbl", "catno": "LB-1"}],
            "identifiers": [], "country": "Canada",
            "tracklist": [{"type_": "track", "position": str(i + 1), "title": f"T{i}", "duration": d}
                          for i, d in enumerate(durs)]}


class FakeHttp:
    def __init__(self):
        self.calls = []
        a_rel = mb_release("mb-alpha", "Artist Alpha", "First Light", 2010, [10000, 20000, 30000])
        t1 = mb_release("mb-tie-1", "Artist Tie", "Twin Pressing", 2005, [10000, 20000, 30000])
        t2 = mb_release("mb-tie-2", "Artist Tie", "Twin Pressing", 2005, [10000, 20000, 30000])
        self.mb = {r["id"]: r for r in (a_rel, t1, t2)}
        self.mb_search = {"First Light": [a_rel], "Twin Pressing": [t1, t2]}
        beta = dg_release(501, "Artist Beta", "Slow Drift", 2012, ["0:10", "0:20", "0:30"])
        nodur = dg_release(502, "Artist NoDur", "Quiet Room", 2013, ["", "", ""])
        bonus = dg_release(503, "Artist Gamma", "Bonus Round", 2014, ["0:10", "0:20", "0:30", "0:40"])
        self.dg = {r["id"]: r for r in (beta, nodur, bonus)}
        self.dg_search = {"Slow Drift": [beta], "Quiet Room": [nodur], "Bonus Round": [bonus]}

    def get_json(self, url, host):
        self.calls.append((host, url))
        import urllib.parse as up
        if host == "mb":
            if "/ws/2/release/?" in url:
                query = up.unquote(url.split("query=")[1].split("&")[0])
                for title, rels in self.mb_search.items():
                    if title in query:
                        return {"releases": [mb_hit(r) for r in rels]}
                return {"releases": []}
            rid = url.split("/release/")[1].split("?")[0]
            return self.mb.get(rid)
        if "/database/search" in url:
            qs = up.unquote(url)
            for title, rels in self.dg_search.items():
                if title in qs:
                    return {"results": [{"id": r["id"], "title": f"{r['artists'][0]['name']} - {r['title']}",
                                         "year": str(r["year"])} for r in rels]}
            return {"results": []}
        return self.dg.get(int(url.rsplit("/", 1)[1]))


def build_library(root):
    def make(artist, folder, tag_title, tag_year, n=3):
        d = Path(root) / artist / folder
        d.mkdir(parents=True)
        for i in range(n):
            subprocess.run(
                ["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i",
                 f"sine=frequency={300 + 50 * i}:duration={10 * (i + 1)}", "-ar", "8000", "-ac", "1",
                 "-metadata", f"title=T{i}", "-metadata", f"album={tag_title}",
                 "-metadata", f"albumartist={artist}", "-metadata", f"date={tag_year}",
                 "-metadata", f"track={i + 1}", str(d / f"{i + 1:02d}.flac"), "-y"], check=True)
        return d
    rows = []
    for artist, folder, title, year in [
        ("Artist Alpha", "2010 - First Light", "First Light", 2010),
        ("Artist Tie", "2005 - Twin Pressing", "Twin Pressing", 2005),
        ("Artist Beta", "2012 - Slow Drift", "Slow Drift", 2012),
        ("Artist NoDur", "2013 - Quiet Room", "Quiet Room", 2013),
        ("Artist Gamma", "2014 - Bonus Round", "Bonus Round", 2014),
        ("Artist Delta", "2000 - Unknown Thing", "Unknown Thing", 2000),
    ]:
        d = make(artist, folder, title, year)
        rows.append({"AlbumArtistFolder": artist, "AlbumFolder": folder, "FullPath": str(d),
                     "Status": "untagged", "OcrCandidateImages": ""})
    return rows


class ExactPhraseHttp(FakeHttp):
    """Like the real services: a quoted title must match the whole title, not part of it."""
    def __init__(self):
        super().__init__()
        g = mb_release("mb-garbage", "Autechre", "Garbage", 1995, [10000, 20000, 30000])
        other = mb_release("mb-other", "miles william", "garbage ep", 2016, [5000, 6000, 7000])
        self.mb = {r["id"]: r for r in (g, other)}
        self.mb_search = {"Garbage": [g], "garbage ep": [other]}
        self.dg_search, self.dg = {}, {}

    def get_json(self, url, host):
        import urllib.parse as up
        if host == "mb" and "/ws/2/release/?" in url:
            self.calls.append((host, url))
            query = up.unquote(url.split("query=")[1].split("&")[0])
            found = []
            for title, rels in self.mb_search.items():
                if f'release:"{title}"'.casefold() in query.casefold() or f'"{title}" AND'.casefold() in query.casefold():
                    if f'"{title}"'.casefold() in query.casefold():
                        found += rels
            return {"releases": [mb_hit(r) for r in found]}
        return super().get_json(url, host)


class ReleaseTypeWord(unittest.TestCase):
    def test_title_with_ep_matches_title_without(self):
        self.assertGreaterEqual(mc.title_sim("Garbage EP", "Garbage"), 0.95)
        self.assertGreaterEqual(mc.title_sim("Peel Session EP", "Peel Session"), 0.95)
        self.assertGreaterEqual(mc.title_sim("Movement (E.P.)", "Movement"), 0.95)

    def test_drop_release_type(self):
        self.assertEqual(mc.drop_release_type("Peel Session 2 EP"), "Peel Session 2")
        self.assertEqual(mc.drop_release_type("Garbage [EP]"), "Garbage")
        self.assertEqual(mc.drop_release_type("Blue Monday (Single)"), "Blue Monday")
        self.assertEqual(mc.drop_release_type("The EP"), "The EP")          # never a bare article
        self.assertEqual(mc.drop_release_type("EP"), "EP")                  # never empty
        self.assertEqual(mc.drop_release_type("Epilogue"), "Epilogue")

    def test_folder_named_ep_finds_the_release_listed_without_it(self):
        tmp = Path(tempfile.mkdtemp())
        try:
            d = tmp / "Autechre" / "1995 - Garbage EP"
            d.mkdir(parents=True)
            for i in range(3):
                subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i",
                                f"sine=frequency={300 + 50 * i}:duration={10 * (i + 1)}", "-ar", "8000", "-ac", "1",
                                "-metadata", f"title=T{i}", "-metadata", "album=Garbage EP",
                                "-metadata", "albumartist=Autechre", "-metadata", "date=1995",
                                "-metadata", f"track={i + 1}", str(d / f"{i + 1:02d}.flac"), "-y"], check=True)
            row = {"AlbumArtistFolder": "Autechre", "AlbumFolder": d.name, "FullPath": str(d),
                   "Status": "untagged", "OcrCandidateImages": ""}
            o = ma.process(row, ExactPhraseHttp(), CFG, ma.load_plugin(PLUGIN_DIR), types.SimpleNamespace(ocr=False))
            self.assertEqual((o["status"], o["best"]["id"]), ("auto", "mb-garbage"), o["why"])
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class Collaborations(unittest.TestCase):
    def test_credit_missing_a_collaborator_is_not_close_enough(self):
        # Croatian Variations: MusicBrainz credits only Blaine L. Reininger
        self.assertLess(mc.artist_sim("Steven Brown & Blaine L. Reininger", "Blaine L. Reininger"), 0.85)
        self.assertEqual(mc.missing_collaborators("Steven Brown & Blaine L. Reininger", ["Blaine L. Reininger"]),
                         ["Steven Brown"])

    def test_full_credit_in_any_order_still_matches(self):
        self.assertGreaterEqual(mc.artist_sim("Steven Brown & Blaine L. Reininger",
                                              "Blaine L. Reininger & Steven Brown"), 0.95)
        self.assertEqual(mc.missing_collaborators("Simon & Garfunkel", ["Simon & Garfunkel"]), [])
        self.assertEqual(mc.missing_collaborators("Max Corbacho; Bruno Sanfilippo",
                                                  ["Bruno Sanfilippo & Max Corbacho"]), [])

    def test_extra_names_in_the_database_credit_are_fine(self):
        self.assertGreaterEqual(mc.artist_sim("Vanessa Wagner & Wilhem Latchoumia",
                                              "Adams, Glass; Vanessa Wagner, Wilhem Latchoumia"), 0.85)

    def test_missing_collaborator_goes_to_review_and_says_who(self):
        a = album(artist="Steven Brown & Blaine L. Reininger", title="Croatian Variations")
        c = cand(artist="Blaine L. Reininger", title="Croatian Variations")
        s = mc.score_candidate(a, c, CFG)
        self.assertTrue(any("credit lacks Steven Brown" in n for n in s["notes"]), s["notes"])
        r = mc.classify([s], CFG)
        self.assertNotEqual(r["status"], "auto", r["why"])
        self.assertTrue(any("Steven Brown" in w for w in r["why"]), r["why"])

    def test_full_collaboration_credit_is_auto(self):
        a = album(artist="Steven Brown & Blaine L. Reininger", title="Croatian Variations")
        c = cand(artist="Steven Brown & Blaine L. Reininger", title="Croatian Variations")
        self.assertEqual(mc.classify([mc.score_candidate(a, c, CFG)], CFG)["status"], "auto")


class CatalogueTags(unittest.TestCase):
    def test_labelno_tag_with_several_numbers_is_read(self):
        tmp = Path(tempfile.mkdtemp())
        try:
            p = tmp / "01.flac"
            subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "sine=duration=1", "-ar", "8000",
                            "-metadata", "LABELNO=MASO CD 90041;12957.0041.2", str(p), "-y"], check=True)
            info = ma.read_file(p)
            self.assertEqual(mc.split_codes(info["catno"]), ["MASO CD 90041", "12957.0041.2"])
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class FolderSpellings(unittest.TestCase):
    def test_braces_are_brackets(self):
        # Autechre/2020 - PLUS {Japan BRC-657}
        self.assertEqual(mc.search_title("PLUS {Japan BRC-657}"), "PLUS")
        self.assertGreaterEqual(mc.title_sim("PLUS {Japan BRC-657}", "PLUS"), 0.95)
        self.assertIn("BRC-657", mc.catno_hints("PLUS {Japan BRC-657}"))

    def test_letters_without_accents_fold_to_plain_letters(self):
        # Autechre & The Hafler Trio/2003 - ae3o & h3ae  vs  "æ³o & h³æ"
        self.assertGreaterEqual(mc.title_sim("ae3o & h3ae", "æ³o & h³æ"), 0.95)
        self.assertEqual(mc.fold("Ørsted Łódź Straße Þór"), mc.fold("Orsted Lodz Strasse Thor"))


class MovedFolders(unittest.TestCase):
    def test_folder_moved_away_is_not_a_nomatch(self):
        # Croatian Variations: retagged in Picard while the loop ran, which moved its folder
        row = {"AlbumArtistFolder": "Gone", "AlbumFolder": "1992 - Gone", "FullPath": "/nonexistent/Gone/1992 - Gone",
               "Status": "untagged", "OcrCandidateImages": ""}
        o = ma.process(row, FakeHttp(), CFG, None, types.SimpleNamespace(ocr=False))
        self.assertEqual(o["status"], "moved")
        self.assertIn("no longer exists", o["why"][0])
        self.assertEqual(o["version"], ma.result_version())          # not redone every round

    def test_moved_albums_get_their_own_list(self):
        tmp = Path(tempfile.mkdtemp())
        try:
            db = sqlite3.connect(":memory:")
            db.execute("CREATE TABLE results (path TEXT PRIMARY KEY, result TEXT)")
            o = {"path": "/x", "artist_folder": "A", "album_folder": "B", "tag_artist": "", "tag_title": "",
                 "tag_year": None, "tracks": 0, "status": "moved", "why": ["folder no longer exists"],
                 "best": None, "alts": []}
            db.execute("INSERT INTO results VALUES (?,?)", ("/x", json.dumps(o)))
            counts = ma.export(db, tmp)
            self.assertEqual(counts.get("moved"), 1)
            self.assertEqual(counts.get("nomatch"), 0)
            self.assertTrue((tmp / "matches_moved.csv").exists())
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class VersionStamp(unittest.TestCase):
    def test_plugin_code_is_part_of_the_version(self):
        tmp = Path(tempfile.mkdtemp())
        try:
            f = tmp / "discogs_mapping.py"
            f.write_text("A = 1\n")
            v1 = ma._script_version([f])
            f.write_text("A = 2\n")
            self.assertNotEqual(v1, ma._script_version([f]))
            self.assertNotEqual(ma._script_version(), v1)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_plugin_files_are_found_next_to_the_loaded_module(self):
        names = [p.name for p in ma.plugin_files(ma.load_plugin(PLUGIN_DIR))]
        self.assertEqual(names, ["discogs_mapping.py", "discogs_countries.py"])


class CatalogueZeros(unittest.TestCase):
    def test_leading_zeros_inside_catalogue_numbers(self):
        # Zoviet France - Norsch: CHARRMCD03 (MusicBrainz) is CHARRMCD3 (Discogs)
        self.assertEqual(mc.norm_code("CHARRMCD03"), mc.norm_code("CHARRMCD3"))
        self.assertEqual(mc.norm_code("BRC-067"), mc.norm_code("BRC 67"))
        self.assertNotEqual(mc.norm_code("CHARRMCD31"), mc.norm_code("CHARRMCD3"))
        self.assertNotEqual(mc.norm_code("CD100"), mc.norm_code("CD10"))


class Pipeline(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        cls.rows = build_library(Path(cls.tmp) / "lib")
        cls.dm = ma.load_plugin(PLUGIN_DIR)
        cls.http = FakeHttp()
        args = types.SimpleNamespace(ocr=False)
        cls.out = {r["AlbumFolder"]: ma.process(r, cls.http, CFG, cls.dm, args) for r in cls.rows}

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_musicbrainz_exact_is_auto(self):
        o = self.out["2010 - First Light"]
        self.assertEqual((o["status"], o["best"]["source"], o["best"]["id"]), ("auto", "mb", "mb-alpha"), o["why"])

    def test_auto_mb_skips_discogs_lookup(self):
        calls = [u for h, u in self.http.calls if h == "discogs" and "First" in u]
        self.assertEqual(calls, [])

    def test_identical_pressings_need_review(self):
        o = self.out["2005 - Twin Pressing"]
        self.assertEqual(o["status"], "review", o["why"])
        self.assertTrue(any("runner-up" in w for w in o["why"]))

    def test_discogs_fallback_auto(self):
        o = self.out["2012 - Slow Drift"]
        self.assertEqual((o["status"], o["best"]["source"], o["best"]["id"]), ("auto", "discogs", "501"), o["why"])

    def test_discogs_without_durations_is_review(self):
        o = self.out["2013 - Quiet Room"]
        self.assertEqual(o["status"], "review", o["why"])

    def test_bonus_track_near_miss_is_review(self):
        o = self.out["2014 - Bonus Round"]
        self.assertEqual(o["status"], "review")
        self.assertTrue(any("track count" in w for w in o["why"]))

    def test_unknown_album_is_nomatch(self):
        o = self.out["2000 - Unknown Thing"]
        self.assertEqual(o["status"], "nomatch")

    def test_csv_export_splits_by_status(self):
        db = sqlite3.connect(":memory:")
        db.execute("CREATE TABLE results (path TEXT PRIMARY KEY, result TEXT)")
        for o in self.out.values():
            db.execute("INSERT INTO results VALUES (?,?)", (o["path"], json.dumps(o, default=list)))
        counts = ma.export(db, self.tmp)
        self.assertEqual(counts, {"auto": 2, "review": 3, "nomatch": 1, "moved": 0})
        with open(Path(self.tmp) / "matches_auto.csv", encoding="utf-8") as fh:
            ids = {r["ID"] for r in csv.DictReader(fh)}
        self.assertEqual(ids, {"mb-alpha", "501"})


class RealWorldCases(unittest.TestCase):
    """Cases taken from a real first run."""
    @classmethod
    def setUpClass(cls):
        cls.dm = ma.load_plugin(PLUGIN_DIR)

    @staticmethod
    def _mk(p, secs, freq=440, **tags):
        p.parent.mkdir(parents=True, exist_ok=True)
        meta = sum((["-metadata", f"{k}={v}"] for k, v in tags.items()), [])
        subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i",
                        f"sine=frequency={freq}:duration={secs}",
                        "-ar", "8000", *meta, str(p), "-y"], check=True)

    def test_files_without_track_numbers_use_natural_order(self):
        d = Path(tempfile.mkdtemp())
        try:
            for i in range(1, 12):
                self._mk(d / f"{i} - song.flac", i + 1, album="X")
            got = [round(t["secs"]) for t in ma.read_album(d, "A", "2000 - X")["tracks"]]
            self.assertEqual(got, list(range(2, 13)))
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_disc_folders_without_disc_tags_do_not_interleave(self):
        d = Path(tempfile.mkdtemp())
        try:
            for disc in (1, 2):
                for n in (1, 2, 3):
                    self._mk(d / f"CD{disc}" / f"0{n}.flac", disc * 10 + n, album="Y", track=n)
            alb = ma.read_album(d, "A", "2000 - Y")
            self.assertEqual([round(t["secs"]) for t in alb["tracks"]], [11, 12, 13, 21, 22, 23])
            self.assertEqual([t["disc"] for t in alb["tracks"]], [1, 1, 1, 2, 2, 2])
            self.assertEqual(alb["discs"], 2)
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_duplicate_copies_in_two_formats_count_once(self):
        d = Path(tempfile.mkdtemp())
        try:
            for n in (1, 2):
                self._mk(d / f"0{n}.flac", 5 + n, album="Z", track=n)
                self._mk(d / f"0{n}.opus", 5 + n, album="Z", track=n)
            alb = ma.read_album(d, "A", "2000 - Z")
            self.assertEqual(len(alb["tracks"]), 2)
            self.assertEqual(alb["dropped_formats"], ["*.opus"])
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_same_format_copies_in_two_folders_count_once(self):
        d = Path(tempfile.mkdtemp())
        try:
            for res, size_hint in (("16-44", 8000), ("24-96", 16000)):
                for n, secs in ((1, 7), (2, 9), (3, 12)):
                    p = d / res / f"0{n}.flac"
                    p.parent.mkdir(parents=True, exist_ok=True)
                    subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i",
                                    f"sine=duration={secs}", "-ar", str(size_hint), "-metadata",
                                    "album=Y", "-metadata", f"track={n}", "-metadata", f"title=T{n}",
                                    str(p), "-y"], check=True)
            alb = ma.read_album(d, "A", "2000 - Y")
            self.assertEqual((len(alb["tracks"]), alb["discs"]), (3, 1))
            self.assertEqual(alb["dropped_formats"], ["16-44/*.flac"])   # larger copy kept
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_real_discs_in_different_formats_are_both_kept(self):
        d = Path(tempfile.mkdtemp())
        try:
            for n, secs in ((1, 4), (2, 5)):
                self._mk(d / "Disc 1" / f"0{n}.flac", secs, album="Z", track=n, title=f"A{n}")
            for n, secs in ((1, 11), (2, 13)):
                self._mk(d / "Disc 2" / f"0{n}.mp3", secs, album="Z", track=n, title=f"B{n}")
            alb = ma.read_album(d, "A", "2000 - Z")
            self.assertEqual((len(alb["tracks"]), alb["discs"], alb["dropped_formats"]), (4, 2, []))
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_two_discs_of_equal_length_are_not_merged(self):
        """e.g. two discs that each hold one long piece of about the same length"""
        d = Path(tempfile.mkdtemp())
        try:
            self._mk(d / "CD1" / "01.flac", 20, freq=330, album="Q", track=1, title="Part One")
            self._mk(d / "CD2" / "01.flac", 20, freq=550, album="Q", track=1, title="Part Two")
            self.assertEqual(len(ma.read_album(d, "A", "2000 - Q")["tracks"]), 2)
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_twin_files_in_one_folder_count_once(self):
        """Real layout from the library: every track saved twice in one folder
        under two naming-script versions, with no title tags to go on."""
        d = Path(tempfile.mkdtemp())
        try:
            names = ["ACAaM", "TSAO", "WRWFHW"]
            for n, (name, secs) in enumerate(zip(names, (7, 9, 12)), 1):
                self._mk(d / f"00{n}.{name}.flac", secs, album="Touch Dissolves", track=n)
                self._mk(d / f"0{n}.{name.upper()}.flac", secs, album="Touch Dissolves", track=n)
            alb = ma.read_album(d, "Aaron Martin", "2018 - Touch Dissolves")
            self.assertEqual([round(t["secs"]) for t in alb["tracks"]], [7, 9, 12])
            self.assertEqual(len(alb["dropped_tracks"]), 3)
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_twin_keeps_the_copy_that_already_has_ids(self):
        """Real case (Touch Dissolves): only the newer twin carries the MusicBrainz ID."""
        from mutagen.flac import FLAC
        d = Path(tempfile.mkdtemp())
        try:
            old, new = d / "005.TSUtPC.flac", d / "05.TSUTPC.flac"
            self._mk(old, 6, title="To Stems Unclasped the Petals Cling", track=5)
            self._mk(new, 6, title="To Stems Unclasped The Petals Cling", track=5)
            f = FLAC(new)
            f["musicbrainz_albumid"] = "6b1efab6-84e5-4c4a-a472-1c44ba969d6b"
            f.save()
            info = {p: ma.read_file(p) for p in (old, new)}
            kept, gone = ma._drop_duplicate_tracks([old, new], info)
            self.assertEqual((kept, gone), ([new], ["005.TSUtPC.flac"]))
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_identical_flac_audio_is_one_track_even_with_different_titles(self):
        d = Path(tempfile.mkdtemp())
        try:
            self._mk(d / "01.flac", 6, title="First name", track=1)
            self._mk(d / "copy of 01.flac", 6, title="Renamed later", track=1)
            info = {p: ma.read_file(p) for p in sorted(d.iterdir())}
            self.assertTrue(all(i["audio_md5"] for i in info.values()))
            kept, gone = ma._drop_duplicate_tracks(sorted(d.iterdir()), info)
            self.assertEqual(len(kept), 1)
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_different_tracks_with_the_same_title_are_kept(self):
        d = Path(tempfile.mkdtemp())
        try:
            self._mk(d / "01.flac", 5, album="U", track=1, title="Untitled")
            self._mk(d / "02.flac", 9, album="U", track=2, title="Untitled")
            self.assertEqual(len(ma.read_album(d, "A", "2000 - U")["tracks"]), 2)
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_ocr_words_must_match_whole_words(self):
        a = album(artist="Ra Ono", title="Moonlight Sun")
        s = mc.score_candidate(a, cand(artist="Ra Ono", title="Moonlight Sun"), CFG,
                               ocr_text="SUNDAY IN HONOLULU")
        self.assertIn("OCR text found 0% of artist/title words", s["notes"])
        s2 = mc.score_candidate(a, cand(artist="Ra Ono", title="Moonlight Sun"), CFG,
                                ocr_text="RA ONO - MOONLIGHT SUN")
        self.assertIn("OCR text found 100% of artist/title words", s2["notes"])

    def test_mixed_formats_with_different_counts_are_kept(self):
        d = Path(tempfile.mkdtemp())
        try:
            self._mk(d / "01.flac", 3, album="M", track=1)
            self._mk(d / "02.flac", 4, album="M", track=2)
            self._mk(d / "03.mp3", 5, album="M", track=3)     # a genuinely mixed album
            self.assertEqual(len(ma.read_album(d, "A", "2000 - M")["tracks"]), 3)
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_artist_named_like_a_joining_word(self):
        self.assertEqual(mc.artist_sim("X", "X"), 1.0)
        self.assertLess(mc.artist_sim("X", "Bauhaus"), 0.5)
        self.assertGreater(mc.artist_sim("Max Corbacho & Bruno Sanfilippo",
                                         "Bruno Sanfilippo and Max Corbacho"), 0.95)

    def test_musicbrainz_video_disc_not_counted(self):
        rel = {"id": "r", "title": "T", "artist-credit": [{"name": "A"}],
               "media": [{"format": "CD", "tracks": [{"title": "a", "length": 60000}]},
                         {"format": "DVD-Video", "tracks": [{"title": "v", "length": 60000}]}]}
        c = ma.mb_candidate(rel)
        self.assertEqual((len(c["tracks"]), c["disc_count"]), (1, 1))

    def test_discogs_credit_matches_what_the_plugin_writes(self):
        rel = {"id": 7, "title": "Works", "year": 1999,
               "artists": [{"name": "Steve Reich (2)", "anv": "Reich", "join": "/"},
                           {"name": "John Adams", "anv": "Adams"}],
               "tracklist": [{"position": "1", "title": "a", "duration": "1:00"}]}
        c = ma.discogs_candidate(rel, self.dm)
        self.assertEqual(c["artist"], self.dm.format_artist_credit(rel["artists"]))
        self.assertEqual(c["artist_alt"], "Steve Reich & John Adams")
        # files tagged with the full names still match through the canonical form
        s = mc.score_candidate(album(artist="Steve Reich; John Adams", title="Works",
                                     tracks=[{"disc": 1, "num": 1, "title": "a", "secs": 60.0}]),
                               c, CFG)
        self.assertGreater(s["artist_sim"], 0.95)

    def test_disc_count_uses_plugin_rule_including_format_quantity(self):
        rel = {"id": 8, "title": "Two", "artists": [{"name": "X"}],
               "formats": [{"name": "CD", "qty": "2"}],
               "tracklist": [{"position": str(i), "title": "t", "duration": "1:00"} for i in range(1, 5)]}
        self.assertEqual(ma.discogs_candidate(rel, self.dm)["disc_count"], 2)

    def test_sub_track_positions_do_not_invent_discs(self):
        rel = {"id": 9, "title": "Suites", "artists": [{"name": "X"}],
               "tracklist": [
                   {"type_": "index", "position": "1", "title": "Suite 1", "sub_tracks": [
                       {"type_": "track", "position": "1.1", "title": "i", "duration": "1:00"},
                       {"type_": "track", "position": "1.2", "title": "ii", "duration": "1:00"}]},
                   {"type_": "index", "position": "2", "title": "Suite 2", "sub_tracks": [
                       {"type_": "track", "position": "2.1", "title": "i", "duration": "1:00"}]}]}
        c = ma.discogs_candidate(rel, self.dm)
        self.assertEqual(len(c["tracks"]), 3)
        self.assertEqual({t["disc"] for t in c["tracks"]}, {1})
        self.assertEqual(c["disc_count"], 1)

    def test_musicbrainz_canonical_names_are_kept(self):
        rel = {"id": "m1", "title": "Joint", "artist-credit": [
            {"name": "Corbacho", "joinphrase": " & ", "artist": {"name": "Max Corbacho"}},
            {"name": "Sanfilippo", "artist": {"name": "Bruno Sanfilippo"}}], "media": []}
        c = ma.mb_candidate(rel)
        self.assertEqual(c["artist"], "Corbacho & Sanfilippo")
        self.assertEqual(c["artist_alt"], "Max Corbacho; Bruno Sanfilippo")

    def test_discogs_index_tracklist_is_counted(self):
        rel = {"id": 1, "title": "Work", "artists": [{"name": "Someone"}], "year": 2013,
               "tracklist": [
                   {"type_": "heading", "title": "Part A", "position": ""},
                   {"type_": "index", "position": "1", "title": "Suite", "duration": "",
                    "sub_tracks": [{"type_": "track", "position": "1a", "title": "i", "duration": "1:00"},
                                   {"type_": "track", "position": "1b", "title": "ii", "duration": "2:00"}]},
                   {"type_": "track", "position": "2", "title": "Coda", "duration": "3:00"}]}
        c = ma.discogs_candidate(rel, self.dm)
        self.assertEqual([t["secs"] for t in c["tracks"]], [60, 120, 180])

    def test_upc_and_ean_forms_are_the_same_barcode(self):
        self.assertEqual(mc.barcode_key("0801837013429"), mc.barcode_key("801837013429"))
        c = cand(barcodes={"0801837013429"})
        s = mc.score_candidate(album(barcodes={"801837013429"}), c, CFG)
        self.assertEqual(s["code"], "barcode")
        self.assertIn("0801837013429", mc.barcode_variants("801837013429"))

    def test_barcode_check_digit(self):
        self.assertTrue(ma._valid_barcode("0801837013429"))
        self.assertFalse(ma._valid_barcode("0801837013420"))
        self.assertFalse(ma._valid_barcode("12345"))

    def test_barcode_reader_finds_codes_and_nothing_on_blank(self):
        try:
            import barcode
            from barcode.writer import ImageWriter
            from PIL import Image, ImageFilter
            from pyzbar.pyzbar import decode  # noqa: F401
        except Exception:
            self.skipTest("barcode/pyzbar/Pillow not available")
        import io
        buf = io.BytesIO()
        barcode.get("ean13", "080183701342", writer=ImageWriter()).write(buf, {"write_text": False})
        tile = Image.open(buf).convert("RGB").resize((320, 160))
        page = Image.new("RGB", (2400, 2400), (235, 228, 215))
        page.paste(tile, (1800, 2100))
        d = tempfile.mkdtemp()
        try:
            good, blank, blurred = (str(Path(d) / n) for n in ("good.png", "blank.png", "blur.png"))
            page.save(good)
            page.filter(ImageFilter.GaussianBlur(1.2)).save(blurred)
            Image.new("RGB", (2400, 2400), (235, 228, 215)).save(blank)
            self.assertEqual({mc.barcode_key(x) for x in ma.ocr_barcodes([good])}, {"801837013429"})
            self.assertEqual({mc.barcode_key(x) for x in ma.ocr_barcodes([blurred])}, {"801837013429"})
            self.assertEqual(ma.ocr_barcodes([blank], per_image_s=3, total_s=5), set())
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_reads_every_value_of_a_split_collaboration_tag(self):
        """The plugin writes one albumartist value per collaborator."""
        from mutagen.flac import FLAC
        d = tempfile.mkdtemp()
        try:
            p = Path(d) / "01.flac"
            subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "sine=duration=1",
                            "-ar", "8000", str(p), "-y"], check=True)
            f = FLAC(p)
            f["albumartist"] = ["Max Corbacho", "Bruno Sanfilippo"]
            f["album"] = "Joint"
            f.save()
            t = ma.read_file(p)
            self.assertEqual(t["artist"], "Max Corbacho; Bruno Sanfilippo")
            self.assertGreater(mc.artist_sim(t["artist"], "Bruno Sanfilippo & Max Corbacho"), 0.95)
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_musicbrainz_discogs_link_is_captured_like_the_plugin(self):
        rel = {"relations": [
            {"target-type": "url", "type": "discogs", "url": {"resource": "https://www.discogs.com/master/111"}},
            {"target-type": "url", "type": "discogs", "url": {"resource": "https://www.discogs.com/release/222-Some-Title"}}]}
        self.assertEqual(ma.mb_linked_discogs_id(rel), "222")
        self.assertEqual(ma.mb_linked_discogs_id({"relations": [
            {"target-type": "url", "url": {"resource": "https://www.discogs.com/master/111"}}]}), "")
        self.assertEqual(ma.mb_linked_discogs_id({}), "")
        self.assertEqual(ma.mb_linked_discogs_id(rel), self.dm.discogs_release_id_from_mb(rel))

    def test_catalogue_number_allows_some_length_disagreement(self):
        lens = [200, 305, 410, 150, 260, 340, 220, 190]
        a = album(catnos={"abc123"}, tracks=[{"disc": 1, "num": i, "title": "t", "secs": float(s)}
                                              for i, s in enumerate(lens)])
        wrong = [200, 305, 410, 150, 260 + 40, 340 + 55, 220 + 30, 190]       # 5 of 8 agree
        c = cand(catnos={"abc123"}, tracks=[{"disc": 1, "title": "t", "secs": float(s)} for s in wrong])
        self.assertEqual(mc.classify([mc.score_candidate(a, c, CFG)], CFG)["status"], "auto")
        # ...but the same length errors WITHOUT a matching code still need review
        a2 = album(tracks=a["tracks"])
        c2 = cand(tracks=c["tracks"])
        self.assertEqual(mc.classify([mc.score_candidate(a2, c2, CFG)], CFG)["status"], "review")
        # ...and a code cannot rescue lengths that mostly disagree
        bad = [lens[0]] + [s + 90 for s in lens[1:]]
        c3 = cand(catnos={"abc123"}, tracks=[{"disc": 1, "title": "t", "secs": float(s)} for s in bad])
        self.assertNotEqual(mc.classify([mc.score_candidate(a, c3, CFG)], CFG)["status"], "auto")

    def test_artist_credited_in_another_script_matches_by_sort_name(self):
        """Live case: MusicBrainz credits Kitaro as 喜多郎; its sort name is Latin."""
        rel = {"id": "k", "title": "Tenku", "date": "1986",
               "artist-credit": [{"name": "喜多郎", "artist": {"name": "喜多郎", "sort-name": "Kitaro"}}],
               "media": [{"format": "CD", "tracks": [{"title": "t", "length": 60000}]}]}
        c = ma.mb_candidate(rel)
        a = album(artist="Kitaro", title="Tenku", year=1986,
                  tracks=[{"disc": 1, "num": 1, "title": "t", "secs": 60.0}])
        self.assertGreater(mc.score_candidate(a, c, CFG)["artist_sim"], 0.95)
        # "Last, First" sort names still match (order is ignored)
        self.assertGreater(mc.artist_sim("Steve Roach", "Roach, Steve"), 0.95)

    def test_pressing_year_from_folder_name(self):
        cases = {"Tenku [20bit RM Domo 1997]": 1997, "X [2004 RM]": 2004, "X [2016 RM]": 2016,
                 "X [2002 remastered edition]": 2002, "X [2013 Esoteric remaster, ECLEC 2421]": 2013,
                 "X [2025 Remaster]": 2025,
                 "X [24bit 96khz]": None, "X [Live 1996]": None, "X [India Archive, 2003]": None,
                 "X [October 2 2011]": None, "X (Eilean 85)": None, "Plain Title": None}
        for text, want in cases.items():
            self.assertEqual(mc.pressing_year(text), want, text)

    def test_release_from_another_year_than_the_named_edition_is_not_auto(self):
        """Live case: 'Tenku [20bit RM Domo 1997]' auto-matched the 1986 release."""
        a = album(title="Tenku", year=1986, pressing_year=1997)
        orig = mc.score_candidate(a, cand(id="1986", title="Tenku", year=1986), CFG)
        r = mc.classify([orig], CFG)
        self.assertEqual(r["status"], "review")
        self.assertTrue(any("1997 edition" in w for w in r["why"]))
        # the 1997 remaster wins when it is among the candidates
        rm = mc.score_candidate(a, cand(id="1997", title="Tenku", year=1997), CFG)
        r2 = mc.classify([orig, rm], CFG)
        self.assertEqual(r2["best"]["cand"]["id"], "1997")

    def test_pressing_year_is_searched(self):
        seen = []

        class Spy:
            def get_json(self, url, host):
                seen.append(url)
                return {"releases": []} if host == "mb" else {"results": []}
        a = album(artist="Kitaro", title="Tenku", pressing_year=1997)
        ma.mb_search(Spy(), a, CFG)
        ma.discogs_search(Spy(), a, CFG, self.dm)
        joined = " ".join(seen)
        self.assertIn("date%3A1997", joined)
        self.assertIn("year=1997", joined)

    def test_catalogue_numbers_in_folder_names(self):
        """Real bracket texts from the ambient library."""
        cases = {
            "Mandala [20bit RM DOMO 71063-2]": ["DOMO 71063-2"],
            "X [Tatman Music TM 050426]": ["TM 050426"],
            "X [Warpcd92]": ["Warpcd92"],
            "X [BRC-67]": ["BRC-67"],
            "X [DiN34]": ["DiN34"],
            "X [IMED 0368]": ["IMED 0368"],
            "X [STCD 058]": ["STCD 058"],
            "X [MUSLIMLIM 017]": ["MUSLIMLIM 017"],
            "X [D1005 CD]": ["D1005"],
            "X [Sol 61 Cd]": ["Sol 61"],
            "X [2CD ADOR 2357]": ["ADOR 2357"],
            "X [TEMCD012]": ["TEMCD012"],
            "X [ns26]": ["ns26"],
            "X [ESS 011]": ["ESS 011"],
            "X [CBS, FM 39564, NL, Vinyl Rip 24bit 96khz]": ["FM 39564"],
            "X [2013 Esoteric remaster with bonus track, ECLEC 2423]": ["ECLEC 2423"],
            "Nattesne (Eilean 85)": ["Eilean 85"],
            "X (Hubro CD2544)": ["CD2544"],
            "X [TANTRA X25]": ["X25"],
            "X [Deutsche Grammophon 479 7050 Germany]": ["479 7050"],
            "X (Warp Records - WARP364, Web)": ["WARP364"],
            "X [Vol 1 & 2 _ 20bit RM DOMO 71050-2]": ["DOMO 71050-2"],
            "X [BSI 1999-3]": ["BSI 1999-3"],
            # not catalogue numbers
            "X [20bit RM Domo 1997]": [],
            "X [Mystery School Volume 5]": [],
            "X [Reinterpreted By 36]": [],
            "X [India Archive, 2003]": [],
            "X [October 2 2011]": [],
            "X [Studio Sessions, Vol. 5]": [],
            "X [Live 1996]": [],
            "X [Trans-Sonic Festival Philadelphia 05-31-1986]": [],
            "X [EDITION 2023]": [],
            "X [20th Anniversary Edition]": [],
            "X [24bit 96khz]": [], "X [2CD]": [], "X [6ch DTS Rip]": [], "X [2004 RM]": [],
            "X [7x140g Vinyl Rip 24bit 96khz]": [], "X [Boxset, 13CD, RM]": [],
            "X [Bojan Gorisek 2CD]": [], "X [SACD 2.0+5.1]": [], "X [No Vox]": [],
        }
        for text, want in cases.items():
            self.assertEqual(mc.catno_hints(text), want, text)
        # label-prefixed forms are kept for matching (HUBROCD2544 on Discogs)
        self.assertIn("Hubro CD2544", mc.catno_hints("X (Hubro CD2544)", variants=True))
        self.assertIn("TANTRA X25", mc.catno_hints("X [TANTRA X25]", variants=True))

    def test_folder_catalogue_number_never_counts_against_a_release(self):
        """'[Tatman Music TM 050426]' is on many different albums - not a real catalogue number."""
        tm = mc.norm_code("TM 050426")
        a = album(catnos={tm}, catnos_from_folder={tm})
        right = cand(catnos={mc.norm_code("SR-123")})
        s = mc.score_candidate(a, right, CFG)
        self.assertNotIn("catalogue number differs", " ".join(s["notes"]))
        self.assertEqual(mc.classify([s], CFG)["status"], "auto")
        # ...while a catalogue number from the TAGS still does
        a2 = album(catnos={tm})
        self.assertIn("catalogue number differs", " ".join(mc.score_candidate(a2, right, CFG)["notes"]))

    def test_folder_catalogue_number_picks_the_pressing(self):
        a = album(catnos={mc.norm_code("DOMO 71063-2")},
                  catnos_from_folder={mc.norm_code("DOMO 71063-2")})
        right = cand(id="domo", catnos={mc.norm_code("DOMO-71063-2")})
        other = cand(id="other")
        r = mc.classify([mc.score_candidate(a, other, CFG), mc.score_candidate(a, right, CFG)], CFG)
        self.assertEqual((r["status"], r["best"]["cand"]["id"]), ("auto", "domo"), r["why"])
        self.assertIn("catalogue number (folder name) agrees", r["best"]["notes"])
        self.assertTrue(any("pressing chosen by catalogue number" in n for n in r["best"]["notes"]))

    def test_folder_catalogue_number_is_searched(self):
        seen = []

        class Spy:
            def get_json(self, url, host):
                seen.append(url)
                return {"releases": []} if host == "mb" else {"results": []}
        a = album(artist="Kitaro", title="Mandala", catno_search=["DOMO 71063-2"])
        ma.mb_search(Spy(), a, CFG)
        ma.discogs_search(Spy(), a, CFG, self.dm)
        joined = " ".join(seen)
        self.assertIn("catno%3A%22DOMO%2071063-2%22", joined)        # MusicBrainz
        self.assertIn("catno=DOMO%2071063-2", joined)                # Discogs

    def test_read_album_collects_folder_catalogue_number(self):
        d = Path(tempfile.mkdtemp()) / "1994 - Mandala [20bit RM DOMO 71063-2]"
        try:
            self._mk(d / "01.flac", 5, album="Mandala", track=1)
            alb = ma.read_album(d, "Kitaro", d.name)
            self.assertEqual(alb["catno_search"], ["DOMO 71063-2"])
            self.assertIn(mc.norm_code("DOMO 71063-2"), alb["catnos"])
        finally:
            shutil.rmtree(d.parent, ignore_errors=True)

    def test_barcode_picks_the_pressing_when_only_one_shares_it(self):
        """Live case (Zakir Hussain - Making Music): tied at 100 with a pressing
        that lists no barcode; the barcode should decide."""
        a = album(barcodes={"042283154421"})
        with_code = cand(id="p-barcode", barcodes={"042283154421"})
        without = cand(id="p-none")
        r = mc.classify([mc.score_candidate(a, without, CFG), mc.score_candidate(a, with_code, CFG)], CFG)
        self.assertEqual((r["status"], r["best"]["cand"]["id"]), ("auto", "p-barcode"), r["why"])
        self.assertTrue(any("pressing chosen by barcode" in n for n in r["best"]["notes"]))

    def test_two_pressings_sharing_the_barcode_still_need_review(self):
        a = album(barcodes={"042283154421"})
        r = mc.classify([mc.score_candidate(a, cand(id="p1", barcodes={"042283154421"}), CFG),
                         mc.score_candidate(a, cand(id="p2", barcodes={"042283154421"}), CFG)], CFG)
        self.assertEqual(r["status"], "review")

    def test_partial_title_needs_nearly_all_lengths(self):
        """Live case (Blade Runner - Esper Definitive Collectors Edition vs Esper
        Edition): title only partly matches and 6 of 74 lengths disagree."""
        lens = [100.0 + 7 * i for i in range(74)]
        a = album(artist="Vangelis", title="Blade Runner - Esper Definitive Collectors Edition [4CD]",
                  tracks=[{"disc": 1, "num": i, "title": "t", "secs": s} for i, s in enumerate(lens)])
        off = [s + (60 if i % 12 == 0 else 0) for i, s in enumerate(lens)]   # 7 of 74 wrong
        partial = cand(artist="Vangelis", title="Blade Runner - Esper Edition -",
                       tracks=[{"disc": 1, "title": "t", "secs": s} for s in off])
        r = mc.classify([mc.score_candidate(a, partial, CFG)], CFG)
        self.assertEqual(r["status"], "review", r["why"])
        # same partial title but every length agrees: fine
        exact_lens = cand(artist="Vangelis", title="Blade Runner - Esper Edition -",
                          tracks=[{"disc": 1, "title": "t", "secs": s} for s in lens])
        self.assertEqual(mc.classify([mc.score_candidate(a, exact_lens, CFG)], CFG)["status"], "auto")
        # exact title with the same few wrong lengths stays auto (80% rule)
        a2 = dict(a, title="Blade Runner - Esper Edition")
        exact_title = cand(artist="Vangelis", title="Blade Runner - Esper Edition",
                           tracks=[{"disc": 1, "title": "t", "secs": s} for s in off])
        self.assertEqual(mc.classify([mc.score_candidate(a2, exact_title, CFG)], CFG)["status"], "auto")

    def test_musicbrainz_503_slows_down_instead_of_long_pauses(self):
        import io
        import urllib.error
        db = sqlite3.connect(":memory:")
        cfg = dict(CFG)
        http = ma.Http(db, cfg, "t")
        calls, sleeps = [], []

        class Resp(io.BytesIO):
            def __enter__(self): return self
            def __exit__(self, *a): return False

        def fake_urlopen(req, timeout=0):
            calls.append(1)
            if len(calls) == 1:
                raise urllib.error.HTTPError(req.full_url, 503, "busy", {}, None)
            return Resp(b'{"ok": true}')
        orig_open, orig_sleep = ma.urllib.request.urlopen, ma.time.sleep
        ma.urllib.request.urlopen, ma.time.sleep = fake_urlopen, sleeps.append
        try:
            self.assertEqual(http.get_json("https://musicbrainz.org/ws/2/x", "mb"), {"ok": True})
        finally:
            ma.urllib.request.urlopen, ma.time.sleep = orig_open, orig_sleep
        self.assertLessEqual(max(sleeps), 5)               # not a 15 s pause
        self.assertGreater(cfg["mb_delay"], CFG["mb_delay"])  # and slower from now on

    def test_credit_listing_composers_still_matches_performers(self):
        self.assertGreaterEqual(
            mc.artist_sim("Vanessa Wagner & Wilhem Latchoumia",
                          "Adams, Bernstein, Glass, Monk; Vanessa Wagner, Wilhem Latchoumia"), 0.9)
        self.assertLess(mc.artist_sim("Vanessa Wagner & Wilhem Latchoumia", "Kim Weston"), 0.5)

    def test_one_extra_file_with_matching_lengths_is_review_with_explanation(self):
        lens = [212, 187, 305, 244, 198, 351, 276, 233, 190, 402, 266, 215, 301, 178, 249, 330, 285]
        a = album(artist="Vanessa Wagner & Wilhem Latchoumia", title="This is America! [24bit 96khz]",
                  tracks=[{"disc": 1, "num": i + 1, "title": f"t{i}", "secs": float(s)}
                          for i, s in enumerate(lens + [97])])           # 18th file: bonus
        c = cand(artist="Adams, Bernstein, Glass, Monk; Vanessa Wagner, Wilhem Latchoumia",
                 title="This is America!", year=2021,
                 tracks=[{"disc": 1, "title": f"t{i}", "secs": float(s)} for i, s in enumerate(lens)])
        s = mc.score_candidate(a, c, CFG)
        self.assertTrue(s["aligned"])
        self.assertTrue(any("line up in order" in n and "1 extra track" in n for n in s["notes"]), s["notes"])
        r = mc.classify([s], CFG)
        self.assertEqual(r["status"], "review", r["why"])      # never auto: counts differ
        self.assertIn("track count differs", r["why"])

    def test_extra_file_with_unrelated_lengths_is_not_aligned(self):
        a = album(tracks=[{"disc": 1, "num": i, "title": "x", "secs": float(60 + 37 * i)} for i in range(4)])
        c = cand(tracks=[{"disc": 1, "title": "x", "secs": float(500 + 91 * i)} for i in range(3)])
        self.assertFalse(mc.score_candidate(a, c, CFG)["aligned"])

    def test_artist_parts_split_collaborations(self):
        self.assertEqual(mc.artist_parts("Vanessa Wagner & Wilhem Latchoumia")[0], "Vanessa Wagner")
        self.assertEqual(mc.artist_parts("Philip Glass; Steve Reich"), ["Philip Glass", "Steve Reich"])
        self.assertEqual(mc.artist_parts("Simon and Garfunkel")[0], "Simon")
        self.assertEqual(mc.artist_parts("Vangelis"), ["Vangelis"])

    def test_title_contained_in_longer_credit_title(self):
        self.assertGreaterEqual(
            mc.title_sim("Metamorphosis & Acoustic Counterpoint",
                         "Philip Glass Metamorphosis / Steve Reich Acoustic Counterpoint"), 0.9)
        self.assertLess(mc.title_sim("Dark Side", "Light Of The Moon"), 0.6)

    def test_other_artists_credited_but_content_matches_goes_to_review(self):
        a = album(artist="Philip Glass & Steve Reich", title="Metamorphosis & Acoustic Counterpoint")
        c = cand(artist="Neil Campbell & Carl Bowry", id="d1", source="discogs",
                 title="Philip Glass Metamorphosis / Steve Reich Acoustic Counterpoint")
        r = mc.classify([mc.score_candidate(a, c, CFG)], CFG)
        self.assertEqual(r["status"], "review", r["why"])
        self.assertTrue(any("artist credit differs" in w for w in r["why"]))

    def test_unrelated_release_stays_nomatch(self):
        c = cand(artist="Other Person", title="Completely Different", id="d2")
        r = mc.classify([mc.score_candidate(album(), c, CFG)], CFG)
        self.assertEqual(r["status"], "nomatch")

    def test_mb_search_uses_one_collaborator(self):
        seen = []

        class Spy:
            def get_json(self, url, host):
                seen.append(url)
                return {"releases": []}
        a = album(artist="Vanessa Wagner & Wilhem Latchoumia", title="This is America! [24bit 96khz]")
        ma.mb_search(Spy(), a, CFG)
        joined = " ".join(seen)
        self.assertIn("Vanessa%20Wagner", joined)
        self.assertNotIn("Wilhem", joined)
        self.assertNotIn("24bit", joined)


class Reevaluation(unittest.TestCase):
    """Results from an older version of the scripts must be decided again,
    including auto matches; OCR must not be redone when images are unchanged."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        rows = build_library(self.tmp / "lib")
        self.audit = self.tmp / "audit.csv"
        with open(self.audit, "w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
            w.writeheader()
            w.writerows(rows)
        self.saved = (ma.Http, ma.check_discogs_token, ma.SCRIPT_VERSION)
        ma.Http = lambda db, cfg, token: FakeHttp()
        ma.check_discogs_token = lambda token: "stub"
        import os
        os.environ["DISCOGS_TOKEN"] = "x"

    def tearDown(self):
        ma.Http, ma.check_discogs_token, ma.SCRIPT_VERSION = self.saved
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_main(self, *extra):
        import io
        import contextlib
        argv = sys.argv
        sys.argv = ["match_albums.py", str(self.audit), "--out-dir", str(self.tmp / "out"),
                    "--plugin-dir", PLUGIN_DIR, *extra]
        buf = io.StringIO()
        try:
            with contextlib.redirect_stdout(buf):
                ma.main()
        finally:
            sys.argv = argv
        return buf.getvalue()

    def test_new_version_redecides_everything_including_auto(self):
        ma.SCRIPT_VERSION = "v-old"
        first = self.run_main()
        self.assertIn("6 to go", first)
        again = self.run_main()
        self.assertIn("0 to go", again)                       # same version: nothing redone
        ma.SCRIPT_VERSION = "v-new"
        newer = self.run_main()
        self.assertIn("Re-evaluating 6 albums", newer)        # autos included
        self.assertIn("6 to go", newer)

    def csv_rows(self):
        total = 0
        for s in ("auto", "review", "nomatch"):
            with open(self.tmp / "out" / f"matches_{s}.csv", encoding="utf-8") as fh:
                total += len(list(csv.DictReader(fh)))
        return total

    def test_interrupted_reevaluation_keeps_every_result_in_the_csvs(self):
        ma.SCRIPT_VERSION = "v-old"
        self.run_main()
        self.assertEqual(self.csv_rows(), 6)
        ma.SCRIPT_VERSION = "v-new"
        real = ma.process
        calls = []

        def stop_after_one(*a, **k):
            if calls:
                raise KeyboardInterrupt          # what the runner's SIGTERM becomes
            calls.append(1)
            return real(*a, **k)
        ma.process = stop_after_one
        try:
            out = self.run_main()
        finally:
            ma.process = real
        self.assertIn("Interrupted", out)
        self.assertEqual(self.csv_rows(), 6)    # nothing vanished while half-done

    def test_failures_are_reevaluated_first(self):
        ma.SCRIPT_VERSION = "v-old"
        self.run_main()
        ma.SCRIPT_VERSION = "v-new"
        out = self.run_main()
        lines = [l for l in out.splitlines() if l.startswith("[")]
        self.assertIn("Unknown Thing", lines[0])   # the nomatch album goes first

    def test_retry_review_does_not_delete_results(self):
        ma.SCRIPT_VERSION = "v-old"
        self.run_main()
        real = ma.process
        ma.process = lambda *a, **k: (_ for _ in ()).throw(KeyboardInterrupt)
        try:
            out = self.run_main("--retry-review")
        finally:
            ma.process = real
        self.assertIn("Re-evaluating 3 albums (3 review)", out)
        self.assertEqual(self.csv_rows(), 6)

    def test_keep_old_results_flag(self):
        ma.SCRIPT_VERSION = "v-old"
        self.run_main()
        ma.SCRIPT_VERSION = "v-new"
        self.assertIn("0 to go", self.run_main("--keep-old-results"))

    def test_second_instance_is_refused(self):
        import fcntl
        out = self.tmp / "out"
        out.mkdir(parents=True, exist_ok=True)
        holder = open(out / "match.lock", "w")
        fcntl.flock(holder, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            with self.assertRaises(SystemExit) as cm:
                self.run_main()
            self.assertIn("already working", str(cm.exception))
        finally:
            holder.close()

    def test_ocr_result_is_cached(self):
        db = sqlite3.connect(":memory:")
        calls = []
        compute = lambda: calls.append(1) or ["0801837013429"]  # noqa: E731
        self.assertEqual(ma._cached(db, "barcodes", "k1", compute), ["0801837013429"])
        self.assertEqual(ma._cached(db, "barcodes", "k1", compute), ["0801837013429"])
        self.assertEqual(len(calls), 1)


class DiscogsDown(FakeHttp):
    """Discogs raises `exc`; MusicBrainz works normally."""
    def __init__(self, exc):
        super().__init__()
        self.exc = exc

    def get_json(self, url, host):
        if host == "discogs":
            raise self.exc
        return super().get_json(url, host)


class FailureHandling(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        cls.rows = {r["AlbumFolder"]: r for r in build_library(Path(cls.tmp) / "lib")}
        cls.dm = ma.load_plugin(PLUGIN_DIR)
        cls.args = types.SimpleNamespace(ocr=False)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def run_row(self, folder, exc):
        return ma.process(self.rows[folder], DiscogsDown(exc), CFG, self.dm, self.args)

    def test_bad_token_stops_the_run(self):
        with self.assertRaises(ma.AuthError):
            self.run_row("2012 - Slow Drift", ma.AuthError("401"))

    def test_mb_auto_unaffected_by_discogs_outage(self):
        o = self.run_row("2010 - First Light", ma.AuthError("401"))   # never reaches Discogs
        self.assertEqual(o["status"], "auto")

    def test_transient_discogs_failure_keeps_musicbrainz_review(self):
        o = self.run_row("2005 - Twin Pressing", RuntimeError("timeout"))
        self.assertEqual(o["status"], "review")
        self.assertTrue(any("Discogs lookup failed" in w for w in o["why"]))

    def test_nothing_from_mb_plus_discogs_failure_is_error_not_nomatch(self):
        o = self.run_row("2012 - Slow Drift", RuntimeError("timeout"))
        self.assertEqual(o["status"], "error")


if __name__ == "__main__":
    unittest.main(argv=[sys.argv[0]], verbosity=2)
