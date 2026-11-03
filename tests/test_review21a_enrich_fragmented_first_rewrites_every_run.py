# Twenty-first independent review (A): this test failed before its fix.
# Twenty-first independent review (A). New with today's change (the previous version stopped
# at the fragmented file instead); minor.
# enrich_album decides whether an album still needs ReplayGain (and genres) from its FIRST file
# only: `args.overwrite or not get(files[0], "REPLAYGAIN_ALBUM_GAIN")`. A fragmented MP4 is now
# measured but never written, so when it sorts first ("01 ...m4a") it never gets the tag, and
# every later `enrich.py --replaygain --apply` measures the whole album again and rewrites
# every other file with the same values - a new run, new backups and a new modification time
# for each file on every run ("no rewrites on a repeat run" holds elsewhere). The same for
# --genres (a web lookup per run).
# Fix: decide on the files that can be written (files not in frag), e.g. the first writable one.
import io
import json
import sqlite3
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _review21a_common import FRAGS, Base, decoded, encode  # noqa: E402


class EnrichRepeat(Base):
    def run_enrich(self, root):
        import enrich
        self.n = getattr(self, "n", 0) + 1
        out = self.tmp / f"out{self.n}"
        db = self.tmp / "cache.db"
        sqlite3.connect(db).close()
        buf = io.StringIO()
        with redirect_stdout(buf), mock.patch("match_albums._discogs_token", return_value=""), \
                mock.patch.object(enrich, "navidrome_rescan", return_value=False):
            enrich.main([str(root), "--replaygain", "--apply", "--out-dir", str(out), "--cache-db", str(db)])
        run = next(out.glob("run-*"))
        j = run / "journal.json"
        return buf.getvalue(), (json.loads(j.read_text()) if j.exists() else [])

    def test_second_run_writes_nothing(self):
        import enrich
        d = self.tmp / "music" / "Artist" / "2002 - B"
        frag = encode(d / "01 a.m4a", 550, *FRAGS, duration=3)
        t2 = encode(d / "02 b.m4a", 440, "-c:a", "aac")
        played = decoded(frag)
        self.run_enrich(self.tmp / "music")
        self.assertTrue(enrich.get(t2, "REPLAYGAIN_ALBUM_GAIN"))
        mtime = t2.stat().st_mtime_ns
        log, journal = self.run_enrich(self.tmp / "music")
        self.assertEqual(decoded(frag), played)
        self.assertEqual(journal, [], f"the second run wrote the album again:\n{log}")
        self.assertEqual(t2.stat().st_mtime_ns, mtime, "02 b.m4a rewritten on the second run")


if __name__ == "__main__":
    unittest.main()
