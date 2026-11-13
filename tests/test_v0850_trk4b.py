"""TRK-4b: one database numbers a set as one run (Discogs's A1..D2 of a 2LP -> 1..n), the other
disc by disc (MusicBrainz 3 + 3). The merge wrote MusicBrainz's disc beside Discogs's track:
Deftones - Palms "disc 2, track 4 of 6", Tame Impala - Innerspeaker "disc 2, track 7 of 11".
Every file now gets one layout, the one the files' own numbers follow."""
import os as _os, tempfile as _tempfile  # noqa: E402
_os.environ.setdefault("XDG_CONFIG_HOME", _tempfile.mkdtemp(prefix="tm-config-"))
_os.environ.setdefault("XDG_STATE_HOME", _tempfile.mkdtemp(prefix="tm-state-"))
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import engine  # noqa: E402


def mb(per_disc=(3, 3)):
    out = []
    for d, n in enumerate(per_disc, 1):
        out += [{"discnumber": [str(d)], "tracknumber": [str(k)], "totaltracks": [str(n)],
                 "totaldiscs": [str(len(per_disc))], "media": ["12\" Vinyl"]} for k in range(1, n + 1)]
    return out


def dg(n=6, discs=2):
    return [{"discnumber": "1", "tracknumber": str(k), "totaltracks": str(n), "totaldiscs": str(discs)}
            for k in range(1, n + 1)]


def layout(tags):
    return [((t.get("discnumber") or [None])[0] if isinstance(t.get("discnumber"), list) else t.get("discnumber"),
             (t["tracknumber"][0] if isinstance(t["tracknumber"], list) else t["tracknumber"])) for t in tags]


class TRK4b_OneRunAgainstDiscs(unittest.TestCase):
    def test_files_numbered_as_one_run_keep_one_run_and_lose_no_number(self):
        files = [{"disc": 1, "disc_tag": 0, "num": k} for k in range(1, 7)]
        m, d, note = engine.settle_disc_layout(mb(), dg(), files)
        self.assertEqual(layout(m), [(None, str(k)) for k in range(1, 7)])
        self.assertEqual(layout(d), [(None, str(k)) for k in range(1, 7)])
        self.assertTrue(all(t["totaltracks"] == ["6"] for t in m))
        self.assertTrue(all("discnumber" in t["~remove"] and "totaldiscs" in t["~remove"] for t in m),
                        "the disc numbers an earlier run wrote beside one run go")
        self.assertIn("TRK-4b", note)

    def test_your_own_disc_numbers_are_kept(self):
        files = [{"disc": 1, "disc_tag": 1, "num": k} for k in range(1, 7)]
        m, _d, _n = engine.settle_disc_layout(mb(), dg(), files)
        self.assertFalse(any("~remove" in t for t in m), "a disc number your files had is no earlier run's")

    def test_files_numbered_disc_by_disc_follow_the_split(self):
        files = [{"disc": 1, "disc_tag": 1, "num": k} for k in (1, 2, 3)] + \
                [{"disc": 2, "disc_tag": 2, "num": k} for k in (1, 2, 3)]
        m, d, _n = engine.settle_disc_layout(mb(), dg(), files)
        self.assertEqual(layout(m), [("1", "1"), ("1", "2"), ("1", "3"), ("2", "1"), ("2", "2"), ("2", "3")])
        self.assertEqual(layout(d), layout(m), "both databases' tags carry the one layout")
        self.assertTrue(all(t["totaltracks"] == ["3"] for t in m))

    def test_no_numbers_take_the_split(self):
        m, d, _n = engine.settle_disc_layout(mb((6, 5)), dg(11), [{"disc": 1, "num": 0} for _ in range(11)])
        self.assertEqual(layout(m)[6], ("2", "1"))
        self.assertEqual(layout(d)[6], ("2", "1"), "never disc 2 track 7")

    def test_the_same_layout_or_another_split_unchanged(self):
        same = mb()
        dg_same = [{"discnumber": t["discnumber"][0], "tracknumber": t["tracknumber"][0]} for t in same]
        self.assertEqual(engine.settle_disc_layout(same, dg_same, [])[2], "")
        self.assertEqual(engine._one_run_vs_discs([(1, 1), (1, 3)], [(1, 1), (2, 1)]), "")
        self.assertEqual(engine._one_run_vs_discs([(1, 1), (1, 2)], [(1, 1), (2, 1)]), "mb")


if __name__ == "__main__":
    unittest.main()
