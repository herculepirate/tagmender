# 0.8.11: Watched folders - the lists' heights are dragged with a visible handle and kept for the
# next start; Check Now is a button under the folders (the menus' command too), not only a menu item.
import sys
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_engine import Base  # noqa: E402  (isolated settings and state)

try:
    from PySide6.QtWidgets import QApplication
except ImportError:                          # pragma: no cover
    QApplication = None


def settle(app, ms=50):
    end = time.time() + ms / 1000
    while time.time() < end:
        app.processEvents()
        time.sleep(0.01)


@unittest.skipIf(QApplication is None, "PySide6 not installed")
class WatchedPanes(Base):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def page(self):
        import gui
        win = gui.Main()
        win.resize(1100, 800)
        win.show()
        win.go("Watched folders")
        settle(self.app)
        return win, win.pages["Watched folders"]

    def test_the_heights_dragged_are_kept_for_the_next_start(self):
        win, w = self.page()
        try:
            sizes = w.split.sizes()
            want = [sum(sizes) - 2 * 120, 120, 120]
            w.split.setSizes(want)
            w.split.splitterMoved.emit(0, 1)                       # (as a drag ends)
            got = w.split.sizes()
        finally:
            win.close()
            win.deleteLater()
            settle(self.app)
        win, w = self.page()
        try:
            self.assertEqual(w.split.sizes(), got)
            self.assertEqual(w.split.handle(1).cursor().shape(), __import__("PySide6.QtCore").QtCore.Qt.SplitVCursor)
        finally:
            win.close()
            win.deleteLater()
            settle(self.app)

    def test_check_now_is_a_button(self):
        win, w = self.page()
        try:
            self.assertTrue(w.check_btn.isVisible())
            with mock.patch.object(type(w), "check_now") as now:
                w.check_btn.click()
            now.assert_called_once()
        finally:
            win.close()
            win.deleteLater()
            settle(self.app)


if __name__ == "__main__":
    unittest.main()
