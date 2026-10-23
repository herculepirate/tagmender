# Tests for inbox.py (folder discovery and loose-track grouping).
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

# TagMender settings and state of the tests never touch the real ones
import os as _os, tempfile as _tempfile  # noqa: E402
_os.environ["XDG_CONFIG_HOME"] = _tempfile.mkdtemp(prefix="tm-config-")
_os.environ["XDG_STATE_HOME"] = _tempfile.mkdtemp(prefix="tm-state-")
HERE = Path(__file__).resolve().parent.parent   # the scripts sit above tests/
sys.path.insert(0, str(HERE))
import inbox  # noqa: E402


def audio(p, secs=1, **tags):
    p.parent.mkdir(parents=True, exist_ok=True)
    meta = sum((["-metadata", f"{k}={v}"] for k, v in tags.items()), [])
    subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", f"sine=frequency=440:duration={secs}",
                    *meta, str(p), "-y"], check=True)


class Inbox(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_album_folders_with_disc_subfolders(self):
        audio(self.tmp / "Band - Album" / "CD1" / "01.flac")
        audio(self.tmp / "Band - Album" / "CD2" / "01.flac")
        audio(self.tmp / "Other" / "2001 - Thing" / "01.flac")
        audio(self.tmp / "_unresolved" / "Old" / "01.flac")
        self.assertEqual([p.relative_to(self.tmp).as_posix() for p in inbox.album_folders(self.tmp)],
                         ["Band - Album", "Other/2001 - Thing"])

    def test_waits_until_a_copy_has_finished(self):
        audio(self.tmp / "A" / "01.flac")
        self.assertFalse(inbox.settled(self.tmp / "A", 1))
        old = time.time() - 600
        os.utime(self.tmp / "A" / "01.flac", (old, old))
        self.assertTrue(inbox.settled(self.tmp / "A", 1))

    def test_loose_tracks_grouped_by_their_album_tags(self):
        audio(self.tmp / "a.mp3", artist="Yob", album="Atma")
        audio(self.tmp / "b.mp3", artist="Yob", album="Atma")
        audio(self.tmp / "c.mp3", artist="Sleep", album="Dopesmoker")
        audio(self.tmp / "d.mp3")                                     # nothing says which album
        made = inbox.group_loose(self.tmp, http=None, db=None)
        self.assertEqual(sorted(p.name for p in made), ["Sleep - Dopesmoker", "Yob - Atma"])
        self.assertEqual(sorted(p.name for p in (self.tmp / "Yob - Atma").iterdir()), ["a.mp3", "b.mp3"])
        self.assertTrue((self.tmp / "d.mp3").exists())


class WatchedRules(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        import exclusions
        self.ex = exclusions
        self.saved = (exclusions.CONFIG_DIR, exclusions.STATE_DIR)
        exclusions.CONFIG_DIR, exclusions.STATE_DIR = self.tmp / "config", self.tmp / "state"

    def tearDown(self):
        self.ex.CONFIG_DIR, self.ex.STATE_DIR = self.saved
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_once(self, calls):
        def fake_handle(folder, args, http, cfg, dm, db, log):
            calls.append(Path(folder).name)
            dec = self.ex.gate(folder, "watch")             # what ma.process does first
            return ("ignored" if dec.summary().action == "ignore" else "skipped") if dec.all_excluded else "tagged"
        saved = (inbox.handle, inbox.ma.load_plugin, inbox.ma.Http)
        inbox.handle, inbox.ma.load_plugin, inbox.ma.Http = fake_handle, (lambda d: None), (lambda *a: None)
        os.environ["DISCOGS_TOKEN"] = "x"
        try:
            inbox.main(["--inbox", str(self.tmp / "in"), "--library", str(self.tmp / "lib"), "--once",
                        "--out-dir", str(self.tmp / "out"), "--settle-minutes", "0"])
        finally:
            inbox.handle, inbox.ma.load_plugin, inbox.ma.Http = saved

    def test_ignored_album_is_not_queued_again_until_something_changes(self):
        d = self.tmp / "in" / "Colors"
        audio(d / "01.flac", album="Colors [No Vox]")
        old = time.time() - 600
        os.utime(d / "01.flac", (old, old))
        calls = []
        self.run_once(calls)
        self.run_once(calls)
        self.assertEqual(calls, ["Colors"])                  # looked at once, then left alone
        self.ex.set_enabled("no-vox", False)                 # the rule is switched off: look again
        self.run_once(calls)
        self.assertEqual(calls, ["Colors", "Colors"])


if __name__ == "__main__":
    unittest.main()
