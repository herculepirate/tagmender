"""TRK-4: MusicBrainz and Discogs split the same tracks into discs otherwise - the files'
own numbers say whose layout numbers them, never a mix of the two."""
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

import engine  # noqa: E402


def mb(layout, subs=None):
    out = []
    for d, n in enumerate(layout, 1):
        for k in range(1, n + 1):
            t = {"discnumber": [str(d)], "totaldiscs": [str(len(layout))], "tracknumber": [str(k)],
                 "totaltracks": [str(n)], "musicbrainz_trackid": [f"t{d}-{k}"]}
            if subs:
                t["discsubtitle"] = [subs[d - 1]]
            out.append(t)
    return out


def dg(layout):
    return [{"discnumber": str(d), "totaldiscs": str(len(layout)), "tracknumber": str(k), "totaltracks": str(n),
             "title": f"T{d}.{k}"} for d, n in enumerate(layout, 1) for k in range(1, n + 1)]


def files(layout):
    return [{"disc": d, "num": k} for d, n in enumerate(layout, 1) for k in range(1, n + 1)]


def pos(t):
    g = lambda k: (t[k][0] if isinstance(t[k], list) else t[k])   # noqa: E731
    return (g("discnumber"), g("tracknumber"), g("totaltracks"))


class TRK4(unittest.TestCase):
    def test_TRK4_files_follow_discogs(self):
        # (The Studio Albums 1973-1983: MusicBrainz 8 + 10 on discs 4-5, Discogs and the files 9 + 9)
        m = mb([3, 2, 4], subs=["A", "B", "C"])
        d = dg([3, 3, 3])
        tm, td, note = engine.settle_disc_layout(m, d, files([3, 3, 3]))
        self.assertIn("TRK-4", note)
        self.assertEqual([pos(t) for t in tm], [pos(t) for t in td])
        self.assertEqual(pos(tm[5]), ("2", "3", "3"))            # (MusicBrainz's disc 3 track 1)
        self.assertEqual(tm[5]["discsubtitle"], ["B"])          # (its new disc's title)
        self.assertEqual(tm[5]["musicbrainz_trackid"], ["t3-1"])   # (the track stays its own)

    def test_TRK4_files_follow_musicbrainz(self):
        m, d = mb([2, 4]), dg([3, 3])
        tm, td, note = engine.settle_disc_layout(m, d, files([2, 4]))
        self.assertEqual([pos(t) for t in td], [pos(t) for t in tm])
        self.assertIn("your files", note)

    def test_TRK4_one_file_numbered_otherwise_does_not_outvote(self):
        own = files([3, 3, 3])
        own[5] = {"disc": 3, "num": 3}            # (an earlier run's mixed numbers on one file)
        tm, td, _ = engine.settle_disc_layout(mb([3, 2, 4]), dg([3, 3, 3]), own)
        self.assertEqual(pos(tm[5]), ("2", "3", "3"))

    def test_TRK4_unnumbered_files_take_musicbrainz_whole(self):
        own = [{"disc": 1, "num": 0}] * 9
        tm, td, _ = engine.settle_disc_layout(mb([3, 2, 4]), dg([3, 3, 3]), own)
        self.assertEqual([pos(t) for t in td], [pos(t) for t in tm])     # (never a mix)
        self.assertEqual(pos(td[5]), ("3", "1", "4"))

    def test_TRK4_an_offset_or_a_one_disc_listing_is_no_other_split(self):
        # (review C: MusicBrainz numbering the CD 2 after a DVD, Discogs one disc 1-6; a set
        # listed as one disc; a hidden track 0 - the plugin's merge as before, nothing moved)
        m = mb([3, 3])
        for t in m:
            t["discnumber"] = [str(int(t["discnumber"][0]) + 1)]
            t["media"] = ["CD"]
        cases = [(m, dg([3, 3])), (mb([3, 3]), dg([6])), (mb([6]), dg([3, 3]))]
        for tm0, td0 in cases:
            tm, td, note = engine.settle_disc_layout(tm0, td0, files([3, 3]))
            self.assertEqual(note, "")
            self.assertIs(tm, tm0)
        h = mb([4])
        for k, t in enumerate(h):
            t["tracknumber"] = [str(k)]
        self.assertEqual(engine.settle_disc_layout(h, dg([4]), files([4]))[2], "")

    def test_TRK4_same_layout_untouched(self):
        m, d = mb([3, 3]), dg([3, 3])
        tm, td, note = engine.settle_disc_layout(m, d, files([3, 3]))
        self.assertEqual(note, "")
        self.assertIs(tm, m)
        self.assertIs(td, d)


if __name__ == "__main__":
    unittest.main()
