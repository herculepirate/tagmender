# Fourteenth independent review: this test failed before its fix.
# Fourteenth independent review (A): this test fails at eb6cfad (and at f4778b5).
# Plex's local artist art in an artist folder is "artist-poster.jpg" and
# "artist-background.jpg" (also "poster.jpg"). ARTIST_ART only knows the stem "artist" with an
# optional number ("artist2"), and fanart/logo/banner/backdrop/background..., so when an album
# ripped flat into the artist folder (beside the artist's album folders) moves, the artist
# folder's Plex art is carried off into that album's new folder.
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
from test_cover_replace import jpeg  # noqa
from test_executor import audio, target  # noqa

for _n in dir(R8):
    if _n.startswith("test_"):
        setattr(R8, _n, None)


class PlexArtistArtStays(R8):
    def test_plex_artist_art_stays_in_the_artist_folder(self):
        artist = self.album.parent
        x = audio(artist / "01 x.flac", 660, album="Flat", albumartist="Some Artist", title="x")
        y = audio(artist / "02 y.flac", 770, album="Flat", albumartist="Some Artist", title="y")
        (artist / "cover.jpg").write_bytes(jpeg(450, 450, (200, 0, 0)))          # the flat album's
        art = {"artist-poster.jpg": jpeg(400, 600, (1, 2, 3)), "artist-background.jpg": jpeg(1920, 1080, (4, 5, 6))}
        for n, data in art.items():
            (artist / n).write_bytes(data)
        prof = self.profile(operation="move", destination=str(self.dest))
        prop = self.proposal(source_dir=str(artist), files=[str(x), str(y)],
                             targets=[target(n + 1, t, album="Flat", artist="Some Artist")
                                      for n, t in enumerate("xy")])
        prop.group_files = [str(x), str(y)]
        _p, res = self.run_one(prof, prop)
        self.assertEqual(res["counts"], {"done": 1}, res)
        self.assertFalse((artist / "cover.jpg").exists())                         # the album's went
        for n in art:
            self.assertTrue((artist / n).exists(),
                            f"the artist folder's Plex art {n} was carried off with the flat album: "
                            f"{sorted(str(p.relative_to(self.dest)) for p in self.dest.rglob('*.jpg'))}")


if __name__ == "__main__":
    unittest.main()
