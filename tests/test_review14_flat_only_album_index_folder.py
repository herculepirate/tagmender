# Fourteenth independent review: this test failed before its fix.
# Fourteenth independent review (A): this test fails at eb6cfad (and at f4778b5).
# The thirteenth review's fix (an artist's only album ripped flat into its artist folder uses
# the artist.jpg there) works only right below a library root: artist_dir_for takes the album
# folder for the artist folder when it holds_other_albums "or not inside(d)". In a library
# split into letter folders ("Lib/S/Some Artist/"), or "Lib/Rock/Some Artist/", the parent
# is inside the root, so the user's artist.jpg beside the flat album is ignored: the artist
# is looked up on the network and a download goes into the tracks.
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


class OnlyAlbumFlatUnderLetterFolder(R8):
    def setUp(self):
        super().setUp()
        shutil.rmtree(self.album.parent)
        artist = self.lib / "S" / "Some Artist"
        self.x = audio(artist / "01 x.flac", 660, album="Flat", albumartist="Some Artist", title="x")
        self.y = audio(artist / "02 y.flac", 770, album="Flat", albumartist="Some Artist", title="y")
        for f in (self.x, self.y):
            give(f, jpeg(600, 600))
        (artist / "artist.jpg").write_bytes(jpeg(400, 400, (1, 2, 3)))
        self.artist = artist

    def test_in_place_users_photo(self):
        self.run_one(self.prof(), self.prop(folder=self.artist, files=[self.x, self.y]))
        self.assertIn((8, (400, 400)), pictures(self.x), f"a downloaded photo, not the user's artist.jpg "
                                                         f"(lookups: {FakeCoverUp.calls})")


if __name__ == "__main__":
    unittest.main()
