# Twentieth independent review (A): this test failed before its fix.
# Twentieth independent review (A). New with today's guard (the previous version wrote the
# ReplayGain tags into the fragmented file - review 19 - breaking its audio; it fails this test
# on that check).
# enrich.put now raises tagio.Unsupported for a fragmented MP4, but enrich_album neither leaves
# such a file out of its plan nor catches the error per file: it backs up every file, then calls
# put() file by file, and the first fragmented track ends the album (main() prints
# "! <album>: skipped (Unsupported: ...)"):
#  - the tracks before it ARE written (ReplayGain, lyrics, genre...), those after it are not,
#    and the message says the album was skipped;
#  - ReplayGain is decided from the album's first track (get(files[0], "REPLAYGAIN_ALBUM_GAIN")):
#    with track 1 written, every later run takes the album as done - the tracks after the
#    fragmented one never get ReplayGain, its album gain applies to a third of the album;
#  - with the fragmented track first, nothing is written ever, every run measures the whole album
#    again and says "skipped".
# Fix: leave fragmented files out of `files` in enrich_album (with a note), as plan.py does; the
# album's gain is still measured over all of them.
import io
import sqlite3
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _review20a_common import FRAGS, Base, decoded, encode  # noqa: E402


class EnrichFragmented(Base):
    def run_enrich(self, root):
        import enrich
        self.n = getattr(self, "n", 0) + 1
        out = self.tmp / f"out{self.n}"             # (two runs in one second: one run folder)
        db = self.tmp / "cache.db"
        sqlite3.connect(db).close()
        buf = io.StringIO()
        with redirect_stdout(buf), mock.patch("match_albums._discogs_token", return_value=""), \
                mock.patch.object(enrich, "navidrome_rescan", return_value=False):
            enrich.main([str(root), "--replaygain", "--apply", "--out-dir", str(out), "--cache-db", str(db)])
        return buf.getvalue()

    def test_every_other_track_gets_replaygain(self):
        import enrich
        d = self.tmp / "music" / "Artist" / "2001 - A"
        t1 = encode(d / "01 a.m4a", 440, "-c:a", "aac")
        frag = encode(d / "02 b.m4a", 550, *FRAGS, duration=3)
        t3 = encode(d / "03 c.m4a", 660, "-c:a", "aac")
        played = decoded(frag)
        log = self.run_enrich(self.tmp / "music") + self.run_enrich(self.tmp / "music")
        got = {p.name: enrich.get(p, "REPLAYGAIN_TRACK_GAIN") for p in (t1, frag, t3)}
        self.assertEqual(decoded(frag), played, "the fragmented track no longer plays")
        self.assertTrue(got["03 c.m4a"], f"track 3 never gets ReplayGain: {got}\n{log}")
        self.assertFalse(got["01 a.m4a"] and "skipped" in log,
                         f"the album is reported skipped, yet track 1 was written: {got}\n{log}")

    def test_fragmented_first_track(self):
        import enrich
        d = self.tmp / "music" / "Artist" / "2002 - B"
        frag = encode(d / "01 a.m4a", 550, *FRAGS, duration=3)
        t2 = encode(d / "02 b.m4a", 440, "-c:a", "aac")
        played = decoded(frag)
        log = self.run_enrich(self.tmp / "music")
        self.assertEqual(decoded(frag), played, "the fragmented track no longer plays")
        self.assertTrue(enrich.get(t2, "REPLAYGAIN_TRACK_GAIN"), f"no track of the album gets ReplayGain:\n{log}")


if __name__ == "__main__":
    unittest.main()
