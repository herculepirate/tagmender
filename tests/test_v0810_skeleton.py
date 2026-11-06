# 0.8.10: a list loading shows its rows' shape (gui/skeleton.Skeleton), a soft pulse that stands
# still with reduced motion, words when it is slow, and words plus Try Again when it fails (offscreen).
import sys
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_engine import Base  # noqa: E402  (isolated settings and state)

try:
    from PySide6.QtCore import QAbstractTableModel, Qt
    from PySide6.QtWidgets import QApplication, QTableView
except ImportError:                          # pragma: no cover
    QApplication = None


def settle(app, ms=50):
    end = time.time() + ms / 1000
    while time.time() < end:
        app.processEvents()
        time.sleep(0.01)


@unittest.skipIf(QApplication is None, "PySide6 not installed")
class Skeletons(Base):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def table(self):
        class Empty(QAbstractTableModel):
            def rowCount(self, _p=None):
                return 0

            def columnCount(self, _p=None):
                return 3

            def data(self, *_a):
                return None
        t = QTableView()
        t._m = Empty()
        t.setModel(t._m)
        t.resize(600, 300)
        t.show()
        settle(self.app)
        return t

    def test_the_bars_cover_the_list_while_it_loads_and_go_when_it_has(self):
        from gui.skeleton import Skeleton
        t = self.table()
        sk = Skeleton.over(t)
        self.assertIs(Skeleton.over(t), sk)                              # (one per list)
        sk.start()
        settle(self.app)
        self.assertTrue(sk.loading())
        self.assertEqual(sk.geometry(), t.viewport().rect())             # (the list's own size: nothing jumps)
        sk.stop()
        self.assertFalse(sk.isVisible())

    def test_reduced_motion_stands_still(self):
        from gui import common
        from gui.skeleton import Skeleton
        t = self.table()
        with mock.patch.object(common, "reduced_motion", return_value=True):
            sk = Skeleton.over(t)
            sk.start()
        self.assertNotEqual(sk._anim.state(), sk._anim.State.Running)
        sk.stop()
        with mock.patch.object(common, "reduced_motion", return_value=False):
            sk.start()
        self.assertEqual(sk._anim.state(), sk._anim.State.Running)
        sk.stop()

    def test_slow_then_long_says_so_and_offers_stop(self):
        from gui import skeleton
        t = self.table()
        stopped = []
        with mock.patch.object(skeleton, "SLOW", 0), mock.patch.object(skeleton, "LONG", 0):
            sk = skeleton.Skeleton.over(t)
            sk.start(cancel=lambda: stopped.append(1))
            settle(self.app, 120)
        self.assertIn("longer than usual", sk.words.text())
        self.assertEqual(sk.button.text(), "Stop")
        sk.button.click()
        self.assertEqual(stopped, [1])
        sk.stop()

    def test_a_failure_says_what_happened_with_try_again(self):
        from gui.skeleton import Skeleton
        t = self.table()
        again = []
        sk = Skeleton.over(t)
        sk.start()
        sk.fail("MusicBrainz could not be reached.", retry=lambda: again.append(1))
        self.assertFalse(sk.loading())
        self.assertEqual(sk.words.text(), "MusicBrainz could not be reached.")
        self.assertEqual(sk.button.text(), "Try Again")
        sk.button.click()
        self.assertEqual(again, [1])
        self.assertFalse(sk.isVisible())


@unittest.skipIf(QApplication is None, "PySide6 not installed")
class ReadyLine(Base):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_the_ready_count_follows_ticks_and_goes_with_the_list(self):
        import tempfile
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
        import gui
        import gui_screens
        from gui.models import PlanModel
        win = gui.Main()
        win.show()
        try:
            ov = win.pages["Overview"]
            tmp = Path(tempfile.mkdtemp())
            ov.path = str(tmp)
            ov.planned(gui_screens.sample_plan(tmp))
            settle(self.app)
            n = len(ov.to_apply())
            self.assertGreater(n, 1)
            self.assertIn(f"{n} albums ready to apply", ov.ready_line.text())
            first = next(a for a in ov.plan["albums"] if a in ov.to_apply())
            ov.model.set_left_out([first], True) if hasattr(ov.model, "set_left_out") else first.update(
                {PlanModel.LEAVE_OUT: True})
            win.update_album_actions()
            self.assertIn(f"{n - 1} album", ov.ready_line.text())         # (unticked: one fewer)
            ov.plan = None
            ov.model.set_plan(None)
            ov.show_ready()
            self.assertTrue(ov.ready_line.isHidden())                       # (no list: no count)
        finally:
            win.close()
            win.deleteLater()


class Sheets(Base):
    def test_stop_drops_the_late_answer_and_try_again_asks_anew(self):
        from gui import search
        self.assertTrue(hasattr(search.SearchSheet, "run_again") and hasattr(search.VersionsSheet, "run_again"))
        self.assertIsNot(search.SearchSheet.run_again, search._Base.run_again)   # (the field's text now)
        self.assertIsNot(search.VersionsSheet.run_again, search._Base.run_again)
        src = Path(search.__file__).read_text()
        at = src.index("def stop_waiting")
        self.assertIn("self.worker = None", src[at:at + 600])


if __name__ == "__main__":
    unittest.main()
