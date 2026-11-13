"""Rick Wakeman & Tony Fernandez - Zodiaque (88561-1024-2): the retail US CD Discogs 5459861 -
its matrix "88561-1024-2 (V) :: MASTERED BY NIMBUS" on the disc, no "CRC" on the back - was never
compared, and the Columbia House club edition 32698659 was taken.
RET-1: what a search by your own barcode or catalogue number finds is always fetched.
BAR-11: the barcode written is a barcode, not the catalogue number Discogs lists as "Text".
CLB-1: a club edition whose mark your legibly read scans lack yields to the release carrying
your barcode that nothing argues against."""
import os
import sys
import unittest
import urllib.parse as up
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import match_albums as ma  # noqa: E402
import match_core as mc  # noqa: E402
from test_match import album, cand, CFG  # noqa: E402

PLUGIN_DIR = os.environ.get("TAGMENDER_DISCOGS_PLUGIN") or ma.DEFAULT_PLUGIN_DIR


def release(id_, title, year, barcodes=(), club_mark=None):
    ids = [{"type": "Barcode", "value": b, "description": d} for b, d in barcodes]
    if club_mark:
        ids.append({"type": "Other", "value": club_mark, "description": "Back cover"})
        ids.append({"type": "Other", "value": "Made in USA - Digital Audio Disc Corp.", "description": "Ring text"})
    fmt = ["Album", "Club Edition", "Reissue"] if club_mark else ["Album", "Reissue"]
    return {"id": id_, "title": title, "year": year, "released": str(year), "uri": "",
            "artists": [{"name": "Rick Wakeman"}], "labels": [{"name": "Relativity", "catno": "88561-1024-2"}],
            "formats": [{"name": "CD", "qty": "1", "descriptions": fmt}], "identifiers": ids,
            "tracklist": [{"position": str(i + 1), "title": f"T{i}", "duration": "3:20", "type_": "track"}
                          for i in range(3)]}


class Flood:
    """The barcode and number searches find the retail CD; the title search finds many
    entries of 1988 that rank above it by title and year."""
    def __init__(self):
        self.urls = []

    def get_json(self, url, host):
        u = up.unquote(url)
        self.urls.append(u)
        if host != "discogs":
            return None
        if "search?" in u:
            if "barcode=" in u or "catno=" in u:
                return {"results": [{"id": 1, "title": "Rick Wakeman - Zodiaque", "year": "1988"},
                                    {"id": 2, "title": "Rick Wakeman - Zodiaque", "year": "1989"}]}
            return {"results": [{"id": 10 + i, "title": "Rick Wakeman - Zodiaque", "year": "1988"} for i in range(9)]
                    + [{"id": 2, "title": "Rick Wakeman - Zodiaque", "year": "1989"}]}
        rid = int(u.rsplit("/", 1)[1])
        return release(rid, "Rick Wakeman - Zodiaque", 1989 if rid == 2 else 1988)


