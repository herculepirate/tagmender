# Twentieth independent review (A): this test failed before its fix.
# Twentieth independent review (A). New with today's guards: the previous version had already
# broken the file in the run itself (review 19); with the run now refusing it, the damage moves
# to the undo.
# enrich.py, tag_albums.py --mark-unresolved (and web_tag.py) back up EVERY file of the album
# into the run's journal before writing, then write file by file; the fragmented track is now
# refused (or the run stops there), but its record stays in the journal. `--undo <run>`
# (tag_albums.undo) then calls restore_tags on every recorded file, unconditionally - with no
# fragmented check (only _put_moov, for b589302's records, has one) and without first looking
# whether the file's tags already are as recorded. restore_tags rebuilds the M4A's tags through
# mutagen (f.tags.clear(); ...; f.save(padding=...)), which comes back 8 bytes larger (the
# documented "empty free atom"): the moov grows, mutagen moves only the first fragment's
# offset, and the file - never written by the run - no longer plays after the undo. The undo
# reports it "restored".
# Fix: in tag_albums.undo (or restore_tags) skip a file whose tags equal its record
# (executor._same_backup's comparison), and refuse fragmented files in restore_tags as the
# writers do; better, do not record files the run will not write.
import argparse
import io
import json
import sqlite3
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _review20a_common import FRAGS, Base, decoded, encode, ta  # noqa: E402


class RunUndoBreaksFragmented(Base):
    def make_album(self, name):
        d = self.tmp / "music" / "Artist" / name
        good = encode(d / "01 a.m4a", 440, "-c:a", "aac")
        frag = encode(d / "02 b.m4a", 550, *FRAGS, duration=3)
        return d, good, frag

    def test_enrich_undo(self):
        import enrich
        d, good, frag = self.make_album("2001 - A")
        played = decoded(frag)
        out = self.tmp / "out"
        db = self.tmp / "cache.db"
        sqlite3.connect(db).close()
        with redirect_stdout(io.StringIO()), mock.patch("match_albums._discogs_token", return_value=""), \
                mock.patch.object(enrich, "navidrome_rescan", return_value=False):
            enrich.main([str(self.tmp / "music"), "--replaygain", "--apply", "--out-dir", str(out),
                         "--cache-db", str(db)])
        self.assertEqual(decoded(frag), played, "(the run itself broke it)")
        run = next(out.glob("run-*")).name[len("run-"):]
        buf = io.StringIO()
        with redirect_stdout(buf):
            enrich.main(["--undo", run, "--out-dir", str(out)])
        self.assertEqual(decoded(frag), played, f"the undo broke a file the run never wrote: {buf.getvalue()}")

    def test_mark_unresolved_undo(self):
        d, good, frag = self.make_album("2002 - B")
        played = decoded(frag)
        db = self.tmp / "matches.db"
        con = sqlite3.connect(db)
        con.execute("CREATE TABLE results (path TEXT PRIMARY KEY, result TEXT)")
        con.execute("INSERT INTO results VALUES (?, ?)", (str(d), json.dumps({"status": "nomatch", "why": ["x"]})))
        con.commit()
        con.close()
        out = self.tmp / "out2"
        out.mkdir()
        with redirect_stdout(io.StringIO()):
            try:
                ta.mark_unresolved(argparse.Namespace(matches_db=str(db), only=None, apply=True), out)
            except ValueError:
                pass                                       # (see test_review20a_mark_unresolved_...)
        self.assertEqual(decoded(frag), played, "(the run itself broke it)")
        run = next(out.glob("run-*")).name[len("run-"):]
        buf = io.StringIO()
        with redirect_stdout(buf):
            ta.undo(out, run)
        self.assertEqual(decoded(frag), played, f"the undo broke a file the run never wrote: {buf.getvalue()}")


if __name__ == "__main__":
    unittest.main()
