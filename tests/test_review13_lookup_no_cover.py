# Thirteenth independent review: this test failed before its fix.
# Thirteenth independent review: an artist lookup on every run for an album with no cover.
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
from test_executor import audio, target  # noqa

for _n in dir(R8):
    if _n.startswith("test_"):
        setattr(R8, _n, None)


class NoCoverNoMbidLookupEveryRun(R8):
    """A Discogs-only match (no MusicBrainz artist ID) in a folder not named after the album
    artist ("Some Artist" holding an album by "Someone Else"): no front cover is found for it,
    so the artist photo is never embedded (never a track's only picture) - and the tracks
    "lack" it for ever. With no ID and no artist folder the lookup key is the name, which is
    never kept (lasting False): the artist is looked up on the network on EVERY run, its photo
    thrown away each time."""
    def test_one_lookup(self):
        from mutagen.flac import FLAC
        for f in (self.f1, self.f2):
            t = FLAC(f)
            t.clear_pictures()
            t.save()
        def prop():
            tg = [target(n + 1, t, artist="Someone Else") for n, t in enumerate("ab")]
            return self.proposal(targets=tg)
        self.run_one(self.prof(), prop())
        self.assertEqual(pictures(self.f1), [])                  # (nothing found, nothing embedded)
        FakeCoverUp.calls = []
        for _ in range(2):
            self.run_one(self.prof(), prop())
        self.assertEqual(FakeCoverUp.calls, [], "the artist is looked up again on every run")


if __name__ == "__main__":
    unittest.main()
