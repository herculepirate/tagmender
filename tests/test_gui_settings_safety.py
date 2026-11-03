# The settings in the app kept safe: the scroll wheel changes a pop-up or stepper only once it is
# clicked, a hand-written number outside a stepper's range is never cut on an unrelated save,
# Restore Defaults keeps the lossy library folder and what happens to the files, rows that only
# work at the Audiophile level say so, a key added in Setup counts without reopening the profile,
# and a Preferences setting that could not be saved shows the stored value again.
import os as _os, tempfile as _tempfile  # noqa: E402
_os.environ["XDG_CONFIG_HOME"] = _tempfile.mkdtemp(prefix="tm-config-")
_os.environ["XDG_STATE_HOME"] = _tempfile.mkdtemp(prefix="tm-state-")
_os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
_os.environ.setdefault("TAGMENDER_TAGGER_INI", "/nonexistent")

import sys
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "tests"))

from test_gui_settings_ui import Base, QApplication, settle  # noqa: E402


def wheel(widget, steps=-1):
    from PySide6.QtCore import QPoint, QPointF, Qt
    from PySide6.QtGui import QWheelEvent
    pos = QPointF(widget.width() / 2, widget.height() / 2)
    ev = QWheelEvent(pos, widget.mapToGlobal(pos), QPoint(), QPoint(0, 120 * steps), Qt.NoButton, Qt.NoModifier,
                     Qt.NoScrollPhase, False)
    QApplication.sendEvent(widget, ev)


class Editor(Base):
    def test_the_wheel_changes_nothing_until_the_control_is_clicked(self):
        from gui.rows import Popup, Stepper
        pr = self.page("Whl")
        ed = pr.editor
        level, genres = ed.controls["tag_level"], ed.controls["genres.max"]
        self.assertIsInstance(level, Popup)
        self.assertIsInstance(genres, Stepper)
        before = (ed.value("tag_level"), ed.value("genres.max"))
        ed.controls["description"].setFocus() if "description" in ed.controls else None
        wheel(level)
        wheel(genres)
        settle(self.app, 2)
        self.assertEqual((ed.value("tag_level"), ed.value("genres.max")), before)
        self.assertEqual(ed.changed_keys(), [])

    def test_a_number_outside_the_steppers_range_is_kept(self):
        pr = self.page("Rty", retry={"after_days": 30, "max_attempts": 30})
        ed = pr.editor
        self.assertEqual(ed.value("retry.max_attempts"), 30)
        self.assertEqual(ed.changed_keys(), [])
        ed.set_value("genres.max", 3)
        pr.save()
        self.assertEqual(self.pf.raw("Rty")["retry"]["max_attempts"], 30)

    def test_restore_defaults_keeps_the_lossy_folder_and_the_operation(self):
        lib, lossy = self.tmp / "lib", self.tmp / "lossy"
        lib.mkdir()
        lossy.mkdir()
        pr = self.page("Lsy", operation="copy", destination=str(lib), destinations={"lossy": str(lossy)},
                       genres={"max": 9})
        ed = pr.editor
        ed.restore_defaults()
        self.assertEqual(ed.value("genres.max"), 5)
        pr.save()
        raw = self.pf.raw("Lsy")
        self.assertEqual(raw.get("operation"), "copy")
        self.assertEqual((raw.get("destinations") or {}).get("lossy"), str(lossy))

    def test_rows_that_only_work_at_audiophile_say_so(self):
        pr = self.page("Aud")
        ed = pr.editor
        keys = ("quality_action", "loudness.replace_existing", "loudness.album_gain", "loudness.opus_r128")
        ed.set_value("tag_level", "enthusiast")
        for k in keys:
            self.assertFalse(ed.rows[k].control.isEnabled(), k)
            self.assertIn("Audiophile", ed.rows[k].reason.text())
        ed.set_value("tag_level", "audiophile")
        for k in keys:
            self.assertTrue(ed.rows[k].control.isEnabled(), k)
        ed.set_value("audiophile.replaygain", False)
        self.assertFalse(ed.rows["loudness.album_gain"].control.isEnabled())

    def test_a_key_added_in_setup_counts_on_return(self):
        import requirements as rq
        with mock.patch.object(rq, "ready", return_value=False):
            pr = self.page("Key")
            ed = pr.editor
            self.assertFalse(ed.rows["acoustid"].control.isEnabled())
        with mock.patch.object(rq, "ready", return_value=True):
            self.win.go("Setup")
            settle(self.app, 2)
            self.win.go("Profiles")
            settle(self.app, 3)
            self.assertTrue(ed.rows["acoustid"].control.isEnabled())


class Preferences(Base):
    def test_a_refused_setting_shows_the_stored_value_again(self):
        import app_settings
        from gui.preferences import NetworkPane
        pane = NetworkPane()
        with mock.patch.object(app_settings, "set_value", side_effect=ValueError("no")):
            pane.langs.set_value(["eng", "deu"])
            pane.langs.changed.emit()
            pane.effort.setCurrentIndex(pane.effort.findData("thorough"))
        self.assertEqual(pane.langs.value(), app_settings.get("ocr.languages"))
        self.assertEqual(pane.effort.currentData(), app_settings.get("ocr.effort"))
        pane.deleteLater()


if __name__ == "__main__":
    unittest.main()
