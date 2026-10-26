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
import re
import unittest
from pathlib import Path

# TagMender settings and state of the tests never touch the real ones
import os as _os, tempfile as _tempfile  # noqa: E402
_os.environ["XDG_CONFIG_HOME"] = _tempfile.mkdtemp(prefix="tm-config-")
_os.environ["XDG_STATE_HOME"] = _tempfile.mkdtemp(prefix="tm-state-")
HERE = Path(__file__).resolve().parent.parent   # the scripts sit above tests/
sys.path.insert(0, str(HERE))
import match_core as mc  # noqa: E402
import match_albums as ma  # noqa: E402

PLUGIN_DIR = sys.argv[1] if len(sys.argv) > 1 else "~/Documents/Github/picard-discogs-enrichment"
CFG = dict(mc.DEFAULT_CFG, preferred_countries=[])   # tie rules tested without the country preference
CFG_PREF = dict(mc.DEFAULT_CFG)


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

    def test_combine_puts_discogs_first(self):
        """Discogs is primary (it lists more pressings); the MusicBrainz release of the
        same pressing comes along so Picard can still tag from it."""
        good = mc.classify([mc.score_candidate(album(), cand(catnos={"abc1"}), CFG)], CFG)
        good["scored"] = [good["best"]]
        dgood = mc.classify([mc.score_candidate(album(), cand(source="discogs", id="d", catnos={"abc1"}), CFG)], CFG)
        weak = mc.classify([mc.score_candidate(album(), cand(artist="Nope", title="Nope"), CFG)], CFG)
        both = mc.combine(good, dgood)
        self.assertEqual((both["best"]["cand"]["source"], both["mb_twin"]), ("discogs", "x1"))
        self.assertEqual(mc.combine(weak, dgood)["best"]["cand"]["source"], "discogs")
        other = mc.classify([mc.score_candidate(album(), cand(source="discogs", id="d", catnos={"zzz9"},
                                                              country="RU"), CFG)], CFG)
        self.assertNotIn("mb_twin", mc.combine(good, other))            # another pressing: no twin
        unsure = dict(dgood, status="review")
        self.assertEqual(mc.combine(good, unsure)["best"]["cand"]["source"], "mb")

    def test_discogs_without_evidence_does_not_override_musicbrainz(self):
        """Candlemass (2005): Discogs's only confident pick is Nuclear Blast, Germany;
        MusicBrainz is sure of Metal Mind, Poland. Nothing about the copy tells them
        apart, so the country order decides; with evidence, the evidence does."""
        mbr = mc.classify([mc.score_candidate(album(), cand(id="pl", country="PL", label="Metal Mind"), CFG)], CFG)
        mbr["scored"] = [mbr["best"]]
        dgr = mc.classify([mc.score_candidate(album(), cand(id="9", source="discogs", country="Germany",
                                                            label="Nuclear Blast"), CFG)], CFG)
        self.assertEqual(mc.combine(mbr, dgr, ["PL", "DE"])["best"]["cand"]["id"], "pl")
        self.assertEqual(mc.combine(mbr, dgr, ["DE", "PL"])["best"]["cand"]["id"], "9")
        self.assertEqual(mc.combine(mbr, dgr)["best"]["cand"]["id"], "9")              # no order: Discogs first
        a = album(folder="2005 - First Light [Nuclear Blast, Germany]")
        dge = mc.classify([mc.score_candidate(a, cand(id="9", source="discogs", country="Germany",
                                                      label="Nuclear Blast"), CFG)], CFG)
        self.assertEqual(mc.combine(mbr, dge, ["PL", "DE"])["best"]["cand"]["id"], "9")  # evidence wins

    def test_same_label_country_year_and_tracks_is_one_release(self):
        mbc = cand(id="m", label="Death Cult Switzerland", country="CH", catnos=set())
        dgc = cand(id="d", source="discogs", label="Death Cult Switzerland", country="Switzerland",
                   catnos={"dcs3"})
        self.assertTrue(mc.same_pressing(mbc, dgc))
        self.assertFalse(mc.same_pressing(mbc, dict(dgc, country="Germany")))
        self.assertFalse(mc.same_pressing(dict(mbc, catnos={"dcs9"}), dgc))      # both numbered: they must agree

    def test_evidence_both_picks_share_decides_nothing(self):
        """Death - Leprosy [2014 RM 3CD]: 'a CD (folder name)' is true of the US and the
        Japanese Relapse CD alike, so it does not let Discogs's Japanese pick win."""
        a = album(folder="1988 - First Light [2014 RM 3CD]", media_hint="cd")
        mbr = mc.classify([mc.score_candidate(a, cand(id="us", country="US", label="Relapse", formats={"cd"}),
                                              CFG)], CFG)
        mbr["scored"] = [mbr["best"]]
        dgr = mc.classify([mc.score_candidate(a, cand(id="7", source="discogs", country="Japan", label="Relapse",
                                                      formats={"cd"}), CFG)], CFG)
        self.assertEqual(mc.combine(mbr, dgr, ["CA", "US"])["best"]["cand"]["id"], "us")

    def test_another_version_of_the_album_is_not_auto(self):
        """Cynic - Traced In Air (2008): 'Traced in Air Remixed' (2019) fits every length
        but is another recording; only a folder saying Remixed takes it."""
        c = cand(title="First Light Remixed")
        r = mc.classify([mc.score_candidate(album(folder="2008 - First Light"), c, CFG)], CFG)
        self.assertNotEqual(r["status"], "auto")
        self.assertTrue(any("Remixed version" in w or "remixed version" in w for w in r["why"]), r["why"])
        r = mc.classify([mc.score_candidate(album(folder="2019 - First Light [Remixed 24bit]"), c, CFG)], CFG)
        self.assertEqual(r["status"], "auto", r["why"])
        r = mc.classify([mc.score_candidate(album(title="Alive", folder="2001 - Alive"), cand(title="Alive"), CFG)],
                        CFG)
        self.assertEqual(r["status"], "auto")

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


class OcrCodes(unittest.TestCase):
    TEXT = ("SOVIET FRANCE  Norsch\nCat. No. MASO CD 90041   LC 01234   Made in Italy\n"
            "Recorded 1990. Tel 0191 2611 234  BWV 1001  Track 12  Disc 2\n"
            "8 012957 004125\n 1 234567 890123")

    def test_catalogue_numbers_and_barcodes_are_read_from_scan_text(self):
        codes = mc.codes_from_ocr_text(self.TEXT)
        self.assertIn("MASO CD 90041", codes["catnos"])
        for bad in ("LC 01234", "BWV 1001", "Track 12", "Disc 2"):
            self.assertNotIn(bad, codes["catnos"])
        self.assertEqual(codes["barcodes"], ["8012957004125"])        # check digit valid; the other is not
        self.assertEqual(mc.codes_from_ocr_text("CHARRMCD03 and BRC-657")["catnos"], ["CHARRMCD03", "BRC-657"])
        self.assertEqual(mc.codes_from_ocr_text(""), {"barcodes": [], "catnos": []})
        # real catalogue numbers from your scans are kept, real noise from them is not
        kept = mc.codes_from_ocr_text("ECM 1349  WARPCD211  SICCD 087  EFA 08872-2  HMU 907608  ALKEOS 201340", limit=10)["catnos"]
        self.assertEqual(kept, ["ECM 1349", "WARPCD211", "SICCD 087", "EFA 08872-2", "HMU 907608", "ALKEOS 201340"])
        noise = "Santa Monica CA 90064  NY 11576  POSTFACH 600331  COPY 11  RETURNING 43  GALERIE 213"
        self.assertEqual(mc.codes_from_ocr_text(noise)["catnos"], [])

    def test_a_catalogue_number_on_a_scan_finds_the_release(self):
        # The title search finds nothing; only the catalogue number printed on the scan does.
        class CatnoOnlyHttp(FakeHttp):
            def __init__(self):
                super().__init__()
                rel = mb_release("mb-scan", "Scan Artist", "Scan Album", 2001, [10000, 20000, 30000])
                rel["label-info"] = [{"catalog-number": "ZZQ-4471", "label": {"name": "L"}}]
                self.mb, self.dg, self.dg_search = {"mb-scan": rel}, {}, {}
                self.rel = rel

            def get_json(self, url, host):
                import urllib.parse as up
                if host == "mb" and "/ws/2/release/?" in url:
                    q = up.unquote(url.split("query=")[1].split("&")[0])
                    return {"releases": [mb_hit(self.rel)] if 'catno:"ZZQ-4471"' in q else []}
                if host == "discogs" and "database/search" in url:
                    return {"results": []}
                return super().get_json(url, host)

        tmp = Path(tempfile.mkdtemp())
        saved = (ma.list_images, ma.ocr_barcodes, ma.ocr_text)
        try:
            d = tmp / "Scan Artist" / "2001 - Scan Album Untitled"
            d.mkdir(parents=True)
            for i in range(3):
                subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i",
                                f"sine=frequency={300 + 50 * i}:duration={10 * (i + 1)}", "-ar", "8000", "-ac", "1",
                                "-metadata", f"title=T{i}", "-metadata", f"track={i + 1}",
                                str(d / f"{i + 1:02d}.flac"), "-y"], check=True)
            (d / "back.jpg").write_bytes(b"x")
            ma.list_images = lambda base, n: ([d / "back.jpg"], "k1")
            ma.ocr_barcodes = lambda imgs: set()
            ma.ocr_text = lambda imgs: "Scan Artist  Cat. No. ZZQ-4471  Made in EU"
            row = {"AlbumArtistFolder": "Scan Artist", "AlbumFolder": d.name, "FullPath": str(d),
                   "Status": "untagged", "OcrCandidateImages": ""}
            o = ma.process(row, CatnoOnlyHttp(), CFG, ma.load_plugin(PLUGIN_DIR), types.SimpleNamespace(ocr=True, max_images=15))
            self.assertEqual(((o.get("best") or {}).get("id"), o["status"]), ("mb-scan", "auto"), o.get("why"))
            self.assertIn("ZZQ-4471", o.get("codes_from_ocr", ""))
        finally:
            ma.list_images, ma.ocr_barcodes, ma.ocr_text = saved
            shutil.rmtree(tmp, ignore_errors=True)


