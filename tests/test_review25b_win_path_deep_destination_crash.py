# Twenty-fifth independent review (B). Passes with TM_REPO=prev: a regression of the last
# fix round (Windows path shortening).
"""Planning crashes (ZeroDivisionError, the whole plan, every album in it) when the
destination lies deep below its mount point and the naming script makes no folders
("%title%", "$num(%tracknumber%,2) %title%").

Cause: filenames.win_short_parts (filenames.py:150-152). With "Windows compatibility" on
and "Windows long path support" off (both Picard's defaults off Windows) the room the
destination takes below its mount point is reserved (plan._name_limits). When that is more
than 247 characters, `remaining` is negative, so even an empty folder path (0 characters)
does not fit; the folder list is empty and `float(remaining) / len(names)` divides by
zero. Picard's _make_win_short_filename has the same arithmetic but its folder list is
[""] (os.path.split of a bare file name), so it raises WinPathTooLong ("Path too long. You
need to move renamed files to a different directory.") and refuses that one file.

Picard 3.0.2 (picard.util.filenaming._make_win_short_filename("Title.mp3", 256)):
WinPathTooLong. TagMender: ZeroDivisionError out of plan.build - no plan at all."""
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


class DeepDestinationNoFolders(unittest.TestCase):
    def test_make_path_does_not_raise(self):
        # 256 characters reserved: a destination 252 characters below its mount point
        try:
            dst, notes = fnm.make_path("/mnt/x", ["Title"], ".mp3", dict(LIMITS, win_reserved=256))
        except ZeroDivisionError as exc:            # pragma: no cover - the bug
            self.fail(f"make_path raised {exc!r} (Picard refuses the file: WinPathTooLong)")
        # (that the file should then be refused, as Picard refuses it, is
        # test_review25b_win_path_too_long_moved_anyway)

    def test_plan_does_not_crash(self):
        mount = tempfile.mkdtemp(prefix="tm25B-mnt-")
        dest = os.path.join(mount, "d" * 200, "e" * 50)          # 252 characters below the mount
        os.makedirs(dest)
        real = os.path.ismount
        files = c.album(".flac", 2)
        c.fresh_state()
        prof = c.profile(Path(files[0]).parent.parent, operation="move", destination=dest,
                         naming={"template": "%title%"})
        with mock.patch("os.path.ismount", lambda p: os.path.normpath(p) == mount or real(p)):
            try:
                p = pl.build([c.proposal(files)], prof)
            except ZeroDivisionError as exc:
                self.fail(f"plan.build raised {exc!r} - no plan at all for a deep destination")
        self.assertEqual(len(p["albums"]), 1)


if __name__ == "__main__":
    unittest.main()
