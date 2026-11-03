# Fifteenth independent review (A).
# Plex and Jellyfin read an artist's poster from poster.jpg in the artist folder (both list it
# with folder.jpg/artist.jpg as the artist's primary image), and Plex its background from
# art.jpg. ARTIST_ART (artwork.py) knows artist, artist-poster, fanart, logo, banner, backdrop,
# background ... but not "poster" or "art". So when an album ripped flat into a SURE artist
# folder (named after the artist, holding the artist's other albums) moves, the artist's
# poster.jpg and art.jpg are carried off into that album's new folder - where poster.jpg is now
# that album's cover for Plex/Jellyfin, and the artist folder has lost its poster - while the
# folder.jpg beside them (just as ambiguous) correctly stays.
import os as _os, tempfile as _tempfile
_os.environ.setdefault("XDG_CONFIG_HOME", _tempfile.mkdtemp(prefix="tm-config-"))
_os.environ.setdefault("XDG_STATE_HOME", _tempfile.mkdtemp(prefix="tm-state-"))
import os, sys, unittest
from pathlib import Path
REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO)); sys.path.insert(0, str(REPO / "tests"))
import artwork, executor, plan as pl  # noqa
from test_review8_artist import R8  # noqa
from test_cover_replace import jpeg  # noqa
from test_executor import audio, target  # noqa

for _n in dir(R8):
    if _n.startswith("test_"):
        setattr(R8, _n, None)


class PosterAndArtCarriedOff(R8):
    def test_artist_poster_and_background_stay(self):
        artist = self.album.parent
        audio(artist.parent / "Some Artist" / "2005 - Third" / "01 t.flac", 330, album="Third", title="t",
              artist="Some Artist")
        x = audio(artist / "01 x.flac", 660, album="Flat", albumartist="Some Artist", title="x")
        y = audio(artist / "02 y.flac", 770, album="Flat", albumartist="Some Artist", title="y")
        (artist / "cover.jpg").write_bytes(jpeg(450, 450, (200, 0, 0)))          # the flat album's
        (artist / "folder.jpg").write_bytes(jpeg(680, 1000, (9, 9, 9)))          # the artist's
        art = {"poster.jpg": jpeg(680, 1000, (1, 2, 3)), "art.jpg": jpeg(1920, 1080, (4, 5, 6))}
        for n, data in art.items():
            (artist / n).write_bytes(data)
        prof = self.profile(operation="move", destination=str(self.dest))
        prop = self.proposal(source_dir=str(artist), files=[str(x), str(y)],
                             targets=[target(n + 1, t, album="Flat", artist="Some Artist")
                                      for n, t in enumerate("xy")])
        prop.group_files = [str(x), str(y)]
        p = pl.build([prop], prof)
        res = executor.Executor(p).run()
        self.assertEqual(res["counts"], {"done": 1}, res)
        self.assertFalse((artist / "cover.jpg").exists())                         # the album's went
        self.assertTrue((artist / "folder.jpg").exists())                         # the artist's stayed
        gone = [n for n in art if not (artist / n).exists()]
        self.assertEqual(gone, [], f"the artist folder's {gone} were carried off with the flat album: "
                                   f"{sorted(str(q.relative_to(self.dest)) for q in self.dest.rglob('*.jpg'))}")


if __name__ == "__main__":
    unittest.main()
