# Thirteenth independent review: this test failed before its fix.
# Thirteenth independent review: an artist folder whose name is the album artist as it is
# ("R.E.M."), not as Windows-compatible names would spell it ("R.E.M_").
import os as _os, tempfile as _tempfile
_os.environ.setdefault("XDG_CONFIG_HOME", _tempfile.mkdtemp(prefix="tm-config-"))
_os.environ.setdefault("XDG_STATE_HOME", _tempfile.mkdtemp(prefix="tm-state-"))
import os, shutil, sys, unittest
from pathlib import Path
REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO)); sys.path.insert(0, str(REPO / "tests"))
import artwork, executor, plan as pl  # noqa
from test_review8_artist import R8  # noqa
from test_artist_photo import FakeCoverUp, pictures  # noqa
from test_cover_replace import jpeg, give  # noqa
from test_executor import audio, target  # noqa

for _n in dir(R8):
    if _n.startswith("test_"):
        setattr(R8, _n, None)


class TrailingDotArtistFolder(R8):
    """"lib/R.E.M./1991 - Out of Time/", the user's artist.jpg in "lib/R.E.M./" (a library
    made on Linux or a NAS, by Picard without Windows-compatible names, beets, a ripper).
    named_after compares the folder only with safe_component(artist, "windows") = "R.E.M_":
    the folder is not the artist's, the user's artist.jpg is ignored, the artist is looked
    up on the network and a downloaded photo goes into the tracks."""
    def test_users_photo(self):
        shutil.rmtree(self.lib / "Some Artist")
        a = self.lib / "R.E.M." / "1991 - Out of Time"
        fs = [audio(a / "01 a.flac", 440, album="Out of Time", title="a", artist="R.E.M."),
              audio(a / "02 b.flac", 550, album="Out of Time", title="b", artist="R.E.M.")]
        for f in fs:
            give(f, jpeg(600, 600))
        (a.parent / "artist.jpg").write_bytes(jpeg(400, 400, (1, 2, 3)))
        self.run_one(self.prof(), self.prop(folder=a, files=fs, artist="R.E.M."))
        self.assertEqual(FakeCoverUp.calls, [], "looked up although the artist folder has its photo")
        self.assertIn((8, (400, 400)), pictures(fs[0]), "a downloaded photo, not the user's artist.jpg")


if __name__ == "__main__":
    unittest.main()
