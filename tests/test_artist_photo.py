# The artist's photo at Enthusiast and Audiophile: artist.jpg in the artist folder and an
# "Artist/performer" picture in each track - looked up once per artist, only while something
# lacks it, never over a photo that is there, never for a compilation; undo takes it away.
import os as _os, tempfile as _tempfile  # noqa: E402
_os.environ["XDG_CONFIG_HOME"] = _tempfile.mkdtemp(prefix="tm-config-")
_os.environ["XDG_STATE_HOME"] = _tempfile.mkdtemp(prefix="tm-state-")
import socket
import sys
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "tests"))
import artwork  # noqa: E402
import executor  # noqa: E402
import plan as pl  # noqa: E402
import tagio  # noqa: E402
from test_cover_replace import Engine, MBID, _no_network, give, jpeg, size  # noqa: E402
from test_executor import Base, audio, target  # noqa: E402

ARTIST_MBID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"


class FakeCoverUp:
    """CoverUp's artist sources, offline: one photo per call, counted."""
    calls = []

    def __init__(self, staging, *a, **k):
        self.staging = Path(staging)

    def artist_photo(self, folder, ids, artist="", files=None):
        FakeCoverUp.calls.append(artist)
        path, w, h = artwork._stage(jpeg(1000, 1000, (10, 120, 200)), self.staging)
        return {"image": str(path), "width": w, "height": h, "source": "coverup:fanart"}


def pictures(path):
    from mutagen.flac import FLAC
    return sorted((p.type, size(p.data)) for p in FLAC(path).pictures)


