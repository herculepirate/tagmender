"""DAT-3 (asked for as "DAT-1"; that ID is Date / Original Date): a rip log of your files dated
in one year rules out a pressing first released in a later year (Pekka Pohjola - Flight Of The
Angel: an EAC log of 27 August 2005 against MusicBrainz's 2012 Finnish entry sharing the 2002
remaster's barcode and PELPCD 4). Synthetic logs and candidates only."""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import match_albums as ma  # noqa: E402
import match_core as mc  # noqa: E402

UPC = "6400000000017"
TITLES = ["Morning Song", "Glass Garden", "Il Ritorno", "Pressure Point", "Last Waltz"]
SECS = (300.0, 320.0, 190.0, 520.0, 500.0)


def _cand(**kw):
    c = {"source": "mb", "id": "x", "url": "", "artist": "Band", "title": "Flight", "year": 2002,
         "date": "2002", "country": "FI", "label": "Label", "catnos": {mc.norm_code("LBLCD 4")},
         "barcodes": {UPC}, "disc_count": 1, "formats": {"cd"}, "release_group": "rg",
         "tracks": [{"disc": 1, "title": t, "secs": s} for t, s in zip(TITLES, SECS)]}
    c.update(kw)
    return c


def _album(**kw):
    a = {"artist": "Band", "title": "Flight", "year": 1986, "discs": 1, "folder": "1986 - Flight (2002 LBLCD 4)",
         "tracks": [{"disc": 1, "num": i + 1, "title": t, "secs": s} for i, (t, s) in enumerate(zip(TITLES, SECS))],
         "barcodes": {UPC}, "ocr_barcodes": [UPC], "catnos": {mc.norm_code("LBLCD 4")},
         "catnos_from_folder": {mc.norm_code("LBLCD 4")}, "rip_log_year": 2005}
    a.update(kw)
    return a


CFG = dict(mc.DEFAULT_CFG, preferred_countries=["FI"])


def _res(album, *cands):
    scored = [mc.score_candidate(album, c, CFG) for c in cands]
    return dict(mc.classify(scored, CFG), scored=scored, source=cands[0]["source"])


def _p303(album=None):
    """The 2012 entry first and fuller (a day-exact date), as MusicBrainz lists them."""
    a = album or _album()
    return _res(a, _cand(id="mb-2012", year=2012, date="2012-05-30"), _cand(id="mb-2002"))


