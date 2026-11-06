# The app's Mac-style shell (the app's design rules), offscreen: every command in the menu bar with a
# shortcut or a keyboard route, accessible names, window-modal sheets that never block a test,
# the sidebar, the inspector, Preferences, the icons, 960x600 without clipped controls, and text
# contrast of at least 4.5:1 for the status colours in light and dark.
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
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QPalette
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import (QAbstractButton, QAbstractItemView, QAbstractSpinBox, QApplication, QComboBox,
                                   QLineEdit, QPlainTextEdit, QScrollArea, QTabBar, QWidget)
except ImportError:                                           # GUI is optional
    QApplication = None


def settle(app, n=10):
    for _ in range(n):
        app.processEvents()
        time.sleep(0.01)


def interactive(root):
    """The controls a person reaches: buttons, fields, lists and tables (not Qt's own parts: a
    combo box's line edit, scroll bars, a tab bar's scroll arrows, the dock's close button)."""
    out = []
    for w in root.findChildren(QWidget):
        if not isinstance(w, (QAbstractButton, QComboBox, QLineEdit, QAbstractSpinBox, QAbstractItemView,
                              QPlainTextEdit)):
            continue
        if w.objectName().startswith("qt_") or \
                isinstance(w.parentWidget(), (QComboBox, QAbstractSpinBox, QTabBar, QLineEdit)):
            continue
        if type(w).__name__ in ("QHeaderView", "QTableCornerButton", "QComboBoxListView", "QCalendarView"):
            continue
        if w.parentWidget() is not None and isinstance(w.parentWidget().parentWidget(), QComboBox):
            continue                                          # (a combo box's drop-down list)
        out.append(w)
    return out


