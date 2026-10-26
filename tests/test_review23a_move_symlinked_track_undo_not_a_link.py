# Twenty-third independent review (A): an album with a track that is a symbolic link (a library
# assembled with links into a download folder, a "best of" folder of links). A move profile
# moves it - the link is replaced by a copy of what it points to - and undo then puts back a
# plain file where the user had a link: the job is not undone exactly. (A tag-in-place job
# refuses a linked track instead: "refusing to write through a symbolic link".)
import os
import shutil
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _review23a_common import Case, changed, executor, snapshot  # noqa: E402


class MoveSymlinkedTrack(Case):
    def test_undo_puts_the_link_back(self):
        album = self.lib / "Artist A" / "2001 - First"
        files = self.album(album, "First", "Artist A", n=3)
        real = self.root / "downloads" / "03 Track 3.flac"
        real.parent.mkdir()
        shutil.move(files[2], real)
        os.symlink(real, files[2])                       # the user's link into the download folder
        before = snapshot(self.root)
        p = self.plan(self.profile("move"))
        self.run_plan(p)
        self.assertEqual(snapshot(self.root / "downloads"), {k[len("downloads/"):]: v for k, v in before.items()
                                                             if k.startswith("downloads/")})
        out = executor.undo(p["job"])
        self.assertFalse([x for x in out if x.startswith("PROBLEM")], out)
        after = snapshot(self.root)
        self.assertTrue(os.path.islink(files[2]), f"{files[2].name} is no longer a symbolic link after undo")
        self.assertEqual(changed(before, after), [])


if __name__ == "__main__":
    unittest.main()
