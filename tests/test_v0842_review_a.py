# Review A of the 0.8.4x matching changes (docs/MATCHING_RULES.md): BAR-7's barcode decides
# only an album that fits (half its lengths or titles, as BAR-8's); TRK-2 counts moved tracks
# as all but the longest run in order, and a release in the files' order keeps reordered ones
# out of the tie; TRK-3's medium split only where each medium is one disc; BAR-8's printed
# catalogue number is never the start of a longer one nor joined across lines; TRK-1 merges a
# work only when none of its movements has a length. Synthetic data only, no network.
import os as _os, tempfile as _tempfile  # noqa: E401
_os.environ["XDG_CONFIG_HOME"] = _tempfile.mkdtemp(prefix="tm-config-")
_os.environ["XDG_STATE_HOME"] = _tempfile.mkdtemp(prefix="tm-state-")
_os.environ.setdefault("TAGMENDER_TAGGER_INI", "/nonexistent")

import sys  # noqa: E402
import unittest  # noqa: E402
from pathlib import Path  # noqa: E402

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "tests"))
sys.path.insert(0, str(HERE / "plugins_builtin" / "discogs"))
import discogs_mapping as dmap  # noqa: E402
import match_albums as ma  # noqa: E402
import match_core as mc  # noqa: E402
from test_v0832_rules import album  # noqa: E402

CD_AUDIO = {"cd_frames": True, "not_cd": False, "hires": False, "lossless": True}
PLAIN_AUDIO = {"cd_frames": False, "not_cd": False, "hires": False, "lossless": True}


def _bar7_album():
    return dict(album(CD_AUDIO), title="Greatest Hits", artist="Foo", barcodes={"075678182522"},
                tracks=[{"disc": 1, "num": i + 1, "title": f"My Song {chr(65 + i)}", "secs": 180.0 + i * 7}
                        for i in range(10)])


def _bar7_cand(titles, secs):
    return {"source": "mb", "id": "gh", "artist": "Foo", "title": "Greatest Hits",
            "barcodes": {"075678182522"}, "catnos": set(), "country": "US", "formats": {"cd"},
            "year": 2001, "date": "2001", "disc_count": 1,
            "tracks": [{"disc": 1, "title": t, "secs": s} for t, s in zip(titles, secs)]}


class BAR7(unittest.TestCase):
    CFG = dict(mc.DEFAULT_CFG, preferred_countries=["US"])

    def test_BAR7_barcode_on_an_album_that_fits_neither_by_length_nor_title_decides_nothing(self):
        cand = _bar7_cand([f"Different Tune {i}" for i in range(10)], [300.0 + i * 11 for i in range(10)])
        s = mc.score_candidate(_bar7_album(), cand, self.CFG)
        out = mc.classify([s], self.CFG)
        self.assertNotEqual(out["status"], "auto")
        self.assertFalse(out["best"].get("own_number"))

    def test_BAR7_remaster_titles_agree_lengths_differ_barcode_still_decides(self):
        a = _bar7_album()
        cand = _bar7_cand([t["title"] for t in a["tracks"]], [300.0 + i * 11 for i in range(10)])
        s = mc.score_candidate(a, cand, self.CFG)
        out = mc.classify([s], self.CFG)
        self.assertEqual(out["status"], "auto", out.get("why"))
        self.assertTrue(out["best"].get("own_number"))
        self.assertTrue(any("(BAR-7)" in n for n in out["best"]["notes"]))

    def test_BAR7_untitled_files_lengths_all_differ_decide_nothing(self):
        # (no title to agree and every length against it: the barcode is not this album's)
        a = _bar7_album()
        for t in a["tracks"]:
            t["title"] = ""
        cand = _bar7_cand([f"My Song {chr(65 + i)}" for i in range(10)], [300.0 + i * 11 for i in range(10)])
        out = mc.classify([mc.score_candidate(a, cand, self.CFG)], self.CFG)
        self.assertFalse(out["best"].get("own_number"))

    def test_BAR7_nothing_to_compare_barcode_decides(self):
        # (untitled files and a release without lengths: nothing disagrees - the owner's rule)
        a = _bar7_album()
        for t in a["tracks"]:
            t["title"] = ""
        cand = _bar7_cand([f"My Song {chr(65 + i)}" for i in range(10)], [0.0] * 10)
        out = mc.classify([mc.score_candidate(a, cand, self.CFG)], self.CFG)
        self.assertTrue(out["best"].get("own_number"), out.get("why"))


