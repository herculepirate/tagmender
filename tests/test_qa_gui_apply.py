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
        self.assertEqual(FLAC(str(self.f1)).get("title"), ["edited by hand"])
        self.assertEqual(self.f2.read_bytes(), raw2)
        self.assertEqual(list(self.dest.rglob("*.flac")), [])


if __name__ == "__main__":
    unittest.main()
