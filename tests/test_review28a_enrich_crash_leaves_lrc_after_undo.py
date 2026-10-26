# Twenty-eighth independent review (A): after an enrich run is cut short (Ctrl+C, a closed
# terminal, a power cut), `enrich.py --undo <run>` no longer removes the .lrc files the run
# created, and says the user changed them.
# enrich_album writes the album's .lrc files and only then records their checksums
# (entry["created_sha"]) in the journal - with the second write_json_durably, after all of
# them. Stopped between the two, the journal lists the .lrc files as created but has no
# checksum for them, and undo (now: delete only a file whose checksum matches) keeps every one,
# printing "... was changed after the run (or not recorded); left as it is". The user never
# touched them; the undo leaves the run's files in the library and blames the user. The
# previous version deleted them.
# Regression (passes with TM_REPO=prev).
# Fix: record each .lrc's checksum (sha256 of the text to be written) in the journal entry
# before writing the journal the first time - before any file is written - so an undo after
# any crash knows them; write the .lrc files atomically (temp + rename).
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _review28a_common as c  # noqa: E402
import enrich as en  # noqa: E402
import mutagen  # noqa: E402


class Web:
    def get_json(self, url, host):
        return {"syncedLyrics": "[00:01.00] hello", "plainLyrics": "hello"} if "lrclib" in url else {}


class Killed(BaseException):
    pass


class EnrichCrashLrc(unittest.TestCase):
    def test_undo_after_a_cut_short_run_removes_its_lrc(self):
        tmp = Path(tempfile.mkdtemp(prefix="tm28A-lrc-"))
        self.addCleanup(shutil.rmtree, tmp, True)
        album = tmp / "lib" / "Band" / "2020 - Album"
        track = c.encode(album / "01 Song.flac", 440, 3)
        f = mutagen.File(track)
        f["ARTIST"], f["TITLE"], f["ALBUM"] = ["Band"], ["Song"], ["Album"]
        f.save()
        saved = en.CONFIG
        en.CONFIG = tmp / "config"
        self.addCleanup(setattr, en, "CONFIG", saved)
        import executor
        real, calls = getattr(executor, "write_json_durably", None), []

        def cut_short(path, data):                        # killed before the second journal write
            calls.append(path)
            if len(calls) == 2:
                raise Killed()
            return real(path, data)
        orig = en.ma.Http
        en.ma.Http = lambda db, cfg, tok: Web()
        if real is not None:
            executor.write_json_durably = cut_short
        try:
            en.main([str(tmp / "lib"), "--lyrics", "--lrc", "--apply", "--out-dir", str(tmp / "out"),
                     "--cache-db", str(tmp / "c.db")])
        except Killed:
            pass
        finally:
            en.ma.Http = orig
            if real is not None:
                executor.write_json_durably = real
        lrc = album / "01 Song.lrc"
        self.assertTrue(lrc.exists(), "(test setup: the run wrote the .lrc)")
        run = sorted(p.name for p in (tmp / "out").glob("run-enrich-*"))[-1][len("run-"):]
        en.main(["--undo", run, "--out-dir", str(tmp / "out")])
        self.assertFalse(lrc.exists(), "undo left the .lrc file the cut-short run created")


if __name__ == "__main__":
    unittest.main()
