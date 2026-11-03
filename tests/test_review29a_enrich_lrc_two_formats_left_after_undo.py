# Twenty-ninth independent review (A): `enrich.py --lyrics --lrc --apply` over a folder that
# holds a track in two formats ("01 Song.flac" and "01 Song.mp3" - a lossless and a lossy
# copy side by side) makes "01 Song.lrc" once, and `enrich.py --undo` then leaves it behind:
# "01 Song.lrc was changed after the run (or not recorded); left as it is". The user never
# touched it.
#
# enrich_album lists the .lrc to make for every track whose .lrc is missing - both tracks name
# the same "01 Song.lrc". The new write loop (enrich.py:456-465) makes it for the first, and
# for the second finds it there ("one made meanwhile: never written over") and pops its
# checksum from entry["created_sha"] - the checksum of the file this run just wrote. The
# journal then lists the .lrc as created without a checksum, so undo keeps it.
# Regression: passes with TM_REPO=prev (it wrote the file twice and recorded its checksum).
# Fix: list each .lrc once (dict.fromkeys over the paths), or only pop the checksum when the
# existing file was not made by this run (keep a set of paths written so far).
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _review29a_common as c  # noqa: E402
import enrich as en  # noqa: E402
import mutagen  # noqa: E402
from mutagen.easyid3 import EasyID3  # noqa: E402


class Web:
    def get_json(self, url, host):
        return {"syncedLyrics": "[00:01.00] hello", "plainLyrics": "hello"} if "lrclib" in url else {}


class EnrichLrcTwoFormats(unittest.TestCase):
    def test_undo_removes_the_lrc_the_run_made(self):
        tmp = Path(tempfile.mkdtemp(prefix="tm29A-lrc2-"))
        self.addCleanup(shutil.rmtree, tmp, True)
        album = tmp / "lib" / "Band" / "2020 - Album"
        flac = c.encode(album / "01 Song.flac", 440)
        f = mutagen.File(flac)
        f["ARTIST"], f["TITLE"], f["ALBUM"] = ["Band"], ["Song"], ["Album"]
        f.save()
        mp3 = c.encode(album / "01 Song.mp3", 440)
        e = EasyID3()
        e["artist"], e["title"], e["album"] = ["Band"], ["Song"], ["Album"]
        e.save(str(mp3))
        saved = en.CONFIG
        en.CONFIG = tmp / "config"
        self.addCleanup(setattr, en, "CONFIG", saved)
        orig = en.ma.Http
        en.ma.Http = lambda db, cfg, tok: Web()
        self.addCleanup(setattr, en.ma, "Http", orig)
        en.main([str(tmp / "lib"), "--lyrics", "--lrc", "--apply", "--out-dir", str(tmp / "out"),
                 "--cache-db", str(tmp / "c.db")])
        lrc = album / "01 Song.lrc"
        self.assertTrue(lrc.exists(), "(setup: the run made the .lrc)")
        run = sorted(p.name for p in (tmp / "out").glob("run-enrich-*"))[-1][len("run-"):]
        en.main(["--undo", run, "--out-dir", str(tmp / "out")])
        self.assertFalse(lrc.exists(), "undo left the .lrc file the run made")


if __name__ == "__main__":
    unittest.main()
