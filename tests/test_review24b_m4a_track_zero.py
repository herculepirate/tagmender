# Twenty-fourth independent review (B). Fails with TM_REPO=prev too (prev failed the whole
# album; the last round's M4A number fix now skips the number instead).
"""M4A: a track (or disc) number 0 - a hidden track / pregap numbered 0, or a script such as
$set(tracknumber,0) - is not written to trkn, and the old trkn stays; Picard writes (0, total).

Cause: tagio._mp4_int(n) (tagio.py:861) accepts only 1 <= n < 32768 unless zero=True;
tagio.stored() (tagio.py:830-834, "if not n: out.pop(num); out.pop(tot)") and the MP4 branch
of the number writer (tagio.py:1056, `if _mp4_int(n): ...`) call it without zero=True, so "0" is treated as "not a
number" and dropped - with its total. Picard 3.0.2 MP4File._save writes
tags['trkn'] = [(int(metadata['tracknumber']), int(totaltracks))] whenever int() succeeds:
(0, 2) (disk (0, 1) for $set(discnumber,0), confirmed end to end). The name also loses the number (see test_review24b_naming_sees_only_stored_fields):
Picard "00 Hello.m4a" with its default naming script, TagMender "Hello.m4a"."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _review24b_common as c  # noqa: E402

import mutagen  # noqa: E402


class M4aTrackZero(unittest.TestCase):
    def test_tracknumber_zero_written(self):
        files = c.album(".m4a")
        _p, res = c.run(files, ["$set(tracknumber,0)"])
        self.assertEqual(res["counts"], {"done": 1}, c.detail(res))
        self.assertEqual(mutagen.File(files[0]).get("trkn"), [(0, 2)])

    def test_tracknumber_zero_replaces_old_number(self):
        files = c.album(".m4a")
        for f in files:
            m = mutagen.File(f)
            m["trkn"] = [(7, 9)]
            m.save()
        _p, res = c.run(files, ["$set(tracknumber,0)"])
        self.assertEqual(res["counts"], {"done": 1}, c.detail(res))
        self.assertEqual(mutagen.File(files[0]).get("trkn"), [(0, 2)])

    def test_discnumber_zero_written(self):
        files = c.album(".m4a")
        _p, res = c.run(files, ["$set(discnumber,0)"])
        self.assertEqual(res["counts"], {"done": 1}, c.detail(res))
        self.assertEqual(mutagen.File(files[0]).get("disk"), [(0, 1)])


if __name__ == "__main__":
    unittest.main()
