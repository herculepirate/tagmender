# 0.8.11: Find Covers… - a picture's right-click menu (and Menu key): yours opens in its app or
# shows its folder; one inside the tracks opens as a copy (never the track itself); one found
# opens in the browser or its link is copied. Nothing is fetched from the network for the menu.
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_engine import Base  # noqa: E402  (isolated settings and state)

try:
    from PySide6.QtWidgets import QApplication
except ImportError:                          # pragma: no cover
    QApplication = None


@unittest.skipIf(QApplication is None, "PySide6 not installed")
class CoverMenu(Base):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def chooser(self):
        from gui import cover_chooser
        with mock.patch.object(cover_chooser.CoverChooser, "load_own"), \
                mock.patch.object(cover_chooser.CoverChooser, "_ai_ready"):
            insp = mock.Mock()
            insp.window.return_value = None
            return cover_chooser.CoverChooser(insp, {"source_dir": "/music/A"})

    def words(self, dlg, c):
        return [w for w, _fn in dlg.actions_for(c)]

    def test_each_kind_of_picture_has_its_own_choices(self):
        from covers_find import Choice
        dlg = self.chooser()
        own = Choice("own", "Artwork/front.jpg", extra={"path": "/music/A/Artwork/front.jpg"})
        inside = Choice("own", "in 01.flac", data=b"\xff\xd8x", extra={"file": "/music/A/01.flac"})
        found = Choice("found", "Cover Art Archive", fetch=lambda: 1 / 0, extra={"url": "https://example.org/c.jpg"})
        self.assertEqual(self.words(dlg, own), ["Open in Default App", "Show in Folder"])
        self.assertEqual(self.words(dlg, inside), ["Open a Copy in Default App", "Show in Folder"])
        self.assertEqual(self.words(dlg, found), ["Open in Browser", "Copy Image Link"])   # (not fetched)
        opened = []
        with mock.patch("gui.cover_chooser.QDesktopServices.openUrl", lambda u: opened.append(u)):
            dict(dlg.actions_for(own))["Show in Folder"]()
            dict(dlg.actions_for(inside))["Open a Copy in Default App"]()
            dict(dlg.actions_for(found))["Open in Browser"]()
        self.assertEqual(opened[0].toLocalFile(), "/music/A/Artwork")
        copy = opened[1].toLocalFile()
        self.assertNotEqual(copy, "/music/A/01.flac")                 # (a copy, never the track)
        self.assertEqual(Path(copy).read_bytes(), b"\xff\xd8x")
        os.remove(copy)
        self.assertEqual(opened[2].toString(), "https://example.org/c.jpg")
        dict(dlg.actions_for(found))["Copy Image Link"]()
        self.assertEqual(QApplication.clipboard().text(), "https://example.org/c.jpg")

    def test_the_menu_opens_on_the_picture_under_the_mouse(self):
        from covers_find import Choice
        dlg = self.chooser()
        dlg.add([Choice("own", "a.jpg", 10, 10, extra={"path": "/music/A/a.jpg"}),
                 Choice("own", "b.jpg", 10, 10, extra={"path": "/music/A/b.jpg"})])
        dlg.show()
        self.app.processEvents()
        it = dlg.grid.item(1)
        with mock.patch("gui.common.popup_menu") as pm:
            dlg.menu(dlg.grid.visualItemRect(it).center())
        self.assertIs(dlg.grid.currentItem(), it)
        self.assertEqual([a.text() for a in pm.call_args.args[3] if a is not None],
                         ["Use This Cover", "Open in Default App", "Show in Folder"])


if __name__ == "__main__":
    unittest.main()
