#!/usr/bin/env python3
"""0.8.21: a sure pick of the preferred database, or one carrying the same barcode, is no
contradiction; a catalogue number on the scans says nothing against an entry listing none."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import match_core as mc  # noqa: E402
from test_match import album, cand, CFG  # noqa: E402

CODE = "707723007025"


def result(source, status, cands, a, prefer=""):
    scored = [mc.score_candidate(a, c, CFG) for c in cands]
    why = [] if status == "auto" else ["runner-up x within 1.0 points (likely another pressing)"]
    return {"status": status, "best": scored[0], "scored": scored, "why": why, "source": source,
            "cfg": {"prefer_source": prefer}}


OTHER = "0602517880123"


class SurePickNotDisputed(unittest.TestCase):
    def combine(self, dg_codes, prefer, mine=(CODE,)):
        a = album(barcodes=set(mine))
        dg = result("discogs", "auto", [cand(source="discogs", id="4332599", catnos={"wla1"}, barcodes=set(dg_codes))],
                    a, prefer)
        mb = result("mb", "review", [cand(source="mb", id="mb-1", catnos={"other9"}, barcodes={CODE})], a, prefer)
        return mc.combine(mb, dg)

    def test_both_carry_your_barcode(self):
        for prefer in ("", "discogs"):
            out = self.combine({CODE}, prefer)
            self.assertEqual((out["status"], str(out["best"]["cand"]["id"])), ("auto", "4332599"))

    def test_your_barcode_on_the_unsure_pick_alone_is_taken(self):
        for prefer in ("", "discogs"):           # BAR-2: your barcode is the release, preferred or not
            out = self.combine(set(), prefer)
            self.assertEqual((out["status"], str(out["best"]["cand"]["id"])), ("auto", "mb-1"))

    def test_two_different_barcodes_of_yours(self):
        # HF-2: with a preference, its pick is taken; without one, a person decides
        out = self.combine({OTHER}, "discogs", mine=(CODE, OTHER))
        self.assertEqual((out["status"], str(out["best"]["cand"]["id"])), ("auto", "4332599"))
        self.assertNotEqual(self.combine({OTHER}, "", mine=(CODE, OTHER))["status"], "auto")


def entry(id_, formats, catnos, clues, conf):
    return {"cand": {"source": "discogs", "id": id_, "title": "Last Night", "formats": set(formats),
                     "catnos": set(catnos), "barcodes": set(), "tracks": []},
            "clues": clues, "confidence": conf}


class ScansNumberAgainstAnUnlistedOne(unittest.TestCase):
    def test_the_download_your_folder_names_stays(self):
        web = entry("17223712", ["digital"], [], {"format": "digital release (folder name)"}, 100.0)
        lp = entry("2992042", ["vinyl"], ["ecm2077"], {"catalogue number": "catalogue number ECM2077 on the scans"}, 94.2)
        pick = mc.choose_pressing([lp, web])
        self.assertIsNotNone(pick)
        self.assertEqual(pick[0]["cand"]["id"], "17223712")

    def test_a_number_still_tells_listed_pressings_apart(self):
        a = entry("1", ["cd"], ["ecm2077"], {"catalogue number": "catalogue number ECM2077 on the scans"}, 98.0)
        b = entry("2", ["cd"], ["b0012573"], {}, 98.0)
        self.assertEqual(mc.choose_pressing([b, a])[0]["cand"]["id"], "1")

    def test_a_cd_entry_without_a_number_is_still_told_apart(self):
        a = entry("1", ["cd"], ["ecm2077"], {"catalogue number": "catalogue number ECM2077 on the scans"}, 98.0)
        b = entry("2", ["cd"], [], {}, 98.0)
        self.assertEqual(mc.choose_pressing([b, a])[0]["cand"]["id"], "1")


if __name__ == "__main__":
    unittest.main()
