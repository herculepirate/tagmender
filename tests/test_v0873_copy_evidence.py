#!/usr/bin/env python3
"""0.8.73: identification by what your copy itself shows (held-out validation of 0.8.72).

Synthetic fixtures only, modelled on the layouts the validation met (no network, no music
library): MED-2 a hi-res download of an album listed on one CD only, TIE-1/FLD-1 an album
title that reads as a country on the scans, CAT-7 a folder number inside the release's longer
one, BAR-8 a box's per-disc number printed on the disc, MED-13 an SACD extraction log's
catalogue number."""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_match import album, cand, CFG  # noqa: E402  (isolates XDG dirs and Picard.ini first)
import match_albums as ma  # noqa: E402
import match_core as mc  # noqa: E402

TAGS_CODE, BOX_CODE = "196500000322", "196500000414"
HIRES = {"hires": True, "sacd": False, "cd_frames": False, "not_cd": False}     # (as real 24/96 audio reads)


def tracks(*secs, disc=1):
    return [{"disc": disc, "num": i + 1, "title": f"T{disc}{i}", "secs": float(s)} for i, s in enumerate(secs)]


def ctracks(*secs, disc=1):
    return [{"disc": disc, "title": f"T{disc}{i}", "secs": s} for i, s in enumerate(secs)]


def res(a, cands, ocr="", source=None):
    scored = [mc.score_candidate(a, c, CFG, ocr) for c in cands]
    return dict(mc.classify(scored, CFG), scored=scored, source=source or cands[0]["source"])


class MED2_HiResOfAnAlbumListedOnOneCd(unittest.TestCase):
    """MED-2: 24/96 files of an album whose only pressing listed is a 2CD (The Flower Kings -
    Stardust We Are, 2022 Inside Out, MusicBrainz only CD): the album is known from that CD,
    never "not on MusicBrainz or Discogs" from a web store."""

    def hires_album(self, **kw):
        a = album(discs=2, tracks=tracks(634, 87, 475, disc=1) + tracks(80, 511, 1510, disc=2),
                  audio=HIRES, barcodes={TAGS_CODE}, tag_barcodes={TAGS_CODE}, tags_by_tagger=True,
                  tag_values_by_tagger=True)
        a.update(kw)
        return a

    def cd(self, **kw):
        c = cand(id="cd2", disc_count=2, formats={"cd"}, barcodes={TAGS_CODE},
                 tracks=ctracks(634, 87, 475, disc=1) + ctracks(80, 511, 1510, disc=2))
        c.update(kw)
        return c

    def test_MED2_one_cd_listed_album_known(self):
        r = res(self.hires_album(), [self.cd()])
        self.assertEqual(r["status"], "review")
        self.assertIn("hi-res audio cannot come from this CD", r["why"])
        known = mc.album_only(r, CFG)
        self.assertIsNotNone(known)
        self.assertEqual([t["id"] for t in known["tied"]], ["cd2"])
        self.assertEqual(known["media"], "hi-res audio cannot come from this CD")

    def test_MED2_nothing_of_the_one_pressing_is_written(self):
        import match_albums as ma_
        a = self.hires_album()
        r = res(a, [self.cd(label="Inside Out", country="Europe", year=2022)])
        known = mc.album_only(r, CFG, ma_.tie_source(a, r))
        self.assertTrue(known["other_kind"])
        tags = {"album": ["Stardust We Are"], "media": ["CD"], "label": ["Inside Out"], "releasecountry": ["XE"],
                "date": ["2022-03-04"], "originaldate": ["1997"], "tracknumber": ["1"], "totaltracks": ["10"],
                "discnumber": ["1"], "totaldiscs": ["2"]}
        kept = mc.album_only_tags(tags, known["tied"], other_kind=known["other_kind"], media_held=bool(known["media"]))
        self.assertEqual(kept, {"album": ["Stardust We Are"], "date": ["1997"], "originaldate": ["1997"]})

    def test_MED2_another_album_as_close_holds_it(self):
        # (another album, its title apart but within the margin: "Firstlight Sessions" at 93.1)
        r = res(self.hires_album(), [self.cd(), dict(self.cd(id="sessions"), title="Firstlight Sessions")])
        self.assertIsNone(mc.album_only(r, CFG))

    def test_MED2_a_download_listed_is_weighed_instead(self):
        r = res(self.hires_album(), [self.cd(formats={"cd", "digital"})])
        self.assertIsNone(mc.album_only(r, CFG))

    def test_MED2_another_doubt_still_holds_it(self):
        # (the lengths of another album: not known by the media rule)
        r = res(self.hires_album(), [self.cd(tracks=ctracks(300, 300, 300, disc=1) + ctracks(300, 300, 300, disc=2))])
        self.assertIsNone(mc.album_only(r, CFG))

    def test_MED2_media_alone_ranks_over_a_conflicting_low_pick(self):
        a = self.hires_album()
        mb = res(a, [self.cd()])
        # Discogs: the CD + vinyl set, another barcode than the tags' (another product), its
        # lengths off on a third of the tracks
        dg = res(a, [self.cd(source="discogs", id="box", formats={"cd", "vinyl"}, barcodes={BOX_CODE},
                             tracks=ctracks(634, 95, 475, disc=1) + ctracks(88, 511, 1510, disc=2))],
                 source="discogs")
        self.assertTrue(dg["best"].get("conflict"))
        out = mc.combine(mb, dg)
        self.assertEqual(out["best"]["cand"]["id"], "cd2")
        self.assertIsNotNone(mc.album_only(out, CFG))


