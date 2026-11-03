# Thirteenth independent review: this test failed before its fix.
# Thirteenth independent review: an album folder holding a nested album, its folder.jpg.
import os as _os, tempfile as _tempfile
_os.environ.setdefault("XDG_CONFIG_HOME", _tempfile.mkdtemp(prefix="tm-config-"))
_os.environ.setdefault("XDG_STATE_HOME", _tempfile.mkdtemp(prefix="tm-state-"))
import os, sys, unittest
from pathlib import Path
REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO)); sys.path.insert(0, str(REPO / "tests"))
import executor, plan as pl  # noqa
from test_review9_companions import B  # noqa
from test_cover_replace import jpeg  # noqa
from test_executor import audio  # noqa

for _n in dir(B):
    if _n.startswith("test_"):
        setattr(B, _n, None)


class NestedLiveAlbumKeepsFolderJpg(B):
    """"Band/Main/" - the album's tracks, cover.jpg and folder.jpg (the same cover, as
    many rippers and Windows leave it) - holds a nested album "Live 1995/". _companions now
    takes ANY folder with another album below it (others) for an artist folder, and
    artist_folder_photo takes folder.jpg beside a cover.jpg for the artist's photo: Main's
    folder.jpg is kept as "the artist photo" and stays behind when Main moves, while its
    cover.jpg and log go. Before f4778b5 both went."""
    def test_folder_jpg_goes(self):
        m = self.lib / "Band" / "Main"
        a = [audio(m / "01 a.flac", 500, album="Main", albumartist="Band"),
             audio(m / "02 b.flac", 510, album="Main", albumartist="Band")]
        live = m / "Live 1995"
        audio(live / "01 x.flac", 600, album="Live 1995", albumartist="Band")
        audio(live / "02 y.flac", 610, album="Live 1995", albumartist="Band")
        cover = jpeg(450, 450, (200, 0, 0))
        (m / "cover.jpg").write_bytes(cover)
        (m / "folder.jpg").write_bytes(cover)
        (m / "rip.log").write_text("EAC")
        self.move_main(m, a)
        left = sorted(x.name for x in m.iterdir() if x.is_file())
        self.assertEqual(left, [], f"Main's own files stayed behind: {left}")


if __name__ == "__main__":
    unittest.main()
