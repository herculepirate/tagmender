# MED-22b (docs/MATCHING_RULES.md): a hybrid SACD whose CD layer adds bonus tracks after the
# SACD layers' own is still one disc. Modelled on a 50th-anniversary Japanese edition listed on
# MusicBrainz as CD layer (14 tracks: the album's 10 + 4 live), stereo and multichannel SACD
# layers (the 10), and a live CD (18): a CD-layer rip is 32 files on 2 discs, not 42 on 3.
# Synthetic data only.
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_engine as te  # noqa: E402,F401  (isolates XDG dirs and Picard.ini first)
from test_v0868_med22_hybrid_layers import CDL, SA2, SAM, medium, rel_of  # noqa: E402
import engine  # noqa: E402
import match_albums as ma  # noqa: E402
import mb_tags  # noqa: E402

ALBUM = [f"Song {k}" for k in range(1, 11)]
BONUS = [f"Song {k} (live)" for k in (1, 2, 6, 9)]
LIVE = [f"Live {k}" for k in range(1, 19)]


def anniversary(cd_titles=ALBUM + BONUS, sacd_titles=ALBUM):
    return rel_of([medium(1, CDL, cd_titles), medium(2, SA2, sacd_titles), medium(3, SAM, sacd_titles),
                   medium(4, "CD", LIVE, "Live")])


def positions(rel):
    return [[m["position"] for m in g] for g in mb_tags.layer_groups(rel)]


class MED22b(unittest.TestCase):
    def test_MED22b_a_cd_layers_bonus_tracks_keep_the_disc_one(self):
        self.assertEqual(positions(anniversary()), [[1, 2, 3], [4]])
        cand = ma.mb_candidate(anniversary())
        self.assertEqual((len(cand["tracks"]), cand["disc_count"]), (32, 2))
        self.assertEqual([t["disc"] for t in cand["tracks"]].count(1), 14)
        self.assertEqual(cand["layer_positions"], {1: 1, 2: 1, 3: 1, 4: 2})

    def test_MED22b_each_layer_as_your_copy_is(self):
        rel = anniversary()
        self.assertEqual([len(m["tracks"]) for m in mb_tags.one_layer(rel, "cd")["media"]], [14, 18])
        self.assertEqual([len(m["tracks"]) for m in mb_tags.one_layer(rel, "stereo")["media"]], [10, 18])
        cd = {"tracks": [{"disc": d, "audio": {"rate": 44100, "bits": 16}} for d in (1, 2)]}
        self.assertEqual(engine._layer_prefer(cd, rel), {1: "cd", 2: "cd"})

    def test_MED22b_counts_alone(self):
        hit = {"id": "h", "track-count": 52, "media": [{"format": f, "track-count": n} for f, n in
                                                       ((CDL, 14), (SA2, 10), (SAM, 10), ("CD", 18))]}
        self.assertEqual(ma._mb_hit_tracks(hit), 32)


class MED22b_Limits(unittest.TestCase):
    def test_MED22b_only_the_cd_layer_may_add(self):
        # (an SACD layer of more tracks than the CD layer: another disc)
        self.assertEqual(positions(rel_of([medium(1, CDL, ALBUM), medium(2, SA2, ALBUM + BONUS)])), [[1], [2]])

    def test_MED22b_the_same_titles_first(self):
        other = ["Other"] + ALBUM[1:]
        self.assertEqual(positions(anniversary(sacd_titles=other)), [[1], [2, 3], [4]])

    def test_MED22b_no_more_than_as_many_again(self):
        self.assertEqual(positions(anniversary(cd_titles=ALBUM + LIVE)), [[1], [2, 3], [4]])

    def test_MED22b_counts_alone_limits(self):
        def hit(*counts):
            return {"id": "h", "track-count": sum(n for _f, n in counts),
                    "media": [{"format": f, "track-count": n} for f, n in counts]}
        self.assertEqual(ma._mb_hit_tracks(hit((CDL, 10), (SA2, 14))), 24)    # (SACD layer longer)
        self.assertEqual(ma._mb_hit_tracks(hit((CDL, 21), (SA2, 10))), 31)    # (more than as many again)
        self.assertEqual(ma._mb_hit_tracks(hit((CDL, 14), (CDL, 10), (SA2, 10), (SA2, 14))), 48)   # (order)


if __name__ == "__main__":
    unittest.main(verbosity=2)
