# Tests for gui.py, offscreen: the window drives the same engine/planner/executor as the CLI.
# Set TAGMENDER_SHOTS=/some/dir to save screenshots of each step.
import os as _os, tempfile as _tempfile  # noqa: E402
_os.environ["XDG_CONFIG_HOME"] = _tempfile.mkdtemp(prefix="tm-config-")
_os.environ["XDG_STATE_HOME"] = _tempfile.mkdtemp(prefix="tm-state-")
_os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import sys
import time
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "tests"))
try:
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication, QMessageBox
except ImportError:                                           # GUI is optional
    QApplication = None
import test_engine as te  # noqa: E402
import exclusions as ex  # noqa: E402
import match_albums as ma  # noqa: E402
import profiles as pf  # noqa: E402
import tagio  # noqa: E402

SHOTS = _os.environ.get("TAGMENDER_SHOTS")


def wait(cond, app, timeout=60):
    end = time.time() + timeout
    while time.time() < end:
        app.processEvents()
        if cond():
            return True
        time.sleep(0.02)
    return False


@unittest.skipIf(QApplication is None, "PySide6 not installed")
class Gui(te.Base):
    @classmethod
    def setUpClass(cls):
        import gui
        cls.gui = gui
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setStyleSheet(gui.stylesheet())

    def setUp(self):
        super().setUp()
        self.d = self.album("Band G", "Album G")
        self.d2 = self.album("Band N", "Album N [no vox]")
        self.http = te.FakeHttp(dg=[te.dg_release(920, "Band G", "Album G"),
                                    te.dg_release(921, "Band N", "Album N [no vox]")])
        p = mock.patch.object(ma, "Http", lambda db, cfg, token: self.http)
        p.start()
        self.addCleanup(p.stop)
        ex._write(ex.rules_file(), {"rules": [{"id": "no-vox", "name": "[no vox] editions", "action": "ignore",
                                               "target": "album", "when": {"all": [{"field": "album", "op": "contains",
                                                                                     "value": "[no vox]"}]}}]})
        pf.save(pf.from_dict({"name": "Auto", "unattended": True, "providers": {"web": {"enabled": False}},
                              "artwork": {"enabled": False}}))
        self.win = self.gui.Main()
        self.win.resize(1180, 760)
        self.win.show()

    def tearDown(self):
        self.win.close()
        super().tearDown()

    def shot(self, name):
        if SHOTS:
            Path(SHOTS).mkdir(parents=True, exist_ok=True)
            self.app.processEvents()
            self.win.grab().save(str(Path(SHOTS) / f"{name}.png"))

    def test_preview_shows_excluded_as_ignored_then_apply(self):
        ov = self.win.pages["Overview"]
        ov.refresh_profiles()
        ov.profile.setCurrentText("Auto")
        self.shot("1-empty")
        before = (self.d2 / "01.flac").read_bytes()
        ov.set_path(str(self.lib))
        self.assertTrue(wait(lambda: ov.plan is not None, self.app), ov.summary.text())
        self.assertTrue(ov.results.isVisible())
        rows = {ov.model.data(ov.model.index(i, 1)).split("   ")[0]: ov.model.data(ov.model.index(i, 0))
                for i in range(ov.model.rowCount())}
        self.assertEqual(rows["2005 - Album G"], "●  Ready")
        self.assertEqual(rows["2005 - Album N [no vox]"], "⊘  Ignored")      # never "Unresolved"
        ov.table.selectRow(0)
        self.app.processEvents()
        self.assertIn("Plan", ov.detail.toPlainText())
        self.shot("2-preview")
        ov.search.setText("no vox")
        self.assertEqual(ov.proxy.rowCount(), 1)
        ov.search.setText("")
        self.assertTrue(ov.btn_apply.isEnabled())
        ov.apply()                                                     # unattended profile: no question
        self.assertTrue(wait(lambda: ov.plan is None and not ov.btn_cancel.isEnabled(), self.app),
                        ov.summary.text())
        self.assertIn("Job", ov.summary.text())
        self.assertEqual(tagio.read(self.d / "01.flac")["discogs_release_id"], ["920"])
        self.assertEqual((self.d2 / "01.flac").read_bytes(), before)
        self.shot("3-applied")
        jobs = self.win.pages["Jobs"]
        self.assertGreaterEqual(jobs.table.rowCount(), 1)
        self.win.go("Jobs")
        self.shot("4-jobs")

    def test_rules_page_lists_and_tests_rules(self):
        pr = self.win.pages["Profiles"]
        pr.list.setCurrentRow(0)                                       # (global rules)
        self.app.processEvents()
        self.assertEqual(pr.rules.rowCount(), 1)
        self.assertIn('album contains "[no vox]"', pr.rules.item(0, 4).text())
        with mock.patch.object(self.gui.QFileDialog, "getExistingDirectory", return_value=str(self.lib)):
            pr.test_rules()
        self.assertTrue(wait(lambda: "Nothing was changed" in pr.test_out.toPlainText(), self.app))
        out = pr.test_out.toPlainText()
        self.assertIn("Ignored", out)
        self.assertIn("Album N [no vox]", out)
        self.assertNotIn("Album G", out)
        self.win.go("Profiles")
        self.shot("5-rules")

    def test_rule_dialog_builds_a_literal_case_insensitive_rule(self):
        dlg = self.gui.RuleDialog(self.win)
        dlg.name.setText("Live albums")
        dlg.action.setCurrentText("skip")
        dlg.conds.item(0, 2).setText("[live]")
        dlg.add_cond({"field": "folder", "op": "contains", "value": "(Live)"})
        dlg.match.setCurrentIndex(1)
        r = dlg.rule()
        self.assertEqual((r.action, r.target, list(r.when)), ("skip", "album", ["any"]))
        self.assertEqual(r.when["any"][0], {"field": "album", "op": "contains", "value": "[live]"})
        self.assertTrue(ex.matches(r.when, {"album": ["Show [LIVE] 1999"]}))

    def test_profile_tagging_level_is_saved(self):
        import profiles as pf
        pf.save(pf.from_dict({"name": "Lvl"}))
        pr = self.win.pages["Profiles"]
        pr.reload()
        self.app.processEvents()
        row = [pr.list.item(i).text() for i in range(pr.list.count())].index("Lvl")
        pr.list.setCurrentRow(row)
        self.app.processEvents()
        self.assertEqual(pr.level.currentText(), "enthusiast")
        self.assertFalse(pr.aud["lyrics"].isEnabled())
        pr.level.setCurrentText("audiophile")
        self.assertTrue(pr.aud["lyrics"].isEnabled())
        pr.aud["acoustid"].setChecked(False)
        pr.save()
        p = pf.load("Lvl")
        self.assertEqual((p["tag_level"], p["audiophile"]["acoustid"], p["audiophile"]["lyrics"]),
                         ("audiophile", False, True))

    def test_profile_edition_settings_are_saved_and_offer_picard_lines(self):
        import profiles as pf
        pf.save(pf.from_dict({"name": "Ed"}))
        pr = self.win.pages["Profiles"]
        pr.reload()
        self.app.processEvents()
        pr.list.setCurrentRow([pr.list.item(i).text() for i in range(pr.list.count())].index("Ed"))
        self.app.processEvents()
        self.assertEqual((pr.editions.currentData(), pr.album_editions.currentData()), ("off", "off"))
        self.assertFalse(pr.picard_btn.isEnabled())
        pr.editions.setCurrentIndex(pr.editions.findData("edition_year"))
        self.assertTrue(pr.picard_btn.isEnabled())
        self.assertIn("$if(%_edition%, [%_edition%])", pr.picard_script_text())
        pr.album_editions.setCurrentIndex(pr.album_editions.findData("edition"))
        self.assertIn("$set(album,%album% [%_edition%])", pr.picard_script_text())
        pr.save()
        n = pf.load("Ed")["naming"]
        self.assertEqual((n["editions"], n["album_editions"]), ("edition_year", "edition"))
        pr.reload()
        self.app.processEvents()
        pr.list.setCurrentRow([pr.list.item(i).text() for i in range(pr.list.count())].index("Ed"))
        self.app.processEvents()
        self.assertEqual((pr.editions.currentData(), pr.album_editions.currentData()), ("edition_year", "edition"))

    def test_settings_never_show_a_saved_key(self):
        st = self.win.pages["Settings"]
        st.fields["fanart"].setText("SECRET-FANART")
        with mock.patch.object(QMessageBox, "information"):
            st.save()
        self.assertEqual(st.fields["fanart"].text(), "")
        self.assertIn("set (file)", st.fields["fanart"].placeholderText())
        self.win.go("Settings")
        st.run_checks()
        self.assertNotIn("SECRET-FANART", st.checks.toPlainText())
        self.shot("6-settings")

    def test_every_page_has_a_keyboard_route_and_accessible_names(self):
        for i, (name, _c) in enumerate(self.gui.PAGES):
            self.win.go(name)
            self.assertEqual(self.win.stack.currentWidget(), self.win.pages[name])
        ov = self.win.pages["Overview"]
        for w in (ov.drop, ov.table, ov.search, ov.profile, ov.detail):
            self.assertTrue(w.accessibleName(), w)
        self.assertEqual(ov.btn_preview.shortcut().toString(), "Ctrl+P")


if __name__ == "__main__":
    unittest.main()
