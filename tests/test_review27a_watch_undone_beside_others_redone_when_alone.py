# Twenty-seventh independent review (A): the last round's fix of watcher.due covers an undone
# album remembered by its folder that later gets other music beside it (key "folder" ->
# "folder#files": the fallback looks up the plain folder row). The opposite direction is not
# covered. An album undone while OTHER music lay loose beside it in the watched folder is
# remembered by "<folder>#<hash of its files>" (executor._watcher_forgets ->
# watcher.album_key: the folder holds others). When that other music goes - the user deletes
# or files it by hand, or the watcher itself files it on a later retry - the undone album is
# alone again, Watcher.key gives the plain folder, there is no row for it, due() says "new",
# and the watcher identifies the album and does the job the user undid all over again: the
# tracks the user put back are retagged and moved into the library, unasked, although none
# of them changed.
# Not a regression in effect (the previous version re-did it too); the fallback added in this
# round handles only the folder -> folder#files direction.
# Fix: in due(), when no row is found for the key, look for an "undone" row under the other
# form of the key as well (folder#files of these files when the key is the folder) and match
# the shape; or have _watcher_forgets record the undone album under both keys.
import shutil
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _review27a_common  # noqa: E402,F401  (REPO on sys.path)
import test_watch_folders as tw  # noqa: E402  (sets XDG_* to temp folders before anything is imported)
import executor  # noqa: E402
import watcher  # noqa: E402


class UndoneBesideOthersRedoneWhenAlone(tw.DropBase):
    def loose(self, artist, title, prefix):
        d = self.drop(artist, title)
        out = []
        for f in sorted(d.iterdir()):
            out.append(self.inbox / f"{prefix} {f.name}")
            shutil.move(str(f), str(out[-1]))
        d.rmdir()
        tw.settle(self.inbox)
        return out

    def test_undone_album_stays_undone_when_the_music_beside_it_goes(self):
        prof = self.profile()
        mine = self.loose("Band X", "Album X", "X")
        other = self.loose("Band Z", "Album Z", "Z")     # no release for it: unresolved, stays in the inbox
        res = watcher.Watcher([prof], once=True).tick()
        self.assertEqual(len([p for p in self.audio_in(self.dest) if "Band X" in str(p)]), 3, res)
        self.assertTrue(all(p.exists() for p in other), "Band Z was filed too (test setup)")
        job = next(j for j, *_r in executor.jobs(10)
                   if "X 01" in (executor.job_dir(j) / "plan.json").read_text(encoding="utf-8"))
        executor.undo(job)                               # the user puts Album X back
        before = {p.name: p.read_bytes() for p in mine}
        self.assertEqual([p for p in self.audio_in(self.dest) if "Band X" in str(p)], [])
        tw.settle(self.inbox)
        watcher.Watcher([prof], once=True).tick()
        self.assertEqual({p.name: p.read_bytes() for p in mine}, before, "undone album redone at once (test setup)")
        for p in other:                                  # Album Z goes (deleted, or filed by hand)
            p.unlink()
        tw.settle(self.inbox)
        res = watcher.Watcher([prof], once=True).tick()
        back = sorted(str(p.relative_to(self.dest)) for p in self.audio_in(self.dest) if "Band X" in str(p))
        self.assertEqual(back, [], f"the album the user undid was filed again by the watcher: {res}")
        self.assertEqual({p.name: p.read_bytes() for p in mine if p.exists()}, before)


if __name__ == "__main__":
    unittest.main()
