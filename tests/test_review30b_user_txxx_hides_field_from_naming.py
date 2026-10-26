# Thirtieth independent review (B). Not a regression (fails with TM_REPO=prev too).
"""MP3/WAV/AIFF/DSF: when a file has both a field in its own frame and a user TXXX spelled like
it - TSOC "Old CSort" + TXXX:COMPOSERSORT "Old CSort2" (older taggers / v2.3 compat writers),
TIT1 "Grp" + TXXX:GROUPING "Grp2" - and the release does not set that field, the field's own
value disappears from naming and tagger scripts, and is shown under the user tag's spelling:

  naming "$if2(%composersort%,X)/%grouping%/%title%"
      Picard: "Old CSort/Grp/Hello.mp3"        TagMender: "X/Hello.mp3"
  script $set(comment,%composersort%|%COMPOSERSORT%|%grouping%|%GROUPING%)
      Picard: "Old CSort|Old CSort2|Grp|Grp2"   TagMender: "|Old CSort||Grp"

(checked end to end with Picard's own code, r30B_work/j_cs.py; found by the foreign-tag sweep,
3004:277, where a classical naming script filed an album under the album artist instead of the
composer sort name.)

Cause: tagio.spellings() (tagio.py ~372-388) maps the lower-case name to the user TXXX's
spelling ({"composersort": "COMPOSERSORT"}) even though the field itself lives in TSOC; plan.py
then renames the FIELD's value to that spelling - for the scripts (~674-676: `spell = ... ;
tags = {spell.get(k, k): ...}`) and for naming (~1041-1043: `names[sp_] = names.pop(k_)`) -
while the user tag's own value (now "~custom:COMPOSERSORT", tagio.read) is not offered at all.

Fix: spellings() must skip a TXXX whose lower-case name is a field the file holds in its own
frame (the "~custom:" case); expose the user tag's value under its own spelling instead.
"""
import sys
import unittest
from pathlib import Path

import mutagen
from mutagen.id3 import TIT1, TSOC, TXXX

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _review30b_common as c  # noqa: E402


def leftovers(files):
    for f in files:
        m = mutagen.File(str(f))
        if m.tags is None:
            m.add_tags()
        m.tags.add(TSOC(encoding=3, text=["Old CSort"]))
        m.tags.add(TXXX(encoding=3, desc="COMPOSERSORT", text=["Old CSort2"]))
        m.tags.add(TIT1(encoding=3, text=["Grp"]))
        m.tags.add(TXXX(encoding=3, desc="GROUPING", text=["Grp2"]))
        m.save()


class UserTxxxHidesField(unittest.TestCase):
    def test_naming(self):
        files = c.album(".mp3", 2)
        leftovers(files)
        p, res, dest = c.move_run(files, "$if2(%composersort%,X)/%grouping%/%title%")
        self.assertEqual(res.get("counts"), {"done": 1}, c.detail(p, res))
        self.assertEqual(c.names(dest), ["Old CSort/Grp/Track 1.mp3", "Old CSort/Grp/Track 2.mp3"])

    def test_script(self):
        files = c.album(".mp3", 2)
        leftovers(files)
        p, res = c.run(files, scripts=["$set(comment,%composersort%|%COMPOSERSORT%|%grouping%|%GROUPING%)"])
        self.assertEqual(res.get("counts"), {"done": 1}, c.detail(p, res))
        self.assertEqual(str(mutagen.File(str(files[0])).tags["COMM::eng"]), "Old CSort|Old CSort2|Grp|Grp2")


if __name__ == "__main__":
    unittest.main()