class FLD1_AlbumTitleIsNoCountryOnTheScans(unittest.TestCase):
    """FLD-1, TIE-1: "Made in Japan" printed on the scans as the album's title (Deep Purple, the
    1998 EU remaster's back) names no country: never "pressing chosen by made in Japan"."""

    SCANS = "DEEP PURPLE\nMADE IN JAPAN\nThe Remastered Edition\nMade in the EU\n"

    def test_FLD1_title_made_in_is_no_country(self):
        jp = cand(id="jp", title="Made In Japan", country="Japan")
        agree, disagree = mc.pressing_clues(self.SCANS, jp)
        self.assertNotIn("country", agree)
        # (and the sleeve's own "Made in the EU" still speaks: against the Japanese pressing)
        self.assertTrue(any("made in XE" in d for d in disagree), disagree)

    def test_FLD1_the_eu_line_still_agrees_with_the_eu_pressing(self):
        eu = cand(id="eu", title="Made In Japan (The Remastered Edition)", country="Europe")
        agree, _ = mc.pressing_clues(self.SCANS, eu)
        self.assertIn("EU", agree.get("country", ""))

    def test_FLD1_a_japanese_entry_titled_otherwise_gets_no_clue(self):
        # (Discogs: "Made In Japan = ライヴ・イン・ジャパン", "メイド・イン・ジャパン"; the copy the 1998 EU
        # remaster, its back printing the title and "Made in the EU")
        self.assertEqual(mc.countries_made_in("Made In Japan = ライヴ・イン・ジャパン"), {"JP"})
        a = album(title="Made in Japan")
        cands = [cand(id="jp1", title="Made In Japan = ライヴ・イン・ジャパン", country="Japan"),
                 cand(id="jp2", title="メイド・イン・ジャパン", country="Japan"),
                 cand(id="eu", title="Made In Japan", country="Europe")]
        mc.note_titles(a, cands)
        for c in cands:
            s = mc.score_candidate(a, c, CFG, "DEEP PURPLE\nMADE IN JAPAN\nMade in the EU\n")
            self.assertNotIn("made in Japan", str(s["clues"].get("country", "")), c["id"])
        r = res(a, cands, "DEEP PURPLE\nMADE IN JAPAN\nMade in the EU\n")
        self.assertNotIn(r["best"]["cand"]["id"], ("jp1", "jp2"))

    def test_FLD1_another_title_keeps_its_made_in(self):
        jp = cand(id="jp", title="Live In Tokyo", country="Japan")
        agree, _ = mc.pressing_clues("LIVE IN TOKYO\nMade in Japan\n", jp)
        self.assertIn("country", agree)


