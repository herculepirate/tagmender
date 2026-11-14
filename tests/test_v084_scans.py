# What a plan album reports about its scans and how its release was found
# (identification "scans" / "via"), and the watcher keeping it for its Recently list.
# Reporting only: these tests also check the identification itself is what it was.
import os as _os, tempfile as _tempfile  # noqa: E402
_os.environ["XDG_CONFIG_HOME"] = _tempfile.mkdtemp(prefix="tm-config-")
_os.environ["XDG_STATE_HOME"] = _tempfile.mkdtemp(prefix="tm-state-")

import json
import sqlite3
import sys
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_engine import Base, FakeHttp, mb_release  # noqa: E402

import match_albums as ma  # noqa: E402
import plan as pl  # noqa: E402
import profiles as pf  # noqa: E402
import watcher  # noqa: E402
import exclusions as ex  # noqa: E402

CATNO = "ABC1234"            # distinctive: letters and digits, 5+ characters


def release(rid="mb-s", **kw):
    r = mb_release(rid, "Band S", "Album S", catno=CATNO, **kw)
    r["barcode"] = ""                                   # (the scans' barcode is the test's)
    return r


class LinkHttp(FakeHttp):
    """FakeHttp whose MusicBrainz /url lookup ties the files' Bandcamp page to LINKED."""
    linked = ""

    def get_json(self, url, host):
        if host == "mb" and "/ws/2/url?resource=" in url:
            self.calls.append((host, url))
            return {"relations": [{"release": {"id": self.linked}}]} if "bandcamp.com" in url else {}
        return super().get_json(url, host)


class Scans(Base):
    def setUp(self):
        super().setUp()
        self.text, self.decoded = "", set()
        self.patches = [mock.patch.object(ma, "ocr_problems", lambda: []),
                        mock.patch.object(ma, "ocr_text", lambda paths: self.text),
                        mock.patch.object(ma, "ocr_spines", lambda paths: ""),
                        mock.patch.object(ma, "ocr_barcode_digits", lambda paths, *a, **k: ""),
                        mock.patch.object(ma, "ocr_barcodes", lambda paths, *a, **k: set(self.decoded)),
                        mock.patch.object(ma, "ocr_disc_rings", lambda paths, *a, **k: ""),
                        mock.patch.object(ma, "ocr_deep", lambda paths, *a, **k: ""),
                        mock.patch.object(ma, "web_release_links", lambda *a, **k: ([], [])),
                        mock.patch.object(ma, "fingerprint_ready", lambda: False),
                        mock.patch.object(ma, "CONCURRENT_SCANS", False)]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        super().tearDown()

    def run_album(self, scans=True, http=None, **tags):
        d = self.album("Band S", "Album S", **tags)
        if scans:
            (d / "back.jpg").write_bytes(b"\xff\xd8\xff\xe0 not really a picture")
        eng = self.engine(http or FakeHttp(mb=[release()]), ocr="auto")
        eng.use_discogs = False
        return eng.identify(eng.albums_in(d)[0]), eng

    def ident(self, prop):
        """identification as the plan carries it."""
        plan = pl.build([prop], pf.from_dict({"name": "default", "operation": "in_place"}))
        return plan["albums"][0]["identification"]

    def test_catalogue_number_read_on_the_scans_proves(self):
        self.text = f"Label MB  {CATNO}  Made in Germany"
        prop, _ = self.run_album()
        self.assertEqual((prop.status, prop.tier, prop.release_id), ("identified", "exact", "mb-s"), prop.why)
        ident = self.ident(prop)
        self.assertEqual(ident["scans"]["state"], "proven", ident["scans"])
        self.assertIn("catalogue_number", ident["scans"]["found"])
        self.assertEqual(ident["scans"]["against"], [])
        self.assertEqual(ident["via"], "")                     # the plain search found it

    def test_scans_naming_another_country_differ(self):
        self.text = "Made in Japan"
        prop, _ = self.run_album()
        ident = self.ident(prop)
        self.assertEqual(ident["scans"]["state"], "differs", ident["scans"])
        self.assertIn("country", ident["scans"]["against"])

    def test_a_barcode_decoded_off_the_scans_not_on_the_release_differs(self):
        self.decoded = {"4006381333931"}
        prop, _ = self.run_album(http=FakeHttp(mb=[dict(release(), barcode="5012345678900")]))
        ident = self.ident(prop)
        self.assertEqual(ident["scans"]["state"], "differs", ident["scans"])
        self.assertIn("barcode", ident["scans"]["against"])

    def test_a_release_that_lists_no_barcode_does_not_differ_from_the_scans(self):
        self.decoded = {"4006381333931"}
        prop, _ = self.run_album()
        ident = self.ident(prop)
        self.assertEqual(ident["scans"]["state"], "unread", ident["scans"])
        self.assertEqual(ident["scans"]["against"], [])

    def test_scans_with_nothing_read_are_unread(self):
        self.text = ""
        prop, _ = self.run_album()
        self.assertEqual((prop.status, prop.release_id), ("identified", "mb-s"), prop.why)
        ident = self.ident(prop)
        scans = dict(ident["scans"])
        cov = scans.pop("coverage")                    # (SCN-1: the counts of what was read, told too)
        self.assertEqual(scans, {"state": "unread", "found": [], "against": []})
        self.assertEqual((cov["found"], cov["read"], cov["complete"]), (1, 1, True))

    def test_a_folder_without_scans_reports_none(self):
        prop, _ = self.run_album(scans=False)
        ident = self.ident(prop)
        self.assertNotIn("scans", ident)
        self.assertEqual(ident.get("via"), "")

    def test_the_files_shop_link_found_it(self):
        http = LinkHttp(mb=[release("mb-l")])
        http.linked = "mb-l"
        prop, _ = self.run_album(scans=False, http=http, comment="https://bands.bandcamp.com/album/album-s")
        self.assertEqual((prop.status, prop.release_id), ("identified", "mb-l"), prop.why)
        self.assertEqual(self.ident(prop)["via"], "link")


