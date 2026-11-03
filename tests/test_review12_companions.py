# Twelfth independent review: these tests failed before their fix.
# Twelfth independent review: companions when an album leaves an artist folder.
import os as _os, tempfile as _tempfile
_os.environ.setdefault("XDG_CONFIG_HOME", _tempfile.mkdtemp(prefix="tm-config-"))
_os.environ.setdefault("XDG_STATE_HOME", _tempfile.mkdtemp(prefix="tm-state-"))
import os, sys, unittest
from pathlib import Path
REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO)); sys.path.insert(0, str(REPO / "tests"))
import executor, plan as pl  # noqa
from test_review9_companions import B  # noqa
from test_review8_artist import R8  # noqa
from test_cover_replace import jpeg  # noqa
from test_executor import audio, target  # noqa

for _n in dir(R8):
    if _n.startswith("test_"):
        setattr(R8, _n, None)
for _n in dir(B):
    if _n.startswith("test_"):
        setattr(B, _n, None)


class OneTrackReleaseWithNestedMusic(B):
    """A one-track release in its own folder ("2001 - Single/01 a.flac", cover.jpg, rip.log)
    holding a folder of other music ("Remixes"): the new loose-single rule (len(loose) == 1)
    takes the release's own folder for an artist folder and leaves its cover and log behind.
    Before e8f54ee they moved (the folder is not named after the artist)."""
    def test_cover_and_log_go(self):
        m = self.lib / "Band" / "2001 - Single"
        a = [audio(m / "01 a.flac", 500, album="Main", albumartist="Band")]
        rx = m / "Remixes"
        audio(rx / "01 x.flac", 600, album="Single Remixes", albumartist="Band")
        audio(rx / "02 y.flac", 610, album="Single Remixes", albumartist="Band")
        (m / "cover.jpg").write_bytes(b"single cover")
        (m / "rip.log").write_text("EAC")
        self.move_main(m, a)
        left = sorted(x.name for x in m.iterdir() if x.is_file())
        self.assertEqual(left, [], f"the release's own cover/log stayed behind: {left}")


class FlatAlbumTakesKodiExtrafanart(R8):
    """An album ripped flat into the artist folder: Kodi's extrafanart folder (the artist's
    extra backdrops, no music in it) is carried off into the album's new folder. Before
    e8f54ee everything of the artist folder stayed."""
    def test_extrafanart_stays(self):
        artist = self.album.parent
        x = audio(artist / "01 x.flac", 660, album="Flat", albumartist="Some Artist", title="x")
        y = audio(artist / "02 y.flac", 770, album="Flat", albumartist="Some Artist", title="y")
        (artist / "fanart.jpg").write_bytes(jpeg(400, 400))
        xf = artist / "extrafanart"
        xf.mkdir()
        (xf / "fanart1.jpg").write_bytes(jpeg(300, 300))
        (xf / "fanart2.jpg").write_bytes(jpeg(301, 300))
        prof = self.profile(operation="move", destination=str(self.dest))
        prop = self.proposal(source_dir=str(artist), files=[str(x), str(y)],
                             targets=[target(n + 1, t, album="Flat", artist="Some Artist")
                                      for n, t in enumerate("xy")])
        prop.group_files = [str(x), str(y)]
        p, res = self.run_one(prof, prop)
        self.assertEqual(res["counts"], {"done": 1}, res)
        self.assertTrue((artist / "fanart.jpg").exists())
        moved = sorted(str(q.relative_to(self.dest)) for q in self.dest.rglob("fanart*.jpg"))
        self.assertEqual(moved, [], "the artist folder's extrafanart went with the flat album")
        self.assertTrue((xf / "fanart1.jpg").exists(), "extrafanart left the artist folder")

    def test_numbered_backdrops_stay(self):
        """Jellyfin saves an artist's extra backdrops as backdrop1.jpg, backdrop2.jpg ...
        (Kodi: fanart1.jpg): ARTIST_FOLDER_ART matches the bare stem only."""
        artist = self.album.parent
        x = audio(artist / "01 x.flac", 660, album="Flat", albumartist="Some Artist", title="x")
        y = audio(artist / "02 y.flac", 770, album="Flat", albumartist="Some Artist", title="y")
        for n in ("backdrop.jpg", "backdrop1.jpg", "backdrop2.jpg", "fanart1.jpg"):
            (artist / n).write_bytes(jpeg(400, 400))
        prof = self.profile(operation="move", destination=str(self.dest))
        prop = self.proposal(source_dir=str(artist), files=[str(x), str(y)],
                             targets=[target(n + 1, t, album="Flat", artist="Some Artist")
                                      for n, t in enumerate("xy")])
        prop.group_files = [str(x), str(y)]
        p, res = self.run_one(prof, prop)
        self.assertEqual(res["counts"], {"done": 1}, res)
        gone = [n for n in ("backdrop.jpg", "backdrop1.jpg", "backdrop2.jpg", "fanart1.jpg")
                if not (artist / n).exists()]
        self.assertEqual(gone, [], "the artist's numbered backdrops went with the flat album")