class CAT7_FolderNumberInsideTheReleasesLongerOne(unittest.TestCase):
    """CAT-7: "570 934-4" (the folder) is Universal's "0 6025 570 934-4 5" (Discogs) - whole
    groups, in order; a set's disc's own number listed among the release's other identifiers
    agrees as its catalogue number (Enigma - The Fall Of A Rebel Angel, disc 2 tagged
    0602557093414)."""

    def test_CAT7_whole_groups_inside(self):
        self.assertTrue(mc.catno_groups_inside("570 934-4", "0 6025 570 934-4 5"))
        self.assertTrue(mc.catno_groups_inside("570934-4 5", "0 6025 570934 4 5"))

    def test_CAT7_guards(self):
        self.assertFalse(mc.catno_groups_inside("70 934-4", "0 6025 570 934-4 5"))   # a piece of a group
        self.assertFalse(mc.catno_groups_inside("934-4", "0 6025 570 934-4 5"))      # one group
        self.assertFalse(mc.catno_groups_inside("12 34", "0 12 34 5"))                # under six digits
        self.assertFalse(mc.catno_groups_inside("570 934-4", "570 934-4"))           # the whole number: catno_same's
        self.assertFalse(mc.catno_groups_inside("SICP 10151", "SICP 10151~4"))
        self.assertFalse(mc.catno_groups_inside("570 934-4", "0 6025 570 934-5 5"))

    def folder_album(self):
        return album(discs=1, catnos={"5709344"}, catnos_from_folder={"5709344"}, catno_search=["570 934-4"])

    def test_CAT7_folder_number_agrees(self):
        c = cand(id="deluxe", source="discogs", catnos={mc.norm_code("0 6025 570 934-4 5")},
                 catno_shown="0 6025 570 934-4 5")
        s = mc.score_candidate(self.folder_album(), c, CFG)
        self.assertEqual(s["code"], "catalogue number (folder name)")
        self.assertTrue(s["folder_catno_agrees"])
        self.assertFalse(s["folder_catno_absent"])
        self.assertFalse(s["own_catno_absent"])

    def test_CAT7_another_number_still_differs(self):
        c = cand(id="std", source="discogs", catnos={mc.norm_code("0 6025 570 933-9 1")},
                 catno_shown="0 6025 570 933-9 1")
        s = mc.score_candidate(self.folder_album(), c, CFG)
        self.assertEqual(s["code"], "")
        self.assertTrue(s["folder_catno_absent"])

    def tagged_album(self):
        return album(catnos={"602557093414"}, tag_catnos={"602557093414"}, catno_search=["0602557093414"])

    def test_CAT7_tags_number_among_the_other_identifiers(self):
        c = cand(id="deluxe", source="discogs", catnos={"602557093445"}, catno_shown="0 6025 570 934-4 5",
                 other_codes={"602557093407", "602557093414"})
        s = mc.score_candidate(self.tagged_album(), c, CFG)
        self.assertEqual(s["code"], "catalogue number")
        self.assertNotIn("catalogue number differs", s["notes"])
        self.assertFalse(s["own_catno_absent"])

    def test_CAT7_without_it_the_number_differs(self):
        c = cand(id="deluxe", source="discogs", catnos={"602557093445"}, catno_shown="0 6025 570 934-4 5")
        s = mc.score_candidate(self.tagged_album(), c, CFG)
        self.assertIn("catalogue number differs", s["notes"])

    def test_CAT7_short_other_identifiers_never_count(self):
        self.assertFalse(mc.distinctive_number("2016"))
        self.assertFalse(mc.distinctive_number("123456789"))
        self.assertTrue(mc.distinctive_number("602557093414"))
        self.assertTrue(mc.distinctive_number("sicl10001"))


class BAR4_MisreadOfTheBoxNumberIsNoBarcode(unittest.TestCase):
    """BAR-4, BAR-8: OCR's "88007200550 5" for disc 5's printed "88697890552-5" passes a UPC's
    check digit and is on no release: beside the release's own number of digits only, printed
    whole, it is ignored as a sticker is (The Alan Parsons Project - Complete Albums
    Collection)."""

    MISREAD = "880072005505"
    OCR = "THE TURN OF A FRIENDLY CARD\nLOOSE. 88007200550 5\n88697890552-5\n"

    def box(self):
        return cand(id="box", catnos={"88697890552"}, barcodes={"886978905529"})

    def test_BAR4_misread_beside_the_printed_number_is_ignored(self):
        a = album(barcodes={self.MISREAD}, unlisted_barcodes={self.MISREAD})
        s = mc.score_candidate(a, self.box(), CFG, self.OCR)
        self.assertFalse(s["conflict"])
        self.assertTrue(any("own number 88697890552 is printed whole on your copy" in n for n in s["notes"]), s["notes"])
        self.assertFalse(any("shop sticker" in n for n in s["notes"]), s["notes"])

    def test_BAR4_without_the_printed_number_it_differs(self):
        a = album(barcodes={self.MISREAD}, unlisted_barcodes={self.MISREAD})
        s = mc.score_candidate(a, self.box(), CFG, "THE TURN OF A FRIENDLY CARD\n88007200550 5\n")
        self.assertTrue(s["conflict"])

    def test_BAR4_the_start_of_a_longer_printed_run_is_not_printed(self):
        self.assertTrue(mc.digits_printed("88697890552-5", "88697890552"))
        self.assertTrue(mc.digits_printed("88697 89055 2", "88697890552"))
        self.assertFalse(mc.digits_printed("88697890552 1234", "88697890552"))
        self.assertFalse(mc.digits_printed("88697890552-5-7", "88697890552"))

    def test_BAR4_a_piece_of_a_longer_run_is_not_printed(self):
        a = album(barcodes={self.MISREAD}, unlisted_barcodes={self.MISREAD})
        s = mc.score_candidate(a, self.box(), CFG, "88007200550 5\n4886978905521\n")
        self.assertTrue(s["conflict"])


