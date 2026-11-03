# Fourteenth independent review: this test failed before its fix.
# Fourteenth independent review (A): this test fails at eb6cfad.
# Two albums of one artist from two libraries, merged into one destination in ONE run: the
# first album planned (from a library without an artist photo) claims the destination artist
# folder with its DOWNLOAD; the second album, from an artist folder holding the user's own
# artist.jpg, then takes that claimed download (plan.Planner._artist_photo, "dkey in claims")
# - into its tracks, replacing the picture made from the user's photo (authoritative is True
# because ap["own"] is), and as the destination's artist.jpg.
import os as _os, tempfile as _tempfile
_os.environ.setdefault("XDG_CONFIG_HOME", _tempfile.mkdtemp(prefix="tm-config-"))
_os.environ.setdefault("XDG_STATE_HOME", _tempfile.mkdtemp(prefix="tm-state-"))
import os, sys, unittest
from pathlib import Path
from unittest import mock
REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO)); sys.path.insert(0, str(REPO / "tests"))
import artwork, executor, plan as pl, profiles as pf  # noqa
from test_review8_artist import R8  # noqa
from test_artist_photo import ARTIST_MBID, FakeCoverUp, pictures  # noqa
from test_cover_replace import MBID, Engine, jpeg, give, size  # noqa
from test_executor import audio, target  # noqa

for _n in dir(R8):
    if _n.startswith("test_"):
        setattr(R8, _n, None)


class SameRunClaimBeatsUsersPhoto(R8):
    def setUp(self):
        super().setUp()
        lib2 = self.tmp / "lib2"
        self.xdir = lib2 / "Some Artist" / "2003 - Other"
        self.xs = [audio(self.xdir / "01 p.flac", 660, album="Other", title="p", artist="Some Artist"),
                   audio(self.xdir / "02 q.flac", 670, album="Other", title="q", artist="Some Artist")]
        for f in self.xs:
            give(f, jpeg(600, 600))
        self.mine = jpeg(400, 400, (1, 2, 3))
        (self.album.parent / "artist.jpg").write_bytes(self.mine)            # the user's own photo
        art = self.prof()["artwork"]
        self.mprof = pf.from_dict({"name": "t", "sources": [{"path": str(self.lib)}, {"path": str(lib2)}],
                                   "thresholds": {"organize_min_confidence": 95}, "artwork": art,
                                   "tag_level": "enthusiast", "operation": "move", "destination": str(self.dest)})

    def px(self):
        return self.proposal(source_dir=str(self.xdir), files=[str(f) for f in self.xs],
                             targets=[target(n + 1, t, album="Other", artist="Some Artist", musicbrainz_albumid=[MBID],
                                             musicbrainz_albumartistid=[ARTIST_MBID]) for n, t in enumerate("pq")])

    def run_both(self):
        e = Engine(self.mprof)
        px, py = self.px(), self.prop()
        with mock.patch.object(artwork, "chain", lambda *a, **k: []):
            for pr in (px, py):
                e.add_artwork(pr)
        p = pl.build([px, py], self.mprof)                                   # X (no photo) planned first
        res = executor.Executor(p).run()
        self.assertEqual(res["counts"], {"done": 2}, res)
        return p

    def moved_y(self):
        ys = [f for f in self.dest.rglob("*.flac") if f.name not in ("01 p.flac", "02 q.flac")]
        self.assertTrue(ys, sorted(str(x) for x in self.dest.rglob("*")))
        return ys

    def test_users_photo_goes_into_its_album(self):
        self.run_both()
        for f in self.moved_y():
            self.assertIn((8, (400, 400)), pictures(f),
                          "the album from the folder with the user's artist.jpg got the download of "
                          "another album planned before it")

    def test_users_picture_in_tracks_is_not_replaced(self):
        # Y was tagged in place earlier: its tracks carry the picture made from the user's photo
        self.run_one(self.prof(), self.prop())
        self.assertIn((8, (400, 400)), pictures(self.f1))
        FakeCoverUp.calls = []
        self.run_both()
        for f in self.moved_y():
            self.assertIn((8, (400, 400)), pictures(f),
                          "the picture made from the user's own artist.jpg was replaced by a download")


if __name__ == "__main__":
    unittest.main()
