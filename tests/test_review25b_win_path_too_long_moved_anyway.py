# Twenty-fifth independent review (B). Fails with TM_REPO=prev too (prev did not shorten for
# Windows at all), so not a regression; the last round's fix left this case out.
"""With Windows compatibility on and "Windows long path support" off, a destination so deep
below its mount point that the folders cannot be fitted into Windows' 259 characters (less
than one character per folder left) gets the files moved there anyway, with every name at
its full length - paths of 400-700 characters that Windows cannot open - and no note or
conflict says so.

Cause: filenames.win_short_parts (filenames.py:153-154): when the average room per folder
is below one character it returns the parts unshortened ("Picard: Path too long; the limits
below say so") - but the limits below are max_path_chars (4096 by default) and the 255-byte
name limits, which such a path passes. Picard's _make_win_short_filename raises
WinPathTooLong there ("Path too long. You need to move renamed files to a different
directory.") and the file is not moved.

Picard 3.0.2: _make_win_short_filename("Artist/Album/Title.mp3", 250) -> WinPathTooLong.
TagMender: the album is planned "ready" and moved to <dest>/Artist .../Album .../01 ....flac
with the full-length names."""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _review25b_common as c  # noqa: E402
import filenames as fnm  # noqa: E402
import plan as pl  # noqa: E402

LIMITS = {"compat": "windows", "max_name_bytes": 255, "max_dir_bytes": 255, "max_path_chars": 4096}


class TooDeepForWindows(unittest.TestCase):
    def test_make_path_refuses(self):
        comps = ["Artist " * 20, "Album " * 30, "01 " + "Title " * 40]
        dst, notes = fnm.make_path("/mnt/x", comps, ".mp3", dict(LIMITS, win_reserved=250))
        self.assertIsNone(dst, f"Picard: WinPathTooLong; TagMender planned {dst} ({len(str(dst))} characters)")

    def test_plan_does_not_place_files_beyond_259(self):
        mount = tempfile.mkdtemp(prefix="tm25B-mnt-")
        dest = os.path.join(mount, "d" * 200, "e" * 40)          # 242 characters below the mount
        os.makedirs(dest)
        real = os.path.ismount
        files = c.album(".flac", 1)
        c.fresh_state()
        prof = c.profile(Path(files[0]).parent.parent, operation="move", destination=dest,
                         naming={"template": "%albumartist%/%album%/$num(%tracknumber%,2) %title%"})
        prop = c.proposal(files, {"albumartist": ["Artist " * 20], "album": ["Album " * 30],
                                  "title": ["Title " * 40]})
        with mock.patch("os.path.ismount", lambda p: os.path.normpath(p) == mount or real(p)):
            p = pl.build([prop], prof)
        a = p["albums"][0]
        for fe in a["files"]:
            below = len(fe["dst"]) - len(mount) + 3          # as Windows counts it: "X:\" + the rest
            self.assertLessEqual(below, 259, f"planned {fe['dst']!r}: {below} characters on Windows, "
                                             f"status {a['status']}, conflicts {a['conflicts']}")


if __name__ == "__main__":
    unittest.main()
