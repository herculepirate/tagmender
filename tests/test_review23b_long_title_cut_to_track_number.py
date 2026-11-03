# Twenty-third independent review (B). Fails with TM_REPO=prev too (not a regression).
"""A long title without spaces (Japanese, Chinese ...; or one very long word) is cut away
entirely: the file is named after its track number only ("13.flac").

filenames.truncate_words keeps the longest whole-word prefix that fits; with Picard's default
naming the stem is "13 <title>", so the only whole word that fits is "13" and the title is
gone. The module's own rule ("word-aware truncation ... never below 8 characters"; a cut
inside a word "only when the first word alone is too long") is not met: step b (each
component within its own limit) has no minimum, unlike step c.

Picard 3.0.2 (File.make_filename -> make_short_filename, same release and default naming
script) keeps as much of the title as fits:
  13 日本語のタイトルとても長い日本語のタイトルとても長い...日本語の.flac
  07 xxxxxxxx....flac
TagMender: "13.flac", "07.flac". Every such track of an album gets the same bare-number look
and its title is lost from the file name."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _review23b_common as c  # noqa: E402,F401

import filenames as fnm  # noqa: E402

JA = "日本語のタイトルとても長い" * 12          # 156 characters, 468 UTF-8 bytes


class LongTitleCut(unittest.TestCase):
    def test_cjk_title_keeps_its_start(self):
        path, notes = fnm.make_path("/music", ["R.E.M.", "Live", "13 " + JA], ".flac", {})
        self.assertIsNotNone(path, notes)
        self.assertTrue(path.name.startswith("13 日本語のタイトル"), path.name)
        self.assertLessEqual(len(path.name.encode()), 255)

    def test_one_long_word_title_keeps_its_start(self):
        path, notes = fnm.make_path("/music", ["R.E.M.", "Live", "07 " + "x" * 300], ".flac", {})
        self.assertIsNotNone(path, notes)
        self.assertTrue(path.name.startswith("07 xxxxxxxx"), path.name)


if __name__ == "__main__":
    unittest.main()
