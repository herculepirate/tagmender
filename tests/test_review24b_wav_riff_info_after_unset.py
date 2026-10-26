# Twenty-fourth independent review (B). Fails with TM_REPO=prev too (not a regression).
"""WAV: a field a tagger script $unset-s (or sets empty) still goes into the RIFF INFO
chunk TagMender writes, taken from the file's ID3 tag; Picard 3.0.2 leaves it out.

Cause: plan.py:888 (`fe["riff"] = {k: list(merged[k]) for k in tagio.RIFF_INFO if
merged.get(k)}`) writes RIFF INFO from `merged` - every field the file holds after the
write. Picard's WAVFile._save writes INFO from metadata.rawitems(): $unset(genre) takes genre
out of the track's metadata (the ID3 TCON frame stays, as it is not a deleted tag), so no
IGNR is written.

Picard, end to end: a WAV holding ID3 TCON "Rock", tagger script $unset(genre): ID3 TCON
"Rock" kept in both; RIFF INFO without IGNR in Picard, with IGNR "Rock" in TagMender. (The
same with $set(comment,) and the file's COMM: no ICMT in Picard.)"""
import struct
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _review24b_common as c  # noqa: E402

from mutagen.id3 import TCON  # noqa: E402
from mutagen.wave import WAVE  # noqa: E402


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


class WavRiffInfoAfterUnset(unittest.TestCase):
    def test_unset_genre_not_in_riff_info(self):
        files = c.album(".wav", 1)
        w = WAVE(str(files[0]))
        w.add_tags()
        w.tags.add(TCON(encoding=3, text=["Rock"]))
        w.save()
        _p, res = c.run(files, ["$unset(genre)"])
        self.assertEqual(res["counts"], {"done": 1}, c.detail(res))
        self.assertEqual(WAVE(str(files[0])).tags["TCON"].text, ["Rock"])
        info = riff_info(files[0])
        self.assertEqual(info.get("INAM"), "Track 1")
        self.assertNotIn("IGNR", info)


if __name__ == "__main__":
    unittest.main()
