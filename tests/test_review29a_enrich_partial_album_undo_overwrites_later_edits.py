# Twenty-ninth independent review (A): an enrich run that stops part-way through an album -
# now whenever one of its tracks is a symbolic link or is locked by a running TagMender job
# (both new refusals of executor.staged_write) - leaves a journal without the "as written"
# signature of the tracks it did write, and `enrich.py --undo` then puts those tracks back
# over whatever was done to them since: a later edit by the user (or a job) is lost, with no
# "edited after the run" warning and without --force.
#
# enrich.enrich_album writes the journal once before writing (backups only), then calls put()
# for each track and records r["after_sig"] - but only writes the journal again after the
# whole album. put() now raises StepError for a symlinked track ("is a symbolic link") and for
# a track a job holds ("is being changed by another TagMender process"); the exception leaves
# enrich_album (main prints "skipped" for the album, though its first tracks were written) and
# the journal on disk keeps no after_sig. tag_albums.undo restores a record without after_sig
# unconditionally.
# Regression: passes with TM_REPO=prev (there staged_write neither refused a link nor took the
# lock, so the album was written whole and every after_sig recorded).
# Fix: in enrich_album write each track inside its own try (a failed track is reported, the
# others go on), record after_sig right after each put and write the journal durably after
# each track (or in a finally); report the album as partly written, not "skipped".
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _review29a_common as c  # noqa: E402
import enrich as en  # noqa: E402
import executor  # noqa: E402
import mutagen  # noqa: E402


class Web:
    def get_json(self, url, host):
        return {"plainLyrics": "la la la"} if "lrclib" in url else {}


class EnrichPartialAlbum(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="tm29A-enrich-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.album = self.tmp / "lib" / "Band" / "2020 - Album"
        saved = en.CONFIG
        en.CONFIG = self.tmp / "config"
        self.addCleanup(setattr, en, "CONFIG", saved)
        orig = en.ma.Http
        en.ma.Http = lambda db, cfg, tok: Web()
        self.addCleanup(setattr, en.ma, "Http", orig)

    def tag(self, f, title):
        m = mutagen.File(f)
        m["ARTIST"], m["TITLE"], m["ALBUM"] = ["Band"], [title], ["Album"]
        m.save()

    def enrich_then_edit_then_undo(self, during=None):
        t1 = c.encode(self.album / "01 One.flac", 440)
        self.tag(t1, "One")
        t2 = self.second
        if during is None:
            en.main([str(self.tmp / "lib"), "--lyrics", "--apply", "--out-dir", str(self.tmp / "out"),
                     "--cache-db", str(self.tmp / "c.db")])
        else:
            with during:
                en.main([str(self.tmp / "lib"), "--lyrics", "--apply", "--out-dir", str(self.tmp / "out"),
                         "--cache-db", str(self.tmp / "c.db")])
        self.assertEqual(mutagen.File(t1).get("LYRICS"), ["la la la"], "(setup: enrich wrote track 1)")
        m = mutagen.File(t1)                     # the user (or a job) changes track 1 afterwards
        m["GENRE"] = ["Edited later"]
        m.save()
        run = sorted(p.name for p in (self.tmp / "out").glob("run-enrich-*"))[-1][len("run-"):]
        en.main(["--undo", run, "--out-dir", str(self.tmp / "out")])
        return t1, t2

    def test_symlinked_track_later_edit_kept(self):
        real = c.encode(self.tmp / "elsewhere" / "02 Two.flac", 550)
        self.tag(real, "Two")
        self.second = self.album / "02 Two.flac"
        self.album.mkdir(parents=True, exist_ok=True)
        self.second.symlink_to(real)
        t1, _t2 = self.enrich_then_edit_then_undo()
        self.assertEqual(mutagen.File(t1).get("GENRE"), ["Edited later"],
                         "enrich --undo (no --force) overwrote a change made to the track after the run")

    def test_track_locked_by_a_job_later_edit_kept(self):
        self.second = c.encode(self.album / "02 Two.flac", 550)
        self.tag(self.second, "Two")
        t1, _t2 = self.enrich_then_edit_then_undo(during=executor.file_lock(self.second))
        self.assertEqual(mutagen.File(t1).get("GENRE"), ["Edited later"],
                         "enrich --undo (no --force) overwrote a change made to the track after the run")


if __name__ == "__main__":
    unittest.main()
