# Fifteenth independent review (A).
# Many libraries name artist folders by the artist's SORT name ("Bowie, David/1977 - Low/",
# what %albumartistsort% gives, foobar2000/MusicBee/Mp3tag defaults). artwork.named_after only
# knows the artist's name as written and the "Beatles, The" form, never the album artist's sort
# name that MusicBrainz gives (albumartistsort). So artist_dir_for finds no artist folder:
# the user's own artist.jpg in "Artist, Some/" is ignored, the artist is looked up on the
# network, and the DOWNLOAD goes into every track - the user's photo loses to a download.
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
from test_cover_replace import jpeg  # noqa

for _n in dir(R8):
    if _n.startswith("test_"):
        setattr(R8, _n, None)


class SortNameFolder(R8):
    def test_users_photo_in_a_sort_name_artist_folder(self):
        sorted_dir = self.lib / "Artist, Some"
        os.replace(self.album.parent, sorted_dir)              # "Artist, Some/2001 - Some Album"
        self.album = sorted_dir / self.album.name
        self.f1, self.f2 = self.album / self.f1.name, self.album / self.f2.name
        (sorted_dir / "artist.jpg").write_bytes(jpeg(400, 400, (1, 2, 3)))   # the user's own photo
        prop = self.prop(folder=self.album, files=[self.f1, self.f2], albumartistsort=["Artist, Some"])
        _p, res = self.run_one(self.prof(), prop)
        self.assertEqual(res["counts"], {"done": 1}, res)
        self.assertNotIn((8, (800, 800)), pictures(self.f1),
                         "a downloaded artist photo went into the tracks beside the user's own artist.jpg")
        self.assertIn((8, (400, 400)), pictures(self.f1))
        self.assertEqual(FakeCoverUp.calls, [], "looked up although the artist folder has the user's photo")


if __name__ == "__main__":
    unittest.main()
