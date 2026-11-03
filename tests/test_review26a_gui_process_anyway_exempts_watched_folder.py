# Twenty-sixth independent review (A): the app's "Process anyway" (gui.Overview.process_anyway,
# gui.py ~624-643) on an excluded album of LOOSE TRACKS - tracks dropped straight into a watched
# folder, which the engine groups as an album whose folder is the watched folder itself. The
# comment says "a loose track, or one album of a mixed folder: only its own files, never the whole
# folder", but the test for that is "is there other music in the folder NOW": when the loose
# tracks are the only music in the inbox at that moment, ex.State().allow() is called on the
# watched folder itself. Overrides are permanent and cover everything below them
# (exclusions._overridden), so every later drop into the watched folder is exempt from every
# Ignore/Skip rule: an album the rules say to leave alone ("Karaoke" editions here) is
# identified, retagged and moved into the library by the watcher (unattended profile), unasked;
# the same happens for every later preview or `tagmender run` of the folder.
# Not a regression (gui.py is the same in the previous version).
import os as _os
import shutil
import sys
import tempfile as _tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

_os.environ["XDG_CONFIG_HOME"] = _tempfile.mkdtemp(prefix="tm26A-config-")
_os.environ["XDG_STATE_HOME"] = _tempfile.mkdtemp(prefix="tm26A-state-")
_os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
_HERE = Path(__file__).resolve().parent
REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "tests"))
try:
    from PySide6.QtWidgets import QApplication
except ImportError:                                           # GUI is optional
    QApplication = None
import test_engine as te  # noqa: E402
import exclusions as ex  # noqa: E402
import match_albums as ma  # noqa: E402
import profiles as pf  # noqa: E402
import watcher  # noqa: E402


def wait(cond, app, timeout=60):
    end = time.time() + timeout
    while time.time() < end:
        app.processEvents()
        if cond():
            return True
        time.sleep(0.02)
    return False


def settle(path):
    old = time.time() - 3600
    for f in [path, *Path(path).rglob("*")]:
        _os.utime(f, (old, old))


@unittest.skipIf(QApplication is None, "PySide6 not installed")
class ProcessAnywayLooseTracks(te.Base):
    @classmethod
    def setUpClass(cls):
        import gui
        cls.gui = gui
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        super().setUp()
        self.inbox, self.dest = self.tmp / "inbox", self.tmp / "library"
        self.inbox.mkdir()
        self.dest.mkdir()
        self.http = te.FakeHttp(dg=[te.dg_release(920, "Band G", "Album G Karaoke"),
                                    te.dg_release(921, "Band N", "Album N Karaoke")])
        p = mock.patch.object(ma, "Http", lambda db, cfg, token: self.http)
        p.start()
        self.addCleanup(p.stop)
        ex._write(ex.rules_file(), {"rules": [{"id": "no-vox", "name": "karaoke editions", "action": "ignore",
                                               "target": "album", "when": {"all": [
                                                   {"field": "album", "op": "contains", "value": "Karaoke"}]}}]})
        self.prof = pf.from_dict({"name": "Inbox", "operation": "move", "destination": str(self.dest),
                                  "sources": [{"path": str(self.inbox), "watch": True, "recursive": True}],
                                  "unattended": True, "providers": {"web": {"enabled": False}},
                                  "artwork": {"enabled": False}, "watch": {"settle_minutes": 1}})
        pf.save(self.prof)
        self.win = self.gui.Main()

    def tearDown(self):
        end = time.time() + 30
        while time.time() < end and any(self.gui._alive(th) for pg in self.win.pages.values()
                                        for th, _w in getattr(pg, "_threads", [])):
            self.app.processEvents()
            time.sleep(0.02)
        self.win.pages["Watched folders"].finish()
        self.win.close()
        super().tearDown()

    def test_process_anyway_on_loose_tracks_keeps_the_rules_for_later_drops(self):
        # loose tracks of an ignored edition, dropped straight into the watched folder
        d = self.album("Band G", "Album G Karaoke")
        loose = []
        for f in sorted(d.iterdir()):
            loose.append(self.inbox / f"G {f.name}")
            shutil.move(str(f), str(loose[-1]))
        ov = self.win.pages["Overview"]
        ov.refresh_profiles()
        ov.profile.setCurrentText("Inbox")
        ov.set_path(str(self.inbox))
        self.assertTrue(wait(lambda: ov.plan is not None, self.app), ov.summary.text())
        self.assertEqual([a["status"] for a in ov.plan["albums"]], ["excluded"])
        ov.table.selectRow(0)
        self.app.processEvents()
        ov.process_anyway()                  # the user wants THESE tracks done, once
        self.assertEqual(ex.State().overrides() & {str(self.inbox.resolve())}, set(),
                         "Process anyway on loose tracks exempted the whole watched folder from the rules: "
                         f"overrides = {sorted(ex.State().overrides())}")

    def later_drop(self, process_anyway):
        d = self.album("Band G", "Album G Karaoke")
        for f in sorted(d.iterdir()):
            shutil.move(str(f), str(self.inbox / f"G {f.name}"))
        if process_anyway:
            ov = self.win.pages["Overview"]
            ov.refresh_profiles()
            ov.profile.setCurrentText("Inbox")
            ov.set_path(str(self.inbox))
            self.assertTrue(wait(lambda: ov.plan is not None, self.app), ov.summary.text())
            ov.table.selectRow(0)
            self.app.processEvents()
            ov.process_anyway()
        # later: another "Karaoke" edition dropped into the watched folder, as an album folder
        later = self.album("Band N", "Album N Karaoke")
        drop = self.inbox / "Band N - Album N Karaoke"
        shutil.move(str(later), str(drop))
        before = {p.name: p.read_bytes() for p in drop.iterdir()}
        settle(self.inbox)
        watcher.Watcher([self.prof], once=True).tick()
        filed = sorted(str(p.relative_to(self.dest)) for p in self.dest.rglob("*.flac") if "Band N" in str(p))
        self.assertEqual(filed, [], "the watcher filed an album its Ignore rule excludes")
        self.assertTrue(drop.is_dir())
        self.assertEqual({p.name: p.read_bytes() for p in drop.iterdir()}, before,
                         "the ignored album's files were changed")

    def test_control_no_process_anyway(self):              # (passes: the rule keeps the drop as it is)
        self.later_drop(process_anyway=False)

    def test_later_drop_of_an_ignored_edition_is_left_alone_by_the_watcher(self):
        self.later_drop(process_anyway=True)


if __name__ == "__main__":
    unittest.main()
