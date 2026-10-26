# Thirteenth independent review: this test failed before its fix.
# Thirteenth independent review: tagging a loose A/B single turns the artist's folder.jpg
# into "an album's cover".
import os as _os, tempfile as _tempfile
_os.environ.setdefault("XDG_CONFIG_HOME", _tempfile.mkdtemp(prefix="tm-config-"))
_os.environ.setdefault("XDG_STATE_HOME", _tempfile.mkdtemp(prefix="tm-state-"))
import os, sys, unittest
from pathlib import Path
REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO)); sys.path.insert(0, str(REPO / "tests"))
import artwork, executor, plan as pl  # noqa
from test_review8_artist import R8  # noqa
from test_artist_photo import ARTIST_MBID, FakeCoverUp, pictures  # noqa
from test_cover_replace import MBID, jpeg, give  # noqa
from test_executor import audio, target  # noqa

for _n in dir(R8):
    if _n.startswith("test_"):
        setattr(R8, _n, None)


class TaggedSingleFlipsFolderJpg(R8):
    """The artist folder: the user's artist photo as folder.jpg (Kodi, Jellyfin), an album
    folder, and an A/B single - two loose tracks, untagged. While the single is untagged,
    folder.jpg is the artist's photo (album_here: two untagged tracks are no album), and it
    goes into the album's tracks. Then TagMender tags the single in place (album "Single",
    release type single): two loose tracks tagged as one album now ARE "an album" to
    album_here, so folder.jpg becomes that "album's cover". Next runs of the other album: the
    artist is looked up, a downloaded artist.jpg is written beside the user's folder.jpg, and
    then - artist.jpg now being "the artist folder's own photo" - the download REPLACES the
    user's photo in the album's tracks."""
    def test_users_folder_jpg_stays_the_artist_photo(self):
        artist = self.album.parent
        (artist / "folder.jpg").write_bytes(jpeg(400, 400, (1, 2, 3)))         # the user's artist photo
        s = [audio(artist / "A.flac", 660, title="A"), audio(artist / "B.flac", 670, title="B")]
        for f in s:
            give(f, jpeg(500, 500))
        self.run_one(self.prof(), self.prop())                                  # the album
        # (a folder.jpg is never put into the tracks - it may be an album's cover as well - but it
        # is the artist's image: nothing is looked up or written)
        self.assertNotIn(8, [t for t, _s in pictures(self.f1)])
        self.assertEqual(FakeCoverUp.calls, [])
        sp = self.proposal(source_dir=str(artist), files=[str(f) for f in s],
                           targets=[target(n + 1, t, album="Single", artist="Some Artist", releasetype=["single"],
                                           musicbrainz_albumid=[MBID], musicbrainz_albumartistid=[ARTIST_MBID])
                                    for n, t in enumerate("AB")])
        sp.group_files = [str(f) for f in s]
        _p, res = self.run_one(self.prof(), sp)                                 # the single, in place
        self.assertEqual(res["counts"], {"done": 1}, res)
        FakeCoverUp.calls = []
        for _ in range(2):
            self.run_one(self.prof(), self.prop())                              # the album again
        wrong = []
        if FakeCoverUp.calls:
            wrong.append("looked up although folder.jpg is the artist's photo")
        if (artist / "artist.jpg").exists():
            wrong.append("a download written as artist.jpg beside the user's folder.jpg")
        if 8 in [t for t, _s in pictures(self.f1)]:
            wrong.append(f"a picture went into the tracks: {pictures(self.f1)}")
        self.assertEqual(wrong, [])


if __name__ == "__main__":
    unittest.main()
