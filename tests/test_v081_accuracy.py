# Tests for 0.8.1's identification accuracy fixes (no network; fakes and synthetic albums).
# Evidence from the copy itself (exact disc ID, a barcode read off it, a fitting cue's
# CATALOG, the rip log) outranks the folder name; what another tagger wrote does not:
# - 1: a MusicBrainz/Discogs release link in the tags is a candidate, never "source link";
# - 2: the unsure database's pick carrying your copy's proof disputes the other's sure pick;
# - 3: another tagger's label, country, date and media tags break no tie;
# - 4: the script a pressing is listed in tells only when every tied pressing lists one;
# - 5: a cue sheet that fits none of the files' discs is typed text, not print;
# - 6: a hidden track before track 1 (a file numbered 0) no longer makes the rip's own log
#      foreign, and is compared with MusicBrainz's pregap track;
# - 7: Russian label lines are read as search leads.
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import test_engine as te  # noqa: E402  (its settings and state dirs are temporary)
import match_albums as ma  # noqa: E402
import match_core as mc  # noqa: E402
import mb_tags  # noqa: E402

CFG = dict(mc.DEFAULT_CFG, preferred_countries=[])
EU_BARCODE = "4006759000014"
JP_BARCODE = "4988002000000"


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


def pick(a, *cands, ocr="", cfg=CFG):
    return mc.classify([mc.score_candidate(a, c, cfg, ocr) for c in cands], cfg)


def result(a, *cands, ocr=""):
    scored = [mc.score_candidate(a, c, CFG, ocr) for c in cands]
    r = mc.classify(scored, CFG)
    r["scored"] = scored
    return r


def outcome(r):
    return r["status"], r["best"]["cand"]["id"]


# ---- 1: release links in the tags ---------------------------------------------------------
MB_LINKED = "6118a8cb-da37-4f82-bd1e-6087e0ac7d3b"


class TagReleaseLinks(unittest.TestCase):
    def test_a_discogs_link_by_another_tagger_is_no_proof(self):
        # (the auditor's p3b: Mp3tag's DISCOGS_RELEASE_ID and URL picked 111 automatically)
        rel = (cand(source="discogs", id="111", country="Europe"), cand(source="discogs", id="222", country="Japan"))
        a = album(tag_discogs_links={"111"}, tag_release_discogs={"111"}, tags_by_tagger=True)
        r = pick(a, *rel)
        self.assertEqual(r["status"], "review", r["why"])
        self.assertNotIn("source link", r["best"].get("clues") or {})

    def test_a_discogs_link_does_not_excuse_track_lengths(self):
        # (p3c: lengths 60 s off, "track lengths not compared: your files' own tags link")
        tr = [{"disc": 1, "num": i + 1, "title": f"T{i}", "secs": s} for i, s in enumerate((260.0, 365.0, 470.0))]
        r = pick(album(tracks=tr, tag_discogs_links={"111"}, tag_release_discogs={"111"}, tags_by_tagger=True),
                 cand(source="discogs", id="111", country="Europe"))
        self.assertEqual(r["status"], "review", r["why"])
        self.assertFalse(any("track lengths not compared" in n for n in r["best"]["notes"]), r["best"]["notes"])

    def test_the_tags_musicbrainz_link_is_no_longer_a_link_release(self):
        class H:
            def get_json(self, url, host):
                raise AssertionError("no lookup is needed for a release link")
        a = {"tag_mb_links": {MB_LINKED}, "tag_urls": [], "isrcs": []}
        self.assertEqual(ma.link_releases(H(), a), set())
        self.assertEqual(a["link_releases"], set())

    def test_a_shop_page_link_still_proves(self):
        # (test_links_and_isrcs_left_in_the_tags: the Bandcamp page MusicBrainz ties to a release)
        class H:
            def get_json(self, url, host):
                return {"relations": [{"release": {"id": "b"}}]} if "/ws/2/url" in url else {}
        a = {"tag_urls": ["https://x.bandcamp.com/album/y"], "isrcs": []}
        ma.link_releases(H(), a)
        r = pick(album(link_releases=a["link_releases"]), cand(id="a"), cand(id="b"))
        self.assertEqual(outcome(r), ("auto", "b"), r["why"])


class TagReleaseLinksRead(te.Base):
    def test_links_are_candidates_and_mark_the_tags_another_taggers(self):
        d = self.album("Band K", "Album K", comment=f"https://musicbrainz.org/release/{MB_LINKED} "
                                                    "https://www.discogs.com/release/1441661-Band-K")
        a = ma.read_album(d, "Band K", "2005 - Album K")
        self.assertIn(MB_LINKED, a["tag_release_mb"])
        self.assertIn("1441661", a["tag_release_discogs"])
        self.assertTrue(a["tags_by_tagger"])
        blind = ma.read_album(d, "Band K", "2005 - Album K", blind=True)
        self.assertFalse(blind["tags_by_tagger"])
        self.assertEqual((blind["tag_release_mb"], blind["tag_release_discogs"]), (set(), set()))


