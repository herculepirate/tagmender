# QA: Apply through the window on real files - a file changed after the preview is not applied, and
# an Apply cancelled before its first album says nothing was changed (it said ". Undo it from
# Jobs." with no job and no counts: merge_results counted nothing and reported the run as done).
import os
import sys
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import plan as pl  # noqa: E402
from test_executor import Base  # noqa: E402

try:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
except ImportError:                          # pragma: no cover
    QApplication = None


def settle(app, s=0.05):
    end = time.time() + s
    while time.time() < end:
        app.processEvents()
        time.sleep(0.01)


class MergeResults(unittest.TestCase):
    def test_cancelled_before_any_plan_counts_its_albums_cancelled(self):
        from gui.overview import merge_results
        plans = [{"albums": [{"source_dir": "/a"}, {"source_dir": "/b"}]}, {"albums": [{"source_dir": "/c"}]}]
        res = merge_results(plans, lambda p: self.fail("nothing runs once cancelled"), lambda: True)
        self.assertEqual(res["counts"], {"cancelled": 3})
        self.assertEqual(res["status"], "partial")
        self.assertEqual(res["job"], "")

    def test_cancelled_between_plans_counts_the_plans_not_started(self):
        from gui.overview import merge_results
        plans = [{"albums": [{"source_dir": "/a"}]}, {"albums": [{"source_dir": "/b"}, {"source_dir": "/c"}]}]
        stop = []

        def run(p):
            stop.append(1)
            return {"job": "j1", "status": "done", "counts": {"done": 1},
                    "albums": [{"index": 0, "album": "/a", "status": "done"}]}
        res = merge_results(plans, run, lambda: bool(stop))
        self.assertEqual(res["counts"], {"done": 1, "cancelled": 2})
        self.assertEqual(res["status"], "partial")


@unittest.skipIf(QApplication is None, "PySide6 not installed")
class ApplyInTheWindow(Base):
    def run_apply(self, mutate=None, cancel=False):
        import gui
        app = QApplication.instance() or QApplication([])
        p = pl.build([self.proposal()], self.profile(operation="move", destination=str(self.dest)))
        win = gui.Main()
        win.show()
        ov = win.pages["Overview"]
        ov.path = str(self.lib)
        ov.planned(p)
        settle(app)
        if mutate:
            mutate()
        try:
            with mock.patch("gui.overview.sheets.confirm", return_value=True), \
                    mock.patch("gui.sheets.warning"), mock.patch("gui.sheets.information"):
                ov.apply()
                if cancel:
                    ov.cancel()
                end = time.time() + 60
                while time.time() < end and (ov.running or win.threads()):
                    settle(app)
            settle(app, 0.2)
            return ov.summary.text(), [a.get("status") for a in ov.model.albums]
        finally:
            win.close()
            settle(app, 0.2)

    def test_cancel_before_the_first_album_says_nothing_was_changed(self):
        text, statuses = self.run_apply(cancel=True)
        self.assertFalse(text.startswith("."), text)
        self.assertNotIn("Undo", text)
        self.assertIn("1 Cancelled", text)
        self.assertEqual(statuses, ["ready"])                    # (still there, to apply later)
        self.assertEqual(sorted(p.name for p in self.album.glob("*.flac")), ["01 a.flac", "02 b.flac"])
        self.assertEqual(list(self.dest.rglob("*.flac")), [])

    def test_a_file_changed_after_the_preview_is_not_applied(self):
        from mutagen.flac import FLAC
        raw2 = self.f2.read_bytes()

        def mutate():
            f = FLAC(str(self.f1))
            f["title"] = ["edited by hand"]
            f.save()
        text, statuses = self.run_apply(mutate)
        self.assertEqual(statuses, ["conflict"])
        self.assertIn("Changed since preview", text)
        self.assertNotIn("Undo", text)                            # (the job changed nothing)
        self.assertEqual(FLAC(str(self.f1)).get("title"), ["edited by hand"])
        self.assertEqual(self.f2.read_bytes(), raw2)
        self.assertEqual(list(self.dest.rglob("*.flac")), [])


