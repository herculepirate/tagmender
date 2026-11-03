# Twenty-sixth independent review (A): a partly copied album of loose tracks in a watched folder.
# An album's "still changing" test (watcher.snapshot) looks at the album's own files - and, for
# an album folder, at the folder and everything in it. For loose tracks at the top of a watched
# folder (deep=False) it looks at their own times only. A copy that keeps the files' times
# (cp -a, cp -p, file managers that keep times; rsync is caught by its temporary file) gives every finished track its
# old time; the track being copied now is a file whose tags cannot be read yet (just created, or
# its large picture block not written yet), so the engine groups it on its own ("untagged
# leftovers: each on its own") - and the finished tracks, judged by their old times, are
# "settled": the watcher identifies an album that is still being copied (2 of 3 tracks here) and
# acts on it - here it writes TagMender's Unresolved mark ("incomplete_album ... track count
# differs") into the finished tracks while the copy runs (with unresolved.action "move" they are
# moved into the holding folder; when the partial album matches, it is retagged and filed, and the
# last track arrives later on its own).
# An album FOLDER copied the same way waits (control): its folder's time and the new file count.
# Not a regression (watcher.py is the same in the previous version).
import os
import shutil
import sys
import time
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "tests"))
import test_watch_folders as tw  # noqa: E402  (sets XDG_* to temp folders before anything is imported)
import watcher  # noqa: E402


class LooseTracksMidCopy(tw.DropBase):
    def mid_copy(self, into):
        """Tracks 1-2 copied with their old times; track 3 just created by the copy (empty)."""
        d = self.drop("Band X", "Album X")
        src = self.tmp / "elsewhere"
        shutil.move(str(d), str(src))
        into.mkdir(parents=True, exist_ok=True)
        done = []
        for f in sorted(src.iterdir())[:2]:
            done.append(into / f.name)
            shutil.copy2(f, done[-1])                     # (cp -a: the file's own old time)
            old = time.time() - 3600
            os.utime(done[-1], (old, old))
        (into / sorted(src.iterdir())[2].name).write_bytes(b"")     # being copied now
        return done

    def check(self, into):
        prof = self.profile()
        done = self.mid_copy(into)
        before = {p: p.read_bytes() for p in done}
        res = watcher.Watcher([prof], once=True).tick()
        self.assertEqual(self.audio_in(self.dest), [], f"an album still being copied was filed: {res}")
        self.assertEqual({p: p.read_bytes() for p in done if p.exists()}, before,
                         f"tracks of an album still being copied were changed: {res}")

    def test_control_album_folder_mid_copy_waits(self):           # (passes)
        self.check(self.inbox / "Band X - Album X")

    def test_loose_tracks_mid_copy_wait(self):
        self.check(self.inbox)


if __name__ == "__main__":
    unittest.main()
