# BOX-2 (docs/MATCHING_RULES.md): one disc of a set in a folder of its own, where the set's discs
# list the same titles (Nektar - Journey To The Centre Of The Eye, BELLE 132133-4: the original
# album on an SHM-CD and a live CD of the same 13 titles): the lengths tell which disc it is - never
# the disc that lists no lengths because the other's listed lengths are off. Synthetic data: no network.
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import match_core as mc  # noqa: E402

TITLES = ["Prelude", "Astronaut's Nightmare", "Countenance", "Warp Oversight"]
STUDIO = [86.0, 387.0, 214.0, 250.0]
LIVE = [123.0, 410.0, 218.0, 269.0]


def album(secs=STUDIO, barcodes=("4527516600990",)):
    return {"title": "Journey To The Centre Of The Eye", "barcodes": set(barcodes),
            "tracks": [{"disc": 1, "num": i + 1, "title": t, "secs": s} for i, (t, s) in enumerate(zip(TITLES, secs))]}


def cand(d1=STUDIO, d2=LIVE, source="mb"):
    return {"source": source, "id": "6b2c", "title": "Journey to the Centre of the Eye",
            "barcodes": ["4527516600990"], "disc_titles": {1: "Original Album", 2: "Official Bootleg: Live in Germany"},
            "tracks": [{"disc": 1, "title": t, "secs": s} for t, s in zip(TITLES, d1)] +
                      [{"disc": 2, "title": t, "secs": s} for t, s in zip(TITLES, d2)]}


class BOX2SameTitles(unittest.TestCase):
    def test_BOX2_same_titles_the_lengths_choose_the_disc(self):
        box = mc._box_disc(album(), cand())
        self.assertEqual((box["box_disc"], box["box_proof"]), (1, "barcode and lengths"))
        self.assertEqual([t["secs"] for t in box["tracks"]], STUDIO)
        self.assertEqual(mc._box_disc(album(LIVE), cand())["box_disc"], 2)

    def test_BOX2_never_the_unlisted_disc_beside_one_whose_lengths_are_off(self):
        # (Discogs 10554394: disc 1 listed with other lengths, the live disc with none - BOX-3's
        # titles fit both, so neither is chosen by them)
        off = [x + 15 for x in STUDIO]
        self.assertIsNone(mc._box_disc(album(), cand(d1=off, d2=[None] * 4, source="discogs")))

    def test_BOX3_an_unlisted_disc_is_never_chosen_because_its_twin_is_off(self):
        # (intended, review 0.8.73: disc 1 lists no lengths, disc 2 the same titles 20 s off each.
        # Before, BOX-3 chose disc 1. Lengths off on a listed disc do not prove the other is yours -
        # Discogs 10554394 lists Nektar's own studio disc with the LP's lengths, off by as much - so
        # neither is chosen; another database listing the lengths, or the release whole, decides)
        off = [x + 20 for x in STUDIO]
        self.assertIsNone(mc._box_disc(album(), cand(d1=[None] * 4, d2=off, source="discogs")))

    def test_BOX2_lengths_route_needs_your_barcode(self):
        self.assertIsNone(mc._box_disc(album(barcodes=()), cand()))
        self.assertIsNone(mc._box_disc(album(barcodes=("0000000000000",)), cand()))

    def test_BOX2_two_discs_of_your_lengths_choose_none(self):
        self.assertIsNone(mc._box_disc(album(), cand(d2=STUDIO)))


if __name__ == "__main__":
    unittest.main()
