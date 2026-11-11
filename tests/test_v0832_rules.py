# MED-12 (docs/MATCHING_RULES.md): a release that is video only - the DVD of a concert, its songs
# listed as chapters without lengths (AC/DC - Live At Donington) - is never the source of a CD
# rip: tracks of whole CD sectors (or a CD rip log) argue against it, and a person decides.
# Audio that is not known to come off a CD (a restoration of the film's sound) is left alone.
# Synthetic candidates: no network.
import os as _os, tempfile as _tempfile  # noqa: E401
_os.environ["XDG_CONFIG_HOME"] = _tempfile.mkdtemp(prefix="tm-config-")
_os.environ["XDG_STATE_HOME"] = _tempfile.mkdtemp(prefix="tm-state-")
_os.environ.setdefault("TAGMENDER_TAGGER_INI", "/nonexistent")

import sys  # noqa: E402
import unittest  # noqa: E402
from pathlib import Path  # noqa: E402

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
import match_albums as ma  # noqa: E402
import match_core as mc  # noqa: E402

CFG = dict(mc.DEFAULT_CFG, preferred_countries=[])
SONGS = ["Thunderstruck", "Shoot to Thrill", "Back in Black", "Heatseeker", "Fire Your Guns"]


def release(formats):
    return {"id": 14821000, "title": "Live At Donington", "artists": [{"name": "AC/DC"}], "year": 2003,
            "country": "US", "formats": formats, "labels": [{"name": "Epic Music Video", "catno": "EVD 50366"}],
            "identifiers": [],
            "tracklist": [{"position": str(i + 1), "title": t, "duration": "", "type_": "track"}
                          for i, t in enumerate(SONGS)]}


def album(audio):
    return {"artist": "AC/DC", "title": "Live At Donington", "year": 1992, "discs": 1, "folder": "Live At Donington",
            "tracks": [{"disc": 1, "num": i + 1, "title": t, "secs": 300.0} for i, t in enumerate(SONGS)],
            "barcodes": set(), "catnos": set(), "audio": audio}


class MED12(unittest.TestCase):
    def setUp(self):
        self.dm = ma.load_plugin(str(HERE / "plugins_builtin" / "discogs"))

    def test_MED12_a_video_only_release_is_marked(self):
        self.assertTrue(ma.discogs_candidate(release([{"name": "DVD", "qty": "1", "descriptions": ["NTSC"]}]),
                                             self.dm)["video_only"])
        for formats in ([{"name": "CD", "qty": "2"}],
                        [{"name": "CD", "qty": "2"}, {"name": "DVD", "qty": "1"}],          # (a CD + DVD set)
                        [{"name": "DVD", "qty": "1", "descriptions": ["DVD-Audio"]}],       # (audio on a DVD)
                        [{"name": "Blu-ray", "qty": "1", "descriptions": ["Blu-ray Audio"]}]):
            self.assertFalse(ma.discogs_candidate(release(formats), self.dm)["video_only"], formats)

    def test_MED12_a_cd_rip_is_not_the_concert_dvd(self):
        c = ma.discogs_candidate(release([{"name": "DVD", "qty": "1"}]), self.dm)
        cd_rip = album({"cd_frames": True, "not_cd": False, "hires": False, "lossless": True})
        r = mc.score_candidate(cd_rip, c, CFG)
        self.assertTrue(any("video (DVD/Blu-ray) release" in a for a in r["clues_against"]))
        self.assertTrue(mc.format_contradictions(r))
        out = mc.classify([r], CFG)
        self.assertNotEqual(out["status"], "auto")
        self.assertTrue(any("video (DVD/Blu-ray)" in w for w in out["why"]))

    def test_MED12_a_dvd_rip_is_left_alone(self):
        c = ma.discogs_candidate(release([{"name": "DVD", "qty": "1"}]), self.dm)
        cd_rip = album({"cd_frames": True, "not_cd": False, "hires": False, "lossless": True})
        named = dict(cd_rip, folder="Live At Donington [DVD]")            # (the folder says so)
        self.assertFalse(mc.score_candidate(named, c, CFG)["clues_against"])
        pal = album({"cd_frames": True, "pal_frames": True, "not_cd": False, "hires": False, "lossless": True})
        self.assertFalse(mc.score_candidate(pal, c, CFG)["clues_against"])  # (whole PAL frames: a DVD's sound)
        # whole PAL frames: 1764 samples (3 CD sectors) on every track
        infos = [{"lossless": True, "rate": 44100, "bits": 16, "samples": 1764 * n} for n in (5000, 7001)]
        self.assertTrue(ma.audio_evidence(infos)["pal_frames"])
        infos[1]["samples"] = 588 * 7001
        self.assertTrue(ma.audio_evidence(infos)["cd_frames"])
        self.assertFalse(ma.audio_evidence(infos)["pal_frames"])

    def test_MED12_audio_not_known_to_come_off_a_cd_is_left_alone(self):
        c = ma.discogs_candidate(release([{"name": "DVD", "qty": "1"}]), self.dm)
        restored = album({"cd_frames": False, "not_cd": False, "hires": False, "lossless": True})
        self.assertFalse(mc.score_candidate(restored, c, CFG)["clues_against"])


