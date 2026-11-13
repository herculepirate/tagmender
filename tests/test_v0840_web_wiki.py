# WEB-3 (docs/MATCHING_RULES.md): a Wikipedia track listing's links ("[[Night Stalkers (song)|Night
# Stalkers]]") are read as their text - before, the "|" inside the link ended the field and the
# title was written "[[Night Stalkers (song)" - and its bonus-track sections are other editions of
# the album, never discs 2 and 3 (Megadeth - The Sick, the Dying... and the Dead!). No network.
import os as _os, tempfile as _tempfile  # noqa: E401
_os.environ["XDG_CONFIG_HOME"] = _tempfile.mkdtemp(prefix="tm-config-")
_os.environ["XDG_STATE_HOME"] = _tempfile.mkdtemp(prefix="tm-state-")
_os.environ.setdefault("TAGMENDER_TAGGER_INI", "/nonexistent")

import sys  # noqa: E402
import unittest  # noqa: E402
from pathlib import Path  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import web_tag as w  # noqa: E402

TEXT = """{{Infobox album
| name = Quiet Record
| artist = Some Band
| released = 2022-09-02
| label = Some Label
}}
==Track listing==
{{Track listing
| title1 = Opening
| length1 = 5:04
| title2 = [[Night Stalkers (song)|Night Stalkers]]
| length2 = 6:38
| title3 = [[Soldier On!]]
| length3 = 4:54
| title4 = Célebutante{{efn|A note}}
| length4 = 3:51
}}
{{Track listing
| headline = Digital edition bonus tracks
| title5 = [[Police Truck]]
| length5 = 2:29
| title6 = This Planet's on Fire (Burn in Hell)
| length6 = 5:04
}}
{{Track listing
| headline = [[EMP Merchandising|EMP]] exclusive CD bonus tracks
| title5 = This Planet's on Fire
| length5 = 5:04
| title6 = [[The Conjuring (song)|The Conjuring]]
| length6 = 5:49
}}
"""


class Http:
    def get_json(self, url, host):
        return {"parse": {"title": "Quiet Record", "wikitext": {"*": TEXT}}}


class WEB3(unittest.TestCase):
    def test_WEB3_links_are_their_text(self):
        std = w.wikipedia_album(Http(), "Quiet Record")
        self.assertEqual([t["title"] for t in std["tracks"]], ["Opening", "Night Stalkers", "Soldier On!", "Célebutante"])
        self.assertEqual([t["secs"] for t in std["tracks"]][:2], [304.0, 398.0])

    def test_WEB3_bonus_sections_are_other_editions_not_discs(self):
        eds = w.wikipedia_editions(Http(), "Quiet Record")
        self.assertEqual([len(e["tracks"]) for e in eds], [4, 6, 6])
        self.assertEqual({t["disc"] for e in eds for t in e["tracks"]}, {1})
        self.assertEqual([t["title"] for t in eds[2]["tracks"]][-2:], ["This Planet's on Fire", "The Conjuring"])
        self.assertEqual(len({e["id"] for e in eds}), 3)                       # (each its own candidate)

    def test_WEB3_an_lp_side_two_runs_on(self):
        B = lambda ns, h="": (ns[0], [(n, f"T{n}", 60.0) for n in ns], h)      # noqa: E731
        self.assertEqual([len(e) for e in w._wiki_editions([B(range(1, 6), "Side one"), B(range(6, 11), "Side two")])],
                         [10])
        self.assertEqual([len(e) for e in w._wiki_editions([B(range(1, 6)), B(range(6, 11))])], [10])   # (no headline)
        two = [B(range(1, 13)), (13, [(13, "A", 1.0), (14, "B", 1.0)], "Digital edition bonus tracks"),
               (13, [(13, "C", 1.0), (14, "D", 1.0)], "Exclusive CD bonus tracks")]
        self.assertEqual([len(e) for e in w._wiki_editions(two)], [12, 14, 14])

    def test_WEB3_templates_keep_their_text(self):
        self.assertEqual(w._unwrap_templates("{{nowrap|a {{sup|b}}}}"), "a b")
        self.assertEqual(w._unwrap_templates("{{lang|fr|Titre}}"), "Titre")
        self.assertEqual(w._unwrap_templates("Song{{efn|A note|name=n1}}"), "Song")
        self.assertEqual(w._unwrap_templates("Song{{cite web|url=x|title=y}}"), "Song")

    def test_WEB3_editions_from_blocks(self):
        B = lambda ns, h="Bonus tracks": (ns[0], [(n, f"T{n}", 60.0) for n in ns], h)   # noqa: E731
        self.assertEqual([len(e) for e in w._wiki_editions([B(range(1, 13), ""), B([13, 14]), B([15, 16])])], [12, 14, 16])
        two = w._wiki_editions([B(range(1, 9), "Disc one"), B(range(1, 7), "Disc two")])   # (a real 2-disc set)
        self.assertEqual((len(two), len(two[0]), two[0][-1][0]), (1, 14, 2))


if __name__ == "__main__":
    unittest.main(verbosity=2)
