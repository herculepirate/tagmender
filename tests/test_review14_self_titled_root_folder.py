# Fourteenth independent review: this test failed before its fix.
# Fourteenth independent review (A): this test fails at eb6cfad and passes at f4778b5.
# A library of album folders ("Lib/<Album>/"): the self-titled album "Boston" by Boston lies
# in "Lib/Boston/". artist_dir_for now takes any folder named after the album artist right
# below a library root for the artist's folder ("or not inside(d)", for an artist's only album
# ripped flat into its artist folder), so tagging it in place writes a downloaded artist.jpg
# INTO THE ALBUM'S OWN FOLDER. When the album is later organised (moved), _companions keeps
# artist.jpg as "the artist folder's own art": it is left behind, alone, in "Lib/Boston/".
import os as _os, tempfile as _tempfile
_os.environ.setdefault("XDG_CONFIG_HOME", _tempfile.mkdtemp(prefix="tm-config-"))
_os.environ.setdefault("XDG_STATE_HOME", _tempfile.mkdtemp(prefix="tm-state-"))
import os, shutil, sys, unittest
from pathlib import Path
REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO)); sys.path.insert(0, str(REPO / "tests"))
import artwork, executor, plan as pl  # noqa
from test_review8_artist import R8  # noqa
from test_artist_photo import ARTIST_MBID, FakeCoverUp, pictures  # noqa
from test_cover_replace import MBID, jpeg, give  # noqa
from test_executor import audio, target  # noqa

for _n in dir(R8):
    if _n.startswith("test_"):
        setattr(R8, _n, None)


class SelfTitledAlbumFolderAtRoot(R8):
    def setUp(self):
        super().setUp()
        shutil.rmtree(self.album.parent)                   # a library of album folders: no artist level
        self.d = self.lib / "Boston"
        self.xs = [audio(self.d / "01 More Than a Feeling.flac", 660, album="Boston", albumartist="Boston", title="x"),
                   audio(self.d / "02 Peace of Mind.flac", 770, album="Boston", albumartist="Boston", title="y")]
        for f in self.xs:
            give(f, jpeg(600, 600))
        (self.d / "cover.jpg").write_bytes(jpeg(600, 600))

    def prop_b(self):
        return self.proposal(source_dir=str(self.d), files=[str(f) for f in self.xs],
                             targets=[target(n + 1, t, album="Boston", artist="Boston", musicbrainz_albumid=[MBID],
                                             musicbrainz_albumartistid=[ARTIST_MBID]) for n, t in enumerate("xy")])

    def test_no_artist_jpg_in_the_albums_own_folder(self):
        self.run_one(self.prof(), self.prop_b())
        self.assertFalse((self.d / "artist.jpg").exists(),
                         "the artist's photo was written into the self-titled album's own folder")

    def test_nothing_left_behind_when_organised_later(self):
        self.run_one(self.prof(), self.prop_b())                         # tagged in place first
        prof = self.profile(artwork=self.prof()["artwork"], tag_level="enthusiast", operation="move",
                            destination=str(self.dest))
        prop = self.prop_b()
        prop.group_files = [str(f) for f in self.xs]
        _p, res = self.run_one(prof, prop)
        self.assertEqual(res["counts"], {"done": 1}, res)
        left = sorted(os.listdir(self.d)) if self.d.exists() else []
        self.assertEqual(left, [], f"left behind in the album's old folder: {left}")


if __name__ == "__main__":
    unittest.main()
