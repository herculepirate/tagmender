# Twenty-third independent review (B). Fails with TM_REPO=prev too (not a regression).
"""A custom tag a tagger script makes - $set(MyTag,hello), $set(ORIGINALYEAR,...) - is written
in lower case in ID3 (MP3, WAV, AIFF) and MP4: TXXX:mytag, ----:com.apple.iTunes:mytag.

tagio.field_name() lower-cases every field name ("as TagMender keys it") and _set_id3 /
_set_mp4 use that name for a NEW frame; only a tag the file already has keeps its case
(docs/FORMATS.md). Picard 3.0.2 keeps the script's name: end to end on the same release and
script it writes TXXX:MyTag (id3.py: TXXX desc = name) and ----:com.apple.iTunes:MyTag
(mp4.py). Tag editors and players that match these fields by name (case-sensitively in
MP4 freeform atoms) show a different field from Picard's. (Vorbis and APEv2 agree: Picard
writes MYTAG / Mytag there too.)"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _review23b_common as c  # noqa: E402

import mutagen  # noqa: E402


class CustomTagCase(unittest.TestCase):
    def test_mp3(self):
        files = c.album(".mp3")
        _p, res = c.run(files, ["$set(MyTag,hello)"])
        self.assertEqual(res["counts"], {"done": 1}, c.detail(res))
        descs = [fr.desc for fr in mutagen.File(files[0]).tags.getall("TXXX")]
        self.assertIn("MyTag", descs)

    def test_m4a(self):
        files = c.album(".m4a")
        _p, res = c.run(files, ["$set(MyTag,hello)"])
        self.assertEqual(res["counts"], {"done": 1}, c.detail(res))
        self.assertIn("----:com.apple.iTunes:MyTag", list(mutagen.File(files[0]).tags.keys()))


if __name__ == "__main__":
    unittest.main()
