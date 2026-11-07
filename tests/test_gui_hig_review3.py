# The M6 shell after HIG review round 3, offscreen: destructive sheets default to Cancel, Quit
# keeps typed keys when Preferences says Cancel, the Edit menu acts only where it can, one rule for
# the Needs attention badge, the watched inbox is never named as an artist, Preferences in words
# and sizes, the Profiles form at 960 px, the inspector's stacked tags, and the commands the first
# shell tests only listed (Show Sidebar, Copy Album Path, Help, Shortcuts, Report, About).
import os as _os, tempfile as _tempfile  # noqa: E402
_os.environ["XDG_CONFIG_HOME"] = _tempfile.mkdtemp(prefix="tm-config-")
_os.environ["XDG_STATE_HOME"] = _tempfile.mkdtemp(prefix="tm-state-")
_os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
_os.environ.setdefault("TAGMENDER_TAGGER_INI", "/nonexistent")

import sys
import time
import unittest
import shutil
import tempfile
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE / "tests"))
import test_gui_shell as _shell  # noqa: E402  (its window set-up; not its tests: not imported by name)
from test_gui_shell import QApplication, settle  # noqa: E402

if QApplication is not None:
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QCloseEvent
    from PySide6.QtWidgets import QPushButton


@unittest.skipIf(QApplication is None, "PySide6 not installed")
class Review3(unittest.TestCase):
    setUpClass = classmethod(_shell.Shell.setUpClass.__func__)
    tearDownClass = classmethod(_shell.Shell.tearDownClass.__func__)
    setUp = _shell.Shell.setUp
    tearDown = _shell.Shell.tearDown
    plan = _shell.Shell.plan
    show_plan = _shell.Shell.show_plan
    select = _shell.Shell.select

    # ---- destructive sheets ---------------------------------------------------
    def test_a_destructive_sheet_defaults_to_cancel_and_says_its_action_in_red(self):
        sheets = self.gui.sheets
        s = sheets.Sheet(self.win, "Undo the job?", "Puts back…", buttons=("Cancel", "Undo Job"), destructive=True)
        self.assertEqual(s.default_button().text(), "Cancel")
        self.assertEqual(s.buttons["Undo Job"].property("role"), "destructive")
        s.deleteLater()
        seen = {}

        def run(s, kind="sheet"):                                     # Return: the default button
            seen["default"] = s.default_button().text()
            s.default_button().click()
            return s.result()
        with mock.patch.object(sheets, "blocking", lambda: True), mock.patch.object(sheets, "run", run):
            self.assertFalse(sheets.confirm(self.win, "Undo?", "x", "y", "Undo Job", destructive=True))
            self.assertEqual(seen["default"], "Cancel")
            self.assertTrue(sheets.confirm(self.win, "Apply?", "x", "y", "Apply 2 Albums"))   # (forward: accent)
            self.assertEqual(seen["default"], "Apply 2 Albums")

    def test_undo_remove_rule_stop_watching_and_dismiss_are_destructive(self):
        w, pr, jobs = (self.win.pages[n] for n in ("Watched folders", "Profiles", "Jobs"))
        rule = mock.Mock(scope="global", id="r1")
        rule.name = "Karaoke"
        with mock.patch.object(self.gui.sheets, "confirm", return_value=False) as asked:
            jobs.undo("20261031-120000-zz99")
            with mock.patch.object(type(pr), "current_rule", lambda s: rule), \
                    mock.patch.object(type(pr), "own_rule", lambda s: True):
                pr.remove_rule()
            with mock.patch.object(type(w), "selected", lambda s: ("/music/inbox", "Inbox")):
                w.remove()
            with mock.patch.object(type(w), "chosen_plan", lambda s: {"albums": [("/music/inbox/Ferry", "", "")],
                                                                       "path": "p", "job": "j"}):
                w.dismiss()
        self.assertEqual(asked.call_count, 4)
        for call in asked.call_args_list:
            self.assertTrue(call.kwargs.get("destructive"), call)
        dismiss = asked.call_args_list[-1]
        self.assertIn("Ferry", dismiss.args[1])                          # (it names the album)
        self.assertIn("Ferry", dismiss.args[2])

    # ---- Quit and Preferences ---------------------------------------------------
    def test_cancel_on_the_typed_keys_sheet_keeps_the_app_open(self):
        prefs = self.win.preferences()
        prefs.show_tab("Keys")
        prefs.fields["fanart"].setText("TYPED")
        with mock.patch.object(self.gui.sheets, "ask", return_value="Cancel"):
            e = QCloseEvent()
            self.win.closeEvent(e)
        self.assertFalse(e.isAccepted())
        self.assertTrue(prefs.isVisible())
        self.assertEqual(prefs.fields["fanart"].text(), "TYPED")
        prefs.fields["fanart"].clear()

    def test_preferences_in_words_and_sizes(self):
        import credentials
        prefs = self.win.preferences()
        self.assertEqual(set(prefs.fields), set(credentials.FILES))   # (no key dropped from Keys)
        prefs.show_tab("General")
        self.assertEqual(prefs.windowTitle(), "General — TagMender Preferences")
        self.assertEqual(prefs.height(), prefs.TALL)                  # (every pane scrolls inside one height)
        row = prefs.general.motion.parentWidget()                     # (a grouped list's row: label, then switch)
        self.assertEqual(row.label.text(), "Reduce motion")
        self.assertTrue(row.foot.text())                              # (said under the label, not in a tooltip)
        prefs.show_tab("Keys")
        self.assertEqual(prefs.height(), prefs.TALL)
        self.assertIn("Web search", prefs.keys.fields["marginalia"].parentWidget().findChildren(
            type(prefs.keys.saved))[0].text())
        heads = [lb.text() for lb in prefs.keys.findChildren(type(prefs.keys.saved)) if lb.property("role") == "headline"]
        self.assertEqual(heads[:3], ["Identification", "Covers and artist photos", "Web search (optional)"])
        prefs.fields["fanart"].setText("K")
        prefs.save()
        self.assertEqual(prefs.keys.saved.accessibleName(), "")      # (the text is what is read)
        self.assertEqual(prefs.keys.saved.text(), "Saved 1 key.")

    def test_reduce_motion_is_saved_and_followed(self):
        common = self.gui.common
        g = self.win.preferences().general
        g.motion.setChecked(False)
        self.assertIs(common._gui_prefs()["reduce_motion"], False)
        self.assertFalse(common.reduced_motion())
        g.motion.setChecked(True)
        self.assertIs(common._gui_prefs()["reduce_motion"], True)
        self.assertTrue(common.reduced_motion())

    def test_run_checks_is_off_while_the_checks_run_and_shows_rows(self):
        import doctor
        prefs = self.win.preferences()
        prefs.show_tab("Checks")
        pane = prefs.checks_pane
        found = [doctor.Check("python", "ok", "3.12"), doctor.Check("acoustid key", "warn", "not set",
                                                                    "Preferences > Keys")]
        with mock.patch.object(doctor, "checks", lambda: (time.sleep(0.2), found)[1]):
            prefs.run_checks()
            self.assertFalse(pane.run_btn.isEnabled())
            prefs.run_checks()                                          # (a second click: nothing)
            end = time.time() + 10
            while time.time() < end and not pane.run_btn.isEnabled():
                settle(self.app, 2)
        self.assertTrue(pane.run_btn.isEnabled())
        settle(self.app)
        self.assertIn("python: 3.12", prefs.checks.toPlainText())
        self.assertEqual(pane.run_btn.property("role"), "primary")
        names = [lb.accessibleName() for lb in prefs.checks.findChildren(type(pane.note)) if lb.isVisibleTo(prefs)]
        self.assertIn("OK", names)
        self.assertIn("Needs a look", names)
        self.assertIn("Fix: Preferences > Keys", names)

    # ---- the Edit menu ----------------------------------------------------------
    def test_edit_commands_act_only_where_they_can_and_copy_a_tables_row(self):
        att = self.win.pages["Needs attention"]
        self.win.go("Needs attention")
        folder = str(self.tmp / "Artist" / "Album")
        att.show_rows([{"folder": folder, "codes": ["no_candidate"], "reason": "", "files": 1, "mark": "Unresolved"}])
        att.table.setFocus()
        att.table.clearSelection()
        settle(self.app)
        self.win.update_edit_actions()
        for k in ("copy", "cut", "paste"):
            self.assertFalse(self.win.act(k).isEnabled(), k)
        att.table.selectRow(0)
        settle(self.app)
        self.assertTrue(self.win.act("copy").isEnabled())
        self.assertFalse(self.win.act("paste").isEnabled())
        self.win.act("copy").trigger()
        self.assertEqual(QApplication.clipboard().text(), folder)
        self.win.search_box.setFocus()
        settle(self.app)
        for k in ("copy", "cut", "paste", "select_all"):
            self.assertTrue(self.win.act(k).isEnabled(), k)

    def test_copy_album_path_show_sidebar_and_the_help_commands(self):
        ov = self.show_plan()
        a = self.select(ov, "ready", "exact")
        self.win.act("copy_path").trigger()
        self.assertEqual(QApplication.clipboard().text(), a["source_dir"])
        self.assertEqual(self.win.statusBar().currentMessage(), "Copied the album's folder.")
        self.win.act("show_sidebar").trigger()
        self.assertFalse(self.win.nav.isVisible())
        self.win.act("show_sidebar").trigger()
        self.assertTrue(self.win.nav.isVisible())
        self.gui.sheets.shown.clear()
        with mock.patch.object(self.gui.window.QDesktopServices, "openUrl") as opened:
            self.win.act("help").trigger()
            self.win.act("shortcuts").trigger()
            self.win.act("about").trigger()
            self.win.act("report").trigger()                          # (offscreen: the sheet says no)
        opened.assert_called_once()
        heads = [h for _k, h, _b in self.gui.sheets.shown]
        self.assertEqual(heads[0], "Keyboard shortcuts")
        self.assertTrue(heads[1].startswith("TagMender "))
        self.assertEqual(heads[2], "Report a problem?")

    # ---- busy -----------------------------------------------------------------------
    def test_a_drop_during_a_run_waits_for_it_and_replaces_nothing(self):
        from PySide6.QtCore import QMimeData, QPoint, QUrl
        from PySide6.QtGui import QDropEvent
        ov = self.show_plan()
        was = ov.path
        other = Path(tempfile.mkdtemp(prefix="tm-later-"))
        ov.busy(True)
        try:
            self.assertTrue(self.win.act("add_folder").isEnabled())   # (a folder chosen waits as well)
            md = QMimeData()
            md.setUrls([QUrl.fromLocalFile(str(other))])
            with mock.patch.object(type(ov), "set_path") as set_path, \
                    mock.patch.object(type(ov), "add_waiting") as later:
                self.win.dropEvent(QDropEvent(QPoint(5, 5), Qt.CopyAction, md, Qt.LeftButton, Qt.NoModifier))
                set_path.assert_not_called()                          # (the preview is not replaced)
                self.assertEqual(ov.later, [str(other)])
                self.assertIn("added to the list when the current work ends",
                              self.win.statusBar().currentMessage())
                self.assertEqual(ov.path, was)
                ov.busy(False)
                settle(self.app)
                later.assert_called_once()                            # (the run ended: now added)
        finally:
            ov.later = []
            ov.busy(False)
            shutil.rmtree(other, ignore_errors=True)

    # ---- the toolbar's profile pop-up -------------------------------------------
    def test_the_profile_pop_up_is_shown_on_overview_only(self):
        label, box = self.win.profile_actions
        self.assertIs(self.win.toolbar.widgetForAction(box), self.win.profile_box)
        self.assertEqual(self.win.toolbar.widgetForAction(label).text(), "Profile")
        self.win.go("Overview")
        self.assertTrue(label.isVisible() and box.isVisible())
        self.win.go("Jobs")
        self.assertFalse(label.isVisible() or box.isVisible())
        self.assertNotIn("down-arrow:disabled { image: none", self.gui.style.stylesheet())

    # ---- the Needs attention badge ----------------------------------------------
    def test_the_badge_follows_the_page_not_the_preview(self):
        import tagmender
        att = self.win.pages["Needs attention"]
        att.root.setText(str(self.tmp / "elsewhere"))                 # the page looks in another folder:
        with mock.patch.object(tagmender, "_unresolved_albums", lambda p: [{"folder": "x"}] * 5) as _f:
            self.win.set_badge("Needs attention", 1)
            self.show_plan()                                          # its count stands
            settle(self.app, 20)
        self.assertEqual(self.win.badge("Needs attention"), 1)

    # ---- names ---------------------------------------------------------------------
    def test_album_words_one_spacing_and_the_watched_inbox_left_out(self):
        common = self.gui.common
        self.assertEqual(common.album_words("/m/North Pier/Signals"), "Signals — North Pier")
        self.assertEqual(common.album_words("/m/inbox/Moss - Garden", root="/m/inbox"), "Moss - Garden")
        self.assertEqual(common.album_words("/m/inbox/Moss/Garden", root="/m/inbox"), "Garden — Moss")
        self.assertEqual(self.gui.watched.watch_root("/m/inbox/Moss - Garden", ["/m/inbox/"]), "/m/inbox")
        self.assertIsNone(self.gui.watched.watch_root("/m/inbox/Moss/Garden", ["/m/inbox"]))
        att = self.gui.attention
        self.assertEqual(att.short_reason("no_candidate"), "Not found")
        self.assertEqual(att.short_reason("zz_new_code"), "Not identified")   # (a code is never the label)

    def test_the_inspector_reads_out_its_text_and_bullets_every_doubt(self):
        ov = self.show_plan()
        a = self.select(ov, "ready", "exact")
        insp = self.win.inspector
        self.assertEqual(insp.name.accessibleName(), "Album: " + self.gui.common.album_words(a["source_dir"]))
        self.assertEqual(insp.st_word.accessibleName(), "Status: Ready")
        self.assertTrue(insp.evidence.fields["Score"].accessibleName().startswith("Score: 97"))
        self.assertNotIn("<a", insp.evidence.fields["Source"].accessibleName())
        self.assertEqual(ov.summary.accessibleName(), "")              # (the counts are what is read)
        self.assertEqual(insp.buttons["release"].toolButtonStyle(), Qt.ToolButtonTextBesideIcon)
        insp.show_row({"folder": str(self.tmp / "A" / "B"), "codes": ["ambiguous_release"],
                       "reason": "two pressings of 2009 fit"})
        doubts = insp.evidence.fields["Doubts"].text().splitlines()
        self.assertEqual(len(doubts), 2)
        self.assertTrue(all(x.startswith("• ") for x in doubts), doubts)
        self.assertEqual(doubts[1], "• Two pressings of 2009 fit")

    def test_the_apply_sheet_lists_albums_once_and_hangs_their_lines(self):
        ov = self.show_plan()
        head, changes, undo, action = ov.apply_words()
        self.assertTrue(changes.startswith("<p>"))
        self.assertIn("<li>", changes)
        if "as listed" not in changes:                                 # (one outcome: said once)
            self.assertNotIn("names unchanged", changes)
        body = self.gui.sheets.confirm_body(changes, undo)
        import html
        self.assertTrue(body.endswith(f"<p>{html.escape(undo)}</p>"))

    # ---- layout at 960 x 600 -----------------------------------------------------
    def test_profiles_form_fits_at_960(self):
        self.win.go("Profiles")
        self.win.prefs_window.resize(740, 600)          # (Profiles is in Preferences: the page as narrow as
        pr = self.win.pages["Profiles"]                  #  it was beside a 960-point window's sidebar)
        names = [pr.list.item(i).data(Qt.UserRole) for i in range(pr.list.count())]
        import profiles as pf
        if "R3" not in names:
            pf.save(pf.from_dict({"name": "R3", "destination": "/tmp/somewhere/rather/long/library"}))
            pr.reload()
            names = [pr.list.item(i).data(Qt.UserRole) for i in range(pr.list.count())]
        pr.list.setCurrentRow(names.index("R3"))
        settle(self.app, 20)
        scroll = pr.editor
        self.assertLessEqual(pr.settings.width(), scroll.viewport().width())   # (nothing under the scroll bar)
        self.assertLessEqual(pr.list.width(), pr.LIST_NARROW)
        for w in (pr.desc, pr.op, pr.dest):
            right = w.mapTo(scroll.viewport(), w.rect().topRight()).x()
            self.assertLess(right, scroll.viewport().width(), w.accessibleName())

    def test_the_sidebar_keeps_its_width_when_the_inspector_opens_at_960(self):
        self.win.resize(960, 600)
        self.win.go("Overview")
        settle(self.app, 20)
        empty = self.win.nav.width()
        self.show_plan()
        settle(self.app, 30)
        self.assertTrue(self.win.inspector.isVisible())
        self.assertEqual(self.win.nav.width(), empty)
        self.assertEqual(self.win.nav.width(), self.gui.style.SIDEBAR[0])

    def test_stacked_tags_show_the_new_value_whole_and_rows_breathe(self):
        self.win.resize(960, 600)
        ov = self.show_plan()
        self.select(ov, "ready", "exact")
        self.win.inspector.show_tab("Tags")
        settle(self.app, 30)
        tags = self.win.inspector.tags
        self.assertTrue(tags.stacked)
        t = tags.table
        fm = t.fontMetrics()
        one = t.verticalHeader().defaultSectionSize()
        for r in range(t.rowCount()):
            text = t.item(r, 2).text()
            need = fm.boundingRect(0, 0, t.columnWidth(2) - self.gui.inspector.CELL_PAD, 100000,
                                   Qt.TextWordWrap, text).height()
            if "\n" in text:
                self.assertGreaterEqual(t.rowHeight(r), need + self.gui.inspector.ROW_AIR, text)
            else:
                self.assertGreaterEqual(t.rowHeight(r), min(need, one), text)
        self.assertIs(t.itemDelegateForColumn(1), tags.one_line)        # (a tag's name on one line)

    # ---- sheets' own buttons, rules and watching ----------------------------------
    def test_dialog_buttons_are_cancel_then_the_default_on_the_right(self):
        for dlg in (self.gui.WatchDialog(self.win.pages["Watched folders"]),
                    self.gui.RuleDialog(self.win.pages["Profiles"])):
            row = [b for b in dlg.findChildren(QPushButton) if b.text() in ("Cancel", "Watch Folder", "Save Rule")]
            row.sort(key=lambda b: b.mapTo(dlg, b.rect().topLeft()).x())
            self.assertEqual(row[0].text(), "Cancel")
            self.assertTrue(row[-1].isDefault())
            dlg.deleteLater()

    def test_a_rule_is_turned_on_or_off_from_the_menu_and_a_global_one_asks(self):
        import exclusions as ex
        pr = self.win.pages["Profiles"]
        self.win.go("Profiles")
        ex.save_rule(ex.Rule.from_dict({"id": "r3-karaoke", "name": "Karaoke", "action": "ignore",
                                        "target": "album", "when": {"all": [{"field": "album", "op": "contains",
                                                                              "value": "karaoke"}]}}), "global")
        pr.reload()
        pr.list.setCurrentRow(0)                                       # Global rules
        settle(self.app)
        row = next(r for r in range(pr.rules.rowCount()) if pr.rules.item(r, 1).text() == "Karaoke")
        pr.rules.selectRow(row)
        settle(self.app)
        a = self.win.act("toggle_rule")
        self.assertTrue(a.isEnabled() and a.isChecked())
        self.assertIn(a, self.win.menus["Rules"].actions())              # (Tools > Rules)
        self.gui.sheets.shown.clear()
        a.trigger()                                                    # (offscreen: the sheet says no)
        self.assertEqual(self.gui.sheets.shown[-1][1], "Turn the global rule “Karaoke” off?")
        self.assertTrue(next(r for r in ex.load_rules(None, include_disabled=True) if r.id == "r3-karaoke").enabled)
        with mock.patch.object(self.gui.sheets, "confirm", return_value=True):
            a.trigger()
        self.assertFalse(next(r for r in ex.load_rules(None, include_disabled=True) if r.id == "r3-karaoke").enabled)
        ex.remove_rule("r3-karaoke", "global")

    def test_keep_watching_asks_first_when_a_profile_files_without_asking(self):
        import profiles as pf
        w = self.win.pages["Watched folders"]
        pf.save(pf.from_dict({"name": "AutoR3", "operation": "move", "destination": str(self.tmp / "lib"),
                              "unattended": True, "sources": [{"path": str(self.tmp / "drop"), "watch": True}]}))
        try:
            self.gui.sheets.shown.clear()
            with mock.patch.object(type(w), "check_now") as checked:
                w.live.setChecked(True)                               # (offscreen: the sheet says no)
            checked.assert_not_called()
            self.assertFalse(w.live.isChecked())
            self.assertEqual(self.gui.sheets.shown[-1][1], "Keep watching while TagMender is open?")
            self.assertIn("drop", self.gui.sheets.shown[-1][2])
            with mock.patch.object(type(w), "check_now") as checked, \
                    mock.patch.object(self.gui.sheets, "confirm", return_value=True):
                w.live.setChecked(True)
            checked.assert_called_once()
            w.live.setChecked(False)
        finally:
            pf.path_of("AutoR3").unlink(missing_ok=True)

    def test_watched_outcomes_reach_the_status_bar(self):
        w = self.win.pages["Watched folders"]
        w.say("1 Done: Ferry.\nmore")
        self.assertEqual(w.out.text(), "1 Done: Ferry.\nmore")
        self.assertEqual(self.win.statusBar().currentMessage(), "1 Done: Ferry.")

    def test_menus_keep_one_narrow_check_column(self):
        self.assertTrue(self.win.menus["File"].property("checks"))       # (Read Scans is ticked there)
        self.assertFalse(self.win.menus["Album"].property("checks"))
        self.assertTrue(QApplication.testAttribute(Qt.AA_DontShowIconsInMenus))
        self.assertIn('QMenu[checks="true"]::item', self.gui.style.stylesheet())


if __name__ == "__main__":
    unittest.main()
