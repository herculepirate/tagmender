# The settings in the app, second review: the Collector preset moves nothing inside your album
# folders, the lossy-folder rows and Include Subfolders say when they do nothing, change sheets
# translate list values, the naming example follows the two name settings, Save Profile with a
# problem in an Advanced fold opens it, and a screen reader hears each Test Connection result.
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


@unittest.skipIf(QApplication is None, "PySide6 not installed")
class EditorRows(Base):
    def test_collector_turns_on_no_moves_inside_album_folders(self):
        from gui import profile_editor as pe
        data = pe.preset_data("Mine", "collector")
        self.assertNotIn("subfolders", data)
        self.assertNotIn("operation", data)

    def test_lossy_rows_say_they_need_copy_or_move(self):
        from gui import profile_editor as pe
        for key in ("destinations.lossy",):
            self.assertTrue(pe.BY_KEY[key].enable({"_op": "in_place"}), key)
            self.assertIsNone(pe.BY_KEY[key].enable({"_op": "copy"}), key)

    def test_list_values_are_translated_in_the_change_sheets(self):
        from gui import profile_editor as pe
        with mock.patch.object(pe, "tr", side_effect=lambda t: "XX" + t):
            words = pe.words_of(pe.BY_KEY["providers.web.sources"], ["deezer", "bandcamp"])
        self.assertEqual(words, "XXDeezer, XXBandcamp")

    def test_the_naming_example_follows_the_name_settings(self):
        from gui import profile_editor as pe
        st = pe.ProfileEditor._name_settings({"tagger.ascii_filenames": True,
                                              "tagger.replace_spaces_with_underscores": True})
        self.assertEqual(st, {"ascii_filenames": True, "replace_spaces_with_underscores": True})
        self.page("Names")
        ed = self.win.pages["Profiles"].editor
        ed.set_value("tagger.replace_spaces_with_underscores", True)
        settle(self.app, 5)
        self.assertIn("Ada_Lark", ed.preview.text())

    def test_save_with_a_problem_in_an_advanced_fold_opens_it(self):
        from gui import profile_editor as pe
        self.page("Fat")
        pr = self.win.pages["Profiles"]
        ed = pr.editor
        c = ed.controls["filenames.compat"]
        other = next(c.itemData(i) for i in range(c.count()) if c.itemData(i) not in ("windows", None))
        ed.set_value("filenames.compat", other)
        ed.set_value("filenames.fat", True)
        settle(self.app, 10)
        self.assertGreater(ed.problem_count, 0)
        self.assertTrue(pr.save_btn.isEnabled())
        sid = pe.GROUPS[pe.BY_KEY[ed.first_problem_key].group][0]
        self.assertFalse(ed.advanced[sid].isChecked())
        pr.save_btn.click()
        settle(self.app, 5)
        self.assertTrue(ed.advanced[sid].isChecked())
        self.assertTrue(ed.first_problem.isVisible())


@unittest.skipIf(QApplication is None, "PySide6 not installed")
class ConnectionResultsReadOut(Base):
    def test_a_screen_reader_hears_the_result(self):
        import connection
        from gui.setup import ConnectionPanel
        p = ConnectionPanel(None)
        p.answered([connection.Result("musicbrainz", "ok", 200, 200, "")])
        word = p.rows["musicbrainz"][1]
        self.assertIn(word.text(), word.accessibleName())
        self.assertTrue(word.accessibleName().startswith("MusicBrainz connection"))
        p.deleteLater()


if __name__ == "__main__":
    unittest.main()
