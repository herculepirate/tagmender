# 0.8.3 evidence fixes (no network; synthetic albums and candidates):
# - another tagger's release IDs never pick between MusicBrainz's and Discogs's pressings;
# - a catalogue number only another tagger wrote proves no pressing (as its barcode);
# - an audio fingerprint names recordings: it neither overrides the folder's catalogue number,
#   nor counts as evidence about your copy between the two databases, nor backs a bootleg.
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_v080_evidence import album, cand, pick, outcome, tracks  # noqa: E402
import match_core as mc  # noqa: E402


class TaggerIdsBetweenDatabases(unittest.TestCase):
    def results(self, by_tagger):
        a = album(tag_release_discogs={"222"}, tags_by_tagger=by_tagger)
        return (pick(a, cand(id="m1", country="DE", label="Lab A")),
                pick(a, cand(source="discogs", id="222", country="JP", label="Lab B", catnos={"zz1"})))

    def test_another_taggers_discogs_id_picks_nothing(self):
        self.assertEqual(mc.combine(*self.results(True), ["US"])["status"], "review")

    def test_tagmenders_own_tags_still_keep_their_release(self):
        out = mc.combine(*self.results(False), ["US"])
        self.assertEqual((out["status"], out["best"]["cand"]["id"]), ("auto", "222"))


class TaggerCatalogueNumber(unittest.TestCase):
    T = dict(catnos={"cm1234"}, tag_catnos={"cm1234"}, tag_release_mb={"b"})

    def test_it_is_a_weak_code(self):
        r = pick(album(tags_by_tagger=True, **self.T), cand(id="b", catnos={"cm1234"}))
        self.assertEqual(r["best"]["code"], mc.TAGS_CATNO)
        r = pick(album(tags_by_tagger=False, **self.T), cand(id="b", catnos={"cm1234"}))
        self.assertEqual(r["best"]["code"], "catalogue number")       # (TagMender's own, or no IDs)

    def test_it_excuses_no_lengths_silences_no_folder_year_backs_no_bootleg(self):
        for a, c in ((album(tracks=tracks(230.0, 335.0, 440.0), tags_by_tagger=True, **self.T),
                      cand(id="b", catnos={"cm1234"})),
                     (album(pressing_year=2005, tags_by_tagger=True, **self.T),
                      cand(id="b", year=1998, catnos={"cm1234"})),
                     (album(tags_by_tagger=True, **self.T), cand(id="b", unofficial=True, catnos={"cm1234"}))):
            self.assertEqual(pick(a, c)["status"], "review", pick(a, c)["why"])

    def test_the_folder_naming_it_too_keeps_full_weight(self):
        r = pick(album(tags_by_tagger=True, catnos_from_folder={"cm1234"}, **self.T), cand(id="b", catnos={"cm1234"}))
        self.assertNotIn(r["best"]["code"], mc.WEAK_BARCODES)


class Fingerprints(unittest.TestCase):
    def test_no_override_of_the_folders_catalogue_number(self):
        a = album(folder="2010 - First Light [Japan, VICP-99999]", catnos={"vicp99999"},
                  catnos_from_folder={"vicp99999"}, fp_releases={"jp"})
        r = pick(a, cand(id="eu", country="XE"), cand(id="jp", country="JP"))
        self.assertEqual(r["status"], "review", r["why"])

    def test_no_evidence_between_the_two_databases(self):
        a = album(fp_releases={"m1"})
        mb = pick(a, cand(id="m1", country="DE", label="Lab A"))
        dg = pick(a, cand(source="discogs", id="222", country="JP", label="Lab B", catnos={"zz1"}))
        self.assertEqual(mc.combine(mb, dg, ["US"])["status"], "review")

    def test_backs_no_bootleg(self):
        r = pick(album(fp_releases={"bt"}), cand(id="bt", unofficial=True))
        self.assertEqual(r["status"], "review", r["why"])


class Round3(unittest.TestCase):
    CODE = {mc.norm_code("ABC-12345")}

    def test_a_fingerprint_breaks_no_tie_between_pressings(self):
        r = pick(album(fp_releases={"a"}), cand(id="a", country="DE"), cand(id="b", country="US"))
        self.assertEqual(r["status"], "review", r["why"])

    def test_a_number_printed_on_your_scans_is_yours_whoever_also_tagged_it(self):
        off = [{"disc": 1, "title": f"T{i}", "secs": s} for i, s in enumerate((200, 330, 440))]
        a = album(tags_by_tagger=True, catnos=self.CODE, tag_catnos=self.CODE)
        r = pick(a, cand(id="a", catnos=self.CODE, tracks=off), ocr="back cover: ABC-12345 made in germany")
        self.assertEqual((outcome(r), r["best"]["code"]), (("auto", "a"), "catalogue number (OCR)"))

    def test_a_weak_code_still_has_the_scans_looked_at(self):
        import os
        import tempfile
        import match_albums as ma
        d = tempfile.mkdtemp()
        open(os.path.join(d, "back.jpg"), "wb").close()
        best = {"code": mc.TAGS_CATNO, "cand": {"catnos": self.CODE, "barcodes": set()}}
        a = album(tags_by_tagger=True, catnos=self.CODE, tag_catnos=self.CODE)
        self.assertTrue(ma._needs_investigation({"status": "auto", "best": best}, a, "XYZ-99887", d))

    def test_another_taggers_number_still_proves_the_album(self):
        s = {"clues": {}, "code": mc.TAGS_CATNO, "dur_frac": 1.0}
        self.assertTrue(mc._numbers_prove_album(s, mc.DEFAULT_CFG))


class Round4(unittest.TestCase):
    def test_another_taggers_barcode_never_hides_the_folders_catalogue_number(self):
        from test_v080_evidence import EU_BARCODE
        a = album(folder="2010 - First Light [CM1234]", catnos={"cm1234"}, catnos_from_folder={"cm1234"},
                  barcodes={EU_BARCODE}, tag_barcodes={EU_BARCODE}, tag_release_mb={"a"}, tags_by_tagger=True)
        r = pick(a, cand(id="a", country="DE", catnos={"cm1234"}, barcodes={EU_BARCODE}),
                 cand(id="b", country="US", catnos={"cm1234"}))
        self.assertEqual(r["status"], "review", r["why"])
        r = pick(dict(a, tags_by_tagger=False), cand(id="a", country="DE", catnos={"cm1234"}, barcodes={EU_BARCODE}),
                 cand(id="b", country="US", catnos={"cm1234"}))
        self.assertEqual(outcome(r), ("auto", "a"))                  # (your copy's own barcode)


if __name__ == "__main__":
    unittest.main()
