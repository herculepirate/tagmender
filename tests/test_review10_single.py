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
from test_artist_photo import ARTIST_MBID  # noqa
from test_cover_replace import MBID, jpeg  # noqa
from test_executor import audio, target  # noqa

for _n in dir(R8):
    if _n.startswith("test_"):
        setattr(R8, _n, None)


class TwoTrackSingle(R8):
    """A 7" single (A-side, B-side) ripped loose into the artist folder beside its albums."""
    def go(self, name):
        artist = self.album.parent
        photo = jpeg(1000, 1000, (0, 255, 0))
        (artist / name).write_bytes(photo)
        a = audio(artist / "01 a-side.flac", 660, title="a", artist="Some Artist")
        b = audio(artist / "02 b-side.flac", 770, title="b", artist="Some Artist")
        prof = self.profile(artwork=self.prof()["artwork"], tag_level="enthusiast", operation="move",
                            destination=str(self.dest))
        prop = self.proposal(source_dir=str(artist), files=[str(a), str(b)],
                             targets=[target(n + 1, t, album="Single", artist="Some Artist", totaltracks="2",
                                             musicbrainz_albumid=[MBID], musicbrainz_albumartistid=[ARTIST_MBID])
                                      for n, t in enumerate("ab")])
        prop.group_files = [str(a), str(b)]
        p, res = self.run_one(prof, prop)
        comps = [c["src"] for al in p["albums"] for c in al["companions"]]
        self.assertTrue((artist / name).exists(),
                        f"{name} left the artist folder with a 2-track single; companions {comps}")

    def test_artist_jpg_stays(self):
        self.go("artist.jpg")

    def test_folder_jpg_stays(self):
        self.go("folder.jpg")


if __name__ == "__main__":
    unittest.main()
