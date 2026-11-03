# Twenty-eighth independent review (A): the watcher files again an album the user undid, when
# two albums dropped loose into one watched folder (or two in one folder) are both undone.
# The watcher remembers each album by "<folder>#<its files>" (the folder holds other music):
# filing them leaves a "done" row under that key. Undoing album X while album Y is still in
# the library puts X back alone in the folder, so executor._watcher_forgets records X's
# "undone" under the plain folder key (watcher.album_key: no other music there now). Undoing Y
# then records Y under "<folder>#Y". On the next pass X's key is "<folder>#X" again, and the
# stale "done" row from when it was filed is there: Watcher.due() looks for an "undone" record
# under the other key only `if not row` (this round's fix), so it compares that old row's
# signature with the files as they are now, finds them "changed" and plans X again - with
# "File albums without asking" on, the tracks the user just put back are retagged and moved
# into the library once more, unasked, though nothing about them changed (FIRST_RUN/release
# notes: "an album you undid stays undone").
# Not a regression in effect (the previous version re-files it too); this round's fallback
# only looks when no row exists under the key.
# Fix: in due(), consult the "undone" rows under both keys whenever the row under this key is
# not itself "undone" and is older than them (or have _watcher_forgets write the undone record
# under both the folder key and the folder#files key, replacing the stale "done" row).
import shutil
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _review28a_common  # noqa: E402,F401  (REPO on sys.path)
import test_watch_folders as tw  # noqa: E402  (XDG_* temp folders before anything is imported)
import executor  # noqa: E402
import watcher  # noqa: E402


class BothUndoneOneRefiled(tw.DropBase):
    def loose(self, artist, title, prefix):
        d = self.drop(artist, title)
        out = []
        for f in sorted(d.iterdir()):
            out.append(self.inbox / f"{prefix} {f.name}")
            shutil.move(str(f), str(out[-1]))
        d.rmdir()
        tw.settle(self.inbox)
        return out

    def job_of(self, text):
        return next(j for j, *_r in executor.jobs(10)
                    if text in (executor.job_dir(j) / "plan.json").read_text(encoding="utf-8"))

    def test_two_loose_albums_undone_stay_undone(self):
        prof = self.profile()
        x = self.loose("Band X", "Album X", "X")
        y = self.loose("Band Y", "Album Y", "Y")
        res = watcher.Watcher([prof], once=True).tick()
        self.assertEqual(len(self.audio_in(self.dest)), 6, res)
        executor.undo(self.job_of("X 01"))                # the user puts both back: X first ...
        executor.undo(self.job_of("Y 01"))                # ... then Y
        self.assertEqual(self.audio_in(self.dest), [])
        before = {p.name: p.read_bytes() for p in x + y}
        tw.settle(self.inbox)
        res = watcher.Watcher([prof], once=True).tick()
        refiled = sorted(str(p.relative_to(self.dest)) for p in self.audio_in(self.dest))
        self.assertEqual(refiled, [], f"an album the user undid was filed again by the watcher: {res}")
        self.assertEqual({p.name: p.read_bytes() for p in x + y if p.exists()}, before)


if __name__ == "__main__":
    unittest.main()
