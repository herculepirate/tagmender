# The Setup page (offscreen): a row for every key, login and optional program the requirements
# registry lists, their statuses, saving a key (a 0600 file in a temporary config folder), the
# first start opening on Setup and Set Up Later, the commands that need something missing saying
# exactly what with Open Setup, the Overview's and inspector's notices, and no key ever shown.
import os as _os, tempfile as _tempfile  # noqa: E402
_os.environ["XDG_CONFIG_HOME"] = _tempfile.mkdtemp(prefix="tm-config-")
_os.environ["XDG_STATE_HOME"] = _tempfile.mkdtemp(prefix="tm-state-")
_os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
_os.environ.setdefault("TAGMENDER_TAGGER_INI", "/nonexistent")

import shutil
import stat
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
    from PySide6.QtCore import QEvent
    from PySide6.QtWidgets import QAbstractButton, QApplication, QLabel, QLineEdit, QWidget
except ImportError:                                           # GUI is optional
    QApplication = None

import credentials  # noqa: E402
import requirements as rq  # noqa: E402

for _var in credentials.ENV.values():                         # (no key of the person running the tests)
    _os.environ.pop(_var, None)

SECRET = "SECRET-TOKEN-7f3a9c"                                 # (made up: must never appear in a widget)
MAIL = "made-up@example.org"
RID = "11111111-2222-3333-4444-555555555555"


def settle(app, n=10):
    for _ in range(n):
        app.processEvents()
        time.sleep(0.01)


def texts(root):
    """Every word a widget under root shows or tells a screen reader."""
    out = []
    for w in [root] + root.findChildren(QWidget):
        for get in ("text", "placeholderText", "toolTip", "accessibleName", "accessibleDescription", "windowTitle"):
            f = getattr(w, get, None)
            if callable(f):
                try:
                    v = f()
                except TypeError:
                    continue
                if isinstance(v, str):
                    out.append(v)
    return "\n".join(out)


