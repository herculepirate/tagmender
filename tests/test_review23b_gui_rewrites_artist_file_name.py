# Twenty-third independent review (B). A REGRESSION: passes with TM_REPO=prev.
"""The new "Also save it as artist.jpg in the artist folder" box (gui.py, Profiles.save:
file="artist.jpg" if self.art_artist_file.isChecked() else "") rewrites a profile's own
artwork.artist.file whenever the profile is saved in the app - even when nothing about the
artist photo was touched. profiles.py accepts any .jpg/.jpeg name ("artist.jpeg", "photo.jpg",
"folder.jpg" for Kodi ...), and the box is shown checked for it; saving turns it into
"artist.jpg". From then on the artist folder gets a second file ("artist.jpg" beside the
user's chosen name). The previous version kept the profile's file name (it only set
"enabled")."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _review23b_common as c  # noqa: E402

import os  # noqa: E402
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
try:
    from PySide6.QtWidgets import QApplication
except ImportError:                                     # GUI is optional
    QApplication = None


@unittest.skipIf(QApplication is None, "PySide6 not installed")
class GuiArtistFile(unittest.TestCase):
    def test_saving_keeps_the_profiles_artist_file_name(self):
        import gui
        app = QApplication.instance() or QApplication([])
        c.fresh_state()
        c.pf.save(c.pf.from_dict({"name": "Art", "artwork": {"artist": {"enabled": True, "file": "artist.jpeg",
                                                                         "embed": True}}}))
        win = gui.Main()
        try:
            pr = win.pages["Profiles"]
            pr.reload()
            app.processEvents()
            pr.list.setCurrentRow([pr.list.item(i).text() for i in range(pr.list.count())].index("Art"))
            app.processEvents()
            if hasattr(pr, "art_artist_file"):          # (the previous version has no such box)
                self.assertTrue(pr.art_artist_file.isChecked())
            pr.save()                                   # (nothing changed)
            self.assertEqual(c.pf.load("Art")["artwork"]["artist"].get("file"), "artist.jpeg")
        finally:
            win.close()


if __name__ == "__main__":
    unittest.main()
