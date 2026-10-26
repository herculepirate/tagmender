"""Picard's "Delete empty directories" turned off is not followed: after moving an album
TagMender removes the emptied source folders whatever Picard.ini says.

The executor (Executor.run -> cleanup_dirs) decides by the profile's remove_empty_dirs
(default True) alone; nothing derives it from Picard.ini's delete_empty_dirs, unlike
preserve_timestamps (profile null = "as Picard is set"). docs/PICARD_COVERAGE.md:
"Delete empty directories | on | Full". Picard 3.0.2 (File._save_and_rename) removes the old
folder only `if config.setting['delete_empty_dirs']`. A user who keeps empty folders (a
download client that still seeds into them, a watched inbox folder that must stay) loses
them. Fails with TM_REPO=prev too.
"""
import os
import unittest
from pathlib import Path

import _review27b_common as c
import picard_config

NAMING = "%albumartist%/%album%/$num(%tracknumber%,2) %title%"


class DeleteEmptyDirsOff(unittest.TestCase):
    def setUp(self):
        ini = c.write_ini("[application]\nversion=3.0.2.final0\n\n[setting]\ndelete_empty_dirs=false\n")
        os.environ["TAGMENDER_PICARD_INI"] = str(ini)
        picard_config._cache.clear()

    def tearDown(self):
        os.environ["TAGMENDER_PICARD_INI"] = "/nonexistent/tm27B/Picard.ini"
        picard_config._cache.clear()

    def test_source_folder_kept(self):
        files = c.album(".mp3", 2)
        src = Path(files[0]).parent
        p, res, dest = c.move_run(files, NAMING)
        self.assertEqual(res.get("counts"), {"done": 1}, c.detail(p, res))
        self.assertEqual(len(c.names(dest)), 2)
        self.assertTrue(src.is_dir(), "Picard keeps the emptied folder when Delete empty directories is off")


if __name__ == "__main__":
    unittest.main()