class PressingFromScans(unittest.TestCase):
    """Pressings that score the same are told apart by what the scans show."""
    BACK = ("FIRST LIGHT  1. T0  2. T1  3. T2  (P) 2010 Nuclear Blast GmbH  "
            "Made in Germany  NB 1234-2")

    def classify(self, text, *cands):
        return mc.classify([mc.score_candidate(album(), c, CFG, ocr_text=text) for c in cands], CFG)

    def test_country_preference_never_skips_the_scans(self):
        """Once Was Not: a US and a German CD tie; the spine's FO532CD finds the FONO pressing.
        The country preference must not settle the tie before the scans are read."""
        def rel(rid, country, catno):
            r = mb_release(rid, "Scan Artist", "Scan Album", 2005, [10000, 20000, 30000], country=country)
            r["label-info"] = [{"catalog-number": catno, "label": {"name": "L"}}]
            return r
        us, de, ru = rel("us", "US", "CMR 8242-2"), rel("de", "DE", "CM 77542-2"), rel("ru", "RU", "FO532CD")

        class H(FakeHttp):
            def __init__(self):
                super().__init__()
                self.mb, self.dg, self.dg_search = {"us": us, "de": de, "ru": ru}, {}, {}

            def get_json(self, url, host):
                import urllib.parse as up
                if host == "mb" and "/ws/2/release/?" in url:
                    q = up.unquote(url.split("query=")[1].split("&")[0])
                    if "catno" in q:
                        return {"releases": [mb_hit(ru)] if "FO532CD" in q else []}
                    return {"releases": [mb_hit(us), mb_hit(de)]}
                if host == "discogs" and "database/search" in url:
                    return {"results": []}
                return super().get_json(url, host)

        tmp = Path(tempfile.mkdtemp())
        saved = (ma.list_images, ma.ocr_barcodes, ma.ocr_text, ma.ocr_spines)
        try:
            d = tmp / "Scan Artist" / "2005 - Scan Album"
            d.mkdir(parents=True)
            for i in range(3):
                subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i",
                                f"sine=frequency={300 + 50 * i}:duration={10 * (i + 1)}", "-ar", "8000", "-ac", "1",
                                "-metadata", f"title=T{i}", "-metadata", f"track={i + 1}",
                                str(d / f"{i + 1:02d}.flac"), "-y"], check=True)
            (d / "10.jpg").write_bytes(b"x")
            ma.list_images = lambda base, n: ([d / "10.jpg"], "k-pref")
            ma.ocr_barcodes = lambda imgs: set()
            ma.ocr_text = lambda imgs: "Scan Artist  Scan Album"
            ma.ocr_spines = lambda imgs: "SCAN ARTIST  FO532CD"
            row = {"AlbumArtistFolder": "Scan Artist", "AlbumFolder": d.name, "FullPath": str(d),
                   "Status": "untagged", "OcrCandidateImages": ""}
            o = ma.process(row, H(), CFG_PREF, ma.load_plugin(PLUGIN_DIR),
                           types.SimpleNamespace(ocr=True, max_images=15))
            self.assertEqual(((o.get("best") or {}).get("id"), o["status"]), ("ru", "auto"), o.get("why"))
        finally:
            ma.list_images, ma.ocr_barcodes, ma.ocr_text, ma.ocr_spines = saved
            shutil.rmtree(tmp, ignore_errors=True)

    def test_excluded_album_makes_no_lookups_and_is_labelled(self):
        """A '[No Vox]' album: Ignored by the rule before any network request."""
        class NoNetwork(FakeHttp):
            def get_json(self, url, host):
                raise AssertionError(f"looked up {url}")
        tmp = Path(tempfile.mkdtemp())
        try:
            d = tmp / "BTBAM" / "2007 - Colors [No Vox]"
            d.mkdir(parents=True)
            subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "sine=duration=2",
                            "-metadata", "album=Colors [No Vox]", str(d / "01.flac"), "-y"], check=True)
            row = {"AlbumArtistFolder": "BTBAM", "AlbumFolder": d.name, "FullPath": str(d), "Status": "untagged"}
            o = ma.process(row, NoNetwork(), CFG, None, types.SimpleNamespace(ocr=True, max_images=15))
            self.assertEqual(o["status"], "ignored")
            self.assertIn("Ignored by rule", o["why"][0])
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_rule_matches_the_identified_release(self):
        """Untagged files; MusicBrainz identifies them as 'Coma Ecliptic [No Vox]': Ignored before tagging."""
        rel = mb_release("nv", "Band", "Coma Ecliptic [No Vox]", 2015, [10000, 20000])

        class H(FakeHttp):
            def __init__(self):
                super().__init__()
                self.mb, self.dg, self.dg_search = {"nv": rel}, {}, {}

            def get_json(self, url, host):
                if host == "mb" and "/ws/2/release/?" in url:
                    return {"releases": [mb_hit(rel)]}
                if host == "discogs":
                    return {"results": []}
                return super().get_json(url, host)
        tmp = Path(tempfile.mkdtemp())
        try:
            d = tmp / "Band" / "2015 - Coma Ecliptic"
            d.mkdir(parents=True)
            for i in range(2):
                subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i",
                                f"sine=frequency={300 + 50 * i}:duration={10 * (i + 1)}", "-ar", "8000",
                                "-metadata", f"title=T{i}", "-metadata", f"track={i + 1}",
                                str(d / f"{i + 1:02d}.flac"), "-y"], check=True)
            row = {"AlbumArtistFolder": "Band", "AlbumFolder": d.name, "FullPath": str(d), "Status": "untagged"}
            o = ma.process(row, H(), CFG, ma.load_plugin(PLUGIN_DIR), types.SimpleNamespace(ocr=False, max_images=15))
            self.assertEqual(o["status"], "ignored", o.get("why"))
            self.assertIn("identified release", o["why"][0])
            o = ma.process(row, H(), CFG, ma.load_plugin(PLUGIN_DIR),
                           types.SimpleNamespace(ocr=False, max_images=15, process_anyway=True))
            self.assertEqual(o["status"], "auto", o.get("why"))          # "process anyway"
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_label_and_country_on_the_back_pick_the_pressing(self):
        r = self.classify(self.BACK,
                          cand(id="us", country="US", label="Century Media"),
                          cand(id="de", country="DE", label="Nuclear Blast"))
        self.assertEqual(r["status"], "auto", r["why"])
        self.assertEqual(r["best"]["cand"]["id"], "de")
        note = " ".join(r["best"]["notes"])
        self.assertIn("pressing chosen by", note)
        self.assertIn("Nuclear Blast", note)
        self.assertIn("made in Germany", note)

    def test_catalogue_number_printed_on_the_back_picks_the_pressing(self):
        r = self.classify(self.BACK, cand(id="a", country="DE", catnos={mc.norm_code("NB 9999-2")}),
                          cand(id="b", country="DE", catnos={mc.norm_code("NB 1234-2")}))
        self.assertEqual((r["status"], r["best"]["cand"]["id"]), ("auto", "b"), r["why"])

    def test_clues_shared_by_every_pressing_decide_nothing(self):
        r = self.classify(self.BACK, cand(id="cd", country="DE", label="Nuclear Blast"),
                          cand(id="lp", country="DE", label="Nuclear Blast"))
        self.assertEqual(r["status"], "review")
        self.assertTrue(any("runner-up" in w for w in r["why"]))

    def test_front_cover_only_decides_nothing(self):
        r = self.classify("FIRST LIGHT  ARTIST ALPHA", cand(id="us", country="US", label="Century Media"),
                          cand(id="de", country="DE", label="Nuclear Blast"))
        self.assertEqual(r["status"], "review")

    def test_two_pressings_each_with_a_clue_stay_in_review(self):
        r = self.classify("Century Media  Made in Germany", cand(id="us", country="US", label="Century Media"),
                          cand(id="de", country="DE", label="Nuclear Blast"))
        self.assertEqual(r["status"], "review")

    def test_a_pressing_the_scans_contradict_is_never_picked(self):
        r = self.classify("Nuclear Blast  Printed in Japan", cand(id="de", country="DE", label="Nuclear Blast"),
                          cand(id="jp", country="JP", label="Avalon"))
        self.assertEqual(r["status"], "review")

    def test_countries_and_labels_are_read_carefully(self):
        self.assertEqual(mc.countries_on_scans("Printed in Germany. Made in the EU"), {"DE", "XE"})
        self.assertEqual(mc.countries_on_scans("MADE IN U.S.A."), {"US"})
        self.assertEqual(mc.countries_on_scans("handmade in heaven"), set())
        self.assertEqual(mc.country_code("Germany"), "DE")
        self.assertEqual(mc.country_code("DE"), "DE")
        agree, _ = mc.pressing_clues("all music by the band, recorded at studio", cand(label="Music Records"))
        self.assertEqual(agree, {})                         # filler words are not a label


class ScanReading(unittest.TestCase):
    """The scans as TagMender reads them (needs tesseract; numpy for the barcode patch)."""

    def setUp(self):
        if not shutil.which("tesseract"):
            self.skipTest("tesseract not installed")
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def font(self, size):
        from PIL import ImageFont
        for f in ("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
                  "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"):
            if Path(f).exists():
                return ImageFont.truetype(f, size)
        try:
            return ImageFont.load_default(size=size)
        except TypeError:
            self.skipTest("no scalable font")

    def test_the_spine_of_an_open_digipak_spread_is_read(self):
        """Tome II's digipak was scanned open: the spine with HHR 2018-23 runs down the middle."""
        from PIL import Image, ImageDraw
        spine = Image.new("L", (1400, 120), 255)
        ImageDraw.Draw(spine).text((20, 20), "CRYPTOPSY HHR 2018-23", font=self.font(70), fill=0)
        spread = Image.new("L", (3200, 1400), 200)
        spread.paste(spine.rotate(90, expand=True), (1540, 0))
        spread.save(self.tmp / "spread.png")
        text = ma.ocr_spines([self.tmp / "spread.png"])
        self.assertIn("HHR 2018-23", mc.codes_from_ocr_text(text)["catnos"])

    def test_barcode_digits_are_read_from_the_barcode_patch(self):
        try:
            import numpy  # noqa: F401
        except ImportError:
            self.skipTest("numpy not installed")
        from PIL import Image, ImageDraw
        im = Image.new("L", (4000, 2400), 230)
        d = ImageDraw.Draw(im)
        x = 3000
        for i, wbar in enumerate([4, 2, 6, 2, 4, 8, 2, 4, 2, 6, 4, 2, 8, 2, 4, 6, 2, 4] * 3):
            if i % 2 == 0:
                d.rectangle((x, 100, x + wbar, 260), fill=0)
            x += wbar + 3
        d.text((3000, 270), "8 715392 182327", font=self.font(34), fill=0)
        im.save(self.tmp / "back.png")
        self.assertTrue(ma.barcode_regions(Image.open(self.tmp / "back.png")))
        text = ma.ocr_barcode_digits([self.tmp / "back.png"])
        self.assertEqual(mc.codes_from_ocr_text(text)["barcodes"], ["8715392182327"])

    def test_the_matrix_code_round_a_disc_hub_is_read(self):
        """Exhorder's Dissonance CD: "QDISS0201CDD-1 15 A00" runs round the hub."""
        try:
            import numpy  # noqa: F401
        except ImportError:
            self.skipTest("numpy not installed")
        import numpy as np
        from PIL import Image, ImageDraw
        # the code set on a flat band, then bent round the hub (outer edge = top of the text)
        r0, r1 = 900 * 0.30, 900 * 0.38
        band = Image.new("L", (int(2 * np.pi * r1), int(r1 - r0)), 200)
        ImageDraw.Draw(band).text((300, 14), "QDISS0201CDD-1 15 A00", font=self.font(40), fill=0)
        b = np.asarray(band)
        yy, xx = np.mgrid[0:2000, 0:2000]
        rad = np.hypot(xx - 1000, yy - 1000)
        ang = np.mod(np.arctan2(yy - 1000, xx - 1000), 2 * np.pi)
        disc = np.where(rad <= 900, 200, 255).astype(np.uint8)
        ring = (rad >= r0) & (rad < r1)
        col = (ang[ring] / (2 * np.pi) * b.shape[1]).astype(int) % b.shape[1]
        row = np.clip(((r1 - rad[ring]) / (r1 - r0) * b.shape[0]).astype(int), 0, b.shape[0] - 1)
        disc[ring] = b[row, col]
        disc[rad < 160] = 255                                   # the hole
        im = Image.fromarray(disc)
        im.save(self.tmp / "matrix.png")
        text = ma.ocr_disc_rings([self.tmp / "matrix.png"])
        self.assertIn("QDISS0201CDD", mc.codes_from_ocr_text(text)["catnos"], text)

    def test_a_barcode_on_a_late_scan_is_found_within_the_time_budget(self):
        """Enslaved - Axioma Ethica Odini: nine huge scans, the barcode on the 8th; the
        whole-page passes spent the budget first. Bar patches are now decoded first."""
        try:
            import numpy  # noqa: F401
            import pyzbar  # noqa: F401
        except ImportError:
            self.skipTest("numpy or pyzbar not installed")
        from PIL import Image, ImageDraw
        L = ["0001101", "0011001", "0010011", "0111101", "0100011", "0110001", "0101111", "0111011",
             "0110111", "0001011"]
        G = ["0100111", "0110011", "0011011", "0100001", "0011101", "0111001", "0000101", "0010001",
             "0001001", "0010111"]
        R = ["1110010", "1100110", "1101100", "1000010", "1011100", "1001110", "1010000", "1000100",
             "1001000", "1110100"]
        PARITY = ["LLLLLL", "LLGLGG", "LLGGLG", "LLGGGL", "LGLLGG", "LGGLLG", "LGGGLL", "LGLGLG", "LGLGGL",
                  "LGGLGL"]
        code = "7090014385050"
        d = [int(c) for c in code]
        bits = "101" + "".join((L if p == "L" else G)[x] for p, x in zip(PARITY[d[0]], d[1:7])) + "01010" + \
            "".join(R[x] for x in d[7:]) + "101"
        paths = []
        for n in range(4):
            im = Image.new("L", (4000, 3000), 235)
            ImageDraw.Draw(im).text((200, 200), f"page {n}", fill=0)
            if n == 3:
                x = 3000
                for b in bits:
                    if b == "1":
                        ImageDraw.Draw(im).rectangle((x, 2500, x + 3, 2700), fill=0)
                    x += 4
            p = self.tmp / f"img10{n}.png"
            im.save(p)
            paths.append(p)
        self.assertEqual(ma.ocr_barcodes(paths, per_image_s=0.01, total_s=0.01), {code})

    def test_a_completed_barcode_does_not_stand_for_the_copy(self):
        """A misread completed by the check digit always passes the check."""
        text = mc.COMPLETED + "9000044388080\n5 013929 020160\n" + mc.COMPLETED + "5013929020160"
        self.assertEqual(mc.barcodes_read_whole(text, ["9000044388080", "5013929020160"]), ["5013929020160"])

    def test_the_barcodes_first_digit_is_completed_by_the_check_digit(self):
        """Exhorder: the band under the bars read "BS 013929)020160)"; the 5 stands apart."""
        self.assertEqual(mc.complete_ean("013929020160"), "5013929020160")
        self.assertEqual(mc.complete_ean("01392902016"), "")


class ZeroReadAsO(unittest.TestCase):
    """Dissonance's QDISS0201CDD, printed in a font whose 0 OCR reads as O."""

    def test_the_catalogue_number_is_searched_with_a_zero(self):
        codes = mc.codes_from_ocr_text("ROAD, LONDON W4 5PY. UK. MADE IN EU / MCPS. QDISSO201CDD")
        self.assertIn("QDISS0201CDD", codes["catnos"])
        self.assertNotIn("QDISSO201CDD", codes["catnos"])

    def test_the_pressing_carrying_it_agrees(self):
        agree, _ = mc.pressing_clues("spine: QDISSO201CDD", cand(catnos={mc.norm_code("QDISS0201CDD")}))
        self.assertIn("catalogue number", agree)
        agree, _ = mc.pressing_clues("spine: QDISSO202CDD", cand(catnos={mc.norm_code("QDISS0201CDD")}))
        self.assertNotIn("catalogue number", agree)