class BOX3(unittest.TestCase):
    """BOX-3: one disc of a set whose release lists no lengths (Elvis Presley - FTD The Wonderful
    World of Christmas, "CD 1" alone): your barcode is the release's and that disc's titles are
    your files'."""
    D1 = ["O Come All Ye Faithful", "The First Noel", "On a Snowy Christmas Night"]
    D2 = ["Holly Leaves (take 3)", "Silver Bells (take 1)", "Winter Wonderland (take 2)"]

    def cand(self, d1_secs=None):
        tracks = [{"disc": 1, "title": t, "secs": d1_secs} for t in self.D1] + \
                 [{"disc": 2, "title": t, "secs": None} for t in self.D2]
        return {"source": "mb", "id": "e571", "title": "Elvis Sings the Wonderful World of Christmas",
                "barcodes": ["5060209750316"], "tracks": tracks}

    def album(self, titles, barcodes=("5060209750316",)):
        return {"title": "The Wonderful World Of Christmas 1", "barcodes": set(barcodes),
                "tracks": [{"disc": 1, "num": i + 1, "title": t, "secs": 200.0} for i, t in enumerate(titles)]}

    def test_BOX3_the_disc_whose_titles_are_yours(self):
        box = mc._box_disc(self.album(self.D1), self.cand())
        self.assertEqual((box["box_disc"], box["box_proof"]), (1, "barcode and titles"))
        self.assertEqual([t["title"] for t in box["tracks"]], self.D1)
        self.assertEqual(mc._box_disc(self.album(self.D2), self.cand())["box_disc"], 2)

    def test_BOX3_not_without_your_barcode_or_with_other_titles(self):
        self.assertIsNone(mc._box_disc(self.album(self.D1, barcodes=()), self.cand()))
        self.assertIsNone(mc._box_disc(self.album(self.D1, barcodes=("0000000000000",)), self.cand()))
        self.assertIsNone(mc._box_disc(self.album(["Blue Christmas", "Santa Claus", "White Christmas"]), self.cand()))

    def test_BOX3_two_discs_that_fit_choose_none(self):
        c = self.cand()
        c["tracks"] = [dict(t, disc=1, secs=None) for t in c["tracks"][:3]] + \
                      [dict(t, disc=2, secs=200.0) for t in c["tracks"][:3]]   # (stereo unlisted, mono listed)
        self.assertIsNone(mc._box_disc(self.album(self.D1), c))

    def test_BOX3_not_when_your_scans_show_another_barcode(self):
        a = dict(self.album(self.D1), ocr_barcodes={"5012982500220"})
        self.assertIsNone(mc._box_disc(a, self.cand()))
        a["ocr_barcodes"] = {"5060209750316"}
        self.assertEqual(mc._box_disc(a, self.cand())["box_disc"], 1)

    def test_BOX3_lengths_the_release_gives_must_agree(self):
        c = self.cand()
        c["tracks"][0]["secs"] = 100.0                     # (one length given: 100 s against 200 s)
        self.assertIsNone(mc._box_disc(self.album(self.D1), c))


class BAR6(unittest.TestCase):
    """BAR-6: your barcode proves the release and it holds just your 20 audio tracks; the tags'
    track total of 30 (Blood, Sweat & Tears - 3 & 4, DIAB 8056) counted something else."""

    def final(self, code):
        cand = {"source": "mb", "id": "bfa9", "tracks": [{"disc": 1, "title": f"T{i}", "secs": 200.0} for i in range(20)]}
        return {"best": {"code": code, "cand": cand, "notes": []}}

    def album(self):
        return {"tracktotal": 30, "tracks": [{"disc": 1, "num": i + 1} for i in range(20)], "barcodes": set()}

    def test_BAR6_the_barcodes_release_outweighs_the_tags_total(self):
        f = self.final("barcode")
        self.assertEqual(ma._copy_disagrees(f, self.album(), "", []), [])
        self.assertTrue(any("the release your barcode names has 20" in n for n in f["best"]["notes"]))

    def test_BAR6_without_your_own_number_the_total_still_holds_it(self):
        for code in ("", ma.mc.TAGS_BARCODE):
            why = ma._copy_disagrees(self.final(code), self.album(), "", [])
            self.assertTrue(any("tags say the album has 30 tracks" in w for w in why), code)

    def test_BAR6_a_release_with_more_tracks_than_the_folder_is_no_excuse(self):
        f = self.final("barcode")
        f["best"]["cand"]["tracks"] += [{"disc": 2, "title": "X", "secs": 100.0}]
        self.assertTrue(ma._copy_disagrees(f, self.album(), "", []))


