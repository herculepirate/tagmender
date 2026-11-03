# Thirtieth independent review (B). Not a regression (fails with TM_REPO=prev too). Low severity.
"""WAV: Picard 3.0.2 fills the RIFF INFO comment (ICMT) from every comment field, also a
described or other-language one (picard/formats/wav.py translate_tag_to_riff_name:
`if name.startswith('comment:'): name = 'comment'` - the last such field written wins);
TagMender writes ICMT only from the plain "comment":

  file COMM lang "XXX" (Mp3tag's comment frame), no plain comment
      Picard: ICMT "mp3tag comment"            TagMender: no ICMT
  script $set(comment:foo,bar) on a file with a plain comment "ripped by EAC"
      Picard: ICMT "bar"                       TagMender: ICMT "ripped by EAC"

Checked end to end with Picard's own code (r30B_work/j_riff2.py; foreign sweep 3004:162, where
Picard, reading the WAV back, then names the file with that comment - it reads INFO for fields
the ID3 tag lacks - and TagMender's file gets another name).

Cause: tagio.RIFF_INFO maps only "comment" (tagio.py ~1079); plan.py's fe["riff"] (~1003) and
_write_riff_info take "comment:*" fields nowhere.

Fix: map comment:* fields to ICMT as Picard does (in the order Picard writes the file's
fields: the file's own first, then the script's).
"""
import struct
import sys
import unittest
from pathlib import Path

import mutagen
from mutagen.id3 import COMM

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _review30b_common as c  # noqa: E402


def riff_info(path):
    data = open(path, "rb").read()
    pos, out = 12, {}
    while pos + 8 <= len(data):
        cid, size = data[pos:pos + 4], struct.unpack("<I", data[pos + 4:pos + 8])[0]
        if cid == b"LIST" and data[pos + 8:pos + 12] == b"INFO":
            p, end = pos + 12, pos + 8 + size
            while p + 8 <= end:
                sid, ssz = data[p:p + 4], struct.unpack("<I", data[p + 4:p + 8])[0]
                out[sid.decode("latin-1")] = data[p + 8:p + 8 + ssz].rstrip(b"\0").decode("latin-1")
                p += 8 + ssz + (ssz & 1)
        pos += 8 + size + (size & 1)
    return out


class RiffComment(unittest.TestCase):
    def test_other_language_comment(self):
        files = c.album(".wav", 2)
        for f in files:
            m = mutagen.File(str(f))
            if m.tags is None:
                m.add_tags()
            m.tags.add(COMM(encoding=3, lang="XXX", desc="", text=["mp3tag comment"]))
            m.save()
        p, res = c.run(files)
        self.assertEqual(res.get("counts"), {"done": 1}, c.detail(p, res))
        self.assertEqual(riff_info(files[0]).get("ICMT"), "mp3tag comment")

    def test_described_comment_from_script(self):
        files = c.album(".wav", 2)
        for f in files:
            m = mutagen.File(str(f))
            if m.tags is None:
                m.add_tags()
            m.tags.add(COMM(encoding=3, lang="eng", desc="", text=["ripped by EAC"]))
            m.save()
        p, res = c.run(files, scripts=["$set(comment:foo,bar)"])
        self.assertEqual(res.get("counts"), {"done": 1}, c.detail(p, res))
        self.assertEqual(riff_info(files[0]).get("ICMT"), "bar")


if __name__ == "__main__":
    unittest.main()
