# Fifteenth independent review (A).
# Albums of one artist are moved into a destination library over two runs. Run 1 files an
# album from a folder without an artist photo: TagMender downloads one and writes it as the
# destination's artist.jpg (recorded as a download). Run 2 moves an album out of an artist
# folder holding the USER'S OWN artist.jpg. Planner._artist_photo sets `there = None` ("the
# user's own photo wins"), but then recomputes has_image with artist_folder_has_photo(), which
# finds the download again - so no artist.jpg is planned, and the download stays the artist
# folder's photo (what Plex/Jellyfin/Navidrome show), while only the moved album's tracks get
# the user's photo. In ONE run the same two albums end with the user's photo as artist.jpg
# (the claims takeover), so the result depends on how the user batched the moves.
import os as _os, tempfile as _tempfile
_os.environ.setdefault("XDG_CONFIG_HOME", _tempfile.mkdtemp(prefix="tm-config-"))
_os.environ.setdefault("XDG_STATE_HOME", _tempfile.mkdtemp(prefix="tm-state-"))
import os, sys, unittest
from pathlib import Path
REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO)); sys.path.insert(0, str(REPO / "tests"))
import artwork, executor, plan as pl, profiles as pf  # noqa
from test_review8_artist import R8  # noqa
from test_artist_photo import ARTIST_MBID, FakeCoverUp, pictures  # noqa
from test_cover_replace import MBID, jpeg, give, size  # noqa
from test_executor import audio, target  # noqa

for _n in dir(R8):
    if _n.startswith("test_"):
        setattr(R8, _n, None)


class MoveDownloadBeatsUsersPhoto(R8):
    def test_users_photo_becomes_the_artist_jpg(self):
        lib2 = self.tmp / "lib2"
        xdir = lib2 / "Inbox" / "Some Artist - Other"
        xs = [audio(xdir / "01 p.flac", 660, album="Other", title="p", artist="Some Artist"),
              audio(xdir / "02 q.flac", 670, album="Other", title="q", artist="Some Artist")]
        for f in xs:
            give(f, jpeg(600, 600))
        art = self.prof()["artwork"]
        mprof = pf.from_dict({"name": "t", "sources": [{"path": str(self.lib)}, {"path": str(lib2)}],
                              "thresholds": {"organize_min_confidence": 95}, "artwork": art,
                              "tag_level": "enthusiast", "operation": "move", "destination": str(self.dest)})
        px = self.proposal(source_dir=str(xdir), files=[str(f) for f in xs],
                           targets=[target(n + 1, t, album="Other", artist="Some Artist", musicbrainz_albumid=[MBID],
                                           musicbrainz_albumartistid=[ARTIST_MBID]) for n, t in enumerate("pq")])
        _p, res = self.run_one(mprof, px)                               # run 1: a download at the destination
        self.assertEqual(res["counts"], {"done": 1}, res)
        dart = self.dest / "Some Artist" / "artist.jpg"
        self.assertTrue(dart.exists() and artwork.written_by_us(dart), sorted(str(x) for x in self.dest.rglob("*")))
        self.assertEqual(size(dart.read_bytes()), (1000, 1000))
        (self.album.parent / "artist.jpg").write_bytes(jpeg(400, 400, (1, 2, 3)))      # the user's own
        _p, res = self.run_one(mprof, self.prop())                      # run 2: from the user's artist folder
        self.assertEqual(res["counts"], {"done": 1}, res)
        moved = [f for f in self.dest.rglob("*.flac") if f.name not in ("01 p.flac", "02 q.flac")]
        self.assertIn((8, (400, 400)), pictures(moved[0]))              # (its tracks: the user's photo)
        self.assertEqual(size(dart.read_bytes()), (400, 400),
                         "the destination artist folder keeps TagMender's download as its artist.jpg; "
                         "the user's own photo, brought by the moved album, is only in that album's tracks")
        self.assertFalse(artwork.written_by_us(dart))
        self.assertIn("downloaded artist photo replaced by yours", pl.preview(_p))
        again, res2 = self.run_one(mprof, self.prop())                  # (settles: nothing more to do)
        self.assertEqual(size(dart.read_bytes()), (400, 400))
        executor.undo(res["job"])                                       # run 2 undone: the download is back
        self.assertEqual(size(dart.read_bytes()), (1000, 1000))
        self.assertTrue(artwork.written_by_us(dart))


if __name__ == "__main__":
    unittest.main()