class BAR7_TIE5_ALB3(unittest.TestCase):
    """BAR-7: your barcode decides whatever the lengths say (Egberto Gismonti - Árvore, 4 of 8).
    TIE-5: no runner-up your copy rules out (a bootleg set aside, a vinyl against a CD rip)
    holds the pick back. ALB-3: album only on the lengths an automatic pick needs."""

    def setUp(self):
        self.dm = ma.load_plugin(str(HERE / "plugins_builtin" / "discogs"))

    def cand(self, rid, durations, formats=None, barcode=None, **kw):
        rel = dict(release(formats or [{"name": "CD", "qty": "1"}]), id=rid, **kw)
        rel["tracklist"] = [dict(t, duration=d) for t, d in zip(rel["tracklist"], durations)]
        if barcode:
            rel["identifiers"] = [{"type": "Barcode", "value": barcode}]
        return ma.discogs_candidate(rel, self.dm)

    def rip(self, **audio):
        a = album(dict({"cd_frames": True, "not_cd": False, "hires": False, "lossless": True}, **audio))
        return dict(a, year=2003)

    def test_BAR7_your_barcode_decides_whatever_the_lengths(self):
        a = dict(self.rip(), barcodes={"5099750936625"})
        c = self.cand(1, ["2:00"] * 5, barcode="5099750936625")          # (no length agrees: 300 s files)
        out = mc.classify([mc.score_candidate(a, c, CFG)], CFG)
        self.assertEqual(out["status"], "auto", out["why"])
        self.assertTrue(any("BAR-7" in n for n in out["best"]["notes"]))
        c2 = self.cand(1, ["2:00"] * 5, barcode="0000000000000")         # (not your barcode: held)
        self.assertNotEqual(mc.classify([mc.score_candidate(dict(a), c2, CFG)], CFG)["status"], "auto")

    def test_TIE5_a_vinyl_runner_up_never_holds_a_cd_rip(self):
        a = self.rip()
        cd = self.cand(1, ["5:00"] * 5)
        lp = self.cand(2, ["5:00"] * 5, formats=[{"name": "Vinyl", "qty": "1", "descriptions": ["LP"]}])
        out = mc.classify([mc.score_candidate(a, cd, CFG), mc.score_candidate(dict(a), lp, CFG)], CFG)
        self.assertEqual((out["status"], str(out["best"]["cand"]["id"])), ("auto", "1"), out["why"])

    def test_ALB3_album_only_on_most_lengths(self):
        best = {"confidence": 92.1, "artist_sim": 1.0, "title_sim": 1.0, "tracks_ok": True, "dur_frac": 0.875,
                "dur_cov": 1.0, "conflict": None, "cand": {"id": 1, "source": "discogs", "title": "A", "tracks": [{}] * 8}}
        other = dict(best, confidence=87.2, dur_frac=0.5, cand=dict(best["cand"], id=2))
        f = {"status": "review", "best": best, "scored": [best, other],
             "why": ["runner-up 2 within 4.9 points (likely another pressing)"]}
        self.assertEqual([t["id"] for t in mc.album_only(f, CFG)["tied"]], ["1", "2"])
        self.assertIsNone(mc.album_only(dict(f, best=dict(best, dur_frac=0.75)), CFG))


