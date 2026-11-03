# Translations (gui/i18n.py), offscreen: tr() gives the English as written when no translation is
# loaded (and calls no Qt then); a tagmender_<lang>.qm compiled here from a .ts with one message
# is loaded for the language chosen and translates through tr(), N_() marks only; Preferences >
# General lists System, English and the translations found, and saves the choice in gui.json;
# the sheets' default buttons and a module table's words go through tr() where they are shown;
# tools/i18n_extract.sh gathers tr() and N_() texts into one context. Everything in temp folders.
import os as _os, tempfile as _tempfile  # noqa: E402
_os.environ["XDG_CONFIG_HOME"] = _tempfile.mkdtemp(prefix="tm-config-")
_os.environ["XDG_STATE_HOME"] = _tempfile.mkdtemp(prefix="tm-state-")
_os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
_os.environ.setdefault("TAGMENDER_TAGGER_INI", "/nonexistent")

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
try:
    from PySide6.QtWidgets import QApplication
except ImportError:                                           # GUI is optional
    QApplication = None

LRELEASE = shutil.which("pyside6-lrelease")
LUPDATE = shutil.which("pyside6-lupdate")
TS = """<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE TS>
<TS version="2.1" language="de">
<context>
    <name></name>
    <message><source>Preview</source><translation>Vorschau</translation></message>
    <message><source>Saved {name}.</source><translation>{name} gespeichert.</translation></message>
    <message><source>Cancel</source><translation>Abbrechen</translation></message>
    <message><source>Ready</source><translation>Bereit</translation></message>
</context>
</TS>
"""


@unittest.skipIf(QApplication is None, "PySide6 not installed")
class I18n(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        from gui import i18n
        self.i18n = i18n
        self.tmp = Path(tempfile.mkdtemp(prefix="tm-i18n-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.addCleanup(i18n.install, self.app, "en")       # (back to English for the next test)
        from gui.common import _set_gui_pref
        _set_gui_pref("language", "system")

    def compiled(self):
        if not LRELEASE:
            self.skipTest("pyside6-lrelease not installed")
        ts = self.tmp / "tagmender_de.ts"
        ts.write_text(TS, encoding="utf-8")
        subprocess.run([LRELEASE, str(ts), "-qm", str(self.tmp / "tagmender_de.qm")], check=True,
                       capture_output=True)
        return self.tmp

    def test_without_a_translation_tr_gives_the_text_and_calls_no_qt(self):
        i18n = self.i18n
        i18n.install(self.app, "en")
        with mock.patch.object(i18n.QCoreApplication, "translate", side_effect=AssertionError("Qt called")):
            self.assertEqual(i18n.tr("Preview"), "Preview")
            self.assertEqual(i18n.tr("Saved {name}.").format(name="x.zip"), "Saved x.zip.")
            self.assertEqual(i18n.tr(""), "")
        self.assertEqual(i18n.N_("Preview"), "Preview")

    def test_a_translation_is_loaded_for_the_language_chosen(self):
        i18n = self.i18n
        with mock.patch.object(i18n, "I18N", self.compiled()):
            self.assertEqual(i18n.available(), [("de", "Deutsch")])
            self.assertEqual(i18n.install(self.app, "de"), "de")
            self.assertEqual(i18n.tr("Preview"), "Vorschau")
            self.assertEqual(i18n.tr("Saved {name}.").format(name="x.zip"), "x.zip gespeichert.")
            self.assertEqual(i18n.tr("Not in the catalogue"), "Not in the catalogue")
            from gui import sheets, style
            from gui.common import label_for
            self.assertEqual(label_for({"status": "ready"})[1], "Bereit")      # (a table's word, shown)
            self.assertEqual(style.STATUS["ready"][1], "Ready")                 # (the table keeps English)
            from PySide6.QtWidgets import QDialog
            dlg = QDialog()
            self.addCleanup(dlg.deleteLater)
            row, ok = sheets.button_row(dlg, "Go", lambda: None)
            self.assertEqual(row.itemAt(1).widget().text(), "Abbrechen")
            self.assertIsNone(i18n.install(self.app, "fr"))                    # (no French: English)
            self.assertEqual(i18n.tr("Preview"), "Preview")

    def test_the_system_language_is_the_default(self):
        i18n = self.i18n
        with mock.patch.object(i18n, "I18N", self.compiled()), \
                mock.patch.object(i18n.QLocale, "system", return_value=i18n.QLocale("de_DE")):
            self.assertEqual(i18n.chosen(), "system")
            self.assertEqual(i18n.install(self.app), "de")
            self.assertEqual(i18n.tr("Preview"), "Vorschau")

    def test_preferences_lists_the_languages_and_saves_the_choice(self):
        i18n = self.i18n
        with mock.patch.object(i18n, "I18N", self.compiled()):
            from gui.preferences import GeneralPane
            pane = GeneralPane(None)
            self.addCleanup(pane.deleteLater)
            box = pane.language
            self.assertEqual([box.itemData(i) for i in range(box.count())], ["system", "en", "de"])
            self.assertEqual([box.itemText(i) for i in range(box.count())], ["System", "English", "Deutsch"])
            self.assertEqual(box.currentData(), "system")
            box.setCurrentIndex(box.findData("de"))
            from gui.common import _gui_prefs
            self.assertEqual(_gui_prefs()["language"], "de")
            self.assertEqual(i18n.tr("Preview"), "Preview")                    # (from the next start)
            pane2 = GeneralPane(None)
            self.addCleanup(pane2.deleteLater)
            self.assertEqual(pane2.language.currentData(), "de")

    def test_a_language_chosen_whose_translation_is_gone_stays_listed(self):
        from gui import preferences
        from gui.common import _set_gui_pref
        _set_gui_pref("language", "pt_BR")
        with mock.patch.object(self.i18n, "I18N", self.tmp):
            got = preferences.language_choices()
        self.assertEqual(got[-1], ("pt_BR", "pt_BR (no translation)"))

    @unittest.skipIf(not LUPDATE, "pyside6-lupdate not installed")
    def test_extract_gathers_tr_and_n_texts_in_one_context(self):
        import xml.etree.ElementTree as ET
        root = self.tmp / "repo"
        (root / "gui").mkdir(parents=True)
        (root / "tools").mkdir()
        shutil.copy(HERE / "tools" / "i18n_extract.sh", root / "tools")
        (root / "gui" / "a.py").write_text(
            'from .i18n import N_, tr\nTABLE = {"k": N_("From a table")}\n\n\nclass A:\n'
            '    def f(self):\n        return tr("Saved {name}.").format(name="x"), tr("Open", "a folder")\n',
            encoding="utf-8")
        subprocess.run(["bash", str(root / "tools" / "i18n_extract.sh"), "--new", "de"], check=True,
                       capture_output=True, cwd=root)
        for name in ("tagmender.ts", "tagmender_de.ts"):
            t = ET.parse(root / "assets" / "i18n" / name)
            self.assertEqual([c.findtext("name") for c in t.iter("context")], [""])
            self.assertEqual(sorted(m.findtext("source") for m in t.iter("message")),
                             ["From a table", "Open", "Saved {name}."])
        self.assertEqual(ET.parse(root / "assets" / "i18n" / "tagmender_de.ts").getroot().get("language"), "de")
        self.assertFalse((root / "assets" / "i18n" / "tagmender_de.qm").exists())


if __name__ == "__main__":
    unittest.main(argv=[sys.argv[0]], verbosity=2)
