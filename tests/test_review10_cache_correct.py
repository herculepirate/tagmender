# Tenth independent review: this test failed before its fix.
import os as _os, tempfile as _tempfile
_os.environ.setdefault("XDG_CONFIG_HOME", _tempfile.mkdtemp(prefix="tm-config-"))
_os.environ.setdefault("XDG_STATE_HOME", _tempfile.mkdtemp(prefix="tm-state-"))
import sys, unittest
from pathlib import Path
from unittest import mock
REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO)); sys.path.insert(0, str(REPO / "tests"))
import artwork, executor, plan as pl  # noqa
from test_review9_artist import R9  # noqa
from test_artist_photo import FakeCoverUp, pictures  # noqa
from test_cover_replace import MBID, jpeg, size  # noqa
from test_executor import target  # noqa


class Right(FakeCoverUp):
    def artist_photo(self, folder, ids, artist="", files=None):
        FakeCoverUp.calls.append(artist)
        path, w, h = artwork._stage(jpeg(900, 900, (0, 200, 0)), self.staging)
        return {"image": str(path), "width": w, "height": h, "source": "coverup:audiodb"}


class WrongPhotoCannotBeCorrected(R9):
    """No MusicBrainz artist ID (Discogs match): CoverUp's name search returned a namesake's
    photo (run 1). The user deletes the wrong artist.jpg so that it is looked up again."""
    def test_deleting_the_wrong_artist_jpg_brings_it_back(self):
        def prop():
            tg = [target(n + 1, t, artist="Some Artist", musicbrainz_albumid=[MBID]) for n, t in enumerate("ab")]
            return self.proposal(targets=tg)
        prof = self.prof()
        self.run_one(prof, prop())
        photo = self.album.parent / "artist.jpg"
        self.assertEqual(size(photo.read_bytes()), (1000, 1000))     # the wrong one
        photo.unlink()
        FakeCoverUp.calls = []
        with mock.patch.object(artwork, "CoverUp", Right):
            self.run_one(prof, prop())
        self.assertNotEqual(size(photo.read_bytes()), (1000, 1000),
                            f"the deleted wrong photo was written back from the cache; lookups {FakeCoverUp.calls}")


if __name__ == "__main__":
    unittest.main()
