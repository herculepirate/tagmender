# Thirteenth independent review: this test failed before its fix.
# Thirteenth independent review: an artist folder holding one album, ripped flat into it.
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


class OnlyAlbumFlatInArtistFolder(R8):
    """"Lib/Some Artist/" holds the artist's only album, ripped flat, and the user's own
    artist.jpg (Kodi/Jellyfin). artist_dir_for takes the album folder for the artist folder
    only when it holds_other_albums: here it returns the parent - the library root - so None.
    The user's artist.jpg is ignored: the artist is looked up on the network and a downloaded
    photo goes into the tracks (in place), or is written into the new artist folder while the
    user's stays behind (moved). _companions meanwhile does take the folder for an artist
    folder (named after the album artist) and keeps artist.jpg there."""
    def setUp(self):
        super().setUp()
        shutil.rmtree(self.album)
        artist = self.lib / "Some Artist"
        self.x = audio(artist / "01 x.flac", 660, album="Flat", albumartist="Some Artist", title="x")
        self.y = audio(artist / "02 y.flac", 770, album="Flat", albumartist="Some Artist", title="y")
        for f in (self.x, self.y):
            give(f, jpeg(600, 600))
        (artist / "artist.jpg").write_bytes(jpeg(400, 400, (1, 2, 3)))
        self.artist = artist

    def test_in_place_users_photo(self):
        self.run_one(self.prof(), self.prop(folder=self.artist, files=[self.x, self.y]))
        self.assertEqual(FakeCoverUp.calls, [], "looked up although the artist folder has its photo")
        self.assertIn((8, (400, 400)), pictures(self.x), "a downloaded photo, not the user's artist.jpg")

    def test_moved_takes_users_photo(self):
        prof = self.profile(artwork=self.prof()["artwork"], tag_level="enthusiast", operation="move",
                            destination=str(self.dest))
        p, res = self.run_one(prof, self.prop(folder=self.artist, files=[self.x, self.y]))
        self.assertEqual(res["counts"], {"done": 1}, res)
        moved = sorted(self.dest.rglob("*.flac"))
        self.assertTrue(moved)
        self.assertEqual(FakeCoverUp.calls, [], "looked up although the artist folder has its photo")
        self.assertIn((8, (400, 400)), pictures(moved[0]), "a downloaded photo, not the user's artist.jpg")


if __name__ == "__main__":
    unittest.main()
