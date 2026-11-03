# Fourteenth independent review (Picard code): this test failed before its fix.
"""tagio.write() on a WAV file with an ID3v2.3 tag: the RIFF INFO chunk gets the values as
the ID3v2.3 tag stores them (the planner hands write() tagio.stored(): several values joined
with "/", a date without its day cut to the year), not as Picard writes INFO
(picard/formats/wav.py: the metadata values joined with ", ", the date as it is).
Picard 3.0.2 with write_id3v23 on writes IGNR "Rock, Pop" and ICRD "2001-05" here
(checked by running Picard's WAVFile._save)."""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _review14b_common import make  # noqa: E402

import tagio  # noqa: E402


def info(p):
    from mutagen._riff import RiffFile
    with open(p, "rb") as fh:
        r = RiffFile(fh)
        for c in r.root.subchunks():
            if c.id == "LIST" and getattr(c, "name", None) == "INFO":
                return {s.id: s.read().rstrip(b"\0").decode("windows-1252") for s in c.subchunks()}
    return {}


class WavV23Info(unittest.TestCase):
    def test_info_as_picard_writes_it(self):
        from mutagen.wave import WAVE
        p = make(Path(tempfile.mkdtemp()) / "a.wav")
        w = WAVE(str(p))
        w.add_tags()
        w.save(v2_version=3)                                 # a WAV Picard tagged with "Write ID3v2.3"
        target = {"title": ["T"], "genre": ["Rock", "Pop"], "date": ["2001-05"]}
        changes = tagio.stored(p, target)                     # (as plan.py hands it over: the RIFF INFO
        tagio.write(p, changes, riff=target)                   # values as they are, beside)
        got = info(p)
        self.assertEqual((got.get("IGNR"), got.get("ICRD")), ("Rock, Pop", "2001-05"), got)


if __name__ == "__main__":
    unittest.main()
