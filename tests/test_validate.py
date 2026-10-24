# Tests for validate.py (blind re-identification of albums already tagged).
import os as _os, tempfile as _tempfile  # noqa: E402
_os.environ["XDG_CONFIG_HOME"] = _tempfile.mkdtemp(prefix="tm-config-")
_os.environ["XDG_STATE_HOME"] = _tempfile.mkdtemp(prefix="tm-state-")

import json
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent   # the scripts sit above tests/
sys.path.insert(0, str(HERE))
import match_albums as ma  # noqa: E402
import validate as va  # noqa: E402

MBID = "88d4d59b-d5da-4540-b095-6f51980f6f21"


def audio(p, **tags):
    p.parent.mkdir(parents=True, exist_ok=True)
    meta = sum((["-metadata", f"{k}={v}"] for k, v in tags.items()), [])
    subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
                    *meta, str(p), "-y"], check=True)


def rel(rid, country, catno, discogs=None):
    r = {"id": rid, "title": "Endorama", "country": country, "date": "2003-07-07",
         "artist-credit": [{"name": "Kreator", "artist": {"name": "Kreator", "id": "a"}}],
         "label-info": [{"label": {"name": "L"}, "catalog-number": catno}],
         "media": [{"format": "CD", "tracks": [{"title": "Golden Age", "length": 1000}]}]}
    if discogs:
        r["relations"] = [{"type": "discogs", "url": {"resource": f"https://www.discogs.com/release/{discogs}"}}]
    return r


class FakeHttp:
    def __init__(self, releases):
        self.releases = releases

    def get_json(self, url, host):
        for rid, r in self.releases.items():
            if f"/release/{rid}" in url or f"/releases/{rid}" in url:
                return r
        return None


