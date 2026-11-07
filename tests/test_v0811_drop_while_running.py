# 0.8.11: a folder dropped (or chosen with Add Folder) while a look-up or Apply runs is held, and
# when the run ends its albums join the list and are looked up; the albums already listed keep
# what was found for them. Cancel drops what waited; after a failed look-up they are listed only.
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_engine import Base  # noqa: E402  (isolated settings and state)

try:
    from PySide6.QtWidgets import QApplication
except ImportError:                          # pragma: no cover
    QApplication = None


def settle(app, ms=50):
    end = time.time() + ms / 1000
    while time.time() < end:
        app.processEvents()
        time.sleep(0.01)


@unittest.skipIf(QApplication is None, "PySide6 not installed")
class DropWhileRunning(Base):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        super().setUp()
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
        import gui
        import gui_screens
        __import__("app_settings").set_value("add_folder", "look_up")
        self.top = Path(tempfile.mkdtemp(prefix="tm-drop-"))
        self.first, self.other = self.top / "first", self.top / "other"
        self.first.mkdir()
        (self.other / "Album B").mkdir(parents=True)
        self.win = gui.Main()
        self.win.show()
        self.ov = self.win.pages["Overview"]
        self.ov.path = str(self.first)
        self.ov.planned(gui_screens.sample_plan(self.first))
        settle(self.app)

    def tearDown(self):
        end = time.time() + 20
        while time.time() < end and self.win.threads():
            settle(self.app)
        self.win.close()
        self.win.deleteLater()
        settle(self.app)
        shutil.rmtree(self.top, ignore_errors=True)
        super().tearDown()

    def listing(self, *_a, **_k):
        d = str(self.other / "Album B")
        return {"albums": [{"source_dir": d, "status": "listed", "label": "", "rule": None,
                            "identification": {"status": "listed"}, "release": {}, "files": [],
                            "companions": [], "artwork": None, "conflicts": [], "skipped": [], "notes": [],
                            "album_files": [d + "/01.flac"], "key": "album-b"}], "listed": True}

    def wait(self):
        end = time.time() + 10
        while time.time() < end and (self.ov.running or self.win.threads()):
            settle(self.app)
        settle(self.app)

    def test_dropped_during_a_run_joins_the_list_and_is_looked_up_after(self):
        import engine
        had = list(self.ov.plan["albums"])
        self.ov.busy(True)
        self.assertEqual(self.win.dropped([str(self.other)]), [str(self.other)])
        self.assertEqual(self.ov.path, str(self.first))                 # (nothing replaced meanwhile)
        with mock.patch.object(engine.Engine, "listing", self.listing), \
                mock.patch.object(type(self.ov), "look_up") as look_up:
            self.ov.busy(False)
            self.wait()
        self.assertEqual(self.ov.later, [])
        keys = [self.ov.album_key(a) for a in self.ov.plan["albums"]]
        self.assertEqual(keys[:len(had)], [self.ov.album_key(a) for a in had])   # (kept, in their places)
        self.assertEqual(keys[-1], "album-b")
        self.assertIs(self.ov.model.albums, self.ov.plan["albums"])
        self.assertEqual(self.ov.path, str(self.first))                  # (no wider folder read)
        self.assertEqual(self.ov.roots, {"album-b": str(self.other)})    # (it is read from its own)
        look_up.assert_called_once()
        self.assertEqual([self.ov.album_key(a) for a in look_up.call_args.args[1]], ["album-b"])

    def test_the_same_folder_twice_or_one_already_listed_is_not_held(self):
        self.ov.busy(True)
        try:
            self.assertEqual(self.ov.add_later([str(self.other)]), [str(self.other)])
            self.assertEqual(self.ov.add_later([str(self.other)]), [])
            self.assertEqual(self.ov.add_later([str(self.first)]), [])   # (the folder previewed)
            self.assertEqual(self.ov.later, [str(self.other)])
        finally:
            self.ov.later = []
            self.ov.busy(False)

    def test_cancel_drops_what_waited(self):
        self.ov.busy(True)
        self.ov.add_later([str(self.other)])
        self.ov.worker = mock.Mock(cancelled=False)
        self.ov.cancel()
        self.assertEqual(self.ov.later, [])
        self.assertIn("Not added", self.win.statusBar().currentMessage())
        self.assertEqual(self.ov.add_later([str(self.other)]), [])        # (still cancelling: not taken)
        self.ov.worker = None
        self.ov.busy(False)

    def test_after_a_failed_look_up_they_are_listed_not_looked_up(self):
        import engine
        self.ov.busy(True)
        self.ov.add_later([str(self.other)])
        with mock.patch.object(engine.Engine, "listing", self.listing), \
                mock.patch.object(type(self.ov), "look_up") as look_up, \
                mock.patch("gui.sheets.warning"):
            self.ov.lookup_failed("MusicBrainz could not be reached.")
            self.wait()
        look_up.assert_not_called()
        self.assertEqual(self.ov.album_key(self.ov.plan["albums"][-1]), "album-b")
        self.assertIn("not looked up yet", self.ov.summary.text())


    def test_an_applied_album_given_another_profile_is_filed_again_from_where_it_is(self):
        import engine
        lib = self.top / "library" / "Album B"
        lib.mkdir(parents=True)
        (lib / "01.flac").write_bytes(b"x")
        a = self.ov.plan["albums"][0]
        a["status"] = "done"
        a["files"] = [{"src": "/gone/01.flac", "dst": str(lib / "01.flac")}]
        self.ov.model.set_plan(self.ov.plan)

        def listing(_e, folder, **_k):
            self.assertEqual(folder, str(lib))                           # (read where Apply put it)
            return {"albums": [dict(self.listing()["albums"][0], source_dir=str(lib),
                                    album_files=[str(lib / "01.flac")], key="moved")]}
        with mock.patch.object(engine.Engine, "listing", listing), \
                mock.patch.object(type(self.ov), "look_up") as look_up:
            self.ov.set_album_profile([a], "")
            self.wait()
        self.assertEqual(self.ov.album_key(self.ov.model.albums[0]), "moved")   # (in its row's place)
        self.assertEqual(self.ov.roots["moved"], str(lib))
        look_up.assert_called_once()
        self.assertTrue(look_up.call_args.kwargs.get("again"))

    def test_applied_files_gone_says_so(self):
        a = self.ov.plan["albums"][0]
        a["status"] = "done"
        a["files"] = [{"src": "/gone/01.flac", "dst": "/gone/too/01.flac"}]
        self.ov.set_album_profile([a], "")
        self.assertIn("no longer where Apply put them", self.win.statusBar().currentMessage())


if __name__ == "__main__":
    unittest.main()
