# The app's side of the settings now read: Setup's Test Connection (each service's answer with
# an icon and words, on a worker thread), the Overview's line when a service is busy or down,
# and Preferences > Files (undo backups kept and cleaned, the sheet naming the jobs that lose
# Undo, the duplicates folder).
import os as _os, tempfile as _tempfile  # noqa: E402
_os.environ["XDG_CONFIG_HOME"] = _tempfile.mkdtemp(prefix="tm-config-")
_os.environ["XDG_STATE_HOME"] = _tempfile.mkdtemp(prefix="tm-state-")
_os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
_os.environ.setdefault("TAGMENDER_TAGGER_INI", "/nonexistent")

import json
import sqlite3
import sys
import time
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
try:
    from PySide6.QtCore import QEvent
    from PySide6.QtWidgets import QApplication
except ImportError:                                           # GUI is optional
    QApplication = None

import credentials  # noqa: E402

for _var in credentials.ENV.values():                         # (no key of the person running the tests)
    _os.environ.pop(_var, None)


def settle(app, n=10):
    for _ in range(n):
        app.processEvents()
        time.sleep(0.01)


@unittest.skipIf(QApplication is None, "PySide6 not installed")
class SettingsInTheApp(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import gui
        cls.gui = gui
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        import app_settings
        app_settings.save(dict(app_settings.DEFAULTS))
        self.gui.common._set_gui_pref("reduce_motion", True)
        self.gui.sheets.shown.clear()
        self.win = self.gui.Main()
        self.win.show()
        settle(self.app)

    def tearDown(self):
        end = time.time() + 30
        while time.time() < end and self.win.threads():
            settle(self.app, 2)
        self.win._closing = True
        self.win.close()
        if self.win.prefs_window is not None:
            self.win.prefs_window.deleteLater()
        self.win.deleteLater()
        QApplication.sendPostedEvents(None, QEvent.DeferredDelete)

    def test_test_connection_shows_each_answer(self):
        import connection
        panel = self.win.pages["Setup"].connection
        results = [connection.Result("musicbrainz", connection.OK, 120, 200, "answered in 120 ms"),
                   connection.Result("discogs", connection.REFUSED, 80, 401, "key refused (401)"),
                   connection.Result("acoustid", connection.BUSY, 0, 503, "busy"),
                   connection.Result("coverartarchive", connection.UNREACHABLE, 0, 0, "unreachable (no route)")]
        with mock.patch.object(connection, "test_all", return_value=results):
            panel.button.click()
            end = time.time() + 10
            while time.time() < end and not panel.button.isEnabled():
                settle(self.app, 2)
        words = {n: w.text() for n, (_p, w) in panel.rows.items()}
        self.assertEqual(words, {"musicbrainz": "Answered in 120 ms", "discogs": "Key refused",
                                 "acoustid": "Busy (503): try again later", "coverartarchive": "Unreachable"})
        self.assertTrue(all(not p.pixmap().isNull() for p, _w in panel.rows.values()))   # (a symbol with each)
        self.assertTrue(panel.button.accessibleName())

    def test_overview_says_when_a_service_is_down(self):
        import exclusions as ex
        ov = self.win.pages["Overview"]
        ex.STATE_DIR.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(ex.STATE_DIR / "cache.sqlite")
        db.execute("CREATE TABLE IF NOT EXISTS provider_health (host TEXT PRIMARY KEY, fails INT, last_error TEXT, "
                   "last_fail REAL, last_ok REAL)")
        db.execute("INSERT OR REPLACE INTO provider_health VALUES ('mb', 5, 'URLError: down', ?, 0)", (time.time(),))
        db.commit()
        db.close()
        ov.service_status(force=True)
        self.assertFalse(ov.service_note.isHidden())
        self.assertIn("MusicBrainz is not answering", ov.service_note.text())
        self.assertTrue(ov.service_note.text().startswith("⚠"))
        self.assertEqual(self.win.service_badge.text(), "⚠ MusicBrainz not answering")   # (on every page)
        self.assertFalse(self.win.service_badge.isHidden())
        db = sqlite3.connect(ex.STATE_DIR / "cache.sqlite")
        db.execute("UPDATE provider_health SET last_ok = ?", (time.time() + 1,))
        db.commit()
        db.close()
        ov.service_status(force=True)
        self.assertTrue(ov.service_note.isHidden())
        self.assertTrue(self.win.service_badge.isHidden())

    def test_files_pane_keeps_backups_and_asks_before_undo_ends(self):
        import app_settings
        import executor
        prefs = self.win.preferences()
        prefs.show_tab("Files")
        pane = prefs.files
        old = executor.jobs_dir() / "20200101-000000-bbbb"
        (old / "blobs").mkdir(parents=True, exist_ok=True)
        (old / "journal.jsonl").write_text(json.dumps({"ev": "end", "t": time.time() - 40 * 86400}) + "\n")
        app_settings.set_value("backups.keep_days", 90)
        pane.keep.setCurrentIndex(pane.keep.findData(90))
        # shortening to 30 days with cleaning on: the sheet names the job; offscreen it is cancelled
        pane.keep.setCurrentIndex(pane.keep.findData(30))
        pane.set_keep(0)
        self.assertTrue(any(k == "confirm" and "older jobs" in what for k, what, _b in self.gui.sheets.shown))
        self.assertEqual(app_settings.get("backups.keep_days"), 90)
        self.assertEqual(pane.keep.currentData(), 90)
        # with cleaning off, nothing is lost at once: saved without asking
        app_settings.set_value("backups.auto_clean", False)
        pane.auto.setChecked(False)
        pane.keep.setCurrentIndex(pane.keep.findData(7))
        pane.set_keep(0)
        self.assertEqual(app_settings.get("backups.keep_days"), 7)
        # the duplicates folder: a plain name, else refused and put back
        pane.dupes.setText("Second Copies")
        pane.set_dupes()
        self.assertEqual(app_settings.duplicates_folder(), "Second Copies")
        pane.dupes.setText("a/b")
        pane.set_dupes()
        self.assertEqual(app_settings.duplicates_folder(), "Second Copies")
        self.assertEqual(pane.dupes.text(), "Second Copies")


if __name__ == "__main__":
    unittest.main()
