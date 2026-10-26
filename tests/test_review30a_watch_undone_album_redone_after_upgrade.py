# Thirtieth independent review (A): after upgrading to this version, the watcher re-does
# albums the user undid with the previous version - when a track has a user tag spelled like a
# field it keeps elsewhere (ffmpeg's TXXX:TOTALTRACKS beside TRCK "1/2", the case the
# split_custom change is about).
#
# An undo records the album's "shape" (executor._watcher_forgets -> watcher.shape, a hash of
# every track's tagio.read) with status "undone"; watcher.due leaves the album alone while the
# shape is the same ("a job undone by hand is not done again by itself"). tagio.read now reads
# such a tag as "~custom:TOTALTRACKS" instead of merging it into totaltracks, so the shape of
# the very same untouched files changes: due() says "changed since the undo" and the watcher
# identifies and tags (or moves into the library) the album the user had put back. The
# fingerprints of jobs got a split_custom=False reading for exactly this (executor._sig_is),
# the watcher's shape did not.
# Regression: passes with TM_REPO=prev.
# Fix: accept the shape as the previous version read it too - compare against
# shape(..., split_custom=False) (tagio.read(p, split_custom=False)) as _sig_is does, or store a
# version-independent shape (file sizes and the raw tag bytes' hash).
import os
import sqlite3
import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _review30a_common as c  # noqa: E402
import plan as pl  # noqa: E402
import profiles as pf  # noqa: E402
import watcher  # noqa: E402
from mutagen.id3 import ID3, TALB, TIT2, TPE1, TRCK, TXXX  # noqa: E402

PREV_SHAPE = """
import sys, os
sys.path.insert(0, sys.argv[1])
import tempfile
os.environ["XDG_CONFIG_HOME"] = tempfile.mkdtemp(); os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
from pathlib import Path
import watcher
folder = Path(sys.argv[2])
files = sorted(folder.glob("*.mp3"))
print(watcher.shape(folder, files))
"""


class WatchUndoneAfterUpgrade(unittest.TestCase):
    def test_album_undone_by_the_previous_version_stays_undone(self):
        tmp = c.fresh_state("tm30A-watch-")
        inbox = tmp / "inbox"
        folder = inbox / "Band - Album"
        files = []
        for n in (1, 2):
            f = c.encode(folder / f"0{n} Song.mp3", 400 + n * 40)
            t = ID3()
            t.add(TIT2(encoding=3, text=[f"Song {n}"]))
            t.add(TPE1(encoding=3, text=["Band"]))
            t.add(TALB(encoding=3, text=["Album"]))
            t.add(TRCK(encoding=3, text=[f"{n}/2"]))
            t.add(TXXX(encoding=3, desc="TOTALTRACKS", text=["2"]))      # (as ffmpeg leaves it)
            t.save(str(f))
            files.append(f)
        old = time.time() - 3600
        for p in [inbox, folder, *files]:
            os.utime(p, (old, old))
        # the undo, done with the previous version, remembered the album by this shape
        # (as the previous version read tags: a user tag spelled like a field merged into it)
        prev_shape = watcher.shape(folder, files, split_custom=False)
        db = sqlite3.connect(":memory:")
        db.execute("CREATE TABLE processed (folder TEXT PRIMARY KEY, profile TEXT, signature TEXT,"
                   " status TEXT, attempts INTEGER, next_try REAL, ts REAL, detail TEXT)")
        key = watcher.album_key(folder, files)
        db.execute("INSERT INTO processed VALUES (?,?,?,?,?,?,?,?)",
                   (key, "Inbox", prev_shape, "undone", 0, 0, time.time(), ""))
        prof = pf.from_dict({"name": "Inbox", "operation": "move", "destination": str(tmp / "library"),
                             "sources": [{"path": str(inbox), "watch": True, "recursive": True}],
                             "unattended": True, "watch": {"settle_minutes": 1}})
        go, why = watcher.Watcher([prof], once=True).due(folder, prof, db, key, files=files)
        self.assertEqual((go, why), (False, "undone"),
                         "the watcher would do again the job the user undid (no file changed since)")


if __name__ == "__main__":
    unittest.main()