class Validate(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_blind_reading_ignores_what_came_from_the_release(self):
        d = self.tmp / "lib" / "Kreator" / "1999 - Endorama"
        audio(d / "01.flac", artist="Kreator", album="Endorama", title="Golden Age", date="2003",
              CATALOGNUMBER="AMG 099", LABEL="Art Music", RELEASECOUNTRY="RU", BARCODE="4600317000999",
              ISRC="RUA010300001", MUSICBRAINZ_ALBUMID=MBID)
        seen = ma.read_album(d, "Kreator", "1999 - Endorama")
        self.assertTrue(seen["catnos"] and seen["barcodes"] and seen["tag_country"])
        blind = ma.read_album(d, "Kreator", "1999 - Endorama", blind=True)
        self.assertEqual((blind["catnos"], blind["barcodes"], blind["tag_country"], blind["tag_label"],
                          blind["isrcs"], blind["tag_mb_links"]), (set(), set(), "", "", [], set()))
        self.assertEqual(blind["year"], 1999)                 # the folder's year, not the pressing's
        self.assertEqual(blind["title"], "Endorama")
        # ... but the ORIGINAL folder name's notes still count
        orig = ma.read_album(d, "Kreator", "1999 - Endorama (2003 Art Music, AMG 099, Russia)", blind=True)
        self.assertIn(ma.mc.norm_code("AMG 099"), orig["catnos_from_folder"])

    def test_release_ids_are_read_from_the_tags(self):
        d = self.tmp / "lib" / "A" / "B"
        audio(d / "01.flac", MUSICBRAINZ_ALBUMID=MBID, DISCOGS_RELEASE_ID="1234567")
        audio(d / "02.mp3", MUSICBRAINZ_ALBUMID=MBID)
        self.assertEqual(va.file_ids(d), (MBID, "1234567"))
        audio(self.tmp / "lib" / "C" / "D" / "01.flac", title="untagged")
        self.assertEqual(va.file_ids(self.tmp / "lib" / "C" / "D"), ("", ""))

    def test_original_folder_names_come_from_the_journals(self):
        run = self.tmp / "run-1"
        run.mkdir()
        new = "/lib/Kreator/1999 - Endorama"
        (run / "journal.json").write_text(json.dumps([{
            "album": "/lib/Kreator/1999 - Endorama (2003 Art Music, AMG 099, Russia)", "mbid": MBID,
            "files": [{"path": "x", "new_path": f"{new}/01.flac"}]},
            {"album": "/lib/Feared/2015 - Synder [2CD]", "mbid": "m",
             "files": [{"path": "y", "new_path": "/lib/Feared/2015 - Synder/Disc 02/201.flac"}]}]))
        st = sqlite3.connect(":memory:")
        names = va.original_names(st, [run / "journal.json"])
        self.assertTrue(names[new].endswith("AMG 099, Russia)"))
        self.assertTrue(names["/lib/Feared/2015 - Synder"].endswith("[2CD]"))     # disc folder -> album
        self.assertEqual(va.original_names(st, [run / "journal.json"]), names)      # read once

    def test_verdicts(self):
        http = FakeHttp({MBID: rel(MBID, "RU", "AMG 099", discogs="777"),
                         "twin": rel("twin", "RU", "AMG 099"), "jp": rel("jp", "JP", "VICP-60704")})
        dm = types_dm()
        auto = lambda b: {"status": "auto", "best": b, "why": []}   # noqa: E731
        self.assertEqual(va.verdict(auto({"source": "mb", "id": MBID, "mb_id": MBID}), MBID, "", http, dm)[0],
                         "agree")
        self.assertEqual(va.verdict(auto({"source": "discogs", "id": "777", "mb_id": ""}), MBID, "777", http,
                                    dm)[0], "agree")
        # the same pressing listed twice on MusicBrainz
        self.assertEqual(va.verdict(auto({"source": "mb", "id": "twin", "mb_id": "twin"}), MBID, "", http, dm)[0],
                         "agree")
        self.assertEqual(va.verdict(auto({"source": "mb", "id": "jp", "mb_id": "jp"}), MBID, "", http, dm)[0],
                         "other pressing")                                  # same album, another pressing
        http.releases["other"] = dict(rel("other", "DE", "Q"), title="Violent Revolution")
        self.assertEqual(va.verdict(auto({"source": "mb", "id": "other", "mb_id": "other"}), MBID, "", http,
                                    dm)[0], "differs")
        r = va.verdict({"status": "review", "best": {"source": "mb", "id": MBID, "mb_id": MBID},
                        "why": ["runner-up x within 0.0 points"]}, MBID, "", http, dm)
        self.assertEqual(r, ("unsure (would pick yours)", "runner-up x within 0.0 points"))
        self.assertEqual(va.verdict({"status": "nomatch", "best": None, "why": ["no candidates found"]},
                                    MBID, "", http, dm)[0], "no match")


    def test_tags_holding_only_a_discogs_id_are_compared_through_discogs(self):
        # Albums tagged from Discogs alone carry DISCOGS_RELEASE_ID and no MusicBrainz ID; a pick
        # of another pressing of the same album was reported as "differs"
        dm = plugin_dm()
        if not dm:
            self.skipTest("Discogs Enrichment plugin not available")

        def dg(rid, country, catno, title="Endorama", n=1):
            return {"id": rid, "title": title, "country": country, "year": 2003, "released": "2003",
                    "artists": [{"name": "Kreator", "id": 1}],
                    "labels": [{"name": "Art Music", "catno": catno}], "formats": [{"name": "CD", "qty": "1"}],
                    "tracklist": [{"position": str(i + 1), "title": f"T{i}", "duration": "3:00", "type_": "track"}
                                  for i in range(n)]}
        http = FakeHttp({"555": dg(555, "Russia", "AMG 099"), "jp": dict(rel("jp", "JP", "VICP-60704")),
                         "666": dg(666, "Germany", "DRAKKAR 1", title="Violent Revolution", n=5)})
        http.releases["jp"]["media"][0]["tracks"] = [{"title": "T0", "length": 180000}]
        http.releases["ru"] = dict(rel("ru", "RU", "AMG 099"))
        http.releases["ru"]["media"][0]["tracks"] = [{"title": "T0", "length": 180000}]
        auto = lambda b: {"status": "auto", "best": b, "why": []}   # noqa: E731
        cache = {"dm": dm}
        # the same pressing on MusicBrainz
        self.assertEqual(va.verdict(auto({"source": "mb", "id": "ru", "mb_id": "ru"}), "", "555", http, cache)[0],
                         "agree")
        # the same album, another pressing
        self.assertEqual(va.verdict(auto({"source": "mb", "id": "jp", "mb_id": "jp"}), "", "555", http, cache)[0],
                         "other pressing")
        # another album
        self.assertEqual(va.verdict(auto({"source": "discogs", "id": "666"}), "", "555", http, cache)[0], "differs")


class Rechecks(unittest.TestCase):
    def test_disagreements_are_checked_again_after_a_fix(self):
        st = sqlite3.connect(":memory:")
        st.execute("CREATE TABLE validation (path TEXT PRIMARY KEY, row TEXT)")
        for path, verdict, ver in (("a", "agree", "v1"), ("b", "differs", "v1"), ("c", "unsure", "v2"),
                                   ("d", "not tagged", "v1"), ("e", "error", "v1")):
            st.execute("INSERT INTO validation VALUES (?,?)", (path, json.dumps({"verdict": verdict, "version": ver})))
        self.assertEqual(va.settled(st, "v2"), {"a", "c", "d"})
        self.assertEqual(va.settled(st, "v1"), {"a", "b", "d", "e"})


class Fixes(unittest.TestCase):
    def test_only_evidence_decided_disagreements_are_exported(self):
        st = sqlite3.connect(":memory:")
        st.execute("CREATE TABLE validation (path TEXT PRIMARY KEY, row TEXT)")
        st.execute("CREATE TABLE http_cache (url TEXT PRIMARY KEY, body TEXT, ts REAL)")
        rows = {
            "/l/A/by-scans": dict(verdict="other pressing", status="auto", picked="mb:de", picked_mb_twin="de",
                                  notes="pressing chosen by made in Germany on the scans (not on eu)"),
            "/l/A/by-country": dict(verdict="other pressing", status="auto", picked="mb:us", picked_mb_twin="us",
                                    notes="pressing chosen by your preferred country order (CA, US) puts USA first"),
            "/l/A/by-original": dict(verdict="other pressing", status="auto", picked="mb:o", picked_mb_twin="o",
                                     notes="pressing chosen by the label's original pressing, released first (L)"),
            "/l/A/agrees": dict(verdict="agree", status="auto", picked="mb:x", notes="pressing chosen by disc ID"),
            "/l/A/unsure": dict(verdict="unsure", status="review", picked="mb:y", notes="pressing chosen by disc ID"),
            "/l/A/old": dict(verdict="differs", status="auto", picked="mb:z", notes="catalogue number agrees",
                             version="old"),
        }
        for p_, r in rows.items():
            st.execute("INSERT INTO validation VALUES (?,?)",
                       (p_, json.dumps(dict({"folder": p_, "version": "v", "tags_mb": "t"}, **r))))
        tmp = Path(tempfile.mkdtemp())
        try:
            path, fixes = va.export_fixes(st, tmp, "v")
            self.assertEqual([f["folder"] for f in fixes], ["/l/A/by-scans"])
            db = sqlite3.connect(path)
            o = json.loads(db.execute("SELECT result FROM results").fetchone()[0])
            self.assertEqual((o["status"], o["best"]["mb_id"], o["version"]), ("auto", "de", "v"))
        finally:
            shutil.rmtree(tmp)


def types_dm():
    return {"dm": None}


def plugin_dm():
    d = _os.environ.get("TAGMENDER_DISCOGS_PLUGIN") or str(HERE.parent / "herculepirate" / "picard-discogs-enrichment")
    if not (Path(d) / "discogs_mapping.py").exists():
        return None
    sys.path.insert(0, d)
    import discogs_mapping
    return discogs_mapping


if __name__ == "__main__":
    unittest.main()
