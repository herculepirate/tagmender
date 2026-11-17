# 0.8.71 main window, offscreen: the Overview as a Mac window - no page title, Sources or Library
# folder bar; the toolbar's All / Ready / Needs Attention filter with counts, and Apply carrying its
# count; the albums pane is the list (an album a row, its discs and tracks under it, its menu and
# keys); the inspector is the pane under the lists (Tags | Why This Release, beside the Cover),
# shown or hidden by the toolbar's Inspector button and remembered; the profile pop-up follows the
# rows you select (Albums or Your files), "Mixed" when they differ; rows tinted by their profile's
# colour, its name beside it.
import os as _os, tempfile as _tempfile  # noqa: E402
_os.environ["XDG_CONFIG_HOME"] = _tempfile.mkdtemp(prefix="tm-config-")
_os.environ["XDG_STATE_HOME"] = _tempfile.mkdtemp(prefix="tm-state-")
_os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
_os.environ.setdefault("TAGMENDER_TAGGER_INI", "/nonexistent")

import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "tools"))
try:
    from PySide6.QtCore import QItemSelectionModel, QPoint, Qt
    from PySide6.QtWidgets import QApplication
except ImportError:                                           # GUI is optional
    QApplication = None


def settle(app, n=10):
    for _ in range(n):
        app.processEvents()
        time.sleep(0.01)


