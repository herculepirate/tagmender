#!/usr/bin/env python3
"""retag_approved.py: its checks stop a run whose plan is not what you approved."""
import csv
import json
import shutil
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import retag_approved as ra  # noqa: E402


class Checks(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        db = sqlite3.connect(self.tmp / "fixes.db")
        db.execute("CREATE TABLE results (path TEXT PRIMARY KEY, result TEXT)")
        for path, best in (("/l/AVKRVST/The Approbation", {"source": "mb", "id": "14da", "mb_id": "14da"}),
                           ("/l/Jesu/Terminus", {"source": "discogs", "id": "162", "mb_id": "52c3"}),
                           ("/l/King Diamond/Deadly Lullabyes", {"source": "discogs", "id": "2648846"})):
            db.execute("INSERT INTO results VALUES (?,?)", (path, json.dumps({"best": best})))
        db.commit()
        self.wanted = ra.approved_releases(self.tmp / "fixes.db")

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def plan(self, rows):
        p = self.tmp / "tag_plan.csv"
        with open(p, "w", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(["Album folder", "Files", "MusicBrainz release", "Matched as", "Confidence"])
            for folder, rel in rows:
                w.writerow([folder, 3, f"https://musicbrainz.org/release/{rel}" if rel else "", "x", 100])
        return p

    def web(self, rows):
        p = self.tmp / "web.csv"
        with open(p, "w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=["album", "source", "page", "matched", "confidence", "result"])
            w.writeheader()
            for folder, source in rows:
                w.writerow({"album": folder, "source": source, "page": "", "matched": "m", "confidence": "",
                            "result": ""})
        return p

    def test_the_picard_plan_must_be_the_approved_releases(self):
        ok = self.plan([("/l/AVKRVST/The Approbation", "14da"), ("/l/Jesu/Terminus", "52c3"), ("/l/X", "")])
        self.assertEqual(ra.check_tag_plan(ok, self.wanted),
                         (["/l/AVKRVST/The Approbation", "/l/Jesu/Terminus"], []))
        bad = self.plan([("/l/AVKRVST/The Approbation", "56af"), ("/l/Other/Album", "aaaa")])
        self.assertEqual(len(ra.check_tag_plan(bad, self.wanted)[1]), 2)

    def test_new_albums_only_the_sure_ones(self):
        db = sqlite3.connect(self.tmp / "fixes.db")
        db.execute("INSERT INTO results VALUES (?,?)", ("/l/Sepultura/The Mediator", json.dumps(
            {"status": "review", "best": {"source": "mb", "id": "6a70", "mb_id": "6a70"}})))
        db.execute("UPDATE results SET result=? WHERE path='/l/Jesu/Terminus'", (json.dumps(
            {"status": "auto", "best": {"source": "mb", "id": "52c3"}}),))
        db.commit()
        sure = ra.approved_releases(self.tmp / "fixes.db", sure_only=True)
        self.assertEqual(sorted(sure), ["/l/Jesu/Terminus"])     # the others carry no status

    def test_discogs_only_albums_must_come_from_discogs(self):
        """King Diamond - Deadly Lullabyes Live was about to be tagged from iTunes."""
        self.assertEqual(ra.check_web_plan(self.web([("/l/King Diamond/Deadly Lullabyes", "Discogs")]),
                                           self.wanted), (["/l/King Diamond/Deadly Lullabyes"], []))
        found, problems = ra.check_web_plan(self.web([("/l/King Diamond/Deadly Lullabyes", "iTunes")]),
                                            self.wanted)
        self.assertEqual(found, [])
        self.assertIn("iTunes", problems[0])


if __name__ == "__main__":
    unittest.main()
