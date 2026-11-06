# 0.8.9: History > Clear History…: everything, or what is older than 1, 5 or 10 days; only the
# log goes, what the rules remember stays.
import sys
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_engine import Base  # noqa: E402  (isolated settings and state)
import exclusions as ex  # noqa: E402


class Clear(Base):
    def fill(self, st):
        now = time.time()
        for days in (0, 2, 7, 30):
            st.db.execute("INSERT INTO history VALUES (?,?,?,?,?,?,?)",
                          (now - days * 86400 - 60, "j", f"/m/{days}", "Ignored", "r", "rule", ""))
        st.db.commit()

    def test_older_than_and_everything(self):
        st = ex.State()
        self.fill(st)
        self.assertEqual(st.clear_history(10), 1)                          # (the 30-day one)
        self.assertEqual(st.clear_history(5), 1)                           # (the 7-day one)
        self.assertEqual(st.clear_history(1), 1)                           # (the 2-day one)
        self.assertEqual([r[2] for r in st.history()], ["/m/0"])
        self.assertEqual(st.clear_history(None), 1)
        self.assertEqual(st.history(), [])


try:
    from PySide6.QtWidgets import QApplication
except ImportError:                          # pragma: no cover
    QApplication = None


@unittest.skipIf(QApplication is None, "PySide6 not installed")
class Page(Base):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_the_button_asks_then_clears(self):
        from gui import sheets
        from gui.history import History
        st = ex.State()
        Clear.fill(self, st)
        page = History(None)
        self.assertEqual(page.table.rowCount(), 4)
        texts = [a.text() for a in page.clear_btn.menu().actions()]
        self.assertEqual(texts, ["Everything", "Older Than 1 Day", "Older Than 5 Days", "Older Than 10 Days"])
        with mock.patch.object(sheets, "confirm", return_value=False):
            self.assertEqual(page.clear(None), 0)
        self.assertEqual(page.table.rowCount(), 4)
        with mock.patch.object(sheets, "confirm", return_value=True):
            self.assertEqual(page.clear(5), 2)
        self.assertEqual(page.table.rowCount(), 2)


if __name__ == "__main__":
    unittest.main()
