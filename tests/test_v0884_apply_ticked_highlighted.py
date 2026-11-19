# 0.8.84 (APL-2): Apply carries out every ticked album and every album you highlighted, in
# either pane - a row of Albums as they will be, or a folder in Your files - even one unticked.
# Before, rows highlighted on the right replaced the ticks, and Your files' highlight was ignored.
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_v0811_list_keys as _keys  # noqa: E402  (its Base and settle only)

try:
    from PySide6.QtCore import QItemSelectionModel
    from PySide6.QtWidgets import QApplication
except ImportError:                          # pragma: no cover
    QApplication = None


@unittest.skipIf(QApplication is None, "PySide6 not installed")
class ApplyTickedAndHighlighted(_keys.Base):
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
        self.ov = self.win.pages["Overview"]
        tmp = Path(tempfile.mkdtemp(prefix="tm-apl2-"))
        self.ov.path = str(tmp)
        self.ov.planned(gui_screens.sample_plan(tmp))
        _keys.settle(self.app)
        self.ready = [a for a in self.ov.plan["albums"] if self.ov.applicable() and a in self.ov.applicable()]
        self.assertGreaterEqual(len(self.ready), 3)

    def tearDown(self):
        self.win.close()
        self.win.deleteLater()
        _keys.settle(self.app)
        super().tearDown()

    def row_of(self, album):
        node = self.ov.tree.node_of(album)
        return self.ov.tree.index_of(node)

    def highlight_row(self, album):
        self.ov.table.selectionModel().setCurrentIndex(self.row_of(album),         # (as a click does)
                                                       QItemSelectionModel.ClearAndSelect | QItemSelectionModel.Rows)
        _keys.settle(self.app)

    def highlight_folder(self, album):
        fv = self.ov.panes.folder_view
        node = self.ov.panes._folder_node(album, "album", None)
        fv.selectionModel().setCurrentIndex(fv.model().index_of(node),             # (as a click does)
                                            QItemSelectionModel.ClearAndSelect | QItemSelectionModel.Rows)
        _keys.settle(self.app)

    def ids(self, albums):
        return {id(a) for a in albums}

    def test_APL2_nothing_highlighted_applies_the_ticked(self):
        a, b, c = self.ready[:3]
        self.ov.model.set_left_out([c], True)
        _keys.settle(self.app)
        got = self.ids(self.ov.to_apply())
        self.assertIn(id(a), got)
        self.assertIn(id(b), got)
        self.assertNotIn(id(c), got)

    def test_APL2_a_highlighted_row_adds_to_the_ticked_even_unticked(self):
        a, b, c = self.ready[:3]
        self.ov.model.set_left_out([c], True)
        _keys.settle(self.app)
        self.highlight_row(c)
        got = self.ids(self.ov.to_apply())
        self.assertTrue({id(a), id(b), id(c)} <= got)

    def test_APL2_a_folder_highlighted_in_your_files_counts(self):
        a, b, c = self.ready[:3]
        self.ov.model.set_left_out([b, c], True)
        _keys.settle(self.app)
        self.highlight_folder(c)
        got = self.ids(self.ov.to_apply())
        self.assertIn(id(a), got)
        self.assertIn(id(c), got)
        self.assertNotIn(id(b), got)

    def test_APL2_apply_this_album_is_exactly_it(self):
        a, b, _c = self.ready[:3]
        self.highlight_folder(b)
        seen = {}
        self.ov.apply = lambda: seen.setdefault("albums", self.ov.to_apply())
        self.ov.apply_selected([a])
        self.assertEqual(self.ids(seen["albums"]), {id(a)})

    def test_APL2_a_highlight_ticks_and_its_going_unticks(self):
        from gui.models import PlanModel
        a, b, c = self.ready[:3]
        self.ov.model.set_left_out([b, c], True)
        _keys.settle(self.app)
        self.highlight_row(c)
        self.assertFalse(PlanModel.left_out(c))                      # (highlighted: ticked)
        self.highlight_folder(b)                                      # (Your files: b's; c let go of)
        self.assertFalse(PlanModel.left_out(b))
        self.assertTrue(PlanModel.left_out(c))                       # (its highlight gone: unticked again)
        self.ov.table.clearSelection()                                # (the panes let go together)
        _keys.settle(self.app)
        self.assertTrue(PlanModel.left_out(b))
        self.assertFalse(PlanModel.left_out(a))                      # (your own tick: never touched)

    def test_APL2_a_box_you_click_stays_as_you_set_it(self):
        from PySide6.QtCore import Qt
        from gui.models import PlanModel
        _a, b, _c = self.ready[:3]
        self.ov.model.set_left_out([b], True)
        _keys.settle(self.app)
        self.highlight_row(b)
        self.assertFalse(PlanModel.left_out(b))
        r = self.ov.model.albums.index(b)
        self.ov.model.setData(self.ov.model.index(r, 1), Qt.Checked, Qt.CheckStateRole)   # (you tick it)
        self.ov.table.clearSelection()
        _keys.settle(self.app)
        self.assertFalse(PlanModel.left_out(b))                      # (yours: stays ticked)

    def test_APL2_an_album_only_shown_is_never_ticked_nor_applied(self):
        from gui.models import PlanModel
        _a, _b, c = self.ready[:3]
        self.ov.model.set_left_out([c], True)
        _keys.settle(self.app)
        self.ov.show_row(self.ov.proxy.mapFromSource(self.ov.model.index(self.ov.model.albums.index(c), 0)).row())
        _keys.settle(self.app)
        self.assertTrue(PlanModel.left_out(c))
        self.assertNotIn(id(c), self.ids(self.ov.to_apply()))

    def test_APL2_a_new_preview_keeps_no_tick_of_an_old_highlight(self):
        import gui_screens
        from gui.models import PlanModel
        _a, _b, c = self.ready[:3]
        self.ov.model.set_left_out([c], True)
        self.highlight_row(c)                                         # (ticked by its highlight)
        self.ov.planned(gui_screens.sample_plan(Path(self.ov.path)))   # (previewed again)
        _keys.settle(self.app)
        new = [x for x in self.ov.model.albums if x["source_dir"] == c["source_dir"]]
        self.assertTrue(new and not PlanModel.left_out(new[0]))
        self.highlight_row(self.ov.model.albums[0])
        self.assertFalse(PlanModel.left_out(new[0]))                  # (a click elsewhere: still ticked)

    def test_APL2_include_in_apply_from_the_menu_is_yours(self):
        from gui.models import PlanModel
        _a, _b, c = self.ready[:3]
        self.ov.model.set_left_out([c], True)
        self.highlight_row(c)
        self.ov.set_boxes([c], True)
        self.ov.set_boxes([c], False)                                 # (Include in Apply)
        self.highlight_row(self.ready[0])
        self.assertFalse(PlanModel.left_out(c))


if __name__ == "__main__":
    unittest.main()