class BothDatabases(unittest.TestCase):
    """Kreator - Endorama (2003 Art Music, AMG 099, Russia): the Russian CD has 15 tracks
    (2 extra), so a track-count filter skipped it; and Discogs was not asked because
    MusicBrainz was sure of the Japanese CD."""

    def test_the_named_pressing_is_fetched_whatever_its_track_count(self):
        def rel(rid, country, catno, n):
            return {"id": rid, "title": "Endorama", "country": country, "track-count": n,
                    "label-info": [{"catalog-number": catno, "label": {"name": "L"}}],
                    "media": [{"format": "CD", "track-count": n}],
                    "release-group": {"id": "rg"}}
        hits = [rel("jp", "JP", "VICP-60704", 3), rel("de", "DE", "X", 3), rel("us", "US", "Y", 3),
                rel("de2", "DE", "Z", 3), rel("xw", "XW", "W", 3), rel("ru", "RU", "AMG 099", 5)]
        fetched = []

        class H:
            def get_json(self, url, host):
                if "/release/?query=" in url:
                    return {"releases": hits}
                if "release-group=" in url:
                    return {"releases": []}
                rid = url.split("/release/")[1].split("?")[0]
                fetched.append(rid)
                r = next(h for h in hits if h["id"] == rid)
                return dict(r, media=[{"format": "CD", "tracks": [{"title": f"T{i}", "length": 200000}
                                                                  for i in range(r["track-count"])]}])
        a = album(title="Endorama", artist="Kreator", folder="1999 - Endorama (2003 Art Music, AMG 099, Russia)",
                  catnos={mc.norm_code("AMG 099")}, catno_search=[])
        ma.mb_search(H(), a, dict(mc.DEFAULT_CFG, mb_detail_fetch=2))
        self.assertIn("ru", fetched)

    def test_enhanced_cd_videos_are_not_tracks(self):
        songs = [{"position": str(i), "title": f"Song {i}", "secs": 200} for i in range(1, 14)]
        rel = {"formats": [{"name": "CD", "descriptions": ["Album", "Enhanced", "Reissue"]}]}
        videos = [{"position": "14", "title": "Chosen Few (Video)", "secs": 0},
                  {"position": "Video", "title": "Golden Age", "secs": 0}]
        self.assertEqual(len(ma.drop_video_tracks(songs + videos, rel)), 13)
        plain = {"formats": [{"name": "CD", "descriptions": ["Album"]}]}
        kept = songs + [{"position": "14", "title": "Video Killed the Radio Star", "secs": 200}]
        self.assertEqual(len(ma.drop_video_tracks(kept, plain)), 14)
        self.assertEqual(len(ma.drop_video_tracks(kept, rel)), 14)        # a song, even on an enhanced CD
        mbrel = {"id": "r", "media": [{"format": "Enhanced CD", "tracks": [
            {"title": "a", "length": 1000}, {"title": "v", "length": 1000, "recording": {"video": True}}]}]}
        self.assertEqual(len(ma.mb_candidate(mbrel)["tracks"]), 1)

    def test_only_discogs_carrying_the_folder_number_wins_over_a_sure_musicbrainz(self):
        nb = mc.norm_code("AMG 099")
        a = album(folder="1999 - First Light (2003 Art Music, AMG 099, Russia)", catnos={nb},
                  catnos_from_folder={nb})
        mb = mc.classify([mc.score_candidate(a, cand(id="jp", country="JP", catnos={"vicp60704"}), CFG)], CFG)
        dg = mc.classify([mc.score_candidate(a, cand(id="7", source="discogs", country="Russia",
                                                     catnos={nb}), CFG)], CFG)
        self.assertEqual(dg["status"], "auto", dg["why"])
        self.assertEqual(mc.combine(mb, dg)["best"]["cand"]["id"], "7")



class Investigation(unittest.TestCase):
    """Exhorder - Slaughter In The Vatican / The Law: the scans carried the catalogue
    number and barcode, yet the album went to a person (or the wrong pressing was
    taken). An album with scans is investigated before it is given up: every scan read
    again in full, every code searched on MusicBrainz, Discogs and the web."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        from PIL import Image
        Image.new("L", (50, 50), 255).save(self.tmp / "Digi.jpg")
        self.saved = (ma.ocr_deep, ma.mb_search, ma.discogs_search, ma.discogs_candidate)

    def tearDown(self):
        ma.ocr_deep, ma.mb_search, ma.discogs_search, ma.discogs_candidate = self.saved
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_a_code_found_only_on_the_web_identifies_the_pressing(self):
        a = album(title="Slaughter In The Vatican / The Law", artist="Exhorder")
        rr = cand(id="rr", title="Slaughter In The Vatican / The Law", artist="Exhorder", country="US",
                  label="Roadrunner", catnos={"rr1"})
        rr2 = dict(rr, id="rr2", country="NL", catnos={"rr2"})
        dis = dict(rr, id="22314946", source="discogs", country="UK", label="Dissonance Productions",
                   catnos={mc.norm_code("QDISS0201CDD")}, barcodes={"5013929020160"})
        scored = [mc.score_candidate(a, c, CFG) for c in (rr, rr2)]
        mb = dict(mc.classify(scored, CFG), scored=scored, source="mb")
        self.assertNotEqual(mb["status"], "auto")
        ma.ocr_deep = lambda paths: "MADE IN EU / MCPS. QDISSO201CDD\nQDISS0201CDD-1 15 A00"
        ma.mb_search = lambda http, album, cfg: []             # the databases' own search finds nothing
        ma.discogs_search = lambda http, album, cfg, dm: []
        ma.discogs_candidate = lambda det, dm: dis
        asked = []

        class H:
            db = None

            def get_text(self, url, host):
                asked.append(url)
                return ('<a href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fwww.discogs.com%2Frelease%2F'
                        '22314946-Exhorder-Slaughter-In-The-Vatican-The-Law">x</a>')

            def get_json(self, url, host):
                return {"id": 22314946} if "api.discogs.com/releases/22314946" in url else None
        out, notes = {}, []
        mb_res, dg_res, final, text = ma.investigate(H(), self.tmp, a, CFG, object(), mb, None, mb, "", out,
                                                     notes, True, True, None)
        self.assertTrue(any("QDISS0201CDD" in u for u in asked), asked)
        self.assertEqual((final["status"], final["best"]["cand"]["id"]), ("auto", "22314946"), final.get("why"))
        self.assertIn("investigated", " ".join(final["best"]["notes"]))
        self.assertEqual(out["investigated"]["web_discogs"], ["22314946"])

    def test_a_sure_pick_without_the_barcode_on_the_scans_is_investigated(self):
        best = {"cand": cand(barcodes={"0016861841127"})}
        final = {"status": "auto", "best": best}
        self.assertTrue(ma._needs_investigation(final, album(), "5 013929 020160 5013929020160", self.tmp))
        self.assertFalse(ma._needs_investigation(final, album(), "0016861841127", self.tmp))
        empty = Path(tempfile.mkdtemp())
        try:
            self.assertFalse(ma._needs_investigation({"status": "review"}, album(), "", empty))  # no scans
        finally:
            shutil.rmtree(empty)


class EveryDocument(unittest.TestCase):
    """Every document in the folder may name the release: notes in subfolders, the
    label's PDF booklet (often only pictures), a Word or HTML page, the cue sheet."""

    def setUp(self):
        self.d = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def codes(self):
        return mc.codes_from_ocr_text(ma.documents_text(self.d), limit=10)

    def test_notes_word_html_and_cue_sheets_are_read(self):
        import zipfile
        (self.d / "tech.info").mkdir()
        (self.d / "tech.info" / "tXt.txt").write_text("Label: Dissonance Productions\nCat#: QDISS0201CDD\n")
        with zipfile.ZipFile(self.d / "notes.docx", "w") as z:
            z.writestr("word/document.xml", '<w:document><w:p><w:r><w:t>Catalogue HHR 2018-23</w:t></w:r></w:p>'
                                            '</w:document>')
        (self.d / "shop.html").write_text("<html><script>var x='ZZZ 999';</script><p>Cat. no. AMG 099</p></html>")
        (self.d / "disc.cue").write_bytes('CATALOG 5013929020160\nFILE "01 ABC 123.flac" WAVE\n'.encode("utf-16"))
        (self.d / "rip.log").write_text("Used drive  : PLEXTOR PX-716A\nRead offset correction : 30\n")
        # a tracker's notes (Exhorder - The Law): the source pressing, an image host's links, a DR report
        (self.d / "tech.info" / "release.txt").write_text(
            "[b]Source:[/b] Metal Mind Productions - MASS CD 1176 DG\n"
            "[img]https://i123.fastpic.org/big/2024/0522/91/88ea2eed2bb5df9f710cd0f0c6155444515556.png[/img]\n"
            "DR10  -0.20 dB  -12.50 dB  4:50 01-Soul Search Me\n")
        c = self.codes()
        self.assertIn("5013929020160", c["barcodes"])
        for code in ("QDISS0201CDD", "HHR 2018-23", "AMG 099", "MASS CD 1176"):
            self.assertIn(code, c["catnos"])
        self.assertFalse({"PX-716", "ZZZ 999", "ABC 123", "DR10"} & set(c["catnos"]), c)   # drive, script, file, DR
        self.assertEqual(c["barcodes"], ["5013929020160"])                                 # not the link's digits

    def test_a_pdf_booklet_of_pictures_is_read_like_a_scan(self):
        if not (shutil.which("tesseract") and shutil.which("pdftoppm")):
            self.skipTest("tesseract or poppler not installed")
        from PIL import Image, ImageDraw, ImageFont
        page = Image.new("RGB", (1700, 2200), "white")
        f = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
        font = ImageFont.truetype(f, 48) if Path(f).exists() else None
        ImageDraw.Draw(page).text((150, 1900), "Made in EU  NB 1234-2", font=font, fill="black")
        page.save(self.d / "booklet.pdf", resolution=200)
        self.assertIn("NB 1234-2", self.codes()["catnos"])


class ShopTrackIds(unittest.TestCase):
    """Lamb of God - Lamb Of God (2021 deluxe, 2 discs): a Deezer download, its files
    carrying SOURCE=Deezer and SOURCEID=<Deezer track ID>. The Deezer album page, which
    MusicBrainz links to the digital release, follows from the track."""

    def test_a_deezer_track_id_in_the_tags_leads_to_the_release(self):
        d = Path(tempfile.mkdtemp())
        try:
            subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "sine=duration=1",
                            "-metadata", "source=Deezer", "-metadata", "sourceid=1203751422",
                            str(d / "111.GSP.flac"), "-y"], check=True)
            a = ma.read_album(d, "Lamb of God", "2020 - Lamb Of God", blind=True)
            self.assertEqual(a["store_tracks"], ["deezer:1203751422"])     # a fresh download has it too
            asked = []

            class H:
                def get_json(self, url, host):
                    asked.append(url)
                    if url == "https://api.deezer.com/track/1203751422":
                        return {"id": 1203751422, "album": {"id": 220000000}}
                    if "/ws/2/url?resource=" in url and "deezer.com/album/220000000" in url:
                        return {"relations": [{"release": {"id": "ab57f8fa-2e7f-488c-aceb-7d99654f1f22"}}]}
                    return {}
            self.assertIn("ab57f8fa-2e7f-488c-aceb-7d99654f1f22", ma.link_releases(H(), a))
        finally:
            shutil.rmtree(d)


class FormatTheFolderNames(unittest.TestCase):
    """Sunn O))) & Ulver - 2014 - Terrestrials [Vinyl Rip 24bit 192khz]: the title search
    found the Japanese CD (with the same track count), so the album's other versions were
    never fetched and the vinyl was "not found". The format the folder names is searched
    for itself, and the master's versions are fetched when no candidate has it."""

    def test_the_vinyl_the_folder_names_is_fetched(self):
        def rel(rid, country, fmt):
            return {"id": rid, "title": "Terrestrials", "year": 2014, "country": country,
                    "artists": [{"name": "Sunn O)))", "id": 1}, {"name": "Ulver", "id": 2, "join": "&"}],
                    "labels": [{"name": "L", "catno": f"X{rid}"}], "formats": [{"name": fmt, "qty": "1"}],
                    "tracklist": [{"position": str(i + 1), "title": f"T{i}", "duration": "12:00", "type_": "track"}
                                  for i in range(3)]}
        asked = []

        class H:
            def get_json(self, url, host):
                asked.append(url)
                if "/database/search" in url:
                    if "format=Vinyl" in url:
                        return {"results": []}           # the format filter alone may find nothing
                    return {"results": [{"id": 1, "title": "Sunn O))) & Ulver - Terrestrials", "year": "2014",
                                         "master_id": 10}]}
                if url.endswith("/releases/1"):
                    return rel(1, "Japan", "CD")
                if "/masters/10/versions" in url:
                    return {"versions": [{"id": 1, "format": "CD", "country": "Japan"},
                                         {"id": 2, "format": "2xVinyl, LP", "country": "US"}]}
                if url.endswith("/releases/2"):
                    return rel(2, "US", "Vinyl")
                return None
        a = album(artist="Sunn O))) & Ulver", title="Terrestrials", year=2014, media_hint="vinyl",
                  folder="2014 - Terrestrials [Vinyl Rip 24bit 192khz]", catno_search=[],
                  tracks=[{"disc": 1, "num": i + 1, "title": f"T{i}", "secs": 720.0} for i in range(3)])
        out = ma.discogs_search(H(), a, mc.DEFAULT_CFG, ma.load_plugin(PLUGIN_DIR))
        self.assertIn("2", [str(c["id"]) for c in out])
        self.assertTrue(any("format=Vinyl" in u for u in asked))

class FilesNameTheArtist(unittest.TestCase):
    """Aghora - Entheogenic Frequencies Orchestral Representations: filed under Aghora,
    but the files' own ARTIST and COPYRIGHT tags and the ripper's .info name Gustavo
    Dobles, and the title and every track length agree: it is his, without asking."""

    def test_track_artist_and_copyright_tags_prove_the_credit(self):
        a = album(artist="Aghora", title="Entheogenic Frequencies Orchestral Representations",
                  credit_text="Gustavo Dobles 2020 Gustavo Dobles Label: Dobles Music / Tidal")
        c = cand(artist="Gustavo Dobles", title="Entheogenic Frequencies Orchestral Representations")
        r = mc.classify([mc.score_candidate(a, c, CFG)], CFG)
        self.assertEqual(r["status"], "auto", r["why"])
        self.assertIn("naming this artist", " ".join(r["best"]["notes"]))
        r = mc.classify([mc.score_candidate(dict(a, credit_text="Aghora"), c, CFG)], CFG)
        self.assertNotEqual(r["status"], "auto")                         # nothing names him: still asks
        far = [{"disc": 1, "title": f"T{i}", "secs": x} for i, x in enumerate((260, 305, 410))]
        r = mc.classify([mc.score_candidate(a, dict(c, tracks=far), CFG)], CFG)
        self.assertNotEqual(r["status"], "auto")                         # lengths must all agree too

    def test_credit_text_is_read_from_tags_and_notes(self):
        import tempfile, shutil
        d = Path(tempfile.mkdtemp())
        try:
            subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "sine=duration=1",
                            "-metadata", "artist=Gustavo Dobles", "-metadata", "album_artist=Aghora",
                            "-metadata", "copyright=2020 Gustavo Dobles", str(d / "01.flac"), "-y"], check=True)
            (d / "notes.info").write_text("Label: Dobles Music / Tidal")
            al = ma.read_album(d, "Aghora", "2020 - Representations")
            self.assertIn("Gustavo Dobles", al["credit_text"])
            self.assertIn("Dobles Music", al["credit_text"])
            self.assertEqual(al["artist"], "Aghora")
            self.assertNotIn("Gustavo", ma.read_album(d, "Aghora", "x", blind=True)["credit_text"].split("Label")[0])
        finally:
            shutil.rmtree(d)


