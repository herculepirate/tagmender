"""MOV-5: an album below organize_min_confidence keeps its move, held until you tick it; an
unattended profile tags it in place. Synthetic files only."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import test_executor as te  # noqa: E402
import plan as pl  # noqa: E402


class MOV5HeldMove(te.Base):
    def test_MOV5_copy_below_threshold_is_held_not_skipped(self):
        prof = self.profile(operation="copy", destination=str(self.dest))
        a = pl.build([self.proposal(conf=91)], prof)["albums"][0]
        self.assertEqual(a["status"], "ready")
        self.assertIn("copied only when you tick it", a["held"])
        self.assertTrue(all(fe["op"] == "copy" for fe in a["files"]))

    def test_MOV5_unattended_copy_below_threshold_still_skipped(self):
        prof = self.profile(operation="copy", destination=str(self.dest), unattended=True)
        a = pl.build([self.proposal(conf=91)], prof)["albums"][0]
        self.assertEqual(a["status"], "skipped")
        self.assertFalse(a.get("held"))

    def test_MOV5_above_threshold_not_held(self):
        prof = self.profile(operation="move", destination=str(self.dest))
        a = pl.build([self.proposal(conf=97)], prof)["albums"][0]
        self.assertFalse(a.get("held"))

    def test_MOV5_in_place_profile_not_held(self):
        a = pl.build([self.proposal(conf=91)], self.profile())["albums"][0]
        self.assertFalse(a.get("held"))


if __name__ == "__main__":
    unittest.main()
