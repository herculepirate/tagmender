# Sixteenth independent review (A): this test failed before its fix.
# Sixteenth independent review (A). Not a regression: the previous version does the same.
# An MP3 with an ID3v2.2 tag (iTunes and old taggers wrote them). mutagen cannot write v2.2:
# backup_tags keeps it re-saved as v2.3 frames with "id3_version": 2, and restore_tags writes
# that back as ID3v2.3. So undo gives the file a v2.3 tag in place of its own v2.2 tag - and
# its backup record no longer matches (id3_version 3 != 2). The FLAC head and the MP3 trailer
# are now kept as raw bytes (_put_ends); a v2.2 tag at the front of an MP3 could be too.
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _review16a_common import Run, bare_mp3, executor, state  # noqa: E402


def v22_tag():
    fr = b"TT2" + (6).to_bytes(3, "big") + b"\x00Old22" + b"TP1" + (4).to_bytes(3, "big") + b"\x00Art"
    body = fr + b"\0" * 64
    n = len(body)
    return b"ID3\x02\x00\x00" + bytes([(n >> 21) & 127, (n >> 14) & 127, (n >> 7) & 127, n & 127]) + body


class Id3v22NotUndoneExactly(Run):
    def test_undo_puts_the_v22_tag_back(self):
        self.fs = [bare_mp3(self.alb / f"0{n} x.mp3", 440 + 100 * n) for n in (1, 2)]
        for f in self.fs:
            f.write_bytes(v22_tag() + f.read_bytes())
        orig = [f.read_bytes() for f in self.fs]
        p, res, before, prof = self.run_plan({})
        out = executor.undo(res["job"])
        self.assertEqual(self.fs[0].read_bytes()[:4], b"ID3\x02", f"the ID3v2.2 tag came back as v2.3: {out}")
        self.assertEqual(state(self.fs[0])[0], before[self.fs[0]][0])
        self.assertEqual([f.read_bytes() for f in self.fs], orig)


if __name__ == "__main__":
    unittest.main()
