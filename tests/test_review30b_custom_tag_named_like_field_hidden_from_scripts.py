# Thirtieth independent review (B). Last round's fix is incomplete; fails with TM_REPO=prev too
# (there the album failed outright).
"""A user tag spelled like a field the file keeps elsewhere - TXXX:TOTALTRACKS beside TRCK (as
ffmpeg leaves them), an APEv2 TOTALTRACKS item beside Track, an MP4 ----:com.apple.iTunes:
TOTALTRACKS beside trkn - is now read as a tag of its own, but under a hidden name
("~custom:TOTALTRACKS"), so tagger scripts and naming cannot see or remove it by its name as
they can in Picard (whose names are case-sensitive: %TOTALTRACKS% is that tag):

  script $delete(TOTALTRACKS)$delete(TRACKTOTAL)   (cleaning ffmpeg's leftovers)
     Picard: TXXX:TOTALTRACKS and TXXX:TRACKTOTAL deleted (APEv2 and MP4 likewise)
     TagMender: TXXX:TRACKTOTAL deleted, TXXX:TOTALTRACKS kept (MP3, M4A and WavPack)
  script $set(comment,%TOTALTRACKS%-%ALBUMARTISTSORT%)   (file TXXX:ALBUMARTISTSORT=Old beside TSO2)
     Picard: COMM "9-Old"; TagMender: COMM "-"
  naming ".../%title% [%TOTALTRACKS%]"
     Picard: "01 Hello [9].mp3"; TagMender: "01 Hello [].mp3"
  M4A, no script: Picard rewrites ----:com.apple.iTunes:TOTALTRACKS with the release's total
     ("2" - mp4.py keeps the freeform's spelling in __casemap and saves the field 'totaltracks'
     there, beside trkn); TagMender leaves the stale "9".

All checked end to end with Picard's own code (r30B_work/j_cust.py).

Cause: tagio.read (this round, ~lines 403-413 and 567-571) files such a tag as
"~custom:" + its spelling; "~" names are hidden from the tagger scripts (plan.py builds the
script's input from `before` without "~" keys, ~line 674) and from naming, and a $delete of
"TOTALTRACKS" is taken as the field totaltracks (field_name lower-cases it), never as the user
tag.

Fix: give scripts and naming the user tag under its own spelling ("TOTALTRACKS"), keep the
lower-case name for the field, and map a $delete/$set of that exact spelling to the user tag
(TXXX/freeform/APEv2 item of that name); for M4A, write the field's value into a freeform atom
spelled like a field Picard saves there (totaltracks/totaldiscs), as Picard does.
"""
import sys
import unittest
from pathlib import Path

import mutagen
from mutagen.id3 import TRCK, TSO2, TXXX
from mutagen.mp4 import MP4FreeForm

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _review30b_common as c  # noqa: E402


def leftovers(files, ext):
    for f in files:
        m = mutagen.File(str(f))
        if m.tags is None:
            m.add_tags()
        if ext == ".mp3":
            m.tags.add(TRCK(encoding=3, text=["1"]))
            m.tags.add(TXXX(encoding=3, desc="TOTALTRACKS", text=["9"]))
            m.tags.add(TXXX(encoding=3, desc="TRACKTOTAL", text=["9"]))
            m.tags.add(TXXX(encoding=3, desc="ALBUMARTISTSORT", text=["Old"]))
            m.tags.add(TSO2(encoding=3, text=["Pink Floyd"]))
        elif ext == ".m4a":
            m.tags["trkn"] = [(1, 0)]
            m.tags["----:com.apple.iTunes:TOTALTRACKS"] = [MP4FreeForm(b"9")]
            m.tags["----:com.apple.iTunes:TRACKTOTAL"] = [MP4FreeForm(b"9")]
        else:
            m.tags["Track"] = "1"
            m.tags["TOTALTRACKS"] = "9"
            m.tags["TRACKTOTAL"] = "9"
        m.save()


def user_totals(path):
    t = mutagen.File(str(path)).tags
    return sorted(k for k in t.keys() if k.upper().endswith(("TOTALTRACKS", "TRACKTOTAL")))


class CustomTagHidden(unittest.TestCase):
    def test_delete_by_name(self):
        for ext in (".mp3", ".m4a", ".wv"):
            files = c.album(ext, 2)
            leftovers(files, ext)
            p, res = c.run(files, scripts=["$delete(TOTALTRACKS)$delete(TRACKTOTAL)"])
            self.assertEqual(res.get("counts"), {"done": 1}, c.detail(p, res))
            self.assertEqual(user_totals(files[0]), [], f"{ext}: Picard deletes both user tags")

    def test_script_sees_user_tags(self):
        files = c.album(".mp3", 2)
        leftovers(files, ".mp3")
        targets = c.proposal(files).targets
        for t in targets:
            t["albumartistsort"] = ["Pink Floyd"]
        p, res = c.run(files, scripts=["$set(comment,%TOTALTRACKS%-%ALBUMARTISTSORT%)"], targets=targets)
        self.assertEqual(res.get("counts"), {"done": 1}, c.detail(p, res))
        self.assertEqual(str(mutagen.File(str(files[0])).tags["COMM::eng"]), "9-Old")

    def test_naming_sees_user_tag(self):
        files = c.album(".mp3", 2)
        leftovers(files, ".mp3")
        p, res, dest = c.move_run(files, "%albumartist%/%album%/$num(%tracknumber%,2) %title% [%TOTALTRACKS%]")
        self.assertEqual(res.get("counts"), {"done": 1}, c.detail(p, res))
        self.assertEqual(c.names(dest), [f"Pink Floyd/The Dark Side of the Moon/0{i} Track {i} [9].mp3"
                                         for i in (1, 2)])

    def test_m4a_freeform_total_rewritten(self):
        files = c.album(".m4a", 2)
        leftovers(files, ".m4a")
        p, res = c.run(files)
        self.assertEqual(res.get("counts"), {"done": 1}, c.detail(p, res))
        t = mutagen.File(str(files[0])).tags
        self.assertEqual(bytes(t["----:com.apple.iTunes:TOTALTRACKS"][0]), b"2",
                         "Picard writes the release's total into the freeform TOTALTRACKS atom")


if __name__ == "__main__":
    unittest.main()