class ArtistPhoto(Base):
    def setUp(self):
        super().setUp()
        FakeCoverUp.calls = []
        for p in (mock.patch.object(socket, "create_connection", _no_network),
                  mock.patch.object(artwork, "CoverUp", FakeCoverUp)):
            p.start()
            self.addCleanup(p.stop)
        for f in (self.f1, self.f2):                       # (an artist photo goes beside a front cover)
            give(f, jpeg(600, 600))

    def prof(self, level="enthusiast", **art):
        # (artist.jpg in the artist folder: off by default since 0.3.0 - these tests turn it on)
        a = {"enabled": True, "embed_front": True, "files": True, "sources": ["caa"],
             "artist": {"enabled": True, "file": "artist.jpg", "embed": True}}
        a.update(art)
        return self.profile(artwork=a, tag_level=level)

    def prop(self, folder=None, files=None, artist="Some Artist", **extra):
        tg = [target(n + 1, t, artist=artist, musicbrainz_albumid=[MBID],
                     musicbrainz_albumartistid=[ARTIST_MBID], **extra) for n, t in enumerate(("a", "b"))]
        kw = {"targets": tg}
        if folder is not None:
            kw.update(source_dir=str(folder), files=[str(f) for f in files])
        return self.proposal(**kw)

    def run_one(self, prof, prop, engine=None):
        e = engine or Engine(prof)
        with mock.patch.object(artwork, "chain", lambda *a, **k: []):
            e.add_artwork(prop)
        p = pl.build([prop], prof)
        return p, executor.Executor(p).run()

    def test_in_place_artist_jpg_and_tracks_then_undo(self):
        prof = self.prof()
        p, res = self.run_one(prof, self.prop())
        self.assertEqual(res["counts"], {"done": 1}, res)
        photo = self.album.parent / "artist.jpg"
        self.assertTrue(photo.exists(), sorted(p.name for p in self.album.parent.iterdir()))
        self.assertEqual(size(photo.read_bytes()), (1000, 1000))
        for f in (self.f1, self.f2):
            self.assertIn((8, (800, 800)), pictures(f))            # small: it is in every track
        self.assertIn("artist photo of Some Artist", pl.preview(p))
        executor.undo(p["job"])
        self.assertFalse(photo.exists())
        for f in (self.f1, self.f2):
            self.assertEqual([t for t, _s in pictures(f)], [3])

    def test_by_default_in_the_tracks_but_no_artist_jpg(self):
        """0.3.0's default: the artist photo goes into the tracks; artist.jpg is not written into
        the artist folder unless the profile asks (artwork.artist.file)."""
        import profiles as pf
        self.assertEqual(pf.DEFAULTS["artwork"]["artist"], {"enabled": True, "file": "", "embed": True})
        prof = self.prof(artist={"enabled": True})
        p, res = self.run_one(prof, self.prop())
        self.assertEqual(res["counts"], {"done": 1}, res)
        self.assertFalse((self.album.parent / "artist.jpg").exists())
        for f in (self.f1, self.f2):
            self.assertIn((8, (800, 800)), pictures(f))
        executor.undo(p["job"])
        for f in (self.f1, self.f2):
            self.assertEqual([t for t, _s in pictures(f)], [3])

    def test_basic_level_gets_none(self):
        prof = self.prof(level="basic")
        p, _res = self.run_one(prof, self.prop())
        self.assertEqual(FakeCoverUp.calls, [])
        self.assertFalse((self.album.parent / "artist.jpg").exists())

    def test_switched_off(self):
        prof = self.prof(artist={"enabled": False})
        self.run_one(prof, self.prop())
        self.assertEqual(FakeCoverUp.calls, [])

    def test_compilation_gets_none(self):
        prof = self.prof()
        self.run_one(prof, self.prop(artist="Various Artists"))
        self.assertEqual(FakeCoverUp.calls, [])

    def test_one_lookup_per_artist_and_none_on_the_next_run(self):
        prof = self.prof()
        other = self.album.parent / "2003 - Other Album"
        g = [audio(other / "01 x.flac", 660, album="Other Album", title="x", artist="Some Artist"),
             audio(other / "02 y.flac", 770, album="Other Album", title="y", artist="Some Artist")]
        e = Engine(prof)
        with mock.patch.object(artwork, "chain", lambda *a, **k: []):
            props = [self.prop(), self.prop(folder=other, files=g)]
            for pr in props:
                e.add_artwork(pr)
        self.assertEqual(FakeCoverUp.calls, ["Some Artist"])     # once for both albums
        p = pl.build(props, prof)
        self.assertEqual(sum(1 for a in p["albums"] for sc in a["sidecars"] if sc.get("kind") == "artist"), 1)
        res = executor.Executor(p).run()
        self.assertEqual(res["counts"], {"done": 2}, res)
        FakeCoverUp.calls = []
        _p, _res = self.run_one(prof, self.prop())
        self.assertEqual(FakeCoverUp.calls, [], "looked up again although it is there")

    def test_an_existing_artist_photo_is_kept(self):
        mine = jpeg(400, 400, (1, 2, 3))
        (self.album.parent / "artist.jpg").write_bytes(mine)
        self.run_one(self.prof(), self.prop())
        self.assertEqual((self.album.parent / "artist.jpg").read_bytes(), mine)
        self.assertIn(8, [t for t, _s in pictures(self.f1)])

    def test_a_folder_not_named_after_the_artist_gets_no_file(self):
        p, _res = self.run_one(self.prof(), self.prop(artist="Someone Else"))
        self.assertFalse((self.album.parent / "artist.jpg").exists())
        self.assertIn(8, [t for t, _s in pictures(self.f1)])

    def test_a_library_root_never_gets_one(self):
        flat = self.lib / "Some Artist - Flat Album"
        g = [audio(flat / "01 a.flac", 440, album="Flat", title="a", artist="Some Artist")]
        self.run_one(self.prof(), self.prop(folder=flat, files=g))
        self.assertFalse((self.lib / "artist.jpg").exists())

    def test_moved_album_artist_photo_in_the_new_artist_folder(self):
        prof = self.prof()
        prof = self.profile(artwork=prof["artwork"], tag_level="enthusiast", operation="move",
                            destination=str(self.dest))
        p, res = self.run_one(prof, self.prop())
        self.assertEqual(res["counts"], {"done": 1}, res)
        photos = list(self.dest.rglob("artist.jpg"))
        self.assertEqual([x.parent.name for x in photos], ["Some Artist"], list(self.dest.rglob("*")))

    def test_never_a_tracks_only_picture(self):
        from mutagen.flac import FLAC
        for f in (self.f1, self.f2):
            t = FLAC(f)
            t.clear_pictures()
            t.save()
        self.run_one(self.prof(), self.prop())               # (no front found: none in the tracks)
        self.assertEqual(pictures(self.f1), [])

    def test_mp3_artist_picture_beside_the_cover(self):
        f = audio(self.album / "03 c.mp3", 660, album="Some Album", title="c", artist="Some Artist")
        from mutagen.id3 import APIC, ID3
        t = ID3(f)
        t.add(APIC(encoding=3, mime="image/jpeg", type=3, desc="", data=jpeg(600, 600)))
        t.save(f)
        prop = self.prop()
        prop.files = [str(f)]
        prop.targets = prop.targets[:1]
        self.run_one(self.prof(), prop)
        got = sorted((a.type, size(a.data)) for a in ID3(f).getall("APIC"))
        self.assertEqual(got, [(3, (600, 600)), (8, (800, 800))])

    def test_photo_gone_at_apply_time_is_not_a_failure(self):
        prof = self.prof()
        prop = self.prop()
        with mock.patch.object(artwork, "chain", lambda *a, **k: []):
            Engine(prof).add_artwork(prop)
        p = pl.build([prop], prof)
        Path(p["albums"][0]["artist_photo"]["image"]).unlink()
        Path(p["albums"][0]["artist_photo"]["embed"]).unlink()
        res = executor.Executor(p).run()
        self.assertEqual(res["counts"], {"done": 1}, res)
        self.assertFalse((self.album.parent / "artist.jpg").exists())


if __name__ == "__main__":
    unittest.main()
