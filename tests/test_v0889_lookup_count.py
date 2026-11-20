# 0.8.89: during a look-up the toolbar's bar and count say how many albums are DONE ("1 of 4 done"),
# never how many have started - with several looked up at once, "3 of 4" was shown while three rows
# still said "Looking up…" and the fourth "Waiting…" (Koki Nakano, four albums).
import os
import sys
import types
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from gui.overview import Overview  # noqa: E402


class Bar:
    def __init__(self):
        self.max = self.value = None

    def setMaximum(self, v):
        self.max = v

    def setValue(self, v):
        self.value = v


def page(states):
    said = []
    p = types.SimpleNamespace(_lookup_order=list(states), states=dict(states), bar=Bar(), _lookup_n=len(states),
                              win=types.SimpleNamespace(show_count=said.append), looked_with="MusicBrainz and Discogs")
    return p, said


class LookUpCount(unittest.TestCase):
    def test_started_albums_are_not_counted_as_done(self):
        p, said = page({"a": "looking", "b": "looking", "c": "looking", "d": "waiting"})
        Overview.lookup_count(p, 4)
        self.assertEqual((said[-1], p.bar.value, p.bar.max), ("0 of 4 done", 0, 4))

    def test_a_finished_album_counts_at_once(self):
        p, said = page({"a": "looking", "b": "looking", "c": "looking", "d": "waiting"})
        p.states.pop("a")                                  # (looked_up_one: its result landed)
        Overview.lookup_count(p)
        self.assertEqual((said[-1], p.bar.value), ("1 of 4 done", 1))

    def test_the_line_under_the_list_says_how_many_are_done(self):
        p, _said = page({"a": "looking", "b": "waiting"})
        p.states.pop("a")
        words = Overview.lookup_words(p, 1, "Album B")
        self.assertIn("Looking up 2 albums with MusicBrainz and Discogs, 1 done", words)
        self.assertNotIn("2 of 2", words)


class GuessedRow(unittest.TestCase):
    def test_a_guessed_pressing_says_so_in_its_row(self):
        from gui.common import label_for
        a = {"status": "ready", "identification": {"tier": "exact", "guessed": {"ref": "discogs:757872"}}}
        self.assertEqual(label_for(a)[1], "Ready · Pressing guessed")
        self.assertEqual(label_for({"status": "ready", "identification": {"tier": "exact"}})[1], "Ready")


if __name__ == "__main__":
    unittest.main()