# ---- 2: combine and the unsure database's proof -------------------------------------------
class UnsureProof(unittest.TestCase):
    def test_a_barcode_on_the_unsure_discogs_pick_disputes_musicbrainz(self):
        # (p9: MusicBrainz sure of the EU CD on nothing; Discogs's two JP entries carry the barcode)
        a = album(barcodes={JP_BARCODE})
        mb = result(a, cand(id="eu", country="XE", label="Century Media"))
        dg = result(a, cand(source="discogs", id="1", country="Japan", label="Victor", barcodes={JP_BARCODE},
                            catnos={"vicp1"}),
                    cand(source="discogs", id="2", country="Japan", label="Victor", barcodes={JP_BARCODE},
                         catnos={"vicp1"}, notes="obi variant"))
        # (0.8.21: Discogs's two entries are one pressing listed twice - sure of it; BAR-1: the
        # barcode on it beats MusicBrainz's pick, which has nothing of your copy behind it)
        self.assertEqual((mb["status"], dg["status"]), ("auto", "auto"))
        f = mc.combine(mb, dg)
        self.assertEqual((f["status"], f["best"]["cand"]["source"], f["best"]["cand"]["id"]),
                         ("auto", "discogs", "1"), f["why"])

    def test_an_exact_disc_id_on_the_unsure_musicbrainz_pick_disputes_discogs(self):
        # (p8: two JP releases carry the exact disc ID; Discogs sure of the EU CD on nothing)
        a = album(toc_releases={"jp1", "jp2"}, exact_toc_releases={"jp1", "jp2"})
        mb = result(a, cand(id="jp1", country="JP", label="Victor", catnos={"vicp1"}),
                    cand(id="jp2", country="JP", label="Victor", catnos={"vicp2"}))
        dg = result(a, cand(source="discogs", id="555", country="Europe", label="Century Media", catnos={"cm1"}))
        self.assertEqual((mb["status"], dg["status"]), ("review", "auto"))
        f = mc.combine(mb, dg)
        self.assertEqual(f["status"], "review", f["why"])
        self.assertIn("exact disc ID", " ".join(f["why"]))

    def test_one_pressing_on_both_sides_is_no_dispute(self):
        # the unsure Discogs pick is the sure MusicBrainz pick's pressing (same barcode)
        a = album(barcodes={EU_BARCODE})
        mb = result(a, cand(id="eu", country="XE", label="Century Media", barcodes={EU_BARCODE}))
        dg = result(a, cand(source="discogs", id="1", country="Europe", label="Century Media", barcodes={EU_BARCODE}),
                    cand(source="discogs", id="2", country="Europe", label="Century Media", barcodes={EU_BARCODE}))
        self.assertEqual(mc.combine(mb, dg)["status"], "auto")


# ---- 3: another tagger's pressing tags -----------------------------------------------------
class TaggerPressingTags(unittest.TestCase):
    REL = (cand(id="de", country="DE", date="2010-02-01", label="Alpha"),
           cand(id="gb", country="GB", date="2010-03-01", label="Beta"))

    def test_another_taggers_tags_break_no_tie(self):
        for kw in (dict(tag_country="GB"), dict(tag_date="2010-03-01"), dict(tag_label="Beta"),
                   dict(tag_media="cd")):
            r = pick(album(tag_release_mb={"gb"}, tags_by_tagger=True, **kw), *self.REL)
            # TAG-2: ignored; TIE-1/TIE-2: the tie goes to the original pressing, tagged in full
            self.assertEqual(outcome(r), ("auto", "de"), (kw, r["why"]))

    def test_the_copys_own_tags_still_do(self):
        for kw in (dict(tag_country="GB"), dict(tag_date="2010-03-01"), dict(tag_label="Beta")):
            r = pick(album(tag_release_mb={"gb"}, tags_by_tagger=False, **kw), *self.REL)
            self.assertEqual(outcome(r), ("auto", "gb"), (kw, r["why"]))
            self.assertEqual(outcome(pick(album(**kw), *self.REL)), ("auto", "gb"))


