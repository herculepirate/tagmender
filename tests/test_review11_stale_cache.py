# Eleventh independent review: this test failed before its fix.
# Round 11: a kept artist photo older than IMAGE_DAYS is looked up again; when that lookup
# finds nothing ("no artist photo found" - also what an outage of Wikimedia/Discogs reports)
# remember_artist_photo() only touches .none and leaves the old .jpg, which
# cached_artist_photo() checks first: never "known" again -> a lookup on EVERY run, and the
# album gets no photo at all although one is kept.
import os as _os, tempfile as _tempfile
_os.environ.setdefault("XDG_CONFIG_HOME", _tempfile.mkdtemp(prefix="tm-config-"))
_os.environ.setdefault("XDG_STATE_HOME", _tempfile.mkdtemp(prefix="tm-state-"))
import sys, time, unittest
from pathlib import Path
from unittest import mock
REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO)); sys.path.insert(0, str(REPO / "tests"))
import artwork  # noqa
from test_review8_artist import R8  # noqa
from test_artist_photo import FakeCoverUp, ARTIST_MBID  # noqa
from test_cover_replace import Engine  # noqa

for _n in dir(R8):
    if _n.startswith("test_"):
        setattr(R8, _n, None)


class NoneFound(FakeCoverUp):
    def artist_photo(self, folder, ids, artist="", files=None):
        FakeCoverUp.calls.append(artist)
        return {"note": "no artist photo found"}


class StalePhotoThenNoneFound(R8):
    def test_no_lookup_every_run(self):
        prof = self.profile(artwork=self.prof()["artwork"], tag_level="enthusiast", operation="move",
                            destination=str(self.dest))
        e = Engine(prof)
        with mock.patch.object(artwork, "chain", lambda *a, **k: []):
            e.add_artwork(self.prop())                  # run 1: found and kept
        img = next(artwork._photo_cache_dir().glob("*.jpg"))
        old = time.time() - 200 * 86400
        _os.utime(img, (old, old))                      # 200 days later
        FakeCoverUp.calls = []
        got = []
        for _run in range(3):
            with mock.patch.object(artwork, "CoverUp", NoneFound), \
                    mock.patch.object(artwork, "chain", lambda *a, **k: []):
                pr = self.prop()
                Engine(prof).add_artwork(pr)
                got.append(pr.artist_photo)
        self.assertLessEqual(len(FakeCoverUp.calls), 1, f"looked up on every run: {FakeCoverUp.calls}")
        self.assertTrue(any(got), "the kept photo is no longer used at all")


if __name__ == "__main__":
    unittest.main()
