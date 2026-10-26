# Fifteenth independent review (B): this test failed before its fix.
"""gui.py choose_release keeps the chosen releases in self.forced keyed by the album's FOLDER
only, {folder: {"ref", "files"}}: the files were added so that one album of a folder holding
several can have its own release - but there is room for just one choice per folder. Choosing
the release of the folder's second album silently throws away the choice made for the first,
which is searched for again on the next preview."""
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _review15b_common  # noqa: E402,F401

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
try:
    from PySide6.QtWidgets import QInputDialog
except ImportError:                                            # (the GUI is optional)
    QInputDialog = None
from _review14b_common import make  # noqa: E402  (the repo's own test helpers)
from test_engine import Base, FakeHttp, mb_release  # noqa: E402

ALPHA = "0a8e97fd-4f6a-4bd7-8e4b-6d3a0b2ad2f1"
BETA = "1b9f08ae-5a7b-4ce8-9f5c-7e4b1c3be3a2"


class _Page:
    """The Overview page's state choose_release() uses."""
    def __init__(self, a):
        self.a, self.forced = a, {}

    def selected(self):
        return self.a

    def preview(self):
        pass


@unittest.skipIf(QInputDialog is None, "PySide6 not installed")
class TwoChoicesOneFolder(Base):
    def test_both_albums_keep_their_chosen_release(self):
        import gui
        d = self.lib / "Band" / "Mixed"
        for i in range(3):
            make(d / f"a{i + 1:02d}.flac", 300 + 50 * i, 10 * (i + 1),
                 {"title": f"T{i}", "album": "Alpha", "albumartist": "Band", "track": str(i + 1)})
            make(d / f"b{i + 1:02d}.flac", 700 + 50 * i, 10 * (i + 1),
                 {"title": f"T{i}", "album": "Beta", "albumartist": "Band", "track": str(i + 1)})
        p = self.engine(FakeHttp()).plan(d)
        by = {Path(a["files"][0]["src"]).name[0]: a for a in p["albums"]}
        self.assertEqual(sorted(by), ["a", "b"])
        page = _Page(by["a"])
        for row, mbid in (("a", ALPHA), ("b", BETA)):
            page.a = by[row]
            with mock.patch.object(QInputDialog, "getText", return_value=(mbid, True)):
                gui.Overview.choose_release(page)
        # the next preview (gui.py preview(): e.force = dict(self.forced)); neither title is one a
        # search finds
        e = self.engine(FakeHttp(mb=[mb_release(ALPHA, "Band", "Zilch"), mb_release(BETA, "Band", "Nada")]))
        e.force = dict(page.forced)
        got = {Path(a["files"][0]["src"]).name[0]: a["identification"].get("release_id") for a in e.plan(d)["albums"]}
        self.assertEqual(got, {"a": ALPHA, "b": BETA}, "the release chosen for the first album was dropped")


if __name__ == "__main__":
    unittest.main()
