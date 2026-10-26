# Twenty-ninth independent review (B). Fails with TM_REPO=prev too (not a regression: last
# round's fix covers a described comment the script sets, not one the file already has).
"""FLAC/Ogg/Opus/WavPack: "$unset(comment)" leaves the file's plain comment in place when the
file also has a described comment (a COMMENT value ending in "(...)", which Picard reads as
comment:desc). Picard 3.0.2 removes the plain one and keeps the described one.

Checked end to end with Picard's own code: a file with COMMENT=["ripped by EAC",
"Recorded live (2 nights)"] and the tagger script $unset(comment):
  Picard:    COMMENT=["Recorded live (2 nights)"]
  TagMender: COMMENT=["ripped by EAC", "Recorded live (2 nights)"]
($set(comment,) and $delete(comment) agree with Picard; MP3/M4A are not affected.)

In Picard the scripts run on the file's metadata; VCommentFile._save / APEv2File._save write
COMMENT whole from what is left (comment:desc as "text (desc)"), replacing the field.

Cause: plan.py:925-933 (last round's fix) removes the file's plain comment only when
`any(k_.startswith("comment:") and raw[k_] for k_ in raw)` - a described comment in the
*target*. A described comment the file already holds (before) is not considered, so the plain
comment the script unset is left as the file has it.

Fix: in that condition also count a described comment the file has (any "comment:" key in
`before` that the target does not remove).
"""
import sys
import unittest
from pathlib import Path

import mutagen

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _review29b_common as c  # noqa: E402

DESCRIBED = "Recorded live (2 nights)"


class UnsetCommentBesideFileDescribed(unittest.TestCase):
    def _go(self, ext, key="COMMENT"):
        files = c.album(ext, 2)
        for f in files:
            m = mutagen.File(str(f))
            if m.tags is None:
                m.add_tags()
            m.tags[key] = ["ripped by EAC", DESCRIBED]
            m.save()
        p, res = c.run(files, scripts=["$unset(comment)"])
        self.assertEqual(res.get("counts"), {"done": 1}, c.detail(p, res))
        for f in files:
            got = mutagen.File(str(f)).tags.get(key)
            self.assertEqual([str(x) for x in got], [DESCRIBED], "Picard keeps only the described comment")

    def test_flac(self):
        self._go(".flac")

    def test_ogg(self):
        self._go(".ogg")

    def test_opus(self):
        self._go(".opus")

    def test_wavpack(self):
        self._go(".wv", "Comment")


if __name__ == "__main__":
    unittest.main()
