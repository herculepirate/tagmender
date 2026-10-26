# Twenty-fifth independent review (B). Fails with TM_REPO=prev too (not a regression); the
# last fix round made tagger scripts see a file's TXXX:CUSTOM as %CUSTOM%, but not the
# file naming script.
"""MP3 (and WAV/AIFF): the file naming script sees a file's own ID3 user tag (TXXX) under a
lower-case name, while Picard 3.0.2 sees it as its description is spelled - the opposite of
what the tagger scripts now see in TagMender.

  file TXXX:CUSTOM = "keep me" (foobar2000 / Mp3tag write such tags in capitals),
  naming script "%title% [%CUSTOM%] [%custom%]":
  Picard 3.0.2 (end to end, File.make_filename): "Hello [keep me] [].mp3"
  TagMender:                                      "Hello [] [keep me].mp3"

Cause: plan.Planner.album builds the naming fields from `merged` (plan.py:930-935), which
starts from `before` = tagio.read() (every name lower-cased by tagio.field_name). The
spelling fix of the last round (tagio.spellings, plan.py:672-688) is applied only to the
fields handed to the tagger scripts (_scripted), not to `merged`/`names` for naming."""
import sys
import unittest
from pathlib import Path

from mutagen.id3 import ID3, TXXX

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _review25b_common as c  # noqa: E402


def album_with_custom():
    files = c.album(".mp3", 1)
    for f in files:
        t = ID3()
        t.add(TXXX(encoding=3, desc="CUSTOM", text=["keep me"]))
        t.save(str(f))
    return files


class CustomTagCaseInNaming(unittest.TestCase):
    def test_spelled_as_in_the_file(self):
        _p, res, dest = c.move_run(album_with_custom(), "%title% [%CUSTOM%] [%custom%]")
        self.assertEqual(res["counts"], {"done": 1}, c.detail(res))
        self.assertEqual(c.names(dest), ["Track 1 [keep me] [].mp3"])

    def test_folder_from_custom_tag(self):
        _p, res, dest = c.move_run(album_with_custom(), "%CUSTOM%/%title%")
        self.assertEqual(res["counts"], {"done": 1}, c.detail(res))
        self.assertEqual(c.names(dest), ["keep me/Track 1.mp3"])


if __name__ == "__main__":
    unittest.main()