class Reports(unittest.TestCase):
    """scans_report / found_via on their own: what each clue counts as."""
    CAND = {"source": "mb", "id": "x", "barcodes": {"4006381333931"}, "catnos": {"abc1234"}}

    def best(self, **kw):
        return dict({"cand": self.CAND, "code": "", "clues": {}, "clues_against": []}, **kw)

    def test_states(self):
        album = {"ocr_barcodes": {"4006381333931"}}
        self.assertEqual(ma.scans_report(self.best(), album, "", (), True)["state"], "proven")
        self.assertEqual(ma.scans_report(self.best(clues={"label": "label X on the scans"}), {}, "x", (), True),
                         {"state": "agrees", "found": ["label"], "against": []})
        self.assertEqual(ma.scans_report(self.best(), {}, "", (), True)["state"], "unread")
        self.assertIsNone(ma.scans_report(self.best(), album, "", (), False))
        other = {"scored": [{"cand": {"source": "mb", "id": "y", "catnos": {"zzz9876"}}}]}
        r = ma.scans_report(self.best(), {}, "cat no ZZZ9876", (other,), True)
        self.assertEqual((r["state"], r["against"]), ("differs", ["catalogue_number"]))

    def test_via(self):
        self.assertEqual(ma.found_via(self.best(clues={"disc TOC": "x"}), {}), "disc_id")
        self.assertEqual(ma.found_via(self.best(), {"found_via": {"mb:x": "scan_search"}}), "scan_search")
        self.assertEqual(ma.found_via(self.best(), {}), "")


class WatcherMatch(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(_tempfile.mkdtemp())
        self.saved = ex.STATE_DIR
        ex.STATE_DIR = self.tmp / "state"
        ex.STATE_DIR.mkdir(parents=True)

    def tearDown(self):
        ex.STATE_DIR = self.saved

    def test_an_old_database_keeps_working_and_recent_has_six_values(self):
        db = sqlite3.connect(ex.STATE_DIR / "state.db")
        db.execute("CREATE TABLE processed (folder TEXT PRIMARY KEY, profile TEXT, signature TEXT,"
                   " status TEXT, attempts INTEGER, next_try REAL, ts REAL, detail TEXT)")
        db.execute("INSERT INTO processed VALUES (?,?,?,?,?,?,?,?)",
                   ("/music/Old", "default", "sig", "done", 1, 0, time.time() - 10, "Old - Album"))
        db.commit()
        db.close()
        rows = watcher.recent(10)
        self.assertEqual(rows, [("/music/Old", "default", "done", rows[0][3], "Old - Album", {})])
        # recorded now, with what identification said
        folder = self.tmp / "New"
        folder.mkdir()
        w = watcher.Watcher([pf.from_dict({"name": "default"})], once=True)
        entry = {"identification": {"tier": "exact", "confidence": 97.5, "provider": "musicbrainz",
                                    "scans": {"state": "proven", "found": ["barcode"], "against": []},
                                    "via": "disc_id"}}
        db = watcher._db()
        w.record(db, folder, w.profiles[0], "planned", 1, "New - Album", match=watcher.match_of(entry))
        db.close()
        rows = watcher.recent(10)
        self.assertEqual(len(rows[0]), 6)
        self.assertEqual(rows[0][5], {"tier": "exact", "confidence": 97.5, "provider": "musicbrainz",
                                      "scans": {"state": "proven", "found": ["barcode"], "against": []},
                                      "via": "disc_id"})
        self.assertEqual(rows[1][5], {})
        # the executor's undo record (eight values, by position) still goes in, and hides the old match
        db = sqlite3.connect(ex.STATE_DIR / "state.db")
        db.execute("INSERT OR REPLACE INTO processed VALUES (?,?,?,?,?,?,?,?)",
                   (pl.db_text(folder), "default", "sig", "undone", 0, 0, time.time() + 5, ""))
        db.commit()
        db.close()
        top = watcher.recent(10)[0]
        self.assertEqual((top[2], top[5]), ("undone", {}))

    def test_match_of(self):
        self.assertIsNone(watcher.match_of({"identification": {"status": "listed"}}))
        m = watcher.match_of({"identification": {"tier": "unresolved", "confidence": 0, "provider": ""}})
        self.assertEqual(m, {"tier": "unresolved", "confidence": 0.0, "provider": "", "scans": None, "via": ""})
        json.dumps(m)


if __name__ == "__main__":
    unittest.main()
