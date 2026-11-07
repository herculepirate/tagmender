"""Every dropdown shows all of its options: a plain list under the box (above it where the
screen ends), as many rows as it has; never a list centred on the current item and cut off by
the screen's edge. Offscreen; temporary settings."""
import os as _os, tempfile as _tempfile  # noqa: E402
_os.environ.setdefault("XDG_CONFIG_HOME", _tempfile.mkdtemp(prefix="tm-config-"))
_os.environ.setdefault("XDG_STATE_HOME", _tempfile.mkdtemp(prefix="tm-state-"))
_os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

try:
    from PySide6.QtWidgets import QApplication, QComboBox
except ImportError:                          # pragma: no cover
    QApplication = None


@unittest.skipIf(QApplication is None, "PySide6 not installed")
class WholeLists(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_every_dropdown_in_the_window(self):
        import gui
        win = gui.Main()
        self.addCleanup(win.close)
        win.show()
        self.app.processEvents()
        combos = win.findChildren(QComboBox)
        self.assertTrue(combos)
        for c in combos:
            self.assertGreaterEqual(c.maxVisibleItems(), max(c.count(), 30), c.objectName() or c.accessibleName())

    def test_a_long_list_opens_whole(self):
        from gui import style
        style.apply(self.app)
        c = QComboBox()
        self.addCleanup(c.deleteLater)
        c.addItems([f"item {i}" for i in range(25)])
        c.show()
        self.app.processEvents()
        c.showPopup()
        self.app.processEvents()
        self.assertFalse(c.view().verticalScrollBar().isVisible())
        c.hidePopup()
        self.assertIn("combobox-popup: 0", style.stylesheet())


if __name__ == "__main__":
    unittest.main()
