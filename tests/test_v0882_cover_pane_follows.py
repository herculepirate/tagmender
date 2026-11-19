# 0.8.82: the Cover pane shows the cover you chose. A cover chosen for the album shown (Choose
# Cover…, a found one, an image file or link) was staged and put in the plan - Apply would write
# it - but the pane beside kept the cover it had read before: it read the pictures again only for
# another album, and your edit keeps the album the same entry.
import io
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_v0869_preview_panes as _pp  # noqa: E402  (its set-up: a window with a preview)


@unittest.skipIf(_pp.QApplication is None, "PySide6 not installed")
class CoverPaneFollows(_pp.PreviewPanesBase):
    def test_a_cover_you_choose_is_shown_in_the_cover_pane(self):
        from PIL import Image
        from gui.inspector import stage_cover
        ov = self.show_plan()
        p = ov.panes
        album = ov.model.albums[0]
        p.select("album", None, album)
        self.assertTrue(_pp.wait(lambda: p.cover_worker is None, self.app))
        buf = io.BytesIO()
        Image.new("RGB", (3000, 3000), (20, 40, 60)).save(buf, "JPEG")
        cover = stage_cover(buf.getvalue(), "example.org")
        insp = self.win.inspector
        insp.show_album(album, ov.plan)
        insp.set_cover(cover, album)
        self.assertTrue(_pp.wait(lambda: ((album.get("artwork") or {}).get("width") == 3000), self.app),
                        album.get("artwork"))
        self.assertTrue(_pp.wait(lambda: p.cover_worker is None and p.covers
                                 and p.covers[0].kind == "new" and p.covers[0].width == 3000, self.app),
                        [(c.kind, c.width) for c in p.covers])
        self.assertIn("3000 × 3000", p.cover_caption.text())
        self.assertIn("your image", p.cover_strip.item(0).text())


if __name__ == "__main__":
    unittest.main()
