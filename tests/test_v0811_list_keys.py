# 0.8.11: the Overview list's own keys, as in a folder - Delete or Backspace takes the selected
# albums out of the list (nothing on disk), Escape lets go of the selection - also after an Apply
# of every album (the rows stay on screen with no plan behind them), and before the menus' Del
# shortcut (Remove Tag): two shortcuts on one key and Qt ran neither. Remove from List as well.
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_engine import Base  # noqa: E402  (isolated settings and state)

try:
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication
except ImportError:                          # pragma: no cover
    QApplication = None


def settle(app, ms=50):
    end = time.time() + ms / 1000
    while time.time() < end:
        app.processEvents()
        time.sleep(0.01)


@unittest.skipIf(QApplication is None, "PySide6 not installed")
class ListKeys(Base):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        super().setUp()
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
        import gui
        import gui_screens
        self.win = gui.Main()
        self.win.show()
        self.win.activateWindow()
        self.ov = self.win.pages["Overview"]
        tmp = Path(tempfile.mkdtemp(prefix="tm-keys-"))
        self.ov.path = str(tmp)
        self.ov.planned(gui_screens.sample_plan(tmp))
        settle(self.app)

    def tearDown(self):
        self.win.close()
        self.win.deleteLater()
        settle(self.app)
        super().tearDown()

    def all_applied(self):
        """As after an Apply of every album: each row Done, the plan gone, the rows still shown."""
        for a in self.ov.model.albums:
            a["status"] = "done"
        self.ov.plan = None
        self.ov.model.refresh_rows()

    def key(self, k):
        self.ov.table.setFocus()
        settle(self.app)
        QTest.keyClick(self.ov.table, k)
        settle(self.app)

    def test_delete_takes_the_selected_out_even_after_everything_was_applied(self):
        self.all_applied()
        n = len(self.ov.model.albums)
        self.ov.table.selectRow(0)
        self.key(Qt.Key_Delete)
        self.assertEqual(len(self.ov.model.albums), n - 1)
        self.ov.table.selectRow(0)
        self.key(Qt.Key_Backspace)
        self.assertEqual(len(self.ov.model.albums), n - 2)

    def test_remove_from_list_in_the_menu_too(self):
        self.all_applied()
        n = len(self.ov.model.albums)
        self.ov.table.selectRow(1)
        self.ov.remove_selected()
        self.assertEqual(len(self.ov.model.albums), n - 1)

    def test_escape_lets_go_of_the_selection_and_nothing_goes_while_work_runs(self):
        self.ov.table.selectRow(0)
        self.key(Qt.Key_Escape)
        self.assertFalse(self.ov.table.selectionModel().hasSelection())
        n = len(self.ov.model.albums)
        self.ov.table.selectRow(0)
        self.ov.busy(True)
        try:
            self.key(Qt.Key_Delete)
            self.assertEqual(len(self.ov.model.albums), n)
            self.assertIn("when the current work ends", self.win.statusBar().currentMessage())
        finally:
            self.ov.busy(False)


if __name__ == "__main__":
    unittest.main()
