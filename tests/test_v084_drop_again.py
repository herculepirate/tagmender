# 0.8.4: folders whose albums were taken out of the list (Remove from List) can be dropped
# again: only albums still listed count as "already in the preview".
import sys
import types
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_gui as tg  # noqa: E402  (isolated config, offscreen Qt)


@unittest.skipIf(tg.QApplication is None, "PySide6 not installed")
class DropAgain(unittest.TestCase):
    def test_removed_albums_folders_are_new_again(self):
        from gui.manual_match import new_paths
        tmp = Path(tg._tempfile.mkdtemp()) if hasattr(tg, "_tempfile") else Path(__import__("tempfile").mkdtemp())
        a, b = tmp / "Band" / "1989 - A", tmp / "Band" / "1990 - B"
        for d in (a, b):
            d.mkdir(parents=True)
            (d / "01.flac").write_bytes(b"x")
        album = lambda d: {"source_dir": str(d), "files": [{"src": str(d / "01.flac")}]}  # noqa: E731
        page = types.SimpleNamespace(path=str(tmp / "Band"), plan={"albums": [album(a), album(b)]})
        self.assertEqual(new_paths(page, [str(a), str(b)]), ([], [str(a), str(b)]))
        self.assertEqual(new_paths(page, [str(tmp / "Band")]), ([], [str(tmp / "Band")]))
        page.removed = {str(a)}
        page.plan["albums"] = [album(b)]                                   # (A taken out of the list)
        self.assertEqual(new_paths(page, [str(a), str(b)]), ([str(a)], [str(b)]))
        page.removed = {str(a), str(b)}
        page.plan["albums"] = []                                           # (all taken out)
        self.assertEqual(new_paths(page, [str(a), str(b)]), ([str(a), str(b)], []))


if __name__ == "__main__":
    unittest.main()
