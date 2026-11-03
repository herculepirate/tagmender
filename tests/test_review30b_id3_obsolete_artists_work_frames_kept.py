# Thirtieth independent review (B). Not a regression (fails with TM_REPO=prev too); last round's
# fix (TXXX:Artists renamed when the artists CHANGE) does not cover an unchanged value.
"""MP3/WAV/AIFF: an old Picard's TXXX:Artists and TXXX:Work frames are left under their
obsolete names when their values do not change. Picard 3.0.2 reads them as ARTISTS / WORK
(id3.py __rename_freetext, _load_txxx_frame) and on every save writes TXXX:ARTISTS /
TXXX:WORK and deletes the old frames (_save_freetext_tag: `tags.delall('TXXX:' +
self.__rrename_freetext[description])`).

Checked end to end with Picard's own code (r30B_work/j_lyr.py): file TXXX:Artists=Adele,
TXXX:Work=Old Work, release artists Adele, no work -> Picard: TXXX:ARTISTS=Adele,
TXXX:WORK=Old Work; TagMender: TXXX:Artists, TXXX:Work as they were. Software reading
Picard's (case-sensitive) frame names finds no artists/work in TagMender's files, and every
later Picard save of the album rewrites them.

Cause: tagio._set_id3 renames them only when the field is written (tagio.py ~1371-1377,
`keep_case = desc if (k in ID3_FREETEXT ...)`), and the plan writes a field only when its
value changes (diff_tags), so an unchanged Artists/Work frame is never touched.

Fix: when an ID3 file is written, rename TXXX:Artists -> TXXX:ARTISTS and TXXX:Work ->
TXXX:WORK (the frames Picard renames), whether or not their value changes - e.g. plan them as
a change when the file's frame has the obsolete spelling (tagio.spellings shows it).
"""
import sys
import unittest
from pathlib import Path

import mutagen
from mutagen.id3 import TXXX

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _review30b_common as c  # noqa: E402


class ObsoleteFrames(unittest.TestCase):
    def test_unchanged_artists_and_work(self):
        files = c.album(".mp3", 2)
        for f in files:
            m = mutagen.File(str(f))
            if m.tags is None:
                m.add_tags()
            m.tags.add(TXXX(encoding=3, desc="Artists", text=["Pink Floyd"]))
            m.tags.add(TXXX(encoding=3, desc="Work", text=["Old Work"]))
            m.save()
        targets = c.proposal(files).targets
        for t in targets:
            t["artists"] = ["Pink Floyd"]
        p, res = c.run(files, targets=targets)
        self.assertEqual(res.get("counts"), {"done": 1}, c.detail(p, res))
        t = mutagen.File(str(files[0])).tags
        self.assertEqual(sorted(k for k in t if k.lower() in ("txxx:artists", "txxx:work")),
                         ["TXXX:ARTISTS", "TXXX:WORK"], "Picard's frame names, the obsolete ones gone")


if __name__ == "__main__":
    unittest.main()