# ---- 4: the script field -------------------------------------------------------------------
class ScriptField(unittest.TestCase):
    def test_a_blank_script_is_unknown_not_another(self):
        r = pick(album(), cand(id="eu", country="XE", script="Latn"), cand(id="us", country="US", script=""))
        self.assertEqual(r["status"], "review", r["why"])
        r = pick(album(), cand(id="eu", country="XE", script="Latn"), cand(id="us", country="US"))
        self.assertEqual(r["status"], "review", r["why"])

    def test_latin_against_japanese_still_tells(self):
        r = pick(album(), cand(id="eu", country="XE", script="Latn"), cand(id="jp", country="JP", script="Jpan"))
        self.assertEqual(outcome(r), ("auto", "eu"), r["why"])


# ---- 5: a cue sheet of another edition -----------------------------------------------------
CUE = """REM DATE 2010
CATALOG 4006759000014
PERFORMER "Artist Alpha"
TITLE "First Light"
FILE "Artist Alpha - First Light.wav" WAVE
  TRACK 01 AUDIO
    INDEX 01 00:00:00
  TRACK 02 AUDIO
    INDEX 01 05:00:00
  TRACK 03 AUDIO
    INDEX 01 10:00:00
  TRACK 04 AUDIO
    INDEX 01 15:00:00
"""


class ForeignCue(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp())
        (self.d / "Other Edition.cue").write_text(CUE)

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def infos(self, lengths):
        return [(self.d / f"{k + 1:02d}.flac", {"secs": s, "disc_eff": 1, "num": k + 1})
                for k, s in enumerate(lengths)]

    def test_a_cue_that_fits_no_disc_is_typed(self):
        infos = self.infos((200.0, 305.0, 410.0))
        foreign = ma.foreign_cues(self.d, infos)
        self.assertEqual(foreign, ["Other Edition.cue"])
        txt = ma.documents_text(self.d, ocr=False)
        typed, printed = ma.split_documents(txt, foreign)
        self.assertNotIn(EU_BARCODE, printed)
        self.assertIn(EU_BARCODE, typed)
        a = album(folder="2010 - First Light [Japan]", typed_text=typed, printed_text=printed)
        r = pick(a, cand(id="eu", country="XE", barcodes={EU_BARCODE}), cand(id="jp", country="JP"), ocr=txt)
        self.assertNotEqual(outcome(r), ("auto", "eu"), r["why"])

    def test_a_fitting_cue_is_still_print(self):
        infos = self.infos((300.0, 300.0, 300.0, 240.0))
        self.assertTrue(ma.cue_fits(self.d / "Other Edition.cue", infos))
        self.assertEqual(ma.foreign_cues(self.d, infos), [])
        txt = ma.documents_text(self.d, ocr=False)
        typed, printed = ma.split_documents(txt, ma.foreign_cues(self.d, infos))
        self.assertIn(EU_BARCODE, printed)
        tr = [{"disc": 1, "num": k + 1, "title": f"T{k}", "secs": s} for k, s in enumerate((300.0, 300.0, 300.0, 240.0))]
        ct = [{"disc": 1, "title": f"T{k}", "secs": s} for k, s in enumerate((300, 300, 300, 240))]
        s = mc.score_candidate(album(tracks=tr, typed_text=typed, printed_text=printed),
                               cand(id="eu", country="XE", barcodes={EU_BARCODE}, tracks=ct), CFG, txt)
        self.assertTrue(mc.barcode_proves(s), s["code"])

    def test_a_cue_naming_the_folders_image_fits(self):
        infos = [(self.d / "Artist Alpha - First Light.wav", {"secs": 1140.0, "disc_eff": 1, "num": 0})]
        self.assertTrue(ma.cue_fits(self.d / "Other Edition.cue", infos))

    def test_an_unverifiable_cue_stays_print(self):
        self.assertIsNone(ma.cue_fits(self.d / "Other Edition.cue", []))
        self.assertEqual(ma.foreign_cues(self.d, []), [])


# ---- 6: the hidden track before track 1 ----------------------------------------------------
PRE = 95 * 75
HTOA_TOC = " ".join(map(str, [1, 3, 150 + PRE + 915 * 75, 150 + PRE, 150 + PRE + 200 * 75, 150 + PRE + 505 * 75]))


def htoa_files(with_zero=True):
    files = [{"num": i + 1, "secs": s, "disc_eff": 1} for i, s in enumerate((200.0, 305.0, 410.0))]
    return ([{"num": 0, "secs": 95.0, "disc_eff": 1}] if with_zero else []) + files


