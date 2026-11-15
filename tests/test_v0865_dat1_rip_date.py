"""DAT-3 (asked for as "DAT-1"; that ID is Date / Original Date): a rip log of your files dated
in one year rules out a pressing first released in a later year (Pekka Pohjola - Flight Of The
Angel: an EAC log of 27 August 2005 against MusicBrainz's 2012 Finnish entry sharing the 2002
remaster's barcode and PELPCD 4). It only chooses among candidates: nothing is held for the
date alone. Synthetic logs and candidates only."""
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
ALL_LATER = "your rip log is dated 2005; every release found came out later: the log's date or the database may be wrong"


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


# nothing of your copy's numbers: no barcode, no catalogue number, a folder naming none
NOB = dict(barcodes=set(), ocr_barcodes=[], catnos=set(), catnos_from_folder=set(), folder="Flight")
CFG = dict(mc.DEFAULT_CFG, preferred_countries=["FI"])


def _res(album, *cands):
    scored = [mc.score_candidate(album, c, CFG) for c in cands]
    return dict(mc.classify(scored, CFG), scored=scored, source=cands[0]["source"])


def _p303(album=None):
    """The 2012 entry first and fuller (a day-exact date), as MusicBrainz lists them."""
    a = album or _album()
    return _res(a, _cand(id="mb-2012", year=2012, date="2012-05-30"), _cand(id="mb-2002"))


def _no_drop(res):
    return not any(mc.rip_dated_out(s) for s in res["scored"])


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
        self.assertTrue(_no_drop(res))
        self.assertFalse(any("DAT-3" in n for n in res["best"]["notes"]))

    def test_DAT3_the_same_year_is_not_later(self):
        res = _res(_album(rip_log_year=2012), _cand(id="mb-2012", year=2012, date="2012-05-30"), _cand(id="mb-2002"))
        self.assertTrue(_no_drop(res))

    def test_DAT3_an_undated_release_is_not_ruled_out(self):
        undated = _cand(id="mb-undated", year=None, date="")
        self.assertEqual(mc.rip_date_against(_album(), undated), "")
        res = _res(_album(), _cand(id="mb-2012", year=2012, date="2012-05-30"), undated)
        self.assertEqual(res["best"]["cand"]["id"], "mb-undated")
        self.assertEqual([s["cand"]["id"] for s in res["scored"] if mc.rip_dated_out(s)], ["mb-2012"])

    def test_DAT3_the_release_groups_first_date_does_not_count(self):
        # (the album's first release, 1986, is before the log; the pressing's own 2012 is not)
        c = _cand(id="mb-2012", year=2012, date="2012-05-30", original_year=1986)
        self.assertTrue(mc.rip_date_against(_album(), c))
        self.assertEqual(mc.pressing_own_year(_cand(year=None, date="2012-05")), 2012)

    def test_DAT3_every_candidate_later_is_never_held_for_the_date(self):
        # B: two pressings sharing your barcode, both later than the log: as without the log,
        # with the note
        later = (_cand(id="mb-2012", year=2012, date="2012-05-30"), _cand(id="mb-2010", year=2010, date="2010"))
        res, plain = _res(_album(), *later), _res(_album(rip_log_year=None), *later)
        self.assertEqual((res["status"], res["best"]["cand"]["id"]), (plain["status"], plain["best"]["cand"]["id"]))
        self.assertFalse(any("DAT-3" in w for w in res["why"]), res["why"])
        self.assertTrue(any(n.startswith(ALL_LATER) for n in res["best"]["notes"]), res["best"]["notes"])
        self.assertEqual(len(res["scored"]), 2)
        self.assertTrue(_no_drop(res))

    def test_DAT3_one_pressing_listed_twice_both_later(self):
        # B2: the same pressing entered twice, both later: as without the log
        twice = (_cand(id="mb-2012a", year=2012, date="2012"), _cand(id="mb-2012b", year=2012, date="2012", country="XE"))
        res, plain = _res(_album(), *twice), _res(_album(rip_log_year=None), *twice)
        self.assertEqual((res["status"], res["best"]["cand"]["id"]), (plain["status"], plain["best"]["cand"]["id"]))
        self.assertFalse(any("DAT-3" in w for w in res["why"]), res["why"])

    def test_DAT3_no_clue_against_unless_left_out(self):
        # (TAG-1, the tie pools and BAR-12 read clues_against: the date is one only when it leaves
        # the pressing out)
        res = _res(_album(), _cand(id="mb-2012", year=2012, date="2012-05-30"))
        self.assertEqual(res["best"]["clues_against"], [])
        self.assertTrue(res["best"]["rip_date_later"])


