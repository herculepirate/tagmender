# Fifteenth independent review (A).
# TagMender wrote a downloaded artist.jpg into the artist folder. The user, not liking it,
# drops their own photo in as artist.png (or artist.webp) - without deleting TagMender's file.
# artist_folder_photo returns the FIRST "artist.*" by name: artist.jpg sorts before
# artist.png, so the download - recorded in written.json as TagMender's - keeps being "the
# artist folder's photo", and the user's own photo is never used: it never goes into the tracks.
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

for _n in dir(R8):
    if _n.startswith("test_"):
        setattr(R8, _n, None)


def png(w, h, color):
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (w, h), color).save(buf, "PNG")
    return buf.getvalue()


class UsersPngBesideDownload(R8):
    def test_users_png_wins_over_the_download(self):
        prof = self.prof()
        self.run_one(prof, self.prop())                                 # a download: artist.jpg
        dl = self.album.parent / "artist.jpg"
        self.assertTrue(dl.exists() and artwork.written_by_us(dl))
        self.assertIn((8, (800, 800)), pictures(self.f1))
        (self.album.parent / "artist.png").write_bytes(png(400, 400, (1, 2, 3)))       # the user's own
        self.run_one(prof, self.prop())
        self.assertIn((8, (400, 400)), pictures(self.f1),
                      f"the user's own artist.png is ignored beside TagMender's downloaded artist.jpg: "
                      f"{pictures(self.f1)}")


if __name__ == "__main__":
    unittest.main()
