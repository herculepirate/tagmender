# Tenth independent review: this test failed before its fix.
import os as _os, tempfile as _tempfile
_os.environ.setdefault("XDG_CONFIG_HOME", _tempfile.mkdtemp(prefix="tm-config-"))
_os.environ.setdefault("XDG_STATE_HOME", _tempfile.mkdtemp(prefix="tm-state-"))
import sys, unittest
from pathlib import Path
REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO)); sys.path.insert(0, str(REPO / "tests"))
from test_review9_companions import B  # noqa
from test_executor import audio  # noqa


class DiscSetWithNestedAlbum(B):
    """Main/CD1 + Main/CD2 (the album) + Main/Live 1995 (another album, not bonus-named):
    the two ninth-review cases together."""
    def test_disc_set_takes_its_cover(self):
        m = self.lib / "Band" / "Main"
        a = [audio(m / "CD1" / "01 a.flac", 500, album="Main", albumartist="Band", discnumber="1"),
             audio(m / "CD1" / "02 b.flac", 510, album="Main", albumartist="Band", discnumber="1"),
             audio(m / "CD2" / "01 c.flac", 520, album="Main", albumartist="Band", discnumber="2"),
             audio(m / "CD2" / "02 d.flac", 530, album="Main", albumartist="Band", discnumber="2")]
        live = m / "Live 1995"
        audio(live / "01 x.flac", 600, album="Live 1995", albumartist="Band")
        audio(live / "02 y.flac", 610, album="Live 1995", albumartist="Band")
        (m / "cover.jpg").write_bytes(b"main cover")
        (m / "rip.log").write_text("EAC")
        self.move_main(m, a)
        left = sorted(x.name for x in m.iterdir() if x.is_file())
        self.assertEqual(left, [], f"the disc set's own cover/log stayed behind: {left}")


if __name__ == "__main__":
    unittest.main()
