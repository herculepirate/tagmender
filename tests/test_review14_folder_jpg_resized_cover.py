# Fourteenth independent review: this test failed before its fix.
# Fourteenth independent review (A): this test fails at eb6cfad (and at f4778b5).
# An album ripped flat into its artist folder (beside the artist's other album folders) with
# its cover saved twice at two sizes: cover.jpg (the full scan) and folder.jpg (a 500 px copy,
# as Windows Media Player, MediaMonkey or dBpoweramp save it). folder_jpg_is_the_albums only
# counts folder.jpg as the album's when it is BYTE-identical to cover.jpg/front.jpg, so the
# album's own folder.jpg is taken for the artist's image: it stays behind in the artist folder
# when the album moves (Jellyfin/Kodi then show that album's cover as the artist), and no
# artist photo is ever looked up for the artist's other albums.
import os as _os, tempfile as _tempfile
_os.environ.setdefault("XDG_CONFIG_HOME", _tempfile.mkdtemp(prefix="tm-config-"))
_os.environ.setdefault("XDG_STATE_HOME", _tempfile.mkdtemp(prefix="tm-state-"))
import io, os, sys, unittest
from pathlib import Path
REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO)); sys.path.insert(0, str(REPO / "tests"))
import artwork, executor, plan as pl  # noqa
from test_review8_artist import R8  # noqa
from test_artist_photo import FakeCoverUp, pictures  # noqa
from test_cover_replace import jpeg  # noqa
from test_executor import audio, target  # noqa
from PIL import Image  # noqa


def resized(data, side):
    with Image.open(io.BytesIO(data)) as im:
        buf = io.BytesIO()
        im.resize((side, side)).save(buf, "JPEG", quality=85)
        return buf.getvalue()


for _n in dir(R8):
    if _n.startswith("test_"):
        setattr(R8, _n, None)


class FlatAlbumFolderJpgSmallerCopy(R8):
    def flat(self):
        artist = self.album.parent
        x = audio(artist / "01 x.flac", 660, album="Flat", albumartist="Some Artist", title="x")
        y = audio(artist / "02 y.flac", 770, album="Flat", albumartist="Some Artist", title="y")
        cover = jpeg(1200, 1200, (200, 0, 0))
        (artist / "cover.jpg").write_bytes(cover)
        (artist / "folder.jpg").write_bytes(resized(cover, 500))     # the same picture, smaller
        return artist, x, y

    def test_folder_jpg_goes_with_the_flat_album(self):
        artist, x, y = self.flat()
        prof = self.profile(operation="move", destination=str(self.dest))
        prop = self.proposal(source_dir=str(artist), files=[str(x), str(y)],
                             targets=[target(n + 1, t, album="Flat", artist="Some Artist")
                                      for n, t in enumerate("xy")])
        prop.group_files = [str(x), str(y)]
        _p, res = self.run_one(prof, prop)
        self.assertEqual(res["counts"], {"done": 1}, res)
        self.assertFalse((artist / "cover.jpg").exists())
        # (an artist folder's folder.jpg is never carried off - it may be the artist's image - but
        # one that looks like the album's cover is no reason to skip the artist photo: next test)
        self.assertTrue((artist / "folder.jpg").exists())

    def test_is_the_albums(self):
        artist, _x, _y = self.flat()
        self.assertTrue(artwork.folder_jpg_is_the_albums(artist),
                        "folder.jpg, the album's cover at another size, is taken for the artist's image")


if __name__ == "__main__":
    unittest.main()