class FlatAlbumWithCoverTakesJellyfinFolderJpg(R8):
    """A flat album with its own cover.jpg, in an artist folder whose folder.jpg is the
    artist's (Jellyfin/Kodi save the artist image so): folder.jpg goes with the album as if it
    were the album's cover; the artist folder loses its photo. Before e8f54ee it stayed."""
    def test_folder_jpg_stays(self):
        artist = self.album.parent
        x = audio(artist / "01 x.flac", 660, album="Flat", albumartist="Some Artist", title="x")
        y = audio(artist / "02 y.flac", 770, album="Flat", albumartist="Some Artist", title="y")
        (artist / "cover.jpg").write_bytes(jpeg(500, 500, (9, 9, 9)))      # the flat album's cover
        (artist / "folder.jpg").write_bytes(jpeg(400, 400, (1, 2, 3)))     # the artist's (Jellyfin)
        prof = self.profile(operation="move", destination=str(self.dest))
        prop = self.proposal(source_dir=str(artist), files=[str(x), str(y)],
                             targets=[target(n + 1, t, album="Flat", artist="Some Artist")
                                      for n, t in enumerate("xy")])
        prop.group_files = [str(x), str(y)]
        p, res = self.run_one(prof, prop)
        self.assertEqual(res["counts"], {"done": 1}, res)
        self.assertTrue((artist / "folder.jpg").exists(),
                        "the artist folder's folder.jpg left with a flat album that has its own cover.jpg")


class FlatAlbumArtistFolderNotNamedExactly(R8):
    """Album artist "Some Artist & The Band" ripped flat in the "Some Artist" folder (with the
    artist's other albums): named_after is false, so artist.jpg, fanart.jpg and logo.png -
    ARTIST_FOLDER_ART, never an album's - go with the album."""
    def test_artist_art_stays(self):
        artist = self.album.parent
        who = "Some Artist & The Band"
        x = audio(artist / "01 x.flac", 660, album="Flat", albumartist=who, title="x")
        y = audio(artist / "02 y.flac", 770, album="Flat", albumartist=who, title="y")
        for n in ("artist.jpg", "fanart.jpg", "logo.png"):
            (artist / n).write_bytes(jpeg(400, 400))
        prof = self.profile(operation="move", destination=str(self.dest))
        prop = self.proposal(source_dir=str(artist), files=[str(x), str(y)],
                             targets=[target(n + 1, t, album="Flat", artist=who) for n, t in enumerate("xy")])
        prop.group_files = [str(x), str(y)]
        p, res = self.run_one(prof, prop)
        self.assertEqual(res["counts"], {"done": 1}, res)
        gone = [n for n in ("artist.jpg", "fanart.jpg", "logo.png") if not (artist / n).exists()]
        self.assertEqual(gone, [], "the artist folder's own art went with the flat album")



class DiscSetInArtistFolderLeavesItsCover(R8):
    """A disc set ripped into the artist folder (Some Artist/CD1, CD2), its cover folder.jpg
    and rip log beside the disc folders, moved into a folder of its own: the log goes, the
    cover stays - artist_folder_photo takes folder.jpg for the artist's photo (no loose tracks)
    and artist_folder_keeps keeps it."""
    def test_cover_goes_with_its_log(self):
        artist = self.album.parent
        fs = [audio(artist / "CD1" / "01 x.flac", 660, album="Twin", albumartist="Some Artist", discnumber="1"),
              audio(artist / "CD1" / "02 y.flac", 670, album="Twin", albumartist="Some Artist", discnumber="1"),
              audio(artist / "CD2" / "01 z.flac", 680, album="Twin", albumartist="Some Artist", discnumber="2"),
              audio(artist / "CD2" / "02 w.flac", 690, album="Twin", albumartist="Some Artist", discnumber="2")]
        (artist / "folder.jpg").write_bytes(jpeg(450, 450, (200, 0, 0)))
        (artist / "Twin.log").write_text("EAC")
        prof = self.profile(operation="move", destination=str(self.dest))
        prop = self.proposal(source_dir=str(artist), files=[str(f) for f in fs],
                             targets=[target(n + 1, t, album="Twin", artist="Some Artist",
                                             discnumber=str(1 + n // 2), totaldiscs="2")
                                      for n, t in enumerate("xyzw")])
        prop.group_files = [str(f) for f in fs]
        p, res = self.run_one(prof, prop)
        self.assertEqual(res["counts"], {"done": 1}, res)
        self.assertFalse((artist / "Twin.log").exists(), sorted(os.listdir(artist)))
        # (since review 14: an artist folder's folder.jpg is never carried off - it may be the
        # artist's image - nor ever put into tracks as the artist picture)
        self.assertTrue((artist / "folder.jpg").exists())


if __name__ == "__main__":
    unittest.main()
