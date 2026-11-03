# Twenty-third independent review (A): the app's new "Also save it as artist.jpg in the artist
# folder" box. A profile whose artwork.artist.file names another picture - "folder.jpg" (what
# Jellyfin and Kodi read for an artist), "artist.jpeg" - which the profile check accepts, is
# rewritten to "artist.jpg" whenever the profile is saved in the app, even when nothing about
# artwork was touched (here: only the tag level changed). Before this change the app kept it.
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _review23a_common  # noqa: E402,F401
from _review23a_common import pf  # noqa: E402

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
try:
    import test_gui  # noqa: E402
except Exception:                                             # (PySide6 missing)
    test_gui = None

if test_gui is not None and test_gui.QApplication is not None:
    for _n in dir(test_gui.Gui):
        if _n.startswith("test_"):
            setattr(test_gui.Gui, _n, None)

    class GuiArtistFile(test_gui.Gui):
        def test_saving_keeps_the_profiles_artist_file_name(self):
            for name in ("folder.jpg", "artist.jpeg"):
                prof = "Jf" + name.split(".")[0]
                self.assertEqual(pf.validate({"name": prof, "artwork": {"artist": {"file": name}}}), [])
                pf.save(pf.from_dict({"name": prof, "artwork": {"artist": {"enabled": True, "file": name,
                                                                           "embed": True}}}))
                pr = self.win.pages["Profiles"]
                pr.reload()
                self.app.processEvents()
                pr.list.setCurrentRow([pr.list.item(i).text() for i in range(pr.list.count())].index(prof))
                self.app.processEvents()
                if hasattr(pr, "art_artist_file"):                # (the box is new in this version)
                    self.assertTrue(pr.art_artist_file.isChecked())
                pr.level.setCurrentText("audiophile")            # (a change that has nothing to do with it)
                pr.save()
                self.assertEqual(pf.load(prof)["artwork"]["artist"]["file"], name)


if __name__ == "__main__":
    unittest.main()
