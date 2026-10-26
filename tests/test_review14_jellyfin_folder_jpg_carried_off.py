# Fourteenth independent review: this test failed before its fix.
# Fourteenth independent review (A): this test fails at eb6cfad.
# An artist folder as Jellyfin/Kodi keep it: folder.jpg is the ARTIST's image (a portrait),
# beside the artist's album folders - and one album ripped flat into the artist folder whose
# cover is only embedded in its tracks (no cover.jpg/front.jpg file). folder_jpg_is_the_albums
# says folder.jpg is that album's cover whenever "the album has no other cover file", without
# looking at the cover the tracks carry: when the flat album moves, the artist's image is
# carried off into the album's new folder - where it now is that album's cover file (players
# show the artist's portrait as the album cover, and no cover is ever looked up for it).
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
from test_cover_replace import jpeg, give  # noqa
from test_executor import audio, target  # noqa

for _n in dir(R8):
    if _n.startswith("test_"):
        setattr(R8, _n, None)


class JellyfinArtistImageCarriedOff(R8):
    def test_artists_folder_jpg_stays(self):
        artist = self.album.parent
        portrait = jpeg(680, 1000, (10, 200, 10))                       # Jellyfin's artist image
        (artist / "folder.jpg").write_bytes(portrait)
        x = audio(artist / "01 x.flac", 660, album="Flat", albumartist="Some Artist", title="x")
        y = audio(artist / "02 y.flac", 770, album="Flat", albumartist="Some Artist", title="y")
        for f in (x, y):
            give(f, jpeg(600, 600, (200, 0, 0)))                        # the flat album's own cover
        prof = self.profile(operation="move", destination=str(self.dest))
        prop = self.proposal(source_dir=str(artist), files=[str(x), str(y)],
                             targets=[target(n + 1, t, album="Flat", artist="Some Artist")
                                      for n, t in enumerate("xy")])
        prop.group_files = [str(x), str(y)]
        _p, res = self.run_one(prof, prop)
        self.assertEqual(res["counts"], {"done": 1}, res)
        took = [p for p in self.dest.rglob("folder.jpg") if p.read_bytes() == portrait]
        self.assertTrue((artist / "folder.jpg").exists() and not took,
                        f"the artist folder's own image (folder.jpg, not the album's embedded cover) was "
                        f"carried off with the flat album: {[str(p.relative_to(self.dest)) for p in took]}")


if __name__ == "__main__":
    unittest.main()
