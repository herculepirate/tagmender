# Thirteenth independent review: this test failed before its fix.
# Thirteenth independent review: a download TagMender wrote at the destination beats the
# user's own artist photo.
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
from test_cover_replace import MBID, jpeg, give  # noqa
from test_executor import audio, target  # noqa

for _n in dir(R8):
    if _n.startswith("test_"):
        setattr(R8, _n, None)


class DownloadAtDestinationReplacesUsersPhoto(R8):
    """Two libraries merged into one destination (move profile, two sources). Album X of the
    artist, from a library with no artist photo, moves first: TagMender downloads a photo and
    writes it as the destination's artist.jpg. Album Y of the same artist lives in an artist
    folder with the user's own artist.jpg; it was tagged in place earlier (the tracks carry
    the user's photo). When Y moves, the destination's artist.jpg - TagMender's own download
    - counts as "the artist folder's own photo" (authoritative): it REPLACES the picture made
    from the user's photo in every track of Y, and the user's artist.jpg stays behind in the
    old folder. The code's own rule is "never by a download"."""
    def test_users_photo_survives_the_move(self):
        lib2 = self.tmp / "lib2"
        xdir = lib2 / "Some Artist" / "2003 - Other"
        xs = [audio(xdir / "01 p.flac", 660, album="Other", title="p", artist="Some Artist"),
              audio(xdir / "02 q.flac", 670, album="Other", title="q", artist="Some Artist")]
        for f in xs:
            give(f, jpeg(600, 600))
        (self.album.parent / "artist.jpg").write_bytes(jpeg(400, 400, (1, 2, 3)))     # the user's
        self.run_one(self.prof(), self.prop())                                        # Y, in place
        self.assertIn((8, (400, 400)), pictures(self.f1))
        art = self.prof()["artwork"]
        prof = pf.from_dict({"name": "t", "sources": [{"path": str(self.lib)}, {"path": str(lib2)}],
                             "thresholds": {"organize_min_confidence": 95}, "artwork": art,
                             "tag_level": "enthusiast", "operation": "move", "destination": str(self.dest)})
        px = self.proposal(source_dir=str(xdir), files=[str(f) for f in xs],
                           targets=[target(n + 1, t, album="Other", artist="Some Artist", musicbrainz_albumid=[MBID],
                                           musicbrainz_albumartistid=[ARTIST_MBID]) for n, t in enumerate("pq")])
        _p, res = self.run_one(prof, px)                                              # X moves first
        self.assertEqual(res["counts"], {"done": 1}, res)
        self.assertTrue(list(self.dest.rglob("artist.jpg")), "X's move wrote no artist.jpg")
        _p, res = self.run_one(prof, self.prop())                                     # then Y
        self.assertEqual(res["counts"], {"done": 1}, res)
        moved = [f for f in self.dest.rglob("*.flac") if "Some Album" in str(f) or "Real Album" in str(f)]
        moved = moved or [f for f in self.dest.rglob("*.flac") if f.name not in ("01 p.flac", "02 q.flac")]
        self.assertTrue(moved, sorted(str(x) for x in self.dest.rglob("*")))
        self.assertIn((8, (400, 400)), pictures(moved[0]),
                      "the picture from the user's own artist.jpg was replaced by TagMender's download")


if __name__ == "__main__":
    unittest.main()
