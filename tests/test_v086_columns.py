# 0.8.6: Overview's columns can each be dragged wider or narrower (fitted to their text until
# you do), and a cut cell shows its whole text when pointed at. 0.8.71: the list is the albums
# pane (name, Status, Score); the other columns' words are in the album row's tooltip.
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_gui as tg  # noqa: E402


def album(folder, why=(), conflicts=()):
    return {"source_dir": folder, "status": "ready", "files": [], "conflicts": list(conflicts),
            "identification": {"provider": "musicbrainz", "confidence": 90, "tier": "exact", "why": list(why)}}


@unittest.skipIf(tg.QApplication is None, "PySide6 not installed")
class V086Columns(tg.Gui):
    def test_every_column_can_be_resized_and_a_drag_is_kept(self):
        # (0.8.71: the albums pane - Albums as they will be, Status, Score - is the list: the name
        # takes the room, Status is fitted to its words until you drag it, and stays so)
        from PySide6.QtWidgets import QHeaderView
        from gui.preview_panes import AlbumsModel
        self.win.resize(2400, 800)
        self.app.processEvents()
        ov = self.win.pages["Overview"]
        h = ov.table.header()
        self.assertEqual(h.sectionResizeMode(AlbumsModel.NAME), QHeaderView.Stretch)
        self.assertEqual(h.sectionResizeMode(AlbumsModel.STATUS), QHeaderView.Interactive)
        ov.model.set_plan({"albums": [album("/m/Band/One", why=["x" * 300])]})
        ov.fit_columns()
        lo, hi = ov.STATUS_WIDTH
        self.assertTrue(lo <= ov.table.columnWidth(AlbumsModel.STATUS) <= hi)
        h.resizeSection(AlbumsModel.STATUS, 201)                  # (you drag it)
        ov.model.set_plan({"albums": [album("/m/Band/A much longer folder name than before")]})
        ov.fit_columns()
        self.assertEqual(ov.table.columnWidth(AlbumsModel.STATUS), 201)

    def test_tooltips_give_the_whole_text(self):
        from gui.models import PlanModel
        m = PlanModel()
        long_why = ["reason %d is rather long" % i for i in range(20)]
        m.albums = [album("/m/x", why=long_why), album("/m/y", conflicts=["first problem", "second problem"])]
        tip = m.data(m.index(0, 5), tg.Qt.ToolTipRole)
        self.assertIn("reason 19 is rather long", tip)                  # (the cell shows 120 characters)
        self.assertLess(len(m.data(m.index(0, 5), tg.Qt.DisplayRole)), len("; ".join(long_why)))
        tip = m.data(m.index(1, 5), tg.Qt.ToolTipRole)
        self.assertIn("second problem", tip)
        m.albums[0]["identification"]["why"] = ["a <b> tag"]
        self.assertIn("a &lt;b&gt; tag", m.data(m.index(0, 5), tg.Qt.ToolTipRole))   # (shown as written)


    def test_no_column_is_hidden_and_the_whys_are_in_the_rows_tooltip(self):
        # (0.8.71: three columns, none given way; Source, Changes, Why and Scans in the row's tooltip)
        ov = self.win.pages["Overview"]
        ov.model.set_plan({"job": "j", "profile": "p", "operation": "in_place",
                           "albums": [album("/m/Band/One", why=["the label differs"])]})
        self.app.processEvents()
        self.assertFalse(any(ov.table.isColumnHidden(c) for c in range(ov.tree.columnCount())))
        tip = ov.tree.data(ov.tree.index(0, 0), tg.Qt.ToolTipRole)
        self.assertIn("Why: the label differs", tip)
        self.assertIn("Source: MusicBrainz", tip)

    def test_columns_give_way_before_the_list_scrolls(self):
        self.win.resize(1440, 900)                                # (a mid-size page)
        self.app.processEvents()
        ov = self.win.pages["Overview"]
        ov.model.set_plan({"job": "j", "profile": "p", "operation": "in_place",
                           "albums": [album("/m/Some Band With A Long Name/An Album With A Long Title", why=["x"])]})
        ov.views.setCurrentWidget(ov.results)
        self.win.go("Overview")
        for _ in range(5):
            self.app.processEvents()
        self.assertLessEqual(ov.table.header().length(), ov.table.viewport().width() + 1)


if __name__ == "__main__":
    unittest.main()
