# 0.8.9: Apply to All Profiles… on a setting of the profile editor puts that setting, as the
# form shows it, into every profile - one that kept its own value of a shared one too (offscreen Qt).
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_gui as tg  # noqa: E402
import profiles as pf  # noqa: E402


@unittest.skipIf(tg.QApplication is None, "PySide6 not installed")
class ApplyAll(tg.Gui):
    def page(self, name, others=("B", "C")):
        for n in (name,) + tuple(others):
            if not pf.path_of(n).exists():
                pf.save(pf.from_dict({"name": n, "version": pf.SCHEMA_VERSION}))
        self.win.go("Profiles")
        p = self.win.pages["Profiles"]
        p.refresh() if hasattr(p, "refresh") else None
        p.show(name)
        self.app.processEvents()
        return p

    def apply(self, p, key, yes=True):
        from gui import sheets
        with mock.patch.object(sheets, "confirm", return_value=yes) as asked:
            got = p.apply_to_all(key)
        return got, asked

    def test_every_row_offers_it_but_the_description(self):
        p = self.page("A")
        rows = p.editor.rows
        self.assertTrue(hasattr(rows["operation"], "more"))                   # (not shared: its own ⋯ menu)
        texts = [a.text() for a in rows["operation"].more.menu().actions()]
        self.assertIn("Apply to All Profiles…", texts)
        shared = [a.text() for a in rows["album_artist.lead"].scope.menu().actions()]
        self.assertIn("Apply to All Profiles…", shared)                       # (shared: in its scope menu)
        self.assertFalse(hasattr(rows["album_artist.lead"], "more"))
        self.assertFalse(hasattr(rows["description"], "more"))

    def test_a_shared_setting_reaches_a_profile_that_kept_its_own(self):
        p = self.page("A")
        d = pf.raw("C")                                                      # (C keeps its own: off)
        d["own_settings"] = ["album_artist.lead"]
        pf._put(d, "album_artist.lead", False)
        pf.save(pf.from_dict(d))
        p.show("A")
        p.editor.set_value("album_artist.lead", True)                         # (unsaved: saved first)
        got, asked = self.apply(p, "album_artist.lead")
        self.assertTrue(got)
        self.assertIn("in all profiles", asked.call_args.args[1])
        for n in ("A", "B", "C"):
            self.assertIs(pf.load(n).data["album_artist"]["lead"], True, n)
        self.assertNotIn("album_artist.lead", pf.raw("C").get("own_settings") or [])
        self.assertFalse(p.dirty())

    def test_a_profile_setting_is_copied_and_nothing_else(self):
        p = self.page("A")
        pf.save(pf.from_dict(dict(pf.raw("B"), description="keep me")))
        before = pf.load("B").data
        p.show("A")
        new = not p.editor.value("matching.extra_careful")
        p.editor.set_value("matching.extra_careful", new)
        got, _ = self.apply(p, "matching.extra_careful")
        self.assertTrue(got)
        after = pf.load("B").data
        self.assertIs(after["matching"]["extra_careful"], new)
        self.assertEqual(after["description"], "keep me")
        after["matching"].pop("extra_careful")
        before["matching"].pop("extra_careful", None)
        self.assertEqual(after, before)                                     # (nothing else changed)

    def test_a_profile_that_cannot_take_it_stops_all(self):
        p = self.page("A")
        from gui import sheets
        was = pf.raw("B")
        p.editor.set_value("operation", "copy")                            # (B has no library folder)
        with mock.patch.object(sheets, "confirm", return_value=True), \
                mock.patch.object(sheets, "warning") as warned:
            self.assertFalse(p.apply_to_all("operation"))
        self.assertTrue(warned.called)
        self.assertEqual(pf.raw("B"), was)

    def test_a_profile_file_that_cannot_be_read_is_named_not_a_crash(self):
        import json
        from gui import sheets
        p = self.page("A")
        pf.path_of("C").write_text(json.dumps({"name": "C", "version": pf.SCHEMA_VERSION,
                                               "matching": "oops", "naming": 5}))
        was = pf.raw("B")
        p.editor.set_value("matching.extra_careful", not p.editor.value("matching.extra_careful"))
        with mock.patch.object(sheets, "confirm", return_value=True), \
                mock.patch.object(sheets, "warning") as warned:
            self.assertFalse(p.apply_to_all("matching.extra_careful"))
        self.assertIn("C:", warned.call_args.args[2])
        self.assertEqual(pf.raw("B"), was)

    def test_dont_save_on_closing_preferences_puts_the_file_back(self):
        from gui import sheets
        p = self.page("A")
        key = "matching.extra_careful"
        was = p.editor.value(key)
        p.editor.set_value(key, not was)
        self.assertTrue(p.dirty())
        with mock.patch.object(sheets, "ask", return_value="Don't Save"):
            self.assertTrue(self.win.prefs_window.close())
        self.assertEqual(p.editor.value(key), was)                       # (the file's, not the edit)
        self.assertFalse(p.dirty())

    def test_reopened_on_profiles_save_profile_works(self):
        self.page("A")
        self.win.prefs_window.close()
        self.win.preferences()
        self.assertTrue(self.win.showing("Profiles"))
        self.assertTrue(self.win.commands["save_profile"].isEnabled())

    def test_a_write_failing_part_way_is_said_not_called_done(self):
        from gui import sheets
        p = self.page("A")
        real, n = pf.save, []

        def full_disk(prof, **kw):
            n.append(prof)
            if len(n) == 2:
                raise OSError(28, "No space left on device")
            return real(prof, **kw)
        p.editor.set_value("matching.extra_careful", not p.editor.value("matching.extra_careful"))
        self.assertTrue(p.save())
        with mock.patch.object(sheets, "confirm", return_value=True), \
                mock.patch.object(sheets, "warning") as warned, mock.patch.object(pf, "save", full_disk):
            self.assertFalse(p.apply_to_all("matching.extra_careful"))
        self.assertIn("Not changed", warned.call_args.args[2])

    def test_cancel_writes_nothing(self):
        p = self.page("A")
        was = pf.raw("B")
        p.editor.set_value("genres.max", 9)
        got, _ = self.apply(p, "genres.max", yes=False)
        self.assertFalse(got)
        self.assertEqual(pf.raw("B"), was)
        self.assertTrue(p.dirty())                                           # (A's change still unsaved)


if __name__ == "__main__":
    unittest.main()
