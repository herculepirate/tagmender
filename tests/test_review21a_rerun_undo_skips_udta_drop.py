# Twenty-first independent review (A): this test failed before its fix.
# Twenty-first independent review (A). A regression: passes with TM_REPO=prev.
# restore_tags now returns early when same_as_backup(path, rec) says the file already holds
# its record, and rec_key counts an MP4 with an EMPTY ilst ({"mp4": []}) as one with no tags
# at all - popping "padding", "mp4_udta" and "mp4_meta" from both sides. But putting back an
# M4A that had no udta (record {"tags": None, "mp4_udta": False}) is two writes in place in
# tag_albums.undo: f.delete() (which leaves udta/meta/hdlr/ilst-empty/free behind) and then
# _drop_udta. A `--undo` cut short between the two (power cut, Ctrl-C, a full disk) leaves the
# empty udta; run again - the documented remedy - restore_tags compares, finds "the same", and
# returns before _drop_udta: the udta the job added stays for good, the undo reports the file
# "restored", and the file is not as it was. The previous version ran f.delete() + _drop_udta
# again and gave the file back byte for byte. (The same with "mp4_meta": False, and through
# executor._matches_backup a file whose tags were emptied after the job counts as untouched.)
# Fix: compare the record exactly in restore_tags' early return (keep mp4_udta / mp4_meta /
# padding in rec_key, or do the equality "as none" only for the executor's edited-since verdict).
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
from _review21a_common import Base, decoded, encode, strip_udta, ta  # noqa: E402


class RerunUndo(Base):
    def test_interrupted_undo_run_again(self):
        d = self.tmp / "music" / "Artist" / "2002 - B"
        f = strip_udta(encode(d / "01 a.m4a", 440, "-c:a", "aac"))
        from mutagen.mp4 import MP4
        self.assertIsNone(MP4(f).tags)
        orig, played = f.read_bytes(), decoded(f)
        db = self.tmp / "matches.db"
        con = sqlite3.connect(db)
        con.execute("CREATE TABLE results (path TEXT PRIMARY KEY, result TEXT)")
        con.execute("INSERT INTO results VALUES (?, ?)", (str(d), json.dumps({"status": "nomatch", "why": ["x"]})))
        con.commit()
        con.close()
        out = self.tmp / "out"
        out.mkdir()
        with redirect_stdout(io.StringIO()):
            ta.mark_unresolved(argparse.Namespace(matches_db=str(db), only=None, apply=True), out)
        self.assertNotEqual(f.read_bytes(), orig)
        run = next(out.glob("run-*")).name[len("run-"):]
        # the first --undo stops after f.delete() saved, before the udta is cut out
        with redirect_stdout(io.StringIO()), mock.patch.object(ta, "_drop_udta", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                ta.undo(out, run)
        buf = io.StringIO()
        with redirect_stdout(buf):
            ta.undo(out, run)                                  # run again
        self.assertEqual(decoded(f), played)
        self.assertEqual(len(f.read_bytes()) - len(orig), 0,
                         f"the second undo left the job's udta in: {buf.getvalue()}")
        self.assertEqual(f.read_bytes(), orig, f"not as it was: {buf.getvalue()}")


if __name__ == "__main__":
    unittest.main()
