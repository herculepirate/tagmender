# Twenty-sixth independent review (A): an undone job is "not done again by itself" (watcher.due,
# status "undone"; executor._watcher_forgets) - but only while the key the watcher remembers the
# album by stays the same. For loose tracks dropped straight into a watched folder that key is
# the watched folder itself (watcher.album_key / Watcher.key: the folder, when it holds no other
# music). When anything else is later dropped loose into the watched folder - another album's
# tracks, a single - the undone album's group gets "others", its key becomes
# "<folder>#<hash of its files>", no row is found for it, due() says "new", and the watcher
# identifies it and does the very job the user undid again: the album the user put back is
# moved into the library (retagged) once more, unasked, although none of its files changed.
# Not a regression (watcher.py and the undo record are the same in the previous version).
import os
import shutil
import sys
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "tests"))
import test_watch_folders as tw  # noqa: E402  (sets XDG_* to temp folders before anything is imported)
import executor  # noqa: E402
import watcher  # noqa: E402


class UndoneLooseAlbumRedone(tw.DropBase):
    def loose(self, artist, title, prefix):
        d = self.drop(artist, title)
        out = []
        for f in sorted(d.iterdir()):
            out.append(self.inbox / f"{prefix} {f.name}")
            shutil.move(str(f), str(out[-1]))
        d.rmdir()
        tw.settle(self.inbox)
        return out

    def undo_first_drop(self):
        prof = self.profile()
        mine = self.loose("Band X", "Album X", "X")
        before = {p.name: p.read_bytes() for p in mine}
        res = watcher.Watcher([prof], once=True).tick()
        self.assertEqual(list(res.values()), ["done"], res)
        self.assertEqual(len(self.audio_in(self.dest)), 3)
        job = executor.jobs(5)[0][0]
        executor.undo(job)                                # (Undo job, from the app or the CLI)
        self.assertEqual({p.name: p.read_bytes() for p in mine if p.exists()}, before, "undo is not exact")
        self.assertEqual(self.audio_in(self.dest), [])
        tw.settle(self.inbox)
        self.assertEqual(watcher.Watcher([prof], once=True).tick(), {})     # undone: left alone
        return prof, mine, before

    def test_control_undone_album_alone_stays_undone(self):      # (passes)
        prof, mine, before = self.undo_first_drop()
        tw.settle(self.inbox)
        self.assertEqual(watcher.Watcher([prof], once=True).tick(), {})
        self.assertEqual({p.name: p.read_bytes() for p in mine}, before)

    def test_undone_album_not_redone_when_something_else_is_dropped_beside_it(self):
        prof, mine, before = self.undo_first_drop()
        self.loose("Band Y", "Album Y", "Y")             # another album's tracks dropped later
        res = watcher.Watcher([prof], once=True).tick()
        back = sorted(str(p.relative_to(self.dest)) for p in self.audio_in(self.dest) if "Band X" in str(p))
        self.assertEqual(back, [], f"the album the user undid was filed again by the watcher: {res}")
        self.assertEqual({p.name: p.read_bytes() for p in mine if p.exists()}, before)


if __name__ == "__main__":
    unittest.main()
