# 0.8.12: watched folders are looked in when you say - Check Selected (one folder) or Check All
# Now - or by the timer, on the clock: every 10 minutes is 12:00, 12:10, 12:20 for all of them
# (not 10 minutes after TagMender started); the page says when the next check is.
import sys
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_watch_folders import DropBase, settle as settle_folder  # noqa: E402
import watcher  # noqa: E402


class OnTheClock(unittest.TestCase):
    def at(self, h, m, s=0):
        return time.mktime((2026, 11, 7, h, m, s, 0, 0, -1))

    def test_every_ten_minutes_is_on_the_tens(self):
        self.assertEqual(watcher.next_on_clock(600, self.at(12, 3, 17)), self.at(12, 10))
        self.assertEqual(watcher.next_on_clock(600, self.at(12, 10)), self.at(12, 20))   # (just ran: the next)
        self.assertEqual(watcher.next_on_clock(300, self.at(12, 1)), self.at(12, 5))
        self.assertEqual(watcher.next_on_clock(3600, self.at(23, 30)), self.at(23, 30) + 1800)   # (midnight)

    def test_a_day_starts_again_at_midnight(self):
        # every 9 hours: 00:00, 09:00, 18:00, then midnight - not 03:00 the next day
        self.assertEqual(watcher.next_on_clock(9 * 3600, self.at(19, 0)),
                         time.mktime((2026, 11, 8, 0, 0, 0, 0, 0, -1)))
        self.assertEqual(watcher.next_on_clock(9 * 3600, self.at(8, 59)), self.at(9, 0))

    def test_on_the_wall_clock_on_a_summer_time_day(self):
        import os
        old = os.environ.get("TZ")
        os.environ["TZ"] = "America/New_York"
        time.tzset()
        try:
            def at(d, h, m=0):
                return time.mktime((2026, 3, d, h, m, 0, 0, 0, -1))
            # 8 March 2026 the clocks go from 02:00 to 03:00: every 2 hours stays 12:00, 14:00
            got = time.localtime(watcher.next_on_clock(7200, at(8, 11, 30)))
            self.assertEqual((got.tm_hour, got.tm_min), (12, 0))
            # 02:30 does not exist that day: the next check is still ahead, never in the past
            self.assertGreater(watcher.next_on_clock(1800, at(8, 1, 59)), at(8, 1, 59))
            # and on 1 November (03:00 back to 02:00) as well
            got = time.localtime(watcher.next_on_clock(7200, time.mktime((2026, 11, 1, 11, 30, 0, 0, 0, -1))))
            self.assertEqual((got.tm_hour, got.tm_min), (12, 0))
        finally:
            if old is None:
                os.environ.pop("TZ", None)
            else:
                os.environ["TZ"] = old
            time.tzset()


class OnlySome(DropBase):
    def test_check_selected_looks_in_that_folder_only(self):
        other = self.tmp / "other"
        other.mkdir()
        dropped = self.drop("Band X", "Album X")
        later = self.drop("Band Y", "Album Y", where=other)
        settle_folder(other)
        prof = self.profile(sources=[{"path": str(self.inbox), "watch": True, "recursive": True},
                                     {"path": str(other), "watch": True, "recursive": True}])
        res = watcher.Watcher([prof], once=True, only=[str(other)]).tick()
        self.assertEqual(list(res), [str(later)])
        self.assertTrue(self.audio_in(dropped))                      # (the other folder: not looked in)


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
class Page(DropBase):
    def test_the_page_checks_the_selected_folder_and_says_when_the_timer_looks(self):
        app = QApplication.instance() or QApplication([])
        import gui
        self.profile()
        win = gui.Main()
        win.show()
        try:
            w = win.pages["Watched folders"]
            w.reload()
            win.go("Watched folders")
            w.table.selectRow(0)
            settle(app)
            with mock.patch.object(type(w), "check_now") as now:
                w.check_one_btn.click()
            now.assert_called_once_with(only=[str(self.inbox)])
            with mock.patch.object(w, "check_now"), mock.patch.object(w, "ask_live", return_value=True):
                w.live.setChecked(True)
                settle(app)
            self.assertTrue(w.timer.isActive() and w.timer.isSingleShot())
            self.assertRegex(w.next_label.text(), r"^Next check \d\d:\d\d$")
            due = w._next_at
            self.assertEqual(due, watcher.next_on_clock(w.interval_ms() / 1000))
            w.live.setChecked(False)
            settle(app)
            self.assertFalse(w.timer.isActive())
            self.assertEqual(w.next_label.text(), "")
        finally:
            win.close()
            win.deleteLater()
            settle(app)


if __name__ == "__main__":
    unittest.main()
