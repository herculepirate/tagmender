# Eleventh review, around its fixes: what the new rules must keep doing.
import os as _os, tempfile as _tempfile
_os.environ.setdefault("XDG_CONFIG_HOME", _tempfile.mkdtemp(prefix="tm-config-"))
_os.environ.setdefault("XDG_STATE_HOME", _tempfile.mkdtemp(prefix="tm-state-"))
import sys, unittest
from pathlib import Path
REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO)); sys.path.insert(0, str(REPO / "tests"))
import artwork, executor, plan as pl  # noqa
from test_review8_artist import R8  # noqa
from test_artist_photo import ARTIST_MBID, pictures  # noqa
from test_cover_replace import MBID, jpeg, give, size  # noqa
from test_executor import audio, target  # noqa

for _n in dir(R8):
    if _n.startswith("test_"):
        setattr(R8, _n, None)


def descs(path):
    from mutagen.flac import FLAC
    return [(p.type, p.desc) for p in FLAC(path).pictures]


class FlatAlbumLeavesTheArtistsArt(R8):
    """A tagged album ripped flat into the artist folder moves into a folder of its own: its
    cover, cue and log go with it; the artist's photo and Kodi art stay."""
    def test_flat_album(self):
        artist = self.album.parent
        x = audio(artist / "01 x.flac", 660, album="Flat", albumartist="Some Artist", title="x")
        y = audio(artist / "02 y.flac", 770, album="Flat", albumartist="Some Artist", title="y")
        for n in ("folder.jpg", "artist.jpg", "fanart.jpg", "logo.png"):
            (artist / n).write_bytes(jpeg(400, 400))
        (artist / "Flat.cue").write_text('FILE "01 x.flac" WAVE\n')
        (artist / "Flat.log").write_text("EAC log")
        prof = self.profile(operation="move", destination=str(self.dest))
        prop = self.proposal(source_dir=str(artist), files=[str(x), str(y)],
                             targets=[target(n + 1, t, album="Flat", artist="Some Artist")
                                      for n, t in enumerate("xy")])
        prop.group_files = [str(x), str(y)]
        p, res = self.run_one(prof, prop)
        self.assertEqual(res["counts"], {"done": 1}, res)
        for n in ("artist.jpg", "fanart.jpg", "logo.png"):
            self.assertTrue((artist / n).exists(), f"{n} left the artist folder")
        for n in ("folder.jpg", "Flat.cue", "Flat.log"):
            self.assertFalse((artist / n).exists(), f"{n} stayed behind: {sorted(os.listdir(artist))}")


import os  # noqa: E402


class TaggedSingle(R8):
    """A single (release type single) tagged as one album, loose in the artist folder beside a
    Kodi folder.jpg: folder.jpg is the artist's, not the single's."""
    def test_folder_jpg_stays(self):
        artist = self.album.parent
        (artist / "folder.jpg").write_bytes(jpeg(400, 400))
        a = audio(artist / "01 a.flac", 660, album="Single", albumartist="Some Artist", title="a")
        b = audio(artist / "02 b.flac", 770, album="Single", albumartist="Some Artist", title="b")
        prof = self.profile(operation="move", destination=str(self.dest))
        prop = self.proposal(source_dir=str(artist), files=[str(a), str(b)],
                             targets=[target(n + 1, t, album="Single", artist="Some Artist",
                                             releasetype=["single"]) for n, t in enumerate("ab")])
        prop.group_files = [str(a), str(b)]
        p, res = self.run_one(prof, prop)
        self.assertEqual(res["counts"], {"done": 1}, res)
        self.assertTrue((artist / "folder.jpg").exists(), "the artist folder's folder.jpg left with a single")


class MarkedAndReplaced(R8):
    def test_own_artist_picture_is_never_replaced(self):
        mine = jpeg(300, 300, (1, 2, 3))
        for f in (self.f1, self.f2):
            give(f, mine, ptype=8)                       # the user's own artist picture
        (self.album.parent / "artist.jpg").write_bytes(jpeg(500, 500, (0, 0, 255)))
        p, res = self.run_one(self.prof(), self.prop())
        for f in (self.f1, self.f2):
            self.assertIn((8, (300, 300)), pictures(f))
            self.assertNotIn((8, (500, 500)), pictures(f))

    def test_embedded_picture_is_marked_and_stable(self):
        self.run_one(self.prof(), self.prop())
        d = dict(descs(self.f1))
        self.assertTrue(d[8].startswith(artwork.ARTIST_MARK), d)
        before = self.f1.read_bytes()
        p, res = self.run_one(self.prof(), self.prop())        # nothing changed: nothing rewritten
        self.assertEqual(self.f1.read_bytes(), before)
        self.assertNotIn("in place of the one TagMender embedded", pl.preview(p))

    def test_corrected_photo_replaces_ours_and_undo_brings_it_back(self):
        self.run_one(self.prof(), self.prop())
        first = pictures(self.f1)
        (self.album.parent / "artist.jpg").write_bytes(jpeg(500, 500, (0, 0, 255)))
        p, res = self.run_one(self.prof(), self.prop())
        self.assertIn("in place of the one TagMender embedded", pl.preview(p))
        got = pictures(self.f1)
        self.assertEqual([t for t, _s in got].count(8), 1, got)
        self.assertIn((8, (500, 500)), got)
        executor.undo(p["job"])
        self.assertEqual(pictures(self.f1), first)


class MarkedId3AndOgg(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(_tempfile.mkdtemp())

    def test_formats(self):
        for ext in (".mp3", ".opus"):
            f = audio(self.tmp / f"t{ext}", 440, title="t")
            executor.embed_picture(f, jpeg(100, 100), desc=artwork.artist_mark("a" * 16))
            self.assertEqual(artwork.our_artist_marks(f), ["a" * 16])
            executor.embed_picture(f, jpeg(120, 120), desc=artwork.artist_mark("b" * 16), replace=True)
            self.assertEqual(artwork.our_artist_marks(f), ["b" * 16], ext)
            self.assertEqual([t for t, _d in artwork._pictures(f)].count(8), 1, ext)


class CacheRules(unittest.TestCase):
    def setUp(self):
        import exclusions as ex
        self.saved = ex.STATE_DIR
        ex.STATE_DIR = Path(_tempfile.mkdtemp())
        self.addCleanup(setattr, ex, "STATE_DIR", self.saved)

    def test_dir_keys_never_kept_and_forget_all(self):
        src = Path(_tempfile.mkdtemp()) / "p.jpg"
        src.write_bytes(jpeg(400, 400))
        got = artwork.remember_artist_photo("dir:/x/Artist", {"image": str(src)}, "Artist")
        self.assertEqual(got["image"], str(src))
        self.assertEqual(artwork.cached_artist_photo("dir:/x/Artist"), (False, None))
        artwork.remember_artist_photo(ARTIST_MBID, {"image": str(src), "source": "s"}, "Artist")
        self.assertTrue(artwork.cached_artist_photo(ARTIST_MBID)[1])
        self.assertEqual(artwork.forget_artist_photos(""), 1)     # (the name-found one was never kept)
        self.assertEqual(list(artwork._photo_cache_dir().iterdir()), [])


if __name__ == "__main__":
    unittest.main()
