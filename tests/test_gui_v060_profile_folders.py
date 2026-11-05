# 0.6.0: a profile no longer says which folders it covers. The Watched folders page pairs a folder with
# a profile; the profile editor keeps the profile's sources as they are.
import os as _os
import tempfile as _tempfile
_os.environ["XDG_CONFIG_HOME"] = _tempfile.mkdtemp(prefix="tm-config-")
_os.environ["XDG_STATE_HOME"] = _tempfile.mkdtemp(prefix="tm-state-")
_os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import copy
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "tests"))
from test_gui_settings_ui import Base, QApplication  # noqa: E402
import profiles as pf  # noqa: E402
import test_watch_folders as twf  # noqa: E402
import watcher  # noqa: E402

SOURCES = [{"path": "/srv/inbox-a", "watch": True, "recursive": False, "destination": "/srv/lib-a"},
           {"path": "/srv/inbox-b", "watch": False, "recursive": True}]


@unittest.skipIf(QApplication is None, "PySide6 not installed")
class ProfileFolders(Base):
    def test_the_folder_rows_are_gone(self):
        from gui import profile_editor as pe
        self.assertNotIn("_sources", pe.BY_KEY)
        self.assertNotIn("_recursive", pe.BY_KEY)
        self.assertNotIn("folders", pe.GROUPS)
        self.assertTrue(all(s.group in pe.GROUPS for s in pe.SETTINGS))
        pr = self.page("Src")
        self.assertNotIn("_sources", pr.editor.rows)
        self.assertNotIn("_recursive", pr.editor.rows)

    def test_the_rules_button_is_still_reachable(self):
        pr = self.page("Src")
        ed = pr.editor
        got = []
        ed.open_rules.connect(lambda: got.append(1))
        from PySide6.QtWidgets import QPushButton
        btns = [b for b in ed.findChildren(QPushButton) if b.text() == "Ignore and Skip Rules…"]
        self.assertEqual(len(btns), 1)
        btns[0].click()
        self.assertEqual(got, [1])

    def test_sources_survive_an_editor_save(self):
        from gui import profile_editor as pe
        full = pf.from_dict({"name": "x", "version": pf.SCHEMA_VERSION}, check=False).data
        raw = {"name": "x", "sources": copy.deepcopy(SOURCES), "operation": "copy", "destination": "/srv/lib"}
        ed = pe.ProfileEditor()
        ed.load(copy.deepcopy(raw), pf.from_dict(dict(raw, version=pf.SCHEMA_VERSION), check=False).data, full)
        ed.set_value("formats", ["lossless"])
        out = ed.write(copy.deepcopy(raw))
        self.assertEqual(out["sources"], SOURCES)
        self.assertEqual(out["formats"], ["lossless"])

    def test_watch_dialog_subfolders_round_trip(self):
        pf.save(pf.from_dict({"name": "Mover", "operation": "copy", "destination": str(self.tmp / "lib")}))
        inbox = self.tmp / "inbox"
        inbox.mkdir()
        wp = self.win.pages["Watched folders"]
        dlg = self.gui.WatchDialog(wp, "", "Mover")
        self.assertTrue(dlg.deep.isChecked())
        self.assertEqual(dlg.deep.accessibleName(), "Include subfolders")
        dlg.path.setText(str(inbox))
        dlg.deep.setChecked(False)
        dlg.save()
        src = [s for s in pf.load("Mover")["sources"] if s["path"] == str(inbox)][0]
        self.assertIs(src["recursive"], False)
        self.assertTrue(src["watch"])
        again = self.gui.WatchDialog(wp, str(inbox), "Mover")
        self.assertFalse(again.deep.isChecked())
        again.deep.setChecked(True)
        again.save()
        src = [s for s in pf.load("Mover")["sources"] if s["path"] == str(inbox)][0]
        self.assertIs(src["recursive"], True)


class ShallowWatch(twf.DropBase):
    def test_subfolders_off_files_only_albums_directly_in_the_folder(self):
        prof = self.profile(sources=[{"path": str(self.inbox), "watch": True, "recursive": False}])
        deep = self.drop("Band X", "Album X", where=self.inbox / "Sub")
        self.assertEqual(watcher.Watcher([prof], once=True).tick(), {})
        self.assertTrue(deep.exists())
        top = self.drop("Band Y", "Album Y")                    # (an album folder directly in it: filed)
        prof = self.profile(sources=[{"path": str(self.inbox), "watch": True, "recursive": False}])
        res = watcher.Watcher([prof], once=True).tick()
        self.assertEqual(list(res.values()), ["done"], res)
        self.assertFalse(top.exists())
        self.assertTrue(deep.exists())
        prof = self.profile()                                   # (subfolders on again)
        res = watcher.Watcher([prof], once=True).tick()
        self.assertEqual(list(res.values()), ["done"], res)
        self.assertFalse(deep.exists())


if __name__ == "__main__":
    unittest.main()
