# Twenty-fourth independent review (B). Fails with TM_REPO=prev too (not a regression).
"""Picard's default Windows compatibility shortens names that TagMender leaves long.

Picard 3.0.2 on Linux with its defaults (windows_compatibility on, windows_long_paths off -
const/defaults.py DEFAULT_LONG_PATHS is False off Windows) names files through
util/filenaming.make_short_filename(..., win_shorten_path=True) -> _make_win_short_filename:
  * every folder and file name is cut to WIN_MAX_NODE_LEN - 29 = 226 UTF-16 units (the file
    name with its extension), inside a word if need be;
  * the whole path below the destination's mount point must fit 259 characters
    (reserved = len(destination) - len(mount point) + 4), the file name cut to what is left.
TagMender's filenames.make_path only applies 255 bytes per name and max_path_chars (default
4096), so long classical names (several performers in %artist%, work: movement titles) come
out longer than Picard's - a different file name for the same track. Seen end to end with
a 2-CD classical box and Picard's default naming script: Picard "2-01 Ludwig van Beethoven;
Anne-Sophie Mutter, Berliner Philharmoniker, Herbert von Karajan - Violin Concerto in D major,
Op. 61_ I..mp3", TagMender "... Op. 61_ I. Allegro ma non troppo.mp3".

PICARD_COVERAGE.md lists Windows compatibility as "Full"."""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _review24b_common as c  # noqa: E402

TITLE = " ".join("Movement %d Allegro" % i for i in range(1, 20))[:240]
# Picard 3.0.2 make_short_filename(dest, TITLE + ".flac", win_shorten_path=True):
PICARD_NAME = ("Movement 1 Allegro Movement 2 Allegro Movement 3 Allegro Movement 4 Allegro Movement 5 Allegro "
               "Movement 6 Allegro Movement 7 Allegro Movement 8 Allegro Movement 9 Allegro Movement 10 Allegro "
               "Movement 11 Allegro Movement 1.flac")


def picard_reserved(dest):
    """Picard's make_short_filename on Linux: the characters the destination takes below its
    mount point, plus a drive name and the separator."""
    dest = os.path.abspath(str(dest))
    mount = dest
    while mount and not os.path.ismount(mount):
        mount = os.path.dirname(mount)
    if mount == os.path.sep:
        mount = os.path.dirname(dest)
    return len(dest) - len(mount) + 3 + 1


class WindowsPathShortening(unittest.TestCase):
    def test_file_name_cut_to_226(self):
        self.assertEqual(len(PICARD_NAME), 226)
        files = c.album(".flac", 1)
        _p, res, dest = c.move_run(files, "%title%", extra={"title": [TITLE]})
        self.assertEqual(res["counts"], {"done": 1}, c.detail(res))
        self.assertEqual(c.names(dest), [PICARD_NAME])

    def test_whole_path_fits_259_below_the_mount_point(self):
        files = c.album(".flac", 1)
        _p, res, dest = c.move_run(files, "%albumartist%/%album%/%title%",
                                   extra={"album": ["The Dark Side of the Moon " * 6], "title": [TITLE]})
        self.assertEqual(res["counts"], {"done": 1}, c.detail(res))
        rel = c.names(dest)[0]
        self.assertLessEqual(picard_reserved(dest) + len(rel), 259, rel)


if __name__ == "__main__":
    unittest.main()
