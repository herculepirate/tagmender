# Twenty-ninth independent review (A): a tag_albums.py / web_tag.py run over an album with a
# symlinked track can no longer be undone.
#
# The run writes its tags in place with mutagen (tag_albums.write_tagmender / write_albumid,
# web_tag.write_tags, Picard itself), which writes through the link into the file it points
# to. tag_albums.undo (also web_tag's and enrich's undo) now restores every track through
# executor.staged_write, which refuses a symbolic link ("TagMender does not write through
# links"): the undo prints "could not restore ...", exits 1, and the file keeps the run's tags
# for good (--force does not help). The previous version replaced the link with a copy holding
# the old tags (the link target kept the run's tags) - wrong too, so this is not a regression
# in the outcome; but neither version can put the track back.
# Shown with `tag_albums.py --mark-unresolved --apply` and `--undo`.
# Fix: either the writers refuse links the same way (write_tagmender, write_albumid,
# web_tag.write_tags, and albums with links are not handed to Picard), or undo restores the
# file the run wrote: staged_write(os.path.realpath(cur)) keeping the link, after checking the
# target is the file the backup describes.
import argparse
import json
import shutil
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _review29a_common as c  # noqa: E402
import mutagen  # noqa: E402
import tag_albums as ta  # noqa: E402


class TagAlbumsUndoSymlink(unittest.TestCase):
    def test_mark_unresolved_on_a_symlinked_track_is_undone(self):
        tmp = Path(tempfile.mkdtemp(prefix="tm29A-ta-"))
        self.addCleanup(shutil.rmtree, tmp, True)
        album = tmp / "lib" / "Band" / "2020 - Album"
        t1 = c.encode(album / "01 One.flac", 440)
        real = c.encode(tmp / "originals" / "02 Two.flac", 550)
        for f, title in ((t1, "One"), (real, "Two")):
            m = mutagen.File(f)
            m["ARTIST"], m["TITLE"] = ["Band"], [title]
            m.save()
        (album / "02 Two.flac").symlink_to(real)
        before = c.snapshot(tmp)
        db = tmp / "matches.db"
        con = sqlite3.connect(db)
        con.execute("CREATE TABLE results (path TEXT, result TEXT)")
        con.execute("INSERT INTO results VALUES (?, ?)", (str(album), json.dumps({"status": "nomatch",
                                                                                  "why": ["nothing found"]})))
        con.commit()
        con.close()
        out = tmp / "out"
        out.mkdir()
        ta.mark_unresolved(argparse.Namespace(matches_db=str(db), only=None, apply=True), out)
        self.assertEqual(mutagen.File(real).get("TAGMENDER"), ["Unresolved"], "(setup: the run wrote the link's file)")
        run = sorted(p.name for p in out.glob("run-*"))[-1][len("run-"):]
        rc = ta.undo(out, run)
        after = {k: v for k, v in c.snapshot(tmp).items() if not k.startswith("out") and k != "matches.db"}
        self.assertEqual(after, {k: v for k, v in before.items()}, "the undo did not put the album back")
        self.assertEqual(rc, 0)


if __name__ == "__main__":
    unittest.main()
