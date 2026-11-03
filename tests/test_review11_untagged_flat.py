# Eleventh independent review: this test failed before its fix.
# Round 11: an UNTAGGED album ripped flat into the artist folder (8 tracks, folder.jpg its cover)
# beside the artist's tagged album folder. _flat_album_here() counts only tracks with an album
# tag, so folder.jpg is taken for the artist's photo: embedded as the "Artist/performer" picture
# into the other album's tracks (no lookup, no artist.jpg).
import os as _os, tempfile as _tempfile
_os.environ.setdefault("XDG_CONFIG_HOME", _tempfile.mkdtemp(prefix="tm-config-"))
_os.environ.setdefault("XDG_STATE_HOME", _tempfile.mkdtemp(prefix="tm-state-"))
import sys, unittest
from pathlib import Path
REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO)); sys.path.insert(0, str(REPO / "tests"))
import artwork, executor, plan as pl  # noqa
from test_review8_artist import R8  # noqa
from test_artist_photo import FakeCoverUp, pictures  # noqa
from test_cover_replace import jpeg  # noqa
from test_executor import audio  # noqa

for _n in dir(R8):
    if _n.startswith("test_"):
        setattr(R8, _n, None)


class UntaggedFlatAlbum(R8):
    def test_untagged_flat_albums_cover_is_not_the_artist_photo(self):
        artist = self.album.parent
        for n in range(8):
            audio(artist / f"{n + 1:02d} Track {n + 1}.flac", 300 + 40 * n)       # (no tags at all)
        (artist / "folder.jpg").write_bytes(jpeg(700, 700, (255, 0, 0)))
        self.assertIsNone(artwork.artist_folder_photo(artist),
                          "an untagged flat album's folder.jpg counts as the artist photo")

    def test_embedded(self):
        artist = self.album.parent
        for n in range(8):
            audio(artist / f"{n + 1:02d} Track {n + 1}.flac", 300 + 40 * n)
        (artist / "folder.jpg").write_bytes(jpeg(700, 700, (255, 0, 0)))
        self.run_one(self.prof(), self.prop())
        got = pictures(self.f1)
        self.assertNotIn((8, (700, 700)), got, f"the flat album's cover embedded as the artist photo: {got}")


from test_artist_photo import ARTIST_MBID  # noqa
from test_cover_replace import MBID  # noqa
from test_executor import target  # noqa


class UntaggedFlatAlbumItself(R8):
    """The untagged flat album is matched now and organised into its own folder (move, the
    library into itself): its own cover goes into its tracks as the ARTIST picture."""
    def test_own_cover_not_embedded_as_artist(self):
        artist = self.album.parent
        fs = [audio(artist / f"{n + 1:02d} Track {n + 1}.flac", 300 + 40 * n) for n in range(3)]
        from test_cover_replace import give
        for f in fs:
            give(f, jpeg(600, 600))                        # (its cover embedded too)
        (artist / "folder.jpg").write_bytes(jpeg(700, 700, (255, 0, 0)))
        prof = self.profile(artwork=self.prof()["artwork"], tag_level="enthusiast", operation="move",
                            destination=str(self.lib))
        prop = self.proposal(source_dir=str(artist), files=[str(f) for f in fs],
                             targets=[target(n + 1, f"t{n}", album="Flat", artist="Some Artist", totaltracks="3",
                                             musicbrainz_albumid=[MBID], musicbrainz_albumartistid=[ARTIST_MBID])
                                      for n in range(3)])
        prop.group_files = [str(f) for f in fs]
        p, res = self.run_one(prof, prop)
        self.assertEqual(res["counts"], {"done": 1}, res)
        ap = p["albums"][0]["artist_photo"]
        now = sorted((self.lib / "Some Artist").rglob("*Track*.flac")) or sorted(self.lib.rglob("01*.flac"))
        got = pictures([x for x in self.lib.rglob("*.flac") if "Some Album" not in str(x)][0])
        self.assertNotIn((8, (700, 700)), got,
                         f"its own cover embedded as the artist picture ({ap and ap['source']}): {got}")


if __name__ == "__main__":
    unittest.main()
