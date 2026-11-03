# Thirtieth independent review (B). Not a regression (fails with TM_REPO=prev too).
"""FLAC/Ogg/Opus: lyrics kept in UNSYNCEDLYRICS (as Mp3tag and Bandcamp write them) are not read
as the lyrics, as Picard 3.0.2 reads them (picard/formats/vorbis.py _load: "Map UNSYNCEDLYRICS
to LYRICS ... We read both but always write LYRICS", PICARD-3199).

Picard 3.0.2, saving such a file (checked end to end with its own code in the foreign-tag
sweep, r30B_work/sec.py with PRECH=foreign: "only picard: LYRICS=old lyrics" on every Vorbis
track), writes LYRICS=<the lyrics> - so players that read LYRICS (foobar2000, MusicBee, the
Hydrogenaudio mapping) show them afterwards; a tagger script sees them as %lyrics%.
TagMender leaves the file without LYRICS, and %lyrics% is empty in its scripts and naming.

Cause: tagio.read's Vorbis branch has no mapping for UNSYNCEDLYRICS (nor "unsyncedlyrics:desc"),
so it is read as a custom tag "unsyncedlyrics"; the plan therefore never has lyrics to write.

Fix: read UNSYNCEDLYRICS (and UNSYNCEDLYRICS:x) as lyrics in Vorbis, as Picard 3.0.2 does, so
LYRICS is written when the file is saved (and keep UNSYNCEDLYRICS as it is, as Picard does).
"""
import sys
import unittest
from pathlib import Path

import mutagen

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _review30b_common as c  # noqa: E402


class UnsyncedLyrics(unittest.TestCase):
    def _files(self, ext):
        files = c.album(ext, 2)
        for f in files:
            m = mutagen.File(str(f))
            if m.tags is None:
                m.add_tags()
            m.tags["UNSYNCEDLYRICS"] = ["la la la"]
            m.tags["TITLE"] = ["Old"]
            m.save()
        return files

    def test_lyrics_written(self):
        for ext in (".flac", ".opus"):
            files = self._files(ext)
            p, res = c.run(files)
            self.assertEqual(res.get("counts"), {"done": 1}, c.detail(p, res))
            tags = mutagen.File(str(files[0])).tags
            self.assertEqual(list(tags.get("LYRICS") or []), ["la la la"],
                             f"{ext}: Picard 3.0.2 writes the UNSYNCEDLYRICS lyrics as LYRICS")

    def test_script_sees_lyrics(self):
        files = self._files(".flac")
        p, res = c.run(files, scripts=["$set(comment,%lyrics%)"])
        self.assertEqual(res.get("counts"), {"done": 1}, c.detail(p, res))
        tags = mutagen.File(str(files[0])).tags
        self.assertEqual(list(tags.get("COMMENT") or []), ["la la la"], "%lyrics% in a tagger script")


if __name__ == "__main__":
    unittest.main()