class RemasterEditions(unittest.TestCase):
    """[2021 RM] in a folder name: a remaster, usually a digital release, dated near that year."""

    def test_media_named_in_folder(self):
        self.assertEqual(mc.media_hint("2006 - Dante XXI [2021 RM]"), "digital")
        self.assertEqual(mc.media_hint("2001 - Nothing [2019 RM Vinyl Rip 24bit 96khz]"), "vinyl")
        self.assertEqual(mc.media_hint("2000 - Reinventing the Steel [3CD 2020 20th Annv Edn]"), "cd")
        self.assertEqual(mc.media_hint("1990 - Coma Of Souls (2018 RM Noise, NOISE2CD046, Germany)"), "digital")
        self.assertEqual(mc.media_hint("2010 - Zenith"), "")
        self.assertEqual(mc.media_kind('12" Vinyl'), "vinyl")
        self.assertEqual(mc.media_kind("Digital Media"), "digital")
        self.assertEqual(mc.media_kind("File"), "digital")
        self.assertEqual(mc.media_kind("File, FLAC, Album, Stereo"), "digital")    # the Discogs plugin's media
        # a format written after the number: Kreator - Enemy Of God Revisited (SPV 69848 CD+DVD)
        self.assertTrue(mc.catno_same("SPV 69848", "SPV 69848 CD+DVD"))
        self.assertTrue(mc.catno_same("NB 3053-5", "NB 3053-5 2CD"))
        self.assertFalse(mc.catno_same("SPV 69848", "SPV 6984L CD+DVD"))
        self.assertFalse(mc.catno_same("NB 3099-0", "NB 3099-2"))
        self.assertEqual(mc.media_kind("Enhanced CD"), "cd")

    def test_remaster_dated_a_year_or_two_off_is_that_edition(self):
        a = album(pressing_year=2019)
        r = mc.classify([mc.score_candidate(a, cand(id="rm", year=2021), CFG),
                         mc.score_candidate(a, cand(id="orig", year=2001), CFG)], CFG)
        self.assertEqual(r["best"]["cand"]["id"], "rm")
        self.assertFalse(any("edition" in w for w in r["why"]), r["why"])
        r = mc.classify([mc.score_candidate(album(pressing_year=2021), cand(id="orig", year=2006), CFG)], CFG)
        self.assertTrue(any("2021 edition" in w for w in r["why"]))      # far off: still review

    def test_exact_year_preferred_among_equals(self):
        a = album(pressing_year=2019)
        r = mc.classify([mc.score_candidate(a, cand(id="b", year=2021), CFG),
                         mc.score_candidate(a, cand(id="a", year=2019), CFG)], CFG)
        self.assertEqual(r["best"]["cand"]["id"], "a")

    def test_digital_remaster_picked_over_cd_and_vinyl_of_the_same_edition(self):
        a = album(pressing_year=2021, media_hint="digital")
        r = mc.classify([mc.score_candidate(a, cand(id="cd", year=2021, formats={"cd"}), CFG),
                         mc.score_candidate(a, cand(id="lp", year=2021, formats={"vinyl"}), CFG),
                         mc.score_candidate(a, cand(id="web", year=2021, formats={"digital"}), CFG)], CFG)
        self.assertEqual((r["status"], r["best"]["cand"]["id"]), ("auto", "web"), r["why"])
        self.assertIn("digital release (folder name)", " ".join(r["best"]["notes"]))

    def test_two_digital_releases_still_need_a_person(self):
        a = album(pressing_year=2021, media_hint="digital")
        r = mc.classify([mc.score_candidate(a, cand(id="w1", year=2021, formats={"digital"}), CFG),
                         mc.score_candidate(a, cand(id="w2", year=2021, formats={"digital"}), CFG)], CFG)
        self.assertEqual(r["status"], "review")



class OriginalPressing(unittest.TestCase):
    """John's rule for pressings nothing about the copy tells apart: a new album gets the
    label's original pressing (the first released); only pressings released together fall
    back to the country order. Albums already tagged keep their tag (validate.py does not
    export such picks as fixes)."""

    def test_the_first_pressing_released_is_picked(self):
        a = album()
        r = mc.classify([mc.score_candidate(a, cand(id="lic", country="BR", date="2010-11-02"), CFG_PREF),
                         mc.score_candidate(a, cand(id="orig", country="DE", date="2010-03-15"), CFG_PREF),
                         mc.score_candidate(a, cand(id="us", country="US", date="2010-04-20"), CFG_PREF)], CFG_PREF)
        self.assertEqual((r["status"], r["best"]["cand"]["id"]), ("auto", "orig"), r["why"])
        self.assertIn("label's original pressing", " ".join(r["best"]["notes"]))

    def test_pressings_released_together_fall_back_to_the_country_order(self):
        a = album()
        r = mc.classify([mc.score_candidate(a, cand(id="de", country="DE", date="2010-03-15"), CFG_PREF),
                         mc.score_candidate(a, cand(id="us", country="US", date="2010-03-15"), CFG_PREF)], CFG_PREF)
        self.assertEqual(r["best"]["cand"]["id"], "us")
        self.assertIn("preferred country order", " ".join(r["best"]["notes"]))
        # a year alone cannot say which came first within it
        self.assertIsNone(mc.prefer_original([mc.score_candidate(a, cand(id="p", date="2010"), CFG),
                                              mc.score_candidate(a, cand(id="q", date="2010-06-01"), CFG)]))

    def test_an_edition_year_in_the_folder_is_not_overruled(self):
        a = album(pressing_year=2019)
        self.assertIsNone(mc.prefer_original([mc.score_candidate(a, cand(id="rm", year=2019), CFG),
                                              mc.score_candidate(a, cand(id="o", year=2001), CFG)]))

    def test_between_the_databases_the_original_wins_too(self):
        a = album()
        mb = mc.classify([mc.score_candidate(a, cand(id="mbus", country="US", date="2012-05-01"), CFG)], CFG)
        dg = mc.classify([mc.score_candidate(a, cand(id="dgde", source="discogs", country="DE",
                                                         date="2010-03-15"), CFG)], CFG)
        out = mc.combine(dict(mb, scored=[]), dict(dg, scored=[]), ["CA", "US", "DE"])
        self.assertEqual(out["best"]["cand"]["id"], "dgde")
        self.assertIn("label's original pressing", " ".join(out["best"]["notes"]))



class ShortCatalogueNumbers(unittest.TestCase):
    def test_a_short_code_printed_on_its_own_counts(self):
        """At The Gates - Gardens Of Grief: the back reads "BS.OO4, P&G BLACK SUN RECORDS 1995";
        BS 004 normalises to "bs4", too short for the general rule, so the Black Sun pressing
        tied with the 1991 one."""
        c = cand(catnos={mc.norm_code("BS 004")})
        agree, _ = mc.pressing_clues("2 BS.OO4, P&G BLAGK SUN RECORDS 1995 ALL RIGHTS RESERVED", c)   # as read
        self.assertIn("catalogue number", agree)
        agree, _ = mc.pressing_clues("Track lengths: ABS 0045 and BS 0041 elsewhere", c)
        self.assertNotIn("catalogue number", agree)          # part of another code: no

class AlbumKnownPressingNot(unittest.TestCase):
    def test_a_tie_between_pressings_of_a_certain_album_is_album_only(self):
        a = album()
        tied = [mc.score_candidate(a, cand(id="p1", country="US", date="2018"), CFG),
                mc.score_candidate(a, cand(id="p2", country="DE", date="2018"), CFG)]
        r = dict(mc.classify(tied, CFG), scored=tied)
        self.assertEqual(r["status"], "review")
        ao = mc.album_only(r, CFG)
        self.assertEqual(sorted(t["id"] for t in ao["tied"]), ["p1", "p2"])
        # not when the doubt is about the album itself
        far = [mc.score_candidate(a, cand(id="x", tracks=[{"disc": 1, "title": "T0", "secs": 260}] * 3), CFG)]
        self.assertIsNone(mc.album_only(dict(mc.classify(far, CFG), scored=far), CFG))

    def test_what_every_pressing_shares_is_kept(self):
        tags = {"album": ["A"], "label": ["L"], "releasecountry": ["US"], "date": ["2018-06-01"],
                "originaldate": ["2017-01-01"], "barcode": ["1"], "catalognumber": ["C"], "media": ["CD"],
                "musicbrainz_albumid": ["m"], "discogs_release_id": ["9"], "genre": ["Metal"]}
        tied = [{"label": "L", "country": "US", "formats": ["cd"], "year": 2018},
                {"label": "L", "country": "DE", "formats": ["cd"], "year": 2017}]
        t = mc.album_only_tags(tags, tied)
        self.assertEqual(sorted(t), ["album", "date", "genre", "label", "media", "originaldate"])
        self.assertEqual(t["date"], ["2017-01-01"])           # pressings from different years: the original

    def test_a_cd_rip_is_tagged_from_the_cds_only(self):
        """John: a log or cue sheet (a CD rip) or a download decides which kind of release
        the album tags come from; a CD rip's media is then CD."""
        a = album()
        tied = [mc.score_candidate(a, cand(id="cd1", country="US", formats={"cd"}), CFG),
                mc.score_candidate(a, cand(id="cd2", country="DE", formats={"cd"}), CFG),
                mc.score_candidate(a, cand(id="web", country="XW", formats={"digital"}), CFG)]
        r = dict(mc.classify(tied, CFG), scored=tied)
        ao = mc.album_only(r, CFG, "cd")
        self.assertEqual(sorted(t["id"] for t in ao["tied"]), ["cd1", "cd2"])
        t = mc.album_only_tags({"media": ["CD"], "album": ["A"]}, ao["tied"])
        self.assertEqual(t["media"], ["CD"])
        self.assertEqual(ma.copy_source({"tocs": {1: "x"}, "audio": {}}), "cd")
        self.assertEqual(ma.copy_source({"store_tracks": ["deezer:1"], "audio": {}}), "digital")
        self.assertEqual(ma.copy_source({"media_hint": "vinyl", "tocs": {1: "x"}}), "vinyl")
        self.assertEqual(ma.copy_source({"audio": {"hires": True}}), "")