class DAT3LoneLaterRelease(unittest.TestCase):
    """A, E, F: the only release found is later than the log: kept, as without the log, told."""

    def check(self, album, **kw):
        res = _res(album, _cand(id="mb-2012", year=2012, date="2012", **kw))
        plain = _res(dict(album, rip_log_year=None), _cand(id="mb-2012", year=2012, date="2012", **kw))
        self.assertEqual(res["status"], plain["status"])
        self.assertEqual((res["status"], res["best"]["cand"]["id"]), ("auto", "mb-2012"), res["why"])
        self.assertTrue(any(n.startswith(ALL_LATER) for n in res["best"]["notes"]), res["best"]["notes"])

    def test_DAT3_a_lone_barcode_match_is_unaffected(self):
        self.check(_album())

    def test_DAT3_a_lone_exact_disc_id_release(self):                  # A
        self.check(_album(toc_releases={"mb-2012"}, exact_toc_releases={"mb-2012"}, **NOB),
                   barcodes=set(), catnos=set())

    def test_DAT3_a_lone_release_with_nothing_of_yours(self):          # E
        self.check(_album(**NOB), barcodes=set(), catnos=set())

    def test_DAT3_a_lone_release_with_your_catalogue_number(self):     # F
        self.check(_album(barcodes=set(), ocr_barcodes=[]), barcodes=set())


class DAT3Guards(unittest.TestCase):
    def test_DAT3_your_barcode_beats_an_earlier_pressing_without_it(self):
        other = _cand(id="mb-other", barcodes={"5000000000001"}, catnos={mc.norm_code("OTH 1")})
        res = _res(_album(), _cand(id="mb-2012", year=2012, date="2012-05-30"), other)
        self.assertEqual(res["best"]["cand"]["id"], "mb-2012")
        self.assertFalse(any("DAT-3" in w for w in res["why"]), res["why"])

    def test_DAT3_an_earlier_vinyl_never_takes_a_cd_rips_place(self):
        # C: the LP of 1986 is not your CD rip (whole CD sectors against it): it leaves nothing
        # out, and the CD carrying your exact disc ID stays the pick
        a = _album(toc_releases={"mb-2008"}, exact_toc_releases={"mb-2008"}, audio={"cd_frames": True}, **NOB)
        res = _res(a, _cand(id="mb-2008", year=2008, date="2008", barcodes=set(), catnos=set()),
                   _cand(id="mb-lp", year=1986, date="1986", formats={"vinyl"}, barcodes=set(), catnos=set()))
        self.assertEqual((res["status"], res["best"]["cand"]["id"]), ("auto", "mb-2008"), res["why"])
        self.assertEqual(len(res["scored"]), 2)
        self.assertTrue(_no_drop(res))
        self.assertTrue(any("DAT-3" in n for n in res["best"]["notes"]), res["best"]["notes"])

    def test_DAT3_your_disc_id_on_the_later_pressing_alone_keeps_it(self):
        # D: a 2008 CD carrying your exact disc ID against a 1990 CD without it: the disc ID
        # stands, the date only told
        a = _album(toc_releases={"mb-2008"}, exact_toc_releases={"mb-2008"}, **NOB)
        res = _res(a, _cand(id="mb-2008", year=2008, date="2008", barcodes=set(), catnos=set()),
                   _cand(id="mb-1990", year=1990, date="1990", barcodes=set(), catnos=set()))
        self.assertEqual((res["status"], res["best"]["cand"]["id"]), ("auto", "mb-2008"), res["why"])
        self.assertTrue(any("your disc ID is on it" in n and "DAT-3" in n for n in res["best"]["notes"]),
                        res["best"]["notes"])
        self.assertTrue(_no_drop(res))

    def test_DAT3_a_log_dated_before_the_album_existed_is_misdated(self):
        # (a PC clock set to 1999 for an album first out in 2002: the log says nothing)
        cands = (_cand(id="mb-2012", year=2012, date="2012-05-30", original_year=2002),
                 _cand(id="mb-2002", original_year=2002))
        album = _album(rip_log_year=1999, year=2002, folder="2002 - Flight")
        res, plain = _res(album, *cands), _res(dict(album, rip_log_year=None), *cands)
        self.assertEqual((res["status"], res["best"]["cand"]["id"]), (plain["status"], plain["best"]["cand"]["id"]))
        self.assertTrue(_no_drop(res))
        self.assertEqual(len(res["scored"]), 2)
        later = next(s for s in res["scored"] if s["cand"]["id"] == "mb-2012")
        self.assertIn("before the album's first release (2002)", later["rip_date_kept"])
        # (without the release group's year, the folder's or tags' year is the album's)
        cands = (_cand(id="mb-2012", year=2012, date="2012-05-30"), _cand(id="mb-2002"))
        res, plain = _res(album, *cands), _res(dict(album, rip_log_year=None), *cands)
        self.assertEqual((res["status"], res["best"]["cand"]["id"]), (plain["status"], plain["best"]["cand"]["id"]))
        self.assertTrue(_no_drop(res))
        # (a log of 2003 for the same album is no misdated one: the 2012 entry is left out)
        res = _res(dict(album, rip_log_year=2003), *cands)
        self.assertEqual([s["cand"]["id"] for s in res["scored"] if mc.rip_dated_out(s)], ["mb-2012"])


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

