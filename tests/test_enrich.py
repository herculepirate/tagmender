# Tests for enrich.py (ReplayGain, genres, lyrics, quality, undo) with ffmpeg-made audio and a fake web.
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent   # the scripts sit above tests/
sys.path.insert(0, str(HERE))
import enrich as en  # noqa: E402
import tag_albums as ta  # noqa: E402

import mutagen  # noqa: E402


def make(p, src, secs=4, **tags):
    p.parent.mkdir(parents=True, exist_ok=True)
    meta = sum((["-metadata", f"{k}={v}"] for k, v in tags.items()), [])
    subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", src, "-t", str(secs), "-ar", "48000" if str(p).endswith(".opus") else "44100",
                    *meta, str(p), "-y"], check=True)


NOISE = "anoisesrc=color=white:amplitude=0.3:sample_rate=44100"


class Enrich(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.album = self.tmp / "lib" / "Band" / "2020 - Album"

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_replaygain_track_and_album(self):
        make(self.album / "01.flac", "sine=frequency=440:duration=4")
        make(self.album / "02.flac", "sine=frequency=440:duration=4,volume=0.25")
        rg = en.replaygain(ta.audio_files(self.album))
        g1 = float(rg[self.album / "01.flac"]["REPLAYGAIN_TRACK_GAIN"].split()[0])
        g2 = float(rg[self.album / "02.flac"]["REPLAYGAIN_TRACK_GAIN"].split()[0])
        self.assertAlmostEqual(g2 - g1, 12.0, delta=0.5)               # a quarter of the level: 12 dB quieter
        self.assertEqual(rg[self.album / "01.flac"]["REPLAYGAIN_ALBUM_GAIN"],
                         rg[self.album / "02.flac"]["REPLAYGAIN_ALBUM_GAIN"])

    def test_transcode_found_full_band_not(self):
        pink = "anoisesrc=color=pink:amplitude=0.3:sample_rate=44100"
        mp3 = self.tmp / "m.mp3"
        make(mp3, pink, 30)
        subprocess.run(["ffmpeg", "-loglevel", "error", "-i", str(mp3), "-b:a", "128k", str(self.tmp / "m2.mp3"),
                        "-y"], check=True)
        self.album.mkdir(parents=True, exist_ok=True)
        subprocess.run(["ffmpeg", "-loglevel", "error", "-i", str(self.tmp / "m2.mp3"), str(self.album / "fake.flac"),
                        "-y"], check=True)
        make(self.tmp / "real" / "real.flac", pink, 30)
        self.assertIn("transcoded", en.quality([self.album / "fake.flac"]))
        self.assertEqual(en.quality([self.tmp / "real" / "real.flac"]), "")
        hi = self.tmp / "hi" / "up.flac"
        hi.parent.mkdir()
        subprocess.run(["ffmpeg", "-loglevel", "error", "-i", str(self.tmp / "real" / "real.flac"), "-ar", "96000",
                        str(hi), "-y"], check=True)
        self.assertIn("upsampled", en.quality([hi]))

    def test_genres_mapped_to_your_list(self):
        conf = {"map": {"funeral doom metal": "Doom Metal"}, "allowed": ["Doom Metal", "Black Metal"], "max": 2}
        self.assertEqual(en.map_genres(["Funeral Doom Metal", "Pop", "black metal"], conf),
                         ["Doom Metal", "Black Metal"])
        self.assertEqual(en.map_genres(["death metal", "Technical Death Metal"], {}),
                         ["Death Metal", "Technical Death Metal"])

    def test_lyrics_and_lrc_with_undo(self):
        make(self.album / "01.flac", "sine=frequency=440:duration=4", artist="Band", title="Song", album="Album")
        before = sorted(mutagen.File(self.album / "01.flac").tags.items())

        class Web:
            def get_json(self, url, host):
                return {"syncedLyrics": "[00:01.00] hello", "plainLyrics": "hello"} if "lrclib" in url else {}
        orig = en.ma.Http
        en.ma.Http = lambda db, cfg, tok: Web()
        try:
            en.main([str(self.tmp / "lib"), "--lyrics", "--lrc", "--apply", "--out-dir", str(self.tmp / "out"),
                     "--cache-db", str(self.tmp / "c.db")])
        finally:
            en.ma.Http = orig
        self.assertEqual(en.get(self.album / "01.flac", "LYRICS"), "[00:01.00] hello")
        self.assertTrue((self.album / "01.lrc").exists())
        run = sorted(p.name for p in (self.tmp / "out").glob("run-enrich-*"))[-1][len("run-"):]
        en.main(["--undo", run, "--out-dir", str(self.tmp / "out")])
        self.assertEqual(sorted(mutagen.File(self.album / "01.flac").tags.items()), before)
        self.assertFalse((self.album / "01.lrc").exists())

    def test_put_and_get_in_every_format(self):
        for name in ("a.flac", "b.mp3", "c.m4a", "d.opus"):
            make(self.album / name, "sine=frequency=440:duration=1")
            en.put(self.album / name, {"GENRE": ["Doom Metal"], "TAGMENDER_QUALITY": "x", "LYRICS": "la"})
            self.assertEqual(en.get(self.album / name, "GENRE"), "Doom Metal", name)
            self.assertEqual(en.get(self.album / name, "TAGMENDER_QUALITY"), "x", name)
            self.assertEqual(en.get(self.album / name, "LYRICS"), "la", name)

    def test_navidrome_needs_its_settings(self):
        orig = en.CONFIG
        en.CONFIG = self.tmp
        try:
            self.assertFalse(en.navidrome_rescan())
        finally:
            en.CONFIG = orig


if __name__ == "__main__":
    unittest.main()
