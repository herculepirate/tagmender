# Fifteenth independent review (A).
# A copy profile (a lossy or mirror copy of the library) made the copies with a DOWNLOADED
# artist photo (the source artist folder had none then): the destination's artist.jpg is
# TagMender's download, and every copy carries it. The user then puts their own artist.jpg
# into the source artist folder. Engine._add_artist_photo judges the copies' artist folder
# first ("home = artist_dir"); it switches to the source's artist.jpg only when the copies'
# folder has NO artist.jpg - a download there counts as the artist's photo. So the user's own
# photo never reaches the copies or the destination's artist.jpg: the download wins for ever.
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
from test_cover_replace import jpeg, size  # noqa

for _n in dir(R8):
    if _n.startswith("test_"):
        setattr(R8, _n, None)


class CopyDownloadBeatsUsersPhoto(R8):
    def test_users_photo_reaches_the_copies(self):
        prof = self.profile(artwork=self.prof()["artwork"], tag_level="enthusiast", operation="copy",
                            destination=str(self.dest))
        _p, res = self.run_one(prof, self.prop())                    # copies made with a download
        self.assertEqual(res["counts"], {"done": 1}, res)
        copies = sorted(self.dest.rglob("*.flac"))
        dart = self.dest / "Some Artist" / "artist.jpg"
        self.assertTrue(copies and dart.exists(), sorted(str(x) for x in self.dest.rglob("*")))
        self.assertTrue(artwork.written_by_us(dart))
        self.assertIn((8, (800, 800)), pictures(copies[0]))
        (self.album.parent / "artist.jpg").write_bytes(jpeg(400, 400, (1, 2, 3)))     # the user's own
        FakeCoverUp.calls = []
        _p, res = self.run_one(prof, self.prop())
        self.assertIn((8, (400, 400)), pictures(copies[0]),
                      f"the copies keep TagMender's download although the source artist folder now has "
                      f"the user's own artist.jpg: {pictures(copies[0])}")
        self.assertEqual(size(dart.read_bytes()), (400, 400))       # (the copies' artist.jpg too)
        self.assertEqual(FakeCoverUp.calls, [])


if __name__ == "__main__":
    unittest.main()
