"""Picard's "Windows compatibility" turned off (a Linux/macOS user's common choice) is not
followed: TagMender names files Windows-compatible whatever Picard.ini says.

TagMender reads Picard's other file-naming options from Picard.ini (ascii_filenames,
replace_spaces_with_underscores, replace_dir_separator, windows_long_paths - tagger_settings
TAG_DEFAULTS), but not windows_compatibility: the profile's filenames.compat ("windows" by
default) alone decides, and nothing derives it from Picard.ini. docs/FEATURES.md lists
"Windows compatibility | on | Full" with no mention that Picard's setting is ignored.

With windows_compatibility=false Picard 3.0.2 (File.make_filename -> make_save_path with
win_compat False) names "AC/DC" / "Who Made Who?" / "Shake: Your Foundations" as
"AC_DC/Who Made Who?/01 Shake: Your Foundations.mp3"; TagMender gives
"AC_DC/Who Made Who_/01 Shake_ Your Foundations.mp3". Checked end to end with Picard's own
code (a weird-characters release: every ':', '?', '*', '|', '<', '>', '"', and the trailing
dots of "R.E.M." differ). With the profile's filenames.compat "linux" TagMender matches
Picard. Fails with TM_REPO=prev too.
"""
import os
import unittest

import _review27b_common as c
import tagger_settings

NAMING = "%albumartist%/%album%/$num(%tracknumber%,2) %title%"
import _v2_defaults  # noqa: E402  (written for the defaults before schema 3)
_v2_defaults.use()


class WindowsCompatibilityOff(unittest.TestCase):
    def setUp(self):
        ini = c.write_ini("[application]\nversion=3.0.2.final0\n\n[setting]\nwindows_compatibility=false\n")
        os.environ["TAGMENDER_TAGGER_INI"] = str(ini)
        tagger_settings._cache.clear()

    def tearDown(self):
        os.environ["TAGMENDER_TAGGER_INI"] = "/nonexistent/tm27B/Picard.ini"
        tagger_settings._cache.clear()

    def test_names_keep_colon_and_question_mark(self):
        files = c.album(".mp3", 1)
        t = {k: list(v) for k, v in c.TARGET.items()}
        t.update(albumartist=["AC/DC"], artist=["AC/DC"], album=["Who Made Who?"],
                 title=["Shake: Your Foundations"], tracknumber=["1"], totaltracks=["1"])
        p, res, dest = c.move_run(files, NAMING, targets=[t])
        self.assertEqual(res.get("counts"), {"done": 1}, c.detail(p, res))
        self.assertEqual(c.names(dest), ["AC_DC/Who Made Who?/01 Shake: Your Foundations.mp3"])


if __name__ == "__main__":
    unittest.main()
