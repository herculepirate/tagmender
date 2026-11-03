# Fifteenth independent review (A).
# `tagmender artist-photos --forget` (no MATCH: "forget all", what the release notes and the
# command's help suggest after a wrong match) calls artwork.forget_artist_photos(""), which
# unlinks EVERY file in STATE_DIR/artist_photos - also written.json, the record of the
# artist.jpg files TagMender downloaded and wrote. From then on every such download counts as
# the user's own photo (written_by_us is False): it is "authoritative", and wins over the
# user's real photo. Here: a download sits in the destination artist folder; the user's own
# artist.jpg comes with a moved album - before the forget its tracks get the user's photo,
# after it they get the download.
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


class ForgetAllDropsTheDownloadRecord(R8):
    def test_download_still_known_after_forget_all(self):
        prof = self.prof()
        self.run_one(prof, self.prop())                                 # in place: a download
        photo = self.album.parent / "artist.jpg"
        self.assertTrue(photo.exists() and artwork.written_by_us(photo))
        artwork.forget_artist_photos("")                                # tagmender artist-photos --forget
        self.assertTrue(artwork.written_by_us(photo),
                        "after `artist-photos --forget`, TagMender's downloaded artist.jpg counts as the "
                        "user's own photo (written.json was deleted with the cache)")

    def test_users_photo_still_wins_after_forget_all(self):
        lib2 = self.tmp / "lib2"
        xdir = lib2 / "Inbox" / "Some Artist - Other"
        xs = [audio(xdir / "01 p.flac", 660, album="Other", title="p", artist="Some Artist"),
              audio(xdir / "02 q.flac", 670, album="Other", title="q", artist="Some Artist")]
        for f in xs:
            give(f, jpeg(600, 600))
        mprof = pf.from_dict({"name": "t", "sources": [{"path": str(self.lib)}, {"path": str(lib2)}],
                              "thresholds": {"organize_min_confidence": 95}, "artwork": self.prof()["artwork"],
                              "tag_level": "enthusiast", "operation": "move", "destination": str(self.dest)})
        px = self.proposal(source_dir=str(xdir), files=[str(f) for f in xs],
                           targets=[target(n + 1, t, album="Other", artist="Some Artist", musicbrainz_albumid=[MBID],
                                           musicbrainz_albumartistid=[ARTIST_MBID]) for n, t in enumerate("pq")])
        self.run_one(mprof, px)                                         # a download at the destination
        self.assertTrue(artwork.written_by_us(self.dest / "Some Artist" / "artist.jpg"))
        artwork.forget_artist_photos("")
        (self.album.parent / "artist.jpg").write_bytes(jpeg(400, 400, (1, 2, 3)))      # the user's own
        _p, res = self.run_one(mprof, self.prop())
        self.assertEqual(res["counts"], {"done": 1}, res)
        moved = [f for f in self.dest.rglob("*.flac") if f.name not in ("01 p.flac", "02 q.flac")]
        self.assertIn((8, (400, 400)), pictures(moved[0]),
                      f"after `artist-photos --forget` the download beats the user's own photo: {pictures(moved[0])}")


if __name__ == "__main__":
    unittest.main()
