# Twelfth independent review: these tests failed before their fix.
# Twelfth independent review: the artist photo (marks, replacing, lookups).
import os as _os, tempfile as _tempfile
_os.environ.setdefault("XDG_CONFIG_HOME", _tempfile.mkdtemp(prefix="tm-config-"))
_os.environ.setdefault("XDG_STATE_HOME", _tempfile.mkdtemp(prefix="tm-state-"))
import os, sys, unittest
from pathlib import Path
from unittest import mock
REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO)); sys.path.insert(0, str(REPO / "tests"))
import artwork, executor, plan as pl  # noqa
from test_review8_artist import R8  # noqa
from test_artist_photo import ARTIST_MBID, FakeCoverUp, pictures  # noqa
from test_cover_replace import MBID, jpeg, give, size  # noqa
from test_executor import audio, target  # noqa

for _n in dir(R8):
    if _n.startswith("test_"):
        setattr(R8, _n, None)


class UsersOwnBesideOurs(R8):
    """A track with TagMender's marked artist picture and, added later by the user (Mp3tag,
    foobar2000), an artist picture of their own. The artist folder's photo is corrected: the
    plan says ours is replaced, the executor drops ours, finds the user's and returns without
    saving - and the next run plans the same again (rewritten on every run, never settled)."""
    def test_settles(self):
        self.run_one(self.prof(), self.prop())
        for f in (self.f1, self.f2):
            give(f, jpeg(300, 300, (1, 2, 3)), ptype=8)                  # the user's own, beside ours
        (self.album.parent / "artist.jpg").write_bytes(jpeg(500, 500, (0, 0, 255)))
        self.run_one(self.prof(), self.prop())
        before = os.stat(self.f1).st_ino
        p, res = self.run_one(self.prof(), self.prop())                   # third run: nothing to do
        a = p["albums"][0]
        self.assertEqual(os.stat(self.f1).st_ino, before, "the track was rewritten (replaced) again")
        self.assertEqual((a.get("artist_photo") or {}).get("embed_into") or [], [],
                         "the replacement is planned again on every run: " + pl.preview(p))


class EmbedOnlyCorrection(R8):
    """artwork.artist.file "" (embed only, no artist.jpg written): the documented recovery -
    put the right photo in the artist folder as artist.jpg, forget, run again - never reaches
    the tracks: the engine only checks the artist folder when it may write a file there."""
    def test_corrected_artist_jpg_reaches_the_tracks(self):
        prof = self.prof(artist={"file": ""})
        self.run_one(prof, self.prop())
        self.assertIn((8, (800, 800)), pictures(self.f1))
        (self.album.parent / "artist.jpg").write_bytes(jpeg(500, 500, (0, 0, 255)))
        artwork.forget_artist_photos("")
        self.run_one(prof, self.prop())
        self.assertIn((8, (500, 500)), pictures(self.f1), pictures(self.f1))


class FlatAlbumInPlaceUsersArtistJpg(R8):
    """An album ripped flat into the artist folder, tagged in place, the user's own artist.jpg
    beside its tracks: the tracks get a downloaded photo instead (artist_dir_for looks only
    at the album folder's parent), and the artist is looked up on the network."""
    def test_users_artist_jpg_is_embedded(self):
        artist = self.album.parent
        x = audio(artist / "01 x.flac", 660, album="Flat", albumartist="Some Artist", title="x")
        y = audio(artist / "02 y.flac", 770, album="Flat", albumartist="Some Artist", title="y")
        for f in (x, y):
            give(f, jpeg(600, 600))
        (artist / "artist.jpg").write_bytes(jpeg(400, 400, (1, 2, 3)))
        prop = self.prop(folder=artist, files=[x, y])
        p, res = self.run_one(self.prof(), prop)
        self.assertEqual(FakeCoverUp.calls, [], "looked up although the artist folder has its photo")
        self.assertNotIn((8, (800, 800)), pictures(x), "a downloaded photo, not the user's artist.jpg")


