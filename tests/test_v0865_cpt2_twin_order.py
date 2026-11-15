"""CPT-2: of a Discogs pick's MusicBrainz twins otherwise alike, one nothing of your copy speaks
against comes before one it does (a pressing later than your rip log, DAT-3), and one linking
the pick itself before one linking none (Pekka Pohjola - Flight Of The Angel: Discogs 3625724
brings MusicBrainz 1ec067b6, which links it, not the 2012 192d6570). Synthetic results only."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import match_core as mc  # noqa: E402

DG = {"source": "discogs", "id": "3625724"}


def twin(id_, links=(), against=(), conf=100.0, toc=False):
    s = {"cand": {"source": "mb", "id": id_, "discogs_ids": list(links)}, "confidence": conf,
         "clues": {}, "clues_against": list(against)}
    if toc:
        s["clues"]["disc TOC"] = mc.EXACT_TOC_CLUE
    return s


class CPT2TwinOrder(unittest.TestCase):
    def test_CPT2_the_twin_linking_the_pick_before_one_linking_none(self):
        for order in ((0, 1), (1, 0)):
            ts = [twin("mb-2012"), twin("mb-2002", links=["3625724"])]
            ts = [ts[i] for i in order]
            self.assertEqual(mc.best_twin(ts, DG)["cand"]["id"], "mb-2002", order)

    def test_CPT2_a_twin_your_rip_log_rules_out_comes_last(self):
        ts = [twin("mb-2012", links=["3625724"], against=[mc.RIP_DATE_AGAINST + "2005; this pressing came out in 2012"]),
              twin("mb-2002")]
        self.assertEqual(mc.best_twin(ts, DG)["cand"]["id"], "mb-2002")

    def test_CPT2_a_twin_your_rip_log_rules_out_loses_its_disc_id_lead(self):
        # (Flight Of The Angel: the CUETools report's disc ID is on the 2012 entry too, which
        # DAT-3 left out of MusicBrainz's own pick: never written beside Discogs's 2002 CD)
        ts = [twin("mb-2012", toc=True, against=[mc.RIP_DATE_AGAINST + "2005; this pressing came out in 2012"]),
              twin("mb-2002", links=["3625724"])]
        self.assertEqual(mc.best_twin(ts, DG)["cand"]["id"], "mb-2002")

    def test_CPT2_your_disc_id_still_comes_first(self):
        # (CPT-1 as before: the twin carrying your disc ID, linking none, before one linking the pick)
        ts = [twin("mb-link", links=["3625724"]), twin("mb-toc", toc=True)]
        self.assertEqual(mc.best_twin(ts, DG)["cand"]["id"], "mb-toc")

    def test_CPT2_one_linking_another_discogs_release_still_loses(self):
        ts = [twin("mb-other", links=["999"], toc=True), twin("mb-none")]
        self.assertEqual(mc.best_twin(ts, DG)["cand"]["id"], "mb-none")


if __name__ == "__main__":
    unittest.main()
