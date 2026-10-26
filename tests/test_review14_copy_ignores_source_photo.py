# Fourteenth independent review: this test failed before its fix.
# Fourteenth independent review (A): this test fails at eb6cfad (and at f4778b5).
# A copy profile (a lossy or mirror copy of the library) whose copies exist already - made
# before the artist photo was switched on, or before the user put an artist.jpg into the
# source artist folder. Engine._add_artist_photo then judges only the COPIES' artist folder
# ("home = artist_dir"): it has no photo, so the artist is looked up on the network and a
# download goes into every copy and into the destination's artist.jpg - while the user's own
# artist.jpg sits in the source artist folder, ignored. (A first copy, by contrast, takes the
# source's artist.jpg along.)
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


class CopiesIgnoreSourceArtistPhoto(R8):
    def test_copies_take_the_users_photo(self):
        off = dict(self.prof()["artwork"], artist={"enabled": False})
        prof0 = self.profile(artwork=off, tag_level="enthusiast", operation="copy", destination=str(self.dest))
        _p, res = self.run_one(prof0, self.prop())                    # copies made, no artist photo yet
        self.assertEqual(res["counts"], {"done": 1}, res)
        copies = sorted(self.dest.rglob("*.flac"))
        self.assertTrue(copies)
        (self.album.parent / "artist.jpg").write_bytes(jpeg(400, 400, (1, 2, 3)))     # the user's own
        prof = self.profile(artwork=self.prof()["artwork"], tag_level="enthusiast", operation="copy",
                            destination=str(self.dest))
        _p, res = self.run_one(prof, self.prop())
        self.assertIn((8, (400, 400)), pictures(copies[0]),
                      f"the copies got a download, not the user's artist.jpg: {pictures(copies[0])}")
        self.assertEqual(FakeCoverUp.calls, [], "looked up although the source artist folder has the user's photo")


if __name__ == "__main__":
    unittest.main()
