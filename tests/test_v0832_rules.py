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
