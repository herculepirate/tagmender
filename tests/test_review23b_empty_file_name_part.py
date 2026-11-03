# Twenty-third independent review (B). Fails with TM_REPO=prev too (not a regression).
"""A naming script whose file-name part comes out empty - a folder-only script ending in "/"
("%albumartist%/%album%/"), or a last part that is empty for some tracks
("%albumartist%/%album%/%_mytitle%") - names every track after the ALBUM FOLDER, one level
up, with collision suffixes:

    dest/Pink Floyd/The Dark Side of the Moon.flac
    dest/Pink Floyd/The Dark Side of the Moon ~e813.flac ...

naming.render drops empty components ([p.strip() for p in ... if p.strip()]), so the album
folder becomes the file name. Picard 3.0.2 (File._script_to_filename: "if not basename:
old_name = ..."), end to end with the same release and scripts, keeps the file's own name in
the folder the script gives:

    dest/Pink Floyd/The Dark Side of the Moon/track01.flac, track02.flac ..."""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _review23b_common as c  # noqa: E402


class EmptyFileNamePart(unittest.TestCase):
    def check(self, template):
        files = c.album(".flac")
        dest = Path(tempfile.mkdtemp(prefix="tm23B-dest-"))
        p, res = c.run(files, operation="move", destination=str(dest), naming={"template": template})
        self.assertEqual(res["counts"], {"done": 1}, c.detail(res))
        got = sorted(str(Path(fe["dst"]).relative_to(dest)) for fe in p["albums"][0]["files"])
        self.assertEqual(got, ["Pink Floyd/The Dark Side of the Moon/track01.flac",
                               "Pink Floyd/The Dark Side of the Moon/track02.flac"])

    def test_folder_only_script(self):
        self.check("%albumartist%/%album%/")

    def test_empty_last_part(self):
        self.check("%albumartist%/%album%/%_nothing%")


if __name__ == "__main__":
    unittest.main()
