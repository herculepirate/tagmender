#!/usr/bin/env python3
"""0.8.21: the rules of docs/MATCHING_RULES.md that changed, one class per rule ID."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import marks  # noqa: E402
import match_albums as ma  # noqa: E402
import match_core as mc  # noqa: E402
import profiles as pf  # noqa: E402
from test_match import album, cand, CFG  # noqa: E402

CODE, OTHER = "707723007025", "0602517880123"


def entry(id_, formats, clues, conf=98.0, catnos=("x1",), **cand_kw):
    return {"cand": dict({"source": "discogs", "id": id_, "title": "Album", "formats": set(formats),
                          "catnos": set(catnos), "barcodes": set(), "tracks": []}, **cand_kw),
            "clues": clues, "confidence": conf}


def result(source, status, cands, a, prefer="", cfg=CFG):
    scored = [mc.score_candidate(a, c, cfg) for c in cands]
    why = [] if status == "auto" else ["runner-up x within 1.0 points (likely another pressing)"]
    return {"status": status, "best": scored[0], "scored": scored, "why": why, "source": source,
            "cfg": {"prefer_source": prefer}}


class BAR2_YourBarcodeOnTheUnsurePickIsTaken(unittest.TestCase):
    def test_taken_over_the_preferred_sure_pick(self):
        a = album(barcodes={CODE})
        dg = result("discogs", "auto", [cand(source="discogs", id="dg1", catnos={"aa1"})], a, "discogs")
        mb = result("mb", "review", [cand(source="mb", id="mb1", catnos={"bb2"}, barcodes={CODE})], a, "discogs")
        out = mc.combine(mb, dg)
        self.assertEqual((out["status"], out["best"]["cand"]["id"]), ("auto", "mb1"))

    def test_two_entries_sharing_the_barcode_take_the_preferred_pick(self):
        # (BAR-2 cannot name one of them: HF-2, the preferred database's pick)
        a = album(barcodes={CODE})
        dg = result("discogs", "auto", [cand(source="discogs", id="dg1", catnos={"aa1"})], a, "discogs")
        mb = result("mb", "review", [cand(source="mb", id="mb1", catnos={"bb2"}, barcodes={CODE}),
                                     cand(source="mb", id="mb2", catnos={"cc3"}, barcodes={CODE})], a, "discogs")
        out = mc.combine(mb, dg)
        self.assertEqual((out["status"], out["best"]["cand"]["id"]), ("auto", "dg1"))


class BAR3_OuterPackagingBarcode(unittest.TestCase):
    def test_the_back_cover_names_the_product(self):
        a = album(barcodes={CODE, OTHER},
                  scan_log={"barcodes": {"/x/Back.jpg": {CODE}, "/x/Disc 1.jpg": {OTHER}}})
        self.assertTrue(mc.barcode_on_outer(a, {CODE}))
        self.assertFalse(mc.barcode_on_outer(a, {OTHER}))
        box = mc.score_candidate(a, cand(source="discogs", id="box", barcodes={CODE}), CFG)
        disc = mc.score_candidate(a, cand(source="discogs", id="disc", barcodes={OTHER}), CFG)
        self.assertIs(mc._outer_barcode_pick([disc, box]), box)

    def test_two_outer_barcodes_decide_nothing(self):
        a = album(barcodes={CODE, OTHER}, scan_log={"barcodes": {"/x/back.jpg": {CODE}, "/x/box.jpg": {OTHER}}})
        picks = [mc.score_candidate(a, cand(id=i, barcodes={b}), CFG) for i, b in (("1", CODE), ("2", OTHER))]
        self.assertIsNone(mc._outer_barcode_pick(picks))


class EVI1_CatalogueNumberOverDiscIdAndMatrix(unittest.TestCase):
    def test_weights(self):
        w = mc.CLUE_WEIGHTS
        self.assertGreater(w["barcode"], w["catalogue number"])
        self.assertGreater(w["catalogue number"], w["disc TOC"])
        self.assertGreater(w["catalogue number"], w["matrix"])
        self.assertGreater(w["matrix"], w["label"])

    def test_catalogue_number_beats_matrix(self):
        p = entry("p", ["cd"], {"catalogue number": "catalogue number ABC123 on the scans"}, catnos=("abc123",))
        q = entry("q", ["cd"], {"matrix": "matrix XYZ on the scans"}, catnos=("def456",))
        self.assertEqual(mc.choose_pressing([q, p])[0]["cand"]["id"], "p")


class TIE_CountryOrderThenOriginal(unittest.TestCase):
    def test_country_before_the_original_date(self):
        us = entry("us", ["cd"], {}, country="US", date="2012-01-01", catnos=("us1",))
        de = entry("de", ["cd"], {}, country="DE", date="2009-01-01", catnos=("de1",))
        pick = mc.choose_pressing([de, us], ["CA", "US", "DE"])
        self.assertEqual(pick[0]["cand"]["id"], "us")

    def test_the_date_breaks_a_tie_within_the_country(self):
        a = entry("a", ["cd"], {}, country="US", date="2012-01-01", catnos=("a1",))
        b = entry("b", ["cd"], {}, country="US", date="2009-03-01", catnos=("b1",))
        self.assertEqual(mc.choose_pressing([a, b], ["US"])[0]["cand"]["id"], "b")

    def test_a_country_pick_is_tagged_in_full(self):
        a = album()
        scored = [mc.score_candidate(a, cand(source="discogs", id=i, country=c, catnos={i + "99"}), CFG)
                  for i, c in (("de", "DE"), ("ca", "CA"))]
        out = mc.classify(scored, dict(CFG, preferred_countries=["CA"]))
        self.assertEqual((out["status"], out["best"]["cand"]["id"]), ("auto", "ca"))

    def test_held_back_in_the_evidence_pass(self):
        a = album()
        scored = [mc.score_candidate(a, cand(source="discogs", id=i, country=c, catnos={i + "99"},
                                             date=d), CFG)
                  for i, c, d in (("de", "DE", "2001-01-01"), ("ca", "CA", "2003-01-01"))]
        out = mc.classify(scored, dict(CFG, preferred_countries=[], evidence_pass=True))
        self.assertNotEqual(out["status"], "auto")


class ALB1_EveryTitleOneLengthOff(unittest.TestCase):
    def test_a_shortened_title_still_matches(self):
        self.assertTrue(mc._title_or_its_start("Tramonto", "Tramonto. Caldo umido"))
        self.assertTrue(mc._title_or_its_start("Tenera E La Notte", "Tenera è la notte"))
        self.assertFalse(mc._title_or_its_start("Intro", "Outro"))

    def test_just_under_the_cut_off_is_tagged(self):
        best = {"cand": {"tracks": [{}] * 9}, "tracks_ok": True, "titles_loose": 9, "dur_frac": 8 / 9,
                "artist_sim": 0.95, "title_sim": 1.0, "conflict": False, "confidence": 89.3}
        self.assertTrue(mc._one_length_off(best, mc.DEFAULT_CFG))
        self.assertFalse(mc._one_length_off(dict(best, dur_frac=7 / 9), mc.DEFAULT_CFG))
        self.assertFalse(mc._one_length_off(dict(best, titles_loose=8), mc.DEFAULT_CFG))
        self.assertFalse(mc._one_length_off(dict(best, confidence=80.0), mc.DEFAULT_CFG))


class MED3_SacdRates(unittest.TestCase):
    def test_rates(self):
        def ev(rate, bits=24):
            return ma.audio_evidence([{"rate": rate, "bits": bits, "lossless": True, "samples": 1}] * 2)["sacd"]
        self.assertTrue(ev(88200))
        self.assertTrue(ev(176400))
        self.assertTrue(ev(2822400, 1))
        self.assertFalse(ev(96000))
        self.assertFalse(ev(44100, 16))

    def test_sacd_media(self):
        self.assertTrue(mc.sacd_media(["Hybrid SACD"]))
        self.assertFalse(mc.sacd_media(["Blu-ray", "CD"]))

    def test_an_sacd_rate_picks_the_sacd_over_the_download(self):
        a = album(audio={"hires": True, "sacd": True, "cd_frames": False, "not_cd": False})
        cs = [cand(source="discogs", id="web", formats={"digital"}),
              cand(source="discogs", id="sacd", formats={"cd"}, hires_media=True, sacd_media=True)]
        scored = [mc.score_candidate(a, c, CFG) for c in cs]
        self.assertEqual((scored[1].get("clues") or {}).get("format"), mc.SACD_AUDIO_CLUE)
        self.assertEqual(mc.choose_pressing(scored)[0]["cand"]["id"], "sacd")

    def test_without_an_sacd_an_88_2_download_is_the_download(self):
        a = album(audio={"hires": True, "sacd": True, "cd_frames": False, "not_cd": False})
        cs = [cand(source="discogs", id="lp", country="US", formats={"vinyl"}, catnos={"lp1"}),
              cand(source="discogs", id="web", country="XW", formats={"digital"}, catnos={"w1"})]
        out = mc.classify([mc.score_candidate(a, c, CFG) for c in cs], dict(CFG, preferred_countries=["US", "XW"]))
        self.assertEqual(out["best"]["cand"]["id"], "web")


class MED4_HiResWithADiscAndADownload(unittest.TestCase):
    def test_left_open(self):
        web = entry("web", ["digital"], {"format": mc.HIRES_DIGITAL_CLUE}, catnos=())
        bd = entry("bd", ["cd"], {}, hires_media=True)
        self.assertIsNone(mc.choose_pressing([bd, web], ["US"]))

    def test_without_the_disc_the_download(self):
        web = entry("web", ["digital"], {"format": mc.HIRES_DIGITAL_CLUE}, catnos=())
        cd = entry("cd", ["cd"], {})
        self.assertEqual(mc.choose_pressing([cd, web])[0]["cand"]["id"], "web")


class MED6_TapeRip(unittest.TestCase):
    def test_marks(self):
        self.assertEqual(marks.of_album([{"media": ["Cassette"]}], []), marks.TAPE)
        self.assertEqual(marks.of_album([], [], "/in/Artist - Album [Cassette]"), marks.TAPE)
        self.assertEqual(marks.of_album([{"media": ["Vinyl"]}], []), marks.VINYL)


class BAR5_ScansBarcodeOverTags(unittest.TestCase):
    def setUp(self):
        self.a = album(barcodes={OTHER}, ocr_barcodes={CODE})          # tags say OTHER, the back cover CODE

    def test_the_scans_barcode_is_proof(self):
        s = mc.score_candidate(self.a, cand(source="discogs", id="scan", barcodes={CODE}), CFG)
        self.assertTrue(mc.barcode_proves(s))
        self.assertFalse(s.get("conflict"))

    def test_the_tags_barcode_alone_is_weak(self):
        s = mc.score_candidate(self.a, cand(source="discogs", id="tags", barcodes={OTHER}), CFG)
        self.assertFalse(mc.barcode_proves(s))
        self.assertEqual(s["code"], mc.TAGS_BARCODE)

    def test_a_sticker_on_the_scans_changes_nothing(self):
        a = album(barcodes={OTHER}, ocr_barcodes={CODE}, unlisted_barcodes={CODE})
        s = mc.score_candidate(a, cand(source="discogs", id="tags", barcodes={OTHER}), CFG)
        self.assertTrue(mc.barcode_proves(s))


class EVI4_PoorOcrNeverLowers(unittest.TestCase):
    def test(self):
        a = album()
        c = cand(source="discogs", id="d")
        plain = mc.score_candidate(a, c, CFG)["confidence"]
        blurry = mc.score_candidate(a, c, CFG, ocr_text="zzz qqq nothing readable")["confidence"]
        self.assertGreaterEqual(blurry, plain)


class CHG1_YourChoiceStands(unittest.TestCase):
    def scored(self, numbered_other=False):
        a = album(barcodes={CODE} if numbered_other else set())
        mine = mc.score_candidate(a, cand(source="discogs", id="mine", country="DE", catnos={"de1"}), CFG)
        mine["tagged"] = True
        other = mc.score_candidate(a, cand(source="discogs", id="other", country="CA", catnos={"ca1"},
                                           barcodes={CODE} if numbered_other else set()), CFG)
        other["confidence"] = mine["confidence"] + 10
        return [other, mine]

    def test_a_better_score_alone_does_not_move_it(self):
        out = mc.classify(self.scored(), dict(CFG, preferred_countries=["CA"]))
        self.assertEqual((out["status"], out["best"]["cand"]["id"]), ("auto", "mine"))

    def test_your_barcode_elsewhere_moves_it(self):
        out = mc.classify(self.scored(numbered_other=True), CFG)
        self.assertEqual(out["best"]["cand"]["id"], "other")


class HF2_DisputeTakesThePreferredPick(unittest.TestCase):
    def test(self):
        a = album()
        dg = result("discogs", "auto", [cand(source="discogs", id="dg1", catnos={"aa1"})], a, "discogs")
        mb = result("mb", "auto", [cand(source="mb", id="mb1", catnos={"bb2"})], a, "discogs")
        out = mc._disputed(mb, dg, "two pressings, each with evidence")
        self.assertEqual((out["status"], out["best"]["cand"]["id"]), ("auto", "dg1"))
        dg["cfg"]["prefer_source"] = mb["cfg"]["prefer_source"] = ""
        self.assertEqual(mc._disputed(mb, dg, "x")["status"], "review")

    def test_never_a_pick_its_database_was_unsure_of_for_its_score(self):
        a = album()
        dg = result("discogs", "auto", [cand(source="discogs", id="dg1", catnos={"aa1"})], a, "musicbrainz")
        mb = result("mb", "review", [cand(source="mb", id="mb1", catnos={"bb2"})], a, "musicbrainz")
        mb["why"] = ["confidence 62 < 80", "artist differs"]
        self.assertEqual(mc._disputed(dg, mb, "the disc ID is on MusicBrainz's")["status"], "review")


class EVI_FormatNoteIsNoCatalogueNumber(unittest.TestCase):
    """"[ECM 24-96]" (Quiet Inlet) is ECM's 24-bit/96 kHz edition, not a catalogue number:
    taken for one, it counted against every pressing and left the album Unresolved."""
    def test_bit_depth_and_rate_pairs(self):
        for t in ("Quiet Inlet [ECM 24-96]", "Quiet Inlet [ECM 24/96]", "X [24/192]", "X [16-44.1]",
                  "X [24-88.2]"):
            self.assertEqual(mc.catno_hints(t), [], t)

    def test_real_catalogue_numbers_stay(self):
        self.assertEqual(mc.catno_hints("Mercurial Balm [ECM 2269]"), ["ECM 2269"])
        self.assertEqual(mc.catno_hints("Y [BRC-67]"), ["BRC-67"])
        self.assertEqual(mc.catno_hints("W [DIN 24-2]"), ["DIN 24-2"])


class NAM1_LeadArtistByDefault(unittest.TestCase):
    def test_default(self):
        self.assertTrue(pf.DEFAULTS["album_artist"]["lead"])

    def lead(self, credit, names):
        import album_artist
        prof = pf.from_dict({"name": "t", "album_artist": {"lead": True, "bands": False}})
        t = {"albumartist": [credit], "~discogs_albumartists": names, "title": ["x"]}
        return album_artist.apply([t], prof)[0]["albumartist"]

    def test_joint_credit_takes_its_first_artist(self):
        self.assertEqual(self.lead("Thomas Strønen & Time Is A Blind Guide",
                                   ["Thomas Strønen", "Time Is A Blind Guide"]), ["Thomas Strønen"])

    def test_band_credited_after_its_members_is_the_band(self):
        # (Discogs: "Thomas Strønen / Iain Ballamy : Food" - Mercurial Balm went under Thomas Strønen)
        self.assertEqual(self.lead("Thomas Strønen / Iain Ballamy : Food",
                                   ["Thomas Strønen", "Iain Ballamy", "Food"]), ["Food"])


if __name__ == "__main__":
    unittest.main()