class BOX4_ReplicaSleevesNameNoOtherPressing(unittest.TestCase):
    """BOX-4: a box's replica sleeves print each disc's original LP number ("KC 31996") and
    "Made in U.S.A.": a number only another album's releases carry names no other pressing of
    the box, and a Discogs pick your exact disc ID proves through its MusicBrainz entry (TOC-2),
    no other entry carrying it, is not made album-only by the sleeves' country (Mahavishnu
    Orchestra - The Complete Columbia Albums Collection)."""

    def pick(self, **kw):
        b = {"cand": cand(id="4649350", source="discogs", title="The Complete Columbia Albums Collection",
                          catnos={"88697930342"}, barcodes={"886979303423"}, country="Europe", disc_count=5),
             "clues": {"label": "label Columbia on the scans"}, "clues_against": [], "code": "", "confidence": 97.5,
             "tracks_ok": True}
        b.update(kw)
        return b

    def pick_tracks(self):
        return [{"disc": 1, "title": "Meeting Of The Spirits", "secs": 400}, {"disc": 2, "title": "Birds Of Fire",
                                                                             "secs": 345}]

    def lp(self, title="Birds Of Fire"):
        return {"cand": cand(id="2026637", source="discogs", title=title, catnos={"kc31996"}),
                "clues": {}, "confidence": 18.8}

    def test_BOX4_another_language_title_of_the_same_album_still_counts(self):
        best = self.pick()
        best["cand"]["tracks"] = self.pick_tracks()
        report = ma.scans_report(best, album(), "KC 31996\n", [{"scored": [best, self.lp("Die Kolumbia-Sammlung")]}],
                                 True)
        self.assertIn("catalogue_number", report["against"])

    def test_BOX4_another_albums_number_is_not_against(self):
        best = self.pick()
        best["cand"]["tracks"] = self.pick_tracks()
        report = ma.scans_report(best, album(), "BIRDS OF FIRE\nKC 31996\n", [{"scored": [best, self.lp()]}], True)
        self.assertNotIn("catalogue_number", report["against"])

    def test_BOX4_another_pressing_of_the_box_still_is(self):
        best = self.pick()
        other = {"cand": cand(id="9", source="discogs", title="The Complete Columbia Albums Collection",
                              catnos={"kc31996"}), "clues": {}, "confidence": 60.0}
        report = ma.scans_report(best, album(), "KC 31996\n", [{"scored": [best, other]}], True)
        self.assertIn("catalogue_number", report["against"])

    def test_BOX4_disc_id_through_musicbrainz_keeps_the_discogs_pick(self):
        best = self.pick(clues={"disc TOC": "disc ID (track positions of the CD) matches"},
                         clues_against=["scans say made in US, this pressing is from Europe"])
        report = {"state": "differs", "found": ["label"], "against": ["country"]}
        self.assertIsNone(mc.scans_name_another({"status": "auto", "best": best, "scored": [best]}, report))

    def test_BOX4_another_pressing_agreeing_with_the_scans_keeps_them(self):
        # (a US pressing of the same box, its "Made in USA" on the scans: they may be its own)
        best = self.pick(clues={"disc TOC": "disc ID (track positions of the CD) matches"})
        us = self.pick(cand=cand(id="222", source="discogs", title="The Complete Columbia Albums Collection",
                                 country="US", disc_count=5), clues={"country": "made in US on the scans"})
        report = {"state": "differs", "found": ["label"], "against": ["country"]}
        self.assertIsNotNone(mc.scans_name_another({"status": "auto", "best": best, "scored": [best, us]}, report))

    def test_BOX4_a_single_disc_keeps_the_old_way(self):
        best = self.pick(clues={"disc TOC": "disc ID (track positions of the CD) matches"})
        best["cand"] = dict(best["cand"], disc_count=1)
        report = {"state": "differs", "found": ["label"], "against": ["country"]}
        self.assertIsNotNone(mc.scans_name_another({"status": "auto", "best": best, "scored": [best]}, report))

    def test_BOX4_disc_id_two_entries_carry_tells_neither(self):
        best = self.pick(clues={"disc TOC": "disc ID (track positions of the CD) matches"})
        twin = self.pick(cand=cand(id="777", source="discogs", title="The Complete Columbia Albums Collection"),
                         clues={"disc TOC": "disc ID (track positions of the CD) matches"})
        report = {"state": "differs", "found": ["label"], "against": ["country"]}
        self.assertIsNotNone(mc.scans_name_another({"status": "auto", "best": best, "scored": [best, twin]}, report))