def htoa_release():
    def trk(i, s):
        return {"position": i, "number": str(i), "title": f"T{i - 1}", "length": int(s * 1000),
                "recording": {"id": f"r{i}", "title": f"T{i - 1}"}}
    return {"id": "rel1", "title": "First Light", "date": "2010", "country": "XE",
            "artist-credit": [{"name": "Artist Alpha", "artist": {"id": "a", "name": "Artist Alpha",
                                                                   "sort-name": "Alpha, Artist"}}],
            "media": [{"position": 1, "format": "CD", "track-count": 3,
                       "pregap": {"position": 0, "number": "0", "title": "Hidden", "length": 95000,
                                  "recording": {"id": "r0", "title": "Hidden"}},
                       "tracks": [trk(1, 200), trk(2, 305), trk(3, 410)]}],
            "release-group": {"id": "rg"}, "label-info": []}


class HiddenTrackOne(unittest.TestCase):
    def test_the_rips_own_log_fits_with_the_track_zero_file(self):
        self.assertTrue(ma.toc_fits_disc(HTOA_TOC, htoa_files()))
        kept, foreign = ma.fitting_logs([(None, x) for x in htoa_files()], [{"toc": HTOA_TOC, "path": "x.log"}])
        self.assertEqual((len(kept), len(foreign)), (1, 0))
        # a track 0 longer than the pregap is no hidden track of this disc
        longer = htoa_files()
        longer[0]["secs"] = 120.0
        self.assertFalse(ma.toc_fits_disc(HTOA_TOC, longer))
        self.assertEqual(ma.merge_tocs([(None, x) for x in htoa_files()], {1: HTOA_TOC}), {1: HTOA_TOC})

    def test_the_pregap_is_compared_with_the_track_zero_file(self):
        c = ma.mb_candidate(htoa_release())
        self.assertEqual(len(c["tracks"]), 3)                    # the release's own tracks, as before
        files = [{"disc": 1, "num": 0, "title": "Hidden", "secs": 95.0}] + [
            {"disc": 1, "num": i + 1, "title": f"T{i}", "secs": s} for i, s in enumerate((200.0, 305.0, 410.0))]
        a = album(tracks=files, toc_releases={"rel1"}, exact_toc_releases={"rel1"})
        r = pick(a, c)
        self.assertEqual(outcome(r), ("auto", "rel1"), r["why"])
        self.assertTrue(r["best"]["cand"].get("pregap_used"))

    def test_a_rip_without_the_track_zero_file_is_unchanged(self):
        c = ma.mb_candidate(htoa_release())
        a = album(toc_releases={"rel1"}, exact_toc_releases={"rel1"})
        self.assertIs(mc.with_pregap(a, c), c)
        r = pick(a, c)
        self.assertEqual(outcome(r), ("auto", "rel1"), r["why"])
        self.assertFalse(r["best"]["cand"].get("pregap_used"))

    def test_the_pregap_is_tagged_only_when_asked(self):
        rel = htoa_release()
        self.assertEqual(len(mb_tags.release_tags(rel)), 3)
        tags = mb_tags.release_tags(rel, pregap=True)
        self.assertEqual([t["tracknumber"] for t in tags], [["0"], ["1"], ["2"], ["3"]])
        self.assertEqual(tags[0]["title"], ["Hidden"])

    def test_a_file_named_00_is_numbered_0_and_put_first(self):
        infos = [(Path("/m/01 - One.flac"), {}), (Path("/m/00 - Hidden.flac"), {}), (Path("/m/02 - Two.flac"), {})]
        ma.names_from_files(infos)
        self.assertTrue(infos[1][1].get("num0"))
        self.assertFalse(infos[0][1].get("num0"))
        self.assertTrue(ma._numbered_zero("00"))
        self.assertTrue(ma._numbered_zero("0/12"))
        self.assertFalse(ma._numbered_zero(""))
        self.assertFalse(ma._numbered_zero("10"))


# ---- 7: Russian label lines ----------------------------------------------------------------
class RussianLabelLines(unittest.TestCase):
    def test_label_lines(self):
        self.assertEqual(ma.search_labels("", "Издатель (лейбл): Century Media\nЛейбл: Nuclear Blast\nLabel: Relapse"),
                         ["Century Media", "Nuclear Blast", "Relapse"])

    def test_label_and_catalogue_number_on_one_line(self):
        self.assertEqual(ma.labels_from_text("Label / Cat#: Century Media / 77587-2"), ["Century Media"])
        self.assertEqual(ma.labels_from_text("Лейбл / Номер по каталогу: Season of Mist / SOM 123"),
                         ["Season of Mist"])

    def test_never_scored(self):
        a = album(search_labels=["Century Media"])
        r = pick(a, cand(id="de", country="DE", label="Century Media"), cand(id="us", country="US", label="Relapse"))
        self.assertEqual(r["status"], "review", r["why"])


if __name__ == "__main__":
    unittest.main()
