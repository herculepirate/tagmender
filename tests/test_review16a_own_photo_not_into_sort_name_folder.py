# Sixteenth independent review (A): this test failed before its fix.
# Sixteenth independent review (A). Not a regression (prev knows no sort names at all): the new
# sort-name support is incomplete.
# A move profile names artist folders by sort name (naming template %albumartistsort%: the
# album lands in "Artist, Some/Real Album/"). The album comes from "Some Artist/", whose
# artist.jpg is the user's own photo. Engine._add_artist_photo passes the album artist's sort
# name to the planner (artist_sort) only for a DOWNLOADED photo (engine.py, the last line of
# _add_artist_photo); for the artist folder's own photo it builds prop.artist_photo without
# it. So Planner._artist_photo calls artist_dir_for(album, ["Some Artist"]) - the sort-named
# destination folder is not recognised as the artist's folder, and the user's photo is not
# written there as artist.jpg (with %albumartist% it is). The new artist folder is left
# without the photo - the next album of the artist filed there gets a download instead - and
# a download already there (an earlier run's) is not replaced by the user's photo either.
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _review16a_common  # noqa: E402,F401

import profiles as pf  # noqa: E402
from test_review8_artist import R8  # noqa: E402
from test_artist_photo import FakeCoverUp  # noqa: E402
from test_cover_replace import jpeg  # noqa: E402

for _n in dir(R8):
    if _n.startswith("test_"):
        setattr(R8, _n, None)


class OwnPhotoNotIntoSortNameFolder(R8):
    def move(self, field):
        (self.album.parent / "artist.jpg").write_bytes(jpeg(400, 400, (1, 2, 3)))     # the user's own
        prof = pf.from_dict({"name": "t", "sources": [{"path": str(self.lib)}],
                             "thresholds": {"organize_min_confidence": 95}, "artwork": self.prof()["artwork"],
                             "tag_level": "enthusiast", "operation": "move", "destination": str(self.dest),
                             "naming": {"preset": "tagmender",
                                        "template": "%" + field + "%/%album%/$num(%tracknumber%,2) %title%"}})
        _p, res = self.run_one(prof, self.prop(albumartistsort=["Artist, Some"]))
        self.assertEqual(res["counts"], {"done": 1}, res)
        self.assertEqual(FakeCoverUp.calls, [])

    def test_control_folder_named_by_name(self):          # (passes)
        self.move("albumartist")
        self.assertTrue((self.dest / "Some Artist" / "artist.jpg").exists())

    def test_folder_named_by_sort_name(self):
        self.move("albumartistsort")
        self.assertTrue((self.dest / "Artist, Some" / "Real Album" / "01 a.flac").exists())
        self.assertTrue((self.dest / "Artist, Some" / "artist.jpg").exists(),
                        f"the user's artist photo is not written into the sort-named artist folder: "
                        f"{sorted(str(x.relative_to(self.dest)) for x in self.dest.rglob('*'))}")


if __name__ == "__main__":
    unittest.main()
