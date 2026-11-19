# 0.8.84 (MOV-6): an album kept where it is only for its score - below the profile's
# organize_min_confidence - is offered for filing; its tick is the ask, also when it has tags to
# write where it is. Before, such an album (Saxon - Crusader 1984 LP, 92.9 < 95) started ticked
# and Apply tagged it in place, never moving it, though what you tick is filed.
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

try:
    from PySide6.QtWidgets import QApplication
except ImportError:                          # pragma: no cover
    QApplication = None


def below_floor_album():
    return {"source_dir": "/x/Saxon", "status": "ready", "conflicts": [], "identification": {}, "notes": [],
            "files": [{"src": "/x/Saxon/1.flac", "dst": "/x/Saxon/1.flac", "op": "tag",
                       "changes": {"title": ["Crusader"]}, "diff": []}],
            "below_floor": {"confidence": 92.9, "floor": 95, "op": "move"}}


@unittest.skipIf(QApplication is None, "PySide6 not installed")
class MOV6BelowFloorTick(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_MOV6_below_floor_with_tags_starts_unticked_and_its_tick_asks_to_file(self):
        from gui.models import PlanModel
        m = PlanModel()
        a = below_floor_album()
        m.set_plan({"albums": [a]})
        self.assertTrue(PlanModel.left_out(a))                     # (unticked at first: the score decides that)
        asked = []
        m.file_asked.connect(lambda albums: asked.extend(albums))
        m.set_left_out([a], False)
        self.assertEqual(asked, [a])                               # (ticked: looked up again to be filed)
        self.assertFalse(PlanModel.left_out(a))
        m.set_left_out([a], True)
        self.assertTrue(PlanModel.left_out(a))

    def test_APL2_a_highlight_never_stands_in_for_that_tick(self):
        from gui.overview import Overview
        from gui.models import PlanModel
        a = below_floor_album()
        self.assertFalse(Overview.highlight_applies(a))
        b = dict(below_floor_album(), below_floor=None)
        self.assertTrue(Overview.highlight_applies(b))
        b[PlanModel.AWAIT] = True                                  # (Use … and File It…: shown to be checked)
        self.assertFalse(Overview.highlight_applies(b))


if __name__ == "__main__":
    unittest.main()
