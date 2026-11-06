# 0.8.6: Apply asked while albums are looked up: the look-up stops after the album it is on, the
# ready albums are applied (asking first, as always), then the albums not reached are looked up.
import sys
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

    def test_apply_stops_the_look_up_applies_then_looks_up_the_rest(self):
        ov = self.ov
        first, second, third, g2, g3 = self.start()
        if not ov.actionable():
            self.skipTest("the first album has nothing to apply")
        self.assertTrue(self.win.commands["apply"].isEnabled())
        with mock.patch.object(self.gui.sheets, "confirm", return_value=True) as ask, \
                mock.patch.object(self.win, "show_run_report"):
            ov.apply()
            self.assertIn("Stopping the look-up", ov.summary.text())
            self.assertFalse(self.win.commands["apply"].isEnabled())     # (asked once)
            ask.assert_not_called()                                      # (asked when the look-up stopped)
            g2.set()
            self.assertTrue(wait(lambda: ask.called, self.app, timeout=30), ov.summary.text())
            g3.set()
            self.assertTrue(wait(lambda: not ov.running and self.row(third)["status"] != "listed", self.app,
                                 timeout=60), ov.summary.text())
        self.assertEqual(self.row(first)["status"], "done")
        self.assertNotEqual(self.row(third)["status"], "listed")           # (looked up after the Apply)
        self.assertEqual(ov.queue, {})
        self.assertIsNotNone(ov.plan)

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

    def test_cancel_after_apply_stops_it_all(self):
        ov = self.ov
        first, second, third, g2, g3 = self.start()
        with mock.patch.object(self.gui.sheets, "confirm", return_value=True) as ask:
            ov.apply()
            ov.cancel()
            g2.set()
            g3.set()
            self.assertTrue(wait(lambda: not ov.running, self.app, timeout=30), ov.summary.text())
        ask.assert_not_called()
        self.assertEqual(self.row(third)["status"], "listed")
        self.assertEqual(ov.queue, {})

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


del V084Overview

if __name__ == "__main__":
    unittest.main()