@unittest.skipIf(QApplication is None, "PySide6 not installed")
class MainWindow(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import gui
        import profiles as pf
        cls.gui, cls.pf = gui, pf
        cls.app = QApplication.instance() or QApplication([])
        cls.tmp = Path(tempfile.mkdtemp(prefix="tm-v0871-"))
        for name in ("LossyLess Unraid", "Extreme Unraid", "Plain"):
            pf.save(pf.from_dict({"name": name, "operation": "in_place"}))

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def setUp(self):
        from gui.common import _set_gui_pref, forget_profile_colours
        _set_gui_pref("reduce_motion", True)
        _set_gui_pref("preview_inspector", None)
        forget_profile_colours()
        self.gui.sheets.shown.clear()
        self.win = self.gui.Main()
        self.win.resize(1440, 900)
        self.win.show()
        settle(self.app)

    def tearDown(self):
        end = time.time() + 60
        while time.time() < end and self.win.threads():
            settle(self.app, 2)
        self.win.close()
        self.win.deleteLater()
        from PySide6.QtCore import QEvent
        QApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        self.gui.style.apply(self.app, "light")

    def show_plan(self, profile="Plain"):
        import gui_screens
        ov = self.win.pages["Overview"]
        self.win.go("Overview")
        ov.profile.setCurrentText(profile)
        ov.path = str(self.tmp)
        ov.planned(gui_screens.sample_plan(self.tmp))
        settle(self.app)
        return ov

    def rows_of(self, ov, *statuses):
        return [r for r in range(ov.tree.rowCount()) if ov.tree.album_at(r)["status"] in statuses]

    def choose(self, ov, rows):
        """Rows chosen as a click and Ctrl+clicks choose them (not only shown)."""
        sm = ov.table.selectionModel()
        sm.clear()
        for i, r in enumerate(rows):
            sm.setCurrentIndex(ov.tree.index(r, 0), (QItemSelectionModel.ClearAndSelect if not i else
                                                     QItemSelectionModel.Select) | QItemSelectionModel.Rows)
        settle(self.app)
        return [ov.tree.album_at(r) for r in rows]

    def menu_items(self, ov, folders=False):
        """The albums' context menu's items (popup_menu patched: offscreen it would wait for a click)."""
        with mock.patch("gui.overview.popup_menu") as pm:
            view = ov.panes.folder_view if folders else ov.table
            ov.context_menu(view.visualRect(view.currentIndex()).center(), folders=folders)
        return pm.call_args.args[3] if pm.called else []

    @staticmethod
    def named(items, words):
        for a in items:
            if a is not None and a.text().replace("&", "") == words:
                return a
        return None

    # ---- what the Overview no longer has ------------------------------------------------------
    def test_no_page_title_sources_bar_or_library_folder_bar(self):
        ov = self.show_plan()
        from gui.common import TitleLabel
        self.assertEqual([w for w in ov.findChildren(TitleLabel) if w.isVisible()], [])
        for w in (ov.sources, ov.sources_label, ov.library_label, ov.library_where, ov.library_btn, ov.look_btn,
                  ov.refresh_btn, ov.clear_done_btn, ov.where):
            self.assertFalse(w.isVisible(), w)
        self.assertEqual(ov.layout().contentsMargins().left(), 0)          # (the panes meet the window's edges)
        # their commands stay in the menu bar: Look Up, the library folder for this preview, Clear Done
        for key in ("look_up", "choose_library", "clear_done", "preview"):
            self.assertTrue(any(self.win.act(key) in m.actions() for m in self.win.menus.values()), key)
        # one list: the albums pane is it, and the old album table is gone
        self.assertIs(ov.table, ov.panes.album_view)
        self.assertEqual(ov.tree.rowCount(), ov.proxy.rowCount())

    # ---- the toolbar ---------------------------------------------------------------------------
    def test_the_filter_counts_and_shows_only_its_albums(self):
        ov = self.show_plan()
        seg = self.win.filter_seg
        albums = ov.model.albums
        ready = [a for a in albums if ov.ready_now(a)]
        need = [a for a in albums if ov.needs_you(a)]
        self.assertTrue(ready and need)
        self.assertEqual(seg.buttons["all"].text(), f"All {len(albums)}")
        self.assertEqual(seg.buttons["ready"].text(), f"Ready {len(ready)}")
        # (a narrow window says "Attention 3"; its name read out is always whole)
        self.assertIn(seg.buttons["attention"].text(), (f"Needs Attention {len(need)}", f"Attention {len(need)}"))
        self.assertEqual(seg.buttons["attention"].accessibleName(), f"Needs Attention, {len(need)} albums")
        self.assertIn(f"{len(ready)} albums ready", ov.counts_line.text())
        self.assertIn("need your decision", ov.counts_line.text())
        seg.buttons["ready"].click()
        settle(self.app)
        self.assertEqual(ov.tree.rowCount(), len(ready))
        self.assertTrue(all(ov.ready_now(ov.tree.album_at(r)) for r in range(ov.tree.rowCount())))
        seg.buttons["attention"].click()
        settle(self.app)
        self.assertEqual({id(ov.tree.album_at(r)) for r in range(ov.tree.rowCount())}, {id(a) for a in need})
        seg.buttons["all"].click()
        settle(self.app)
        self.assertEqual(ov.tree.rowCount(), len(albums))
        # (only on the Overview)
        self.win.go("History")
        self.assertFalse(self.win.profile_actions[2].isVisible())          # (its toolbar item)
        self.win.go("Overview")
        self.assertTrue(self.win.profile_actions[2].isVisible())

    def test_apply_says_how_many_albums_and_is_off_with_none(self):
        ov = self.show_plan()
        a = self.win.act("apply")
        n = ov.actionable()
        self.assertTrue(n)
        self.assertEqual(a.iconText(), "Apply 1 Album" if n == 1 else f"Apply {n} Albums")
        b = self.win.toolbar.widgetForAction(a)
        self.assertEqual(b.property("role"), "primary")
        self.assertIs(self.win.toolbar.actions()[-1], a)                  # (last: the one prominent button)
        ov.model.set_left_out(ov.model.albums, True)
        self.win.update_album_actions()
        self.assertEqual(a.iconText(), "Apply")
        self.assertFalse(a.isEnabled())
        self.assertEqual(a.text().replace("&", ""), "Apply")              # (the menu keeps its word)

    def test_cancel_is_on_the_toolbar_only_while_work_runs(self):
        self.show_plan()
        self.assertFalse(self.win.cancel_action.isVisible())
        self.win.show_progress(True)
        self.assertTrue(self.win.cancel_action.isVisible())
        self.win.show_progress(False)
        self.assertFalse(self.win.cancel_action.isVisible())
        self.assertIn(self.win.act("cancel"), self.win.menus["File"].actions())

    # ---- the inspector pane --------------------------------------------------------------------
    def test_the_inspector_button_shows_and_hides_the_pane_and_is_remembered(self):
        from gui.common import _gui_prefs
        ov = self.show_plan()
        a = self.win.act("show_inspector")
        self.assertTrue(ov.panes.bottom.isVisible())                       # (shown at first)
        self.assertTrue(a.isChecked())
        self.assertFalse(self.win.inspector.isVisible())                  # (no dock on the Overview)
        a.trigger()
        settle(self.app)
        self.assertFalse(ov.panes.bottom.isVisible())
        self.assertIs(_gui_prefs().get("preview_inspector"), False)
        again = self.gui.Main()
        again.show()
        settle(self.app)
        self.assertTrue(again.pages["Overview"].panes.bottom.isHidden())
        self.assertFalse(again.act("show_inspector").isChecked())
        again.close()
        again.deleteLater()
        a.trigger()
        settle(self.app)
        self.assertTrue(ov.panes.bottom.isVisible())
        self.assertIs(_gui_prefs().get("preview_inspector"), True)
        # View › Evidence opens Why This Release; Tags the tags
        self.win.act("tab_evidence").trigger()
        self.assertIs(ov.panes.stack.currentWidget(), ov.panes.evidence)
        self.win.act("tab_tags").trigger()
        self.assertEqual(ov.panes.stack.currentIndex(), 0)

    def test_f2_edits_a_tag_in_the_pane_and_add_and_remove_are_there(self):
        ov = self.show_plan()
        r = self.rows_of(ov, "ready")[0]
        album = self.choose(ov, [r])[0]
        p = ov.panes
        self.assertIs(p.scope[0], album)
        rows = {x["field"]: i for i, x in enumerate(p.tag_model.rows)}
        p.tag_view.setFocus()
        p.tag_view.selectRow(rows["date"])
        settle(self.app)
        self.win.update_tag_actions()
        self.assertTrue(self.win.act("edit_tag").isEnabled())
        self.assertTrue(self.win.act("add_tag").isEnabled())
        self.win.act("edit_tag").trigger()                                 # (F2: typed in its cell)
        from PySide6.QtWidgets import QAbstractItemView
        self.assertEqual(p.tag_view.state(), QAbstractItemView.EditingState)
        p.tag_view.closePersistentEditor(p.tag_view.currentIndex())
        insp = self.win.inspector
        with mock.patch.object(type(insp), "reapply") as reapply:
            p.tag_model.setData(p.tag_model.index(rows["date"], 3), "1999")
            reapply.assert_called_once()
        self.assertEqual(insp.edit_of(album)["tags"]["date"], ["1999"])
        from gui import inspector
        with mock.patch.object(inspector, "tag_sheet", return_value=("Mood", ["calm"])), \
                mock.patch.object(type(insp), "reapply"):
            self.win.act("add_tag").trigger()
        self.assertEqual(insp.edit_of(album)["tags"]["mood"], ["calm"])
        p.tag_view.selectRow(rows["date"])
        with mock.patch.object(type(insp), "reapply"):
            self.win.act("remove_tag").trigger()
        self.assertIsNone(insp.edit_of(album)["tags"]["date"])
        self.assertTrue(p.rating is not None and not p.rating.isHidden())  # (the stars: Album › Tag › Rating)

    # ---- the albums pane: the list --------------------------------------------------------------
    def test_an_album_row_has_its_menu_status_score_and_discs(self):
        ov = self.show_plan()
        r = self.rows_of(ov, "ready")[0]
        album = self.choose(ov, [r])[0]
        idx = ov.tree.index(r, 0)
        self.assertEqual(ov.tree.data(ov.tree.index(r, 1)).split(" · ")[0], "Ready")
        score = ov.tree.index(r, 2)
        self.assertTrue(ov.tree.data(score))                               # (its score: a plain number,
        self.assertIsNone(ov.tree.data(score, Qt.BackgroundRole))          #  no wash - Plain has no colour -
        self.assertIsNone(ov.tree.data(score, Qt.FontRole))                #  nor bold, in the secondary colour)
        self.assertEqual(ov.tree.data(score, Qt.ForegroundRole).name().lower(),
                         self.gui.style.color("muted").lower())
        self.assertIn("not a probability", ov.tree.data(score, Qt.ToolTipRole))   # (how far to trust it: told)
        self.assertTrue(int(ov.tree.data(score, Qt.TextAlignmentRole)) & int(Qt.AlignRight))
        self.assertTrue(ov.tree.hasChildren(idx))
        disc = ov.tree.index(0, 0, idx)
        self.assertTrue(ov.tree.data(disc, Qt.UserRole + 5).startswith("← "))   # (the folder it came from)
        self.assertIn(album["source_dir"], ov.tree.data(idx, Qt.ToolTipRole))
        items = self.menu_items(ov)
        for words in ("Open Folder", "Search the Web", "Look Up", "Profile", "Leave Out of Apply", "Remove from List",
                      "Apply This Album…", "Change Release", "Use Suggested Pressing"):
            self.assertIsNotNone(self.named(items, words), words)
        with mock.patch("gui.window.open_folder") as opened:
            self.named(items, "Open Folder").trigger()
        opened.assert_called_once_with(album["source_dir"])
        self.named(items, "Leave Out of Apply").trigger()
        self.assertTrue(ov.model.left_out(album))
        # a disc chosen: its album is the one the commands act on
        ov.table.selectionModel().setCurrentIndex(disc, QItemSelectionModel.ClearAndSelect | QItemSelectionModel.Rows)
        settle(self.app)
        self.assertIs(ov.selected(), album)
        self.assertEqual(ov.panes.scope[1], "disc")
        # Delete takes it out of the list (nothing on disk)
        from PySide6.QtTest import QTest
        ov.table.setFocus()
        self.choose(ov, [r])
        QTest.keyClick(ov.table, Qt.Key_Delete)
        self.assertFalse(any(a is album for a in ov.model.albums))
        # sorting by a column of the pane sorts the list
        ov.table.sortByColumn(2, Qt.DescendingOrder)
        self.assertEqual(ov.proxy.sortColumn(), 3)

    def test_folders_dropped_on_the_window_still_join_the_list(self):
        from PySide6.QtCore import QMimeData, QUrl
        from PySide6.QtGui import QDropEvent
        self.show_plan()
        here = self.tmp / "dropped871"
        here.mkdir(exist_ok=True)
        md = QMimeData()
        md.setUrls([QUrl.fromLocalFile(str(here))])
        ov = self.win.pages["Overview"]
        self.assertFalse(ov.tree.canDropMimeData(md, Qt.CopyAction, -1, -1, ov.tree.index(0, 0)))  # (not the pane's)
        with mock.patch("gui.manual_match.add_dropped") as added:
            self.win.dropEvent(QDropEvent(QPoint(5, 5), Qt.CopyAction, md, Qt.LeftButton, Qt.NoModifier))
        self.assertEqual(added.call_args.args[1], [str(here)])

    # ---- the profile follows the selection ------------------------------------------------------
    def test_the_profile_popup_sets_the_profile_of_the_albums_selected(self):
        ov = self.show_plan()
        rows = self.rows_of(ov, "ready", "unresolved")[:2]
        two = self.choose(ov, rows)
        self.assertEqual(ov.profile.currentText(), "Plain")                # (theirs: the preview's)
        with mock.patch.object(type(ov), "look_up") as looked:
            i = ov.profile.findText("Extreme Unraid")
            ov.profile.setCurrentIndex(i)
            ov.profile.activated.emit(i)
        self.assertEqual([ov.profile_of(a) for a in two], ["Extreme Unraid"] * 2)
        self.assertEqual({id(a) for a in looked.call_args.args[1]}, {id(a) for a in two})    # (looked up again)
        self.assertEqual(ov.above(), "Plain")                              # (the preview's profile stays)
        # a mixed selection: "Mixed", a placeholder that is no profile
        other = self.rows_of(ov, "ready", "unresolved", "excluded", "skipped")
        third = next(r for r in other if r not in rows)
        self.choose(ov, [rows[0], third])
        self.assertEqual(ov.profile.currentText(), "Mixed")
        i = ov.profile.findText("Mixed")
        self.assertFalse(ov.profile.model().item(i).isEnabled())
        # nothing selected: the preview's profile, chosen as before
        ov.table.selectionModel().clear()
        settle(self.app)
        self.assertEqual(ov.profile.findText("Mixed"), -1)
        self.assertEqual(ov.profile.currentText(), "Plain")
        ov.profile.setCurrentText("LossyLess Unraid")
        self.assertEqual(ov.above(), "LossyLess Unraid")
        self.assertEqual(ov.profile_of(two[0]), "Extreme Unraid")          # (its own stays its own)

    def test_profile_from_the_context_menu_and_the_album_menu(self):
        ov = self.show_plan()
        two = self.choose(ov, self.rows_of(ov, "ready", "unresolved")[:2])
        items = self.menu_items(ov)
        pm = self.named(items, "Profile").menu()
        names = {a.text().replace("&", ""): a for a in pm.actions() if not a.isSeparator()}
        self.assertTrue(names["As Above (Plain)"].isChecked())
        with mock.patch.object(type(ov), "look_up") as looked:
            names["LossyLess Unraid"].trigger()
        self.assertEqual([ov.profile_of(a) for a in two], ["LossyLess Unraid"] * 2)
        looked.assert_called_once()
        self.assertEqual(ov.profile.currentText(), "LossyLess Unraid")     # (the pop-up says so)
        # Album › Profile: the same, from the menu bar (checked: theirs)
        m = self.win.menus["Profile"]
        m.aboutToShow.emit()
        ticked = [a.text().replace("&", "") for a in m.actions() if a.isChecked()]
        self.assertEqual(ticked, ["LossyLess Unraid"])
        self.assertIn(m.menuAction(), self.win.menus["Album"].actions())

    def test_two_folders_selected_in_your_files_take_a_profile_from_the_menu(self):
        ov = self.show_plan()
        fv, fm = ov.panes.folder_view, ov.panes.folders
        sm = fv.selectionModel()
        sm.setCurrentIndex(fm.index(0, 0), QItemSelectionModel.ClearAndSelect | QItemSelectionModel.Rows)
        sm.setCurrentIndex(fm.index(1, 0), QItemSelectionModel.Select | QItemSelectionModel.Rows)
        settle(self.app)
        albums = ov.panes.folder_albums()
        self.assertEqual(len(albums), 2)
        self.assertEqual(ov.profile_targets(), albums)
        items = self.menu_items(ov, folders=True)
        pm = self.named(items, "Profile").menu()
        with mock.patch.object(type(ov), "look_up"):
            next(a for a in pm.actions() if a.text().replace("&", "") == "Extreme Unraid").trigger()
        self.assertEqual([ov.profile_of(a) for a in albums], ["Extreme Unraid"] * 2)

    # ---- a profile's colour -------------------------------------------------------------------------
    def test_rows_are_tinted_with_their_profiles_colour_and_name_it(self):
        from gui import style
        from gui.common import profile_colour
        self.assertEqual(profile_colour("LossyLess VA Unraid"), "green")
        self.assertEqual(profile_colour("Extreme Unraid"), "orange")
        self.assertEqual(profile_colour("Plain"), "")
        ov = self.show_plan()
        r = self.rows_of(ov, "ready")[0]
        a = ov.tree.album_at(r)
        idx = ov.tree.index(r, 0)
        self.assertIsNone(ov.tree.data(idx, Qt.BackgroundRole))          # (Plain: no colour)
        self.assertIn("Plain", ov.tree.data(idx, Qt.UserRole + 5))        # (its profile, in words)
        self.choose(ov, [r])
        items = self.menu_items(ov)
        with mock.patch.object(type(ov), "look_up"):
            next(x for x in self.named(items, "Profile").menu().actions() if x.text().replace("&", "") == "LossyLess Unraid").trigger()
        settle(self.app)
        green = style.profile_tint("green")
        self.assertEqual(ov.tree.data(idx, Qt.BackgroundRole), green)
        self.assertEqual(ov.tree.data(ov.tree.index(0, 0, idx), Qt.BackgroundRole), green)    # (its discs)
        self.assertIn("LossyLess Unraid", ov.tree.data(idx, Qt.UserRole + 5))
        root = next(i for i in range(ov.panes.folders.rowCount())
                    if any(x is a for x in ov.panes.folders.top.kids[i].data["albums"]))
        self.assertEqual(ov.panes.folders.data(ov.panes.folders.index(root, 0), Qt.BackgroundRole), green)
        # dark: the dark wash
        self.gui.style.apply(self.app, "dark")
        self.assertEqual(ov.tree.data(idx, Qt.BackgroundRole), style.profile_tint("green", dark=True))
        self.assertNotEqual(style.profile_tint("green", dark=True), style.profile_tint("green", dark=False))
        # a colour of the profile's own wins over its name's; "none" is none
        self.pf.save(self.pf.from_dict({"name": "LossyLess Unraid", "operation": "in_place", "colour": "blue"}))
        self.win.pages["Profiles"]._tell_overview()                       # (as Save does)
        self.assertEqual(profile_colour("LossyLess Unraid"), "blue")
        self.pf.save(self.pf.from_dict({"name": "LossyLess Unraid", "operation": "in_place", "colour": "none"}))
        from gui.common import forget_profile_colours
        forget_profile_colours()
        self.assertEqual(profile_colour("LossyLess Unraid"), "")
        self.pf.save(self.pf.from_dict({"name": "LossyLess Unraid", "operation": "in_place"}))

    def test_profile_tints_keep_the_words_readable(self):
        from gui import style
        for dark in (False, True):
            t = style.tokens(dark)
            for name in style.PROFILE_TINTS:
                bg = style.profile_tint(name, dark=dark).name()
                for ink in ("ink", "muted", "attention", "error", "identified"):
                    c = style.contrast(t[ink], bg)
                    self.assertGreaterEqual(c, 4.5, f"{ink} on {name} ({'dark' if dark else 'light'}): {c:.2f}")

    def test_the_profile_editor_offers_a_colour_and_writes_none_until_chosen(self):
        from gui.profile_editor import BY_KEY
        spec = BY_KEY["colour"]
        self.assertIn(("", "Automatic"), spec.choices)
        d = {"name": "X"}
        spec.write(d, "", {})
        self.assertNotIn("colour", d)
        spec.write(d, "orange", {})
        self.assertEqual(d["colour"], "orange")


if __name__ == "__main__":
    unittest.main()
