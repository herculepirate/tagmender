# Tenth independent review: this test failed before its fix.
import os as _os, tempfile as _tempfile
_os.environ.setdefault("XDG_CONFIG_HOME", _tempfile.mkdtemp(prefix="tm-config-"))
_os.environ.setdefault("XDG_STATE_HOME", _tempfile.mkdtemp(prefix="tm-state-"))
import sys, unittest
from pathlib import Path
REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO)); sys.path.insert(0, str(REPO / "tests"))
import artwork, executor, plan as pl  # noqa
from test_review9_artist import R9  # noqa
from test_artist_photo import FakeCoverUp, pictures  # noqa
from test_cover_replace import jpeg, size  # noqa


class DestArtistFolderPhoto(R9):
    """Watched inbox -> library (move). The library's artist folder already has the user's
    artist.jpg (or one TagMender wrote earlier and the user corrected)."""
    def test_moved_album_gets_the_folders_photo_not_a_download(self):
        prof = self.profile(artwork=self.prof()["artwork"], tag_level="enthusiast", operation="move",
                            destination=str(self.dest))
        home = self.dest / "Some Artist"
        home.mkdir(parents=True)
        (home / "artist.jpg").write_bytes(jpeg(500, 500, (255, 0, 0)))
        p, res = self.run_one(prof, self.prop())
        self.assertEqual(res["counts"], {"done": 1}, res)
        now = sorted(self.dest.rglob("*.flac"))
        self.assertTrue(now)
        got = pictures(now[0])
        self.assertIn((8, (500, 500)), got,
                      f"embedded a downloaded photo instead of the library artist folder's artist.jpg: {got}; "
                      f"lookups {FakeCoverUp.calls}")


if __name__ == "__main__":
    unittest.main()
