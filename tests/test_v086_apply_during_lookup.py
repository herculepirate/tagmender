# 0.8.6: Apply asked while albums are looked up: the look-up stops after the album it is on, the
# ready albums are applied (asking first, as always), then the albums not reached are looked up.
# APL-1 (0.8.30): the ready albums are applied beside the look-up instead, on a worker of their
# own; the look-up stops first only when an album to apply shares a folder with one looked up.
import os
import sys
import threading
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_gui as tg  # noqa: E402
from test_gui import wait  # noqa: E402
from test_v084_overview import V084Overview  # noqa: E402


@unittest.skipIf(tg.QApplication is None, "PySide6 not installed")
class V086ApplyDuringLookUp(V084Overview):
    def order(self):
        """The three albums in the order a look-up of all of them takes them."""
        ov = self.ov
        return sorted(ov.model.albums, key=lambda a: ov.model.albums.index(a))

    def start(self):
        ov = self.ov
        self.listed()
        first, second, third = (Path(a["source_dir"]).name.split(" - ")[-1] for a in self.order())
        g2, g3 = self.gate(second), self.gate(third)
        ov.look_up("profile", list(ov.plan["albums"]))
        self.assertTrue(wait(lambda: self.row(first)["status"] != "listed", self.app, timeout=30), ov.summary.text())
        self.assertTrue(ov.running)
        return first, second, third, g2, g3

    def test_APL1_apply_runs_beside_the_look_up(self):
        ov = self.ov
        first, second, third, g2, g3 = self.start()
        if not ov.actionable():
            self.skipTest("the first album has nothing to apply")
        self.assertTrue(self.win.commands["apply"].isEnabled())
        with mock.patch.object(self.gui.sheets, "confirm", return_value=True) as ask, \
                mock.patch.object(self.win, "show_run_report"):
            ov.apply()
            ask.assert_called_once()                                     # (asked at once: nothing stops)
            self.assertNotIn("Stopping the look-up", ov.summary.text())
            self.assertFalse(self.win.commands["apply"].isEnabled())     # (one Apply at a time)
            # the first album is filed while the second's look-up still waits at its gate
            self.assertTrue(wait(lambda: self.row(first)["status"] == "done", self.app, timeout=30),
                            ov.summary.text())
            self.assertTrue(ov.running)
            self.assertEqual(self.row(third)["status"], "listed")
            g2.set()
            g3.set()
            self.assertTrue(wait(lambda: not ov.running and self.row(third)["status"] != "listed", self.app,
                                 timeout=60), ov.summary.text())
        self.assertEqual(self.row(first)["status"], "done")
        self.assertIsNone(ov.apply_worker)
        self.assertEqual(ov.queue, {})
        self.assertIsNotNone(ov.plan)

    def test_APL1_the_work_ends_when_both_have(self):
        """The look-up ends first: the page stays busy until the Apply beside it ends too."""
        import executor
        ov = self.ov
        first, second, third, g2, g3 = self.start()
        if not ov.actionable():
            self.skipTest("the first album has nothing to apply")
        hold, step = threading.Event(), executor.Executor.file_step

        def slow(e, i, j, fe, a, done):
            hold.wait(30)
            return step(e, i, j, fe, a, done)
        with mock.patch.object(executor.Executor, "file_step", slow), \
                mock.patch.object(self.gui.sheets, "confirm", return_value=True), \
                mock.patch.object(self.win, "show_run_report"):
            ov.apply()
            g2.set()
            g3.set()
            self.assertTrue(wait(lambda: self.row(third)["status"] != "listed" and not ov.looking, self.app,
                                 timeout=60), ov.summary.text())
            self.app.processEvents()
            self.assertTrue(ov.running, "the Apply beside it still runs")
            self.assertEqual(self.status(first), "Applying…")
            hold.set()
            self.assertTrue(wait(lambda: not ov.running, self.app, timeout=60), ov.summary.text())
        self.assertEqual(self.row(first)["status"], "done")
        self.assertEqual(ov.states, {})

    def test_APL1_an_album_sharing_a_folder_with_one_looked_up_stops_the_look_up_first(self):
        ov = self.ov
        first, second, third, g2, g3 = self.start()
        if not ov.actionable():
            self.skipTest("the first album has nothing to apply")
        mine = os.path.realpath(self.row(first)["source_dir"])
        with mock.patch.object(ov, "looked_up_dirs", lambda: [os.path.dirname(mine)]), \
                mock.patch.object(self.gui.sheets, "confirm", return_value=True) as ask, \
                mock.patch.object(self.win, "show_run_report"):
            ov.apply()
            self.assertIn("Stopping the look-up", ov.summary.text())
            ask.assert_not_called()                                      # (asked when the look-up stopped)
            g2.set()
            self.assertTrue(wait(lambda: ask.called, self.app, timeout=30), ov.summary.text())
            g3.set()
            self.assertTrue(wait(lambda: not ov.running and self.row(third)["status"] != "listed", self.app,
                                 timeout=60), ov.summary.text())
        self.assertEqual(self.row(first)["status"], "done")
        self.assertEqual(ov.queue, {})

    def test_declining_apply_goes_on_with_the_look_up(self):
        ov = self.ov
        first, second, third, g2, g3 = self.start()
        if not ov.actionable():
            self.skipTest("the first album has nothing to apply")
        with mock.patch.object(self.gui.sheets, "confirm", return_value=False) as ask:
            ov.apply()
            g2.set()
            self.assertTrue(wait(lambda: ask.called, self.app, timeout=30), ov.summary.text())
            g3.set()
            self.assertTrue(wait(lambda: not ov.running and self.row(third)["status"] != "listed", self.app,
                                 timeout=60), ov.summary.text())
        self.assertNotEqual(self.row(first)["status"], "done")

    def test_EDT1_an_album_the_look_up_is_done_with_can_be_edited(self):
        """A cover or tag of an album the look-up has finished is taken while it looks up the
        others; one still to be looked up waits (and you are told)."""
        ov = self.ov
        first, second, third, g2, g3 = self.start()
        insp = self.win.inspector
        try:
            self.assertFalse(ov.album_busy(self.row(first)))
            self.assertTrue(ov.album_busy(self.row(third)))
            if self.row(first)["status"] in ("ready", "unchanged", "unresolved"):
                self.assertTrue(insp.can_edit(self.row(first)))
            self.assertFalse(insp.can_edit(self.row(third)))
            with mock.patch.object(self.win.statusBar(), "showMessage") as said:
                insp.set_cover({"image": "/nonexistent.jpg"}, self.row(third))
            self.assertIn("being looked up or applied", said.call_args[0][0])
        finally:
            g2.set()
            g3.set()
            self.assertTrue(wait(lambda: not ov.running, self.app, timeout=60), ov.summary.text())

    def test_CLR1_clear_done_works_while_the_look_up_goes_on(self):
        ov = self.ov
        first, second, third, g2, g3 = self.start()
        if not ov.actionable():
            self.skipTest("the first album has nothing to apply")
        with mock.patch.object(self.gui.sheets, "confirm", return_value=True), \
                mock.patch.object(self.win, "show_run_report"):
            ov.apply()
            self.assertTrue(wait(lambda: self.row(first)["status"] == "done" and ov.apply_worker is None,
                                 self.app, timeout=30), ov.summary.text())
            self.assertTrue(ov.running)
            self.assertTrue(ov.clear_done_btn.isEnabled())
            ov.clear_done()
            self.assertFalse(any(first in a["source_dir"] for a in ov.model.albums))
            g2.set()
            g3.set()
            self.assertTrue(wait(lambda: not ov.running, self.app, timeout=60), ov.summary.text())
        self.assertNotEqual(self.row(third)["status"], "listed")             # (the look-up went on)
        self.assertFalse(any(first in a["source_dir"] for a in ov.model.albums))

    def test_cancel_after_apply_stops_it_all(self):
        ov = self.ov
        first, second, third, g2, g3 = self.start()
        with mock.patch.object(self.gui.sheets, "confirm", return_value=True), \
                mock.patch.object(self.win, "show_run_report"):
            ov.apply()
            ov.cancel()
            if ov.apply_worker is not None:
                self.assertTrue(ov.apply_worker.cancelled)               # (the Apply beside it too)
            g2.set()
            g3.set()
            self.assertTrue(wait(lambda: not ov.running, self.app, timeout=30), ov.summary.text())
        self.assertEqual(self.row(third)["status"], "listed")
        self.assertEqual(ov.queue, {})
        self.assertIsNone(ov.apply_worker)

    def test_a_refresh_interrupted_by_apply_is_still_a_refresh(self):
        ov = self.ov
        self.listed()
        k = ov.album_key(self.row("Album H"))
        ov._apply_next, ov._lookup_again = True, True
        ov.looking, ov._lookup_order, ov.states = {k: ("Auto", "profile")}, [k], {k: "looking"}
        with mock.patch.object(ov, "apply"), mock.patch.object(ov, "run_queue") as rq:
            ov.look_up_ended()
        self.assertEqual(ov.queue, {k: ("profile", True)})
        rq.assert_called_once()

    def test_a_failed_apply_still_looks_up_the_albums_waiting(self):
        ov = self.ov
        ov.queue = {"k": ("profile", False)}
        with mock.patch.object(ov, "oops"), mock.patch.object(ov, "run_queue") as rq:
            ov.apply_failed("disk full")
        rq.assert_called_once()


    def test_apply_selected_applies_only_the_album_chosen(self):
        ov = self.ov
        first, second, third, g2, g3 = self.start()
        if not ov.actionable():
            self.skipTest("the first album has nothing to apply")
        ov.table.clearSelection()
        ov.table.selectRow(ov.proxy.mapFromSource(ov.model.index(ov.model.albums.index(self.row(first)), 0)).row())
        offered = []
        with mock.patch.object(self.gui.sheets, "confirm",
                               side_effect=lambda *a, **k: offered.append(list(ov.to_apply())) or False) as ask:
            ov.apply_selected()
            g2.set()
            self.assertTrue(wait(lambda: ask.called, self.app, timeout=30), ov.summary.text())
            g3.set()
            self.assertTrue(wait(lambda: not ov.running, self.app, timeout=60), ov.summary.text())
        self.assertEqual([a["source_dir"] for a in offered[0]], [self.row(first)["source_dir"]])


del V084Overview

if __name__ == "__main__":
    unittest.main()
