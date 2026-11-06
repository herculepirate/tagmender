# 0.8.6: the profile editor shows which settings all profiles share; a change of one reaches
# every profile not keeping its own; Only This Profile keeps it to one (offscreen Qt).
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_gui as tg  # noqa: E402
import profiles as pf  # noqa: E402


@unittest.skipIf(tg.QApplication is None, "PySide6 not installed")
class SharedEditor(tg.Gui):
    def page(self, name):
        for n in ("A", "B"):
            if not pf.path_of(n).exists():
                pf.save(pf.from_dict({"name": n, "version": pf.SCHEMA_VERSION}))
        self.win.go("Profiles")
        p = self.win.pages["Profiles"]
        p.refresh() if hasattr(p, "refresh") else None
        p.show(name)
        self.app.processEvents()
        return p

    def test_a_shared_change_reaches_the_other_profile(self):
        from gui.profile_editor import SHARED_ROWS
        self.assertIn("genres.max", SHARED_ROWS)
        self.assertNotIn("destination", SHARED_ROWS)
        p = self.page("A")
        row = p.editor.rows["genres.max"]
        self.assertTrue(row.scope.isVisibleTo(p.editor))
        self.assertIn("All profiles", row.scope.text())
        self.assertFalse(p.editor.rows["operation"].scope.isVisibleTo(p.editor))
        p.editor.set_value("genres.max", 2)
        self.assertTrue(p.save())
        self.assertEqual(pf.load("B").data["genres"]["max"], 2)
        self.assertEqual(pf.load("A").data["genres"]["max"], 2)

    def test_only_this_profile(self):
        p = self.page("A")
        p.editor.set_scope("genres.max", True)
        self.assertIn("This profile only", p.editor.rows["genres.max"].scope.text())
        p.editor.set_value("genres.max", 7)
        self.assertTrue(p.save())
        self.assertEqual(pf.load("A").data["genres"]["max"], 7)
        self.assertNotEqual(pf.load("B").data["genres"]["max"], 7)
        p.editor.set_scope("genres.max", False)                     # (back: the shared value shown)
        self.assertNotEqual(p.editor.value("genres.max"), 7)
        self.assertTrue(p.save())
        self.assertEqual(pf.load("A").data["genres"]["max"], pf.load("B").data["genres"]["max"])


    def test_only_this_profile_kept_before_any_shared_value(self):
        pf.shared_file().unlink(missing_ok=True)
        p = self.page("A")
        default = p.editor.value("genres.max")
        p.editor.set_scope("genres.max", True)
        p.editor.set_value("genres.max", default)
        self.assertTrue(p.save())
        self.assertIn("genres.max", pf.raw("A")["own_settings"])
        p.show("B")
        self.app.processEvents()
        p.editor.set_value("genres.max", default + 3)
        self.assertTrue(p.save())
        self.assertEqual(pf.load("A").data["genres"]["max"], default)

    def test_a_preset_changes_only_this_profile(self):
        p = self.page("A")
        before = pf.load("B").data["artwork"]["enlarge"]
        p.editor.apply_preset("keep" if before else "collector")
        self.assertTrue(p.editor.is_own("artwork.enlarge"))
        self.assertTrue(p.save())
        self.assertEqual(pf.load("B").data["artwork"]["enlarge"], before)
        self.assertNotEqual(pf.load("A").data["artwork"]["enlarge"], before)

    def test_a_shared_change_that_would_break_another_profile_is_refused(self):
        p = self.page("A")
        d = pf.raw("B")
        pf._put(d, "subfolders.misc_name", "Notes")
        pf.save(pf.from_dict(d))
        p.show("A")
        self.app.processEvents()
        p.editor.set_value("subfolders.covers_name", "Notes")
        from unittest import mock
        with mock.patch.object(self.gui.sheets, "warning") as warned:
            self.assertFalse(p.save())
        warned.assert_called_once()
        self.assertIn("every profile", warned.call_args[0][2])
        self.assertEqual(pf.load("B").data["subfolders"]["misc_name"], "Notes")


if __name__ == "__main__":
    unittest.main()
