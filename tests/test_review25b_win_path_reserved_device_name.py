# Twenty-fifth independent review (B). Passes with TM_REPO=prev for the cut length only
# because prev did not shorten at all; the shortening is new, so this is part of the last
# fix round (prev fails the test differently: the name is not cut).
"""A folder named like a Windows device (an artist "Con", "Aux", "Nul", "Prn" ...) makes
the Windows path shortening cut the file name (or the folders) one character shorter than
Picard does, so the file gets a different name.

Cause: filenames.make_path (filenames.py:184-190) makes every component safe first -
safe_component turns "Con" into "Con_" - and only then runs win_short_parts, which counts
"Con_" (4 characters) for the folder path. Picard shortens first
(make_short_filename -> _make_win_short_filename on "Con/...") and adds the "_" afterwards
(make_save_path), so its folder path is one character shorter and the file name keeps one
character more. The same holds for "AUX. Remixes" style names and in the proportional
folder cut (the "_" changes the ratio).

Picard 3.0.2, end to end (File.make_filename, a destination on the root filesystem, the
default naming script), artist "Con", album "Live at the Albert Hall, London " x 3, title
"Symphonic Suite in Three Movements, " x 8:
  ".../01 Symphonic Suite ... Three Movements,.mp3"
TagMender: ".../01 Symphonic Suite ... Three Movements.mp3" (the "," cut off).
Below: _make_win_short_filename("Con/<album>/<title>.mp3", 9) keeps a 145-character file
name; TagMender's is 144."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _review25b_common as c  # noqa: E402,F401
import filenames as fnm  # noqa: E402

LIMITS = {"compat": "windows", "max_name_bytes": 255, "max_dir_bytes": 255, "max_path_chars": 4096}
ALBUM = "Albumwords" * 10
TITLE = "Titlewords" * 30
# Picard 3.0.2: make_save_path(_make_win_short_filename("Con/" + ALBUM + "/" + TITLE + ".mp3", 9), True)
PICARD = "Con_/" + ALBUM + "/" + TITLE[:141] + ".mp3"


class DeviceNameFolder(unittest.TestCase):
    def test_file_name_as_picard_cuts_it(self):
        for art in ("Con", "Aux", "NUL", "Prn"):
            dst, _n = fnm.make_path("/x/dest", [art, ALBUM, TITLE], ".mp3", dict(LIMITS, win_reserved=9))
            self.assertEqual(Path(dst).name, Path(PICARD).name, art)
            self.assertEqual(len(Path(dst).name), 145, art)


if __name__ == "__main__":
    unittest.main()