# a CUETools report of another rip: its CRCs are not the log's
ACCURIP_OTHER = ACCURIP.replace("0C66B341", "11111111").replace("953EBBF8", "22222222")


def _infos(titles=TITLES):
    return [(Path(f"/x/{i + 1:03d}.flac"), {"disc_eff": 1, "num": i + 1, "title": t})
            for i, t in enumerate(titles)]


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

    def test_DAT3_a_checker_log_has_no_rip_date(self):
        self.assertIsNone(ma.log_date_year(AUCDTECT))
        self.assertIsNone(ma.log_date_year("EAC extraction logfile from 27. August 2099, 15:28"))


class DAT3LogOfYourFiles(unittest.TestCase):
    """Only a TOC that fits, or a CUETools report confirming the log's CRCs, ties a log to your
    files; a log merely naming files like yours may be another rip's."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def year(self, infos=None):
        infos = infos or _infos()
        kept, _foreign = ma.fitting_logs(infos, ma.rip_logs(self.dir))
        return ma.rip_log_year(self.dir, kept)

    def test_DAT3_another_rips_log_named_alike_sets_no_ceiling(self):
        # (track by track the same titles as your files, no TOC, no CUETools report)
        (self.dir / "rip.log").write_text(EAC_EN.replace("\n", "\r\n"), encoding="utf-8")
        self.assertEqual(self.year(), (None, ""))
        (self.dir / "rip.accurip").write_text(ACCURIP_OTHER, encoding="utf-8")
        self.assertEqual(self.year(), (None, ""))

    def test_DAT3_a_cuetools_report_ties_the_log(self):
        (self.dir / "rip.log").write_text(EAC_EN, encoding="utf-8")
        (self.dir / "rip.accurip").write_text(ACCURIP, encoding="utf-8")
        self.assertEqual(self.year(), (2005, "rip.log"))

    def test_DAT3_a_toc_that_fits_ties_the_log(self):
        sectors = [round(s * 75) for s in SECS]
        rows, pos = [], 0
        for n, s in enumerate(sectors, 1):
            rows.append(f"        {n}  |  0:00.00 |  0:00.00 |    {pos:6d}    |   {pos + s - 1:6d}   ")
            pos += s
        log = (EAC_EN.split("\nTrack  1")[0] + "\nTOC of the extracted CD\n\n"
               "     Track |   Start  |  Length  | Start sector | End sector \n" + "\n".join(rows) + "\n")
        (self.dir / "rip.log").write_text(log, encoding="utf-8")
        infos = [(p, dict(i, secs=s)) for (p, i), s in zip(_infos(), SECS)]
        self.assertEqual(self.year(infos), (2005, "rip.log"))
        other = [(p, dict(i, secs=s + 30)) for (p, i), s in zip(_infos(), SECS)]
        self.assertEqual(self.year(other), (None, ""))                 # (a TOC of another disc)

    def test_DAT3_a_checker_log_never_counts(self):
        (self.dir / "check.log").write_text(AUCDTECT, encoding="utf-8")
        (self.dir / "rip.accurip").write_text(ACCURIP, encoding="utf-8")
        self.assertEqual(self.year(), (None, ""))

    def test_DAT3_utf16_log(self):
        (self.dir / "rip.log").write_bytes(EAC_EN.encode("utf-16"))
        (self.dir / "rip.accurip").write_text(ACCURIP, encoding="utf-8")
        self.assertEqual(self.year(), (2005, "rip.log"))


if __name__ == "__main__":
    unittest.main()
