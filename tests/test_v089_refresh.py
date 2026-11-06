# 0.8.9: Refresh with the new settings - albums looked up and waiting to be applied are looked up
# again with the profile as it is now, after a setting changed: all of them, the ones the notice
# names, or the selected ones (offscreen Qt, a fake Discogs; no network).
import sys
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_engine as te  # noqa: E402
import match_albums as ma  # noqa: E402
import profiles as pf  # noqa: E402

try:
    from PySide6.QtWidgets import QApplication
except ImportError:                          # pragma: no cover
    QApplication = None


@unittest.skipIf(QApplication is None, "PySide6 not installed")
class Refresh(te.Base):
    @classmethod
    def setUpClass(cls):
        import gui
        cls.gui = gui
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        super().setUp()
        self.d = self.album("Band G", "Album G")
        self.album("Band H", "Album H", folder=self.d.parent / "Band H - Album H")
        self.http = te.FakeHttp(dg=[te.dg_release(930, "Band G", "Album G"), te.dg_release(931, "Band H", "Album H")])
        p = mock.patch.object(ma, "Http", lambda db, cfg, token: self.http)
        p.start()
        self.addCleanup(p.stop)
        pf.save(pf.from_dict({"name": "Mine", "providers": {"web": {"enabled": False}}, "artwork": {"enabled": False}}))
        self.win = self.gui.Main()
        self.win.show()
        self.ov = self.win.pages["Overview"]
        self.ov.refresh_profiles()
        self.ov.profile.setCurrentText("Mine")

    def tearDown(self):
        self.wait()
        self.win.close()
        self.win.deleteLater()
        super().tearDown()

    def wait(self):
        end = time.time() + 60
        while time.time() < end and (self.ov.running or self.win.threads()):
            self.app.processEvents()
            time.sleep(0.02)
        self.app.processEvents()

    def look_up_all(self):
        self.ov.path = str(self.d.parent)
        self.ov.preview()
        self.wait()
        self.assertEqual(len(self.ov.plan["albums"]), 2)

    def change_setting(self):
        d = pf.raw("Mine")
        pf._put(d, "matching.extra_careful", not pf.load("Mine").data["matching"].get("extra_careful", False))
        pf.save(pf.from_dict(d))

    def test_a_changed_setting_is_noticed_and_refresh_looks_up_again(self):
        self.look_up_all()
        self.assertEqual(self.ov.stale_albums(), [])                     # (the same settings: nothing to do)
        self.ov.check_stale()
        self.assertFalse(self.ov.stale_note.isVisibleTo(self.ov))
        self.change_setting()
        self.ov.check_stale()
        self.assertEqual(len(self.ov.stale_albums()), 2)
        self.assertTrue(self.ov.stale_note.isVisibleTo(self.ov))
        self.assertIn("Refresh with the new settings", self.ov.stale_note.text())
        before = len(self.http.calls)
        self.ov.refresh_stale()
        self.wait()
        self.assertGreater(len(self.http.calls), before)                  # (looked up again)
        self.assertEqual(self.ov.stale_albums(), [])
        self.assertFalse(self.ov.stale_note.isVisibleTo(self.ov))

    def test_the_same_settings_are_not_asked_twice(self):
        self.look_up_all()
        with mock.patch.object(self.gui.sheets, "question", return_value=False) as asked:
            self.ov.look_up("profile", list(self.ov.plan["albums"]))
        self.assertTrue(asked.called)                                     # ("already looked up": asked first)

    def test_refresh_takes_the_selected_album_only(self):
        self.look_up_all()
        self.change_setting()
        self.ov.table.selectRow(0)
        self.app.processEvents()
        chosen = self.ov.selected_albums()
        self.assertEqual(len(chosen), 1)
        self.ov.reload()                                                   # (the Refresh button, Ctrl+R)
        self.wait()
        self.assertEqual(len(self.ov.stale_albums()), 1)                   # (the other: still the old settings)
        self.assertTrue(self.ov.refresh_btn.isEnabled())


if __name__ == "__main__":
    unittest.main()
