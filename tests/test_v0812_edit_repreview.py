# 0.8.12: an edit of a field the naming reads (a disc number) changes where the files go: the
# album is previewed again with your edits (plan.apply_edits sets entry["renames"]), so Apply
# files it where the edit says - not, as in 0.8.11, with the old destination and new tags.
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
class EditRepreview(Base):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        super().setUp()
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
        import gui
        import gui_screens
        self.win = gui.Main()
        self.win.show()
        self.ov = self.win.pages["Overview"]
        tmp = Path(tempfile.mkdtemp(prefix="tm-edit-"))
        self.ov.path = str(tmp)
        self.ov.planned(gui_screens.sample_plan(tmp))
        settle(self.app)
        self.album = next(a for a in self.ov.plan["albums"] if a.get("status") == "ready")
        self.insp = self.win.inspector
        self.insp.show_album(self.album, self.ov.plan)
        settle(self.app)

    def tearDown(self):
        end = time.time() + 10
        while time.time() < end and self.win.threads():
            settle(self.app)
        self.win.close()
        self.win.deleteLater()
        settle(self.app)
        super().tearDown()

    def reapply(self, renames):
        import plan as planmod

        def apply_edits(snap, *_a, **_k):
            out = dict(snap)
            if renames:
                out["renames"] = "your edit of discnumber changes where the files go"
            return out
        with mock.patch.object(planmod, "apply_edits", apply_edits), \
                mock.patch.object(type(self.ov), "look_up") as look_up:
            self.insp.reapply(self.album)
            end = time.time() + 10
            while time.time() < end and self.insp._wanted:
                settle(self.app)
            settle(self.app)
        return look_up

    def test_a_naming_edit_previews_the_album_again(self):
        look_up = self.reapply(True)
        look_up.assert_called_once()
        self.assertIs(look_up.call_args.args[1][0], self.album)
        self.assertTrue(look_up.call_args.kwargs.get("again"))
        self.assertIn("previewed again", self.insp.tags.note.text())

    def test_another_edit_does_not(self):
        self.reapply(False).assert_not_called()


if __name__ == "__main__":
    unittest.main()
