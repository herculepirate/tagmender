# Thirteenth independent review: this test failed before its fix.
# Thirteenth independent review: the user's artist photo saved as WebP.
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

for _n in dir(R8):
    if _n.startswith("test_"):
        setattr(R8, _n, None)


def webp(w, h, color=(1, 2, 3)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (w, h), color).save(buf, "WEBP", quality=90)
    return buf.getvalue()


class WebpArtistPhoto(R8):
    """The artist folder holds the user's photo as artist.webp (Jellyfin and Kodi read it;
    artist_folder_keeps even counts .webp as the artist's art). artist_folder_photo looks at
    .jpg/.jpeg/.png only: the artist is looked up on the network, a downloaded artist.jpg is
    written beside the user's artist.webp (two artist images, the media server may pick the
    download), and the downloaded photo - not the user's - goes into every track."""
    def setUp(self):
        super().setUp()
        (self.album.parent / "artist.webp").write_bytes(webp(400, 400))

    def test_no_lookup_and_no_second_photo(self):
        self.run_one(self.prof(), self.prop())
        self.assertEqual(FakeCoverUp.calls, [], "looked up although the artist folder has its photo")
        self.assertFalse((self.album.parent / "artist.jpg").exists(),
                         "a downloaded artist.jpg was written beside the user's artist.webp")

    def test_users_photo_in_the_tracks(self):
        self.run_one(self.prof(), self.prop())
        self.assertIn((8, (400, 400)), pictures(self.f1), "the tracks got a downloaded photo, not artist.webp")


if __name__ == "__main__":
    unittest.main()
