# The album's own cover is looked for in its picture folders too (Artwork/, Scans/, Covers/,
# and one level further: Artwork/Scans/), the largest front winning; a back, inlay, CD or booklet
# page is never taken for the front.
import os as _os, tempfile as _tempfile  # noqa: E402
_os.environ["XDG_CONFIG_HOME"] = _tempfile.mkdtemp(prefix="tm-config-")
_os.environ["XDG_STATE_HOME"] = _tempfile.mkdtemp(prefix="tm-state-")

import sys
import tempfile
import unittest
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import artwork  # noqa: E402


def img(path: Path, side: int):
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (side, side), "red").save(path)


class CoverInArtFolders(unittest.TestCase):
    def setUp(self):
        self.album = Path(tempfile.mkdtemp(prefix="tm-art-")) / "Album"
        self.album.mkdir()

    def rel(self, p):
        return p.relative_to(self.album).as_posix() if p else None

    def test_a_bigger_front_in_artwork_scans_beats_a_small_folder_jpg(self):
        img(self.album / "folder.jpg", 300)
        img(self.album / "Artwork" / "Scans" / "Front Cover.jpg", 1400)
        img(self.album / "Artwork" / "Scans" / "Back.jpg", 2000)
        img(self.album / "Artwork" / "Scans" / "CD.jpg", 2000)
        img(self.album / "Artwork" / "Scans" / "Booklet" / "01.jpg", 3000)
        self.assertEqual(self.rel(artwork.folder_cover(self.album)), "Artwork/Scans/Front Cover.jpg")

    def test_the_cover_beside_the_music_wins_a_tie_and_stays_without_a_front_elsewhere(self):
        img(self.album / "cover.jpg", 1000)
        img(self.album / "Scans" / "front.jpg", 1000)
        self.assertEqual(self.rel(artwork.folder_cover(self.album)), "cover.jpg")
        (self.album / "Scans" / "front.jpg").unlink()
        img(self.album / "Scans" / "inlay.jpg", 1600)
        self.assertEqual(self.rel(artwork.folder_cover(self.album)), "cover.jpg")

    def test_a_front_only_in_a_picture_folder_is_found(self):
        img(self.album / "Covers" / "01 - Front.png", 800)
        self.assertEqual(self.rel(artwork.folder_cover(self.album)), "Covers/01 - Front.png")

    def test_a_folder_that_holds_no_pictures_only_is_not_looked_into(self):
        img(self.album / "Extras" / "front.jpg", 900)                  # (Extras: not a picture folder)
        self.assertIsNone(artwork.folder_cover(self.album))


if __name__ == "__main__":
    unittest.main()