class MED13(unittest.TestCase):
    """MED-13: an SACD rip at 24/96 (DSD converted) - "SACD" in the folder's name or an SACD
    tool's log (which stays when the folder is renamed) - is searched on Discogs as an SACD, and
    the SACD found is fetched however the LPs of the tags' year rank (Blood, Sweat & Tears -
    B, S & T 4, Analogue Productions CAPP 088 SA)."""

    def test_MED13_the_folder_name_or_an_sacd_rip_log_says_sacd(self):
        import tempfile
        d = Path(tempfile.mkdtemp(prefix="tm-med13-"))
        self.assertEqual(ma.sacd_marks(d, "2016 - B S & T 4 [SACD 2ch DSD64 24bit 352khz]"), "folder name")
        self.assertEqual(ma.sacd_marks(d, "1971 - B, S & T; 4 [HD]"), "")
        (d / "misc").mkdir()
        (d / "misc" / "sacd_log.txt").write_text("16908363[0x102004b]: SACD-Ripper Version 0.21\n")
        self.assertEqual(ma.sacd_marks(d, "1971 - B, S & T; 4 [HD]"), "an SACD rip log (sacd_log.txt)")
        self.assertEqual(ma.sacd_marks(d, "Sacdecal"), "an SACD rip log (sacd_log.txt)")
        (d / "misc" / "sacd_log.txt").write_text("my notes\n")
        self.assertEqual(ma.sacd_marks(d, "Album"), "")
        for name in ("DST", "Album DST-2", "Pseudo-DSD", "DSD-Remastered"):     # (no format: a word)
            self.assertEqual(ma.sacd_marks(d, name), "", name)
        self.assertEqual(ma.sacd_marks(d, "Kind of Blue [DSD128]"), "folder name")

    def test_MED13_an_sacd_release_gets_the_clue_only_for_hi_res_audio(self):
        dm = ma.load_plugin(str(HERE / "plugins_builtin" / "discogs"))
        sacd = ma.discogs_candidate(release([{"name": "SACD", "qty": "1", "descriptions": ["Hybrid"]}]), dm)
        hires = {"cd_frames": False, "not_cd": True, "hires": True, "lossless": True, "sacd": False}
        a = dict(album(hires), sacd_hint="an SACD rip log (sacd_log.txt)", media_hint="")
        self.assertTrue(str(mc.score_candidate(a, sacd, CFG)["clues"].get("format", "")).startswith("SACD ("))
        cd_audio = dict(a, audio={"cd_frames": True, "not_cd": False, "hires": False, "lossless": True})
        self.assertFalse(str(mc.score_candidate(cd_audio, sacd, CFG)["clues"].get("format", "")).startswith("SACD ("))
        vinyl = dict(a, media_hint="vinyl")
        self.assertFalse(str(mc.score_candidate(vinyl, sacd, CFG)["clues"].get("format", "")).startswith("SACD ("))
        named = dict(a, sacd_hint="folder name", media_hint="cd")                # ("[SACD]" reads as cd too)
        self.assertTrue(str(mc.score_candidate(named, sacd, CFG)["clues"].get("format", "")).startswith("SACD ("))

    def test_MED13_the_sacd_search_is_asked_first_and_its_find_fetched(self):
        dm = ma.load_plugin(str(HERE / "plugins_builtin" / "discogs"))
        asked, fetched = [], []

        class Http:
            def get_json(self, url, host):
                if "database/search" in url:
                    asked.append(url)
                    if "format=SACD" in url:
                        return {"results": [{"id": 37008981, "title": "Blood, Sweat & Tears - B, S & T 4", "year": "2016"}]}
                    return {"results": [{"id": 100 + i, "title": "Blood, Sweat & Tears - B, S & T 4", "year": "1971"}
                                        for i in range(12)]}
                fetched.append(url.rsplit("/", 1)[-1])
                return release([{"name": "SACD", "qty": "1"}])
        a = {"title": "B, S & T 4", "artist": "Blood, Sweat & Tears", "year": 1971, "barcodes": set(),
             "tracks": [], "sacd_hint": "an SACD rip log (sacd_log.txt)", "pressing_year": 1971}
        ma.discogs_search(Http(), a, dict(CFG, discogs_detail_fetch=5), dm)
        self.assertIn("format=SACD", asked[0])
        self.assertIn("37008981", fetched)
        asked.clear()
        ma.discogs_search(Http(), dict(a, sacd_hint=""), dict(CFG, discogs_detail_fetch=5), dm)
        self.assertFalse(any("format=SACD" in u for u in asked))


class TRK1(unittest.TestCase):
    """TRK-1: a work listed with its movements under it is one track (BS&T 3: "Symphony For The
    Devil" 7:49 then 7.I-7.III; Mirror Image: an index "Mirror Image" 11:09 over 7a-7d)."""

    def rows(self, n, start=1):
        return [{"position": str(i), "type_": "track", "title": f"T{i}", "duration": "3:00"} for i in range(start, n + 1)]

    def bst3(self):
        return (self.rows(6) + [{"position": "7", "type_": "track", "title": "Symphony", "duration": "7:49"}]
                + [{"position": f"7.{r}", "type_": "track", "title": f"Mvt {r}", "duration": ""} for r in ("I", "II", "III")]
                + self.rows(10, 8))

    def mirror(self):
        return (self.rows(6) + [{"position": "", "type_": "index", "title": "Mirror Image", "duration": "11:09",
                                 "sub_tracks": [{"position": f"7{x}", "type_": "track", "title": f"Mvt {x}", "duration": ""}
                                                for x in "abcd"]}] + self.rows(18, 8))

    def test_TRK1_movements_without_lengths_are_one_track(self):
        flat = ma.one_track_movements(self.bst3())
        self.assertEqual([t["position"] for t in flat], [str(i) for i in range(1, 11)])
        flat = ma.one_track_movements(self.mirror())
        self.assertEqual(len(flat), 18)
        self.assertEqual((flat[6]["position"], flat[6]["title"], flat[6]["duration"]), ("7", "Mirror Image", "11:09"))
        self.assertEqual(ma.one_track_movements(flat), flat)                       # (twice: no change)

    def test_TRK1_movements_with_lengths_stay_tracks(self):
        rows = self.bst3()
        rows[7]["duration"] = "2:00"                                               # (7.I has its own length)
        self.assertEqual(len(ma.one_track_movements(rows)), 13)                   # (listed as Discogs has it)
        m = self.mirror()
        m[6]["sub_tracks"][0]["duration"] = "2:00"
        self.assertEqual(len(ma.one_track_movements(m)), 18)                       # (the index entry kept as listed)
        self.assertEqual(ma.one_track_movements(m)[6]["type_"], "index")

    def test_TRK1_a_next_track_is_never_a_movement(self):
        rows = [{"position": "1", "type_": "track", "title": "A", "duration": "3:00"},
                {"position": "10", "type_": "track", "title": "B", "duration": ""},
                {"position": "1a", "type_": "track", "title": "C", "duration": ""}]
        self.assertEqual([t["title"] for t in ma.one_track_movements(rows)], ["A", "B", "C"])
        self.assertTrue(ma._movement_of("7.I", "7") and ma._movement_of("7a", "7") and ma._movement_of("7-II", "7"))
        self.assertFalse(ma._movement_of("10", "1") or ma._movement_of("7", "7") or ma._movement_of("8.I", "7")
                         or ma._movement_of("7.II", "7.I"))

    def test_TRK1_sub_tracks_numbered_as_tracks_stay_tracks(self):
        def idx(positions):
            return [{"position": "", "type_": "index", "title": "Sym 5", "duration": "31:00",
                     "sub_tracks": [{"position": p, "type_": "track", "title": f"M{p}", "duration": ""} for p in positions]}]
        for positions in (("2", "3", "4", "5"), ("2-2", "2-3"), ("1.7", "1.8")):    # (tracks; a disc's tracks)
            flat = ma.one_track_movements(idx(positions))
            self.assertEqual(flat[0]["type_"], "index", positions)
        self.assertEqual(ma.one_track_movements(idx(("2-I", "2-II")))[0]["position"], "2")
        self.assertFalse(ma._movement_of("2-2", "2") or ma._movement_of("7.1", "7"))

    def test_TRK1_the_candidate_keeps_the_rows_as_listed(self):
        dm = ma.load_plugin(str(HERE / "plugins_builtin" / "discogs"))
        rel = dict(release([{"name": "CD", "qty": "1"}]), tracklist=self.bst3())
        c = ma.discogs_candidate(rel, dm)
        self.assertEqual((len(c["tracks"]), c["rows"]), (10, 13))     # (a tagger's total of 13: _total_miscounted)

    def test_TRK1_the_matcher_and_the_tags_count_ten(self):
        dm = ma.load_plugin(str(HERE / "plugins_builtin" / "discogs"))
        rel = dict(release([{"name": "SACD", "qty": "1"}]), tracklist=self.bst3())
        self.assertEqual(len(ma.discogs_candidate(rel, dm)["tracks"]), 10)
        import engine
        _album, per_track, _discs = engine.discogs_parts(rel, dm)
        self.assertEqual([t.get("title") for t in per_track][6:8], ["Symphony", "T8"])


