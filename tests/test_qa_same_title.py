"""A release that plays one piece twice (a suite on one CD: "Gnomus" 2:43 at track 2 and again at
track 18): two FLAC files with the same title and the same length but different audio are two
tracks, both kept; the album is not split into "its" tracks and "other music". A real copy of a
file (the same audio) still counts once. Synthetic files in temporary folders."""
import os as _os, tempfile as _tempfile  # noqa: E402
_os.environ.setdefault("XDG_CONFIG_HOME", _tempfile.mkdtemp(prefix="tm-config-"))
_os.environ.setdefault("XDG_STATE_HOME", _tempfile.mkdtemp(prefix="tm-state-"))
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))
import match_albums as ma  # noqa: E402

FFMPEG = shutil.which("ffmpeg")


@unittest.skipIf(FFMPEG is None, "ffmpeg is needed for the test audio")
class SamePieceTwice(unittest.TestCase):
    def setUp(self):
        from test_executor import audio
        self.tmp = Path(tempfile.mkdtemp(prefix="tm-twice-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.dir = self.tmp / "1996 - Pictures"
        self.audio = audio

    def test_two_recordings_of_one_title_are_both_kept(self):
        self.audio(self.dir / "02 - Band - Gnomus.flac", 440, title="Gnomus", tracknumber="02")
        self.audio(self.dir / "18 - Band - Gnomus.flac", 660, title="Gnomus", tracknumber="18")
        self.audio(self.dir / "03 - Band - Other.flac", 500, title="Other", tracknumber="03")
        a = ma.read_album(self.dir, "Band", self.dir.name)
        self.assertEqual(sorted(Path(t["path"]).name for t in a["tracks"]),
                         ["02 - Band - Gnomus.flac", "03 - Band - Other.flac", "18 - Band - Gnomus.flac"])
        self.assertEqual(a["dropped_tracks"], [])

    def test_a_copy_of_the_same_audio_still_counts_once(self):
        self.audio(self.dir / "02 - Band - Gnomus.flac", 440, title="Gnomus", tracknumber="02")
        shutil.copy2(self.dir / "02 - Band - Gnomus.flac", self.dir / "02 - Band - Gnomus (1).flac")
        a = ma.read_album(self.dir, "Band", self.dir.name)
        self.assertEqual(len(a["tracks"]), 1)
        self.assertEqual(len(a["dropped_tracks"]), 1)


if __name__ == "__main__":
    unittest.main()