class DAT3PressingsSharingYourBarcode(unittest.TestCase):
    def test_DAT3_the_log_date_rules_out_the_later_pressing(self):
        res = _p303()
        self.assertEqual((res["status"], res["best"]["cand"]["id"]), ("auto", "mb-2002"), res["why"])
        self.assertTrue(any("DAT-3" in n and "2005" in n for n in res["best"]["notes"]), res["best"]["notes"])
        later = next(s for s in res["scored"] if s["cand"]["id"] == "mb-2012")
        self.assertIn("your rip log is dated 2005; this pressing came out in 2012", later["clues_against"])

    def test_DAT3_with_discogs_beside_it(self):
        mb = _p303()
        dg = _res(_album(), _cand(source="discogs", id="3625724", year=2002, date="2002"),
                  _cand(source="discogs", id="dg-2012", year=2012, date="2012-05-30"))
        self.assertEqual((dg["status"], dg["best"]["cand"]["id"]), ("auto", "3625724"), dg["why"])
        out = mc.combine(mb, dg, ["FI"])
        self.assertIn(out["best"]["cand"]["id"], ("mb-2002", "3625724"))

    def test_DAT3_without_a_dated_log_nothing_is_ruled_out(self):
        # (the defect's shape: with nothing else of yours, the fuller 2012 entry is taken as
        # "the same pressing listed twice" - which the log's date now tells apart)
        res = _p303(_album(rip_log_year=None))
        self.assertEqual(res["best"]["cand"]["id"], "mb-2012")
        self.assertFalse(any(mc.rip_dated_out(s) for s in res["scored"]))
        self.assertFalse(any("DAT-3" in n for n in res["best"]["notes"]))

    def test_DAT3_the_same_year_is_not_later(self):
        res = _res(_album(rip_log_year=2012), _cand(id="mb-2012", year=2012, date="2012-05-30"), _cand(id="mb-2002"))
        self.assertFalse(any(mc.rip_dated_out(s) for s in res["scored"]))

    def test_DAT3_an_undated_release_is_not_ruled_out(self):
        undated = _cand(id="mb-undated", year=None, date="")
        self.assertEqual(mc.rip_date_against(_album(), undated), "")
        res = _res(_album(), _cand(id="mb-2012", year=2012, date="2012-05-30"), undated)
        self.assertEqual(res["best"]["cand"]["id"], "mb-undated")
        self.assertNotIn("mb-2012", [s["cand"]["id"] for s in res["scored"] if not mc.rip_dated_out(s)])

    def test_DAT3_the_release_groups_first_date_does_not_count(self):
        # (the album's first release, 1986, is before the log; the pressing's own 2012 is not)
        c = _cand(id="mb-2012", year=2012, date="2012-05-30", original_year=1986)
        self.assertTrue(mc.rip_date_against(_album(), c))
        self.assertEqual(mc.pressing_own_year(_cand(year=None, date="2012-05")), 2012)

    def test_DAT3_every_candidate_later_is_held_not_dropped(self):
        res = _res(_album(), _cand(id="mb-2012", year=2012, date="2012-05-30"),
                   _cand(id="mb-2010", year=2010, date="2010"))
        self.assertNotEqual(res["status"], "auto")
        self.assertTrue(any("DAT-3" in w for w in res["why"]), res["why"])
        self.assertEqual(len(res["scored"]), 2)
        self.assertIsNotNone(res["runner_up"])

    def test_DAT3_a_lone_later_release_without_your_barcode_is_held(self):
        a = _album(barcodes=set(), ocr_barcodes=[], catnos=set(), catnos_from_folder=set())
        res = _res(a, _cand(id="mb-2012", year=2012, date="2012-05-30", barcodes=set()))
        self.assertNotEqual(res["status"], "auto")
        self.assertTrue(any("DAT-3" in w for w in res["why"]), res["why"])


class DAT3BarcodeWithNoRival(unittest.TestCase):
    def test_DAT3_a_lone_barcode_match_is_unaffected(self):
        res = _res(_album(), _cand(id="mb-2012", year=2012, date="2012-05-30"))
        self.assertEqual((res["status"], res["best"]["cand"]["id"]), ("auto", "mb-2012"), res["why"])
        self.assertTrue(any("BAR-1" in n and "DAT-3" in n for n in res["best"]["notes"]))

    def test_DAT3_your_barcode_beats_an_earlier_pressing_without_it(self):
        other = _cand(id="mb-other", barcodes={"5000000000001"}, catnos={mc.norm_code("OTH 1")})
        res = _res(_album(), _cand(id="mb-2012", year=2012, date="2012-05-30"), other)
        self.assertEqual(res["best"]["cand"]["id"], "mb-2012")
        self.assertFalse(any("DAT-3" in w for w in res["why"]), res["why"])


EAC_EN = """EAC extraction logfile from 27. August 2005, 15:28 for CD
Band / Flight

Used drive  : PLEXTOR DVDR   PX-712A   Adapter: 1  ID: 0

Track  1
     Filename I:\\Band - Flight\\01 - Morning song .wav

     Peak level 95.5 %
     Copy CRC 0C66B341
     Copy OK

Track  2
     Filename I:\\Band - Flight\\02 - Glass garden.wav

     Copy CRC 953EBBF8
     Copy OK

Track  3
     Filename I:\\Band - Flight\\03 - Il Ritorno.wav

     Copy CRC C8D14F0F
     Copy OK

Track  4
     Filename I:\\Band - Flight\\04 - Pressure point.wav

     Copy CRC BC9D054D
     Copy OK

Track  5
     Filename I:\\Band - Flight\\05 - Last waltz.wav

     Copy CRC F9811762
     Copy OK

No errors occured
"""

EAC_V1 = """Exact Audio Copy V1.0 beta 3 from 29. August 2011

EAC extraction logfile from 4. March 2012, 20:12

Band / Flight
"""

EAC_DE = "EAC Auslese-Logdatei vom 27. August 2005, 15:28 für CD\nBand / Flight\n"
EAC_RU = "Отчёт EAC об извлечении, выполненном 27. августа 2005, 15:28\n\nBand / Flight\n"

