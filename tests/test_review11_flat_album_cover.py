# Eleventh independent review: this test failed before its fix.
# Round 11: an album ripped flat into its artist folder (beside the artist's album folders),
# its cover folder.jpg. Moved into a folder of its own, the album leaves folder.jpg behind
# (src_shared: foreign + named_after) - and once the flat tracks are gone,
# artist_folder_photo() takes that ALBUM cover for the artist's photo: embedded into every
# other album's tracks as the "Artist/performer" picture, and no artist.jpg is ever written.
import os as _os, tempfile as _tempfile
_os.environ.setdefault("XDG_CONFIG_HOME", _tempfile.mkdtemp(prefix="tm-config-"))
_os.environ.setdefault("XDG_STATE_HOME", _tempfile.mkdtemp(prefix="tm-state-"))
import sys, unittest
from pathlib import Path
from unittest import mock
REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO)); sys.path.insert(0, str(REPO / "tests"))
import artwork, executor, plan as pl  # noqa
from test_review8_artist import R8  # noqa
from test_artist_photo import FakeCoverUp, pictures, ARTIST_MBID  # noqa
from test_cover_replace import MBID, jpeg, size  # noqa
from test_executor import audio, target  # noqa

for _n in dir(R8):
    if _n.startswith("test_"):
        setattr(R8, _n, None)


class FlatAlbum(R8):
    def setUp(self):
        super().setUp()
        self.artist = self.album.parent
        self.x = audio(self.artist / "01 x.flac", 660, album="Flat", albumartist="Some Artist", title="x")
        self.y = audio(self.artist / "02 y.flac", 770, album="Flat", albumartist="Some Artist", title="y")
        (self.artist / "folder.jpg").write_bytes(jpeg(700, 700, (255, 0, 0)))     # Flat's cover

    def move_flat(self, prof):
        prop = self.proposal(source_dir=str(self.artist), files=[str(self.x), str(self.y)],
                             targets=[target(n + 1, t, album="Flat", artist="Some Artist",
                                             musicbrainz_albumid=[MBID], musicbrainz_albumartistid=[ARTIST_MBID])
                                      for n, t in enumerate("xy")])
        prop.group_files = [str(self.x), str(self.y)]
        return self.run_one(prof, prop)

    def test_flat_album_takes_its_cover(self):
        prof = self.profile(operation="move", destination=str(self.dest))
        p, res = self.move_flat(prof)
        self.assertEqual(res["counts"], {"done": 1}, res)
        self.assertFalse((self.artist / "folder.jpg").exists(),
                         "the flat album's cover stayed in the artist folder (it moved with it before 98f62ae)")

    def test_left_cover_becomes_the_artist_photo(self):
        prof = self.profile(operation="move", destination=str(self.dest))
        self.move_flat(prof)                                    # (run 1: organise, Basic level)
        if not (self.artist / "folder.jpg").exists():
            self.skipTest("cover moved with the album")
        # run 2 (Enthusiast now): the artist's other album, in place
        self.run_one(self.prof(), self.prop())
        got = pictures(self.f1)
        self.assertNotIn((8, (700, 700)), got,
                         f"Flat's album cover embedded as the artist's photo: {got}; lookups {FakeCoverUp.calls}")


if __name__ == "__main__":
    unittest.main()
