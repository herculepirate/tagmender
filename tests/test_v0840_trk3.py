# TRK-3 (docs/MATCHING_RULES.md): a Discogs tracklist numbered by its medium ("CD-1" ... "CD-11",
# "DVD") is one disc per medium, headings between being sections ("Bonus Track": Mogwai - Mr.
# Beast's track 11 was numbered 1 of a second disc); and the parts of a work listed with no
# position of its own carry their own ("2-3" ... "2-6": Spock's Beard - Live's suite was put on
# disc 1 as tracks 11-14). No network.
import os as _os, tempfile as _tempfile  # noqa: E401
_os.environ["XDG_CONFIG_HOME"] = _tempfile.mkdtemp(prefix="tm-config-")
_os.environ["XDG_STATE_HOME"] = _tempfile.mkdtemp(prefix="tm-state-")
_os.environ.setdefault("TAGMENDER_TAGGER_INI", "/nonexistent")

import sys  # noqa: E402
import unittest  # noqa: E402
from pathlib import Path  # noqa: E402

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "plugins_builtin" / "discogs"))
import discogs_mapping as dmap  # noqa: E402
import match_albums as ma  # noqa: E402


def tr(pos, title, dur="3:00", kind="track", **kw):
    return dict({"position": pos, "title": title, "duration": dur, "type_": kind}, **kw)


class TRK3(unittest.TestCase):
    def test_TRK3_medium_positions_are_one_disc_each(self):
        rel = {"formats": [{"name": "CD", "qty": "1"}, {"name": "DVD", "qty": "1"}],
               "tracklist": [tr(f"CD-{i}", f"T{i}") for i in range(1, 11)]
               + [tr("", "Bonus Track", "", "heading"), tr("CD-11", "1% Of Monster"),
                  tr("", "-", "", "heading"), tr("DVD", "The Recording", "40:32")]}
        discs = dmap.parse_discs(rel)
        self.assertEqual([(d["number"], len(d["tracks"])) for d in discs], [(1, 11), (2, 1)])
        self.assertEqual(discs[0]["tracks"][-1]["title"], "1% Of Monster")

    def test_TRK3_other_positions_are_read_as_before(self):
        sides = {"tracklist": [tr("A1", "a"), tr("A2", "b"), tr("B1", "c")]}
        self.assertEqual(len(dmap.parse_discs(sides)), 1)                     # (vinyl sides: one disc)
        two = {"tracklist": [tr("1-1", "a"), tr("1-2", "b"), tr("2-1", "c")]}
        self.assertEqual([len(d["tracks"]) for d in dmap.parse_discs(two)], [2, 1])
        self.assertIsNone(dmap._split_by_medium([tr("CD", "only"), tr("DVD", "film")]))   # (no numbers)

    def test_TRK3_a_works_parts_keep_their_own_positions(self):
        dm = ma.load_plugin(str(HERE / "plugins_builtin" / "discogs"))
        rel = {"id": 1, "title": "Live", "artists": [{"name": "Band"}], "year": 2008, "country": "US",
               "formats": [{"name": "CD", "qty": "2"}], "labels": [], "identifiers": [],
               "tracklist": [tr("1-1", "Intro"), tr("1-2", "Day"), tr("2-1", "Walking"), tr("2-2", "Hereafter"),
                             {"type_": "index", "position": "", "title": "As Far As The Mind Can See",
                              "duration": "", "sub_tracks": [tr(f"2-{k}", f"Part {k}", "4:00") for k in (3, 4, 5, 6)]},
                             tr("2-7", "Rearranged")]}
        tracks = ma.discogs_candidate(rel, dm)["tracks"]
        self.assertEqual([t["disc"] for t in tracks], [1, 1, 2, 2, 2, 2, 2, 2, 2])


class TRK3_Limits(unittest.TestCase):
    def test_TRK3_a_movements_number_is_no_disc(self):
        dm = ma.load_plugin(str(HERE / "plugins_builtin" / "discogs"))
        works = [{"type_": "index", "position": "", "title": f"Work {w}", "duration": "",
                  "sub_tracks": [tr(f"{w}.{k}", f"Mvt {k}", "4:00") for k in (1, 2)]} for w in (1, 3)]
        rel = {"id": 2, "title": "Works", "artists": [{"name": "X"}], "year": 2000, "country": "DE",
               "formats": [{"name": "CD", "qty": "1"}], "labels": [], "identifiers": [], "tracklist": works}
        self.assertEqual({t_["disc"] for t_ in ma.discogs_candidate(rel, dm)["tracks"]}, {1})   # (never "disc 3")

    def test_TRK3_numbers_restarting_are_two_discs(self):
        rel = {"formats": [{"name": "CD", "qty": "2"}],
               "tracklist": [tr("CD-1", "a"), tr("CD-2", "b"), tr("", "Disc 2", "", "heading"), tr("CD-1", "c"),
                             tr("CD-2", "d")]}
        self.assertIsNone(dmap._split_by_medium(rel["tracklist"]))
        self.assertEqual([len(d["tracks"]) for d in dmap.parse_discs(rel)], [2, 2])


if __name__ == "__main__":
    unittest.main(verbosity=2)