class TRK2(unittest.TestCase):
    """TRK-2: your files are the release's tracks in another order (BS&T - Child Is Father To The
    Man, an SACD rip whose 001 is the release's track 11): each paired with its own track."""
    REL = [("Overture", 92), ("I Love You More", 357), ("Morning Glory", 256), ("My Days Are Numbered", 199)]

    def cand(self):
        return {"source": "discogs", "id": 6316090, "artist": "AC/DC", "title": "Live At Donington", "barcodes": set(),
                "catnos": set(), "tracks": [{"disc": 1, "title": t, "secs": s} for t, s in self.REL]}

    def files(self, order, secs=None):
        return {"tracks": [{"disc": 1, "num": i + 1, "title": self.REL[k][0], "secs": (secs or {}).get(k, self.REL[k][1])}
                           for i, k in enumerate(order)]}

    def test_TRK2_each_file_is_paired_with_its_own_track(self):
        v = mc.order_view(self.files([3, 1, 0, 2]), self.cand())
        self.assertEqual((v["slots"], v["missing"], v["full_tracks"]), ([3, 1, 0, 2], {}, 4))
        self.assertEqual([t["title"] for t in v["tracks"]], ["My Days Are Numbered", "I Love You More", "Overture",
                                                             "Morning Glory"])

    def test_TRK2_not_in_order_already_nor_on_a_doubt(self):
        self.assertIsNone(mc.order_view(self.files([0, 1, 2, 3]), self.cand()))         # (the order it has)
        self.assertIsNone(mc.order_view(self.files([3, 1, 0, 2], secs={3: 260}), self.cand()))   # (a length off)
        c = self.cand()
        c["tracks"][2]["title"] = "Overture"                                             # (two tracks one title)
        self.assertIsNone(mc.order_view(self.files([3, 1, 0, 2]), c))
        f = self.files([3, 1, 0, 2])
        f["tracks"][0]["title"] = ""
        self.assertIsNone(mc.order_view(f, self.cand()))                                 # (a file without a title)

    def test_TRK2_scored_on_the_pairing_and_exported_for_the_tags(self):
        a = dict(album({"cd_frames": False, "not_cd": True, "hires": True, "lossless": True}),
                 tracks=self.files([3, 1, 0, 2])["tracks"], title="Live At Donington")
        dm = ma.load_plugin(str(HERE / "plugins_builtin" / "discogs"))
        rel = release([{"name": "SACD", "qty": "1"}])
        rel["tracklist"] = [{"position": str(i + 1), "type_": "track", "title": t, "duration": f"{s // 60}:{s % 60:02d}"}
                            for i, (t, s) in enumerate(self.REL)]
        s = mc.score_candidate(a, ma.discogs_candidate(rel, dm), CFG)
        self.assertEqual((s["dur_frac"], s["cand"]["slots"]), (1.0, [3, 1, 0, 2]))
        self.assertTrue(any("TRK-2" in n for n in s["notes"]))
        out = ma.summarize({"best": s})
        self.assertEqual((out["slots"], out["missing"], out["release_tracks"]), ([3, 1, 0, 2], {}, 4))

    def test_TRK2_a_release_you_choose_is_tagged_track_by_track(self):
        import engine
        dm = ma.load_plugin(str(HERE / "plugins_builtin" / "discogs"))
        rel = release([{"name": "SACD", "qty": "1"}])
        rel["tracklist"] = [{"position": str(i + 1), "type_": "track", "title": t, "duration": f"{s // 60}:{s % 60:02d}"}
                            for i, (t, s) in enumerate(self.REL)]
        cand = ma.discogs_candidate(rel, dm)
        a = self.files([3, 1, 0, 2])
        sc = mc.score_candidate(dict(album({}), tracks=a["tracks"]), cand, CFG)
        got = engine._slots_of(a, cand, sc)
        self.assertEqual((got["slots"], got["missing"], got["release_tracks"]), ([3, 1, 0, 2], {}, 4))
        in_order = self.files([0, 1, 2, 3])
        self.assertEqual(engine._slots_of(in_order, cand, mc.score_candidate(dict(album({}), tracks=in_order["tracks"]),
                                                                             cand, CFG)), {})

    def test_TRK2_two_swapped_tracks_are_another_editions_order(self):
        rel = self.REL + [(f"Song {i}", 200 + 20 * i) for i in range(6)]
        cand = dict(self.cand(), id=1, tracks=[{"disc": 1, "title": t, "secs": s} for t, s in rel])
        order = [1, 0] + list(range(2, 10))                       # (8 of 10 lengths agree as filed)
        files = {"tracks": [{"disc": 1, "num": i + 1, "title": rel[k][0], "secs": rel[k][1]} for i, k in enumerate(order)]}
        self.assertIsNone(mc.order_view(files, cand))             # (a few moved: decided as before)
        whole = list(reversed(range(10)))                         # (a running order of its own)
        files = {"tracks": [{"disc": 1, "num": i + 1, "title": rel[k][0], "secs": rel[k][1]} for i, k in enumerate(whole)]}
        self.assertEqual(mc.order_view(files, cand)["slots"], whole)