XLD = """X Lossless Decoder version 20080812 (149.2)

XLD extraction logfile from 2008-09-24 22:07:47 +0900

Band / Flight

Track 01
    Filename : /Users/me/Music/Band - Flight/01 Morning Song.flac
"""

AUCDTECT = """----------------------
DON'T MODIFY THIS FILE
PERFORMER: auCDtect Task Manager, ver. 1.5.1 Copyright (c) 2008-2010 y-soft
FILE: 01 - Morning Song.flac
     Conclusion: CDDA 100%
"""

ACCURIP = """[CUETools log; Date: 21.02.2012 14:09:24; Version: 2.1.2a]
Track Peak [ CRC32  ] [W/O NULL] [  LOG   ]
 --   98,8 [2CC86B7D] [CBB1F689]
 01   95,5 [0C66B341] [20B11D22]   CRC32
 02   97,3 [953EBBF8] [42FF2755]   CRC32
"""


def _infos(titles=TITLES, stems=None):
    stems = stems or [f"{i + 1:03d}.X" for i in range(len(titles))]
    return [(Path(f"/x/{s}.flac"), {"disc_eff": 1, "num": i + 1, "title": t})
            for i, (t, s) in enumerate(zip(titles, stems))]


class DAT3LogDates(unittest.TestCase):
    def test_DAT3_eac_dates_in_its_languages(self):
        self.assertEqual(ma.log_date_year(EAC_EN), 2005)
        self.assertEqual(ma.log_date_year(EAC_DE), 2005)
        self.assertEqual(ma.log_date_year(EAC_RU), 2005)

    def test_DAT3_eac_1_build_date_is_not_the_rip_date(self):
        self.assertEqual(ma.log_date_year(EAC_V1), 2012)
        self.assertIsNone(ma.log_date_year("Exact Audio Copy V1.0 beta 3 from 29. August 2011\n\nBand / Flight\n"))

    def test_DAT3_xld_date(self):
        self.assertEqual(ma.log_date_year(XLD), 2008)
        self.assertEqual(ma.log_track_names(XLD), {1: "01 Morning Song"})

    def test_DAT3_a_checker_log_has_no_rip_date(self):
        self.assertIsNone(ma.log_date_year(AUCDTECT))
        self.assertIsNone(ma.log_date_year("EAC extraction logfile from 27. August 2099, 15:28"))

    def test_DAT3_eac_track_names(self):
        names = ma.log_track_names(EAC_EN)
        self.assertEqual(names[1], "01 - Morning song ")
        self.assertEqual(len(names), 5)


class DAT3LogOfYourFiles(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def year(self, infos):
        logs = ma.rip_logs(self.dir)
        kept, _foreign = ma.fitting_logs(infos, logs)
        return ma.rip_log_year(self.dir, kept, infos)

    def test_DAT3_a_log_naming_your_files_track_by_track(self):
        (self.dir / "rip.log").write_text(EAC_EN.replace("\n", "\r\n"), encoding="utf-8")
        self.assertEqual(self.year(_infos()), (2005, "rip.log"))

    def test_DAT3_a_log_without_toc_not_tied_to_your_files_says_nothing(self):
        (self.dir / "rip.log").write_text(EAC_EN, encoding="utf-8")
        other = ["Another", "Album", "Entirely", "Different", "Songs"]
        self.assertEqual(self.year(_infos(other)), (None, ""))
        self.assertEqual(self.year(_infos()[:4]), (None, ""))          # (four files, five tracks)

    def test_DAT3_a_cuetools_report_ties_the_log(self):
        (self.dir / "rip.log").write_text(EAC_EN, encoding="utf-8")
        (self.dir / "rip.accurip").write_text(ACCURIP, encoding="utf-8")
        other = ["Another", "Album", "Entirely", "Different", "Songs"]
        self.assertEqual(self.year(_infos(other)), (2005, "rip.log"))

    def test_DAT3_a_checker_log_never_counts(self):
        (self.dir / "check.log").write_text(AUCDTECT, encoding="utf-8")
        self.assertEqual(self.year(_infos()), (None, ""))

    def test_DAT3_utf16_log(self):
        (self.dir / "rip.log").write_bytes(EAC_EN.encode("utf-16"))
        self.assertEqual(self.year(_infos()), (2005, "rip.log"))


if __name__ == "__main__":
    unittest.main()
