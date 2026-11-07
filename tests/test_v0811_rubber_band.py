# 0.8.11: as in a folder, a rectangle dragged across a list selects the rows it covers (gui.rubber,
# installed by gui.common.left_header on every table); Ctrl adds to the rows selected; a short
# press is a click; a list that selects one row at a time is left alone.
import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_engine import Base  # noqa: E402  (isolated settings and state)

try:
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QAbstractItemView, QApplication, QTableWidget, QTableWidgetItem
except ImportError:                          # pragma: no cover
    QApplication = None


def settle(app, ms=30):
    end = time.time() + ms / 1000
    while time.time() < end:
        app.processEvents()
        time.sleep(0.005)


@unittest.skipIf(QApplication is None, "PySide6 not installed")
class Rubber(Base):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def table(self, mode=QAbstractItemView.ExtendedSelection):
        from gui.common import left_header
        t = QTableWidget(8, 3)
        for r in range(8):
            for c in range(3):
                t.setItem(r, c, QTableWidgetItem(f"{r}{c}"))
        t.setSelectionBehavior(QAbstractItemView.SelectRows)
        t.setSelectionMode(mode)
        left_header(t)
        t.resize(400, 400)
        t.show()
        settle(self.app)
        return t

    def drag(self, t, a, b, mods=Qt.NoModifier):
        port = t.viewport()
        QTest.mousePress(port, Qt.LeftButton, mods, a)
        for i in range(1, 6):
            QTest.mouseMove(port, a + (b - a) * i / 5)
            settle(self.app, 5)
        QTest.mouseRelease(port, Qt.LeftButton, mods, b)
        settle(self.app)

    def rows(self, t):
        return sorted({i.row() for i in t.selectionModel().selectedRows()})

    def y(self, t, r):
        return t.rowViewportPosition(r) + t.rowHeight(r) // 2

    def test_a_rectangle_selects_the_rows_it_covers(self):
        t = self.table()
        self.drag(t, QPoint(300, self.y(t, 1)), QPoint(40, self.y(t, 4)))
        self.assertEqual(self.rows(t), [1, 2, 3, 4])
        self.assertFalse(t._rubber.band.isVisible())                   # (gone on release)

    def test_from_the_empty_space_below_the_rows_upwards(self):
        t = self.table()
        below = t.rowViewportPosition(7) + t.rowHeight(7) + 20
        self.assertLess(below, t.viewport().height())
        self.drag(t, QPoint(200, below), QPoint(200, self.y(t, 5)))
        self.assertEqual(self.rows(t), [5, 6, 7])

    def test_ctrl_adds_to_the_rows_selected(self):
        t = self.table()
        t.selectRow(0)
        self.drag(t, QPoint(200, self.y(t, 3)), QPoint(200, self.y(t, 4)), Qt.ControlModifier)
        self.assertEqual(self.rows(t), [0, 3, 4])

    def test_a_click_is_still_a_click_and_single_select_lists_are_left_alone(self):
        t = self.table()
        QTest.mouseClick(t.viewport(), Qt.LeftButton, Qt.NoModifier, QPoint(200, self.y(t, 2)))
        self.assertEqual(self.rows(t), [2])
        s = self.table(QAbstractItemView.SingleSelection)
        self.drag(s, QPoint(200, self.y(s, 1)), QPoint(200, self.y(s, 4)))
        self.assertLessEqual(len(self.rows(s)), 1)
        self.assertIsNone(s._rubber.band)


if __name__ == "__main__":
    unittest.main()
