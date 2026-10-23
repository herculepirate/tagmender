# Tests for inbox.py (folder discovery and loose-track grouping).
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

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


if __name__ == "__main__":
    unittest.main()
