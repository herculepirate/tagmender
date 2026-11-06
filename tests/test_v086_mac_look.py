# 0.8.6: a Mac-like look - empty lists say so, the window's title names the page, the logo is
# on the main window's pages only, Setup's sections are cards (offscreen Qt).
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_gui as tg  # noqa: E402


@unittest.skipIf(tg.QApplication is None, "PySide6 not installed")
class MacLook(tg.Gui):
    def test_empty_lists_say_so_until_they_have_a_row(self):
        self.win.resize(1400, 900)
        self.win.go("Jobs")
        self.app.processEvents()
        jobs = self.win.pages["Jobs"]
        jobs.table.setRowCount(0)
        self.app.processEvents()
        self.assertTrue(jobs._empty.state.isVisible())
        jobs.table.setRowCount(1)
        self.app.processEvents()
        self.assertFalse(jobs._empty.state.isVisible())

    def test_the_title_names_the_page(self):
        self.win.go("Watched folders")
        self.assertTrue(self.win.windowTitle().startswith("Watched folders — TagMender"))

    def test_the_logo_on_every_screen(self):
        from gui.common import TitleLabel
        self.win.go("Overview")
        self.app.processEvents()
        t = self.win.pages["Overview"].findChildren(TitleLabel)[0]
        self.assertTrue(t.branded())
        prefs = self.win.preferences()                                   # (0.8.10: Preferences too, with
        for name in ("General", "Profiles", "Setup", "Plugins"):         #  Profiles and Setup in it)
            prefs.show_tab(name)
            self.app.processEvents()
            shown = [x for x in prefs.stack.currentWidget().findChildren(TitleLabel) if x.isVisible()]
            self.assertTrue(shown and all(x.branded() for x in shown), name)
            self.assertGreater(shown[0].contentsMargins().left(), 0, name)    # (room for the logo)
        prefs.close()

    def test_setup_sections_are_cards(self):
        s = self.win.pages["Setup"]
        cards = [h.card for h, _rows in s.heads]
        self.assertTrue(cards and all(c.property("role") == "group" for c in cards))
        s.filter("zzzz-nothing")
        self.assertTrue(all(c.isHidden() for c in cards))
        s.filter("")


if __name__ == "__main__":
    unittest.main()
