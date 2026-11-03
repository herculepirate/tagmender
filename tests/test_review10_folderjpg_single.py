# Tenth independent review: this test failed before its fix.
import os as _os, tempfile as _tempfile
_os.environ.setdefault("XDG_CONFIG_HOME", _tempfile.mkdtemp(prefix="tm-config-"))
_os.environ.setdefault("XDG_STATE_HOME", _tempfile.mkdtemp(prefix="tm-state-"))
import sys, unittest
from pathlib import Path
REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO)); sys.path.insert(0, str(REPO / "tests"))
import artwork, executor, plan as pl  # noqa
from test_review8_artist import R8  # noqa
from test_artist_photo import FakeCoverUp, pictures  # noqa
from test_cover_replace import jpeg  # noqa
from test_executor import audio  # noqa

for _n in dir(R8):
    if _n.startswith("test_"):
        setattr(R8, _n, None)


class FolderJpgBesideALooseSingle(R8):
    """Kodi/CoverUp artist folder: folder.jpg is the artist photo; one loose single track sits
    beside the album folders (the layout round 8 #7 treats as "folder.jpg is the artist's")."""
    def test_no_second_artist_photo(self):
        artist = self.album.parent
        (artist / "folder.jpg").write_bytes(jpeg(1500, 1500, (1, 2, 3)))
        audio(artist / "single.flac", 660, title="single", artist="Some Artist")
        self.run_one(self.prof(artist={"embed": False}), self.prop())
        self.assertEqual(FakeCoverUp.calls, [], "looked up although the artist folder has its photo")
        self.assertFalse((artist / "artist.jpg").exists(), "a second artist photo beside folder.jpg")


if __name__ == "__main__":
    unittest.main()
