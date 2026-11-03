# Twenty-ninth independent review (A): an enrich run stopped while it writes a .lrc file
# (Ctrl+C, a closed terminal, a crash, a power cut) leaves a hidden ".01 Song.lrc.<pid>.tmp"
# in the album folder, and `enrich.py --undo` does not remove it - it stays in the library
# for good (a move job later leaves the source folder behind because of it).
#
# The .lrc files are now written through a temp file beside them (enrich.py:456-465) with no
# clean-up: nothing removes the temp file when the run stops between writing it and the
# os.replace, and the journal does not name it, so undo cannot.
# A watched folder holding it is never filed again: watcher.snapshot() takes the ".tmp" for a
# download in progress ("partial download inside").
# Regression in effect: passes with TM_REPO=prev (it wrote the .lrc directly; undo removes it
# by its checksum or reports it).
# Fix: unlink the temp file in a try/finally around write+replace (Ctrl+C, errors); for a
# kill, name it so undo can find it (e.g. f".{c.name}.{run_id}.tmp") and have undo remove it
# beside each "created" path.
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _review29a_common as c  # noqa: E402
import enrich as en  # noqa: E402
import mutagen  # noqa: E402


class Web:
    def get_json(self, url, host):
        return {"syncedLyrics": "[00:01.00] hello", "plainLyrics": "hello"} if "lrclib" in url else {}


class EnrichLrcTemp(unittest.TestCase):
    def test_undo_after_ctrl_c_during_lrc_write_leaves_nothing(self):
        tmp = Path(tempfile.mkdtemp(prefix="tm29A-lrctmp-"))
        self.addCleanup(shutil.rmtree, tmp, True)
        album = tmp / "lib" / "Band" / "2020 - Album"
        track = c.encode(album / "01 Song.flac", 440)
        f = mutagen.File(track)
        f["ARTIST"], f["TITLE"], f["ALBUM"] = ["Band"], ["Song"], ["Album"]
        f.save()
        before = c.snapshot(tmp / "lib")
        saved = en.CONFIG
        en.CONFIG = tmp / "config"
        self.addCleanup(setattr, en, "CONFIG", saved)
        orig = en.ma.Http
        en.ma.Http = lambda db, cfg, tok: Web()
        self.addCleanup(setattr, en.ma, "Http", orig)
        real_replace = os.replace

        def ctrl_c(a, b, *k, **kw):              # the user presses Ctrl+C as the .lrc goes in place
            if str(b).endswith(".lrc"):
                raise KeyboardInterrupt
            return real_replace(a, b, *k, **kw)
        os.replace = ctrl_c
        try:
            en.main([str(tmp / "lib"), "--lyrics", "--lrc", "--apply", "--out-dir", str(tmp / "out"),
                     "--cache-db", str(tmp / "c.db")])
        except KeyboardInterrupt:
            pass
        finally:
            os.replace = real_replace
        import watcher
        self.assertFalse(watcher.snapshot(album, None)[2],
                         "the watcher now takes the album for a download in progress, for good")
        run = sorted(p.name for p in (tmp / "out").glob("run-enrich-*"))[-1][len("run-"):]
        en.main(["--undo", run, "--out-dir", str(tmp / "out")])
        after = c.snapshot(tmp / "lib")
        self.assertEqual(sorted(k for k in set(before) | set(after) if before.get(k) != after.get(k)), [],
                         "the album is not as it was after the undo")


if __name__ == "__main__":
    unittest.main()