T6 = [("Overture", 92), ("I Love You More", 357), ("Morning Glory", 256), ("My Days Are Numbered", 199),
      ("Without Her", 220), ("Just One Smile", 280)]
T10 = [(f"Track Title {w}", 200 + i * 13) for i, w in enumerate(
    ["Alpha", "Bravo", "Charlie", "Delta", "Echo", "Foxtrot", "Golf", "Hotel", "India", "Juliet"])]


def _cand(cid, country, order, table=T6):
    return {"source": "mb", "id": cid, "artist": "AC/DC", "title": "Live At Donington", "barcodes": set(),
            "catnos": set(), "country": country, "formats": {"cd"}, "year": 2003, "date": "2003",
            "disc_count": 1, "tracks": [{"disc": 1, "title": table[k][0], "secs": table[k][1]} for k in order]}


def _files(order, table=T6):
    return dict(album(PLAIN_AUDIO), tracks=[{"disc": 1, "num": i + 1, "title": table[k][0], "secs": table[k][1]}
                                             for i, k in enumerate(order)])


class TRK2(unittest.TestCase):
    CFG = dict(mc.DEFAULT_CFG, preferred_countries=["US", "DE"])

    def test_TRK2_one_bonus_track_moved_first_is_one_track_moved(self):
        order = [9] + list(range(9))
        self.assertIsNone(mc.order_view(_files(order, T10), _cand("x", "US", range(10), T10)))

    def test_TRK2_two_swapped_pairs_are_no_running_order_of_their_own(self):
        self.assertIsNone(mc.order_view(_files([1, 0, 3, 2]), _cand("x", "US", range(4))))

    def test_TRK2_two_of_four_moved_is_under_the_floor_of_three(self):
        # ([3, 1, 0, 2]: 1 and 2 stay in order, two tracks moved - three positions differ)
        self.assertIsNone(mc.order_view(_files([3, 1, 0, 2]), _cand("x", "US", range(4))))
        self.assertIsNotNone(mc.order_view(_files([3, 2, 1, 0]), _cand("x", "US", range(4))))

    def test_TRK2_half_moved_is_still_a_running_order_of_its_own(self):
        v = mc.order_view(_files([3, 4, 5, 0, 1, 2]), _cand("x", "US", range(6)))
        self.assertEqual(v and v["slots"], [3, 4, 5, 0, 1, 2])

    def test_TRK2_one_track_moved_release_is_not_picked_over_the_one_in_files_order(self):
        files = [5, 0, 1, 2, 3, 4]
        a = _files(files)
        s1 = mc.score_candidate(a, _cand("DE-in-order", "DE", files), self.CFG)
        s2 = mc.score_candidate(a, _cand("US-other", "US", range(6)), self.CFG)
        out = mc.classify([s2, s1], self.CFG)
        self.assertEqual(out["best"]["cand"]["id"], "DE-in-order")

    def test_TRK2_reordered_release_leaves_the_tie_to_the_one_in_files_order(self):
        files = [3, 4, 5, 0, 1, 2]
        a = _files(files)
        s1 = mc.score_candidate(a, _cand("DE-in-order", "DE", files), self.CFG)
        s2 = mc.score_candidate(a, _cand("US-other", "US", range(6)), self.CFG)
        self.assertTrue(s2["cand"].get("reordered"))
        self.assertEqual(s1["confidence"], s2["confidence"])
        out = mc.classify([s2, s1], self.CFG)
        self.assertEqual(out["best"]["cand"]["id"], "DE-in-order")      # (not the country order's US)
        self.assertEqual(out["status"], "auto", out.get("why"))
        self.assertTrue(any("(TRK-2)" in n and "US-other" in n for n in out["best"]["notes"]))


def tr(pos, title, dur="3:00", kind="track"):
    return {"position": pos, "title": title, "duration": dur, "type_": kind}