@unittest.skipIf(QApplication is None, "PySide6 not installed")
class Shell(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import gui
        cls.gui = gui
        cls.app = QApplication.instance() or QApplication([])
        cls.tmp = Path(tempfile.mkdtemp(prefix="tm-shell-"))

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def setUp(self):
        self.gui.common._set_gui_pref("reduce_motion", True)
        self.gui.sheets.shown.clear()
        __import__("app_settings").set_value("add_folder", "look_up")   # (these drop a folder to look it up)
        self.win = self.gui.Main()
        self.win.resize(1180, 760)
        self.win.show()
        self.win.activateWindow()
        settle(self.app)

    def tearDown(self):
        end = time.time() + 30
        while time.time() < end and self.win.threads():
            settle(self.app, 2)
        self.win.close()
        if self.win.prefs_window is not None:
            self.win.prefs_window.deleteLater()
        self.win.deleteLater()                                        # (a closed window is still re-styled)
        from PySide6.QtCore import QEvent
        QApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        self.gui.style.apply(self.app, "light")

    def plan(self):
        import gui_screens
        return gui_screens.sample_plan(self.tmp)

    def show_plan(self, plan=None):
        ov = self.win.pages["Overview"]
        self.win.go("Overview")
        ov.path = str(self.tmp)
        ov.planned(plan or self.plan())
        settle(self.app)
        return ov

    def select(self, ov, status, tier=None):
        for r in range(ov.proxy.rowCount()):
            a = ov.proxy.data(ov.proxy.index(r, 0), Qt.UserRole)
            if a["status"] == status and (tier is None or a["identification"].get("tier") == tier):
                # (that row alone: selectRow would follow Ctrl or Shift a key click left held)
                from PySide6.QtCore import QItemSelectionModel
                ov.table.selectionModel().setCurrentIndex(
                    ov.proxy.index(r, 0), QItemSelectionModel.ClearAndSelect | QItemSelectionModel.Rows)
                settle(self.app)
                return ov.selected()                                  # (the plan's own entry, not a copy)
        self.fail(f"no {status} album")

    # ---- commands ---------------------------------------------------------
    def test_every_command_is_in_a_menu_with_a_shortcut_or_a_keyboard_route(self):
        menus = self.win.menus
        in_menu = {a for m in menus.values() for a in m.actions()}
        for title in self.gui.actions.MENUS:
            self.assertIn("&", title[0], f"{title[0]}: no underlined letter (Alt+letter)")
        seen = {}
        for key, a in self.win.commands.items():
            self.assertTrue(a.text().strip(), key)
            self.assertTrue(self.gui.actions.accessible(a), key)
            self.assertTrue(a.statusTip(), key)
            self.assertIn(a, in_menu, f"{key} is in no menu")
            sc = a.shortcut().toString()
            if sc:
                # (Delete: Remove Rule on Profiles, Remove Tag in the inspector's Tags, never both at once)
                if {key, seen.get(sc)} != {"delete_rule", "remove_tag"}:
                    self.assertNotIn(sc, seen, f"{key} and {seen.get(sc)} share {sc}")
                seen[sc] = key
        menu_only = {k for k, a in self.win.commands.items() if a.shortcut().isEmpty()}
        self.assertEqual(menu_only, {"read_scans", "test_rules", "tagscript", "recover", "export",
                                     "zoom", "main_window", "report", "about", "undo_selected", "show_job_albums",
                                     "setup",                             # (the Setup page itself: Alt+7)
                                     "exclude_again", "change_watch", "process_again", "show_plan", "apply_all_plans",
                                     "dismiss_plan", "edit_rule", "run_in_background", "cover_link",
                                     "suggested_cover", "add_to_collection", "submit_ratings",
                                     "submit_acoustids",                  # (sends: menu only, DESIGN.md)
                                     "rate_none", "rate_1", "rate_2", "rate_3", "rate_4", "rate_5"})  # (the stars: 0-5)
        for key, sc in (("use_suggested", "Ctrl+Alt+U"), ("choose_cover", "Ctrl+Alt+C"), ("show_log", "Ctrl+Shift+Y")):
            self.assertEqual(self.win.act(key).shortcut().toString(), sc, key)
        for key, sc in (("apply_plan", "Ctrl+Shift+Return"), ("new_rule", "Ctrl+Alt+N"),
                        ("run_checks", "Ctrl+Shift+D")):
            self.assertEqual(self.win.act(key).shortcut().toString(), sc, key)
        for key, sc in (("preview", "Ctrl+P"), ("apply", "Ctrl+Return"), ("cancel", "Ctrl+."), ("add_folder", "Ctrl+O"),
                        ("find", "Ctrl+F"), ("preferences", "Ctrl+,"), ("show_inspector", "Ctrl+Alt+I"),
                        ("undo_edit", "Ctrl+Z"), ("undo_job", "Ctrl+Alt+Z")):
            self.assertEqual(self.win.act(key).shortcut().toString(), sc, key)
        for i, (name, _c) in enumerate(self.gui.PAGES):
            self.assertEqual(self.win.act(f"page:{name}").shortcut().toString(), f"Alt+{i + 1}")

    def test_every_old_page_and_button_has_a_place(self):
        self.assertEqual([n for n, _c in self.gui.PAGES], ["Overview", "Needs attention", "History",
                                                          "Watched folders", "Jobs", "Profiles", "Setup"])
        for key in ("preview", "apply", "cancel", "read_scans", "process_anyway", "search_web", "open_folder",
                    "use_release", "retry", "undo_job", "recover", "check_now", "watch_folder", "new_profile",
                    "save_profile", "test_rules", "tagscript", "run_checks", "export", "preferences",
                    "dismiss_plan", "apply_all_plans", "show_plan", "show_job_albums", "change_watch",
                    "run_in_background"):
            self.assertIn(key, self.win.commands)
        prefs = self.win.preferences()
        self.assertEqual(list(prefs.panes), ["General", "Keys", "Account", "Network", "Files", "Advanced",
                                             "Plugins"])
        self.assertTrue(prefs.fields and prefs.checks is not None and prefs.anon is not None)

    def test_the_toolbar_has_one_prominent_button_and_it_is_apply(self):
        bar = self.win.toolbar
        prominent = [bar.widgetForAction(a) for a in bar.actions()
                     if bar.widgetForAction(a) is not None and bar.widgetForAction(a).property("role") == "primary"]
        self.assertEqual(len(prominent), 1)
        self.assertIs(prominent[0].defaultAction(), self.win.act("apply"))
        self.assertFalse(self.win.act("apply").isEnabled())          # nothing previewed: an outline
        self.assertFalse(self.win.act("cancel").isEnabled())         # Esc stays with fields and sheets

    def test_shortcuts_work_from_the_keyboard(self):
        QTest.keyClick(self.win, Qt.Key_3, Qt.AltModifier)
        settle(self.app)
        self.assertIs(self.win.stack.currentWidget(), self.win.pages["History"])
        self.assertFalse(self.win.inspector.isVisible())             # (nothing to inspect on History)
        QTest.keyClick(self.win, Qt.Key_1, Qt.AltModifier)
        settle(self.app)
        was = self.win.inspector.isVisible()
        QTest.keyClick(self.win, Qt.Key_I, Qt.ControlModifier | Qt.AltModifier)
        settle(self.app)
        self.assertNotEqual(self.win.inspector.isVisible(), was)
        QTest.keyClick(self.win, Qt.Key_I, Qt.ControlModifier | Qt.AltModifier)
        settle(self.app)
        self.assertEqual(self.win.inspector.isVisible(), was)
        QTest.keyClick(self.win, Qt.Key_F, Qt.ControlModifier)
        settle(self.app)
        self.assertIs(QApplication.focusWidget(), self.win.search_box)

    # ---- the sidebar ------------------------------------------------------
    def test_sidebar_headers_are_not_selectable_and_arrows_pass_over_them(self):
        nav = self.win.nav
        headers = [nav.item(i) for i in range(nav.count()) if nav.item(i).data(Qt.UserRole + 1)]
        self.assertEqual([h.text() for h in headers], ["Library", "Automation", "Setup"])
        for h in headers:
            self.assertFalse(h.flags() & Qt.ItemIsSelectable)
        self.win.go("History")
        nav.setFocus()
        QTest.keyClick(nav, Qt.Key_Down)
        settle(self.app)
        self.assertEqual(nav.currentItem().text(), "Watched folders")
        self.assertIs(self.win.stack.currentWidget(), self.win.pages["Watched folders"])
        QTest.keyClick(nav, Qt.Key_Up)
        self.assertEqual(nav.currentItem().text(), "History")
        self.win.go("Overview")
        QTest.keyClick(nav, Qt.Key_Up)                                # the "Library" header is above it
        self.assertEqual(nav.currentItem().text(), "Overview")

    def test_badges_count_and_are_read_out(self):
        self.win.set_badge("Needs attention", 3)
        it = self.win.nav.item(self.win.nav.row_of("Needs attention"))
        self.assertEqual(self.win.badge("Needs attention"), 3)
        self.assertEqual(it.text(), "Needs attention")
        self.assertEqual(it.data(Qt.AccessibleTextRole), "Needs attention, 3 albums")
        self.win.set_badge("Needs attention", 0)
        self.assertEqual(it.data(Qt.AccessibleTextRole), "Needs attention")
        # one rule on every page: the albums marked Unresolved under the folder, as Needs attention
        # lists them (not the preview's own count, which jumped once that page was opened)
        import tagmender
        marked = [{"folder": str(self.tmp / "m" / f"A{i}"), "codes": ["no_candidate"], "reason": "", "files": 1,
                   "mark": "Unresolved"} for i in range(3)]
        with mock.patch.object(tagmender, "_unresolved_albums", lambda path: marked):
            self.show_plan()
            end = time.time() + 10
            while time.time() < end and self.win.badge("Needs attention") != 3:
                settle(self.app, 2)
        self.assertEqual(self.win.badge("Needs attention"), 3)
        att = self.win.pages["Needs attention"]
        self.assertEqual(att.table.rowCount(), 3)                   # (the page lists what the badge counts)
        self.win.go("Needs attention")
        self.assertEqual(self.win.badge("Needs attention"), 3)

    # ---- the inspector -----------------------------------------------------
    def test_inspector_shows_a_ready_albums_evidence_and_tags_changes_first(self):
        ov = self.show_plan()
        a = self.select(ov, "ready", "exact")
        insp = self.win.inspector
        self.assertEqual(insp.pages.currentIndex(), 1)
        self.assertIn("Northern Lights", insp.name.text())
        self.assertEqual(insp.st_word.text(), "Ready")
        ev = insp.evidence.fields
        self.assertIn("97", ev["Score"].text())
        self.assertIn("Sure", ev["Score"].text())
        self.assertIn("barcode", ev["What matched"].text())
        self.assertIn(a["identification"]["release_id"], ev["Codes"].text())
        self.assertIn("Plan", ov.detail.toPlainText())                # the full preview, folded away
        t = insp.tags.table
        actions = [t.item(r, 0).data(Qt.UserRole) for r in range(t.rowCount())]
        changes = [x for x in actions if x in ("Added", "Changed", "Removed")]
        self.assertTrue(changes)
        self.assertEqual(actions[:len(changes)], changes, "changes are not listed first")
        for r in range(t.rowCount()):                                  # a symbol, the word read out
            self.assertFalse(t.item(r, 0).icon().isNull())              # (a Lucide icon, not a typed glyph)
            self.assertEqual(t.item(r, 0).data(Qt.AccessibleTextRole).split(",")[0], actions[r])
        self.assertEqual(t.horizontalScrollBarPolicy(), Qt.ScrollBarAlwaysOff)  # the New column always in view
        insp.show_tab("Artwork")
        end = time.time() + 10
        while time.time() < end and not insp.art.sides["new"][1].text():
            settle(self.app, 2)
        self.assertIn("1200 × 1200", insp.art.sides["new"][1].text())

    def test_inspector_explains_an_unresolved_album_in_words(self):
        import reasons
        ov = self.show_plan()
        self.select(ov, "unresolved")
        doubts = self.win.inspector.evidence.fields["Doubts"].text()
        self.assertIn(reasons.describe("no_candidate").capitalize(), doubts)
        self.assertIn(reasons.describe("low_similarity").capitalize(), doubts)
        self.assertIn("no_candidate", self.win.inspector.evidence.fields["Codes"].text())
        self.win.go("History")
        self.assertEqual(self.win.inspector.pages.currentIndex(), 0)   # the empty state
        self.assertIn("Select an album", self.win.inspector.empty.head.text())

    def test_needs_attention_feeds_the_inspector_and_the_album_menu(self):
        att = self.win.pages["Needs attention"]
        self.win.go("Needs attention")
        self.assertEqual(att.views.currentIndex(), 0)                 # empty state first
        att.show_rows([{"folder": str(self.tmp / "A" / "B"), "codes": ["incomplete_album"], "reason": "14 of 16",
                        "files": 14, "mark": "Unresolved"}])
        self.assertEqual(self.win.badge("Needs attention"), 1)
        self.assertFalse(self.win.act("retry").isEnabled())
        att.table.selectRow(0)
        settle(self.app)
        self.assertTrue(self.win.act("retry").isEnabled())
        self.assertFalse(self.win.act("use_release").isEnabled())
        self.assertIn("track count", self.win.inspector.evidence.fields["Doubts"].text())

    # ---- Preferences -------------------------------------------------------
    def test_preferences_open_once_and_dark_retints(self):
        QTest.keyClick(self.win, Qt.Key_Comma, Qt.ControlModifier)
        settle(self.app)
        first = self.win.prefs_window
        self.assertTrue(first is not None and first.isVisible())
        self.win.act("preferences").trigger()
        settle(self.app)
        self.assertIs(self.win.prefs_window, first)
        from gui.preferences import Preferences
        self.assertEqual(sum(isinstance(w, Preferences) and w.isVisible() for w in QApplication.topLevelWidgets()), 1)
        light = self.gui.icons.icon("check", "ink").pixmap(16, 16).toImage()
        g = first.general
        g.appearance.setCurrentIndex(g.appearance.findData("dark"))
        settle(self.app)
        self.assertTrue(self.gui.is_dark())
        dark = self.gui.icons.icon("check", "ink").pixmap(16, 16).toImage()
        self.assertNotEqual(light, dark, "the icons kept their light colour")
        self.assertLess(self.app.palette().color(QPalette.Window).lightness(), 128)
        g.appearance.setCurrentIndex(g.appearance.findData("light"))
        self.assertFalse(self.gui.is_dark())
        first.show_tab("Keys")
        self.assertEqual(first.windowTitle(), "Keys — TagMender Preferences")
        first.fields["fanart"].setText("SECRET")
        first.save()
        self.assertEqual(first.keys.saved.text(), "Saved 1 key.")

    # ---- sheets --------------------------------------------------------------
    def test_sheets_are_window_modal_and_never_block_offscreen(self):
        sheets = self.gui.sheets
        s = sheets.Sheet(self.win.pages["Jobs"], "Undo job 1?", "Puts back…", buttons=("Cancel", "Undo Job"))
        self.assertEqual(s.windowModality(), Qt.WindowModal)
        self.assertIs(s.parentWidget(), self.win)
        self.assertEqual(s.default_button().text(), "Undo Job")
        s.deleteLater()
        for dlg in (self.gui.WatchDialog(self.win.pages["Watched folders"]),
                    self.gui.RuleDialog(self.win.pages["Profiles"])):
            self.assertEqual(dlg.windowModality(), Qt.WindowModal, dlg)
            self.assertIs(dlg.parentWidget(), self.win)
            dlg.deleteLater()
        t0 = time.time()
        sheets.warning(self.win, "Something went wrong.", "Details")
        self.assertFalse(sheets.question(self.win, "Close anyway?"))
        self.assertEqual(sheets.get_text(self.win, "Name"), ("", False))
        self.assertFalse(sheets.confirm(self.win, "Undo?", "x", "y", "Undo Job"))
        self.assertLess(time.time() - t0, 5)
        self.assertEqual([k for k, _h, _b in sheets.shown], ["warning", "question", "get_text", "confirm"])

    def test_a_confirmation_names_its_action_and_the_undo(self):
        sheets = self.gui.sheets
        seen = {}

        def run(s, kind="sheet"):
            seen["default"] = s.default_button().text()
            seen["body"] = s.body_text
            seen["modal"] = s.windowModality()
            s.default_button().click()
            return s.result()
        with mock.patch.object(sheets, "blocking", lambda: True), mock.patch.object(sheets, "run", run):
            ov = self.show_plan()
            ov.plan["profile_data"]["unattended"] = False
            with mock.patch.object(self.gui.overview, "start") as started:
                ov.apply()
        started.assert_called_once()
        self.assertEqual(seen["default"], "Apply 3 Albums")
        self.assertIn("undone", seen["body"])
        self.assertEqual(seen["modal"], Qt.WindowModal)

    # ---- icons ---------------------------------------------------------------
    def test_every_icon_is_there_and_licensed(self):
        d = self.gui.icons.DIR
        self.assertIn("ISC", (d / "LICENSE").read_text())
        for name in self.gui.icons.NAMES:
            text = (d / f"{name}.svg").read_text()
            self.assertNotIn("<script", text)
            self.assertNotIn("href", text)
            self.assertFalse(self.gui.icons.icon(name, "ink").isNull(), name)
        self.assertTrue(self.gui.icons.icon("no-such-icon").isNull())   # missing: empty, never a crash

    # ---- accessible names ---------------------------------------------------
    def test_every_interactive_widget_has_an_accessible_name(self):
        prefs = self.win.preferences()
        roots = [self.win, prefs, self.gui.WatchDialog(self.win.pages["Watched folders"]),
                 self.gui.RuleDialog(self.win.pages["Profiles"])]
        missing = []
        for root in roots:
            for w in interactive(root):
                text = w.text() if isinstance(w, QAbstractButton) else ""
                if not (w.accessibleName() or text.strip()):
                    missing.append(f"{type(w).__name__} in {type(w.parentWidget()).__name__} "
                                   f"{w.parentWidget().objectName()!r} ({w.toolTip()[:40]!r} {w.objectName()!r})")
        self.maxDiff = None
        self.assertEqual(missing, [])

    # ---- 960 x 600 -----------------------------------------------------------
    def test_the_window_fits_960_by_600_without_clipped_controls(self):
        import profiles as pf
        pf.save(pf.from_dict({"name": "Wide"}))
        self.show_plan()
        self.win.resize(960, 600)
        self.win.inspector.show()
        settle(self.app, 30)
        self.assertEqual((self.win.width(), self.win.height()), (960, 600))
        self.assertLessEqual(self.win.toolbar.sizeHint().width(), 960)
        self.assertGreaterEqual(self.win.stack.width(), 440)
        clipped = []
        for name, _c in self.gui.PAGES:
            self.win.go(name)
            settle(self.app, 5)
            page = self.win.pages[name]
            if name == "Profiles":                                    # (its settings form, not the rules)
                page.reload()
                page.list.setCurrentRow(page.list.count() - 1)
                page.tabs.setCurrentIndex(0)
                settle(self.app, 5)
            for sc in page.findChildren(QScrollArea):
                if sc.isVisibleTo(page) and sc.horizontalScrollBarPolicy() == Qt.ScrollBarAlwaysOff and \
                        sc.widget().minimumSizeHint().width() > sc.viewport().width():
                    clipped.append(f"{name}: {sc.accessibleName() or 'a scroll area'} needs "
                                   f"{sc.widget().minimumSizeHint().width()} px, has {sc.viewport().width()}")
            if page.minimumSizeHint().width() > self.win.stack.width():
                clipped.append(f"{name}: needs {page.minimumSizeHint().width()} px")
            for w in interactive(page):
                if not w.isVisibleTo(page) or isinstance(w, QAbstractItemView):
                    continue
                right = w.mapTo(page, w.rect().topRight()).x()
                inside_scroll = any(isinstance(p, QScrollArea) for p in self._parents(w, page))
                if right > page.width() + 1 or (w.width() < w.minimumSizeHint().width() - 1 and not inside_scroll):
                    clipped.append(f"{name}: {type(w).__name__} {getattr(w, 'text', lambda: '')()!r} "
                                   f"{w.width()} < {w.minimumSizeHint().width()}")
        self.assertEqual(clipped, [])

    @staticmethod
    def _parents(w, stop):
        p = w.parentWidget()
        while p is not None and p is not stop:
            yield p
            p = p.parentWidget()

    # ---- HIG round r1 -----------------------------------------------------------
    def rows(self):
        import gui_screens
        return gui_screens.fixed_rows(self.tmp)

    def test_the_inspector_steps_aside_on_pages_without_albums_and_comes_back(self):
        self.show_plan()
        self.win.inspector_wanted = True
        self.win.place_inspector()
        settle(self.app)
        self.assertTrue(self.win.inspector.isVisible())
        for name in ("History", "Watched folders", "Jobs", "Profiles"):
            self.win.go(name)
            settle(self.app)
            self.assertFalse(self.win.inspector.isVisible(), name)
            self.assertFalse(self.win.act("show_inspector").isEnabled(), name)
        self.assertTrue(self.gui.common._gui_prefs().get("inspector", True))  # (not the person's choice)
        self.win.go("Needs attention")
        settle(self.app)
        self.assertTrue(self.win.inspector.isVisible())
        self.assertIsNone(self.win.inspector.titleBarWidget().layout())        # no Qt title strip
        self.assertTrue(self.win.inspector.titleBarWidget().sizeHint().height() <= 0)

    def test_preview_and_apply_act_on_overview_only(self):
        self.show_plan()
        self.assertTrue(self.win.act("apply").isEnabled())
        self.assertTrue(self.win.act("preview").isEnabled())
        self.win.go("Watched folders")
        settle(self.app)
        self.assertFalse(self.win.act("apply").isEnabled())
        self.assertFalse(self.win.act("preview").isEnabled())
        self.win.go("Overview")
        self.assertTrue(self.win.act("apply").isEnabled())

    def test_no_second_folder_while_work_runs(self):
        ov = self.show_plan()
        ov.busy(True)
        try:
            self.assertFalse(ov.choose_btn.isEnabled())
            was = ov.path
            with mock.patch.object(type(ov), "preview") as again:
                ov.set_path(str(self.tmp / "elsewhere"))
                self.win.pages["Needs attention"].show_rows(self.rows()[4])
                self.win.pages["Needs attention"].table.selectRow(0)
                self.win.pages["Needs attention"].retry()
            again.assert_not_called()
            self.assertEqual(ov.path, was)
            from PySide6.QtCore import QMimeData, QPointF, QUrl
            from PySide6.QtGui import QDragEnterEvent
            md = QMimeData()
            md.setUrls([QUrl.fromLocalFile(str(self.tmp))])
            e = QDragEnterEvent(QPointF(10, 10).toPoint(), Qt.CopyAction, md, Qt.LeftButton, Qt.NoModifier)
            self.win.dragEnterEvent(e)                               # taken, and the drop says why nothing
            self.assertTrue(e.isAccepted())                          # happens (not a bare no-drop cursor)
            self.assertEqual(self.win.statusBar().currentMessage(), "Finish or cancel the current work first.")
            self.assertFalse(self.win.act("add_folder").isEnabled())  # (Ctrl+O: no folder picker either)
            self.assertFalse(self.win.search_box.acceptDrops())      # (a folder on the search field is no text)
        finally:
            ov.busy(False)

    def test_a_window_drop_takes_folders_and_music_never_images_or_links(self):
        from PySide6.QtCore import QMimeData, QPoint, QUrl
        from PySide6.QtGui import QDropEvent
        cover = self.tmp / "cover.jpg"
        cover.write_bytes(b"x")
        song = self.tmp / "01.flac"
        song.write_bytes(b"x")

        def drop(*urls):
            md = QMimeData()
            md.setUrls(list(urls))
            with mock.patch.object(type(self.win.pages["Overview"]), "set_path") as sp:
                self.win.dropEvent(QDropEvent(QPoint(5, 5), Qt.CopyAction, md, Qt.LeftButton, Qt.NoModifier))
            return [c.args[0] for c in sp.call_args_list]
        self.assertEqual(drop(QUrl.fromLocalFile(str(cover))), [])
        self.assertEqual(drop(QUrl("https://example.org/cover.jpg")), [])
        self.assertEqual(drop(QUrl.fromLocalFile(str(self.tmp))), [str(self.tmp)])
        self.assertEqual(drop(QUrl.fromLocalFile(str(song))), [str(song)])

    def test_the_summary_and_columns_speak_in_words(self):
        ov = self.show_plan()
        text = ov.summary.text()
        self.assertTrue(text.startswith("3 Ready"), text)                  # what can be applied first
        for word in ("Up to date", "Changed since preview"):
            self.assertIn(word, text)
        for key in ("unchanged", "conflict"):
            self.assertNotIn(key, text)
        changes = [ov.model.data(ov.model.index(r, 4)) for r in range(ov.model.rowCount())]
        self.assertIn("11 tracks, stay in their folder", changes)          # (0.8.4: what Apply will do)
        self.assertFalse([c for c in changes if c and "(s)" in c])
        why = [ov.model.data(ov.model.index(r, 5)) for r in range(ov.model.rowCount())
               if ov.model.albums[r]["status"] == "unresolved"]
        self.assertTrue(why and "_" not in why[0], why)                    # no reason codes
        # (0.8.6: a column you drag wider than the list can be scrolled to)
        self.assertEqual(ov.table.horizontalScrollBarPolicy(), Qt.ScrollBarAsNeeded)

    def test_needs_attention_names_reasons_and_folders_plainly(self):
        att = self.win.pages["Needs attention"]
        self.win.go("Needs attention")
        att.show_rows(self.rows()[4])
        self.assertFalse(any(b.isEnabled() for b in att.buttons))         # nothing selected
        cells = {att.table.item(r, 1).text().split(" — ")[0]: att.table.item(r, 0) for r in range(att.table.rowCount())}
        self.assertEqual(cells["CD1"].text(), "Not found")                # a word, and a Lucide icon (no glyph)
        self.assertFalse(cells["CD1"].icon().isNull())
        self.assertEqual(cells["Rarities"].text(), "Track count differs and 1 more reason")
        att.table.selectRow(0)
        settle(self.app)
        self.assertTrue(all(b.isEnabled() for b in att.buttons))
        self.assertTrue(att.folder().startswith(str(self.tmp)))            # (the whole path behind the name)

    def test_jobs_say_their_status_in_words_and_undo_on_a_worker(self):
        import executor
        jobs = self.rows()[0]
        page = self.win.pages["Jobs"]
        with mock.patch.object(executor, "jobs", lambda limit=50: jobs[:limit]), \
                mock.patch.object(executor, "pending_jobs", lambda: []):
            page.reload()
            # (the newest job had a failed album: not a green "Done")
            self.assertEqual(page.table.item(0, 1).text(), "Done with errors")
            self.assertEqual(page.table.item(0, 1).foreground().color(), self.gui.style.status_color("unresolved"))
            self.assertEqual(page.table.item(1, 1).text(), "Done")
            self.assertEqual(page.table.item(2, 1).text(), "Undone")
            self.assertFalse(page.table.item(2, 1).icon().isNull())
            self.assertIn("8 Done", page.table.item(0, 3).text())
            self.assertFalse(page.out.isVisibleTo(page))                  # no empty box under the list
            self.assertFalse(page.undo_btn.isEnabled())
            self.win.go("Jobs")
            page.table.selectRow(0)
            settle(self.app)
            self.assertTrue(page.undo_btn.isEnabled())
            self.assertEqual(page.job(), jobs[0][0])
            with mock.patch.object(executor, "job_albums", lambda job: []), \
                    mock.patch.object(self.gui.sheets, "confirm", return_value=True), \
                    mock.patch.object(executor, "undo", lambda job: ["put back 01.flac"]):
                page.undo()
                self.assertTrue(page.working)
                self.assertFalse(self.win.act("undo_job").isEnabled())
                end = time.time() + 10
                while time.time() < end and page.working:
                    settle(self.app, 2)
            self.assertIn("put back 01.flac", page.out.toPlainText())
            self.assertTrue(page.out.isVisibleTo(page))
            ov = self.win.pages["Overview"]
            ov.running = True                                             # an Apply in progress
            with mock.patch.object(self.gui.sheets, "confirm") as asked:
                page.undo(jobs[0][0])
            ov.running = False
            asked.assert_not_called()

    def test_waiting_plans_are_confirmed_and_never_applied_twice(self):
        import watcher
        pending = self.rows()[2]
        w = self.win.pages["Watched folders"]
        with mock.patch.object(watcher, "pending_plans", lambda: pending), \
                mock.patch.object(watcher, "apply_pending") as applied, \
                mock.patch.object(watcher, "dismiss_pending") as dismissed:
            w.reload()
            self.win.go("Watched folders")
            self.assertFalse(self.win.act("apply_plan").isEnabled())
            w.pending.selectRow(0)
            settle(self.app)
            self.assertTrue(self.win.act("apply_plan").isEnabled())
            w.apply_one()                                                 # offscreen: the sheet says no
            w.dismiss()
            applied.assert_not_called()
            dismissed.assert_not_called()
            kinds = [h for _k, h, _b in self.gui.sheets.shown]
            self.assertIn("Apply the waiting changes to Harbour Lights - Ferry?", kinds)
            self.assertIn("Dismiss the waiting changes for Harbour Lights - Ferry?", kinds)
            w.applying = True
            w.update_buttons()
            self.assertFalse(self.win.act("apply_plan").isEnabled() or w.plan_btns["all"].isEnabled())
            w._apply([pending[0]])                                        # (a second click while one runs)
            applied.assert_not_called()
            w.applying = False

    def test_unsaved_profile_changes_are_asked_about(self):
        import profiles as pf
        pf.save(pf.from_dict({"name": "Alpha", "description": "a"}))
        pf.save(pf.from_dict({"name": "Beta", "description": "b"}))
        pr = self.win.pages["Profiles"]
        self.win.go("Profiles")
        pr.reload()
        names = [pr.list.item(i).text() for i in range(pr.list.count())]
        self.assertEqual(names[0], "Global rules")
        self.assertTrue(pr.list.item(0).font().italic())                  # (set apart from the profiles)
        pr.list.setCurrentRow(names.index("Alpha"))
        settle(self.app)
        self.assertFalse(pr.dirty())
        pr.desc.setText("changed")
        self.assertTrue(pr.dirty() and pr.save_btn.isEnabled() and self.win.isWindowModified())
        with mock.patch.object(self.gui.sheets, "ask", return_value="Cancel"):
            pr.list.setCurrentRow(names.index("Beta"))
        self.assertEqual(pr.list.currentItem().text(), "Alpha")           # Cancel: stays, edits kept
        self.assertEqual(pr.desc.text(), "changed")
        with mock.patch.object(self.gui.sheets, "ask", return_value="Save"):
            pr.list.setCurrentRow(names.index("Beta"))
        self.assertEqual(pf.load("Alpha")["description"], "changed")
        self.assertEqual(pr.desc.text(), "b")
        self.assertFalse(self.win.isWindowModified())
        self.win.search_box.setText("alp")
        self.assertTrue(pr.list.item(names.index("Beta")).isHidden())
        self.assertFalse(pr.list.item(names.index("Alpha")).isHidden())
        self.win.search_box.setText("")
        self.assertEqual(pr.op.currentText(), "Fix in place")             # words, the key behind them
        self.assertEqual(pr.op.currentData(), "in_place")

    def test_tab_goes_from_the_sidebar_into_the_page_shown(self):
        for name in ("History", "Jobs", "Overview"):
            self.win.go(name)
            self.win.nav.setFocus()
            settle(self.app)
            QTest.keyClick(self.win.nav, Qt.Key_Tab)
            settle(self.app)
            f = QApplication.focusWidget()
            self.assertTrue(f is not None and self.win.pages[name].isAncestorOf(f), (name, f))
        chain = self.win._chain(self.win)
        ins = [w for w in chain if self.win.inspector.isAncestorOf(w)]
        self.assertTrue(ins)
        self.assertGreater(chain.index(ins[0]), chain.index(self.win.nav))   # the inspector after the pages
        self.assertEqual(self.win.act("cancel").shortcut().toString(), "Ctrl+.")  # Esc stays with fields

    def test_controls_are_drawn_so_they_read_as_what_they_are(self):
        css = self.gui.style.stylesheet()
        for rule in ("QCheckBox::indicator", "QAbstractItemView::indicator", "QComboBox::down-arrow",
                     "QScrollBar::handle", "QToolButton[role=\"tool\"]:focus", "QAbstractSpinBox:focus",
                     "QTabBar::tab:focus", "QDialog[sheet=\"true\"]"):
            self.assertIn(rule, css)
        import re
        files = re.findall(r"url\(([^)]+)\)", css)
        self.assertTrue(files)
        self.assertTrue(all(Path(f).is_file() for f in files), files)
        for b in self.win.inspector.buttons.values():
            self.assertEqual(b.property("role"), "tool")
        rel = self.win.inspector.buttons["release"]                     # (one control, its arrow inside)
        self.assertEqual(rel.text(), "Change Release")
        self.assertEqual(rel.popupMode(), rel.ToolButtonPopupMode.InstantPopup)
        self.assertIn("menu-indicator", rel.styleSheet())
        self.assertFalse(any(a.isIconVisibleInMenu() for a in self.win.commands.values()))

    def test_preferences_keep_watching_follows_the_watched_page_both_ways(self):
        prefs = self.win.preferences()
        live = self.win.pages["Watched folders"].live
        with mock.patch.object(type(self.win.pages["Watched folders"]), "check_now"):
            prefs.general.watch.setChecked(True)
            self.assertTrue(live.isChecked())
            live.setChecked(False)
            self.assertFalse(prefs.general.watch.isChecked())
        keys = prefs.keys
        self.assertEqual(keys.fields["discogs"].accessibleName(), "Discogs token")
        self.assertIn(keys.fields["discogs"].placeholderText(), ("Not set", "Saved on this computer",
                                                                "Set by the environment"))

    def test_the_last_page_and_inspector_tab_come_back(self):
        self.win.go("Jobs")
        self.win.go("Overview")
        self.win.inspector.show_tab("Evidence")
        self.win.go("History")
        settle(self.app)
        again = self.gui.Main()
        try:
            self.assertIs(again.stack.currentWidget(), again.pages["History"])
            self.assertEqual(again.inspector.tabs.currentIndex(), self.gui.inspector.TABS.index("Evidence"))
        finally:
            again.close()

    def test_exclude_again_undoes_process_anyway(self):
        import exclusions as ex
        ov = self.show_plan()
        a = self.select(ov, "excluded")
        ov.process_anyway()
        self.assertTrue(ov.overridden(a))
        self.assertTrue(self.win.act("exclude_again").isEnabled())
        self.assertIn("Exclude Again", ov.summary.text())
        ov.exclude_again()
        self.assertFalse(ov.overridden(a))
        self.assertFalse({str(p) for p in ex.State().overrides()} & ov._album_paths(a))

    # ---- HIG round r2 -----------------------------------------------------------
    def test_every_menu_item_has_its_own_underlined_letter(self):
        plain = self.gui.actions.plain
        for title, m in self.win.menus.items():
            letters = {}
            for a in m.actions():
                if a.isSeparator():
                    continue
                t = a.text().replace("&&", "")
                self.assertEqual(t.count("&"), 1, f"{title} > {a.text()!r}: no underlined letter")
                ch = t[t.index("&") + 1].lower()
                self.assertNotIn(ch, letters, f"{title}: {a.text()!r} and {letters.get(ch)!r} share Alt+{ch}")
                letters[ch] = a.text()
        bar = self.win.toolbar                                            # (no "&" where the words are reused)
        for a in bar.actions():
            b = bar.widgetForAction(a)
            if b is not None and hasattr(b, "text"):
                self.assertNotIn("&", b.text() + b.accessibleName())
        for a in self.win.commands.values():
            self.assertNotIn("&", self.gui.actions.accessible(a) + a.toolTip())
        self.assertNotIn("&", self.gui.actions.shortcut_text(self.win.commands))
        self.assertEqual(plain("Rock &&Roll"), "Rock &Roll")

    def test_the_prominent_button_reads_as_one_piece_and_shows_its_focus(self):
        style = self.gui.style
        self.assertEqual(self.win.act("apply").property("icon_role"), "on_accent")
        icon = self.win.act("apply").icon().pixmap(16, 16).toImage()
        drawn = [icon.pixelColor(x, y) for x in range(16) for y in range(16) if icon.pixelColor(x, y).alpha() > 200]
        self.assertTrue(drawn)
        self.assertTrue(all(c.lightness() > 200 for c in drawn), "the check is not drawn in on_accent (white)")
        css = style.stylesheet()
        self.assertIn('QPushButton[role="primary"]:focus', css)
        self.assertIn('QToolBar#toolbar QToolButton[role="primary"]:focus', css)
        for dark in (False, True):
            t = style.DARK if dark else style.LIGHT
            self.assertGreaterEqual(style.contrast(t["focus_ring"], t["accent"]), 3.0, dark)
            self.assertGreaterEqual(style.contrast(t["on_selection"], t["selection"]), 4.5, dark)
        self.assertNotEqual(style.DARK["selection"], style.DARK["accent"])   # (a deeper fill in dark)

    def test_tables_title_their_columns_over_their_start_and_read_from_it(self):
        self.show_plan()
        views = [w for p in self.win.pages.values() for w in p.findChildren(QAbstractItemView)
                 if type(w).__name__ in ("QTableView", "QTableWidget") and w.objectName() != "qt_calendar_calendarview"]
        views.append(self.win.inspector.tags.table)
        for v in views:
            if v.model().columnCount() == 0:
                continue
            al = v.model().headerData(1 if v.model().columnCount() > 1 else 0, Qt.Horizontal, Qt.TextAlignmentRole)
            al = Qt.AlignmentFlag(al) if al is not None else v.horizontalHeader().defaultAlignment()
            self.assertTrue(al & Qt.AlignLeft, f"{v.accessibleName()}: header centred")
        for name in ("Needs attention", "History", "Jobs"):
            t = self.win.pages[name].table
            self.assertEqual(t.textElideMode(), Qt.ElideRight, name)       # (prose, not paths)
        self.assertEqual(self.win.inspector.tags.table.textElideMode(), Qt.ElideRight)
        self.assertIn("QTableView::item", self.gui.style.stylesheet())    # (cells under the header's padding)

    def test_status_cells_carry_an_icon_and_a_word_never_a_glyph(self):
        ov = self.show_plan()
        m = ov.model
        for r in range(m.rowCount()):
            text = m.data(m.index(r, 0))
            self.assertFalse(set(text) & set("●✓!⊘✕↶"), text)
            self.assertFalse(m.data(m.index(r, 0), Qt.DecorationRole).isNull())
        partial = next(r for r in range(m.rowCount()) if (m.albums[r]["identification"] or {}).get("tier") == "partial")
        self.assertIn("pressing", m.data(m.index(partial, 0), Qt.ToolTipRole))

    def test_the_summary_says_what_the_rows_say(self):
        ov = self.show_plan()
        rows = {ov.model.data(ov.model.index(r, 0)).split(" · ")[0] for r in range(ov.model.rowCount())}
        said = {part.split(" ", 1)[1] for part in ov.summary.text().split(", ")}
        self.assertEqual(said, rows)
        self.assertNotIn("Excluded", ov.summary.text())
        self.assertIn("1 Ignored", ov.summary.text())

    def test_evidence_speaks_in_words(self):
        ov = self.show_plan()
        self.select(ov, "ready", "exact")
        ev = self.win.inspector.evidence.fields
        self.assertIn("Catalog Number", ev["Where each tag came from"].text())
        self.assertNotIn("catalognumber", ev["Where each tag came from"].text())
        self.assertNotIn("margin", ev["Score"].text())
        self.assertIn("21 points ahead", ev["Score"].text())
        self.win.inspector.show_row({"folder": str(self.tmp / "A" / "B"), "codes": ["no_candidate"], "reason": ""})
        self.assertIn("marked Unresolved by an earlier run", ev["Score"].text())
        self.assertEqual(ev["Source"].text(), "—")

    def test_a_narrow_inspector_shows_new_under_original_whole(self):
        ov = self.show_plan()
        self.select(ov, "ready", "exact")
        tags = self.win.inspector.tags
        tags.resize(300, 400)
        tags.fill()
        t = tags.table
        self.assertTrue(t.isColumnHidden(3))
        row = next(r for r in range(t.rowCount()) if t.item(r, 1).text() == "Date")
        self.assertEqual(t.item(row, 2).text(), "(none)\n→ 1998-03-02")
        self.assertIn("New: 1998-03-02", t.item(row, 2).toolTip())
        tags.resize(420, 400)
        tags.fill()
        self.assertFalse(t.isColumnHidden(3))
        self.assertEqual(t.item(row, 3).text(), "1998-03-02")
        self.assertLessEqual(t.columnWidth(1), self.gui.inspector.TAG_COL)

    def test_the_watched_page_speaks_in_words(self):
        import watcher
        w = self.win.pages["Watched folders"]
        recent = self.rows()[3] + [(str(self.tmp / "inbox" / "Broken"), "Inbox", "error", time.time(), ""),
                                   (str(self.tmp / "inbox" / "Later"), "Inbox", "provider_error", time.time(), "")]
        with mock.patch.object(watcher, "recent", lambda limit=50: recent[:limit]), \
                mock.patch.object(watcher, "pending_plans", lambda: self.rows()[2]):
            w.reload()
        words = [w.recent.item(r, 2).text() for r in range(w.recent.rowCount())]
        self.assertIn("Done", words)
        self.assertIn("Failed", words)
        for key in ("done", "error", "provider_error", "ignored"):
            self.assertNotIn(key, words)
        self.assertFalse(w.recent.item(0, 2).icon().isNull())
        self.assertEqual(w.recent.item(0, 1).text().split(" — ")[0], "Old Pines - Bark")
        self.assertEqual(w.pending.item(0, 0).text().split(" — ")[0], "Harbour Lights - Ferry")
        text = self.gui.watched.outcome_text([("/x/Ferry", "done"), ("/x/Bark", "error")])
        self.assertTrue(text.startswith("1 Done, 1 Failed: Ferry"), text)
        self.assertNotIn(": /x", text)
        w.show_service("not installed")
        self.assertEqual(w.svc_label.text(), "Not set up to run in the background")
        a = self.win.act("run_in_background")
        self.assertTrue(a.isEnabled())
        w.show_service("running")
        self.assertEqual(self.gui.actions.plain(a.text()), "Stop the Background Watcher…")
        w.show_service(None)
        self.assertFalse(a.isEnabled())
        self.assertIn(a, self.win.menus["Watched Folders"].actions())       # (Tools > Watched Folders)
        w.pending.selectRow(0)
        self.gui.sheets.shown.clear()
        w.show_plan()
        self.assertEqual(self.gui.sheets.shown[-1][1], "What changes for Harbour Lights - Ferry")

    def test_undo_last_job_skips_a_job_that_cannot_be_undone(self):
        import executor
        now = time.time()
        jobs = [("20261031-120000-zz99", "Library", now - 60, now - 30, "undone", '{"done": 2}'),
                ("20261031-110000-yy88", "Inbox", now - 3600, now - 3500, "done", '{"done": 4}')]
        page = self.win.pages["Jobs"]
        with mock.patch.object(executor, "jobs", lambda limit=50: jobs[:limit]), \
                mock.patch.object(executor, "pending_jobs", lambda: []), \
                mock.patch.object(executor, "job_albums", lambda job: [("a", "done", "", "", 0)] * 4), \
                mock.patch.object(self.gui.sheets, "confirm", return_value=False) as asked:
            page.reload()
            self.win.go("Overview")
            self.win.act("undo_job").trigger()                            # Ctrl+Z on another page
            settle(self.app)
            self.assertIs(self.win.stack.currentWidget(), page)           # (its outcome in view)
            head = asked.call_args[0][1]
            self.assertIn("(Inbox)", head)
            self.assertIn("4 albums", head)
            self.assertNotIn("zz99", head)
            self.assertNotIn("yy88", head)                                # (no job ID in the headline)
            page.table.selectRow(0)                                       # the undone job
            settle(self.app)
            self.assertFalse(page.undo_btn.isEnabled())
            self.assertFalse(self.win.act("undo_selected").isEnabled())
            self.assertIn("already undone", page.undo_btn.toolTip())
            page.table.selectRow(1)
            settle(self.app)
            self.assertTrue(page.undo_btn.isEnabled())
        jobs[1] = jobs[1][:4] + ("undone",) + jobs[1][5:]
        with mock.patch.object(executor, "jobs", lambda limit=50: jobs[:limit]), \
                mock.patch.object(executor, "pending_jobs", lambda: []), \
                mock.patch.object(self.gui.sheets, "confirm") as asked, \
                mock.patch.object(self.gui.sheets, "information") as told:
            page.reload()
            page.undo_last()
        asked.assert_not_called()
        self.assertEqual(told.call_args[0][1], "There is no job to undo.")
        plain = self.gui.jobs.plain_error
        said = plain("job 1 did not finish; run `tagmender recover 1` first")
        self.assertIn("Recover Interrupted Jobs", said)
        self.assertNotIn("`", said)
        self.assertEqual(plain("job 1 was already undone"), "This job was already undone.")

    def test_menu_key_opens_a_tables_menu_on_the_current_row(self):
        from PySide6.QtCore import QPoint
        import executor
        page = self.win.pages["Jobs"]
        with mock.patch.object(executor, "jobs", lambda limit=50: self.rows()[0][:limit]), \
                mock.patch.object(executor, "pending_jobs", lambda: []):
            page.reload()
            self.win.go("Jobs")
            page.table.selectRow(1)
            settle(self.app)
            with mock.patch.object(self.gui.common, "QMenu") as menu:     # (a real one would wait for a click)
                page.context_menu(QPoint(-50, -50))                       # (not on a row: the Menu key)
            menu.return_value.exec.assert_called_once()
            page.table.clearSelection()
            with mock.patch.object(self.gui.common, "QMenu") as menu:
                page.context_menu(QPoint(-50, -50))
            menu.return_value.exec.assert_not_called()
        att = self.win.pages["Needs attention"]
        self.assertEqual(att.table.contextMenuPolicy(), Qt.CustomContextMenu)

    def test_commands_act_only_where_they_can(self):
        import exclusions as ex
        import profiles as pf
        pf.save(pf.from_dict({"name": "Gamma"}))
        ex.save_rule(ex.Rule.from_dict({"id": "g1", "name": "Karaoke", "action": "ignore", "target": "album",
                                        "when": {"all": [{"field": "album", "op": "contains", "value": "karaoke"}]}}),
                     "global")
        self.win.go("Jobs")
        self.assertFalse(self.win.profile_box.isEnabled())               # (the preview's profile: Overview only)
        self.win.go("Overview")
        self.assertTrue(self.win.profile_box.isEnabled())
        pr = self.win.pages["Profiles"]
        self.win.go("Profiles")
        pr.reload()
        pr.list.setCurrentRow([pr.list.item(i).text() for i in range(pr.list.count())].index("Gamma"))
        pr.tabs.setCurrentIndex(1)
        settle(self.app)
        row = next(r for r in range(pr.rules.rowCount()) if pr.rules.item(r, 1).text() == "Karaoke")
        self.assertFalse(pr.rules.item(row, 0).flags() & Qt.ItemIsUserCheckable)   # (global: read-only here)
        pr.rules.selectRow(row)
        settle(self.app)
        self.assertFalse(self.win.act("delete_rule").isEnabled())
        pr.list.setCurrentRow(0)                                          # Global rules: its own
        pr.tabs.setCurrentIndex(1)
        settle(self.app)
        row = next(r for r in range(pr.rules.rowCount()) if pr.rules.item(r, 1).text() == "Karaoke")
        self.assertTrue(pr.rules.item(row, 0).flags() & Qt.ItemIsUserCheckable)
        pr.rules.selectRow(row)
        settle(self.app)
        self.assertTrue(self.win.act("delete_rule").isEnabled())
        self.gui.sheets.shown.clear()
        pr.remove_rule()
        self.assertEqual(self.gui.sheets.shown[-1][1], "Remove the global rule “Karaoke”?")
        self.assertIn("Every profile", self.gui.sheets.shown[-1][2])
        pr.list.setCurrentRow([pr.list.item(i).text() for i in range(pr.list.count())].index("Gamma"))
        pr.tabs.setCurrentIndex(0)                                        # Settings: Del removes no rule
        settle(self.app)
        self.assertFalse(self.win.act("delete_rule").isEnabled())
        self.assertEqual(self.gui.actions.plain(self.win.act("delete_rule").text()), "Remove Rule…")
        self.assertIn("Remove Rule…", [b.text() for b in pr.rule_btns.values()])
        self.assertEqual(pr.picard_btn.text(), "Edit Naming Script…")
        self.assertEqual(pr.editor.label_of("operation"), "What happens to files")

    def test_remove_condition_removes_the_one_chosen(self):
        dlg = self.gui.RuleDialog(self.win.pages["Profiles"])
        for v in ("b", "c"):
            dlg.add_cond({"field": "album", "op": "contains", "value": v})
        dlg.conds.cellWidget(0, 2).setText("a")
        dlg.conds.cellWidget(1, 4).click()                                # the "−" of the middle row
        self.assertEqual([dlg.conds.cellWidget(r, 2).text() for r in range(dlg.conds.rowCount())], ["a", "c"])
        self.assertEqual(dlg.conds.cellWidget(1, 4).accessibleName(), "Remove condition 2")
        dlg.deleteLater()

    def test_the_empty_overview_has_no_inspector_and_it_comes_back(self):
        self.win.inspector_wanted = True
        self.win.go("History")
        self.win.go("Overview")
        settle(self.app)
        ov = self.win.pages["Overview"]
        self.assertIs(ov.views.currentWidget(), ov.drop)
        self.assertFalse(self.win.inspector.isVisible())
        self.assertTrue(self.win.inspector_wanted)                        # (still the person's choice)
        self.show_plan()
        self.assertTrue(self.win.inspector.isVisible())

    def test_widths_squeezed_by_a_small_window_are_not_saved_as_chosen(self):
        prefs = self.gui.common._gui_prefs
        self.gui.common._set_gui_pref("sidebar_width", 220)
        self.gui.common._set_gui_pref("inspector_width", 320)
        self.show_plan()
        self.win.resize(960, 600)
        settle(self.app, 30)
        self.win.save_state()
        self.assertEqual((prefs()["sidebar_width"], prefs()["inspector_width"]), (220, 320))
        self.win.resize(1280, 800)
        settle(self.app, 30)
        self.assertEqual(self.win.nav.width(), 220)

    def test_the_sleeve_takes_music_only_and_a_left_click(self):
        from PySide6.QtCore import QMimeData, QPoint, QPointF, QUrl
        from PySide6.QtGui import QDragEnterEvent, QDropEvent, QMouseEvent
        from PySide6.QtCore import QEvent
        drop = self.win.pages["Overview"].drop
        cover = self.tmp / "cover.jpg"
        cover.write_bytes(b"x")
        (self.tmp / "one").mkdir(exist_ok=True)
        (self.tmp / "two").mkdir(exist_ok=True)

        def enter(*urls):
            md = QMimeData()
            md.setUrls(list(urls))
            e = QDragEnterEvent(QPoint(5, 5), Qt.CopyAction, md, Qt.LeftButton, Qt.NoModifier)
            drop.dragEnterEvent(e)
            return e.isAccepted()
        self.assertFalse(enter(QUrl.fromLocalFile(str(cover))))
        self.assertFalse(enter(QUrl("https://example.org/a.jpg")))
        self.assertTrue(enter(QUrl.fromLocalFile(str(self.tmp))))
        md = QMimeData()
        md.setUrls([QUrl.fromLocalFile(str(self.tmp / "one")), QUrl.fromLocalFile(str(self.tmp / "two"))])
        with mock.patch.object(type(self.win.pages["Overview"]), "set_path") as sp:
            drop.dropEvent(QDropEvent(QPoint(5, 5), Qt.CopyAction, md, Qt.LeftButton, Qt.NoModifier))
        # (both listed together, under the folder holding them; nothing is looked up yet)
        self.assertEqual([c.args[0] for c in sp.call_args_list], [str(self.tmp)])
        self.assertEqual(sorted(sp.call_args.kwargs["within"]), [str(self.tmp / "one"), str(self.tmp / "two")])
        with mock.patch.object(type(drop), "choose") as chose:
            for button in (Qt.RightButton, Qt.MiddleButton, Qt.LeftButton):
                for kind in (QEvent.MouseButtonPress, QEvent.MouseButtonRelease):
                    e = QMouseEvent(kind, QPointF(5, 5), QPointF(5, 5), button, button, Qt.NoModifier)
                    (drop.mousePressEvent if kind == QEvent.MouseButtonPress else drop.mouseReleaseEvent)(e)
        self.assertEqual(chose.call_count, 1)

    def test_preferences_reach_every_pane_by_tab_and_keep_typed_keys_in_mind(self):
        prefs = self.win.preferences()
        for a in prefs.bar.actions():
            self.assertEqual(prefs.bar.widgetForAction(a).focusPolicy(), Qt.TabFocus)
        prefs.show_tab("Checks")
        self.assertEqual(prefs.checks_pane.anon.text(), "Hide folder and file names in the export")
        prefs.show_tab("Keys")
        prefs.fields["fanart"].setText("TYPED")
        self.gui.sheets.shown.clear()
        prefs.close()                                                     # (offscreen: "Don't Save")
        self.assertEqual(self.gui.sheets.shown[-1][1], "Save the 1 key you typed?")
        self.assertEqual(prefs.fields["fanart"].text(), "")
        g = prefs.general.appearance
        self.assertEqual(g.sizeAdjustPolicy(), QComboBox.AdjustToContents)

    def test_export_runs_on_a_worker_and_says_where_it_went(self):
        import doctor
        out = str(self.tmp / "diag.zip")
        prefs = self.win.preferences()
        with mock.patch.object(self.gui.sheets, "save_file", return_value=out), \
                mock.patch.object(doctor, "export", lambda path, anonymize_paths=False: Path(path)) as _x, \
                mock.patch.object(self.gui.sheets, "information") as told:
            prefs.export()
            self.assertFalse(prefs.checks_pane.export_btn.isEnabled())
            end = time.time() + 10
            while time.time() < end and not told.called:
                settle(self.app, 2)
        self.assertEqual(told.call_args[0][1], "Saved diag.zip.")
        self.assertTrue(prefs.checks_pane.export_btn.isEnabled())

    def test_the_watch_sheet_asks_about_the_folder_off_the_ui_thread(self):
        import threading
        import profiles as pf
        pf.save(pf.from_dict({"name": "Watcher", "operation": "move", "destination": str(self.tmp / "lib")}))
        on_ui = []
        real = Path.is_dir

        def is_dir(p):
            if "nowhere" in str(p):
                on_ui.append(threading.current_thread() is threading.main_thread())
            return real(p)
        dlg = self.gui.WatchDialog(self.win.pages["Watched folders"])
        dlg.prof.setCurrentText("Watcher")
        with mock.patch.object(Path, "is_dir", is_dir):
            for ch in "/nowhere/inbox":
                QTest.keyClick(dlg.path, ch)
            self.assertEqual(on_ui, [])                                   # (not per keystroke)
            end = time.time() + 10
            while time.time() < end and "not there now" not in dlg.warn.text():
                settle(self.app, 2)
        self.assertIn("not there now", dlg.warn.text())
        self.assertEqual(on_ui, [False])                                  # once, on a worker
        dlg.deleteLater()

    def test_closing_mid_album_defaults_to_waiting(self):
        self.win._closing, self.win._closing_since = True, time.time() - 60
        with mock.patch.object(type(self.win), "threads", lambda w: [object()]), \
                mock.patch.object(self.gui.sheets, "ask", return_value=None) as asked:
            from PySide6.QtGui import QCloseEvent
            e = QCloseEvent()
            self.win.closeEvent(e)
        self.assertFalse(e.isAccepted())
        self.assertEqual(asked.call_args.kwargs["default"], "Keep Waiting")
        self.win._closing = False

    def test_the_profile_list_shows_where_the_focus_is(self):
        css = self.gui.style.stylesheet()
        self.assertIn("QListWidget#profileList::item:selected:focus", css)
        self.assertNotIn("profileList::item:selected:active", css)

    # ---- contrast -------------------------------------------------------------
    # ---- the M7 sheets, from the window ------------------------------------------------
    def wait(self, until=None, secs=10):
        end = time.time() + secs
        while time.time() < end and (self.win.threads() or (until is not None and not until())):
            settle(self.app, 2)

    def test_the_tools_menu_fits_and_jobs_open_their_details(self):
        tools = [a for a in self.win.menus["Tools"].actions() if not a.isSeparator()]
        self.assertLessEqual(len(tools), 10)                                  # (watching and rules: submenus)
        self.assertEqual(self.win.menus["Watched Folders"].menuAction().text(), "&Watched Folders")
        self.assertIn(self.win.act("delete_rule"), self.win.menus["Rules"].actions())
        text = self.gui.actions.shortcut_text(self.win.commands)
        for words in ("Search for Release", "Ctrl+2           Recordings", "Script Editor", "Ctrl+Alt+Z"):
            self.assertIn(words, text)
        import executor
        jobs = self.win.pages["Jobs"]
        self.win.go("Jobs")
        with mock.patch.object(executor, "jobs", lambda limit=50: [("20261031-101500-ab12", "Library",
                                                                     time.time(), time.time(), "done", "{}")]):
            jobs.reload()
        settle(self.app)
        jobs.table.selectRow(0)
        with mock.patch.object(self.gui.jobs, "popup_menu") as pm:
            jobs.context_menu(jobs.table.visualRect(jobs.table.currentIndex()).center())
        keys = [a.objectName() if a is not None else None for a in pm.call_args[0][3]]
        self.assertEqual(keys[0], "job_details")
        with mock.patch.object(type(self.win), "job_details") as jd:
            jobs.table.activated.emit(jobs.table.currentIndex())          # (Return, or a double click)
        jd.assert_called_once()

    def test_the_m7_commands_are_in_the_menus_the_table_and_the_inspector(self):
        menu = {t: [a.objectName() for a in m.actions() if not a.isSeparator()] for t, m in self.win.menus.items()}
        album = menu["Album"]
        self.assertEqual(self.win.menus["Album"].actions()[0].menu(), self.win.menus["Change Release"])
        self.assertEqual(menu["Change Release"], ["use_release", "search_release", "other_versions",
                                                  "choose_pressing"])
        self.assertIn("file_info", album)
        for key in ("edit_scripts", "tags_from_filenames"):
            self.assertIn(key, menu["Tools"])
        self.assertIn("show_log", menu["Window"])
        self.assertIn("job_details", menu["Edit"])
        for key, sc in (("search_release", "Ctrl+Shift+F"), ("other_versions", "Ctrl+Shift+L"),
                        ("choose_pressing", "Ctrl+Shift+P"), ("file_info", "Ctrl+I"), ("show_log", "Ctrl+Shift+Y"),
                        ("edit_scripts", "Ctrl+Shift+E"), ("tags_from_filenames", "Ctrl+Shift+G"),
                        ("job_details", "Ctrl+Shift+J")):
            self.assertEqual(self.win.act(key).shortcut().toString(), sc, key)
        self.assertEqual(self.win.act("find").shortcut().toString(), "Ctrl+F")     # (Find keeps Ctrl+F)
        self.assertEqual(self.gui.actions.TOOLBAR, ("add_folder", "preview", "apply", "cancel"))   # (DESIGN.md)
        # the inspector's Change Release: its menu is the Album menu's Change Release, in the same words
        b = self.win.inspector.buttons["release"]
        self.assertEqual([a.objectName() for a in b.menu().actions()],
                         ["use_release", "search_release", "other_versions", "choose_pressing"])
        # the album table's menu
        ov = self.show_plan()
        self.select(ov, "ready")
        from PySide6.QtCore import QPoint
        with mock.patch.object(self.gui.overview, "popup_menu") as pm:
            ov.context_menu(QPoint(1, 1))
        acts = pm.call_args[0][3]
        keys = [a.objectName() if a is not None else None for a in acts]
        self.assertIs(acts[0].menu(), self.win.menus["Change Release"])        # (the four ways, in one submenu)
        for key in ("file_info", "tags_from_filenames"):
            self.assertIn(key, keys)

    def test_the_m7_commands_act_only_where_they_can(self):
        for key in ("search_release", "other_versions", "choose_pressing", "file_info", "tags_from_filenames",
                    "job_details"):
            self.assertFalse(self.win.act(key).isEnabled(), key)              # (nothing selected yet)
        self.assertTrue(self.win.act("show_log").isEnabled())
        ov = self.show_plan()
        self.select(ov, "ready", "exact")
        for key in ("search_release", "other_versions", "file_info", "tags_from_filenames"):
            self.assertTrue(self.win.act(key).isEnabled(), key)
        self.assertFalse(self.win.act("choose_pressing").isEnabled())         # (its pressing is proven)
        self.select(ov, "ready", "album_only")
        self.assertTrue(self.win.act("choose_pressing").isEnabled())
        self.select(ov, "excluded")
        self.assertFalse(self.win.act("tags_from_filenames").isEnabled())     # (an album not to be written)
        self.assertTrue(self.win.act("search_release").isEnabled())
        ov.busy(True)                                                         # (a preview or Apply runs)
        for key in ("search_release", "other_versions", "choose_pressing", "tags_from_filenames"):
            self.assertFalse(self.win.act(key).isEnabled(), key)
        ov.busy(False)
        self.win.go("History")
        for key in ("search_release", "file_info", "tags_from_filenames"):
            self.assertFalse(self.win.act(key).isEnabled(), key)

    def test_the_m7_sheets_open_from_the_window(self):
        from gui import search, versions
        from gui.dialogs.logs import LogViewer
        ov = self.show_plan()
        a = self.select(ov, "ready", "album_only")
        self.assertTrue(any(a is x for x in ov.plan["albums"]))             # (an edit reaches the plan)
        self.assertIs(self.win.inspector.album, a)
        lookup = mock.Mock(cfg={})
        row = {"source": "mb", "id": "4b7c1f0e-8a3d-4f61-9c2a-0d5e6f7a8b9c", "ref": "4b7c1f0e-8a3d-4f61-9c2a-0d5e6f7a8b9c",
               "url": "https://musicbrainz.org/release/4b7c1f0e-8a3d-4f61-9c2a-0d5e6f7a8b9c", "title": "Hallway",
               "artist": "The Quiet Rooms", "tracks": 8}
        with mock.patch.object(versions, "make_lookup", return_value=lookup), \
                mock.patch.object(versions, "search", return_value={"rows": [row]}) as found, \
                mock.patch.object(versions, "versions", return_value={"rows": [], "notes": [], "n_files": 8}):
            QTest.keyClick(self.win, Qt.Key_F, Qt.ControlModifier | Qt.ShiftModifier)
            self.wait(lambda: getattr(ov, "_search_sheet", None) is not None and ov._search_sheet.model.rowCount())
            self.assertIsInstance(ov._search_sheet, search.SearchSheet)
            self.assertIs(ov._search_sheet.album, a)
            self.assertEqual(ov._search_sheet.model.rowCount(), 1)
            ov._search_sheet.close()
            self.win.act("other_versions").trigger()
            self.wait()
            self.assertIsInstance(ov._versions_sheet, search.VersionsSheet)
            self.assertFalse(ov._versions_sheet.pressing)
            self.win.act("choose_pressing").trigger()
            self.wait()
            self.assertTrue(ov._versions_sheet.pressing)
            ov._versions_sheet.close()
        lookup.close.assert_called()                                          # (each worker closed its lookup)
        self.gui.sheets.shown.clear()
        self.win.activateWindow()
        settle(self.app)
        QTest.keyClick(self.win, Qt.Key_I, Qt.ControlModifier)                # Album > Info…
        self.wait()
        self.assertEqual(self.gui.sheets.shown[0][0], "file_info")
        self.assertEqual(self.win.info_file(), a["files"][0]["src"])
        # Tools > Tags from File Names: the tags become your edits of the preview (nothing is written)
        self.gui.sheets.shown.clear()
        dlg = self.win.tags_from_filenames()
        self.assertEqual(self.gui.sheets.shown[0][0], "tags_from_filenames")
        src = a["files"][0]["src"]
        with mock.patch.object(self.win.inspector, "reapply") as reapply:
            dlg.on_apply({src: {"title": ["From the name"]}})
        reapply.assert_called_once_with(a)
        edit = self.gui.planmod.edit_for(self.win.inspector.edits, _os.path.realpath(a["source_dir"]),
                                         [fe["src"] for fe in a["files"]])
        self.assertEqual(edit["file_tags"][src], {"title": ["From the name"]})
        self.assertIn("Tags from the file names are in the preview of 1 album", self.win.statusBar().currentMessage())
        ov.busy(True)                                                         # (the sheet answered during a run)
        dlg.on_apply({src: {"title": ["Late"]}})
        self.assertEqual(edit["file_tags"][src], {"title": ["From the name"]})
        ov.busy(False)
        # Window > Log, once per window
        QTest.keyClick(self.win, Qt.Key_Y, Qt.ControlModifier | Qt.ShiftModifier)   # (Ctrl+Alt+L locks screens)
        settle(self.app)
        self.wait()
        v = self.win._log_viewer
        self.assertIsInstance(v, LogViewer)
        self.win.act("show_log").trigger()
        self.wait()
        self.assertIs(self.win._log_viewer, v)
        v.close()
        # Edit > Job Details… on the Jobs page's selected job, with the page's own Undo
        jobs = self.win.pages["Jobs"]
        self.win.go("Jobs")
        self.gui.sheets.shown.clear()
        with mock.patch.object(jobs, "job", return_value="20261031-101500-ab12"):
            self.win.update_album_actions()
            self.assertTrue(self.win.act("job_details").isEnabled())
            dlg = self.win.job_details()
        self.wait()
        self.assertEqual(self.gui.sheets.shown[0][0], "job_details")
        with mock.patch.object(jobs, "undo") as undo, mock.patch.object(self.win.player, "release") as release:
            dlg.undo_fn()                                                    # (the page's own Undo, the player
        undo.assert_called_once()                                            #  letting go of the files first)
        release.assert_called_once_with()
        # Tools > Script Editor… on Overview: the preview's profile and the selected album
        import profiles as pf
        pf.save(pf.from_dict({"name": "Library"}))
        ov.refresh_profiles()
        ov.profile.setCurrentText("Library")
        self.win.go("Overview")
        self.select(ov, "ready", "exact")
        self.assertTrue(self.win.act("edit_scripts").isEnabled())
        self.win.activateWindow()
        settle(self.app)
        with mock.patch("gui.script_editor.open_editor") as opened:
            QTest.keyClick(self.win, Qt.Key_E, Qt.ControlModifier | Qt.ShiftModifier)
        opened.assert_called_once()
        self.assertIs(opened.call_args[0][2], ov.selected())

    # ---- M8: CD lookup, manual match, your MusicBrainz account, AcoustID, the player ------------
    def test_the_m8_commands_are_in_the_menus_with_their_keys(self):
        menus = self.win.menus
        names = lambda m: [a.objectName() for a in m.actions() if not a.isSeparator()]  # noqa: E731
        self.assertIn("lookup_cd", names(menus["File"]))
        for key in ("match_files", "add_to_collection", "submit_ratings", "submit_acoustids"):
            self.assertIn(key, names(menus["Album"]), key)
        self.assertEqual(names(menus["Controls"]), ["play_pause", "next_track", "previous_track", "stop_playing",
                                                    "skip_forward", "skip_back", "volume_up", "volume_down", "mute"])
        for key, sc in (("lookup_cd", "Ctrl+K"), ("match_files", "Ctrl+Shift+M"), ("play_pause", "Space"),
                        ("minimize", "Ctrl+M"), ("next_track", "Ctrl+Right"), ("previous_track", "Ctrl+Left"),
                        ("stop_playing", "Ctrl+Alt+."), ("mute", "Ctrl+Alt+M")):
            self.assertEqual(self.win.act(key).shortcut().toString(), sc, key)
        self.assertEqual(self.gui.actions.plain(self.win.act("match_files").text()), "Match Files Manually…")
        self.assertEqual(self.gui.actions.plain(self.win.act("add_to_collection").text()), "Collections…")
        tops = [t.replace("&&", "")[t.replace("&&", "").index("&") + 1].lower() for t, _k in self.gui.actions.MENUS]
        self.assertEqual(len(tops), len(set(tops)))                           # (Alt+C: Controls, its own)
        # Space plays only where the album tables (or the player) have focus: a focused button keeps it
        play = self.win.act("play_pause")
        self.assertEqual(play.shortcutContext(), Qt.WidgetWithChildrenShortcut)
        self.assertNotIn(play, self.win.actions())
        for w in (self.win.pages["Overview"].table, self.win.pages["Needs attention"].table, self.win.player):
            self.assertIn(play, w.actions())
        self.assertIn("Space", self.gui.actions.shortcut_text(self.win.commands))

    def test_ctrl_arrows_in_a_table_stay_the_tables(self):
        ov = self.show_plan()
        self.select(ov, "ready", "exact")
        player = self.win.player
        player.setVisible(True)
        self.win.update_player_actions()
        before = player.volume.value()
        self.win.activateWindow()
        ov.table.setFocus()
        settle(self.app)
        row = ov.table.currentIndex().row()
        with mock.patch.object(player, "next") as nxt:
            QTest.keyClick(ov.table, Qt.Key_Down, Qt.ControlModifier)
            QTest.keyClick(ov.table, Qt.Key_Right, Qt.ControlModifier)
            settle(self.app)
        self.assertEqual(player.volume.value(), before)                       # (not Volume Down)
        nxt.assert_not_called()                                               # (not Next Track)
        self.assertEqual(ov.table.currentIndex().row(), row + 1)              # (the table's: the current row moved)
        if self.win.act("volume_up").isEnabled():                             # (elsewhere the key is Controls')
            self.win.search_box.setFocus()
            QTest.keyClick(self.win.search_box, Qt.Key_Up, Qt.ControlModifier)
            self.assertEqual(player.volume.value(), min(100, before + 10))
            player.volume.setValue(before)
        player.close_player()

    def test_rating_submenu_rates_the_file_shown(self):
        names = [a.objectName() for a in self.win.menus["Rating"].actions() if not a.isSeparator()]
        self.assertEqual(names, ["rate_none", "rate_1", "rate_2", "rate_3", "rate_4", "rate_5"])
        self.assertIn(self.win.menus["Rating"].menuAction(), self.win.menus["Tag"].actions())
        ov = self.show_plan()
        a = self.select(ov, "ready", "exact")
        self.win.inspector.show_tab("Tags")
        settle(self.app, 20)
        box = self.win.inspector.tags.rating
        self.win.update_tag_actions()
        if not self.win.act("rate_4").isEnabled():
            self.skipTest("the album's rating row cannot be edited here")
        with mock.patch.object(self.win.inspector, "set_tag") as set_tag:
            self.win.act("rate_4").trigger()
        self.assertEqual(box.stars.value(), 4)
        self.assertEqual(set_tag.call_args[0][:2], ("~rating", ["4"]))
        self.assertIs(set_tag.call_args[1]["album"], a)

    def test_the_m8_commands_act_only_where_they_can(self):
        keys = ("match_files", "add_to_collection", "submit_ratings", "submit_acoustids")
        for key in keys:
            self.assertFalse(self.win.act(key).isEnabled(), key)              # (nothing selected yet)
        ov = self.show_plan()
        self.select(ov, "ready", "exact")                                     # (a MusicBrainz release)
        for key in keys:
            self.assertTrue(self.win.act(key).isEnabled(), key)
        self.select(ov, "ready", "album_only")                                # (no release: no collection)
        self.assertFalse(self.win.act("add_to_collection").isEnabled())
        self.assertFalse(self.win.act("submit_ratings").isEnabled())
        self.assertTrue(self.win.act("submit_acoustids").isEnabled())
        self.select(ov, "excluded")
        self.assertFalse(self.win.act("match_files").isEnabled())            # (an album not to be written)
        self.select(ov, "ready", "exact")
        ov.busy(True)
        try:
            for key in keys + ("lookup_cd",):
                self.assertFalse(self.win.act(key).isEnabled(), key)
        finally:
            ov.busy(False)
        self.win.go("Jobs")
        for key in keys:
            self.assertFalse(self.win.act(key).isEnabled(), key)
        self.assertTrue(self.win.act("lookup_cd").isEnabled())

    def test_the_m8_sheets_open_from_the_window_with_the_plans_own_album(self):
        from gui import account, acoustid_submit, manual_match
        ov = self.show_plan()
        a = self.select(ov, "ready", "exact")
        self.win.inspector.set_tag("~rating", ["4"], album=a)                 # (your stars: an edit)
        acct = account.account_of(self.win)
        with mock.patch.object(account, "open_collections") as cols, \
                mock.patch.object(account, "submit_ratings") as rates, \
                mock.patch.object(acoustid_submit, "submit_album") as fps, \
                mock.patch.object(manual_match, "open_manual_match") as match:
            for key in ("add_to_collection", "submit_ratings", "submit_acoustids"):
                self.win.act(key).trigger()
            self.win.activateWindow()
            ov.table.setFocus()
            settle(self.app)
            QTest.keyClick(self.win, Qt.Key_M, Qt.ControlModifier | Qt.ShiftModifier)
        self.assertIs(cols.call_args[0][1], a)
        self.assertIs(cols.call_args[0][2], acct)                             # (the window's one account)
        self.assertIs(rates.call_args[0][1], a)
        self.assertEqual(rates.call_args[0][2]["tags"]["~rating"], ["4"])    # (your edit: what is sent)
        self.assertIs(fps.call_args[0][1], a)
        self.assertIs(match.call_args[0][0], ov)
        # the account's own sheets: nothing is sent without a login, and they say where Log In is
        self.gui.sheets.shown.clear()
        self.assertFalse(account.open_collections(self.win, a, acct))
        self.assertEqual(self.gui.sheets.shown[-1][1], "Log in to MusicBrainz first.")
        self.assertIn("Log in from Setup", self.gui.sheets.shown[-1][2])        # (what is missing: the notice)
        self.assertIn("Account", self.win.preferences().panes)

    def test_look_up_cd_opens_on_the_drive_chosen_in_preferences(self):
        import cdlookup
        from gui import browser
        with mock.patch.object(cdlookup, "drives", return_value=["/dev/sr0", "/dev/sr1"]), \
                mock.patch.object(cdlookup, "can_read_drive", return_value=True):
            prefs = self.win.preferences()
            prefs.show_tab("Account")
            pane = prefs.account
            pane.fill_drives()
            self.assertEqual([pane.drive.itemData(i) for i in range(pane.drive.count())], ["", "/dev/sr0", "/dev/sr1"])
            pane.drive.setCurrentIndex(2)
            self.assertEqual(self.gui.common._gui_prefs()["cd_drive"], "/dev/sr1")
            prefs.close()
            self.win.activateWindow()
            settle(self.app)
            QTest.keyClick(self.win, Qt.Key_K, Qt.ControlModifier)
            settle(self.app)
            dlg = self.win.pages["Overview"]._cd_sheet
            self.assertIsInstance(dlg, browser.CdSheet)
            self.assertEqual(dlg.drive.currentData(), "/dev/sr1")
            self.assertIsNone(dlg.disc)                                       # (nothing read or sent yet)
            dlg.close()

    def test_the_account_pane_keeps_your_keys_out_of_sight(self):
        import stat
        import credentials as cr
        prefs = self.win.preferences()
        prefs.show_tab("Account")
        pane = prefs.account
        self.assertEqual(set(pane.fields), {"mb_client_id", "mb_client_secret", "acoustid_user"})
        self.assertFalse(set(pane.fields) & set(prefs.keys.fields))           # (yours here, providers' under Keys)
        self.assertEqual(set(prefs.fields), set(cr.FILES))
        for e in pane.fields.values():
            self.assertEqual(e.echoMode(), QLineEdit.Password)
        self.assertIn("above", pane.login.state.text())                        # (the keys are on this pane)
        self.assertIn("below", pane.login.how.text())                          # (the how-to is above them)
        self.assertNotIn("Keys", pane.login.how.text())
        self.assertNotEqual(pane.login.in_button.property("role"), "primary")  # (one prominent button: Save)
        self.assertEqual(pane.save_button.property("role"), "primary")
        self.assertFalse(pane.login.in_button.isEnabled())                    # (no client ID and secret yet)
        pane.fields["mb_client_id"].setText("CLIENT-ID-SECRETISH")
        pane.fields["mb_client_secret"].setText("CLIENT-SECRET-VALUE")
        with mock.patch.object(self.gui.sheets, "ask", return_value="Cancel"):  # (typed, not saved: asked)
            self.assertFalse(prefs.close())
        pane.save()
        self.assertEqual(pane.saved.text(), "Saved 2 keys.")
        for name in ("mb_client_id", "mb_client_secret"):
            p = cr.path_of(name)
            self.assertEqual(stat.S_IMODE(p.stat().st_mode), 0o600)
            self.assertEqual(pane.fields[name].text(), "")
            self.assertEqual(pane.fields[name].placeholderText(), "Saved on this computer")
        self.assertEqual(cr.get("mb_client_secret"), "CLIENT-SECRET-VALUE")
        from PySide6.QtWidgets import QLabel
        shown = " ".join(lb.text() for lb in prefs.findChildren(QLabel))
        self.assertNotIn("CLIENT-SECRET-VALUE", shown)
        self.assertNotIn("CLIENT-ID-SECRETISH", shown)
        self.assertTrue(pane.login.in_button.isEnabled())                     # (Log In now; nothing sent yet)
        self.assertIsNotNone(pane.browser._check)                             # (Browser integration, off)
        self.assertFalse(pane.browser._check.isChecked())
        for name in ("mb_client_id", "mb_client_secret"):
            cr.path_of(name).unlink()

    def test_the_browser_integration_is_off_until_turned_on_and_stops_on_close(self):
        bi = self.win.browser_integration
        self.assertIsNone(bi.server)                                          # (off by default: no port open)
        self.assertEqual(bi.status(), "Off: no requests are taken.")
        with mock.patch.object(bi, "stop") as stop, mock.patch.object(self.win.player, "close_player") as close:
            self.win.close()
            settle(self.app)
        stop.assert_called()
        close.assert_called()

    def test_space_plays_the_selected_album_and_apply_lets_go_of_its_files(self):
        ov = self.show_plan()
        a = self.select(ov, "ready", "exact")
        player = self.win.player
        with mock.patch.object(player, "play_pause_album") as pp:
            ov.table.setFocus()
            settle(self.app)
            QTest.keyClick(ov.table, Qt.Key_Space)
            self.win.act("play_pause").trigger()
        # (Play/Pause in the menu is off without Qt Multimedia; Space still asks the player)
        self.assertEqual(pp.call_count, 2 if player.available else 1)
        self.assertIs(pp.call_args[0][0], a)
        with mock.patch.object(player, "release", return_value=True) as release:
            ov.release_files()
        paths = set(release.call_args[0][0])
        for x in ov.to_apply():
            for fe in x["files"]:
                self.assertIn(fe["src"], paths)
                self.assertIn(fe["dst"], paths)
        with mock.patch.object(player, "release") as release, \
                mock.patch.object(self.win.pages["Jobs"], "undo_last") as undo:
            self.win.act("undo_job").trigger()
        release.assert_called_once_with()                                     # (whatever plays: let go)
        undo.assert_called_once()
        self.win.go("Overview")
        self.assertFalse(self.win.act("next_track").isEnabled())              # (nothing loaded)

    def test_a_folder_dropped_again_is_said_to_be_in_the_preview(self):
        from PySide6.QtCore import QMimeData, QPoint, QUrl
        from PySide6.QtGui import QDropEvent
        ov = self.show_plan()
        md = QMimeData()
        md.setUrls([QUrl.fromLocalFile(str(self.tmp))])
        with mock.patch.object(type(ov), "set_path") as set_path:
            self.win.dropEvent(QDropEvent(QPoint(5, 5), Qt.CopyAction, md, Qt.LeftButton, Qt.NoModifier))
        set_path.assert_not_called()
        self.assertIn("Already in the preview", self.win.statusBar().currentMessage())
        other = Path(tempfile.mkdtemp(prefix="tm-drop-", dir=self.tmp.parent))
        try:
            md.setUrls([QUrl.fromLocalFile(str(other))])
            with mock.patch.object(type(ov), "set_path") as set_path:
                self.win.dropEvent(QDropEvent(QPoint(5, 5), Qt.CopyAction, md, Qt.LeftButton, Qt.NoModifier))
            set_path.assert_called_once_with(str(other))
        finally:
            shutil.rmtree(other, ignore_errors=True)

    def test_the_inspector_has_a_rating_row_for_albums_with_files(self):
        ov = self.show_plan()
        a = self.select(ov, "ready", "exact")
        tags = self.win.inspector.tags
        self.win.inspector.show_tab("Tags")
        settle(self.app)
        self.assertTrue(tags.rating.isVisible())
        self.assertIs(tags.rating.album, a)
        self.assertIn("Submit Ratings", tags.rating.toolTip())
        tags.files.setCurrentIndex(1)                                         # (one file: its own rating)
        self.assertEqual(tags.rating.file, a["files"][0]["src"])
        with mock.patch.object(self.win.inspector, "reapply"):
            tags.rating.rated(3)
        e = self.win.inspector.edit_of(a)
        self.assertEqual(e["file_tags"][a["files"][0]["src"]]["~rating"], ["3"])
        att = self.win.pages["Needs attention"]
        self.win.go("Needs attention")
        self.win.inspector.show_row({"folder": str(self.tmp), "codes": ["no_candidate"]})
        self.assertFalse(tags.rating.isVisible())                             # (no files to rate)
        self.assertIsNotNone(att)

    def test_status_colours_have_text_contrast_in_light_and_dark(self):
        style = self.gui.style
        low = []
        for dark in (False, True):
            t = style.DARK if dark else style.LIGHT
            for key in style.STATUS:
                for bg in ("base", "window", "alt"):
                    c = style.contrast(style.color(key, dark), t[bg])
                    if c < 4.5:
                        low.append(f"{'dark' if dark else 'light'} {key} on {bg}: {c:.2f}")
            for fg, bg in (("ink", "base"), ("ink", "window"), ("ink", "sidebar"), ("muted", "base"),
                           ("muted", "window"), ("muted", "sidebar"), ("on_accent", "accent"), ("accent", "base"),
                           ("attention", "sidebar"), ("error", "base")):
                c = style.contrast(t[fg], t[bg])
                if c < 4.5:
                    low.append(f"{'dark' if dark else 'light'} {fg} on {bg}: {c:.2f}")
            pal = style.palette(dark)
            if style.contrast(pal.color(QPalette.HighlightedText).name(), pal.color(QPalette.Highlight).name()) < 4.5:
                low.append(f"{'dark' if dark else 'light'}: selected rows")
        self.assertEqual(low, [])
        for key, s in style.STATUS.items():                            # a word and a symbol, never colour alone
            self.assertTrue(s[0] and s[1] and s[4], key)


class M8Engine(unittest.TestCase):
    """The planner's and the keys' side of M8, without Qt: files placed after your match, the
    MusicBrainz and AcoustID keys kept 0600 and checked by doctor without being read out."""

    def entry(self, op="move"):
        d = "/nonexistent/tm-m8/Album"
        files = []
        for i, name in enumerate(("a.flac", "b.flac", "c.flac")):
            fe = {"src": f"{d}/{name}", "dst": f"/nonexistent/tm-m8/dest/{i + 1:02d} T{i + 1}.flac", "op": op,
                  "changes": {"tracknumber": [str(i + 1)], "title": [f"T{i + 1}"]}, "diff": []}
            files.append(fe)
        e = {"source_dir": d, "status": "ready", "files": files, "companions": [],
             "artwork": {"embed_into": [f["dst"] for f in files], "embed_replace": [files[0]["dst"]]},
             "sidecars": [{"src": files[0]["src"], "dst": files[0]["dst"][:-5] + ".lrc", "text": "la"}]}
        import plan as pl
        pl._snapshot(e)
        return e

    def test_a_match_places_files_after_their_new_tracks_and_undoes_cleanly(self):
        import plan as pl
        e = self.entry()
        planned = [fe["dst"] for fe in e["files"]]
        a, b, c = e["files"]
        a["changes"]["tracknumber"], b["changes"]["tracknumber"] = ["2"], ["1"]          # (a and b swapped)
        pl._place_by_match(e, {"pairs": {a["src"]: 1, b["src"]: 0}, "unmatched": [c["src"]]})
        self.assertEqual((a["dst"], b["dst"]), (planned[1], planned[0]))
        self.assertEqual((c["dst"], c["op"]), (c["src"], "tag"))                         # (unmatched: stays)
        self.assertEqual(e["artwork"]["embed_into"], [planned[1], planned[0], c["src"]])
        self.assertEqual(e["artwork"]["embed_replace"], [planned[1]])                    # (a's own cover)
        self.assertEqual(e["sidecars"], [])                                              # (a's lyrics were T1's)
        pl._restore(e)                                                                   # (the edit taken back)
        self.assertEqual([fe["dst"] for fe in e["files"]], planned)
        self.assertEqual(len(e["sidecars"]), 1)
        self.assertEqual(e["artwork"]["embed_into"], planned)

    def test_a_swapped_pair_keeps_each_files_own_extension(self):
        import plan as pl
        e = self.entry()
        a, b, _c = e["files"]
        a["src"], a["dst"] = a["src"][:-5] + ".mp3", a["dst"][:-5] + ".mp3"             # (an MP3 beside FLACs)
        pl._snapshot(e)
        a["changes"]["tracknumber"], b["changes"]["tracknumber"] = ["2"], ["1"]          # (a and b swapped)
        pl._place_by_match(e, {"pairs": {a["src"]: 1, b["src"]: 0}, "unmatched": []})
        self.assertEqual(a["dst"], "/nonexistent/tm-m8/dest/02 T2.mp3")                 # (b's name, a's .mp3)
        self.assertEqual(b["dst"], "/nonexistent/tm-m8/dest/01 T1.flac")                # (a's name, b's .flac)

    def test_an_unmatched_file_of_a_copied_album_is_never_written(self):
        import plan as pl
        e = self.entry("copy")
        c = e["files"][2]
        pl._place_by_match(e, {"pairs": {}, "unmatched": [c["src"]]})
        self.assertEqual((c["dst"], c["op"], c["changes"]), (c["src"], "none", {}))

    def test_a_file_on_a_track_no_file_had_keeps_a_name_of_its_own(self):
        import plan as pl
        e = self.entry()
        a, b, _c = e["files"]
        planned_a = a["dst"]
        b["changes"]["tracknumber"] = ["9"]                                              # (a track the plan gave none)
        a["changes"]["tracknumber"] = ["2"]                                              # (a takes b's track)
        pl._place_by_match(e, {"pairs": {a["src"]: 1, b["src"]: 8}, "unmatched": []})
        self.assertEqual(a["dst"], "/nonexistent/tm-m8/dest/02 T2.flac")
        self.assertEqual(b["dst"], "/nonexistent/tm-m8/dest/b.flac")                     # (never over a's)
        self.assertNotEqual(a["dst"], planned_a)

    def test_the_account_keys_are_kept_0600_and_doctor_never_reads_the_login(self):
        import stat
        import credentials as cr
        import doctor
        import exclusions as ex
        for name in ("mb_client_id", "mb_client_secret", "acoustid_user"):
            self.assertIn(name, cr.FILES)
            self.assertIn(name, cr.LABELS)
        p = cr.store("acoustid_user", "USER-KEY-VALUE")
        self.assertEqual(stat.S_IMODE(p.stat().st_mode), 0o600)
        login = ex.CONFIG_DIR / cr.TOKEN_FILES["musicbrainz_login"]
        login.write_text('{"refresh_token": "TOKEN-VALUE"}')
        os_ = __import__("os")
        os_.chmod(login, 0o644)
        try:
            with mock.patch.object(doctor, "_module", return_value="1"):
                found = {c.name: c for c in doctor.checks()}
            self.assertEqual(found["AcoustID user key"].status, "ok")
            self.assertEqual(found["MusicBrainz login"].status, "fail")
            self.assertIn("chmod 600", found["MusicBrainz login"].fix)
            text = " ".join(f"{c.detail} {c.fix}" for c in found.values())
            self.assertNotIn("TOKEN-VALUE", text)
            self.assertNotIn("USER-KEY-VALUE", text)
            os_.chmod(login, 0o600)
            self.assertEqual(cr.token_file_state("musicbrainz_login"), "ok")
        finally:
            login.unlink()
            p.unlink()


if __name__ == "__main__":
    unittest.main(argv=sys.argv[:1])                                   # (the plugin path is not a test name)
