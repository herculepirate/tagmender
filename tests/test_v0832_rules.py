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