@unittest.skipIf(QApplication is None, "PySide6 not installed")
class DropWhileApplying(Base):
    """A folder dropped while Apply runs is in the list at once (read on a thread of its own),
    waiting; Apply goes on, and the album is looked up when it ends."""

    def setUp(self):
        super().setUp()
        import gui
        from test_executor import audio
        self.app = QApplication.instance() or QApplication([])
        self.other = self.tmp / "incoming" / "Other Band" / "2003 - Other Album"
        audio(self.other / "01 x.flac", 600, album="Other Album", artist="Other Band", title="x")
        p = pl.build([self.proposal()], self.profile(operation="move", destination=str(self.dest)))
        self.win = gui.Main()
        self.win.show()
        self.ov = self.win.pages["Overview"]
        self.ov.path = str(self.lib)
        self.ov.planned(p)
        settle(self.app)

    def tearDown(self):
        end = time.time() + 30
        while time.time() < end and (self.ov.running or self.win.threads()):
            settle(self.app)
        self.win.close()
        settle(self.app, 0.2)
        super().tearDown()

    def apply_with_a_drop(self, list_first=False):
        import executor
        import threading
        gate = threading.Event()
        real = executor.Executor.run

        def held(ex):
            gate.wait(20)                                       # (Apply runs until the drop is listed)
            return real(ex)
        seen = {}
        with mock.patch("gui.overview.sheets.confirm", return_value=True), mock.patch("gui.sheets.warning"), \
                mock.patch("gui.sheets.information"), mock.patch.object(executor.Executor, "run", held), \
                mock.patch("gui.overview._list_first", return_value=list_first), \
                mock.patch.object(type(self.ov), "look_up") as look_up:
            self.ov.apply()
            settle(self.app)
            self.assertTrue(self.ov._applying)
            self.assertEqual(self.win.dropped([str(self.other.parent.parent)]), [str(self.other.parent.parent)])
            end = time.time() + 20
            while time.time() < end and len(self.ov.model.albums) < 2:
                settle(self.app)
            seen["running"] = self.ov.running                     # (listed while Apply still runs)
            seen["rows"] = [Path(a["source_dir"]).name for a in self.ov.model.albums]
            new = self.ov.model.albums[-1]
            seen["state"] = self.ov.state_of(new)
            seen["message"] = self.win.statusBar().currentMessage()
            gate.set()
            end = time.time() + 30
            while time.time() < end and (self.ov.running or self.win.threads()):
                settle(self.app)
            settle(self.app, 0.2)
        seen["after"] = [(Path(a["source_dir"]).name, a.get("status")) for a in self.ov.model.albums]
        seen["looked_up"] = [[Path(a["source_dir"]).name for a in c.args[1]] for c in look_up.call_args_list]
        return seen

    def test_listed_at_once_and_looked_up_when_apply_ends(self):
        seen = self.apply_with_a_drop()
        self.assertTrue(seen["running"])
        self.assertEqual(seen["rows"], [self.album.name, "2003 - Other Album"])
        self.assertEqual(seen["state"], "Waiting…")
        self.assertIn("looked up when Apply ends", seen["message"])
        self.assertEqual(seen["after"], [(self.album.name, "done"), ("2003 - Other Album", "listed")])
        self.assertEqual(seen["looked_up"], [["2003 - Other Album"]])
        self.assertEqual(self.ov.later, [])

    def test_a_folder_apply_is_working_in_waits_for_it(self):
        # (the folder the album is being moved into, or one holding it: read once Apply is done)
        import executor
        import threading
        gate = threading.Event()
        real = executor.Executor.run

        def held(ex):
            gate.wait(20)
            return real(ex)
        with mock.patch("gui.overview.sheets.confirm", return_value=True), mock.patch("gui.sheets.warning"), \
                mock.patch("gui.sheets.information"), mock.patch.object(executor.Executor, "run", held), \
                mock.patch.object(type(self.ov), "read_now") as read_now:
            self.ov.apply()
            settle(self.app)
            self.assertTrue(self.ov.touches_apply([str(self.lib)]))
            self.assertTrue(self.ov.touches_apply([str(self.album / "sub")]))
            self.assertFalse(self.ov.touches_apply([str(self.other.parent.parent)]))
            self.assertTrue(self.ov.touches_apply([str(self.dest)]))
            self.win.dropped([str(self.dest)])
            read_now.assert_not_called()
            self.assertEqual(self.ov.later, [str(self.dest)])
            self.assertIn("when the current work ends", self.win.statusBar().currentMessage())
            self.ov.later = []
            gate.set()

    def test_your_setting_to_list_first_lists_it_only(self):
        seen = self.apply_with_a_drop(list_first=True)
        self.assertIn("not looked up yet", seen["message"])
        self.assertEqual(seen["state"], "")
        self.assertEqual(seen["after"], [(self.album.name, "done"), ("2003 - Other Album", "listed")])
        self.assertEqual(seen["looked_up"], [])                   # (the list stays: you choose)


if __name__ == "__main__":
    unittest.main()