class MED13_SacdLogNamesTheDiscsNumber(unittest.TestCase):
    """MED-13, CAT-7: the catalogue number sacd_extract reads off the SACD's master TOC
    ("Disc Information: ... Catalog Number: SICL-10001") is your copy's own number: the version
    folders beside the log read it too (Kodo - One Earth Tour Special: the only SACD pressing,
    never tied with the DVD + CD set)."""

    LOG = ("Disc Information:\r\n\r\n\tVersion:  1.20\r\n\tCreation date: 2004-02-12\r\n"
           "\tCatalog Number: SICL-10001      \r\n\tTitle: One Earth\r\n\r\nAlbum Information:\r\n\r\n"
           "\tCatalog Number: \r\n\tSet Size: 1\r\n\r\nArea count: 2\r\n\tSpeaker config: 2 Channel\r\n")

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.disc = self.tmp / "Artist - One Earth (2004) [SACD] (ISO)"
        for v in ("Stereo", "Multichannel"):
            (self.disc / v).mkdir(parents=True)
        (self.disc / "sacd_log.txt").write_bytes(self.LOG.encode())

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_MED13_log_number_read(self):
        self.assertEqual(ma.sacd_disc_catnos(self.disc, self.disc.name), ["SICL-10001"])

    def test_MED13_version_folders_read_the_log_beside_them(self):
        self.assertEqual(ma.sacd_disc_catnos(self.disc / "Stereo", "Stereo"), ["SICL-10001"])
        self.assertEqual(ma.sacd_disc_catnos(self.disc / "Multichannel", self.disc.name), ["SICL-10001"])

    def test_MED13_another_folder_does_not(self):
        other = self.tmp / "Other Album"
        other.mkdir()
        self.assertEqual(ma.sacd_disc_catnos(other, other.name), [])

    def test_MED13_a_filled_field_is_no_number(self):
        for junk in ("0000000000000000", "1111111111", "00-000-0000"):
            (self.disc / "sacd_log.txt").write_text(self.LOG.replace("SICL-10001", junk))
            self.assertEqual(ma.sacd_disc_catnos(self.disc, self.disc.name), [], junk)

    def test_MED13_no_disc_block_no_number(self):
        (self.disc / "sacd_log.txt").write_text("Album Information:\n\tCatalog Number: SICL-10001\nArea count: 2\n")
        self.assertEqual(ma.sacd_disc_catnos(self.disc, self.disc.name), [])

    def test_MED13_its_number_decides_against_the_dvd_set(self):
        a = album(catnos={"sicl10001"}, catno_search=["SICL-10001"], catnos_sacd_disc=["sicl10001"])
        r = res(a, [cand(id="sacd", catnos={"sicl10001"}, country="JP"),
                    cand(id="dvdcd", catnos={"82876760029"}, country="XE")])
        self.assertEqual((r["status"], r["best"]["cand"]["id"]), ("auto", "sacd"))


