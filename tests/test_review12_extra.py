# Twelfth review, around its fixes: what the new rules must keep doing.
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
from test_cover_replace import jpeg, png  # noqa
from test_executor import audio, target  # noqa

for _n in dir(R8):
    if _n.startswith("test_"):
        setattr(R8, _n, None)


class PngArtistPhoto(R8):
    def test_settles_in_place_and_after_a_move(self):
        (self.album.parent / "artist.png").write_bytes(png(400, 400))
        self.run_one(self.prof(), self.prop())
        self.assertIn((8, (400, 400)), pictures(self.f1))
        ino = os.stat(self.f1).st_ino
        p, _res = self.run_one(self.prof(), self.prop())
        self.assertEqual(os.stat(self.f1).st_ino, ino, pl.preview(p))
        prof = self.profile(artwork=self.prof()["artwork"], tag_level="enthusiast", operation="move",
                            destination=str(self.dest))
        p, res = self.run_one(prof, self.prop())
        self.assertEqual(res["counts"], {"done": 1}, res)
        self.assertEqual(FakeCoverUp.calls, [])
        moved = sorted(self.dest.rglob("*.flac"))
        self.assertIn((8, (400, 400)), pictures(moved[0]))
        self.assertNotIn("in place of the one TagMender embedded", pl.preview(p))
        self.assertTrue(list(self.dest.rglob("artist.jpg")), "the artist photo did not go with the album")


class LooseSingleBesideOneAlbum(R8):
    """A single loose beside ONE album folder in a folder not named after its artist: not
    taken for an artist folder's single - but the artist's art there still stays."""
    def test_art_stays_cover_goes(self):
        artist = self.album.parent
        s = audio(artist / "single.flac", 660, title="single", artist="Other Name")
        (artist / "fanart.jpg").write_bytes(jpeg(300, 300))
        (artist / "single.log").write_text("EAC")
        prof = self.profile(operation="move", destination=str(self.dest))
        prop = self.proposal(source_dir=str(artist), files=[str(s)],
                             targets=[target(1, "single", album="Single", artist="Other Name")])
        p, res = self.run_one(prof, prop)
        self.assertEqual(res["counts"], {"done": 1}, res)
        self.assertTrue((artist / "fanart.jpg").exists())
        self.assertFalse((artist / "single.log").exists())


class AlbumHere(unittest.TestCase):
    def test_rules(self):
        d = Path(_tempfile.mkdtemp())
        audio(d / "01 a.flac", 440, album="X")
        self.assertFalse(artwork.album_here(d))
        audio(d / "02 b.flac", 450, album="X")
        self.assertTrue(artwork.album_here(d))
        e = Path(_tempfile.mkdtemp())
        audio(e / "CD1" / "01 a.flac", 440)
        self.assertTrue(artwork.album_here(e))
        f = Path(_tempfile.mkdtemp())
        for n in range(2):
            audio(f / f"{n}.flac", 440 + n)
        self.assertFalse(artwork.album_here(f))
        audio(f / "2.flac", 460)
        self.assertTrue(artwork.album_here(f))
        self.assertFalse(artwork.album_here(f, single=[f / "0.flac"]))

    def test_artist_art_names(self):
        for n in ("artist", "fanart", "fanart1", "backdrop2", "logo", "clearlogo", "banner", "fanart-3"):
            self.assertTrue(artwork.ARTIST_ART.match(n), n)
        for n in ("cover", "folder", "front", "back", "scan1"):
            self.assertFalse(artwork.ARTIST_ART.match(n), n)


if __name__ == "__main__":
    unittest.main()