class LookupEveryRunNoMbid(R8):
    """A move profile, an album matched on Discogs only (no MusicBrainz artist ID) that stays
    where it is (below organize_min_confidence: tagged in place): the artist is looked up on
    the network on every run - nothing is kept (key = the name, lasting False)."""
    def test_one_lookup(self):
        prof = self.profile(artwork=self.prof()["artwork"], tag_level="enthusiast", operation="move",
                            destination=str(self.dest))
        def prop():
            tg = [target(n + 1, t, artist="Some Artist") for n, t in enumerate("ab")]
            return self.proposal(targets=tg, conf=90.0)
        self.run_one(prof, prop())
        self.assertTrue(self.f1.exists())                  # (stayed: tagged in place)
        self.assertIn(8, [t for t, _s in pictures(self.f1)])
        FakeCoverUp.calls = []
        self.run_one(prof, prop())
        self.assertEqual(FakeCoverUp.calls, [], "looked up again on every run")



class DiscSetInArtistFolderCover(R8):
    """A disc set ripped into the artist folder ("Some Artist/CD1", "Some Artist/CD2") with
    its cover as folder.jpg beside the disc folders: _flat_album_here counts loose tracks only,
    so folder.jpg is taken for the artist's photo and that album cover is embedded as the
    "Artist/performer" picture into every other album of the artist."""
    def test_album_cover_is_not_the_artist_photo(self):
        artist = self.album.parent
        audio(artist / "CD1" / "01 x.flac", 660, album="Twin", albumartist="Some Artist", discnumber="1")
        audio(artist / "CD1" / "02 y.flac", 670, album="Twin", albumartist="Some Artist", discnumber="1")
        audio(artist / "CD2" / "01 z.flac", 680, album="Twin", albumartist="Some Artist", discnumber="2")
        audio(artist / "CD2" / "02 w.flac", 690, album="Twin", albumartist="Some Artist", discnumber="2")
        (artist / "folder.jpg").write_bytes(jpeg(450, 450, (200, 0, 0)))      # Twin's cover
        self.run_one(self.prof(), self.prop())
        self.assertNotIn((8, (450, 450)), pictures(self.f1), "an album's cover embedded as the artist photo")



class MovedAlbumLosesUsersArtistPicture(R8):
    """Tagged in place first: the user's own artist.jpg went into the tracks (marked as
    TagMender's). Later the album is moved to a new library (move profile): the destination
    artist folder has no photo yet, so a downloaded one is planned - and, the identities
    differing, it REPLACES the picture made from the user's photo in every track."""
    def test_users_photo_stays_in_the_tracks(self):
        (self.album.parent / "artist.jpg").write_bytes(jpeg(400, 400, (1, 2, 3)))
        self.run_one(self.prof(), self.prop())
        self.assertIn((8, (400, 400)), pictures(self.f1))
        prof = self.profile(artwork=self.prof()["artwork"], tag_level="enthusiast", operation="move",
                            destination=str(self.dest))
        p, res = self.run_one(prof, self.prop())
        self.assertEqual(res["counts"], {"done": 1}, res)
        moved = sorted(self.dest.rglob("*.flac"))[:1]
        self.assertEqual(len(moved), 1, list(self.dest.rglob("*")))
        self.assertIn((8, (400, 400)), pictures(moved[0]),
                      "the picture from the user's own artist.jpg was replaced by a downloaded one")


if __name__ == "__main__":
    unittest.main()



class SymlinkedArtistJpg(R8):
    """The user's artist.jpg is a symbolic link (to a photo kept elsewhere - a common way to
    share one photo between libraries): artist_folder_photo skips links, so the plan adds an
    artist.jpg sidecar over it (no exists check at plan time), and the executor refuses to
    write through the link - the WHOLE album fails and is put back, on every run."""
    def setUp(self):
        super().setUp()
        photos = self.tmp / "Photos"
        photos.mkdir()
        (photos / "band.jpg").write_bytes(jpeg(400, 400, (1, 2, 3)))
        (self.album.parent / "artist.jpg").symlink_to(photos / "band.jpg")

    def test_album_is_still_tagged(self):
        p, res = self.run_one(self.prof(), self.prop())
        self.assertEqual(res["counts"], {"done": 1}, res)

    def test_link_is_the_photo(self):
        self.run_one(self.prof(), self.prop())
        self.assertEqual(FakeCoverUp.calls, [], "looked up although artist.jpg is there")
        self.assertIn((8, (400, 400)), pictures(self.f1), "the user's artist.jpg is not what the tracks got")


if __name__ == "__main__":
    unittest.main()
