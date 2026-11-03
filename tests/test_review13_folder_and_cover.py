# Thirteenth independent review: this test failed before its fix.
# Thirteenth independent review: folder.jpg beside cover.jpg - the album's two cover files.
import os as _os, tempfile as _tempfile
_os.environ.setdefault("XDG_CONFIG_HOME", _tempfile.mkdtemp(prefix="tm-config-"))
_os.environ.setdefault("XDG_STATE_HOME", _tempfile.mkdtemp(prefix="tm-state-"))
import os, sys, unittest
from pathlib import Path
REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO)); sys.path.insert(0, str(REPO / "tests"))
import artwork, executor, plan as pl  # noqa
from test_review8_artist import R8  # noqa
from test_artist_photo import FakeCoverUp, pictures  # noqa
from test_cover_replace import jpeg  # noqa
from test_executor import audio, target  # noqa

for _n in dir(R8):
    if _n.startswith("test_"):
        setattr(R8, _n, None)


class FlatAlbumWithBothCoverFiles(R8):
    """An album ripped flat into the artist folder (beside the artist's other album folders)
    with its cover saved twice, as cover.jpg AND folder.jpg - the same picture, as foobar2000,
    MusicBee, dBpoweramp + Windows Explorer leave it. artist_folder_photo now takes folder.jpg
    for the ARTIST's photo whenever a cover.jpg is beside it: the flat album's cover is
    embedded as the "Artist/performer" picture into the artist's other albums, and is left
    behind when the flat album moves. Before f4778b5, folder.jpg was the flat album's cover."""
    def flat(self):
        artist = self.album.parent
        x = audio(artist / "01 x.flac", 660, album="Flat", albumartist="Some Artist", title="x")
        y = audio(artist / "02 y.flac", 770, album="Flat", albumartist="Some Artist", title="y")
        cover = jpeg(450, 450, (200, 0, 0))                       # the flat album's cover
        (artist / "cover.jpg").write_bytes(cover)
        (artist / "folder.jpg").write_bytes(cover)                # the same picture again
        return artist, x, y

    def test_album_cover_is_not_embedded_as_the_artist_photo(self):
        self.flat()
        self.run_one(self.prof(), self.prop())                    # the artist's other album, in place
        self.assertNotIn((8, (450, 450)), pictures(self.f1),
                         "the flat album's cover went into another album as the artist photo")

    def test_folder_jpg_goes_with_the_flat_album(self):
        artist, x, y = self.flat()
        prof = self.profile(operation="move", destination=str(self.dest))
        prop = self.proposal(source_dir=str(artist), files=[str(x), str(y)],
                             targets=[target(n + 1, t, album="Flat", artist="Some Artist")
                                      for n, t in enumerate("xy")])
        prop.group_files = [str(x), str(y)]
        p, res = self.run_one(prof, prop)
        self.assertEqual(res["counts"], {"done": 1}, res)
        self.assertFalse((artist / "cover.jpg").exists())
        # (since review 14: an artist folder's folder.jpg is never carried off - it may be the
        # artist's image - nor ever put into tracks as the artist picture)
        self.assertTrue((artist / "folder.jpg").exists())


class FolderNamedAfterArtistNoOtherMusic(R8):
    """An album alone in a folder named after its album artist, no other music anywhere
    below: the artist's only album ripped flat into "Lib/Some Artist/", or a self-titled album
    in a library of album folders ("Lib/Boston/" holding Boston by Boston), with cover.jpg and
    folder.jpg. _companions now takes any folder named after the album artist for an artist
    folder (before f4778b5 only one holding other music too), so the album's folder.jpg is
    kept as "the artist photo" and stays behind, alone, when the album moves."""
    def test_folder_jpg_goes(self):
        d = self.lib / "Boston"
        x = audio(d / "01 x.flac", 660, album="Boston", albumartist="Boston", title="x")
        y = audio(d / "02 y.flac", 770, album="Boston", albumartist="Boston", title="y")
        cover = jpeg(450, 450, (200, 0, 0))
        (d / "cover.jpg").write_bytes(cover)
        (d / "folder.jpg").write_bytes(cover)
        prof = self.profile(operation="move", destination=str(self.dest))
        prop = self.proposal(source_dir=str(d), files=[str(x), str(y)],
                             targets=[target(n + 1, t, album="Boston", artist="Boston") for n, t in enumerate("xy")])
        prop.group_files = [str(x), str(y)]
        p, res = self.run_one(prof, prop)
        self.assertEqual(res["counts"], {"done": 1}, res)
        left = sorted(os.listdir(d)) if d.exists() else []
        self.assertEqual(left, [], f"left behind in the album's old folder: {left}")

    def test_only_album_flat_in_its_artist_folder(self):
        """The same with the artist's only album ripped flat into "Lib/Some Artist/"."""
        import shutil
        shutil.rmtree(self.album)
        d = self.lib / "Some Artist"
        x = audio(d / "01 x.flac", 660, album="Flat", albumartist="Some Artist", title="x")
        y = audio(d / "02 y.flac", 770, album="Flat", albumartist="Some Artist", title="y")
        cover = jpeg(450, 450, (200, 0, 0))
        (d / "cover.jpg").write_bytes(cover)
        (d / "folder.jpg").write_bytes(cover)
        prof = self.profile(operation="move", destination=str(self.dest))
        prop = self.proposal(source_dir=str(d), files=[str(x), str(y)],
                             targets=[target(n + 1, t, album="Flat", artist="Some Artist") for n, t in enumerate("xy")])
        prop.group_files = [str(x), str(y)]
        p, res = self.run_one(prof, prop)
        self.assertEqual(res["counts"], {"done": 1}, res)
        left = sorted(os.listdir(d)) if d.exists() else []
        self.assertEqual(left, [], f"left behind in the album's old folder: {left}")


if __name__ == "__main__":
    unittest.main()
