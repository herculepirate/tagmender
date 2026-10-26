# Sixteenth independent review (A): this test failed before its fix.
# Sixteenth independent review (A). New in this version (the artist "rewrite" companion).
# Run 1 files an album into the destination and writes a DOWNLOADED artist.jpg there. Run 2
# moves another album of the artist, whose artist folder holds the user's own photo: the
# planner adds a companion "rewrite" {"artist": True} that replaces the download with the
# user's photo. Later the user puts a better photo of their own in as artist.jpg.
# Undoing run 2 now keeps the album where the job put it: undo treats the rewritten artist.jpg
# like an album's own cue sheet - "changed after the job", so its album stays (executor.undo:
# comps size/mtime check -> staying). The artist folder's photo is shared by every album
# there; for the artist.jpg a job CREATES, undo says so itself ("kind artist ... one changed
# later never keeps an album from going back") - but the one it REWROTE does keep the album.
# And `undo --force` then "puts the album back anyway, with the file as the job found it":
# the user's newer photo is overwritten with TagMender's old download.
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _review16a_common  # noqa: E402,F401

import artwork  # noqa: E402
import executor  # noqa: E402
import profiles as pf  # noqa: E402
from test_review8_artist import R8  # noqa: E402
from test_artist_photo import ARTIST_MBID  # noqa: E402
from test_cover_replace import MBID, jpeg, give, size  # noqa: E402
from test_executor import audio, target  # noqa: E402

for _n in dir(R8):
    if _n.startswith("test_"):
        setattr(R8, _n, None)


class UsersLaterPhotoBlocksUndo(R8):
    def setup_two_runs(self):
        lib2 = self.tmp / "lib2"
        xdir = lib2 / "Inbox" / "Some Artist - Other"
        xs = [audio(xdir / "01 p.flac", 660, album="Other", title="p", artist="Some Artist"),
              audio(xdir / "02 q.flac", 670, album="Other", title="q", artist="Some Artist")]
        for f in xs:
            give(f, jpeg(600, 600))
        self.mprof = pf.from_dict({"name": "t", "sources": [{"path": str(self.lib)}, {"path": str(lib2)}],
                                   "thresholds": {"organize_min_confidence": 95}, "artwork": self.prof()["artwork"],
                                   "tag_level": "enthusiast", "operation": "move", "destination": str(self.dest)})
        px = self.proposal(source_dir=str(xdir), files=[str(f) for f in xs],
                           targets=[target(n + 1, t, album="Other", artist="Some Artist", musicbrainz_albumid=[MBID],
                                           musicbrainz_albumartistid=[ARTIST_MBID]) for n, t in enumerate("pq")])
        _p, res = self.run_one(self.mprof, px)                          # run 1: a download at the destination
        self.assertEqual(res["counts"], {"done": 1}, res)
        self.dart = self.dest / "Some Artist" / "artist.jpg"
        self.assertTrue(artwork.written_by_us(self.dart))
        (self.album.parent / "artist.jpg").write_bytes(jpeg(400, 400, (1, 2, 3)))      # the user's own
        self.src_files = [self.f1, self.f2]
        _p, res = self.run_one(self.mprof, self.prop())                 # run 2: the download replaced
        self.assertEqual(res["counts"], {"done": 1}, res)
        self.assertEqual(size(self.dart.read_bytes()), (400, 400))
        self.assertFalse(self.f1.exists())
        return res

    def test_undo_moves_the_album_back(self):
        res = self.setup_two_runs()
        better = jpeg(1200, 1200, (5, 6, 7))
        self.dart.write_bytes(better)                                   # the user's better photo, later
        out = executor.undo(res["job"])
        self.assertTrue(self.f1.exists(), f"the album was not put back: {out}")
        self.assertEqual(self.dart.read_bytes(), better)

    def test_undo_force_keeps_the_users_newer_photo(self):
        res = self.setup_two_runs()
        better = jpeg(1200, 1200, (5, 6, 7))
        self.dart.write_bytes(better)
        out = executor.undo(res["job"], force=True)
        self.assertTrue(self.f1.exists(), out)
        self.assertEqual(size(self.dart.read_bytes()), (1200, 1200),
                         f"undo --force overwrote the user's newer artist.jpg with the old download: {out}")


if __name__ == "__main__":
    unittest.main()