class TRK3(unittest.TestCase):
    def _two_cd(self):
        tl = [tr("", "CD 1", "", "heading")] + [tr(f"CD-{i}", f"T{i}") for i in range(1, 7)]
        tl += [tr("", "CD 2", "", "heading")] + [tr(f"CD-{i}", f"T{i}") for i in range(7, 13)]
        return tl

    def test_TRK3_two_cds_numbered_cd_1_to_cd_12_stay_two_discs(self):
        discs = dmap.parse_discs({"tracklist": self._two_cd(), "formats": [{"name": "CD", "qty": "2"}]})
        self.assertEqual([(d["number"], d["title"], len(d["tracks"])) for d in discs],
                         [(1, "CD 1", 6), (2, "CD 2", 6)])

    def test_TRK3_two_cds_numbered_cd_1_to_cd_12_no_formats_stay_two_discs(self):
        discs = dmap.parse_discs({"tracklist": self._two_cd()})
        self.assertEqual([(d["number"], d["title"], len(d["tracks"])) for d in discs],
                         [(1, "CD 1", 6), (2, "CD 2", 6)])

    def test_TRK3_two_cds_candidate_has_tracks_on_both_discs(self):
        rel = {"id": 1, "title": "X", "artists": [{"name": "A"}], "formats": [{"name": "CD", "qty": "2"}],
               "labels": [], "identifiers": [], "tracklist": self._two_cd()}
        c = ma.discogs_candidate(rel, dmap)
        self.assertEqual([t["disc"] for t in c["tracks"]], [1] * 6 + [2] * 6)

    def test_TRK3_one_cd_and_one_dvd_still_split_by_medium(self):
        tl = [tr(f"CD-{i}", f"T{i}") for i in range(1, 5)] + [tr(f"DVD-{i}", f"V{i}") for i in range(1, 3)]
        rel = {"tracklist": tl, "formats": [{"name": "CD", "qty": "1"}, {"name": "DVD", "qty": "1"}]}
        self.assertEqual([len(d["tracks"]) for d in dmap.parse_discs(rel)], [4, 2])
        self.assertEqual([len(d["tracks"]) for d in dmap.parse_discs({"tracklist": tl})], [4, 2])


class BAR8(unittest.TestCase):
    def printed(self, text, catno):
        return mc.catno_printed({"printed_text": text, "typed_text": ""}, catno)

    def test_BAR8_catno_that_starts_a_longer_printed_number_does_not_count(self):
        for catno, text in [("ecm1064", "ECM 1064/65"), ("sicp10151", "SICP 10151~4"),
                            ("srcs1234", "SRCS-1234-5")]:
            self.assertFalse(self.printed(text, catno), text)

    def test_BAR8_catno_is_not_joined_across_a_line_break(self):
        self.assertFalse(self.printed("CD\n12345 Main Street", "cd12345"))

    def test_BAR8_whole_numbers_still_count(self):
        for catno, text in [("2230/31", "2230 31"), ("2230/31", "Cat. 2230/31 Made in EU"),
                            ("charrmcd3", "CHARRMCD03"), ("ecm4764582", "ECM 476 4582"),
                            ("ecm1064", "ECM 1064 / 2004"), ("warpcd92", "WARPCD 92")]:
            self.assertTrue(self.printed(text, catno), text)


class TRK1(unittest.TestCase):
    def test_TRK1_a_movement_with_its_own_length_keeps_the_work_as_listed(self):
        tl = [tr("1", "Suite", "10:00"), tr("1a", "Part A", ""), tr("1b", "Part B", "4:00"), tr("2", "Next")]
        self.assertEqual([e["position"] for e in ma.one_track_movements(tl)], ["1", "1a", "1b", "2"])

    def test_TRK1_movements_without_lengths_still_merge(self):
        tl = [tr("7", "Symphony", "7:49"), tr("7.I", "One", ""), tr("7.II", "Two", ""), tr("8", "Next")]
        self.assertEqual([e["position"] for e in ma.one_track_movements(tl)], ["7", "8"])


if __name__ == "__main__":
    unittest.main()
