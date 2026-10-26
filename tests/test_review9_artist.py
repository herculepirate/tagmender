# Ninth independent review: each test failed before its fix.
import os as _os, tempfile as _tempfile  # noqa: E402
_os.environ.setdefault("XDG_CONFIG_HOME", _tempfile.mkdtemp(prefix="tm-config-"))
_os.environ.setdefault("XDG_STATE_HOME", _tempfile.mkdtemp(prefix="tm-state-"))
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
from test_artist_photo import ArtistPhoto, FakeCoverUp, ARTIST_MBID, pictures  # noqa: E402
from test_cover_replace import MBID, jpeg, give  # noqa: E402
from test_executor import audio, target  # noqa: E402


class R9(ArtistPhoto):
    pass


for _n in dir(ArtistPhoto):
    if _n.startswith("test_"):
        setattr(R9, _n, None)


# A. The artist folder also holds a loose album (tracks directly in it, ripped with its
#    folder.jpg cover - the usual EAC/foobar layout) beside the artist's album folders.
#    That folder.jpg is the LOOSE ALBUM'S COVER, yet every album below gets it embedded as
#    the "Artist/performer" picture, and no artist photo is ever looked up.
class LooseAlbumFolderJpg(R9):
    def test_loose_albums_cover_is_not_the_artist_photo(self):
        artist = self.album.parent
        for n in range(1, 5):
            audio(artist / f"0{n} loose.flac", 300 + n, album="Loose Album", title=f"l{n}",
                  artist="Some Artist")
        (artist / "folder.jpg").write_bytes(jpeg(700, 700, (255, 0, 0)))    # the loose album's cover
        self.run_one(self.prof(), self.prop())
        got = pictures(self.f1)
        self.assertNotIn((8, (700, 700)), got,
                         f"the loose album's folder.jpg was embedded as the artist photo: {got}; "
                         f"lookups {FakeCoverUp.calls}")


# B. A self-titled album whose disc folders are not named "CD1"/"Disc 1" ("Some Artist CD1",
#    "Part 1", "Vol. 1"): artist_dir_for takes the ALBUM folder ("Some Artist/Some Artist")
#    for the artist folder - its folder.jpg (the album cover) becomes the artist photo, and
#    with only cover.jpg there artist.jpg is written into the album folder.
class SelfTitledDiscFolders(R9):
    def test_album_folder_is_not_the_artist_folder(self):
        album = self.lib / "Some Artist" / "Some Artist"
        d1 = album / "Some Artist CD1"
        self.assertIsNone(artwork.artist_dir_for(d1, "Some Artist", [self.lib]),
                          "the self-titled album's folder taken for the artist folder")

    def test_album_cover_not_embedded_as_artist_photo(self):
        album = self.lib / "Some Artist" / "Some Artist"
        d1 = album / "Some Artist CD1"
        g = [audio(d1 / "01 a.flac", 440, album="Some Artist", title="a", artist="Some Artist"),
             audio(d1 / "02 b.flac", 550, album="Some Artist", title="b", artist="Some Artist")]
        for f in g:
            give(f, jpeg(600, 600))
        (album / "folder.jpg").write_bytes(jpeg(700, 700, (255, 0, 0)))     # the album's cover
        self.run_one(self.prof(), self.prop(folder=d1, files=g))
        self.assertNotIn((8, (700, 700)), pictures(g[0]), "the album cover embedded as the artist photo")


# C. A download that fails (or a rate-limited source) is stored as "no photo for this
#    artist" for 30 days: remember_artist_photo(mbid, None) for any note.
class FailedDownloadCachedAsNone(R9):
    def test_a_failed_download_is_asked_again_next_run(self):
        class Failing(FakeCoverUp):
            def artist_photo(self, folder, ids, artist="", files=None):
                FakeCoverUp.calls.append(artist)
                return {"note": "the artist photo from fanart could not be downloaded"}
        prof = self.prof()
        with mock.patch.object(artwork, "CoverUp", Failing):
            self.run_one(prof, self.prop())
        FakeCoverUp.calls = []
        self.run_one(prof, self.prop())
        self.assertEqual(FakeCoverUp.calls, ["Some Artist"],
                         "a transient download failure kept the artist from being asked for 30 days")


# D. No MusicBrainz artist ID (Discogs- or web-identified album), nothing found: the album
#    "lacks" the photo for ever, and only the in-run cache exists for it - one network lookup
#    per such artist on every run (watcher passes, re-runs over the library).
class NoMbidLookedUpEveryRun(R9):
    def test_no_second_lookup(self):
        class Nothing(FakeCoverUp):
            def artist_photo(self, folder, ids, artist="", files=None):
                FakeCoverUp.calls.append(artist)
                return {"note": "no artist photo found"}

        def prop():
            tg = [target(n + 1, t, artist="Some Artist", musicbrainz_albumid=[MBID]) for n, t in enumerate("ab")]
            return self.proposal(targets=tg)
        prof = self.prof()
        with mock.patch.object(artwork, "CoverUp", Nothing):
            self.run_one(prof, prop())
            FakeCoverUp.calls = []
            self.run_one(prof, prop())
        self.assertEqual(FakeCoverUp.calls, [], "the same artist looked up again on the next run")



class SelfTitledArtistJpg(R9):
    def test_no_artist_jpg_in_the_album_folder(self):
        album = self.lib / "Some Artist" / "Some Artist"
        d1 = album / "Some Artist CD1"
        g = [audio(d1 / "01 a.flac", 440, album="Some Artist", title="a", artist="Some Artist"),
             audio(d1 / "02 b.flac", 550, album="Some Artist", title="b", artist="Some Artist")]
        for f in g:
            give(f, jpeg(600, 600))
        (album / "cover.jpg").write_bytes(jpeg(700, 700, (255, 0, 0)))
        self.run_one(self.prof(), self.prop(folder=d1, files=g))
        self.assertFalse((album / "artist.jpg").exists(), "artist.jpg written into the self-titled album's folder")


# E. A move profile that organises the library in place (destination = the library): an album
#    already where the profile puts it, with artist.jpg and the picture in every track, is
#    still "lacking" (stays is False for every move profile), so every album without a
#    MusicBrainz artist ID is looked up on every run.
class MoveProfileEveryRun(R9):
    def test_no_lookup_when_everything_is_there(self):
        prof = self.profile(artwork=self.prof()["artwork"], tag_level="enthusiast", operation="move",
                            destination=str(self.lib))

        def prop(files):
            tg = [target(n + 1, t, artist="Some Artist", album="Some Album", musicbrainz_albumid=[MBID])
                  for n, t in enumerate("ab")]
            return self.proposal(source_dir=str(Path(files[0]).parent), files=[str(f) for f in files], targets=tg)
        _p, res = self.run_one(prof, prop([self.f1, self.f2]))
        self.assertEqual(res["counts"], {"done": 1}, res)
        now = sorted(self.lib.rglob("*.flac"))
        self.assertTrue(list(self.lib.rglob("artist.jpg")))
        self.assertTrue(all(8 in [t for t, _s in pictures(f)] for f in now))
        FakeCoverUp.calls = []
        self.run_one(prof, prop(now))
        self.assertEqual(FakeCoverUp.calls, [], "looked up again although artist.jpg and every picture are there")


if __name__ == "__main__":
    unittest.main()
