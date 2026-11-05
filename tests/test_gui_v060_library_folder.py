# Tests for the Overview's Library folder row (0.6.0): a library folder for one preview and Apply.
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_gui as tg  # noqa: E402  (sets the isolated config, offscreen Qt)
import profiles as pf  # noqa: E402


@unittest.skipIf(tg.QApplication is None, "PySide6 not installed")
class LibraryFolder(tg.Gui):
    def setUp(self):
        super().setUp()
        self.home = self.tmp / "profile-lib"
        self.other = self.tmp / "other-lib"
        self.home.mkdir()
        self.other.mkdir()
        pf.save(pf.from_dict({"name": "Filer", "operation": "copy", "destination": str(self.home),
                              "providers": {"web": {"enabled": False}}, "artwork": {"enabled": False},
                              "sources": [{"path": str(self.lib), "destination": str(self.home / "own")}]}))
        self.ov = self.win.pages["Overview"]
        self.ov.refresh_profiles()
        self.ov.profile.setCurrentText("Filer")

    def preview(self):
        self.ov.set_path(str(self.lib))
        self.assertTrue(tg.wait(lambda: self.ov.plan is not None and not self.ov.running, self.app))

    def dsts(self):
        return [fe["dst"] for a in self.ov.plan["albums"] for fe in a.get("files") or [] if fe.get("dst")]

    def choose(self, folder):
        with mock.patch.object(self.gui.sheets, "choose_folder", return_value=str(folder)):
            self.ov.choose_library()

    def test_default_shows_where_the_folder_previewed_goes(self):
        self.assertEqual(self.ov.library_label.text(), "Library folder")
        self.assertEqual(self.ov.library_where.text(), str(self.home))
        self.preview()                               # (the folder's own destination wins: shown)
        self.assertEqual(self.ov.library_where.text(), str(self.home / "own"))
        self.assertFalse(self.ov.library_keep.isVisibleTo(self.ov))

    def test_choosing_marks_and_plans_under_it(self):
        self.preview()
        self.choose(self.other)
        self.assertEqual(self.ov.library_label.text(), "Library folder ↺")
        self.assertTrue(self.ov.library_keep.isVisibleTo(self.ov))
        self.assertTrue(tg.wait(lambda: not self.ov.running, self.app))   # (looked up again by itself)
        dsts = self.dsts()
        self.assertTrue(dsts)
        self.assertTrue(all(d.startswith(str(self.other)) for d in dsts), dsts)

    def test_missing_folder_keeps_old_value(self):
        with mock.patch.object(self.gui.sheets, "warning") as warned:
            self.choose(self.tmp / "nope")
        warned.assert_called_once()
        self.assertEqual(self.ov.library, "")

    def test_profile_change_and_new_folder_reset(self):
        self.choose(self.other)
        self.assertEqual(self.ov.library, str(self.other))
        self.ov.profile.setCurrentText("Auto")
        self.assertEqual(self.ov.library, "")
        self.ov.profile.setCurrentText("Filer")
        self.choose(self.other)
        self.ov.set_path(str(self.lib))
        self.assertEqual(self.ov.library, "")
        self.assertTrue(tg.wait(lambda: not self.ov.running, self.app))

    def test_in_place_disables(self):
        self.ov.profile.setCurrentText("Auto")
        self.assertFalse(self.ov.library_btn.isEnabled())
        self.assertTrue(self.ov.library_btn.toolTip())

    def test_make_it_the_profiles_saves_destination(self):
        self.preview()
        self.choose(self.other)
        self.assertTrue(tg.wait(lambda: not self.ov.running, self.app))
        with mock.patch.object(self.gui.sheets, "question", return_value=True):
            self.ov.keep_library()
        self.assertEqual(pf.load("Filer")["destination"], str(self.other))
        self.assertFalse(pf.load("Filer")["sources"][0].get("destination"))   # (it would win over it)
        self.assertEqual(self.ov.library, "")
        self.assertEqual(self.ov.library_label.text(), "Library folder")

    def test_choosing_the_folder_it_already_goes_to_changes_nothing(self):
        self.preview()
        self.choose(self.home / "own")
        self.assertEqual(self.ov.library, "")
        self.assertFalse(self.ov.running)

    def test_apply_refuses_a_preview_made_for_another_folder(self):
        self.preview()
        self.choose(self.other)
        self.assertTrue(tg.wait(lambda: not self.ov.running, self.app))
        self.ov.library = str(self.home)                 # (changed again; that look-up failed)
        with mock.patch.object(self.gui.sheets, "information") as told, \
                mock.patch.object(self.gui.sheets, "confirm") as asked:
            self.ov.apply()
        told.assert_called_once()
        asked.assert_not_called()

    def test_a_release_you_chose_is_kept_when_the_folder_changes(self):
        self.preview()
        self.ov.forced = {"x": "discogs:1"}
        with mock.patch.object(self.ov, "preview") as again, mock.patch.object(self.ov, "look_up") as looked:
            self.choose(self.other)
        again.assert_called_once()
        looked.assert_not_called()


for _n in dir(tg.Gui):                 # (only this file's tests: not test_gui's again)
    if _n.startswith("test_") and _n not in LibraryFolder.__dict__:
        setattr(LibraryFolder, _n, None)


if __name__ == "__main__":
    unittest.main()
