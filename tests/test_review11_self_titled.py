# Eleventh independent review: this test failed before its fix.
# Round 11: a self-titled album ("Band/Band": album "Band" by Band) with another album nested
# in it (the ninth-review NestedLiveAlbum layout). named_after(src_dir, "Band") is true for the
# ALBUM folder, so src_shared keeps its cover, rip log and cue behind (they moved before 98f62ae).
import os as _os, tempfile as _tempfile
_os.environ.setdefault("XDG_CONFIG_HOME", _tempfile.mkdtemp(prefix="tm-config-"))
_os.environ.setdefault("XDG_STATE_HOME", _tempfile.mkdtemp(prefix="tm-state-"))
import sys, unittest
from pathlib import Path
REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO)); sys.path.insert(0, str(REPO / "tests"))
from test_review9_companions import B  # noqa
from test_executor import audio  # noqa


class SelfTitledWithNestedAlbum(B):
    def test_album_takes_its_cover_and_log(self):
        m = self.lib / "Band" / "Band"
        a = [audio(m / "01 a.flac", 500, album="Band", albumartist="Band"),
             audio(m / "02 b.flac", 510, album="Band", albumartist="Band")]
        live = m / "Live 1995"
        audio(live / "01 x.flac", 600, album="Live 1995", albumartist="Band")
        audio(live / "02 y.flac", 610, album="Live 1995", albumartist="Band")
        (m / "cover.jpg").write_bytes(b"main cover")
        (m / "rip.log").write_text("EAC")
        self.move_main(m, a)
        left = sorted(x.name for x in m.iterdir() if x.is_file())
        self.assertEqual(left, [], f"the self-titled album's own cover/log stayed behind: {left}")


if __name__ == "__main__":
    unittest.main()