def pressing(cid, country, catnos=("1166190752",), barcode="11661907522", against=(), clues=None, **kw):
    cand = {"source": "mb", "id": cid, "country": country, "formats": {"cd"}, "barcodes": {barcode} if barcode else set(),
            "catnos": set(catnos), "label": "Rounder", "date": "2007-10-23", "year": 2007,
            "tracks": [{"disc": 1, "title": f"T{i}", "secs": 200.0} for i in range(13)]}
    cand.update(kw.pop("cand", {}))
    s = {"cand": cand, "confidence": 100.0, "code": "barcode" if barcode else "", "conflict": None, "year_ok": True,
         "clues": dict(clues or {}), "clues_against": list(against), "notes": [], "tracks_ok": True,
         "artist_sim": 1.0, "title_sim": 1.0, "dur_frac": 1.0, "dur_cov": 1.0}
    s.update(kw)
    return s


class EVI5_EVI6(unittest.TestCase):
    """Robert Plant & Alison Krauss - Raising Sand: a 24/96 download carrying the CD's UPC. The CDs
    of that barcode tie; your scans' catalogue number is on the US (listed twice) and ZA entries,
    not the CA one (EVI-5: the audio's doubt they all share tells none apart; EVI-6: the guesses
    choose among the pressings your evidence is on)."""
    HIRES = "hi-res audio cannot come from this CD"
    CAT = {"catalogue number": "catalogue number 1166190752 on the scans"}
    ORDER = ["CA", "US", "XW", "XE", "GB", "DE"]

    def tied(self, barcode="11661907522"):
        return [pressing("us-1", "US", barcode=barcode, against=[self.HIRES], clues=self.CAT),
                pressing("us-2", "US", barcode=barcode, against=[self.HIRES], clues=self.CAT, cand={"packaging": "jewel"}),
                pressing("za", "ZA", barcode=barcode, against=[self.HIRES], clues=self.CAT),
                pressing("ca", "CA", catnos=("1166190762",), barcode=barcode, against=[self.HIRES])]

    def test_EVI5_EVI6_the_pressing_your_evidence_names(self):
        got = mc.choose_pressing(self.tied(), self.ORDER)
        self.assertEqual(got[0]["cand"]["id"], "us-1")
        self.assertIn("listed twice", " ".join(got[1].values()))

    def test_EVI5_not_without_your_barcode_on_them_all(self):
        self.assertIsNone(mc.choose_pressing(self.tied(barcode=""), self.ORDER))   # (an unlisted download, perhaps)

    def test_EVI5_a_hi_res_copy_takes_the_pressing_its_barcode_names(self):
        tied = self.tied()
        for s in tied:
            s["copy_kind"] = "digital"                                  # (24/96: no CD's audio)
        r = mc.classify(tied, dict(CFG, preferred_countries=self.ORDER))
        self.assertEqual((r["status"], r["best"]["cand"]["id"]), ("auto", "us-1"), r["why"])

    def test_TIE4_the_vinyl_step_keeps_the_bootleg_out(self):
        vinyl = pressing("lp", "US", barcode="", cand={"formats": {"vinyl"}, "title": "A"}, confidence=100.0)
        boot = pressing("ru", "RU", barcode="", cand={"unofficial": True, "title": "A"}, confidence=99.5)
        cd = pressing("cd", "GB", barcode="", confidence=99.5, cand={"title": "A"})
        r = mc.classify([vinyl, boot, cd], dict(CFG, preferred_countries=["RU", "GB"]))
        self.assertNotEqual(r["best"]["cand"]["id"], "ru", r["why"])

    def test_TIE4_your_own_choice_of_a_bootleg_stands(self):
        boot = pressing("ru", "RU", barcode="", cand={"unofficial": True}, tagged=True)
        off = pressing("o", "US", barcode="")
        self.assertEqual(mc._officials_in_tie([boot, off], kept=boot)[1], [])
        self.assertEqual(mc._officials_in_tie([boot, off])[1], [boot])

    def test_TIE4_a_bootleg_in_the_tie_is_never_the_country_orders_pick(self):
        boot = pressing("ru", "RU", barcode="", cand={"unofficial": True}, dur_frac=1.0)
        offs = [pressing(f"o{i}", c, barcode="", dur_frac=0.85, confidence=99.0) for i, c in enumerate(["GB", "US"])]
        r = mc.classify([boot] + offs, dict(CFG, preferred_countries=["RU", "US"]))
        self.assertFalse(r["status"] == "auto" and r["best"]["cand"]["id"] == "ru", r["why"])    # (fits better: a person)
        offs = [pressing(f"o{i}", c, barcode="", dur_frac=1.0, confidence=99.0) for i, c in enumerate(["GB", "US"])]
        r = mc.classify([boot] + offs, dict(CFG, preferred_countries=["RU", "US"]))
        self.assertEqual((r["status"], r["best"]["cand"]["id"]), ("auto", "o1"), r["why"])      # (fits as well: out)

    def test_TIE4_a_bootleg_never_wins_a_tie_break(self):
        official = pressing("rounder", "US", barcode="", against=[], clues={})
        bootleg = pressing("ru", "Russia", barcode="", against=[], clues={}, cand={"unofficial": True})
        r = mc.classify([bootleg, official], dict(CFG, preferred_countries=["RU", "US"]))
        self.assertEqual(str(r["best"]["cand"]["id"]), "rounder", r.get("why"))


