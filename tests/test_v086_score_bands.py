# 0.8.6: the Score cell is tinted by how far the match can be trusted - green (excellent), blue
# (good), amber (worth a look), red (needs investigation) - from the score AND its evidence.
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_gui as tg  # noqa: E402


def album(status="ready", conflicts=(), **ident):
    return {"source_dir": "/m/x", "status": status, "files": [], "conflicts": list(conflicts),
            "identification": dict({"provider": "musicbrainz", "confidence": 90, "tier": "exact"}, **ident)}


@unittest.skipIf(tg.QApplication is None, "PySide6 not installed")
class ScoreBands(unittest.TestCase):
    def test_bands(self):
        from gui.models import score_band
        for a, band in ((album(confidence=100), "excellent"),
                        (album(via="disc_id"), "excellent"),
                        (album(scans={"state": "proven"}), "excellent"),
                        (album(), "good"),
                        (album(confidence=100, scans={"state": "differs"}), "investigate"),   # (not the score alone)
                        (album(tier="album_only", confidence=96), "check"),
                        (album(tier="partial"), "check"),
                        (album(confidence=80), "check"),
                        (album(confidence=60), "investigate"),
                        (album(status="unresolved", confidence=0, tier="unresolved"), "investigate"),
                        (album(conflicts=["x"]), "investigate"),
                        (album(status="listed", confidence=0), None),
                        (album(status="excluded"), None)):
            self.assertEqual(score_band(a), band, a)

    def test_the_cell_is_tinted_and_says_why(self):
        tg.QApplication.instance() or tg.QApplication([])
        from gui.models import PlanModel
        m = PlanModel()
        m.albums = [album(confidence=100), album(status="listed", confidence=0)]
        self.assertIsNotNone(m.data(m.index(0, 3), tg.Qt.BackgroundRole))
        self.assertIn("not a probability", m.data(m.index(0, 3), tg.Qt.ToolTipRole))
        self.assertIsNone(m.data(m.index(1, 3), tg.Qt.BackgroundRole))


if __name__ == "__main__":
    unittest.main()
