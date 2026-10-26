# Twenty-fourth independent review (B). Fails with TM_REPO=prev too (not a regression).
"""A "." folder in a naming script ("%album%/./%title%", or a script that falls back to "."
for "no folder": "%albumartist%/$if($gt(%totaldiscs%,1),Disc %discnumber%,.)/%title%")
becomes a folder named "_" in TagMender; Picard drops it.

Cause: naming.render keeps "." as a path component and filenames.safe_component turns a
name that is only dots into "_" (filenames.py:59, `if not s or set(s) <= {"."}: s = "_"`).
Picard 3.0.2's File.make_filename passes the script's path through
util/filenaming.make_short_filename, which starts with os.path.normpath(relpath): "a/./b"
is "a/b".

Picard, end to end (single "Hello" by Adele, FLAC): "Hello/Hello.flac" and
"Adele/Hello.flac"; TagMender "Hello/_/Hello.flac" and "Adele/_/Hello.flac"."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _review24b_common as c  # noqa: E402

ALBUM = c.TARGET["album"][0]
ARTIST = c.TARGET["albumartist"][0]


class DotFolder(unittest.TestCase):
    def test_dot_folder(self):
        files = c.album(".flac", 1)
        _p, res, dest = c.move_run(files, "%album%/./%title%")
        self.assertEqual(res["counts"], {"done": 1}, c.detail(res))
        self.assertEqual(c.names(dest), [f"{ALBUM}/Track 1.flac"])

    def test_dot_as_no_disc_folder(self):
        files = c.album(".flac", 1)
        _p, res, dest = c.move_run(files, "%albumartist%/$if($gt(%totaldiscs%,1),Disc %discnumber%,.)/%title%")
        self.assertEqual(res["counts"], {"done": 1}, c.detail(res))
        self.assertEqual(c.names(dest), [f"{ARTIST}/Track 1.flac"])


if __name__ == "__main__":
    unittest.main()
