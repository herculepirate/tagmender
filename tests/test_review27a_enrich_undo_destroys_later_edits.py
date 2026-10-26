# Twenty-seventh independent review (A): `enrich.py --undo <run>` throws away what the user
# changed after the run, without a word.
#  * The .lrc files the run created (--lyrics --lrc) are deleted unconditionally
#    (enrich.main: `Path(created).unlink(missing_ok=True)` for every journalled path) - also
#    one the user has since corrected (timings fixed, lines added). The user's work is lost.
#    TagMender's own undo keeps a file it created that was changed since ("changed after the
#    job; left as it is (tagmender undo --force JOB removes it anyway)", executor._undo).
#  * Each track's tags are put back as backed up (tag_albums.undo -> restore_tags) with no look
#    at whether the file was retagged since: a title, artist or any tag fixed after the
#    enrich run is silently reverted to the pre-run value. TagMender's own undo leaves a file
#    whose tags changed after the job alone and reports it (force=True to overwrite).
# Not a regression (the same in the previous version).
# Fix: journal each created .lrc with its size/mtime (or sha256) and delete it only when
# unchanged; record each track's tags signature after the write (as the executor's
# "tags_sig") and restore only files still holding it, reporting the others (with a --force).
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _review27a_common as c  # noqa: E402
import enrich as en  # noqa: E402
import mutagen  # noqa: E402


class Web:
    def get_json(self, url, host):
        return {"syncedLyrics": "[00:01.00] hello", "plainLyrics": "hello"} if "lrclib" in url else {}


class EnrichUndoLaterEdits(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="tm27A-enu-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.album = self.tmp / "lib" / "Band" / "2020 - Album"
        self.track = c.encode(self.album / "01 Song.flac", 440, 3)
        f = mutagen.File(self.track)
        f["ARTIST"], f["TITLE"], f["ALBUM"], f["GENRE"] = ["Band"], ["Song"], ["Album"], ["Death Metal"]
        f.save()
        saved, en.CONFIG = en.CONFIG, self.tmp / "config"
        self.addCleanup(setattr, en, "CONFIG", saved)
        orig = en.ma.Http
        en.ma.Http = lambda db, cfg, tok: Web()
        try:
            en.main([str(self.tmp / "lib"), "--lyrics", "--lrc", "--apply", "--out-dir", str(self.tmp / "out"),
                     "--cache-db", str(self.tmp / "c.db")])
        finally:
            en.ma.Http = orig
        self.lrc = self.album / "01 Song.lrc"
        self.assertTrue(self.lrc.exists())
        self.run_id = sorted(p.name for p in (self.tmp / "out").glob("run-enrich-*"))[-1][len("run-"):]

    def undo(self):
        en.main(["--undo", self.run_id, "--out-dir", str(self.tmp / "out")])

    def test_lrc_the_user_corrected_since_is_not_deleted(self):
        mine = "[00:00.50] hello (timing fixed by hand)\n[00:02.00] and a line the site missed\n"
        self.lrc.write_text(mine, encoding="utf-8")
        self.undo()
        self.assertTrue(self.lrc.exists(), "undo deleted the .lrc file the user had edited since the run")
        self.assertEqual(self.lrc.read_text(encoding="utf-8"), mine)

    def test_tags_retagged_since_are_not_silently_reverted(self):
        f = mutagen.File(self.track)
        f["TITLE"] = ["Song (Remastered)"]                 # the user fixes the title in a tagger
        f.save()
        self.undo()
        self.assertEqual(mutagen.File(self.track).get("TITLE"), ["Song (Remastered)"],
                         "undo of the enrich run reverted a tag the user changed after it")


if __name__ == "__main__":
    unittest.main()