@unittest.skipIf(QApplication is None, "PySide6 not installed")
class SetupPage(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import gui
        from gui import setup
        cls.gui, cls.setup = gui, setup
        cls.app = QApplication.instance() or QApplication([])
        cls.tmp = Path(tempfile.mkdtemp(prefix="tm-setup-"))

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def setUp(self):
        import exclusions as ex
        for f in ex.CONFIG_DIR.glob("*"):                      # (each test starts with nothing set up)
            if f.is_file():
                f.unlink()
        self.gui.common._set_gui_pref("reduce_motion", True)
        self.gui.sheets.shown.clear()
        self.win = self.gui.Main()
        self.win.resize(1180, 760)
        self.win.show()
        settle(self.app)
        self.page = self.win.pages["Setup"]

    def tearDown(self):
        end = time.time() + 30
        while time.time() < end and self.win.threads():
            settle(self.app, 2)
        self.page.discard()
        self.win._closing = True                              # (no "Save changes?" from an earlier page)
        self.win.close()
        if self.win.prefs_window is not None:
            self.win.prefs_window.deleteLater()
        self.win.deleteLater()
        QApplication.sendPostedEvents(None, QEvent.DeferredDelete)

    # ---- the rows -----------------------------------------------------------
    def test_a_row_for_every_registry_entry_in_its_section(self):
        self.assertEqual(set(self.page.rows), set(rq.ids()))
        listed = [i for _t, ids in self.setup.sections() for i in ids]
        self.assertEqual(sorted(listed), sorted(rq.ids()))                     # (each once)
        self.assertEqual(self.setup.sections()[0][1][:2], ["discogs", "acoustid"])
        for i, r in self.page.rows.items():
            e = rq.entry(i)
            self.assertEqual(r.title.text(), e.title)
            self.assertIn(e.url, r.link.text())                                # (where to get it, a link)
            words = texts(r)
            self.assertIn(e.enables, words)
            self.assertIn(e.without, words)
            if e.kind == "key":
                self.assertIsNotNone(r.field, i)
                self.assertEqual(r.field.echoMode(), QLineEdit.Password if e.secret else QLineEdit.Normal, i)
            elif e.kind == "login":
                self.assertIsNotNone(r.button)
            else:
                self.assertEqual(r.cmd.text(), e.install)                       # (the install command)
        # reachable from the sidebar, the View menu and Help > Set Up TagMender…
        self.assertIn("Setup", [n for n, _c in self.gui.PAGES])
        help_menu = [a.objectName() for a in self.win.menus["Help"].actions() if not a.isSeparator()]
        self.assertIn("setup", help_menu)
        self.win.go("Overview")
        self.win.act("setup").trigger()
        self.assertIs(self.win.stack.currentWidget(), self.page)

    def test_statuses(self):
        r = self.page.rows
        self.assertIn("Not set", r["discogs"].word.text())
        self.assertIn("Not logged in", r["musicbrainz_login"].word.text())
        rq.store("discogs", SECRET)
        p = rq.path_of("discogs")
        with mock.patch.object(rq.shutil, "which", lambda name: None if name == "fpcalc" else "/usr/bin/" + name):
            self.page.again.click()
        self.assertIn(">Set<", r["discogs"].word.text())
        self.assertEqual(r["discogs"].pic.accessibleName(), "Set")
        self.assertIn("Missing", r["fpcalc"].word.text())
        self.assertIn("Installed", r["ffmpeg"].word.text())
        os_mode = p.stat().st_mode
        p.chmod(0o644)                                                         # (others can read it)
        try:
            self.page.refresh()
            self.assertIn("Fix permissions", r["discogs"].word.text())
            self.assertIn("chmod 600", r["discogs"].note.text())
            self.assertFalse(r["discogs"].note.isHidden())
        finally:
            p.chmod(stat.S_IMODE(os_mode))
        # an optional entry not set is muted, a recommended one is not
        self.assertEqual(self.setup.state_look("fanart", rq.NOT_SET)[1], "muted")
        self.assertEqual(self.setup.state_look("discogs", rq.NOT_SET)[1], "unresolved")
        self.assertNotIn(SECRET, texts(self.win))

    def test_saving_a_key_writes_a_0600_file_and_never_shows_it(self):
        self.win.go("Setup")
        f = self.page.rows["discogs"].field
        self.assertFalse(self.page.save_button.isEnabled())                    # (nothing typed)
        f.setText(SECRET)
        self.page.rows["gnudb"].field.setText(MAIL)
        self.assertTrue(self.page.save_button.isEnabled())
        self.assertEqual(self.page.save(), 2)
        p = rq.path_of("discogs")
        self.assertEqual(stat.S_IMODE(p.stat().st_mode), 0o600)
        self.assertEqual(p.read_text().strip(), SECRET)
        self.assertEqual(stat.S_IMODE(rq.path_of("gnudb").stat().st_mode), 0o600)
        self.assertEqual(f.text(), "")                                         # (emptied at once)
        self.assertEqual(f.placeholderText(), "Saved on this computer")
        self.assertIn("Saved 2 keys.", self.page.saved.text())
        self.assertEqual(rq.status("discogs"), rq.SET)
        self.assertTrue(self.gui.common._gui_prefs().get(self.setup.FIRST_RUN_PREF))
        # Preferences opened afterwards, and the page after it: no key, no e-mail address anywhere
        prefs = self.win.preferences()
        for name in ("Keys", "Account"):
            prefs.show_tab(name)
        settle(self.app)
        for root in (self.win, prefs):
            words = texts(root)
            self.assertNotIn(SECRET, words)
            self.assertNotIn(MAIL, words)
        # Return in a field saves too; an environment variable wins, and the row says so
        self.page.rows["fanart"].field.setText("made-up-fanart")
        self.page.rows["fanart"].field.returnPressed.emit()
        self.assertEqual(rq.status("fanart"), rq.SET)
        with mock.patch.dict(_os.environ, {credentials.ENV["lastfm"]: "made-up-env"}):
            self.page.refresh()
            self.assertIn(credentials.ENV["lastfm"], self.page.rows["lastfm"].note.text())
            self.assertNotIn("made-up-env", texts(self.win))

    def test_first_start_opens_setup_and_set_up_later_skips_it(self):
        self.win.go("Overview")
        self.assertTrue(rq.nothing_set_up())
        self.assertTrue(self.win.first_run())
        self.assertIs(self.win.stack.currentWidget(), self.page)
        self.assertFalse(self.page.banner.isHidden())
        self.assertIn("Welcome to TagMender", self.page.welcome_text.text())
        self.page.later.click()
        self.assertIs(self.win.stack.currentWidget(), self.win.pages["Overview"])
        self.assertTrue(self.page.banner.isHidden())
        self.assertFalse(self.win.first_run())                                  # (skipped: not again)

    def test_first_start_shows_setup_once_even_when_left_from_the_sidebar(self):
        self.win.go("Overview")
        self.assertTrue(self.win.first_run())
        self.assertTrue(self.gui.common._gui_prefs().get(self.setup.FIRST_RUN_PREF))
        self.win.go("Overview")                                                  # (the sidebar, not Set Up Later)
        self.assertTrue(rq.nothing_set_up())
        self.assertFalse(self.win.first_run())
        self.assertIs(self.win.stack.currentWidget(), self.win.pages["Overview"])

    def test_first_start_is_not_shown_once_a_key_is_set(self):
        rq.store("acoustid", SECRET)
        self.win.go("Overview")
        self.assertFalse(self.win.first_run())
        self.assertIs(self.win.stack.currentWidget(), self.win.pages["Overview"])

    # ---- commands that need what is missing -------------------------------
    def album(self):
        return {"source_dir": str(self.tmp / "Artist" / "Album"), "status": "ready",
                "release": {"musicbrainz_albumid": [RID], "album": ["Album"]},
                "files": [{"src": str(self.tmp / "a.flac"), "dst": str(self.tmp / "a.flac"), "op": "none"}]}

    def test_submit_acoustids_without_keys_says_which_and_opens_setup(self):
        from gui import acoustid_submit
        self.win.go("Overview")
        with mock.patch.object(self.gui.sheets, "ask", return_value="Open Setup") as ask:
            self.assertIsNone(acoustid_submit.submit_album(self.win, self.album()))
        head, body = ask.call_args.args[1], ask.call_args.args[2]
        self.assertEqual(head, "AcoustID is not set up for sending fingerprints.")
        self.assertIn(rq.notice("acoustid", "app"), body)
        self.assertIn(rq.notice("acoustid_user", "app"), body)
        self.assertIn("Open Setup", ask.call_args.args[3])
        self.assertIs(self.win.stack.currentWidget(), self.page)               # (Open Setup: the page)
        first = next(i for i in acoustid_submit.NEEDS if rq.problem(i))
        r = self.page.rows[first]
        self.assertTrue((r.field or r.copy).hasFocus() or self.app.focusWidget() is None)

    def test_collections_without_a_login_say_what_is_missing(self):
        from gui import account
        acct = account.account_of(self.win)
        self.gui.sheets.shown.clear()
        self.assertFalse(account.open_collections(self.win, self.album(), acct))
        kind, head, body = self.gui.sheets.shown[-1]
        self.assertEqual(head, "Log in to MusicBrainz first.")
        self.assertIn(rq.notice("mb_client_id", "app"), body)                  # (no application yet: that first)
        rq.store("mb_client_id", "made-up-client")
        rq.store("mb_client_secret", SECRET)
        self.win.go("Overview")
        with mock.patch.object(self.gui.sheets, "ask", return_value="Open Setup") as ask:
            self.assertFalse(account.submit_ratings(self.win, self.album(), None, acct))
        body = ask.call_args.args[2]
        self.assertIn("you are not logged in to MusicBrainz", body)
        self.assertNotIn("client ID", body)
        self.assertIs(self.win.stack.currentWidget(), self.page)
        self.assertNotIn(SECRET, texts(self.win))

    def test_play_without_qt_multimedia_says_so(self):
        self.win._player_missing = True
        with mock.patch.object(type(self.win.player), "available", new_callable=mock.PropertyMock,
                               return_value=False), \
                mock.patch.object(rq, "problem", lambda i, where="any": rq.notice(i, where, rq.MISSING)
                                  if i == "qt_multimedia" else ""), \
                mock.patch.object(self.win, "album", return_value=("overview", self.album())):
            self.win.update_player_actions()
            self.assertTrue(self.win.act("play_pause").isEnabled())             # (stays on: it says why)
            self.win.play_pause()
        self.assertEqual(self.gui.sheets.shown[-1][1], "The player is not available.")
        self.assertIn("Qt Multimedia is not installed", self.gui.sheets.shown[-1][2])

    def test_look_up_cd_without_discid_links_to_its_row(self):
        from gui import browser
        import cdlookup
        with mock.patch.object(cdlookup, "can_read_drive", return_value=False):
            dlg = self.win.lookup_cd()
            settle(self.app)
            self.assertFalse(dlg.no_drive.isHidden())
            self.assertIn("setup:discid", dlg.no_drive.text())
            self.assertIn("Open Setup", dlg.no_drive.text())
            dlg.no_drive.linkActivated.emit("setup:discid")
        settle(self.app)
        self.assertIs(self.win.stack.currentWidget(), self.page)
        self.assertNotIn("pip", browser.NO_DISCID)

    # ---- notices after a preview, in the inspector and in Checks -----------------
    def test_a_preview_without_discogs_says_so_with_open_setup(self):
        import gui_screens
        plan = gui_screens.sample_plan(self.tmp)
        plan["provider_notes"] = {"discogs": "missing_credentials: " + rq.notice("discogs"),
                                  "musicbrainz": "rate limited"}
        ov = self.win.pages["Overview"]
        self.win.go("Overview")
        ov.path = str(self.tmp)
        ov.planned(plan)
        settle(self.app)
        self.assertFalse(ov.setup_note.isHidden())
        self.assertIn("Discogs is not used: no Discogs token. Add one in Setup", ov.setup_note.text())
        self.assertIn('href="setup:discogs"', ov.setup_note.text())
        self.assertNotIn("missing_credentials", ov.summary.text())
        self.assertIn("musicbrainz: rate limited", ov.summary.text())          # (other notes stay there)
        ov.setup_note.linkActivated.emit("setup:discogs")
        self.assertIs(self.win.stack.currentWidget(), self.page)
        # a key saved since: the next preview has no banner
        rq.store("discogs", SECRET)
        self.win.go("Overview")
        ov.planned(dict(plan, provider_notes={}))
        self.assertTrue(ov.setup_note.isHidden())

    def test_evidence_and_needs_attention_give_the_notice(self):
        import gui_screens
        plan = gui_screens.sample_plan(self.tmp)
        a = next(x for x in plan["albums"] if x["status"] == "unresolved")
        a.setdefault("identification", {})["provider_errors"] = {"discogs": "missing_credentials"}
        self.win.inspector.evidence.show_album(a, plan)
        doubts = self.win.inspector.evidence.fields["Doubts"].text()
        self.assertIn(rq.notice("discogs", "app"), doubts)
        self.assertNotIn("had a problem: missing_credentials", doubts)
        self.win.inspector.evidence.show_row({"folder": str(self.tmp), "codes": ["missing_credentials"], "reason": ""})
        self.assertIn(rq.notice("acoustid", "app"), self.win.inspector.evidence.fields["Doubts"].text())
        att = self.win.pages["Needs attention"]
        att.show_rows([{"folder": str(self.tmp / "A"), "codes": ["missing_credentials"], "reason": "no key"}])
        self.assertIn(rq.notice("discogs", "app"), att.table.item(0, 0).toolTip())

    def test_a_check_about_a_key_opens_its_setup_row(self):
        import doctor
        prefs = self.win.preferences()
        prefs.show_tab("Checks")
        prefs.checks.show_found([doctor.Check("discogs key", "warn", "not set", rq.notice("discogs", "app"))])
        settle(self.app)
        links = [lb for lb in prefs.checks.findChildren(QLabel) if 'href="setup"' in lb.text()]
        self.assertEqual(len(links), 1)
        links[0].linkActivated.emit("setup")
        self.assertIs(self.win.stack.currentWidget(), self.page)

    # ---- closing ------------------------------------------------------------------
    def test_closing_with_a_key_typed_asks(self):
        self.page.rows["tavily"].field.setText(SECRET)
        with mock.patch.object(self.gui.sheets, "ask", return_value="Save") as ask:
            self.assertTrue(self.page.may_leave())
        self.assertIn("Save the 1 key you typed?", ask.call_args.args[1])
        self.assertEqual(rq.status("tavily"), rq.SET)
        self.page.rows["mojeek"].field.setText(SECRET)
        with mock.patch.object(self.gui.sheets, "ask", return_value="Cancel"):
            self.assertFalse(self.page.may_leave())
        self.page.discard()
        self.assertTrue(self.page.may_leave())                                  # (nothing typed: no sheet)

    def test_the_page_fits_960_by_600_and_every_control_is_named(self):
        self.win.resize(960, 600)
        self.win.go("Setup")
        settle(self.app, 20)
        self.assertEqual((self.win.width(), self.win.height()), (960, 600))
        for w in self.page.findChildren(QAbstractButton) + self.page.findChildren(QLineEdit):
            self.assertTrue(w.accessibleName() or w.text(), w)
        self.win.search_box.setText("fingerprint")
        self.assertFalse(self.page.rows["acoustid_user"].isHidden())
        self.assertTrue(self.page.rows["gnudb"].isHidden())
        self.win.search_box.clear()
        self.assertFalse(self.page.rows["gnudb"].isHidden())


if __name__ == "__main__":
    unittest.main(verbosity=2)
