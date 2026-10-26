# Thirteenth independent review: this test failed before its fix.
# Thirteenth independent review: an album's WebP cover when the album moves.
import os as _os, tempfile as _tempfile
_os.environ.setdefault("XDG_CONFIG_HOME", _tempfile.mkdtemp(prefix="tm-config-"))
_os.environ.setdefault("XDG_STATE_HOME", _tempfile.mkdtemp(prefix="tm-state-"))
import io, os, sys, unittest
from pathlib import Path
REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO)); sys.path.insert(0, str(REPO / "tests"))
import executor, plan as pl  # noqa
from test_executor import Base  # noqa
from PIL import Image  # noqa


class WebpCoverStaysBehind(Base):
    """An album folder with its cover as cover.webp (Jellyfin, foobar2000 and Navidrome
    read it; TagMender's own artwork code counts .webp as a picture - IMAGE_EXT, needs_art).
    The default "companions" list has .jpg/.jpeg/.png but no .webp, and in a folder holding
    music only companion extensions go: moved, the album leaves its cover behind in the old
    folder (which then is never removed) and arrives with no cover file."""
    def test_cover_goes(self):
        buf = io.BytesIO()
        Image.new("RGB", (500, 500), (9, 9, 9)).save(buf, "WEBP")
        (self.album / "cover.webp").write_bytes(buf.getvalue())
        prof = self.profile(operation="move", destination=str(self.dest))
        p, res = self.run_plan(prof, self.proposal())
        self.assertEqual(res["counts"], {"done": 1}, res)
        self.assertFalse((self.album / "cover.webp").exists(), "cover.webp stayed behind")
        self.assertTrue(list(self.dest.rglob("cover.webp")), "the moved album has no cover file")


if __name__ == "__main__":
    unittest.main()
