# 0.8.6: Overview's columns can each be dragged wider or narrower (fitted to their text until
# you do; Why takes what is left), and a cut cell shows its whole text when pointed at.
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
        from gui.models import PlanModel
        self.win.resize(2400, 800)                                # (room for every column's text)
        self.win.inspector.hide()
        self.app.processEvents()
        ov = self.win.pages["Overview"]
        h = ov.table.horizontalHeader()
        from PySide6.QtWidgets import QHeaderView
        self.assertTrue(all(h.sectionResizeMode(c) == QHeaderView.Interactive for c in range(len(PlanModel.COLS))))
        self.assertTrue(h.stretchLastSection())
        ov.model.set_plan({"albums": [album("/m/Band/One", why=["x" * 300])]})
        self.app.processEvents()
        ov._header_held = True                                    # (pressed on the header)
        h.resizeSection(1, 77)                                    # (you drag the Album folder wider)
        ov._header_held = False
        ov.model.set_plan({"albums": [album("/m/Band/A much longer folder name than before")]})
        self.app.processEvents()
        self.assertEqual(ov.table.columnWidth(1), 77)
        sc = PlanModel.SCANS                                          # (the others: fitted to their text)
        ov.model.set_plan({"albums": [dict(album("/m/x"), identification=dict(album("/m/x")["identification"],
                           scans={"state": "proven", "found": ["barcode", "catalogue_number", "label"],
                                  "against": []}))]})
        self.app.processEvents()
        self.assertGreaterEqual(ov.table.columnWidth(sc), ov.table.sizeHintForColumn(sc))

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


    def test_qt_resizing_while_you_drag_elsewhere_is_not_yours(self):
        self.win.resize(1500, 800)
        self.win.inspector.hide()
        self.app.processEvents()
        ov = self.win.pages["Overview"]
        ov.model.set_plan({"job": "j", "profile": "p", "operation": "in_place", "albums": [album("/m/Band/One")]})
        self.app.processEvents()
        from unittest import mock
        from PySide6.QtWidgets import QApplication
        with mock.patch.object(QApplication, "mouseButtons", return_value=tg.Qt.LeftButton):
            ov.table.setColumnHidden(5, True)                     # (the inspector's edge dragged: Why gives way)
            ov.table.horizontalHeader().resizeSection(4, 500)
            ov.table.setColumnHidden(5, False)
        self.assertEqual(ov._sized_by_you, set())
        ov._header_held = True
        ov.table.horizontalHeader().resizeSection(5, 400)        # (the last column: Qt's, not yours)
        ov._header_held = False
        self.assertEqual(ov._sized_by_you, set())

    def test_columns_give_way_before_the_list_scrolls(self):
        self.win.resize(1440, 900)                                # (the inspector open: a mid-size page)
        self.win.inspector.show()
        self.app.processEvents()
        ov = self.win.pages["Overview"]
        ov.model.set_plan({"job": "j", "profile": "p", "operation": "in_place",
                           "albums": [album("/m/Some Band With A Long Name/An Album With A Long Title", why=["x"])]})
        ov.views.setCurrentWidget(ov.results)
        self.win.go("Overview")
        for _ in range(5):
            self.app.processEvents()
        self.assertLessEqual(ov.table.horizontalHeader().length(), ov.table.viewport().width())


if __name__ == "__main__":
    unittest.main()