class RET1_YourNumbersFindsAreFetched(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dm = ma.load_plugin(PLUGIN_DIR)

    def test_barcode_find_fetched_though_the_title_ranks_it_low(self):
        a = album(artist="Rick Wakeman", title="Zodiaque", year=1988, barcodes={"088561102425"},
                  catno_search=["88561 1024 2"], search_labels=[])
        h = Flood()
        out = ma.discogs_search(h, a, dict(CFG, discogs_detail_fetch=5), self.dm)
        self.assertIn("2", [c["id"] for c in out], "the release your barcode search found")

    def test_a_number_too_many_carry_adds_nothing(self):
        class Common(Flood):
            def get_json(self, url, host):
                u = up.unquote(url)
                if host == "discogs" and "search?" in u and ("barcode=" in u or "catno=" in u):
                    self.urls.append(u)
                    return {"results": [{"id": 100 + i, "title": "Other - X", "year": "1970"} for i in range(15)]}
                return super().get_json(url, host)
        a = album(artist="Rick Wakeman", title="Zodiaque", year=1988, catno_search=["1"], search_labels=[])
        h = Common()
        ma.discogs_search(h, a, dict(CFG, discogs_detail_fetch=5), self.dm)
        details = [u for u in h.urls if "/releases/" in u]
        self.assertLessEqual(len(details), 5, "a number fifteen releases share adds no fetch of its own")


    def test_the_bare_number_brings_no_other_artist(self):
        # (review M2: "catno=X" without the artist finds other labels' releases; they would count
        # as the number's rivals and cost a vinyl rip its catalogue-number proof)
        class Bare(Flood):
            def get_json(self, url, host):
                u = up.unquote(url)
                if host == "discogs" and "search?" in u and "catno=" in u:
                    self.urls.append(u)
                    if "&artist=" in u:
                        return {"results": []}
                    return {"results": [{"id": 300, "title": "Someone Else - Other", "year": "1975"}]}
                if host == "discogs" and "search?" in u and "barcode=" in u:
                    self.urls.append(u)
                    return {"results": []}
                return super().get_json(url, host)
        a = album(artist="Rick Wakeman", title="Zodiaque", year=1988, catno_search=["LP 001"], search_labels=[])
        h = Bare()
        ma.discogs_search(h, a, dict(CFG, discogs_detail_fetch=5), self.dm)
        self.assertFalse([u for u in h.urls if u.endswith("/releases/300")], "fetched only by RET-1's bare number")

class BAR11_TheBarcodeNotTheNumberUnderIt(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dm = ma.load_plugin(PLUGIN_DIR)

    def test_scanned_barcode_over_the_text_catalogue_number(self):
        rel = release(5459861, "Rick Wakeman - Zodiaque", 1989,
                      barcodes=[("88561-1024-2", "Text"), ("088561102425", "Scanned")])
        fields = self.dm.extract_discogs_fields(rel) if hasattr(self.dm, "extract_discogs_fields") else None
        if fields is None:
            self.skipTest("plugin has no extract_discogs_fields")
        album_fields = fields[0] if isinstance(fields, tuple) else fields
        self.assertEqual(album_fields.get("barcode"), "088561102425")

    def test_only_a_text_value_is_still_written(self):
        rel = release(1, "Rick Wakeman - Zodiaque", 1989, barcodes=[("88561-1024-2", "Text")])
        fields = self.dm.extract_discogs_fields(rel)
        album_fields = fields[0] if isinstance(fields, tuple) else fields
        self.assertEqual(album_fields.get("barcode"), "88561-1024-2")


class CLB1_ClubMarkAbsentFromALegibleBack(unittest.TestCase):
    BACK = "RICK WAKEMAN & TONY FERNANDEZ ZODIAQUE PRESIDENT RELATIVITY 88561-1024-2 Made in the U.S.A."
    LOG = {"barcodes": {"/s/Back.jpg": {"088561102425"}}, "text": {"/s/Back.jpg": [BACK]}}
    SCANNED = album(barcodes={"088561102425"}, ocr_barcodes={"088561102425"}, scan_log=LOG)

    def test_marks_only_of_a_club_edition(self):
        self.assertEqual(ma.discogs_club_marks(release(1, "x", 1988, club_mark="CRC")), ["CRC"])
        self.assertEqual(ma.discogs_club_marks(release(2, "x", 1989)), [])

    def test_absent_on_a_legible_back(self):
        c = cand(source="discogs", id="club", club_marks=["CRC"], barcodes={"088561102425"})
        self.assertTrue(mc.club_mark_missing(self.SCANNED, self.BACK, c).startswith(mc.CLUB_AGAINST))

    def test_present_or_unreadable_says_nothing(self):
        c = cand(source="discogs", id="club", club_marks=["CRC"], barcodes={"088561102425"})
        self.assertEqual(mc.club_mark_missing(self.SCANNED, self.BACK + " CRC", c), "")
        self.assertEqual(mc.club_mark_missing(album(), self.BACK, c), "", "no barcode decoded off a scan")

    def test_bars_decoded_on_a_scan_whose_text_was_not_read(self):
        # (review NEW-1: the bars decoded, the back's own text never read - its CRC may be there)
        c = cand(source="discogs", id="club", club_marks=["CRC"], barcodes={"088561102425"})
        a = album(ocr_barcodes={"088561102425"}, scan_log={"barcodes": {"/s/Back.jpg": {"088561102425"}},
                                                          "text": {"/s/Front.jpg": ["RICK WAKEMAN ZODIAQUE"]}})
        self.assertEqual(mc.club_mark_missing(a, "RICK WAKEMAN ZODIAQUE", c), "")
        self.assertEqual(mc.club_mark_missing(self.SCANNED, "", c), "", "no scan text")

    def test_a_cue_sheet_or_a_disc_scan_is_no_back_cover(self):
        # (review M4a/M4b: a cue's CATALOG line, notes, or a scan of the disc label alone read the
        # number - the bars were never decoded off a scan: nothing is concluded)
        c = cand(source="discogs", id="club", club_marks=["CRC"], barcodes={"088561102425"})
        for text in ("CATALOG 088561102425", "RICK WAKEMAN ZODIAQUE 88561-1024-2 PRESIDENT"):
            self.assertEqual(mc.club_mark_missing(album(barcodes={"088561102425"}), text, c), "", text)
            disc = album(barcodes={"088561102425"}, scan_log={"barcodes": {}, "text": {"/s/CD.jpg": [text]}})
            self.assertEqual(mc.club_mark_missing(disc, text, c), "", text)

    def test_only_sleeve_marks_count(self):
        rel = release(1, "x", 1988, club_mark="CRC")
        rel["identifiers"] += [{"type": "Other", "value": "DADC", "description": "Disc"},
                               {"type": "Other", "value": "Made in USA", "description": "Back cover"}]
        self.assertEqual(ma.discogs_club_marks(rel), ["CRC"])

    def test_the_retail_release_with_your_barcode_is_taken(self):
        a = album(barcodes={"088561102425"}, ocr_barcodes={"088561102425"}, scan_log=self.LOG, year=1988)
        club = cand(source="discogs", id="club", year=1988, country="US", barcodes={"088561102425"}, club_marks=["CRC"])
        retail = cand(source="discogs", id="retail", year=1989, country="US", barcodes={"088561102425"})
        scored = [mc.score_candidate(a, c, CFG, self.BACK + " 0 88561 10242 5") for c in (club, retail)]
        self.assertTrue(any(str(x).startswith(mc.CLUB_AGAINST) for x in scored[0]["clues_against"]))
        self.assertGreater(scored[0]["confidence"], scored[1]["confidence"], "the year alone ranks the club first")
        self.assertEqual(mc.classify(scored, CFG)["best"]["cand"]["id"], "retail")

    def test_far_behind_is_never_promoted(self):
        # (review M4c: only inside the tie margin)
        a = album(barcodes={"088561102425"}, ocr_barcodes={"088561102425"}, scan_log=self.LOG, year=1988)
        club = cand(source="discogs", id="club", year=1988, country="US", barcodes={"088561102425"}, club_marks=["CRC"])
        far = cand(source="discogs", id="retail", year=1989, country="US", barcodes={"088561102425"},
                   tracks=[{"disc": 1, "title": f"Other {i}", "secs": s} for i, s in enumerate((200, 305, 410))])
        scored = [mc.score_candidate(a, c, CFG, self.BACK) for c in (club, far)]
        if scored[0]["confidence"] - scored[1]["confidence"] >= CFG["auto_min_margin"]:
            self.assertEqual(mc.classify(scored, CFG)["best"]["cand"]["id"], "club")

    def test_without_the_objection_the_ranking_is_unchanged(self):
        a = album(barcodes={"088561102425"}, ocr_barcodes={"088561102425"}, scan_log=self.LOG, year=1988)
        club = cand(source="discogs", id="club", year=1988, country="US", barcodes={"088561102425"}, club_marks=["CRC"])
        retail = cand(source="discogs", id="retail", year=1989, country="US", barcodes={"088561102425"})
        scored = [mc.score_candidate(a, c, CFG, self.BACK + " CRC 0 88561 10242 5") for c in (club, retail)]
        self.assertEqual(mc.classify(scored, CFG)["best"]["cand"]["id"], "club")


if __name__ == "__main__":
    unittest.main()
