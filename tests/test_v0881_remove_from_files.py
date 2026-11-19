# 0.8.81: an album can be taken out of the list from either pane. Delete (or Backspace) in Your
# files did nothing - only the albums pane had the keys - so a folder selected there (a box set's
# folder with its disc folders) could not be removed. Edit › Remove from List does it from either.
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_v0811_list_keys as _keys  # noqa: E402  (its Base and settle only)

try:
    from PySide6.QtCore import QItemSelectionModel, Qt
    from PySide6.QtTest import QTest
except ImportError:                          # pragma: no cover
    QTest = None


@unittest.skipIf(QTest is None, "PySide6 not installed")
class RemoveFromYourFiles(_keys.Base):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        super().setUp()
        import tempfile
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
        import gui
        import gui_screens
        self.win = gui.Main()
        self.win.show()
        self.win.activateWindow()
        self.ov = self.win.pages["Overview"]
        tmp = Path(tempfile.mkdtemp(prefix="tm-rm-"))
        self.ov.path = str(tmp)
        self.ov.planned(gui_screens.sample_plan(tmp))
        _keys.settle(self.app)

    def tearDown(self):
        self.win.close()
        self.win.deleteLater()
        _keys.settle(self.app)
        super().tearDown()

    def pick_folder(self, row=0):
        fv = self.ov.panes.folder_view
        idx = fv.model().index(row, 0)
        fv.selectionModel().select(idx, QItemSelectionModel.ClearAndSelect | QItemSelectionModel.Rows)
        fv.setFocus()
        _keys.settle(self.app)
        return fv

    def test_delete_in_your_files_takes_its_album_out(self):
        n = len(self.ov.model.albums)
        fv = self.pick_folder()
        gone = self.ov.panes.folder_albums()
        self.assertEqual(len(gone), 1)
        QTest.keyClick(fv, Qt.Key_Delete)
        _keys.settle(self.app)
        self.assertEqual(len(self.ov.model.albums), n - 1)
        self.assertFalse(any(a is gone[0] for a in self.ov.model.albums))
        self.pick_folder()
        QTest.keyClick(fv, Qt.Key_Backspace)
        _keys.settle(self.app)
        self.assertEqual(len(self.ov.model.albums), n - 2)

    def test_unticked_album_comes_out_too(self):
        n = len(self.ov.model.albums)
        self.ov.model.set_left_out([self.ov.model.albums[0]], True)
        _keys.settle(self.app)
        self.ov.table.setCurrentIndex(self.ov.table.model().index(0, 0))
        fv = self.pick_folder()
        QTest.keyClick(fv, Qt.Key_Delete)
        _keys.settle(self.app)
        self.assertEqual(len(self.ov.model.albums), n - 1)

    def test_edit_menu_remove_from_list_follows_the_pane_chosen(self):
        cmd = self.win.commands["remove_from_list"]
        self.ov.table.clearSelection()
        self.ov.panes.folder_view.clearSelection()
        _keys.settle(self.app)
        self.assertFalse(cmd.isEnabled())
        n = len(self.ov.model.albums)
        self.pick_folder()
        self.assertTrue(cmd.isEnabled())
        cmd.trigger()
        _keys.settle(self.app)
        self.assertEqual(len(self.ov.model.albums), n - 1)
        self.ov.table.setFocus()
        self.ov.table.setCurrentIndex(self.ov.table.model().index(0, 0))
        _keys.settle(self.app)
        cmd.trigger()
        _keys.settle(self.app)
        self.assertEqual(len(self.ov.model.albums), n - 2)

    def test_escape_in_your_files_lets_go_of_the_selection(self):
        fv = self.pick_folder()
        QTest.keyClick(fv, Qt.Key_Escape)
        _keys.settle(self.app)
        self.assertFalse(fv.selectionModel().hasSelection())


if __name__ == "__main__":
    unittest.main()