class PressingEvidence(unittest.TestCase):
    """Tied pressings told apart by the files, the folder and the audio; albums proven
    by a disc ID or catalogue number keep the database's title and artist."""

    def pick(self, a, *cands):
        return mc.classify([mc.score_candidate(a, c, CFG) for c in cands], CFG)

    def test_disc_id_picks_the_pressing(self):
        r = self.pick(album(toc_releases={"b"}), cand(id="a", country="US"), cand(id="b", country="DE"))
        self.assertEqual((r["status"], r["best"]["cand"]["id"]), ("auto", "b"), r["why"])
        self.assertIn("disc ID", " ".join(r["best"]["notes"]))

    def test_a_short_folder_title_does_not_drop_the_release_its_numbers_prove(self):
        """Sepultura - 2013 - The Mediator [Nuclear Blast, NB 3099-0, Germany]: the European
        digipak carries the folder's NB 3099-0 and the files' barcode, but "The Mediator" is
        only part of its title, so every release was dropped and the album left for review."""
        long = "The Mediator Between Head and Hands Must Be the Heart"
        a = album(title="The Mediator", folder="2013 - The Mediator [Nuclear Blast, NB 3099-0, Germany]",
                  catnos={mc.norm_code("NB 3099-0")}, catnos_from_folder={mc.norm_code("NB 3099-0")},
                  barcodes={"727361309909"})
        r = self.pick(a, cand(id="ru", country="RU", title=long, label="Nuclear Blast",
                              catnos={mc.norm_code("NB 3099-0")}, barcodes={"727361309909"}),
                      cand(id="eu", country="XE", title=long, label="Nuclear Blast",
                           catnos={mc.norm_code("NB 3099-0")}, barcodes={"727361309909"}),
                      cand(id="jewel", country="XE", title=long, label="Nuclear Blast",
                           catnos={mc.norm_code("NB 3099-2")}))
        self.assertEqual((r["status"], r["best"]["cand"]["id"]), ("auto", "eu"), r["why"])
        # names that agree with nothing proving the album still leave it for a person
        r = self.pick(album(title="The Mediator", folder="2013 - The Mediator [Germany]"),
                      cand(id="ru", country="RU", title=long), cand(id="us", country="US", title=long))
        self.assertNotEqual(r["status"], "auto")

    def test_folder_number_with_a_disc_digit_and_country_decide(self):
        """Obscura - 2011 - Omnivium [Relapse Records, US, RR 7157-2, LTD Edn]: three pressings
        carry RR 7157 and the barcode; "RR 7157-2" is the same number and "US" picks one."""
        code = {mc.norm_code("RR 7157-2")}
        a = album(title="Omnivium", folder="2011 - Omnivium [Relapse Records, US, RR 7157-2, LTD Edn]",
                  catnos=code, catnos_from_folder=code, barcodes={"781676715725"})
        rr = {mc.norm_code("RR 7157")}
        r = self.pick(a, cand(id="xe", country="XE", title="Omnivium", catnos=rr, barcodes={"781676715725"}),
                      cand(id="de", country="DE", title="Omnivium", catnos=rr, barcodes={"781676715725"}),
                      cand(id="us", country="US", title="Omnivium", catnos={mc.norm_code("RR7157")},
                           barcodes={"781676715725"}))
        self.assertEqual((r["status"], r["best"]["cand"]["id"]), ("auto", "us"), r["why"])
        self.assertFalse(r["best"].get("own_catno_absent"))

    def test_folder_number_without_the_formats_discogs_writes_after_it(self):
        """Kreator - Enemy Of God Revisited (Steamhammer, SPV 69848 CD+DVD, Germany): Discogs
        lists the German pressing as "SPV 69848 CD+DVD", the folder's number reads SPV 69848;
        they were not seen as the same, so no release fit the folder and it went to review."""
        n = mc.norm_code
        self.assertTrue(mc.catnos_agree({n("SPV 69848")}, {n("SPV 69848 CD+DVD")}))
        self.assertTrue(mc.catno_same(n("SPV 69848 CD+DVD"), n("SPV 69848")))
        self.assertFalse(mc.catno_same(n("SPV 69848"), n("SPV 6984L CD+DVD")))
        self.assertFalse(mc.catno_same(n("SPV 69848"), n("SPV 69849 CD")))
        code = {n("SPV 69848")}
        a = album(title="Enemy Of God Revisited", folder="2006 - Enemy Of God Revisited (Steamhammer, SPV 69848 CD+DVD, Germany)",
                  catnos=code, catnos_from_folder=code)
        r = self.pick(a, cand(id="ar", country="Argentina", title="Enemy Of God Revisited", catnos={n("ICARUS 320")}),
                      cand(id="us", country="US", title="Enemy Of God Revisited", catnos={n("SPV 6984L CD+DVD")}),
                      cand(id="de", country="Germany", title="Enemy Of God Revisited", catnos={n("SPV 69848 CD+DVD")}))
        self.assertEqual((r["status"], r["best"]["cand"]["id"]), ("auto", "de"), r["why"])

    def test_the_folders_edition_year_rules_out_other_pressings_in_a_tie(self):
        """Sepultura - 2006 - Dante XXI [2021 RM]: disc 4 of BMG's 2021 box (CD and vinyl
        boxes score the same). Your country order picked the 2006 US CD, which scored lower
        and is not the edition the folder names."""
        tr = [{"disc": 1, "num": i + 1, "title": f"T{i}", "secs": 200.0 + i} for i in range(3)]
        a = album(title="Dante XXI", year=2006, folder="2006 - Dante XXI [2021 RM]", pressing_year=2021, tracks=tr)

        def c(**kw):
            return cand(title="Dante XXI", tracks=[dict(x) for x in tr], **kw)
        us = c(id="us06", country="US", year=2006, date="2006", formats={"cd"})
        cd = c(id="cdbox", country="XE", year=2021, date="2021", formats={"cd"})
        lp = c(id="lpbox", country="XE", year=2021, date="2021", formats={"vinyl"})
        s = [mc.score_candidate(a, x, CFG) for x in (us, cd, lp)]
        r = dict(mc.classify(s, CFG), scored=s)
        self.assertIn(r["best"]["cand"]["id"], ("cdbox", "lpbox"))
        ao = mc.album_only(r, CFG, "")
        self.assertEqual(sorted(t["id"] for t in ao["tied"]), ["cdbox", "lpbox"])   # never the 2006 CD
        s = [mc.score_candidate(a, x, CFG) for x in (us, cd)]
        r = mc.classify(s, CFG)
        self.assertEqual((r["status"], r["best"]["cand"]["id"]), ("auto", "cdbox"), r["why"])

    def test_the_scans_packaging_picks_the_pressing(self):
        """Cynic - 2015 - Carbon-Based Anatomy: the jewel case and the digipak share the barcode
        and number; the folder's "scan-digipak.jpg" says which one this is."""
        code = {mc.norm_code("SOM 381")}
        a = album(title="Carbon-Based Anatomy", folder="2015 - Carbon-Based Anatomy",
                  catnos=code, barcodes={"802644838124"}, packaging={"digipak"})
        jewel = cand(id="jewel", country="XE", title="Carbon-Based Anatomy", catnos=code,
                     barcodes={"802644838124"}, packaging="jewel")
        digi = cand(id="digi", country="XE", title="Carbon-Based Anatomy", catnos=code,
                    barcodes={"802644838124"}, packaging="digipak")
        for order in ((jewel, digi), (digi, jewel)):
            r = self.pick(a, *order)
            self.assertEqual((r["status"], r["best"]["cand"]["id"]), ("auto", "digi"), r["why"])
        # scans that name no packaging decide nothing
        r = self.pick(dict(a, packaging=set()), jewel, digi)
        self.assertNotEqual(r["status"], "auto")

    def test_packaging_names(self):
        for text, kind in (("scan-digipak", "digipak"), ("digi0000", "digipak"), ("Digipack front", "digipak"),
                           ("Digibook 01", "digibook"), ("Jewel Case", "jewel"), ("Slim Jewel Case", "jewel"),
                           ("Mediabook", "mediabook"), ("Gatefold Cover", "gatefold"),
                           ("digital booklet", ""), ("Digital Media", ""), ("book0000", ""), ("", "")):
            self.assertEqual(mc.packaging_kind(text), kind, text)

    def test_the_folders_own_number_outweighs_lost_points(self):
        """ISIS - 2004 - 2012 - Live I - VI [Daymare Recordings, Japan, DYMC-150]: the catalogue
        number and every track agree, but the folder's year (the recording years) and a
        different disc split cost points: 89.5 < 90 left it for review."""
        code = {mc.norm_code("DYMC-150")}
        secs = [200, 300, 250, 400, 350, 280, 320]
        a = album(artist="Isis", title="Live I - VI", year=2004, discs=3,
                  folder="2004 - 2012 - Live I - VI [Daymare Recordings, Japan, DYMC-150]",
                  catnos=code, catnos_from_folder=code,
                  tracks=[{"disc": 1, "num": i + 1, "title": f"T{i}", "secs": float(s + (60 if i == 6 else 0))}
                          for i, s in enumerate(secs)])
        c = cand(id="jp", artist="Isis", title="Live I - VI", year=2012, country="JP", label="Daymare Recordings",
                 catnos=code, disc_count=4,
                 tracks=[{"disc": 1 + i // 2, "title": f"T{i}", "secs": s} for i, s in enumerate(secs)])
        s = mc.score_candidate(a, c, CFG)
        r = mc.classify([s], CFG)
        self.assertEqual(r["status"], "auto", (s["confidence"], r["why"]))
        # without the number, the same points stay under the bar
        r = self.pick(dict(a, folder="2004 - 2012 - Live I - VI", catnos=set(), catnos_from_folder=set()),
                      dict(c, catnos=set(), label="Other"))
        self.assertNotEqual(r["status"], "auto")

    def test_one_disc_of_a_box_set(self):
        """Testament - 1988 - The New Order (2013, Warner, 8122796510-1): disc 2 of the 5CD
        Original Album Series. Compared with whole releases it fitted none; now the box's
        disc that has the folder's tracks, lengths and name is the match, under the box's ID."""
        names = ["The Legacy", "The New Order", "Practice What You Preach"]
        lens = {1: [200, 210, 220], 2: [300, 310, 320, 330], 3: [400, 410, 420]}
        box = cand(id="box", artist="Testament", title="Original Album Series", year=2013,
                   catnos={mc.norm_code("8122796510")}, disc_count=3,
                   tracks=[{"disc": d, "title": f"t{d}{i}", "secs": s} for d in lens for i, s in enumerate(lens[d])],
                   disc_titles={i + 1: n for i, n in enumerate(names)})
        orig = cand(id="1988", artist="Testament", title="The New Order", year=1988,
                    catnos={mc.norm_code("7 81849-2")},
                    tracks=[{"disc": 1, "title": f"t2{i}", "secs": s} for i, s in enumerate(lens[2])])
        code = {mc.norm_code("8122796510-1")}
        a = album(artist="Testament", title="The New Order", year=1988, pressing_year=2013,
                  folder="1988 - The New Order (2013, Warner, 8122796510-1)", catnos=code, catnos_from_folder=code,
                  tracks=[{"disc": 1, "num": i + 1, "title": f"t2{i}", "secs": s + 1.0} for i, s in enumerate(lens[2])])
        r = self.pick(a, orig, box)
        self.assertEqual((r["status"], r["best"]["cand"]["id"], r["best"]["cand"]["box_disc"]), ("auto", "box", 2),
                         r["why"])
        self.assertIn("disc 2 of Original Album Series", r["best"]["notes"])
        self.assertTrue(mc.catno_same("8122796510-1", "8122796510"))
        # a disc named otherwise is never taken for the album
        self.assertIsNone(mc._box_disc(dict(a, title="Souls of Black"), box))

    def test_label_and_country_in_the_folder_name(self):
        a = album(folder="2007 - Fiction [Century Media, 9976158, Germany]")
        r = self.pick(a, cand(id="us", country="US", label="Century Media Records"),
                      cand(id="de", country="DE", label="Century Media"))
        self.assertEqual((r["status"], r["best"]["cand"]["id"]), ("auto", "de"), r["why"])

    def test_folder_country_rules_out_a_pressing(self):
        a = album(folder="2005 - Character [Toys Factory, Japan]")
        r = self.pick(a, cand(id="de", country="DE", label="Century Media"),
                      cand(id="jp", country="JP", label="Toy's Factory"))
        self.assertEqual(r["best"]["cand"]["id"], "jp")

    def test_tags_name_the_pressing(self):
        a = album(tag_label="Nuclear Blast", tag_country="DE")
        r = self.pick(a, cand(id="us", country="US", label="Nuclear Blast America"),
                      cand(id="de", country="DE", label="Nuclear Blast"))
        self.assertEqual((r["status"], r["best"]["cand"]["id"]), ("auto", "de"), r["why"])

    def test_hi_res_audio_is_not_from_a_cd(self):
        a = album(audio={"hires": True, "cd_frames": False})
        r = self.pick(a, cand(id="cd", formats={"cd"}), cand(id="web", formats={"digital"}))
        self.assertEqual((r["status"], r["best"]["cand"]["id"]), ("auto", "web"), r["why"])
        self.assertIn("no CD carries", " ".join(r["best"]["notes"]))

    def test_cd_rip_prefers_the_cd(self):
        a = album(audio={"hires": False, "cd_frames": True})
        r = self.pick(a, cand(id="web", formats={"digital"}), cand(id="cd", formats={"cd"}))
        self.assertEqual((r["status"], r["best"]["cand"]["id"]), ("auto", "cd"), r["why"])

    def test_vinyl_rip_never_takes_the_cd(self):
        a = album(folder="2001 - Nothing [2019 RM Vinyl Rip 24bit 96khz]", media_hint="vinyl",
                  audio={"hires": True, "cd_frames": False})
        r = self.pick(a, cand(id="cd", formats={"cd"}), cand(id="lp", formats={"vinyl"}))
        self.assertEqual((r["status"], r["best"]["cand"]["id"]), ("auto", "lp"), r["why"])

    def test_vinyl_rip_is_never_tagged_as_the_digital_release(self):
        """Sunn O))) & Ulver - Terrestrials [Vinyl Rip 24bit 192khz]: the digital release's
        lengths agree better than the 12" vinyl's, but the folder says vinyl."""
        a = album(folder="2014 - Terrestrials [Vinyl Rip 24bit 192khz]", media_hint="vinyl", discs=2,
                  audio={"hires": True, "cd_frames": False})
        far = [{"disc": 1, "title": f"T{i}", "secs": s} for i, s in enumerate((215, 290, 430))]
        r = self.pick(a, cand(id="web", formats={"digital"}), cand(id="lp", formats={"vinyl"}, tracks=far))
        self.assertEqual((r["status"], r["best"]["cand"]["id"]), ("auto", "lp"), r["why"])
        self.assertIn("folder name says vinyl", " ".join(r["best"]["notes"]))
        r = self.pick(a, cand(id="web", formats={"digital"}))            # no vinyl listed: a person decides
        self.assertEqual(r["status"], "review")
        self.assertIn("no release of this album found that fits", " ".join(r["why"]))

    # Folder statements are rules: the wrong release scoring HIGHER must still lose.
    def off(self, *secs):
        return [{"disc": 1, "title": f"T{i}", "secs": x} for i, x in enumerate(secs)]

    def test_folder_country_beats_better_fitting_lengths(self):
        a = album(folder="2007 - Fiction [Century Media, Germany]")
        r = self.pick(a, cand(id="us", country="US", label="Century Media"),
                      cand(id="de", country="DE", label="Century Media", tracks=self.off(200, 305, 416)))
        self.assertNotEqual(r["best"]["cand"]["id"], "us", r["why"])
        self.assertEqual(r["best"]["cand"]["id"], "de")
        r = self.pick(a, cand(id="us", country="US", label="Century Media"))     # Germany not listed
        self.assertEqual(r["status"], "review")

    def test_folder_catalogue_number_beats_better_fitting_lengths(self):
        nb = mc.norm_code("NB 1234-2")
        a = album(folder="2010 - First Light [Nuclear Blast, NB 1234-2]", catnos={nb}, catnos_from_folder={nb})
        r = self.pick(a, cand(id="other", catnos={mc.norm_code("NB 9999-2")}),
                      cand(id="mine", catnos={mc.norm_code("NB 1234-2 CD")}, tracks=self.off(200, 305, 416)))
        self.assertEqual(r["best"]["cand"]["id"], "mine", r["why"])

    def test_folder_number_on_the_release_outweighs_its_country(self):
        """Kreator Phantom Antichrist (Nuclear Blast, 27361 25880, Germany): the European
        pressing carries that number; Coroner (Noise, NZCDP 4, USA) is listed as German."""
        k = mc.norm_code("27361 25880")
        a = album(folder="2012 - First Light (Nuclear Blast, 27361 25880, Germany)", catnos={k},
                  catnos_from_folder={k})
        r = self.pick(a, cand(id="eu", country="XE", label="Nuclear Blast", catnos={k}),
                      cand(id="us", country="US", label="Nuclear Blast", catnos={"nb1"}))
        self.assertEqual((r["status"], r["best"]["cand"]["id"]), ("auto", "eu"), r["why"])
        r = self.pick(album(folder="2010 - First Light [Century Media, Germany]"),
                      cand(id="eu", country="XE", label="Century Media"))
        self.assertEqual(r["status"], "auto", r["why"])                # Germany is in Europe

    def test_same_pressing_listed_twice_takes_the_entry_with_your_edition_year(self):
        """Sleep - Sleep's Holy Mountain [2015 ReIssue]: two entries share label, catalogue
        number and barcode, one dated 2015 and one 1992. The 2015 one is your copy."""
        a = album(folder="1992 - First Light [2015 ReIssue]", pressing_year=2015, barcodes={"5018615107927"})
        kw = dict(label="Earache", catnos={"mosh1"}, barcodes={"5018615107927"}, country="GB")
        r = self.pick(a, cand(id="old", year=1992, date="1992-10-01", **kw),
                      cand(id="re", year=2015, date="2015", **kw))
        self.assertEqual(r["best"]["cand"]["id"], "re", r["why"])

    def test_a_live_venue_is_not_the_pressing_country(self):
        a = album(folder="2013 - First Light (Live at Roadburn 2011 - Holland)")
        r = self.pick(a, cand(id="se", country="SE", label="Svart"))
        self.assertEqual(r["status"], "auto", r["why"])

    def test_a_disc_id_linked_to_one_discogs_pressing_does_not_pick_it(self):
        """Alcest - Les Chants de l'aurore: MusicBrainz's Brazilian release carries the disc
        ID and links to its Discogs entry; Nuclear Blast's own CDs on Discogs have no such
        link. That is no evidence the copy is Brazilian."""
        a = album(toc_discogs={"br"})
        r = mc.classify([mc.score_candidate(a, cand(id="br", source="discogs", country="Brazil",
                                                    label="Shinigami"), CFG_PREF),
                         mc.score_candidate(a, cand(id="eu", source="discogs", country="Europe",
                                                    label="Nuclear Blast"), CFG_PREF)], CFG_PREF)
        self.assertNotEqual(r["best"]["cand"]["id"], "br", r["why"])
        # a MusicBrainz disc ID still decides between MusicBrainz releases
        r = self.pick(album(toc_releases={"b"}), cand(id="a", country="US"), cand(id="b", country="DE"))
        self.assertEqual(r["best"]["cand"]["id"], "b")

    def test_self_released_album_is_one_release_on_both_databases(self):
        mbc = cand(id="m", label="[no label]", country="XW", formats={"digital"})
        dgc = cand(id="d", source="discogs", label="Not On Label (Band Self-released)", country="Australia",
                   formats={"digital"})
        self.assertTrue(mc.same_pressing(mbc, dgc))
        self.assertFalse(mc.same_pressing(mbc, dict(dgc, year=2014)))
        self.assertFalse(mc.same_pressing(mbc, dict(dgc, label="Napalm")))

    def test_hammerheart_back_cover_beats_the_rights_holders_name(self):
        """Cryptopsy - The Book of Suffering, Tome II: the back reads 'P.O. Box 277, 6300 AG
        Valkenburg, The Netherlands', 'HHR 2018-23' on the spine, barcode 8715392182327,
        and the copyright line '2018 Defen Society 3000, Cryptopsy, all rights reserved'.
        The rights holder's name must not pick the Canadian Defen Society release."""
        back = ("P.O. Box 277 \u2022 6300 AG Valkenburg \u2022 The Netherlands \u2022 www.hammerheart.com\n"
                "\u2117 2018 Defen Society 3000 \u2022 Cryptopsy all rights reserved Socan\n"
                "CRYPTOPSY - THE BOOK OF SUFFERING TOME II - HHR 2018-23\n8\u00b0715392\u00b018 2327")
        self.assertEqual(mc.codes_from_ocr_text(back)["barcodes"], ["8715392182327"])
        self.assertIn("NL", mc.countries_on_scans(back))
        hh = cand(id="hh", country="XE", label="Hammerheart Records", catnos={mc.norm_code("HHR 2018-23")},
                  barcodes={"8715392182327"})
        ds = cand(id="ds", country="CA", label="Defen Society")
        r = mc.classify([mc.score_candidate(album(), c, CFG, ocr_text=back) for c in (ds, hh)], CFG)
        self.assertEqual((r["status"], r["best"]["cand"]["id"]), ("auto", "hh"), r["why"])
        agree, _ = mc.pressing_clues(back, ds)
        self.assertNotIn("label", agree)                                  # only in the copyright line
        agree, against = mc.pressing_clues(back, hh)
        self.assertEqual(against, [])                                     # NL address fits a European pressing

    def test_the_labels_address_on_the_back_tells_the_pressing(self):
        """Nothing else tells two pressings apart, but the back prints the label's office:
        'Prophecy Productions, D-54550 Daun' -> the German CD, not the European one."""
        self.assertEqual(mc.countries_on_scans("Prophecy Productions, Kirchstr. 21, D-54550 Daun"), {"DE"})
        self.assertEqual(mc.countries_on_scans("Nuclear Blast GmbH, Hauptstrasse 109, 73072 Donzdorf"), {"DE"})
        self.assertEqual(mc.countries_on_scans("Napalm Records, Hauptplatz 20, 8790 Eisenerz"), {"AT"})
        self.assertEqual(mc.countries_on_scans("Century Media Inc., Hawthorne, CA 90250"), {"US"})
        self.assertEqual(mc.countries_on_scans("PO Box 144, Nottingham NG3 4GE, England"), {"GB"})
        self.assertEqual(mc.countries_on_scans("Shinigami Records CEP 03163-030 Sao Paulo"), {"BR"})
        self.assertEqual(mc.countries_on_scans("Recorded in Montreal, June 1998. AB1 2CD"), set())
        text = "PROPHECY PRODUCTIONS  D-54550 Daun  www.prophecy.de"
        r = mc.classify([mc.score_candidate(album(), c, CFG, ocr_text=text)
                         for c in (cand(id="eu", country="XE", label="Prophecy Productions"),
                                   cand(id="de", country="DE", label="Prophecy Productions"))], CFG)
        self.assertEqual((r["status"], r["best"]["cand"]["id"]), ("auto", "de"), r["why"])

    def test_a_japanese_title_in_the_folder_names_the_japanese_edition(self):
        """Meshuggah - Koloss = 伏魔殿: the disc ID is also on the Russian CD, but the folder's
        Japanese title says the copy is the Japanese edition."""
        a = album(folder="2012 - Koloss = 伏魔殿", toc_releases={"ru"})
        r = self.pick(a, cand(id="ru", country="RU", label="Nuclear Blast"),
                      cand(id="jp", country="JP", label="Avalon"))
        self.assertEqual(r["best"]["cand"]["id"], "jp", r["why"])
        r = self.pick(album(folder="2008 - obZen = オブゼン"), cand(id="us", country="US"), cand(id="jp", country="JP"))
        self.assertEqual((r["status"], r["best"]["cand"]["id"]), ("auto", "jp"), r["why"])
        r = self.pick(album(folder="2008 - First Light"), cand(id="us", country="US"))     # no title: no rule
        self.assertEqual(r["status"], "auto")

    def test_a_one_track_disc_id_proves_nothing(self):
        """The Ocean - The Grand Inquisitor IV (a one-track single): its TOC also fits an
        unrelated one-track CD, which must not be taken with its own title and artist."""
        one = [{"disc": 1, "num": 1, "title": "T0", "secs": 600.0}]
        a = album(title="The Grand Inquisitor IV", artist="The Ocean", tracks=one, toc_releases={"x"})
        c = cand(id="x", title="The Last Musician of Ur", artist="Michael Mauldin",
                 tracks=[{"disc": 1, "title": "Ur", "secs": 600}])
        r = mc.classify([mc.score_candidate(a, c, CFG)], CFG)
        self.assertNotEqual(r["status"], "auto")
        # with 3+ tracks the disc ID still proves the album (freeDB-style)
        a3 = album(title="Wrong Title", toc_releases={"y"})
        r = mc.classify([mc.score_candidate(a3, cand(id="y", title="Real Title"), CFG)], CFG)
        self.assertEqual(r["status"], "auto", r["why"])

    def test_an_ambient_version_is_another_version(self):
        """Slow - IV - Mythologiæ: the release titled '(Ambient)' is a different recording."""
        r = mc.classify([mc.score_candidate(album(folder="2015 - First Light"), cand(title="First Light (Ambient)"),
                                            CFG)], CFG)
        self.assertNotEqual(r["status"], "auto")

    def test_catalogue_numbers_written_differently(self):
        same = mc.catno_same
        self.assertTrue(same("SPV 260222", "SPV 260222 CD"))
        self.assertTrue(same("CG-0004", "DIGICD CG-0004"))
        self.assertTrue(same("99833708", "9983370"))
        self.assertTrue(same("FO40CD", "FO 40 CD"))
        self.assertFalse(same("NB 1234-2", "NB 1234-3"))
        self.assertFalse(same("SOM299", "SOM399"))
        for x, y in (("RR 9117", "RR 9117 2"), ("CD0540", "MAS CD0540"), ("GUN 079", "079"),
                     ("SPV 91978", "SPV 91978 Box"), ("MASS 1389", "MASS CD 1389 DG")):
            self.assertTrue(same(x, y), (x, y))
        self.assertFalse(same("SPV 99807", "SPV 98807 DVD+CD"))           # a different number

    def test_clearly_closest_track_lengths(self):
        r = self.pick(album(), cand(id="far", tracks=[{"disc": 1, "title": f"T{i}", "secs": s}
                                                       for i, s in enumerate((203, 308, 413))]),
                      cand(id="near"))
        self.assertEqual((r["status"], r["best"]["cand"]["id"]), ("auto", "near"), r["why"])

    def test_a_catalogue_number_on_no_pressing_blocks_weak_picks(self):
        """Mental Vortex [Death Cult Switzerland, DCS-004, Germany]: the copy is a pressing
        none of the candidates is, so 'Germany' must not pick the 1991 Noise CD."""
        a = album(folder="2013 - Mental Vortex [Death Cult Switzerland, DCS-004, Germany]",
                  catnos={mc.norm_code("DCS-004")}, catnos_from_folder={mc.norm_code("DCS-004")})
        r = self.pick(a, cand(id="noise", country="DE", label="Noise Records", catnos={"n0177"}),
                      cand(id="us", country="US", label="Noise International", catnos={"n01772"}))
        self.assertEqual(r["status"], "review")
        self.assertTrue(any("DCS4" in w for w in r["why"]), r["why"])
        r = self.pick(dict(a, toc_releases={"re"}), cand(id="noise", country="DE", catnos={"n0177"}),
                      cand(id="re", country="DE", catnos={"n01772"}))
        self.assertEqual((r["status"], r["best"]["cand"]["id"]), ("auto", "re"))   # a disc ID still decides

    def test_tag_and_folder_numbers_on_different_pressings(self):
        """King Diamond 20 Years Ago (ФОНО, FO40CD, Russia): the tags carry Metal Blade's
        3984-14359-2 (on the US and German CDs), the folder FONO's FO40CD. Each pressing
        matches a number, so the folder's label and country decide: FONO."""
        a = album(folder="2001 - 20 Years Ago - A Night Of Rehearsal (ФОНО, FO40CD, Russia)",
                  catnos={mc.norm_code("3984-14359-2"), mc.norm_code("FO40CD")},
                  catnos_from_folder={mc.norm_code("FO40CD")}, tag_label="Metal Blade",
                  toc_releases={"de", "us", "ru"})
        mb = {mc.norm_code("3984-14359-2")}
        r = self.pick(a, cand(id="de", country="DE", label="Metal Blade Records", catnos=mb),
                      cand(id="us", country="US", label="Metal Blade Records", catnos=mb),
                      cand(id="ru", country="RU", label="Фоно", catnos={mc.norm_code("FO40CD")}))
        self.assertEqual((r["status"], r["best"]["cand"]["id"]), ("auto", "ru"), r["why"])
        # a pressing matching no number never wins over those that do
        r = self.pick(album(catnos={"abc123"}), cand(id="a", catnos={"abc123"}, country="DE"),
                      cand(id="b", catnos={"abc123"}, country="US"), cand(id="c", country="CA"))
        self.assertNotEqual(r["best"]["cand"]["id"], "c")

    def test_year_label_and_number_in_brackets(self):
        f = "1989 - Practice What You Preach (2013, Warner, 8122796510-3)"
        self.assertEqual(mc.catno_hints(f), ["8122796510-3"])
        self.assertEqual(mc.pressing_year(f), 2013)
        self.assertIsNone(mc.pressing_year("2010 - Zenith (2010)"))

    def test_download_is_not_a_cd(self):
        a = album(audio={"hires": False, "cd_frames": False, "not_cd": True})
        r = self.pick(a, cand(id="cd", formats={"cd"}), cand(id="web", formats={"digital"}))
        self.assertEqual((r["status"], r["best"]["cand"]["id"]), ("auto", "web"), r["why"])
        infos = [{"lossless": True, "rate": 44100, "bits": 16, "samples": 588 * n + 5} for n in (1000, 2000)]
        self.assertTrue(ma.audio_evidence(infos)["not_cd"])

    def test_shop_and_discogs_pictures_are_not_scans(self):
        """Samael - Ceremony Of Opposites: an Amazon picture of the 2021 MDD reissue
        carried its barcode into a folder ripped from the 1994 CD in 2005."""
        import tempfile, shutil
        from PIL import Image
        d = Path(tempfile.mkdtemp())
        try:
            for n in ("71-Q6hBQwTL._UF1000,1000_QL80_.jpg", "R-4847696-1388801849-4582.jpg",
                      "samael-ceremony_of_opposites_back.jpg"):
                Image.new("RGB", (1200, 900), "white").save(d / n)
            imgs, _key = ma.list_images(d, 80)
            self.assertEqual([Path(p).name for p in imgs], ["samael-ceremony_of_opposites_back.jpg"])
            (d / "samael-ceremony_of_opposites_back.jpg").unlink()
            self.assertFalse(ma.scan_evidence(d)["physical"])        # web pictures prove no copy
        finally:
            shutil.rmtree(d)
        for n in ("Back.jpg", "R-side.jpg", "img108.jpg", "Samael-CeremonyCD2012b.jpg"):
            self.assertFalse(ma.web_image(n), n)

    def test_rip_log_makes_it_a_cd_rip(self):
        """Nile - Amongst the Catacombs: opus files with the EAC log of the CD
        they were ripped from; the CD, not the download."""
        a = album(rip_log_toc=True, audio={"hires": False, "cd_frames": False, "not_cd": False})
        r = self.pick(a, cand(id="cd", formats={"cd"}), cand(id="web", formats={"digital"}))
        self.assertEqual((r["status"], r["best"]["cand"]["id"]), ("auto", "cd"), r["why"])
        r = self.pick(album(media_hint="vinyl", rip_log_toc=True),                # the folder name rules
                      cand(id="cd", formats={"cd"}), cand(id="lp", formats={"vinyl"}))
        self.assertEqual(r["best"]["cand"]["id"], "lp")

    def test_download_shop_in_the_folder_name(self):
        """Slayer - Reign In Blood [ProStudiosMasters] [RM 2015]: a 24/192 download,
        never the 1986 LP (hi-res audio alone could be a vinyl rip)."""
        self.assertEqual(mc.download_store("1986 - Reign In Blood [ProStudiosMasters] [RM 2015]"),
                         "ProStudioMasters")
        self.assertEqual(mc.download_store("2010 - X (Qobuz 24-96)"), "Qobuz")
        self.assertEqual(mc.download_store("2020 - A [WEB]"), "a download shop")
        for f in ("2001 - Tidal Wave", "2020 - Web of Lies", "2019 - A [2019 Reissue Vinyl Rip]"):
            self.assertEqual(mc.download_store(f), "", f)
        a = album(folder="1986 - Reign In Blood [ProStudiosMasters] [RM 2015]", media_hint="digital",
                  audio={"hires": True, "cd_frames": False, "not_cd": False})
        r = self.pick(a, cand(id="lp", formats={"vinyl"}), cand(id="web", formats={"digital"}))
        self.assertEqual((r["status"], r["best"]["cand"]["id"]), ("auto", "web"), r["why"])

    def test_one_pressing_listed_twice(self):
        r = self.pick(album(), cand(id="a", date="2006", catnos={"vr297"}, barcodes={"746105029729"}),
                      cand(id="b", date="2006-06-13", catnos={"vr297"}, barcodes={"746105029729"}))
        self.assertEqual((r["status"], r["best"]["cand"]["id"]), ("auto", "b"), r["why"])
        r = self.pick(album(), cand(id="a", catnos={"cm77412"}, barcodes=set()),
                      cand(id="b", date="1992-06-01", catnos={"cm77412"}, barcodes={"727701774121"}))
        self.assertEqual((r["status"], r["best"]["cand"]["id"]), ("auto", "b"), r["why"])   # one lacks a barcode
        r = self.pick(album(), cand(id="a", catnos={"nb4664"}, barcodes={"727361466411"}, formats={"vinyl"}),
                      cand(id="b", catnos={"nb4664"}, barcodes={"727361466428"}, formats={"vinyl"}))
        self.assertEqual(r["status"], "review")              # two vinyl colours: different barcodes
        r = self.pick(album(), cand(id="a", catnos={"cm775422"}, country="DE"),
                      cand(id="b", catnos={"cmr82422"}, country="US"))
        self.assertEqual(r["status"], "review")

    def test_lengths_excused_when_a_code_or_vinyl_points_to_the_release(self):
        odd = [{"disc": 1, "title": f"T{i}", "secs": s} for i, s in enumerate((230, 340, 380))]
        r = self.pick(album(), cand(tracks=odd))
        self.assertEqual(r["status"], "review")
        r = self.pick(album(catnos={"nb12342"}), cand(tracks=odd, catnos={"nb12342"}))
        self.assertEqual(r["status"], "auto", r["why"])
        self.assertIn("track lengths not compared", " ".join(r["best"]["notes"]))
        a = album(media_hint="vinyl", folder="2008 - X [Vinyl Rip 24bit 96khz]")
        r = self.pick(a, cand(tracks=odd, formats={"vinyl"}))
        self.assertEqual(r["status"], "auto", r["why"])
        r = self.pick(album(catnos={"nb12342"}), cand(tracks=odd[:2], catnos={"nb12342"}))
        self.assertEqual(r["status"], "review")                    # track count differs: left as is

    def test_rip_log_gives_the_disc_toc(self):
        import tempfile
        d = Path(tempfile.mkdtemp())
        log = ("Exact Audio Copy V1.0 beta 6\r\n\r\nTOC of the extracted CD\r\n\r\n"
               "     Track |   Start  |  Length  | Start sector | End sector \r\n"
               "    ---------------------------------------------------------\r\n"
               "        1  |  0:00.00 |  1:45.61 |         0    |     7935   \r\n"
               "        2  |  1:45.61 |  5:21.11 |      7936    |    32021   \r\n"
               "\r\nRange status and errors\r\n        1  |  0:00.00 |  1:45.61 |         0    |     7935   \r\n")
        (d / "rip.log").write_bytes(b"\xff\xfe" + log.encode("utf-16-le"))
        self.assertEqual(ma.log_tocs(d), {1: "1 2 32172 150 8086"})

    def test_catalogue_number_with_format_after_it(self):
        self.assertIn("FO532CD", mc.codes_from_ocr_text("CRYPTOPSY  FO532CD")["catnos"])

    def test_spine_misreads_and_licensors(self):
        """Whisper Supremacy: the spine's FO501CD is read 'FOSO1 cp'; 'licensed from
        Century Media' names the licensor, not the FONO pressing's label."""
        self.assertIn("FO501CD", mc.codes_from_ocr_text("CRYPTOPSY  FOSO1 cp")["catnos"])
        self.assertEqual(mc.codes_from_ocr_text("FO532CD")["catnos"], ["FO532CD"])
        text = "All recordings are licensed from Century Media Records. FONO Ltd  FOSO1 cp"
        r = self.pick(album(), cand(id="cm", label="Century Media", country="DE"),
                      cand(id="fono", label="Fono", country="RU", catnos={"fo501cd"}))
        scored = [mc.score_candidate(album(), c, CFG, ocr_text=text)
                  for c in (cand(id="cm", label="Century Media", country="DE"),
                            cand(id="fono", label="Fono", country="RU", catnos={"fo501cd"}))]
        r = mc.classify(scored, CFG)
        self.assertEqual((r["status"], r["best"]["cand"]["id"]), ("auto", "fono"), r["why"])
        self.assertNotIn("label", scored[0]["clues"])

    def test_catalogue_number_ending_in_cd_is_not_a_media_note(self):
        f = "2001 - 20 Years Ago - A Night Of Rehearsal (ФОНО, FO40CD, Russia)"
        self.assertEqual(mc.catno_hints(f), ["FO40CD"])
        self.assertEqual(mc.media_hint(f), "")
        self.assertEqual(mc.media_hint("2000 - X [2CD]"), "cd")
        self.assertEqual(mc.media_hint("2014 - Y [SPV 260222 CD]"), "cd")

    def test_country_preference_is_the_last_resort(self):
        cs = [cand(id="de", country="DE"), cand(id="us", country="US"), cand(id="jp", country="JP")]
        r = mc.classify([mc.score_candidate(album(), c, CFG_PREF) for c in cs], CFG_PREF)
        self.assertEqual((r["status"], r["best"]["cand"]["id"]), ("auto", "us"), r["why"])
        self.assertIn("preferred country order", " ".join(r["best"]["notes"]))
        # two US pressings: still undecided
        cs = [cand(id="us1", country="US", catnos={"a1"}), cand(id="us2", country="US", catnos={"a2"})]
        r = mc.classify([mc.score_candidate(album(), c, CFG_PREF) for c in cs], CFG_PREF)
        self.assertEqual(r["status"], "review")
        # evidence beats preference: the folder says Germany
        a = album(folder="2009 - X [Nuclear Blast, Germany]")
        cs = [cand(id="de", country="DE"), cand(id="us", country="US")]
        r = mc.classify([mc.score_candidate(a, c, CFG_PREF) for c in cs], CFG_PREF)
        self.assertEqual(r["best"]["cand"]["id"], "de")

    def test_scans_mean_a_physical_copy(self):
        a = album(scans={"physical": True, "why": "a scan named back.jpg"})
        r = self.pick(a, cand(id="web", country="XW", formats={"digital"}), cand(id="cd", country="AU", formats={"cd"}))
        self.assertEqual((r["status"], r["best"]["cand"]["id"]), ("auto", "cd"), r["why"])
        # but not when the audio says download
        a = dict(a, audio={"hires": True})
        r = self.pick(a, cand(id="web", formats={"digital"}), cand(id="cd", formats={"cd"}))
        self.assertEqual(r["best"]["cand"]["id"], "web")

    def test_scan_evidence_from_names_and_tray_shape(self):
        import tempfile
        from PIL import Image
        d = Path(tempfile.mkdtemp())
        Image.new("RGB", (600, 600)).save(d / "cover.jpg")
        (d / "tech.info").mkdir()
        Image.new("RGB", (1300, 1000)).save(d / "tech.info" / "Au.jpg")
        self.assertFalse(ma.scan_evidence(d)["physical"])
        Image.new("RGB", (1500, 1180)).save(d / "07.jpg")
        self.assertTrue(ma.scan_evidence(d)["physical"])
        (d / "07.jpg").unlink()
        Image.new("RGB", (800, 800)).save(d / "Back.jpg")
        self.assertIn("Back.jpg", ma.scan_evidence(d)["why"])

    def test_latin_script_listing_preferred(self):
        r = self.pick(album(), cand(id="jp", script="Jpan"), cand(id="lat", script="Latn"))
        self.assertEqual((r["status"], r["best"]["cand"]["id"]), ("auto", "lat"), r["why"])
        self.assertEqual(mc.fold("Фоно"), "fono")
        self.assertEqual(mc.fold("Κατά τον Δαίμονα Εαυτού"), "kata ton daimona eaytoy")

    def test_disc_ids_and_ctdb_toc(self):
        self.assertEqual(ma.mb_discid("1 12 267257 150 22767 41887 58317 72102 91375 104652 115380 "
                                      "132165 143932 159870 174597"), "I5l9cCSFccLKFEKS.7wqSZAorPU-")
        self.assertEqual(ma.ctdb_toc("1 2 32172 150 8086"), "0:7936:32022")

    def test_cue_sheet_toc(self):
        import tempfile
        d = Path(tempfile.mkdtemp())
        (d / "album.cue").write_text('FILE "album.flac" WAVE\n  TRACK 01 AUDIO\n    INDEX 01 00:00:00\n'
                                    '  TRACK 02 AUDIO\n    INDEX 01 01:45:61\n')
        infos = [(d / "album.flac", {"secs": 427.0})]
        self.assertEqual(ma.cue_tocs(d, infos), {1: "1 2 32175 150 8086"})

    def test_fingerprints_find_the_releases_common_to_all_tracks(self):
        class H:
            def get_json(self, url, host):
                fp = re.search(r"fingerprint=(\w+)", url).group(1)
                rels = {"fa": ["r1", "r2"], "fb": ["r2", "r3"], "fc": ["r2"]}[fp]
                return {"results": [{"score": 0.95, "recordings": [{"releases": [{"id": r} for r in rels]}]}]}
        tracks = [{"path": f"/m/{i}.flac", "secs": 100} for i in range(5)]
        orig = (ma.fingerprint, ma.acoustid_key, ma.shutil.which)
        fps = {"/m/0.flac": "fa", "/m/2.flac": "fb", "/m/4.flac": "fc"}
        ma.fingerprint = lambda p, db=None: {"duration": 100, "fingerprint": fps[p]}
        ma.acoustid_key = lambda: "k"
        ma.shutil.which = lambda name: "/usr/bin/fpcalc"
        try:
            self.assertEqual(ma.acoustid_releases(H(), {"tracks": tracks}), {"r2"})
        finally:
            ma.fingerprint, ma.acoustid_key, ma.shutil.which = orig
        a = album(title="Far Beyond Live", fp_releases={"x1"})
        r = self.pick(a, cand(title="Nosatsu Live"))
        self.assertEqual(r["status"], "auto", r["why"])
        self.assertIn("audio fingerprint", " ".join(r["best"]["notes"]))

    def test_links_and_isrcs_left_in_the_tags(self):
        infos = [{"clue_text": "Visit https://mournfulcongregation.bandcamp.com/album/the-book-of-kings for more"},
                 {"clue_text": "itunes:plid:1680497923 https://www.discogs.com/release/1441661-Cryptopsy"},
                 {"clue_text": "https://musicbrainz.org/release/6118a8cb-da37-4f82-bd1e-6087e0ac7d3b"}]
        mb, dg, urls = ma.source_links(infos)
        self.assertEqual(mb, {"6118a8cb-da37-4f82-bd1e-6087e0ac7d3b"})
        self.assertEqual(dg, {"1441661"})
        self.assertIn("https://mournfulcongregation.bandcamp.com/album/the-book-of-kings", urls)
        self.assertIn("https://music.apple.com/album/1680497923", urls)

        class H:
            def get_json(self, url, host):
                if "/ws/2/url" in url:
                    return {"relations": [{"release": {"id": "bc-rel"}}]}
                if "/ws/2/isrc/" in url:
                    return {"recordings": [{"releases": [{"id": "x1"}, {"id": "x2"}]}]}
                return {}
        a = {"tag_urls": urls[:1], "isrcs": ["USRC17607839"]}
        self.assertEqual(ma.link_releases(H(), a), {"bc-rel", "x1", "x2"})
        r = self.pick(album(link_releases={"b"}), cand(id="a"), cand(id="b"))
        self.assertEqual((r["status"], r["best"]["cand"]["id"]), ("auto", "b"), r["why"])

    def test_track_numbers_and_titles_from_file_names(self):
        infos = [(Path("/m/01 - Cryptopsy - Luminum.flac"), {}), (Path("/m/02 - Cryptopsy - Carrionshine.flac"), {})]
        ma.names_from_files(infos)
        self.assertEqual([(i["num"], i["title"]) for _p, i in infos], [(1, "Luminum"), (2, "Carrionshine")])
        vinyl = [(Path("/m/A1 Intro.flac"), {}), (Path("/m/A2 Second.flac"), {}), (Path("/m/B1 Third.flac"), {})]
        ma.names_from_files(vinyl)
        self.assertEqual([i["num"] for _p, i in vinyl], [1, 2, 3])
        own = [(Path("/m/001.L.opus"), {"title": "Luminum", "num": 1}), (Path("/m/002.ITKWEDTSIM.opus"), {})]
        ma.names_from_files(own)
        self.assertEqual(own[0][1]["title"], "Luminum")             # tags win
        self.assertEqual((own[1][1]["num"], own[1][1].get("title", "")), (2, ""))   # initials are not a title

    def test_rules_learned_from_how_you_file(self):
        import learn_rules
        rows = [{"Status": "musicbrainz_only", "AlbumArtistFolder": "Death", "TagAlbumArtist": "Control Denied"},
                {"Status": "musicbrainz_only", "AlbumArtistFolder": "Meshuggah",
                 "TagAlbumArtist": "Fredrik Thordendal’s Special Defects"},
                {"Status": "tagged_both", "AlbumArtistFolder": "Sunn O)))", "TagAlbumArtist": "Sunn O))) & Boris"},
                {"Status": "untagged", "AlbumArtistFolder": "X", "TagAlbumArtist": "Y"}]
        rules = learn_rules.learn(rows, {"artist_aliases": {"Slayer": ["Various Artists"]}})
        self.assertEqual(rules["artist_aliases"]["Death"], ["Control Denied"])
        self.assertIn("Meshuggah", rules["artist_aliases"])
        self.assertNotIn("Sunn O)))", rules["artist_aliases"])          # a collaboration, not another artist
        self.assertEqual(rules["artist_aliases"]["Slayer"], ["Various Artists"])   # your own entries stay
        cfg = dict(CFG, artist_aliases=rules["artist_aliases"])
        a = album(artist="Death", artist_folder="Death", title="The Fragile Art of Existence")
        r = mc.classify([mc.score_candidate(a, cand(artist="Control Denied", title="The Fragile Art of Existence"),
                                           cfg)], cfg)
        self.assertEqual(r["status"], "auto", r["why"])
        self.assertIn("you file Control Denied under Death", " ".join(r["best"]["notes"]))

    def test_nothing_telling_stays_in_review(self):
        r = self.pick(album(audio={"hires": False, "cd_frames": True}),
                      cand(id="a", formats={"cd"}), cand(id="b", formats={"cd"}))
        self.assertEqual(r["status"], "review")

    def test_cd_frames_and_disc_toc(self):
        infos = [{"lossless": True, "rate": 44100, "bits": 16, "samples": 588 * n} for n in (1000, 2000)]
        self.assertTrue(ma.audio_evidence(infos)["cd_frames"])
        self.assertFalse(ma.audio_evidence(infos + [{"lossless": True, "rate": 44100, "bits": 16,
                                                      "samples": 588 * 5 + 7}])["cd_frames"])
        self.assertTrue(ma.audio_evidence([{"lossless": True, "rate": 96000, "bits": 24, "samples": 1}])["hires"])
        tocs = ma.disc_tocs([(None, dict(i, disc_eff=1)) for i in infos])
        self.assertEqual(tocs, {1: "1 2 3150 150 1150"})

    def test_proven_album_keeps_database_title_and_artist(self):
        a = album(artist="Death", title="The Fragile Art Of Existence", catnos={mc.norm_code("RR7119")},
                  catnos_from_folder={mc.norm_code("RR7119")})
        r = self.pick(a, cand(artist="Control Denied", title="The Fragile Art of Existence",
                              catnos={mc.norm_code("RR 7119")}))
        self.assertEqual(r["status"], "auto", r["why"])
        self.assertIn("credited as the database does", " ".join(r["best"]["notes"]))
        a = album(title="The Mediator", toc_releases={"x1"})
        r = self.pick(a, cand(title="The Mediator Between Head and Hands Must Be the Heart"))
        self.assertEqual(r["status"], "auto", r["why"])
        self.assertIn("database title is used", " ".join(r["best"]["notes"]))

    def test_scans_naming_the_credited_artist_prove_the_credit(self):
        """Aghora's album is credited to its arranger, Gustavo Dobles, as the sleeve says."""
        a = album(artist="Aghora", title="Aghora Entheogenic Frequencies Orchestral Representations")
        c = cand(artist="Gustavo Dobles", title="Aghora Entheogenic Frequencies Orchestral Representations")
        scan = "For Orchestra and Chorus  Arrangements and Orchestrations by Gustavo Dobles"
        r = mc.classify([mc.score_candidate(a, c, CFG, ocr_text=scan)], CFG)
        self.assertEqual(r["status"], "auto", r["why"])
        self.assertIn("credited as the database does", " ".join(r["best"]["notes"]))
        r = mc.classify([mc.score_candidate(a, c, CFG, ocr_text="Aghora")], CFG)
        self.assertEqual(r["status"], "review")

    def test_unproven_different_title_still_needs_a_person(self):
        r = self.pick(album(title="The Mediator"), cand(title="The Mediator Between Head and Hands Must Be the Heart"))
        self.assertEqual(r["status"], "review")

    def test_proof_never_drops_a_collaborator(self):
        a = album(artist="Steve Brown; Blaine Reininger", toc_releases={"x1"})
        r = self.pick(a, cand(artist="Blaine Reininger"))
        self.assertEqual(r["status"], "review")


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

    def test_discogs_is_asked_even_when_musicbrainz_is_sure(self):
        """Your pressing may be listed only on Discogs (Kreator Endorama, AMG 099)."""
        calls = [u for h, u in self.http.calls if h == "discogs" and "First" in u]
        self.assertTrue(calls)
        self.assertEqual(self.out["2010 - First Light"]["best"]["source"], "mb")

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
        self.assertEqual(counts, {"auto": 2, "review": 3, "nomatch": 1, "moved": 0, "excluded": 0})
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

    def test_box_set_blu_ray_and_vinyl_single_are_not_in_the_files(self):
        """Kreator - Dying Alive (NB 3053-5): 3 CDs ripped; the box's Blu-ray ("BD-1"...) is
        video and its 7" (sides A, B) was not ripped, so neither counts against the files."""
        cds = [{"position": f"CD{d}-{i}", "title": f"c{d}{i}", "duration": "3:00"}
               for d, n in ((1, 3), (2, 2)) for i in range(1, n + 1)]
        rel = {"id": 10, "title": "Dying Alive", "artists": [{"name": "Kreator"}],
               "labels": [{"name": "Nuclear Blast", "catno": "NB 3053-5"}],
               "formats": [{"name": "Blu-ray", "qty": "1"}, {"name": "CD", "qty": "2"},
                           {"name": "Vinyl", "qty": "1", "descriptions": ['7"']}],
               "tracklist": [{"position": f"BD-{i}", "title": f"v{i}", "duration": "4:00"} for i in range(1, 4)]
               + cds + [{"position": "A", "title": "s1", "duration": "3:00"},
                        {"position": "B", "title": "s2", "duration": "3:00"}]}
        c = ma.discogs_candidate(rel, self.dm)
        self.assertEqual((len(c["tracks"]), sum(1 for t in c["tracks"] if t.get("box_vinyl"))), (7, 2))
        files = album(artist="Kreator", title="Dying Alive",
                      tracks=[{"disc": t["disc"], "num": i + 1, "title": t["title"], "secs": 180.0}
                              for i, t in enumerate(c["tracks"][:5])])
        s = mc.score_candidate(files, c, CFG)
        self.assertTrue(s["tracks_ok"], s["notes"])
        mbrel = {"id": "r", "title": "Dying Alive", "artist-credit": [{"name": "Kreator"}],
                 "media": [{"format": "CD", "tracks": [{"title": "a", "length": 180000}]},
                           {"format": '7" Vinyl', "tracks": [{"title": "s", "length": 180000}]}]}
        self.assertEqual([t.get("box_vinyl", False) for t in ma.mb_candidate(mbrel)["tracks"]], [False, True])
        # the mailorder box of Kreator - Gods Of Violence: LPs, a 10" and a tape written "LP-A1",
        # '10"-A1', "MC-B2" next to the CDs
        box = dict(rel, formats=rel["formats"] + [{"name": "Cassette", "qty": "1"}],
                   tracklist=cds + [{"position": p_, "title": p_, "duration": "3:00"}
                                    for p_ in ("LP-A1", "LP-B2", '10"-A1', "MC-B2")])
        c = ma.discogs_candidate(box, self.dm)
        self.assertEqual([t.get("box_vinyl", False) for t in c["tracks"]], [False] * 5 + [True] * 4)
        # a vinyl-only release keeps every side
        lp = dict(rel, formats=[{"name": "Vinyl", "qty": "2"}], tracklist=rel["tracklist"][-2:])
        self.assertFalse(any(t.get("box_vinyl") for t in ma.discogs_candidate(lp, self.dm)["tracks"]))

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
        # ...and (John's rule) when the catalogue number points to the release, even lengths
        # that mostly disagree do not block it: it is that release, ripped differently
        bad = [lens[0]] + [s + 90 for s in lens[1:]]
        c3 = cand(catnos={"abc123"}, tracks=[{"disc": 1, "title": "t", "secs": float(s)} for s in bad])
        r = mc.classify([mc.score_candidate(a, c3, CFG)], CFG)
        self.assertEqual(r["status"], "auto", r["why"])
        self.assertIn("track lengths not compared", " ".join(r["best"]["notes"]))

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


class UnreachableWebSearch(unittest.TestCase):
    """Pass 4 of validate: DuckDuckGo timed out, and each query was retried six times with
    a 60 s timeout, holding every investigated album up for many minutes. The web search
    only adds evidence: asked once, briefly, and left alone for a while after failing."""

    def test_the_web_search_fails_fast_and_is_then_left_alone(self):
        import urllib.error
        calls = []

        def boom(req, timeout=None):
            calls.append(timeout)
            raise urllib.error.URLError("timed out")
        saved = ma.urllib.request.urlopen
        ma.urllib.request.urlopen = boom
        try:
            h = ma.Http(sqlite3.connect(":memory:"), {"mb_delay": 0, "discogs_delay": 0}, "")
            for _ in range(4):
                with self.assertRaises(ma.ProviderUnavailable):
                    h.get_text("https://html.duckduckgo.com/html/?q=x", "duckduckgo")
            self.assertEqual(calls, [15, 15, 15])          # once each, short; the 4th is not even asked
            self.assertEqual(ma.web_release_links(h, ["QDISS0201CDD"], "Exhorder"), ([], []))
            self.assertEqual(len(calls), 3)
        finally:
            ma.urllib.request.urlopen = saved

class WebSearchEngines(unittest.TestCase):
    """John's choice (2026-10-24): Tavily first, DuckDuckGo only when Tavily is not
    set up or fails (SearXNG dropped at his request)."""

    def setUp(self):
        self.env = {k: _os.environ.pop(k, None) for k in ("TAGMENDER_SEARXNG_URL", "TAVILY_API_KEY")}
        self.saved = ma.urllib.request.urlopen
        self.asked = []

    def tearDown(self):
        ma.urllib.request.urlopen = self.saved
        for k, v in self.env.items():
            if v is not None:
                _os.environ[k] = v
            else:
                _os.environ.pop(k, None)

    def serve(self, searx_ok=True, tavily_ok=True):
        import io
        import urllib.error
        link = "https://www.discogs.com/release/22314946-Exhorder-Slaughter-In-The-Vatican-The-Law"

        def fake(req, timeout=None):
            url = req.full_url if hasattr(req, "full_url") else req
            self.asked.append((url, dict(req.header_items()) if hasattr(req, "header_items") else {},
                               getattr(req, "data", None)))
            if "searx.local" in url:
                if not searx_ok:
                    raise urllib.error.URLError("refused")
                body = {"results": [{"url": link, "title": "Exhorder"}]}
            elif "api.tavily.com" in url:
                if not tavily_ok:
                    raise urllib.error.URLError("refused")
                body = {"results": [{"url": link, "content": "QDISS0201CDD"}]}
            else:
                body = None
            return io.BytesIO(json.dumps(body).encode() if body else b"<html>nothing</html>")
        ma.urllib.request.urlopen = fake
        return ma.Http(sqlite3.connect(":memory:"), {"mb_delay": 0, "discogs_delay": 0}, "")

    def test_tavily_first_and_the_key_is_never_stored(self):
        _os.environ["TAGMENDER_SEARXNG_URL"] = "http://searx.local:8080/"
        _os.environ["TAVILY_API_KEY"] = "https://mcp.tavily.com/mcp/?tavilyApiKey=tvly-secret"   # pasted whole
        h = self.serve()
        self.assertEqual(ma.web_release_links(h, ["QDISS0201CDD"], "Exhorder"), (["22314946"], []))
        self.assertTrue(all("api.tavily.com" in u for u, _h, _d in self.asked))
        tav = self.asked[0]
        self.assertEqual(tav[1].get("Authorization"), "Bearer tvly-secret")
        self.assertNotIn(b"tvly-secret", tav[2])
        stored = " ".join(u + b for u, b in h.db.execute("SELECT url, body FROM http_cache"))
        self.assertNotIn("tvly-secret", stored)

    def test_duckduckgo_when_tavily_fails_and_searxng_is_never_asked(self):
        _os.environ["TAGMENDER_SEARXNG_URL"] = "http://searx.local:8080"     # left over: ignored
        _os.environ["TAVILY_API_KEY"] = "tvly-secret"
        h = self.serve(tavily_ok=False)
        self.assertEqual(ma.web_search(h, '"QDISS0201CDD" Exhorder')[1], "duckduckgo")
        self.assertFalse(any("searx" in u for u, _h, _d in self.asked))

    def test_duckduckgo_only_without_either(self):
        h = self.serve()
        self.assertEqual(ma.web_search(h, "x")[1], "duckduckgo")
        self.assertTrue(all("duckduckgo" in u for u, _h, _d in self.asked))

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
        o = self.run_row("2010 - First Light", RuntimeError("timeout"))
        self.assertEqual(o["status"], "auto")
        self.assertTrue(any("Discogs lookup failed" in w for w in o["why"]))
        with self.assertRaises(ma.AuthError):                          # a bad token stops the run
            self.run_row("2010 - First Light", ma.AuthError("401"))

    def test_transient_discogs_failure_keeps_musicbrainz_review(self):
        o = self.run_row("2005 - Twin Pressing", RuntimeError("timeout"))
        self.assertEqual(o["status"], "review")
        self.assertTrue(any("Discogs lookup failed" in w for w in o["why"]))

    def test_nothing_from_mb_plus_discogs_failure_is_error_not_nomatch(self):
        o = self.run_row("2012 - Slow Drift", RuntimeError("timeout"))
        self.assertEqual(o["status"], "error")


if __name__ == "__main__":
    unittest.main(argv=[sys.argv[0]], verbosity=2)
