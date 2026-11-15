"""CAT-5: a catalogue number printed on your copy proves a pressing when the number your
folder, tags or rip logs name is on none of the releases (Judas Priest - Metalogy: the logs
type the US discs' CK 87127-87130; the box, Discogs C4K 87126, prints its own on the back)."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import match_core as mc  # noqa: E402
from test_match import CFG, album, cand  # noqa: E402

TEXT = "JUDAS PRIEST METALOGY Columbia C4K 87126 Made in U.S.A."


def box(**kw):
    typed = {"ck87127", "ck87128"}
    a = album(folder="2004 - First Light [Sony, USA, 4 CD-Bonus DVD]", catnos=set(typed),
              catnos_from_folder=set(typed), catnos_typed=set(typed), printed_text=TEXT)
    a.update(kw)
    return a


class CAT5PrintedNumber(unittest.TestCase):
    def classify(self, a, *cands, text=TEXT):
        return mc.classify([mc.score_candidate(a, c, CFG, ocr_text=text) for c in cands], CFG)

    def cands(self):
        return (cand(id="us-box", country="US", catnos={"c4k87126"}),
                cand(id="us-cd", country="US", catnos={"88697361602"}),
                cand(id="jp", country="JP", catnos={"mhcp312"}))

    def test_CAT5_a_number_printed_on_your_scans_picks_the_pressing(self):
        r = self.classify(box(), *self.cands())
        self.assertEqual(r["best"]["cand"]["id"], "us-box", r["why"])
        self.assertFalse(any("is on none of these pressings" in w and "probably" not in w for w in r["why"]),
                         r["why"])

    def test_CAT5_a_number_only_typed_in_notes_proves_nothing(self):
        # the same text in typed notes, not on a scan: not your copy's print
        a = box(printed_text="", typed_text=TEXT)
        r = self.classify(a, *self.cands())
        self.assertNotEqual(r["status"], "auto")

    def test_CAT5_no_number_on_the_scans_still_holds_the_pick(self):
        r = self.classify(box(printed_text="JUDAS PRIEST METALOGY"), *self.cands(), text="JUDAS PRIEST METALOGY")
        self.assertNotEqual(r["status"], "auto")


if __name__ == "__main__":
    unittest.main()
