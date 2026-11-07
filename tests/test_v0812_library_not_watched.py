# 0.8.12: a watched folder is where new music is dropped; the library is where it is filed. A
# profile's library (or a folder inside it) is never looked at as a watched folder - looked at,
# every album already filed would be filed again, and a pass over a big library never reached the
# drop folders after it. The Watch a Folder dialog refuses it; one already on the list is skipped.
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_watch_folders import DropBase, settle  # noqa: E402
import watcher  # noqa: E402


class LibraryNotWatched(DropBase):
    def test_the_library_is_skipped_and_the_drop_folder_after_it_is_filed(self):
        filed = self.drop("Band Y", "Album Y", where=self.dest)       # (an album already in the library)
        dropped = self.drop("Band X", "Album X")
        settle(self.dest)
        prof = self.profile(sources=[{"path": str(self.dest), "watch": True, "recursive": True},
                                     {"path": str(self.inbox), "watch": True, "recursive": True}])
        w = watcher.Watcher([prof], once=True)
        res = w.tick()
        self.assertEqual(list(res.values()), ["done"])                 # (the drop, and only it)
        self.assertEqual(list(res), [str(dropped)])
        self.assertTrue(filed.is_dir() and self.audio_in(filed))       # (the library's album untouched)
        self.assertEqual(w.libraries, [str(self.dest)])

    def test_inside_the_library_too_and_not_for_a_profile_that_tags_in_place(self):
        inside = self.dest / "Incoming"
        inside.mkdir()
        prof = self.profile()
        self.assertTrue(watcher.Watcher.is_library(self.dest, prof))
        self.assertTrue(watcher.Watcher.is_library(inside, prof))
        self.assertFalse(watcher.Watcher.is_library(self.inbox, prof))
        here = self.profile(operation="in_place")
        self.assertFalse(watcher.Watcher.is_library(self.dest, here))


try:
    from PySide6.QtWidgets import QApplication
except ImportError:                          # pragma: no cover
    QApplication = None


@unittest.skipIf(QApplication is None, "PySide6 not installed")
class DialogRefuses(DropBase):
    def test_watch_a_folder_refuses_the_library(self):
        app = QApplication.instance() or QApplication([])
        from gui import watched
        self.profile()
        dlg = watched.WatchDialog(None)
        dlg.path.setText(str(self.dest))
        dlg.prof.setCurrentIndex(dlg.prof.findText("Inbox"))
        app.processEvents()
        errs, _notes = dlg.problems()
        self.assertTrue(any("is the profile's library" in e for e in errs), errs)
        dlg.path.setText(str(self.inbox))
        app.processEvents()
        errs, _notes = dlg.problems()
        self.assertFalse(any("library" in e for e in errs), errs)


if __name__ == "__main__":
    unittest.main()
