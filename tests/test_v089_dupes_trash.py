# 0.8.9: duplicate copies can go to the Trash (duplicates.dispose = "trash": freedesktop.org
# Trash, undoable), and the duplicates folder can be given as that folder's path at a library's top.
import csv
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

os.environ["XDG_DATA_HOME"] = tempfile.mkdtemp(prefix="tm-data-")     # (never this computer's Trash)
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_engine import Base  # noqa: E402  (isolated settings and state)
import app_settings  # noqa: E402
import dedupe_twins as dt  # noqa: E402


class Trash(Base):
    def test_a_copy_goes_to_the_trash_and_undo_brings_it_back(self):
        lib = self.tmp / "lib" / "Artist" / "2001 - Album"
        lib.mkdir(parents=True)
        a, b = lib / "01.flac", self.tmp / "lib" / "other" / "01.flac"
        b.parent.mkdir()
        a.write_bytes(b"one")
        b.write_bytes(b"two")
        got_a, got_b = dt.to_trash(a), dt.to_trash(b)
        home = Path(os.environ["XDG_DATA_HOME"]) / "Trash"
        self.assertEqual(got_a, home / "files" / "01.flac")
        self.assertEqual(got_b, home / "files" / "01.2.flac")              # (never over another)
        self.assertFalse(a.exists())
        info = (home / "info" / "01.flac.trashinfo").read_text()
        self.assertIn("[Trash Info]", info)
        self.assertIn("Path=" + str(a).replace(" ", "%20"), info)
        self.assertIn("DeletionDate=", info)
        log = self.tmp / "moves.csv"
        with open(log, "w", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(["original", "moved_to", "kept_twin"])
            w.writerow([str(a), str(got_a), ""])
        dt.undo(log)
        self.assertEqual(a.read_bytes(), b"one")
        self.assertFalse((home / "info" / "01.flac.trashinfo").exists())     # (out of the Trash's list too)
        self.assertTrue((home / "info" / "01.2.flac.trashinfo").exists())

    def test_a_failed_move_leaves_no_trash_entry(self):
        f = self.tmp / "x.flac"
        f.write_bytes(b"x")
        with mock.patch.object(dt.os, "rename", side_effect=OSError("busy")):
            with self.assertRaises(OSError):
                dt.to_trash(f)
        self.assertTrue(f.exists())
        self.assertFalse((Path(os.environ["XDG_DATA_HOME"]) / "Trash" / "info" / "x.flac.trashinfo").exists())

    def test_a_latin1_name_is_kept_as_its_bytes(self):
        raw = os.fsencode(str(self.tmp)) + b"/caf\xe9.flac"
        with open(raw, "wb") as fh:
            fh.write(b"x")
        got = dt.to_trash(os.fsdecode(raw))
        info = (got.parent.parent / "info" / f"{got.name}.trashinfo").read_text()
        self.assertIn("caf%E9.flac", info)

    def test_undo_reads_a_log_with_a_latin1_name(self):
        raw = os.fsencode(str(self.tmp)) + b"/vieux\xe9.flac"
        with open(raw, "wb") as fh:
            fh.write(b"x")
        got = dt.to_trash(os.fsdecode(raw))
        log = self.tmp / "moves.csv"
        with open(log, "w", newline="", encoding="utf-8", errors="surrogateescape") as fh:
            w = csv.writer(fh)
            w.writerow(["original", "moved_to", "kept_twin"])
            w.writerow([os.fsdecode(raw), str(got), ""])
        dt.undo(log)
        self.assertTrue(os.path.exists(raw))

    def test_a_drives_trash_says_the_path_from_the_drive_top(self):
        top = self.tmp / "drive"
        (top / "Artist").mkdir(parents=True)
        f = top / "Artist" / "01 a b.flac"
        f.write_bytes(b"x")
        base = top / f".Trash-{os.getuid()}"
        with mock.patch.object(dt, "trash_dir", return_value=base):
            got = dt.to_trash(f)
        self.assertEqual(got, base / "files" / "01 a b.flac")
        info = (base / "info" / "01 a b.flac.trashinfo").read_text()
        self.assertIn("Path=Artist/01%20a%20b.flac\n", info)

    def test_the_setting_takes_trash(self):
        app_settings.set_value("duplicates.dispose", "trash")
        self.assertEqual(app_settings.get("duplicates.dispose"), "trash")
        with self.assertRaises(ValueError):
            app_settings.set_value("duplicates.dispose", "delete")


try:
    from PySide6.QtWidgets import QApplication
except ImportError:                          # pragma: no cover
    QApplication = None


@unittest.skipIf(QApplication is None, "PySide6 not installed")
class Pane(Base):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def pane(self):
        from gui import preferences
        return preferences.FilesPane()

    def test_a_folder_at_a_library_top_is_taken_by_its_name(self):
        from gui import sheets
        lib = self.tmp / "music"
        lib.mkdir()
        app_settings.set_value("library_folder", str(lib))
        p = self.pane()
        p.dupes.setText(str(lib / "2delete"))
        p.set_dupes()
        self.assertEqual(app_settings.duplicates_folder(), "2delete")
        self.assertEqual(p.dupes.text(), "2delete")
        with mock.patch.object(sheets, "warning") as warned:               # (not at a library's top)
            p.dupes.setText(str(self.tmp / "elsewhere" / "dupes"))
            p.set_dupes()
        self.assertTrue(warned.called)
        self.assertEqual(app_settings.duplicates_folder(), "2delete")

    def test_the_default_music_folder_counts_as_a_library_top(self):
        music = self.tmp / "Music"
        music.mkdir()
        with mock.patch.dict(os.environ, {"TAGMENDER_MUSIC_ROOT": str(music)}):
            p = self.pane()
            p.dupes.setText(str(music / "Second Copies"))
            p.set_dupes()
        self.assertEqual(app_settings.duplicates_folder(), "Second Copies")

    def test_choosing_the_trash_hides_the_folder(self):
        p = self.pane()
        p.dispose.setCurrentIndex(p.dispose.findData("trash"))
        self.assertEqual(app_settings.get("duplicates.dispose"), "trash")
        self.assertTrue(p.dupes_row.isHidden())


if __name__ == "__main__":
    unittest.main()