class TRK7_FeaturedArtistCreditsInTitles(unittest.TestCase):
    """TRK-7: "Circle Eight (feat. Nanuk)" in the tags is MusicBrainz's "Circle Eight" (Enigma -
    The Fall Of A Rebel Angel): a featured artist's credit on either side is left out of the
    titles' comparison - only those credit forms, never "(Live)" or "(Remix)"."""

    def test_TRK7_credit_forms(self):
        for a, b in (("Circle Eight (feat. Nanuk)", "Circle Eight"), ("Mother", "Mother (feat. Anggun)"),
                     ("Amen feat. Aquilo", "Amen"), ("Diving [ft. Someone]", "Diving"),
                     ("Agnus Dei (featuring A Choir)", "Agnus Dei"), ("Oxygen Red ft. Anggun", "Oxygen Red")):
            self.assertTrue(mc.track_titles_agree(a, b), (a, b))

    def test_TRK7_other_brackets_still_differ(self):
        for a, b in (("Song (Live)", "Song"), ("Song (Remix)", "Song"), ("Feat Of Clay", "Clay"),
                     ("Six Ft. Under", "Six"), ("Ten ft. tall", "Ten"), ("Walking 10 ft. Tall", "Walking 10")):
            self.assertFalse(mc.track_titles_agree(a, b), (a, b))

    def test_TRK7_titles_agree_counts_them(self):
        a = album(tracks=[{"disc": 1, "num": i + 1, "title": t, "secs": s} for i, (t, s) in enumerate(
            (("One (feat. X)", 200.0), ("Two", 305.0), ("Three feat. Y", 410.0)))])
        c = cand(tracks=[{"disc": 1, "title": t, "secs": s} for t, s in (("One", 200), ("Two", 305), ("Three", 410))])
        self.assertEqual(mc.score_candidate(a, c, CFG)["titles_agree"], 3)


class DSC12_YourNumberOnTheLongerRelease(unittest.TestCase):
    """DSC-12, _full_fit_first: a release holding just the files' tracks sends a longer one's
    slots back to a whole-release score - unless your copy's own number is on the longer one and
    on none of those (Enigma "The Album", tagged with the deluxe 2CD's disc 1 number)."""

    def scored(self, full_code="", slot_code="catalogue number"):
        full = {"tracks_ok": True, "dur_frac": 1.0, "code": full_code, "cand": {"id": "std"}}
        whole = {"tracks_ok": False, "code": slot_code, "cand": {"id": "deluxe"}}
        slots = {"tracks_ok": True, "dur_frac": 1.0, "code": slot_code, "missing": {2: [1, 2]},
                 "whole_view": whole, "cand": {"id": "deluxe"}}
        return [full, slots], whole, slots

    def test_DSC12_own_number_keeps_the_slots(self):
        sc, _whole, slots = self.scored()
        self.assertIs(mc._full_fit_first(sc, CFG)[1], slots)

    def test_DSC12_without_it_the_whole_release(self):
        sc, whole, _slots = self.scored(slot_code="")
        self.assertIs(mc._full_fit_first(sc, CFG)[1], whole)

    def test_DSC12_your_number_on_both_the_whole_release(self):
        sc, whole, _slots = self.scored(full_code="catalogue number")
        self.assertIs(mc._full_fit_first(sc, CFG)[1], whole)

    def test_DSC12_another_taggers_number_never_keeps_them(self):
        sc, whole, _slots = self.scored(slot_code=mc.TAGS_CATNO)
        self.assertIs(mc._full_fit_first(sc, CFG)[1], whole)


class BAR4_ASearchThatFailedIsNoUnlistedBarcode(unittest.TestCase):
    """BAR-4: a barcode the investigation read whose Discogs or MusicBrainz search failed was
    never looked up: never "on no release", never ignored as a sticker."""

    def investigate(self, mb=None, dg=None):
        from unittest import mock
        album_ = album(barcodes=set(), catno_search=[])
        state = {"deep": "", "tried": set(), "asked": set(), "titled": False, "codes": [], "strong": [],
                 "dg_ids": [], "mb_ids": [], "found": 0, "kept_text": " ", "rings": False}
        with mock.patch.object(ma, "mb_search", side_effect=mb or (lambda *a: [])), \
                mock.patch.object(ma, "discogs_search", side_effect=dg or (lambda *a: [])), \
                mock.patch.object(ma, "web_engine", return_value=("none", None)), \
                mock.patch.object(ma, "variant_release_links", return_value=([], [])):
            try:
                ma._investigate_codes(None, Path("/nonexistent"), album_, CFG, None, None, None,
                                      {"status": "review", "best": None}, "8 80072 00550 5", state, [], True, True)
            except RuntimeError:
                pass
        return album_

    def test_BAR4_discogs_failing_is_recorded(self):
        def boom(*a):
            raise RuntimeError("discogs down")
        self.assertTrue(self.investigate(dg=boom).get("investigation_search_failed"))

    def test_BAR4_musicbrainz_failing_is_recorded(self):
        def boom(*a):
            raise RuntimeError("mb down")
        self.assertTrue(self.investigate(mb=boom).get("investigation_search_failed"))

    def test_BAR4_both_answering_is_not(self):
        self.assertFalse(self.investigate().get("investigation_search_failed"))


if __name__ == "__main__":
    unittest.main()
