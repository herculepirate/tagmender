# Twentieth independent review (B): this test failed before its fix.
# Twentieth independent review (B).
"""An APEv2 (WavPack, Monkey's Audio, Musepack) Comment is now split into "comment:desc" by
Picard's VORBIS rule (count parentheses back from the end, cut one character before the "(").
Picard 3.0.2 reads APEv2 by its own rule (picard/formats/apev2.py _load): only a value that
ends in ")" and has " (" (a space before the parenthesis) at index > 0, split at the LAST " (".

  "Recorded(2001)"  Picard: comment = "Recorded(2001)"      TagMender: comment:2001 = "Recorde"
  "a (b (c))"       Picard: comment:c) = "a (b"             TagMender: comment:b (c) = "a"

(checked with Picard's APEv2File._load on such a file). Beyond the misread value, a script's
$set(comment,...) then keeps "Recorded(2001)" next to the new comment (it is not a plain
comment to TagMender), where Picard replaces it.

Regression: passes with TM_REPO=<prev> (which read APEv2 Comment as it is)."""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _review20b_common  # noqa: E402,F401

from mutagen.apev2 import APEv2  # noqa: E402
import tagio  # noqa: E402


class Apev2CommentSplit(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.f = self.tmp / "a.wv"
        subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "sine=duration=0.3", "-c:a", "wavpack",
                        "-map_metadata", "-1", "-y", str(self.f)], check=True)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _read(self, values):
        t = APEv2()
        t["Comment"] = values
        t.save(str(self.f))
        return {k: v for k, v in tagio.read(self.f).items() if k.startswith("comment")}

    def test_no_space_before_parenthesis_is_a_plain_comment(self):
        self.assertEqual(self._read(["Recorded(2001)"]), {"comment": ["Recorded(2001)"]})

    def test_split_at_the_last_space_parenthesis(self):
        self.assertEqual(self._read(["a (b (c))"]), {"comment:c)": ["a (b"]})


if __name__ == "__main__":
    unittest.main()
