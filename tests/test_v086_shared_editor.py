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


if __name__ == "__main__":
    unittest.main()