class FIL2(unittest.TestCase):
    """FIL-2: your copy's own number proves the release and it lists no track lengths: filed
    (Mark Nauseef - Sura, JICK-89728: 76.9)."""

    def test_FIL2_filed_on_your_number_when_no_lengths_are_listed(self):
        import plan as pl
        import profiles as pf
        prop = pl.Proposal(source_dir="/x", files=[], status="identified", tier="exact", number_proven=True)
        self.assertIn("lists no track lengths", pl._files_on_evidence(prop, pf.default(), 76.9))
        self.assertEqual(pl._files_on_evidence(pl.Proposal(source_dir="/x", files=[], status="identified", tier="exact",
                                                           number_proven=True, guessed_pick=True), pf.default(), 76.9), "")
        self.assertEqual(pl._files_on_evidence(pl.Proposal(source_dir="/x", files=[], status="identified", tier="exact"),
                                               pf.default(), 76.9), "")

    def test_FIL2_the_flag_needs_your_number_and_no_lengths(self):
        b = pressing("jick", "JP", catnos=("JICK-89728",), barcode="", code="catalogue number (folder name)",
                     dur_frac=None, dur_cov=0, missing=None)
        b["cand"].update(url="", artist="Mark Nauseef", title="Sura", label="Jimco")
        self.assertTrue(ma.summarize({"best": b})["number_proven"])
        self.assertFalse(ma.summarize({"best": dict(b, dur_frac=0.5, dur_cov=1.0)})["number_proven"])
        self.assertFalse(ma.summarize({"best": dict(b, code="")})["number_proven"])
        self.assertFalse(ma.summarize({"best": dict(b, lacks=["Someone"])})["number_proven"])   # (a credit dropped)


