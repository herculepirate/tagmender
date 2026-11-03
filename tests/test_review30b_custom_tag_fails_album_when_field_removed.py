# Thirtieth independent review (B). Not a regression (fails with TM_REPO=prev too); last round's
# fix for user tags spelled like fields does not cover a field that the run removes.
"""An album whose files carry a user tag spelled like a field (ffmpeg's TXXX:TOTALTRACKS /
TXXX:TOTALDISCS beside TRCK "5/9" / TPOS "1/1", Mp3tag's TXXX:RELEASECOUNTRY, an M4A freeform
TOTALTRACKS beside trkn) FAILS - nothing is tagged - when the run removes that field from its
own place:

  MP3 TRCK "5/9" + TXXX:TOTALTRACKS "9", script $delete(totaltracks) (or $unset(totaltracks))
      -> "track01.mp3: tag totaltracks did not read back as written; album put back as it was"
  MP3 TPOS "1/1" + TXXX:TOTALDISCS "1", the common script
      $if($eq(%totaldiscs%,1),$delete(discnumber)$delete(totaldiscs))
      -> "tag totaldiscs did not read back as written"
  MP3 TXXX:RELEASECOUNTRY "US", a release with no country (MusicBrainz removes the field)
      -> "tag releasecountry did not read back as written"
  M4A trkn (5, 9) + ----:com.apple.iTunes:TOTALTRACKS "9", $delete(totaltracks)
      -> "tag totaltracks did not read back as written"

Picard 3.0.2 tags all of these (the user tag is a tag of its own name: it stays, the field is
written or removed in its own place; checked end to end, r30B_work/j_cust.py and the foreign-tag
sweep, where 3004:41 and 3004:13 failed this way).

Cause: tagio.read (~567-571) reads the user tag as the field whenever the field's own place is
empty. Before the write the field is there (TRCK "5/9"), so the user tag is "~custom:"; the
write empties the field's place, and the read-back (executor.py, "did not read back as
written") now reads the user tag AS the field - a value the plan said is gone - and rolls the
album back.

Fix: never read such a user tag as the field (always a tag of its own), as Picard reads it.
"""
import sys
import unittest
from pathlib import Path

import mutagen
from mutagen.id3 import TPOS, TRCK, TXXX
from mutagen.mp4 import MP4FreeForm

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _review30b_common as c  # noqa: E402


def mp3_with(files, **user):
    for f in files:
        m = mutagen.File(str(f))
        if m.tags is None:
            m.add_tags()
        m.tags.add(TRCK(encoding=3, text=["5/9"]))
        m.tags.add(TPOS(encoding=3, text=["1/1"]))
        for k, v in user.items():
            m.tags.add(TXXX(encoding=3, desc=k, text=[v]))
        m.save()


class CustomTagFailsAlbum(unittest.TestCase):
    def test_delete_total_beside_ffmpeg_total(self):
        files = c.album(".mp3", 2)
        mp3_with(files, TOTALTRACKS="9")
        p, res = c.run(files, scripts=["$delete(totaltracks)"])
        self.assertEqual(res.get("counts"), {"done": 1}, c.detail(p, res))

    def test_single_disc_script(self):
        files = c.album(".mp3", 2)
        mp3_with(files, TOTALDISCS="1")
        p, res = c.run(files, scripts=["$if($eq(%totaldiscs%,1),$delete(discnumber)$delete(totaldiscs))"])
        self.assertEqual(res.get("counts"), {"done": 1}, c.detail(p, res))

    def test_release_without_country(self):
        files = c.album(".mp3", 2)
        mp3_with(files, RELEASECOUNTRY="US")
        targets = c.proposal(files).targets
        for t in targets:
            t["~remove"] = ["releasecountry"]
        p, res = c.run(files, targets=targets)
        self.assertEqual(res.get("counts"), {"done": 1}, c.detail(p, res))

    def test_m4a_delete_total(self):
        files = c.album(".m4a", 2)
        for f in files:
            m = mutagen.File(str(f))
            if m.tags is None:
                m.add_tags()
            m.tags["trkn"] = [(5, 9)]
            m.tags["----:com.apple.iTunes:TOTALTRACKS"] = [MP4FreeForm(b"9")]
            m.save()
        p, res = c.run(files, scripts=["$delete(totaltracks)"])
        self.assertEqual(res.get("counts"), {"done": 1}, c.detail(p, res))


if __name__ == "__main__":
    unittest.main()
