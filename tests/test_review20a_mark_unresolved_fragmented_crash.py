# Twentieth independent review (A): this test failed before its fix.
# Twentieth independent review (A). New with today's guard (the previous version wrote into the
# fragmented file instead - review 19 - so it "passes" this test's first check only by breaking
# the file's audio, which the last check catches).
# tag_albums.write_tagmender now raises ValueError for a fragmented MP4 (_not_fragmented), but
# its caller mark_unresolved (tag_albums.py --mark-unresolved --apply) does not catch it:
#  - the run stops with a traceback at the first album holding a fragmented track; every album
#    after it (in path order) is never marked;
#  - the album itself is half-marked (its tracks before the fragmented one carry
#    TAGMENDER=Unresolved, those after it do not);
#  - the next run picks the same album again (the fragmented file never holds the mark) and
#    stops at the same place: the run never gets past it, whatever is done.
# Fix: in mark_unresolved, leave fragmented files out (tagio.fragmented) - or catch the error
# per file, say "a fragmented MP4: not marked" and carry on - and do not count such a file as
# "still to mark" in the todo check.
import argparse
import io
import json
import shutil
import sqlite3
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _review20a_common import FRAGS, Base, decoded, encode, ta  # noqa: E402


class MarkUnresolvedFragmented(Base):
    def test_run_marks_every_album_and_settles(self):
        a1 = self.lib / "Artist" / "2001 - A"
        a2 = self.lib / "Artist" / "2002 - B"
        good1 = encode(a1 / "01 a.m4a", 440, "-c:a", "aac")
        frag = encode(a1 / "02 b.m4a", 550, *FRAGS, duration=3)
        good3 = encode(a1 / "03 c.m4a", 660, "-c:a", "aac")
        other = encode(a2 / "01 x.m4a", 770, "-c:a", "aac")
        played = decoded(frag)
        db = self.tmp / "matches.db"
        con = sqlite3.connect(db)
        con.execute("CREATE TABLE results (path TEXT PRIMARY KEY, result TEXT)")
        for d in (a1, a2):
            con.execute("INSERT INTO results VALUES (?, ?)",
                        (str(d), json.dumps({"status": "nomatch", "why": ["no candidate"]})))
        con.commit()
        con.close()
        out = self.tmp / "out"
        out.mkdir()
        args = argparse.Namespace(matches_db=str(db), only=None, apply=True)
        for run in (1, 2):
            buf = io.StringIO()
            try:
                with redirect_stdout(buf):
                    ta.mark_unresolved(args, out)
            except Exception as exc:
                self.fail(f"run {run}: --mark-unresolved stopped with {type(exc).__name__}: {exc}\n"
                          f"marked: {[ (p.name, ta.read_tagmender(p)[0]) for p in (good1, frag, good3, other)]}")
        self.assertEqual(ta.read_tagmender(good3)[0], "Unresolved", "a track after the fragmented one: not marked")
        self.assertEqual(ta.read_tagmender(other)[0], "Unresolved", "the next album: never marked")
        self.assertEqual(decoded(frag), played, "the fragmented track no longer plays")


if __name__ == "__main__":
    unittest.main()
