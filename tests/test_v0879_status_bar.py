# 0.8.79: the status bar never draws two texts over each other (owner's screenshot: "Beside the
# look-up: ..." over "1 album ready · 0 need your decision · 4 files").
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import test_gui_shell as _shell   # noqa: E402  (its window set-up; not its tests)


class StatusBarText(unittest.TestCase):
    # (Shell's window set-up only, none of its tests)
    stuck = _shell.Shell.stuck
    setUpClass = classmethod(_shell.Shell.setUpClass.__func__)
    tearDownClass = classmethod(_shell.Shell.tearDownClass.__func__)
    setUp = _shell.Shell.setUp
    tearDown = _shell.Shell.tearDown
    dispose = _shell.Shell.dispose

    def test_a_temporary_message_hides_the_counts_until_it_ends(self):
        ov = self.win.pages["Overview"]
        bar = self.win.statusBar()
        ov.counts_line.setText("1 album ready · 0 need your decision · 4 files")
        self.app.processEvents()
        self.assertTrue(ov.counts_line.isVisibleTo(bar))
        bar.showMessage("Looking up 1 album again")
        self.app.processEvents()
        self.assertFalse(ov.counts_line.isVisibleTo(bar))
        bar.clearMessage()
        self.app.processEvents()
        self.assertTrue(ov.counts_line.isVisibleTo(bar))

    def test_the_counts_stay_off_other_pages_after_a_message(self):
        ov = self.win.pages["Overview"]
        bar = self.win.statusBar()
        self.win.go("History")
        self.app.processEvents()
        bar.showMessage("x", 50)
        bar.clearMessage()
        self.app.processEvents()
        self.assertFalse(ov.counts_line.isVisibleTo(bar))
        bar.showMessage("y")
        self.win.go("Overview")
        self.app.processEvents()
        self.assertFalse(ov.counts_line.isVisibleTo(bar))       # (back on the Overview, a message up)
        bar.clearMessage()
        self.app.processEvents()
        self.assertTrue(ov.counts_line.isVisibleTo(bar))

    def test_apply_beside_a_look_up_says_its_progress_in_the_summary(self):
        import inspect
        from gui import overview
        src = inspect.getsource(overview.Overview.alongside_news)
        self.assertNotIn("showMessage", src)
        self.assertIn("self.summary.setText", src)


if __name__ == "__main__":
    unittest.main()
