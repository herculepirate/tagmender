# Eleventh independent review: this test failed before its fix.
# Round 11: photos kept by the code before 98f62ae (found by NAME, kept under "dir:<artist
# folder>"; or any kept photo without a .key file) are still served by cached_artist_photo()
# for 180 days, and `tagmender artist-photos --forget` (all) never removes them: it only
# walks *.key files. The round-10 fix ("found by name only: never kept for later runs") does
# not reach the wrong photos already kept.
import os as _os, tempfile as _tempfile
_os.environ.setdefault("XDG_CONFIG_HOME", _tempfile.mkdtemp(prefix="tm-config-"))
_os.environ.setdefault("XDG_STATE_HOME", _tempfile.mkdtemp(prefix="tm-state-"))
import hashlib, sys, unittest
from pathlib import Path
REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO)); sys.path.insert(0, str(REPO / "tests"))
import artwork  # noqa
from test_executor import Base  # noqa
from test_cover_replace import jpeg  # noqa


class OldCache(Base):
    def test_old_name_found_photo_is_not_served_and_can_be_forgotten(self):
        key = f"dir:{_os.path.realpath(str(self.album.parent))}"
        h = hashlib.sha1(key.encode()).hexdigest()[:20]
        d = artwork._photo_cache_dir()
        d.mkdir(parents=True)
        (d / f"{h}.jpg").write_bytes(jpeg(300, 300))       # as 14d4f55 kept it (no .key)
        (d / f"{h}.src").write_text("coverup:audiodb")
        n = artwork.forget_artist_photos("")
        known, got = artwork.cached_artist_photo(key)
        self.assertFalse(known and got, f"a name-found photo from before is still served (forgot {n})")


if __name__ == "__main__":
    unittest.main()
