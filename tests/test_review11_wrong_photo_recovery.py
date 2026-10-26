# Eleventh independent review: this test failed before its fix.
# Round 11: FIRST_RUN.md's recovery for a wrong artist photo ("replace or delete the artist
# folder's artist.jpg - your own photo there is the one TagMender uses from then on - and
# `tagmender artist-photos --forget`") leaves the wrong photo embedded in every track already
# done: the plan only embeds into tracks WITHOUT an artist picture.
import os as _os, tempfile as _tempfile
_os.environ.setdefault("XDG_CONFIG_HOME", _tempfile.mkdtemp(prefix="tm-config-"))
_os.environ.setdefault("XDG_STATE_HOME", _tempfile.mkdtemp(prefix="tm-state-"))
import sys, unittest
from pathlib import Path
REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO)); sys.path.insert(0, str(REPO / "tests"))
import artwork  # noqa
from test_review8_artist import R8  # noqa
from test_artist_photo import FakeCoverUp, pictures  # noqa
from test_cover_replace import jpeg  # noqa

for _n in dir(R8):
    if _n.startswith("test_"):
        setattr(R8, _n, None)


class Recovery(R8):
    def test_replaced_artist_jpg_reaches_the_tracks(self):
        prof = self.prof()
        self.run_one(prof, self.prop())                       # the wrong photo (a namesake's)
        self.assertIn((8, (800, 800)), pictures(self.f1))
        (self.album.parent / "artist.jpg").write_bytes(jpeg(500, 500, (0, 0, 255)))   # the user's fix
        artwork.forget_artist_photos("")
        self.run_one(prof, self.prop())
        got = pictures(self.f1)
        self.assertNotIn((8, (800, 800)), got, f"the wrong photo stays in the tracks: {got}")


if __name__ == "__main__":
    unittest.main()
