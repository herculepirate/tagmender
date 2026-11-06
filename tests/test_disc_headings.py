"""Discogs releases whose heading rows mark sides or parts inside a disc, not discs
(a 2-CD album listed "CD 1 - Album", "SIDE A", "SIDE B", "CD 2 - Instrumentals").

Run:  python3 tests/test_disc_headings.py   (offline; needs the Discogs plugin clone,
TAGMENDER_DISCOGS_PLUGIN or plugins_builtin/discogs)
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path

os.environ["XDG_CONFIG_HOME"] = tempfile.mkdtemp(prefix="tm-config-")
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp(prefix="tm-state-")
os.environ["TAGMENDER_TAGGER_INI"] = "/nonexistent/Picard.ini"
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import match_albums as ma  # noqa: E402

PLUGIN = os.environ.get("TAGMENDER_DISCOGS_PLUGIN", "plugins_builtin/discogs")


def heading(title):
    return {"type_": "heading", "position": "", "title": title, "duration": ""}


def track(pos, title, secs=200):
    return {"type_": "track", "position": str(pos), "title": title, "duration": f"{secs // 60}:{secs % 60:02d}"}


def reborn(formats=None):
    """Feared - Reborn as Discogs lists it: two CDs, the first split into two sides."""
    rows = [heading("CD 1 - Album"), heading("SIDE A")]
    rows += [track(i, f"Song {i}") for i in range(1, 7)]
    rows += [heading("SIDE B")] + [track(i, f"Song {i + 6}") for i in range(1, 7)]
    rows += [heading("CD 2 - Instrumentals")] + [track(i, f"Song {i} (Instrumental)") for i in range(1, 13)]
    return {"id": 10162832, "title": "Reborn", "artists": [{"name": "Feared"}], "year": 2016,
            "formats": formats or [{"name": "CD", "qty": "1", "descriptions": ["Album"]},
                                   {"name": "CD", "qty": "1"}],
            "tracklist": rows, "labels": [], "identifiers": []}


class DiscHeadings(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dm = ma.load_plugin(PLUGIN)

    def test_sides_inside_a_cd_stay_on_it(self):
        discs, count = ma.discogs_discs(reborn(), self.dm)
        self.assertEqual(count, 2)
        self.assertEqual([len(d["tracks"]) for d in discs], [12, 12])
        self.assertEqual([d["title"] for d in discs], ["CD 1 - Album", "CD 2 - Instrumentals"])

    def test_candidate_has_two_discs_of_twelve(self):
        c = ma.discogs_candidate(reborn(), self.dm)
        self.assertEqual(c["disc_count"], 2)
        self.assertEqual([t["disc"] for t in c["tracks"]], [1] * 12 + [2] * 12)

    def test_tags_number_through_each_disc(self):
        import engine
        _album, tracks, total = engine.discogs_parts(reborn(), self.dm)
        self.assertEqual(total, 2)
        self.assertEqual([(t["discnumber"], t["tracknumber"]) for t in tracks[10:14]],
                         [("1", "11"), ("1", "12"), ("2", "1"), ("2", "2")])
        self.assertEqual({t["totaldiscs"] for t in tracks}, {"2"})
        self.assertEqual({t["totaltracks"] for t in tracks}, {"12"})

    def test_disc_names(self):
        for title in ("CD 1", "CD2", "Disc 2", "Disc Two", "CD 2 - Instrumentals", "Bonus CD", "DVD"):
            self.assertTrue(ma._DISC_HEADING.match(title), title)
        for title in ("SIDE A", "Side B", "Part I", "Act 2", "Discography", "CDs"):
            self.assertFalse(ma._DISC_HEADING.match(title), title)

    def test_headings_matching_the_formats_are_unchanged(self):
        rel = reborn([{"name": "CD", "qty": "3"}])          # three discs: every heading is one
        discs, count = ma.discogs_discs(rel, self.dm)
        self.assertEqual((count, [len(d["tracks"]) for d in discs]), (3, [6, 6, 12]))

    def test_vinyl_sides_are_kept(self):
        rel = reborn([{"name": "Vinyl", "qty": "2", "descriptions": ["LP"]}])
        self.assertEqual(ma.discogs_discs(rel, self.dm),
                         (self.dm.parse_discs(rel), self.dm.release_disc_count(rel)))

    def test_headings_that_name_no_disc_keep_the_plugin_rule(self):
        rel = reborn()
        rel["tracklist"] = [r for r in rel["tracklist"] if not r["title"].startswith("CD ")]
        rel["tracklist"].insert(12, heading("Bonus"))
        self.assertEqual(ma.discogs_discs(rel, self.dm),
                         (self.dm.parse_discs(rel), self.dm.release_disc_count(rel)))


if __name__ == "__main__":
    unittest.main(verbosity=2)
