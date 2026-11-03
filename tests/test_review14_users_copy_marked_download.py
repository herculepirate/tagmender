# Fourteenth independent review: this test failed before its fix.
# Fourteenth independent review (A): this test fails at eb6cfad and passes at f4778b5.
# executor.new_file calls artwork.remember_written for EVERY artist sidecar - also when the
# picture written is the user's own artist.jpg, carried along by a moved album. From then on
# the destination's artist.jpg (the user's photo) counts as "a download TagMender wrote"
# (written_by_us): it is no longer authoritative, so the tracks of the artist's other albums
# there keep an old downloaded picture for ever, beside an artist folder showing the user's.
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
from test_cover_replace import MBID, Engine, jpeg, give  # noqa
from test_executor import audio, target  # noqa

for _n in dir(R8):
    if _n.startswith("test_"):
        setattr(R8, _n, None)


class UsersPhotoCopiedCountsAsDownload(R8):
    def test_other_album_follows_the_users_photo(self):
        art = self.prof()["artwork"]
        lib2 = self.tmp / "lib2"
        zdir = lib2 / "Some Artist" / "2005 - Zed"
        zs = [audio(zdir / "01 p.flac", 660, album="Zed", title="p", artist="Some Artist"),
              audio(zdir / "02 q.flac", 670, album="Zed", title="q", artist="Some Artist")]
        for f in zs:
            give(f, jpeg(600, 600))

        def pz(d, files):
            return self.proposal(source_dir=str(d), files=[str(f) for f in files],
                                 targets=[target(n + 1, t, album="Zed", artist="Some Artist", musicbrainz_albumid=[MBID],
                                                 musicbrainz_albumartistid=[ARTIST_MBID]) for n, t in enumerate("pq")])
        # 1. Zed is tagged in place in lib2: a downloaded photo goes into its tracks (800 px)
        prof2 = pf.from_dict({"name": "t", "sources": [{"path": str(lib2)}], "artwork": art,
                              "thresholds": {"organize_min_confidence": 95}, "tag_level": "enthusiast"})
        self.run_one(prof2, pz(zdir, zs))
        self.assertIn((8, (800, 800)), pictures(zs[0]))
        # 2. the user's own photo for the artist, in the main library
        (self.album.parent / "artist.jpg").write_bytes(jpeg(400, 400, (1, 2, 3)))
        # 3. Some Album moves to the destination, taking the user's photo along (dest artist.jpg)
        mprof = self.profile(artwork=art, tag_level="enthusiast", operation="move", destination=str(self.dest))
        _p, res = self.run_one(mprof, self.prop())
        self.assertEqual(res["counts"], {"done": 1}, res)
        dart = self.dest / "Some Artist" / "artist.jpg"
        self.assertTrue(dart.exists(), sorted(str(x) for x in self.dest.rglob("*")))
        # 4. Zed is filed into the same destination artist folder (by hand, or another job)
        newz = self.dest / "Some Artist" / "2005 - Zed"
        newz.mkdir(parents=True)
        moved = []
        for f in zs:
            moved.append(newz / f.name)
            os.replace(f, moved[-1])
        # 5. tagged in place there: the artist folder's photo is the user's - it goes into Zed
        dprof = pf.from_dict({"name": "t", "sources": [{"path": str(self.dest)}], "artwork": art,
                              "thresholds": {"organize_min_confidence": 95}, "tag_level": "enthusiast"})
        self.run_one(dprof, pz(newz, moved))
        self.assertIn((8, (400, 400)), pictures(moved[0]),
                      "the user's artist.jpg, carried to the destination by TagMender, is taken for a "
                      "download: the old downloaded picture stays in the artist's other album")

    def test_carried_users_photo_is_not_recorded_as_a_download(self):
        (self.album.parent / "artist.jpg").write_bytes(jpeg(400, 400, (1, 2, 3)))
        mprof = self.profile(artwork=self.prof()["artwork"], tag_level="enthusiast", operation="move",
                             destination=str(self.dest))
        self.run_one(mprof, self.prop())
        dart = self.dest / "Some Artist" / "artist.jpg"
        self.assertTrue(dart.exists())
        self.assertFalse(getattr(artwork, "written_by_us", lambda p: False)(dart),
                         "a copy of the user's own artist.jpg is recorded as TagMender's download")


if __name__ == "__main__":
    unittest.main()
