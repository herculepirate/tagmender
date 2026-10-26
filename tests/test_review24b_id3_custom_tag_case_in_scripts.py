# Twenty-fourth independent review (B). Fails with TM_REPO=prev too (not a regression).
"""MP3 (and WAV): a tagger script sees a file's own ID3 user tag (TXXX) under a lower-case
name, while Picard 3.0.2 sees it as its description is spelled. A script written and tested
in Picard therefore does something else in TagMender:

  file TXXX:CUSTOM = "keep me" (foobar2000 / Mp3tag write such tags in capitals)
  * $set(subtitle,%CUSTOM%)  Picard: TIT3 "keep me";        TagMender: no TIT3
  * $delete(CUSTOM)          Picard: TXXX:CUSTOM removed;   TagMender: kept
  * $delete(custom)          Picard: TXXX:CUSTOM kept;      TagMender: removed (the user's tag lost)
  * $set(subtitle,%custom%)  Picard: no TIT3;               TagMender: TIT3 "keep me"

Cause: tagio.read() keys every field with tagio.field_name() (tagio.py:530, k.lower()), and
plan.Planner._scripted (plan.py:635) feeds those keys to the script engine, so %CUSTOM% is
empty and $delete(CUSTOM) lists "CUSTOM" in "~remove", which never matches the file's
lower-cased "custom" in diff_tags. Picard's
ID3File._load keeps a TXXX description that is not one of its own names as it is
(metadata["CUSTOM"]), and its script names are case-sensitive.

All four confirmed end to end with Picard 3.0.2's own code (Track.update_file_metadata,
ID3File._save) on an MP3 holding TXXX:CUSTOM. Vorbis, MP4 and APEv2 are not affected (Picard
lower-cases those names itself)."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _review24b_common as c  # noqa: E402

from mutagen.id3 import ID3, TXXX  # noqa: E402


def album_with_custom():
    files = c.album(".mp3")
    for f in files:
        t = ID3()
        t.add(TXXX(encoding=3, desc="CUSTOM", text=["keep me"]))
        t.save(str(f))
    return files


def txxx(f):
    return {fr.desc: list(fr.text) for fr in ID3(str(f)).getall("TXXX")}


class Id3CustomTagCase(unittest.TestCase):
    def test_read_with_picard_spelling(self):
        files = album_with_custom()
        _p, res = c.run(files, ["$set(subtitle,%CUSTOM%)"])
        self.assertEqual(res["counts"], {"done": 1}, c.detail(res))
        self.assertEqual([str(x) for x in ID3(str(files[0])).getall("TIT3")], ["keep me"])

    def test_delete_with_picard_spelling(self):
        files = album_with_custom()
        _p, res = c.run(files, ["$delete(CUSTOM)"])
        self.assertEqual(res["counts"], {"done": 1}, c.detail(res))
        self.assertNotIn("CUSTOM", txxx(files[0]))

    def test_delete_lower_case_keeps_the_tag(self):
        files = album_with_custom()
        _p, res = c.run(files, ["$delete(custom)"])
        self.assertEqual(res["counts"], {"done": 1}, c.detail(res))
        self.assertEqual(txxx(files[0]).get("CUSTOM"), ["keep me"])


if __name__ == "__main__":
    unittest.main()
