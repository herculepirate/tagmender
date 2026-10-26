# Thirtieth independent review (B). Not a regression (fails with TM_REPO=prev too).
"""WAV: the RIFF INFO tags a file already has (EAC, dBpoweramp, Windows Media Player, foobar2000
and Windows Explorer tag WAV files there - often the only tags a WAV rip has) are never read.
Picard 3.0.2 reads them as the file's tags for every field the ID3 tag does not have
(picard/formats/wav.py WAVFile._info: `if name not in metadata: metadata[name] = value`), so:

  file: RIFF INFO only - IART, INAM, ICMT "ripped with EAC", IGNR "Pop", ICOP "(c) X", IENG "Eng"
  Picard: the ID3 tag it writes carries COMM "ripped with EAC", TCON "Pop", TCOP "(c) X",
          TIPL engineer "Eng" (fields the release does not set keep the file's values, as for
          any other format); scripts and naming see %comment%, %genre% ...;
          naming ".../%title% [%genre%]" -> "01 Hello [Pop].wav"
  TagMender: none of these in ID3, %genre% empty -> "01 Hello [].wav"

Checked end to end with Picard's own code (r30B_work/j_riff.py).

So a user's genres and comments kept in the INFO chunk are dropped from the ID3 tag that players
read first, and the album is named differently than Picard names it.

Cause: tagio.read() has no RIFF INFO reader (only _write_riff_info, tagio.py ~1085), so `before`
(and the scripts' and naming's view of the file) has none of these fields.

Fix: in tagio.read for a WAVE file, add the INFO entries (Picard's TRANSLATE_RIFF_INFO map,
UTF-8 then windows-1252 decoding, trailing NULs stripped) for fields the ID3 tag does not have,
so they are planned as values to write into ID3 like any other kept field.
"""
import struct
import sys
import unittest
from pathlib import Path

import mutagen

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _review30b_common as c  # noqa: E402


def set_info(path, items):
    """The WAV's LIST INFO chunk replaced by these entries."""
    data = open(path, "rb").read()
    pos, chunks = 12, []
    while pos + 8 <= len(data):
        cid, size = data[pos:pos + 4], struct.unpack("<I", data[pos + 4:pos + 8])[0]
        end = pos + 8 + size + (size & 1)
        if not (cid == b"LIST" and data[pos + 8:pos + 12] == b"INFO"):
            chunks.append(data[pos:end])
        pos = end
    body = b"INFO"
    for k, v in items:
        d = v.encode("latin-1") + b"\0"
        body += k.encode() + struct.pack("<I", len(d)) + d + (b"\0" if len(d) % 2 else b"")
    chunks.insert(1, b"LIST" + struct.pack("<I", len(body)) + body)
    out = b"".join(chunks)
    open(path, "wb").write(b"RIFF" + struct.pack("<I", len(out) + 4) + b"WAVE" + out)


def info_only(files):
    for i, f in enumerate(files):
        set_info(str(f), [("IART", "Pink Floyd"), ("INAM", f"Track {i + 1}"), ("ICMT", "ripped with EAC"),
                          ("IGNR", "Pop"), ("ICOP", "(c) X")])


class RiffInfoRead(unittest.TestCase):
    def test_info_values_carried_into_id3(self):
        files = c.album(".wav", 2)
        info_only(files)
        p, res = c.run(files)
        self.assertEqual(res.get("counts"), {"done": 1}, c.detail(p, res))
        t = mutagen.File(str(files[0])).tags
        got = {k: str(t[k]) for k in ("COMM::eng", "TCON", "TCOP") if k in t}
        self.assertEqual(got, {"COMM::eng": "ripped with EAC", "TCON": "Pop", "TCOP": "(c) X"},
                         "Picard writes the file's INFO comment/genre/copyright into ID3")

    def test_naming_sees_info_genre(self):
        files = c.album(".wav", 2)
        info_only(files)
        p, res, dest = c.move_run(files, "%albumartist%/%album%/$num(%tracknumber%,2) %title% [%genre%]")
        self.assertEqual(res.get("counts"), {"done": 1}, c.detail(p, res))
        self.assertEqual(c.names(dest), [f"Pink Floyd/The Dark Side of the Moon/0{i} Track {i} [Pop].wav"
                                         for i in (1, 2)])


if __name__ == "__main__":
    unittest.main()
