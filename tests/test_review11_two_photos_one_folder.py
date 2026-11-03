# Eleventh independent review: this test failed before its fix.
# Round 11: two albums of one artist folder in one run, one matched with a MusicBrainz artist
# ID (key = the ID), the other by Discogs only (key = "dir:<artist folder>"): two lookups, two
# different photos. The first is written as artist.jpg; the second album's tracks get the OTHER
# photo embedded (the plan's "artist folder's own photo wins" sees no artist.jpg yet - it is
# only planned), and keep it on later runs (they no longer lack an artist picture).
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
from test_cover_replace import Engine, MBID, jpeg, size, give  # noqa
from test_executor import audio, target  # noqa

for _n in dir(R8):
    if _n.startswith("test_"):
        setattr(R8, _n, None)


class Alternating(FakeCoverUp):
    def artist_photo(self, folder, ids, artist="", files=None):
        FakeCoverUp.calls.append(artist)
        side = 500 if len(FakeCoverUp.calls) == 1 else 700
        path, w, h = artwork._stage(jpeg(side, side, (side % 255, 0, 0)), self.staging)
        return {"image": str(path), "width": w, "height": h, "source": f"coverup:{side}"}


class TwoKeysOneFolder(R8):
    def test_one_photo_per_artist_folder(self):
        prof = self.prof()
        other = self.album.parent / "2003 - Other Album"
        g = [audio(other / "01 x.flac", 660, album="Other Album", title="x", artist="Some Artist"),
             audio(other / "02 y.flac", 770, album="Other Album", title="y", artist="Some Artist")]
        for f in g:
            give(f, jpeg(600, 600))
        a = self.prop()                                              # with the MusicBrainz artist ID
        b = self.proposal(source_dir=str(other), files=[str(f) for f in g],
                          targets=[target(n + 1, t, artist="Some Artist") for n, t in enumerate("xy")])  # Discogs only
        e = Engine(prof)
        with mock.patch.object(artwork, "CoverUp", Alternating), \
                mock.patch.object(artwork, "chain", lambda *a_, **k: []):
            for pr in (a, b):
                e.add_artwork(pr)
        p = pl.build([a, b], prof)
        executor.Executor(p).run()
        on_disk = size((self.album.parent / "artist.jpg").read_bytes())
        emb = {t: s for t, s in pictures(g[0])}.get(8)
        self.assertEqual(emb, on_disk, f"lookups {FakeCoverUp.calls}: artist.jpg {on_disk}, "
                                       f"Other Album's tracks {emb}, Some Album's {dict(pictures(self.f1)).get(8)}")


if __name__ == "__main__":
    unittest.main()
