# MED-15 (docs/MATCHING_RULES.md): a Discogs position naming its carrier with the track's number
# alone ("CD-1" beside "BD-1".."BD-33": Gojira - The Flesh Alive, Discogs 3663285, a Blu-ray listed
# first and the CD) is that carrier's one disc - the CD rip is "CD, Enhanced", never "Blu-ray" (the
# first medium, which the CD's disc number 1 pointed to once the Blu-ray was left out, MED-16).
# Synthetic release: no network.
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import engine  # noqa: E402


def release():
    return {"formats": [{"name": "Blu-ray", "qty": "1", "descriptions": []},
                        {"name": "CD", "qty": "1", "descriptions": ["Enhanced"]},
                        {"name": "All Media", "qty": "1", "descriptions": ["Limited Edition"]}],
            "tracklist": [{"position": f"BD-{i}", "title": f"V{i}"} for i in range(1, 4)] +
                         [{"position": f"CD-{i}", "title": f"A{i}"} for i in range(1, 4)]}


class MED15BareCarrier(unittest.TestCase):
    def test_MED15_cd_dash_track_is_the_cd(self):
        rel = release()
        cds = [t for t in rel["tracklist"] if t["position"].startswith("CD")]
        self.assertEqual(engine.discogs_track_media(rel, cds, [1, 1, 1]), ["CD, Enhanced"] * 3)

    def test_MED15_bd_dash_track_is_the_blu_ray(self):
        rel = release()
        bds = [t for t in rel["tracklist"] if t["position"].startswith("BD")]
        # ("BD" is no name of a format listed: the media in order, as before)
        self.assertEqual(engine.discogs_track_media(rel, bds, [1, 1, 1]), ["Blu-ray"] * 3)

    def test_MED15_two_cds_named_without_number_stay_in_order(self):
        rel = release()
        rel["formats"][1]["qty"] = "2"
        cds = [t for t in rel["tracklist"] if t["position"].startswith("CD")]
        # (two CDs: "CD-1" does not say which - the media in order by the disc, as before)
        self.assertEqual(engine.discogs_track_media(rel, cds, [2, 2, 2]), ["CD, Enhanced"] * 3)
        self.assertEqual(engine.discogs_track_media(rel, cds, [1, 1, 1]), ["Blu-ray"] * 3)


if __name__ == "__main__":
    unittest.main()