class BAR8(unittest.TestCase):
    """BAR-8: your copy's own number proves the release: a title your tags write otherwise
    (Guru Guru - UFO, tagged "UFO/ Hinten") or a version your folder does not name (Live &
    Unreleased, the Spalax CD) holds nothing back, and a catalogue number read off your scans is
    your own number (Makarski & Jarrett - Bach, ECM 4764582, 89.6)."""

    def test_BAR8_a_title_your_tags_write_otherwise(self):
        s = pressing("ufo", "DE", title_sim=0.46, dur_frac=0.6, confidence=81.2)
        r = mc.classify([s], CFG)
        self.assertEqual(r["status"], "auto", r["why"])
        self.assertTrue(any("BAR-8" in n for n in r["best"]["notes"]))
        r = mc.classify([pressing("ufo", "DE", barcode="", title_sim=0.46, dur_frac=0.6, confidence=81.2)], CFG)
        self.assertNotEqual(r["status"], "auto")                                    # (no barcode of yours)

    def test_BAR8_a_version_your_barcode_names(self):
        r = mc.classify([pressing("live", "FR", other_version="live")], CFG)
        self.assertEqual(r["status"], "auto", r["why"])
        r = mc.classify([pressing("live", "FR", barcode="", other_version="live")], CFG)
        self.assertTrue(any("live version" in w for w in r["why"]))

    def test_BAR8_your_scans_catalogue_number(self):
        s = pressing("ecm", "DE", barcode="", code="catalogue number (OCR)", catno_on_copy=True, confidence=89.6,
                     dur_frac=0.84)
        r = mc.classify([s], CFG)
        self.assertEqual(r["status"], "auto", r["why"])
        self.assertTrue(r["best"]["own_number"])
        typed = pressing("ecm", "DE", barcode="", code="catalogue number (OCR)", catno_on_copy=False, confidence=89.6,
                         dur_frac=0.84)                                            # (only in notes or a saved page)
        self.assertFalse(mc.classify([typed], CFG)["best"]["own_number"])

    def test_BAR8_printed_means_on_a_scan_and_whole(self):
        a = {"printed_text": "ECM New Series 476 4582 made in Germany", "typed_text": "see ECM 4764590"}
        self.assertTrue(mc.catno_printed(a, "4764582"))
        self.assertFalse(mc.catno_printed(a, "4764590"))                         # (typed only)
        self.assertFalse(mc.catno_printed({"printed_text": "ECM12345", "typed_text": ""}, "ecm1234"))   # (inside)
        self.assertTrue(mc.catno_printed({"printed_text": "CHARRMCD03", "typed_text": ""}, "charrmcd3"))  # (a zero)
        self.assertTrue(mc.catno_printed({"printed_text": "ECM New Series 2230/31", "typed_text": ""},
                                         "ecmnewseries2230/31"))                       # (its "/" printed or not)

    def test_BAR8_never_another_album_under_a_barcode(self):
        s = pressing("other", "DE", title_sim=0.4, dur_frac=0.2, titles_agree=0, confidence=60.0)
        self.assertNotEqual(mc.classify([s], CFG)["status"], "auto")
        r = mc.classify([pressing("live", "FR", other_version="live", dur_frac=0.2, titles_agree=0)], CFG)
        self.assertTrue(any("live version" in w for w in r["why"]))


class TIE6(unittest.TestCase):
    """TIE-6: one disc entered several times in MusicBrainz, its label and number written otherwise
    (King's X - Out Of The Silent Planet): your barcode and exact disc ID on each - one pressing."""

    def entries(self, toc=True):
        clues = {"disc TOC": "disc ID matches"} if toc else {}
        return [pressing("a", "US", catnos=("7567818252",), clues=clues, cand={"label": "Megaforce Worldwide"}),
                pressing("b", "US", catnos=("7818252", "818252"), clues=clues, cand={"label": "Atlantic; Megaforce"}),
                pressing("c", "US", catnos=("7818252", "818252"), clues=clues, cand={"label": "Atlantic", "date": ""})]

    def test_TIE6_one_disc_entered_several_times(self):
        got = mc.same_pressing_listed_twice(self.entries())
        self.assertIn("one disc entered several times", got[1]["duplicate"])

    def test_TIE6_not_without_the_disc_id(self):
        self.assertIsNone(mc.same_pressing_listed_twice(self.entries(toc=False)))

    def test_TIE6_not_across_years(self):
        e = self.entries()
        e[0]["cand"]["year"], e[1]["cand"]["year"] = 1988, 2005                  # (a reissue reusing both)
        got = mc.same_pressing_listed_twice(e)
        self.assertFalse(got and "one disc entered" in got[1].get("duplicate", ""))


class CAT1(unittest.TestCase):
    def test_CAT1_an_sacd_audio_format_is_no_catalogue_number(self):
        # (Blood, Sweat & Tears: "[SACD DSD64 2ch Rip 24bit 352khz]" in the album tag)
        for t in ("Blood, Sweat & Tears [SACD DSD64 2ch Rip 24bit 352khz]", "Greatest Hits [SACD DST64 24bit 352khz]",
                  "Kind of Blue [DSD128]", "Album [DXD 24bit 352.8khz]", "Album [PCM 5.1 MCH ISO]"):
            self.assertEqual(mc.catno_hints(t), [], t)
        self.assertEqual(mc.catno_hints("Blood, Sweat & Tears [2005 MFSL UDSACD2009]"), ["UDSACD2009"])
        self.assertEqual(mc.catno_hints("Kitaro [20bit RM DOMO 71063-2]"), ["DOMO 71063-2"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
