# File > Add Files… (decided 2026-11-20): one or more audio files chosen in a file picker, taken
# exactly as the same files dropped on the window (manual_match.add_dropped): with no list, the
# file is previewed; with a list, its album joins it; during a look-up it joins that look-up when
# it can (LKP-1). In the File menu after Add Folder and in the toolbar beside it. No network.
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_gui as tg  # noqa: E402  (sets the isolated config, offscreen Qt)
import test_engine as te  # noqa: E402
from test_gui import wait  # noqa: E402


@unittest.skipIf(tg.QApplication is None, "PySide6 not installed")
class AddFiles(tg.Gui):
    def setUp(self):
        super().setUp()
        self.ov = self.win.pages["Overview"]
        self.plain_profile()
        self.x = self.album("Band X", "Album X", folder=str(self.tmp / "elsewhere" / "Band X" / "2005 - Album X"))
        self.http.dg[924] = te.dg_release(924, "Band X", "Album X")

    def plain_profile(self):
        """No artwork or web look-ups: nothing reaches the network."""
        import profiles as pf
        pf.save(pf.from_dict({"name": "Plain", "providers": {"web": {"enabled": False}},
                              "artwork": {"enabled": False}}))
        self.ov.refresh_profiles()
        self.ov.profile.setCurrentText("Plain")

    def pick(self, files):
        """Add Files with FILES chosen in the picker; returns what reached the window's drop."""
        from gui import sheets
        got = []
        real = self.win.dropped

        def dropped(paths):
            got.append(list(paths))
            return real(paths)
        with mock.patch.object(sheets, "choose_files", lambda *a, **k: [str(f) for f in files]), \
                mock.patch.object(self.win, "dropped", dropped):
            self.win.act("add_files").trigger()
        return got

    def rows(self):
        return sorted((a["source_dir"], a["status"], tuple(sorted(a.get("album_files") or ())))
                      for a in self.ov.model.albums)

    def test_in_the_file_menu_after_add_folder_and_in_the_toolbar(self):
        from gui import actions
        a = self.win.act("add_files")
        self.assertEqual(a.text().replace("&", ""), "Add Files…")
        self.assertEqual(a.shortcut().toString(), "Ctrl+Alt+O")
        self.assertFalse(a.icon().isNull())
        file_menu = next(m for m in self.win.menuBar().actions() if m.text().replace("&", "") == "File").menu()
        keys = [x.objectName() for x in file_menu.actions() if not x.isSeparator()]
        self.assertEqual(keys[keys.index("add_folder") + 1], "add_files")
        self.assertNotIn("add_files", actions.TOOLBAR)                # (the toolbar is full: File menu and Ctrl+Alt+O)
        # (its key is no other command's)
        taken = [k for k, spec in actions.COMMANDS.items() if k != "add_files" and str(spec[1]) == "Ctrl+Alt+O"]
        self.assertEqual(taken, [])

    def test_the_picker_offers_audio_files_and_records_offscreen(self):
        from gui import sheets
        sheets.shown.clear()
        self.win.act("add_files").trigger()                       # (offscreen: nothing chosen)
        self.assertEqual(sheets.shown[-1][0], "choose_files")
        self.assertIsNone(self.ov.plan)

    def test_files_chosen_with_no_list_are_previewed_as_a_drop(self):
        f = self.x / "01.flac"
        got = self.pick([f, self.tmp / "notes.txt"])               # (not audio: left out, as a drop)
        self.assertEqual(got, [[str(f)]])
        self.assertTrue(wait(lambda: self.ov.plan is not None and not self.ov.running, self.app), self.ov.summary.text())
        self.assertEqual(self.ov.path, str(f))
        picked = self.rows()
        # (the same file dropped: the same rows)
        self.win.close()
        self.win = self.gui.Main()
        self.win.show()
        self.ov = self.win.pages["Overview"]
        self.plain_profile()
        self.win.dropped([str(f)])
        self.assertTrue(wait(lambda: self.ov.plan is not None and not self.ov.running, self.app), self.ov.summary.text())
        self.assertEqual(self.rows(), picked)

    def test_files_chosen_with_a_list_join_it_as_the_same_files_dropped(self):
        ov = self.ov
        ov.set_path(str(self.d))
        self.assertTrue(wait(lambda: ov.plan is not None and not ov.running, self.app), ov.summary.text())
        files = sorted(self.x.glob("*.flac"))
        self.assertEqual(self.pick(files), [[str(f) for f in files]])
        self.assertTrue(wait(lambda: not ov.running and any(Path(a["source_dir"]) == self.x for a in ov.model.albums),
                             self.app), ov.summary.text())
        picked = [r for r in self.rows() if Path(r[0]) == self.x]
        self.assertEqual(len(picked), 1)                             # (one album, as dropping its files)
        self.assertEqual(ov.roots.get(ov.album_key(next(a for a in ov.model.albums if Path(a["source_dir"]) == self.x))),
                         str(self.x))

    def test_files_chosen_during_a_look_up_join_it(self):
        """LKP-1: picked while a look-up runs, they join it as dropped files do - from another
        folder, as a plan of its own - and are found before it ends."""
        import threading
        import engine
        ov = self.ov
        ov.set_path(str(self.d))
        self.assertTrue(wait(lambda: ov.plan is not None and not ov.running, self.app), ov.summary.text())
        gate, real = threading.Event(), engine.Engine.identify_all
        self.addCleanup(gate.set)

        def gated(e, g):
            if Path(g.folder) == self.d:
                gate.wait(30)
            return real(e, g)
        with mock.patch.object(engine.Engine, "identify_all", gated):
            ov.look_up("profile", [a for a in ov.model.albums if Path(a["source_dir"]) == self.d], again=True)
            self.assertTrue(ov.running)
            self.pick([self.x / "01.flac"])
            self.assertTrue(wait(lambda: any(Path(a["source_dir"]) == self.x and a["status"] == "ready"
                                             for a in ov.model.albums), self.app, timeout=30), ov.summary.text())
            self.assertTrue(ov.running)                               # (found before the look-up ends)
            gate.set()
            self.assertTrue(wait(lambda: not ov.running, self.app), ov.summary.text())


for _n in dir(tg.Gui):                                  # (the base class's own tests run there)
    if _n.startswith("test_"):
        setattr(AddFiles, _n, None)


if __name__ == "__main__":
    unittest.main()
