# Thirteenth independent review (Picard code): this test failed before its fix.
"""gui.start(): the _Relay objects it makes are parented to the owner widget and never deleted.

The watched-folders page calls start() on every timer tick (check_now, every 5 s to 5 min),
so a TagMender window left open grows by two QObjects per tick for as long as it runs."""
import os
import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _review13b_common as _common  # noqa: E402,F401
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6.QtCore import QObject
    from PySide6.QtWidgets import QApplication, QWidget
except ImportError:
    QApplication = None


@unittest.skipIf(QApplication is None, "PySide6 not installed")
class RelaysGo(unittest.TestCase):
    def test_finished_jobs_leave_no_relays_behind(self):
        import gui
        app = QApplication.instance() or QApplication([])
        owner = QWidget()
        done = []
        for _ in range(20):
            gui.start(owner, lambda w: 1, done.append)
        end = time.time() + 30
        while time.time() < end and (len(done) < 20 or any(gui._alive(th) for th, _w in owner._threads)):
            app.processEvents()
            time.sleep(0.01)
        for _ in range(5):
            app.processEvents()
        self.assertEqual(len(done), 20)
        relays = [c for c in owner.findChildren(QObject) if type(c).__name__ == "_Relay"]
        self.assertLessEqual(len(relays), 2, f"{len(relays)} relays left on the owner after 20 finished jobs")


if __name__ == "__main__":
    unittest.main()
