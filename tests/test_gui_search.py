# The search sheet, Other versions and the pressing chooser (gui/search.py, gui/versions.py),
# offscreen with stubbed HTTP: rows from MusicBrainz and Discogs answers, each version scored
# against generated files by match_core.score_candidate, the tied pressings and the details
# that differ, and "Use This Release" going through the forced-release flow (page.forced, then a
# preview) - nothing is written. No network: an unknown URL fails the test (FakeHttp records it,
# tearDown checks, since the code under test turns lookup errors into notes), and a sheet whose
# worker failed fails it too.
# Patch sites: gui.versions.make_lookup (the lookup a sheet's worker uses).
import os as _os, tempfile as _tempfile  # noqa: E402
_os.environ["XDG_CONFIG_HOME"] = _tempfile.mkdtemp(prefix="tm-config-")
_os.environ["XDG_STATE_HOME"] = _tempfile.mkdtemp(prefix="tm-state-")
_os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
_os.environ.setdefault("TAGMENDER_TAGGER_INI", "/nonexistent")

import shutil
import sys
import tempfile
import time
import unittest
import urllib.parse as up
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "tests"))
PLUGIN = Path(sys.argv[1] if len(sys.argv) > 1 and not sys.argv[1].startswith("-")
              else Path("plugins_builtin/discogs")).expanduser()
try:
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication, QComboBox, QMainWindow
except ImportError:                                           # GUI is optional
    QApplication = None

import match_albums as ma  # noqa: E402
import match_core as mc  # noqa: E402

RG, MASTER = "rg-alpha", "500"
IDS = ("0a8e97fd-4f6a-4bd7-8e4b-6d3a0b2ad2f1", "1b9f08ae-5a7b-4ce8-9f5c-7e4b1c3be3a2",
       "2c0f19bf-6b7c-4df9-a05d-8f5c2d4cf4b3")
SECS = (10, 20, 30)


def mb_rel(rid, barcode, country, catno, lens=SECS, title="Alpha"):
    return {"id": rid, "title": title, "date": "2005-03-01", "country": country, "status": "Official",
            "barcode": barcode,
            "artist-credit": [{"name": "Band", "joinphrase": "", "artist": {"id": "a-1", "name": "Band",
                                                                            "sort-name": "Band"}}],
            "label-info": [{"label": {"name": "Label MB"}, "catalog-number": catno}],
            "media": [{"format": "CD", "position": 1, "track-count": len(lens), "tracks": [
                {"id": f"{rid}-t{i}", "position": i + 1, "title": f"T{i}", "length": s * 1000,
                 "recording": {"id": f"{rid}-r{i}", "title": f"T{i}"}} for i, s in enumerate(lens)]}]}


def dg_rel(rid, lens=SECS, barcode="5013929020160"):
    return {"id": rid, "title": "Alpha", "year": 2005, "released": "2005-03-01", "master_id": int(MASTER),
            "uri": f"https://www.discogs.com/release/{rid}", "artists": [{"name": "Band", "id": 77}],
            "labels": [{"name": "Label DG", "catno": "DG-1"}],
            "identifiers": [{"type": "Barcode", "value": barcode}], "country": "UK",
            "formats": [{"name": "CD", "qty": "1", "descriptions": ["Album"]}],
            "tracklist": [{"type_": "track", "position": str(i + 1), "title": f"T{i}", "duration": f"0:{s:02d}"}
                          for i, s in enumerate(lens)]}


MB = [mb_rel(IDS[0], "0724384260927", "US", "CDP 7 1"), mb_rel(IDS[1], "5013929020153", "GB", "CDP 7 2"),
      mb_rel(IDS[2], "0000000000000", "JP", "TOCP-1", lens=(10, 20, 30, 40))]
DG = {701: dg_rel(701), 702: dg_rel(702, lens=(10, 20, 30, 40), barcode="111")}


class FakeHttp:
    """MusicBrainz and Discogs answers by URL; anything else is recorded in `unexpected` (the
    test fails in tearDown) and raised (no network). mb: the release group's releases;
    page_cap: the most releases a browse page sends back (MusicBrainz's track budget);
    count: the release-count it claims; links: MusicBrainz release -> linked Discogs release;
    boom: a word that makes a search fail with an error the code does not expect."""

    def __init__(self, down=(), mb=None, page_cap=None, count=None, links=None, boom=None):
        self.calls, self.down, self.unexpected = [], set(down), []
        self.mb, self.page_cap, self.count, self.links, self.boom = list(mb or MB), page_cap, count, links or {}, boom

    def get_json(self, url, host):
        self.calls.append((host, url))
        if host in self.down:
            raise ma.ProviderUnavailable(host, "outage", "HTTP 503")
        if self.boom and self.boom in url:
            raise RuntimeError("the stub broke on purpose")
        MB = self.mb
        if host == "mb":
            if "/ws/2/release/?query=" in url:
                return {"releases": [{k: v for k, v in r.items() if k != "media"} | {
                    "media": [{"format": "CD", "track-count": len(r["media"][0]["tracks"])}],
                    "track-count": len(r["media"][0]["tracks"]),
                    "release-group": {"id": RG}} for r in MB]}
            if "/ws/2/recording/?query=" in url:
                return {"recordings": [{"id": "rec-1", "title": "T0", "length": 10000,
                                        "artist-credit": [{"name": "Band", "joinphrase": ""}],
                                        "releases": [{"id": IDS[0], "title": "Alpha", "country": "US",
                                                      "media": [{"format": "CD", "track-count": 3}]}]}]}
            if "/ws/2/artist/?query=" in url:
                return {"artists": [{"id": "a-1", "name": "Band", "sort-name": "Band", "type": "Group",
                                     "country": "DE", "life-span": {"begin": "1990"}}]}
            if f"release?release-group={RG}" in url:
                qs = up.parse_qs(url.split("?", 1)[1])
                limit, offset = int(qs["limit"][0]), int(qs.get("offset", ["0"])[0])
                limit = min(limit, self.page_cap or limit)
                return {"release-count": self.count or len(MB), "release-offset": offset,
                        "releases": MB[offset:offset + limit]}
            if "/ws/2/release/" in url:
                rid = url.split("/release/")[1].split("?")[0]
                rel = next((dict(r, **{"release-group": {"id": RG}}) for r in MB if r["id"] == rid), None)
                if rel and rid in self.links:
                    rel["relations"] = [{"target-type": "url", "type": "discogs", "url": {
                        "resource": f"https://www.discogs.com/release/{self.links[rid]}"}}]
                return rel
        if host == "discogs":
            if "/database/search" in url:
                assert "q=" in up.unquote(url)
                return {"results": [{"id": 701, "title": "Band - Alpha", "year": "2005", "country": "UK",
                                     "label": ["Label DG"], "catno": "DG-1", "barcode": ["5013929020160"],
                                     "format": ["CD", "Album"], "master_id": int(MASTER)}]}
            if f"/masters/{MASTER}/versions" in url:
                return {"pagination": {"pages": 1}, "versions": [
                    {"id": i, "title": "Alpha", "format": "CD, Album", "label": "Label DG", "country": "UK",
                     "released": "2005", "catno": "DG-1"} for i in DG]}
            tail = url.rsplit("/", 1)[1]
            if "/releases/" in url and tail.isdigit():
                return DG.get(int(tail))
        self.unexpected.append((host, url))
        raise AssertionError(f"unexpected request: {host} {url}")


def settle(app, n=5):
    for _ in range(n):
        app.processEvents()
        time.sleep(0.005)


def wait(app, dlg, timeout=30):
    from gui.work import _alive
    end = time.time() + timeout
    while time.time() < end and any(_alive(t) for t, _w in getattr(dlg, "_threads", [])):
        app.processEvents()
        time.sleep(0.01)
    settle(app)


@unittest.skipIf(QApplication is None, "PySide6 not installed")
class SearchAndVersions(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not (PLUGIN / "discogs_mapping.py").exists():
            raise unittest.SkipTest(f"Discogs Enrichment not found at {PLUGIN}")
        if not shutil.which("ffmpeg"):
            raise unittest.SkipTest("ffmpeg not installed")
        from _review14b_common import make
        import gui  # noqa: F401
        cls.app = QApplication.instance() or QApplication([])
        cls.dm = ma.load_plugin(str(PLUGIN))
        cls.tmp = Path(tempfile.mkdtemp(prefix="tm-search-"))
        cls.folder = cls.tmp / "Band" / "Alpha"
        cls.files = [str(make(cls.folder / f"{i + 1:02d}.flac", 300 + 50 * i, s,
                              {"title": f"T{i}", "album": "Alpha", "albumartist": "Band", "artist": "Band",
                               "track": str(i + 1)})) for i, s in enumerate(SECS)]

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def setUp(self):
        from gui import search, versions
        self.versions = versions
        self.http = FakeHttp()
        self.lookup = versions.Lookup(self.http, dict(mc.DEFAULT_CFG), self.dm)
        self.patch = mock.patch.object(versions, "make_lookup", side_effect=lambda *_a: self.lookup)
        self.patch.start()
        self.failures = []                   # (a sheet's worker that failed: the test fails)
        real_failed = search._Base.failed

        def failed(dlg, msg):
            self.failures.append(msg)
            real_failed(dlg, msg)
        self.failed_patch = mock.patch.object(search._Base, "failed", failed)
        self.failed_patch.start()
        self.page = Page(self.album())
        self.page.show()
        self.sheets = []

    def tearDown(self):
        for d in self.sheets:
            wait(self.app, d)
            self.assertFalse(d.status.text().startswith(("The lookup did not finish", "MusicBrainz could not",
                                                         "Discogs could not")), d.status.text())
            d.close()
        self.patch.stop()
        self.failed_patch.stop()
        self.assertEqual(self.failures, [])
        self.assertEqual(self.http.unexpected, [])
        self.page.close()
        self.page.deleteLater()              # (on this thread: never left to Python's GC on a worker)
        settle(self.app)
        from PySide6.QtCore import QEvent
        self.app.sendPostedEvents(None, int(QEvent.Type.DeferredDelete))

    def album(self, tier="album_only"):
        diff = [{"field": "musicbrainz_releasegroupid", "before": [], "after": [RG], "action": "added"},
                {"field": "discogs_master_id", "before": [], "after": [MASTER], "action": "added"}]
        return {"source_dir": str(self.folder), "status": "ready", "album_files": list(self.files),
                "files": [{"src": f, "dst": f, "op": "tag", "diff": diff} for f in self.files],
                "identification": {"tier": tier, "provider": "musicbrainz", "release_id": "",
                                   "why": ["pressing unknown (2 pressings fit equally)"]},
                "release": {"album": ["Alpha"], "albumartist": ["Band"]}}

    def opened(self, dlg):
        self.sheets.append(dlg)
        wait(self.app, dlg)
        return dlg

    def rows(self, dlg):
        return [dlg.table._proxy.data(dlg.table._proxy.index(r, 0), Qt.UserRole)
                for r in range(dlg.table._proxy.rowCount())]

    # ---- gui.versions, without Qt -------------------------------------------
    def test_search_rows_from_each_source(self):
        v = self.versions
        rel = v.search(self.lookup, "release", "Band Alpha")
        self.assertEqual([r["id"] for r in rel["rows"]], list(IDS))
        first = rel["rows"][0]
        self.assertEqual((first["title"], first["artist"], first["country"], first["label"], first["catno"],
                          first["barcode"], first["format"], first["tracks"]),
                         ("Alpha", "Band", "US", "Label MB", "CDP 7 1", "0724384260927", "CD", 3))
        dg = v.search(self.lookup, "discogs", "Band Alpha")["rows"][0]
        self.assertEqual((dg["ref"], dg["artist"], dg["title"], dg["barcode"], dg["format"]),
                         ("discogs:701", "Band", "Alpha", "5013929020160", "CD; Album"))
        rec = v.search(self.lookup, "recording", "T0")["rows"][0]
        self.assertEqual((rec["recording"], rec["length"], rec["ref"]), ("T0", "0:10", IDS[0]))
        art = v.search(self.lookup, "artist", "Band")["rows"][0]
        self.assertEqual((art["name"], art["years"]), ("Band", "1990"))
        for host, url in self.http.calls:            # (a search goes through the matcher's Http only)
            self.assertIn(host, ("mb", "discogs"))

    def test_search_says_why_nothing_came(self):
        v = self.versions
        self.assertIn("Type", v.search(self.lookup, "release", "  ")["note"])
        down = v.Lookup(FakeHttp(down={"mb"}), dict(mc.DEFAULT_CFG), self.dm)
        res = v.search(down, "release", "Alpha")
        self.assertEqual(res["rows"], [])
        self.assertIn("MusicBrainz did not answer", res["note"])
        off = v.Lookup(self.http, dict(mc.DEFAULT_CFG), None, use_discogs=True)
        self.assertIn("Discogs is off", v.search(off, "discogs", "Alpha")["note"])

    def test_versions_scored_against_the_files(self):
        v = self.versions
        res = v.versions(self.lookup, self.album())
        self.assertEqual(res["anchors"]["group"], RG)
        self.assertEqual(res["anchors"]["master"], MASTER)
        self.assertEqual(res["n_files"], 3)
        by = {r["id"]: r for r in res["rows"]}
        self.assertEqual(set(by), set(IDS) | {"701", "702"})
        self.assertIn("same track count", by[IDS[0]]["fit"])
        self.assertIn("every length agrees", by[IDS[0]]["fit"])
        self.assertIn("4 tracks, you have 3", by[IDS[2]]["fit"])
        self.assertGreater(by[IDS[0]]["score"], by[IDS[2]]["score"])
        self.assertEqual(res["rows"][0]["score"], max(r["score"] for r in res["rows"]))   # (best fit first)
        self.assertEqual(by["701"]["tracks"], 3)
        self.assertEqual(by["701"]["barcode"], "5013929020160")

    def test_tied_pressings_and_what_to_check(self):
        v = self.versions
        rows = v.versions(self.lookup, self.album())["rows"]
        tied = v.tied(rows, mc.DEFAULT_CFG)
        # (Discogs 701 fits as well, but album_only ties within the best one's source)
        self.assertEqual({r["id"] for r in tied}, {IDS[0], IDS[1]})
        diff = v.differing(tied)
        self.assertEqual(diff[0], "barcode")
        self.assertIn("country", diff)
        self.assertNotIn("format", diff)                      # (every one is a CD)
        h = v.hint(tied)
        self.assertEqual(h, "Check the barcode on your case: 0724384260927 vs 5013929020153.")
        same = [dict(tied[0]), dict(tied[0])]
        self.assertIn("same details", v.hint(same))

    def row(self, **kw):
        base = {"source": "mb", "id": "x", "ref": "x", "title": "Alpha", "artist": "Band", "date": "2005-03-01",
                "country": "GB", "label": "L", "catno": "C-1", "barcode": "1", "format": "CD", "tracks": 3,
                "score": 100.0, "n_tracks": 3, "formats": ["cd"], "year_ok": False, "pressing_year": None}
        return dict(base, **kw)

    def test_tied_as_album_only_ties_them(self):
        v, cfg = self.versions, mc.DEFAULT_CFG
        a, b = self.row(id="a"), self.row(id="b", barcode="2")
        # the same score and title but another track count: not the same pressing
        self.assertEqual([r["id"] for r in v.tied([a, b, self.row(id="c", n_tracks=4)], cfg)], ["a", "b"])
        # the best one's source only
        self.assertEqual([r["id"] for r in v.tied([a, self.row(id="d", source="discogs"), b], cfg)], ["a", "b"])
        # the edition the folder names, when the best one is of it
        y = [self.row(id="a", year_ok=True, pressing_year=2005), self.row(id="b", pressing_year=2005),
             self.row(id="e", year_ok=True, pressing_year=2005)]
        self.assertEqual([r["id"] for r in v.tied(y, cfg)], ["a", "e"])
        # what the files were made from: a vinyl pressing is not a CD rip's
        self.assertEqual([r["id"] for r in v.tied([a, b, self.row(id="f", formats=["vinyl"])], cfg, "cd")],
                         ["a", "b"])
        self.assertEqual(v.tied([a, b], cfg, "vinyl"), [])
        # within the profile's margin of the best one, and with the same title
        m = float(cfg["auto_min_margin"])
        self.assertEqual([r["id"] for r in v.tied([a, b, self.row(id="g", score=100 - m),
                                                   self.row(id="h", score=100 - m + 0.5)], cfg)], ["a", "b", "h"])
        self.assertEqual([r["id"] for r in v.tied([a, b, self.row(id="i", title="Omega")], cfg)], ["a", "b"])

    def test_one_pressing_on_both_sources_does_not_differ(self):
        v = self.versions
        mb = self.row(barcode="0724384260927", country="GB", date="2005-03-01", catno="CDP 7 1")
        dg = self.row(source="discogs", barcode="7 24384 26092 7", country="UK", date="2005", catno="CDP-7-1")
        self.assertEqual(v.differing([mb, dg]), [])
        self.assertIn("same details", v.hint([mb, dg]))
        other = self.row(source="discogs", barcode="5013929020153", country="US", date="2006")
        self.assertEqual(v.differing([mb, dg, other])[:1], ["barcode"])
        self.assertEqual(v.hint([mb, dg, other]),
                         "Check the barcode on your case: 0724384260927 vs 5013929020153.")

    def test_anchors_from_the_ids_already_in_the_files(self):
        from _review14b_common import make
        v = self.versions
        for name, meta in (("Held", {"MUSICBRAINZ_RELEASEGROUPID": RG, "DISCOGS_MASTER_ID": MASTER}),
                           ("Release", {"MUSICBRAINZ_ALBUMID": IDS[1]})):
            folder = self.tmp / "Band" / name
            files = [str(make(folder / f"{i + 1:02d}.flac", 300 + 50 * i, s_,
                              dict(meta, title=f"T{i}", album="Alpha", albumartist="Band", track=str(i + 1))))
                     for i, s_ in enumerate(SECS)]
            a = dict(self.album(), source_dir=str(folder), album_files=files,
                     files=[{"src": f, "dst": f, "op": "tag", "diff": []} for f in files])   # (nothing changes)
            res = v.versions(self.lookup, a)
            self.assertEqual(res["anchors"]["group"], RG, name)
            self.assertEqual({r["id"] for r in res["rows"] if r["source"] == "mb"}, set(IDS), name)
            if name == "Held":
                self.assertEqual(res["anchors"]["master"], MASTER)
                self.assertNotIn("No MusicBrainz release group is known for this album.", res["notes"])
            else:
                self.assertEqual(res["anchors"]["release"], IDS[1])

    def test_anchors_from_a_discogs_release(self):
        a = self.album("exact")
        a["identification"].update(release_id="701", provider="discogs")
        for fe in a["files"]:
            fe["diff"] = []
        got = self.versions.anchors(self.lookup, a)
        self.assertEqual((got["master"], got["release"], got["group"]), (MASTER, "discogs:701", ""))
        # a MusicBrainz release linked to a Discogs one gives the master too
        self.http.links = {IDS[0]: "701"}
        a["identification"].update(release_id=IDS[0], provider="musicbrainz")
        got = self.versions.anchors(self.lookup, a)
        self.assertEqual((got["group"], got["master"]), (RG, MASTER))

    def test_release_group_read_page_by_page(self):
        v = self.versions
        self.http.page_cap = 2                                # (fewer than asked, as with a track budget)
        res = v.versions(self.lookup, self.album())
        self.assertEqual({r["id"] for r in res["rows"] if r["source"] == "mb"}, set(IDS))
        browses = [u for _h, u in self.http.calls if "release-group=" in u]
        self.assertEqual(len(browses), 2)
        self.assertIn("offset=2", browses[1])
        self.assertFalse([n for n in res["notes"] if n.startswith("MusicBrainz lists")])
        self.http.count = 5                                   # (it claims more than it sends)
        res = v.versions(self.lookup, self.album())
        self.assertIn("MusicBrainz lists 5 releases of this album; 3 are shown.", res["notes"])

    def test_release_group_stops_when_the_sheet_is_closed(self):
        self.http.page_cap = 1
        res = self.versions.versions(self.lookup, self.album(), cancel=lambda: len(
            [u for _h, u in self.http.calls if "release-group=" in u]) >= 1)
        self.assertEqual(len([u for _h, u in self.http.calls if "release-group=" in u]), 1)
        self.assertLessEqual({r["id"] for r in res["rows"] if r["source"] == "mb"}, {IDS[0]})

    def test_anchors_of_the_identified_release_not_the_files_stale_ids(self):
        # (a match below the replace threshold keeps the files' old group; a removed master leaves nothing)
        a = self.album("partial")
        a["identification"].update(release_id=IDS[1], provider="musicbrainz")
        stale = [{"field": "musicbrainz_releasegroupid", "before": ["rg-stale"], "after": ["rg-stale"],
                  "action": "kept (confidence below replace threshold)"},
                 {"field": "discogs_master_id", "before": ["999"], "after": [], "action": "removed"},
                 {"field": "musicbrainz_albumid", "before": ["old"], "after": ["old"], "action": "locked"}]
        for fe in a["files"]:
            fe["diff"] = stale
        got = self.versions.anchors(self.lookup, a, {"group": "rg-stale", "master": "999", "mb_release": "old"})
        self.assertEqual((got["group"], got["master"], got["release"]), (RG, "", IDS[1]))
        # with no release identified, what the files hold is all there is
        a["identification"].update(release_id="")
        got = self.versions.anchors(self.lookup, a, {"group": RG, "master": MASTER})
        self.assertEqual((got["group"], got["master"]), (RG, MASTER))

    def test_lookups_share_one_pace_per_host(self):
        import sqlite3
        import threading
        v = self.versions
        cfg = dict(mc.DEFAULT_CFG, mb_delay=0.2)
        v._LAST.clear()
        https = [v.paced(ma.Http(sqlite3.connect(":memory:", check_same_thread=False), cfg, "")) for _ in range(2)]
        self.assertIs(https[0].last, https[1].last)
        state = {"now": 0, "most": 0, "times": []}
        lock = threading.Lock()

        class Answer:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self):
                return b"{}"

        def urlopen(req, timeout=None):
            with lock:
                state["now"] += 1
                state["most"] = max(state["most"], state["now"])
                state["times"].append(time.time())
            time.sleep(0.05)
            with lock:
                state["now"] -= 1
            return Answer()
        with mock.patch.object(ma.urllib.request, "urlopen", urlopen):
            ts = [threading.Thread(target=h.get_json, args=(f"https://musicbrainz.org/ws/2/x{i}", "mb"))
                  for i, h in enumerate(https)]
            for t in ts:
                t.start()
            for t in ts:
                t.join()
        v._LAST.clear()
        self.assertEqual(state["most"], 1)                    # (one at a time across the two lookups)
        self.assertGreaterEqual(abs(state["times"][1] - state["times"][0]), 0.19)

    def test_other_albums_in_the_folder_and_the_discogs_cut_off(self):
        from _review14b_common import make
        from gui import search
        extra = make(self.folder / "04.flac", 700, 40, {"title": "Other", "album": "Beta", "track": "4"})
        try:
            with mock.patch.object(self.versions, "DISCOGS_DETAILS", 1):
                res = self.versions.versions(self.lookup, self.album())
                by = {r["id"]: r for r in res["rows"]}
                self.assertEqual(res["n_files"], 3)               # (04.flac is another album's)
                self.assertIn("same track count", by[IDS[0]]["fit"])
                self.assertIsNone(by["702"].get("score"))
                self.assertIn("The first 1 Discogs versions are compared with your files; "
                              "the others are listed without a fit.", res["notes"])
                dlg = self.opened(search.open_versions(self.page))
            rows = self.rows(dlg)
            r = next(i for i, x in enumerate(rows) if x["id"] == "702")
            fit = [k for k, _t in dlg.model.cols].index("fit")
            self.assertEqual(dlg.table._proxy.index(r, fit).data(), "Not compared")
        finally:
            extra.unlink()

    def test_anchor_from_the_identified_release(self):
        a = self.album("exact")
        a["identification"].update(release_id=IDS[1], provider="musicbrainz")
        for fe in a["files"]:
            fe["diff"] = []
        got = self.versions.anchors(self.lookup, a)
        self.assertEqual(got["group"], RG)
        self.assertEqual(got["release"], IDS[1])

    # ---- the sheets ----------------------------------------------------------
    def test_search_sheet_uses_the_release_through_the_forced_flow(self):
        from gui import search
        dlg = self.opened(search.open_search(self.page))
        self.assertEqual(dlg.query.text(), "Band Alpha")
        self.assertEqual(len(self.rows(dlg)), 3)
        self.assertTrue(dlg.ok.isEnabled())
        self.assertEqual(dlg.ok.text(), "Use This Release")
        dlg.table.selectRow(1)
        ref = self.rows(dlg)[1]["ref"]
        dlg.ok.click()
        key = _os.path.realpath(str(self.folder))
        self.assertEqual(self.page.forced[key], [{"ref": ref, "files": self.files}])
        self.assertEqual(self.page.previews, 1)
        self.assertFalse(dlg.isVisible())

    def test_search_sheet_tabs_and_artists(self):
        from gui import search
        dlg = self.opened(search.open_search(self.page))
        dlg.kinds.setCurrentIndex(3)                         # Discogs Releases
        wait(self.app, dlg)
        self.assertEqual([r["ref"] for r in self.rows(dlg)], ["discogs:701"])
        dlg.kinds.setCurrentIndex(2)                         # Artists
        wait(self.app, dlg)
        self.assertEqual(dlg.ok.text(), "Show Releases")
        dlg.ok.click()
        wait(self.app, dlg)
        self.assertEqual(dlg.kinds.currentIndex(), 0)
        self.assertEqual(dlg.query.text(), "Band")                    # (the artist's name, never a raw query)
        self.assertEqual(dlg.search_text(), "arid:a-1")
        self.assertIn("by Band", dlg.status.text())
        dlg.query.textEdited.emit("Band live")                     # (typed: your own words again)
        self.assertIsNone(dlg._artist)
        self.assertEqual(self.page.previews, 0)              # (an artist is never a release to use)

    def test_return_in_the_search_field_searches(self):
        from gui import search
        dlg = self.opened(search.open_search(self.page))
        n = len(self.http.calls)
        dlg.query.setText("Alpha")
        dlg.query.setFocus()
        QTest.keyClick(dlg.query, Qt.Key_Return)
        wait(self.app, dlg)
        self.assertGreater(len(self.http.calls), n)
        self.assertEqual(self.page.previews, 0)
        self.assertEqual(self.page.forced, {})
        # Return with a release selected (Use This Release enabled) still never takes it
        self.assertTrue(dlg.ok.isEnabled())
        dlg.query.blockSignals(True)                         # (no new search to clear the table)
        try:
            QTest.keyClick(dlg.query, Qt.Key_Return)
            settle(self.app)
        finally:
            dlg.query.blockSignals(False)
        self.assertEqual(self.page.forced, {})
        self.assertEqual(self.page.previews, 0)
        self.assertTrue(dlg.isVisible())

    def test_search_sheet_drops_late_answers(self):
        from gui import search
        dlg = self.opened(search.open_search(self.page))
        before = [r["id"] for r in self.rows(dlg)]
        dlg.query.setText("Something else")
        dlg.found({"kind": "release", "text": "Band Alpha", "rows": [dict(MB[0], ref="late")], "note": ""})
        self.assertEqual([r["id"] for r in self.rows(dlg)], before)
        # an older search that fails late leaves the current one's status alone
        self.http.boom = "Doomed"
        dlg.query.setText("Doomed")
        dlg.search()
        dlg.query.setText("Band Alpha")
        dlg.search()
        wait(self.app, dlg)
        self.assertEqual(len(self.rows(dlg)), 3)
        self.assertIn("3 releases found", dlg.status.text())
        self.assertEqual(self.failures, [])

    def test_search_sheet_drops_late_progress(self):
        import threading
        from gui import search
        dlg = self.opened(search.open_search(self.page))
        began, go = threading.Event(), threading.Event()

        def old(_lk, w):
            began.set()
            go.wait(5)
            w.progress.emit(0, 1, "Old step")
        dlg.run(old, lambda r: None, "Old search…")
        end = time.time() + 5
        while not began.is_set() and time.time() < end:
            self.app.processEvents()
            time.sleep(0.005)
        self.assertTrue(began.is_set())
        dlg.run(lambda _lk, _w: "new", lambda r: dlg.status.setText("New search done"), "New search…")
        end = time.time() + 5                                 # (the new one answers first, then the old one moves)
        while dlg.status.text() != "New search done" and time.time() < end:
            self.app.processEvents()
            time.sleep(0.005)
        go.set()
        wait(self.app, dlg)
        self.assertEqual(dlg.status.text(), "New search done")

    def test_sheet_closes_the_lookup_it_made(self):
        from gui import search
        dlg = self.opened(search.open_search(self.page))
        with mock.patch.object(self.lookup, "close") as close:
            dlg.run(lambda _lk, _w: None, lambda r: None, "Searching…")
            wait(self.app, dlg)
        close.assert_called_once()

    def test_album_menu_commands(self):
        from gui import actions, search
        win = QMainWindow()
        cmds = {k: actions.make(win, k, *spec) for k, spec in search.COMMANDS.items()}
        taken = {str(spec[1]) for k, spec in actions.COMMANDS.items() if spec[1] and k not in search.COMMANDS}
        for k, spec in search.COMMANDS.items():
            self.assertIs(actions.COMMANDS[k], spec)          # (the window's commands include them)
            self.assertNotIn(spec[1], taken, k)               # (Ctrl+F stays Find)
            self.assertIn(spec[2], __import__("gui.icons", fromlist=["NAMES"]).NAMES)
        for k, fn in search.handlers(lambda: self.page).items():
            cmds[k].triggered.connect(fn)
        search.update_commands(cmds, self.page, True)
        self.assertTrue(all(a.isEnabled() for a in cmds.values()))
        search.update_commands(cmds, self.page, False)
        self.assertFalse(any(a.isEnabled() for a in cmds.values()))
        self.page.a = self.album("exact")
        search.update_commands(cmds, self.page, True)
        self.assertFalse(cmds["choose_pressing"].isEnabled())
        self.page.a = self.album()
        search.update_commands(cmds, self.page, True)
        for k, cls, pressing in (("search_release", search.SearchSheet, None),
                                 ("other_versions", search.VersionsSheet, False),
                                 ("choose_pressing", search.VersionsSheet, True)):
            cmds[k].trigger()
            dlg = self.page._search_sheet if k == "search_release" else self.page._versions_sheet
            self.opened(dlg)
            self.assertIsInstance(dlg, cls)
            if pressing is not None:
                self.assertEqual(dlg.pressing, pressing)
        win.deleteLater()

    def test_search_sheet_says_what_failed(self):
        from gui import search
        self.http.boom = "Doomed"
        dlg = self.opened(search.open_search(self.page, text="Doomed"))
        self.assertTrue(dlg.status.text().startswith("The lookup did not finish: the stub broke on purpose"),
                        dlg.status.text())
        self.assertIn("Press Return to search again.", dlg.status.text())         # (what to do next)
        self.assertTrue(dlg.status.accessibleName().startswith("Search status: The lookup"))
        self.assertEqual(len(self.failures), 1)
        self.failures.clear()
        dlg.status.setText("")                               # (expected here: not a failure of the test)
        self.lookup = self.versions.Lookup(FakeHttp(down={"mb"}), dict(mc.DEFAULT_CFG), self.dm)
        dlg.query.setText("Alpha")
        dlg.search()
        wait(self.app, dlg)
        self.assertIn("did not answer", dlg.status.text())
        self.assertEqual(self.rows(dlg), [])

    def test_search_sheet_shortcuts_and_recordings(self):
        from gui import search
        dlg = self.opened(search.open_search(self.page))
        dlg.activateWindow()
        settle(self.app)
        QTest.keyClick(dlg, Qt.Key_2, Qt.ControlModifier)
        wait(self.app, dlg)
        self.assertEqual(dlg.kinds.currentIndex(), 1)
        self.assertEqual([k for k, _t in dlg.model.cols][:2], ["recording", "length"])
        rows = self.rows(dlg)
        self.assertEqual((rows[0]["recording"], rows[0]["ref"]), ("T0", IDS[0]))
        self.assertTrue(dlg.ok.isEnabled())                  # (the release it is on can be used)
        QTest.keyClick(dlg, Qt.Key_4, Qt.ControlModifier)
        wait(self.app, dlg)
        self.assertEqual(dlg.kinds.currentIndex(), 3)
        self.assertEqual([r["ref"] for r in self.rows(dlg)], ["discogs:701"])

    def test_search_without_an_album_cannot_use(self):
        from gui import search
        self.page.a = None
        dlg = self.opened(search.open_search(self.page, text="Alpha"))
        self.assertTrue(self.rows(dlg))
        self.assertFalse(dlg.ok.isEnabled())

    def test_other_versions_sheet(self):
        from gui import search
        dlg = self.opened(search.open_versions(self.page))
        rows = self.rows(dlg)
        self.assertEqual(len(rows), 5)
        self.assertEqual(rows[0]["score"], max(r["score"] for r in rows))
        self.assertEqual(dlg.model.highlight, set())
        self.assertTrue(dlg.check.isHidden())
        cols = [c for c, _t in dlg.model.cols]
        self.assertIn("fit", cols)
        self.assertIn("5 releases found", dlg.status.text())

    def test_pressing_chooser(self):
        from gui import search
        self.assertTrue(search.can_choose_pressing(self.page.a))
        self.assertFalse(search.can_choose_pressing(self.album("exact")))
        dlg = self.opened(search.open_pressing(self.page))
        rows = self.rows(dlg)
        self.assertEqual({r["id"] for r in rows}, {IDS[0], IDS[1]})
        self.assertIn("barcode", dlg.model.highlight)
        self.assertNotIn("format", dlg.model.highlight)
        # what tells them apart right after the source, in the order to check them; title and artist last
        keys = [k for k, _t in dlg.model.cols]
        lit = [k for k in self.versions.DIFF_ORDER if k in dlg.model.highlight]
        self.assertEqual(keys[0], "source")
        self.assertEqual(keys[1:1 + len(lit)], lit)
        self.assertEqual(keys[-2:], ["title", "artist"])
        c = [k for k, _t in dlg.model.cols].index("barcode")
        self.assertTrue(dlg.model.headerData(c, Qt.Horizontal, Qt.FontRole).bold())
        self.assertIn("(differs)", dlg.model.data(dlg.model.index(0, c), Qt.AccessibleTextRole))
        self.assertTrue(dlg.check.text().startswith("Check the barcode on your case"))
        self.assertEqual(dlg.ok.text(), "Use This Pressing")
        self.assertIn("0724384260927 vs 5013929020153", dlg.check.text())
        pick = next(i for i, r in enumerate(rows) if r["id"] == IDS[1])
        dlg.table.selectRow(pick)
        dlg.ok.click()
        key = _os.path.realpath(str(self.folder))
        self.assertEqual(self.page.forced[key][0]["ref"], IDS[1])
        self.assertEqual(self.page.previews, 1)

    def test_the_tables_never_scroll_sideways_at_960(self):
        from gui import search
        for dlg in (self.opened(search.open_search(self.page)), self.opened(search.open_versions(self.page))):
            dlg.resize(912, 504)                                         # (the sheet in a 960 x 600 window)
            dlg.show()
            settle(self.app, 6)
            t = dlg.table
            shown = [c for c in range(t.model().columnCount()) if not t.isColumnHidden(c)]
            self.assertLessEqual(sum(t.columnWidth(c) for c in shown), t.viewport().width())
            dlg.resize(640, 420)                                         # (narrower: least needed columns go)
            settle(self.app, 6)
            shown = [c for c in range(t.model().columnCount()) if not t.isColumnHidden(c)]
            self.assertLessEqual(sum(t.columnWidth(c) for c in shown), t.viewport().width())
            hidden = dlg.model.hidden
            if hidden:                                                   # (their values in the row's tooltip)
                k = next(iter(hidden))
                title = dict(dlg.model.cols)[k]
                tips = [dlg.model.data(dlg.model.index(r, 0), Qt.ToolTipRole) or ""
                        for r in range(dlg.model.rowCount())]
                self.assertTrue(any(f"{title}:" in tip for tip in tips), tips)
        s = self.opened(search.open_search(self.page))
        self.assertEqual(s.kinds.tabToolTip(1), "Recordings (Ctrl+2)")
        self.assertEqual(s.status.accessibleDescription(), "Search status")
        self.assertEqual(s.windowTitle(), "Search for Release")
        v = self.opened(search.open_versions(self.page))
        self.assertEqual(v.status.accessibleDescription(), "Lookup status")

    def test_differing_values_are_bold_on_a_tint_not_a_status(self):
        from gui import search
        dlg = self.opened(search.open_pressing(self.page))
        c = [k for k, _t in dlg.model.cols].index("barcode")
        idx = dlg.model.index(0, c)
        self.assertIsNone(dlg.model.data(idx, Qt.DecorationRole))                # (no status icon)
        self.assertIsNone(dlg.model.data(idx, Qt.ForegroundRole))                # (ink, not the Ready colour)
        self.assertTrue(dlg.model.data(idx, Qt.FontRole).bold())
        tint = dlg.model.data(idx, Qt.BackgroundRole)
        self.assertEqual(tint.name(), self.gui_style().color("accent").lower())
        self.assertLess(tint.alpha(), 60)
        self.assertFalse(dlg.legend.isHidden())
        self.assertEqual(dlg.legend.text(), "Bold values are the ones to compare with your copy.")
        # (every pressing fits equally: no Fit columns, said once in the status line)
        keys = [k for k, _t in dlg.model.cols]
        self.assertNotIn("score", keys)
        self.assertNotIn("fit", keys)
        self.assertIn("fit your", dlg.status.text())
        self.assertIn("equally", dlg.status.text())

    def gui_style(self):
        from gui import style
        return style

    def test_results_keep_the_lookups_order_with_no_sort_arrow(self):
        from gui import search
        dlg = self.opened(search.open_search(self.page, text="Alpha"))
        self.assertEqual(dlg.table.horizontalHeader().sortIndicatorSection(), -1)
        got = [dlg.table._proxy.data(dlg.table._proxy.index(r, 0), Qt.UserRole)["id"]
               for r in range(dlg.table._proxy.rowCount())]
        self.assertEqual(got, [r["id"] for r in dlg.model.rows])
        self.assertFalse(dlg.kinds.expanding())                                  # (compact tabs, at the left)

    def test_versions_sheet_offers_try_again_after_a_failure(self):
        from gui import search
        dlg = self.opened(search.open_versions(self.page))
        self.assertTrue(dlg.again.isHidden())
        dlg.failed("<urlopen error timed out>")
        self.failures.clear()
        self.assertTrue(dlg.status.text().startswith("MusicBrainz could not be reached"), dlg.status.text())
        self.assertIn("Try Again (Ctrl+R)", dlg.status.text())
        self.assertFalse(dlg.again.isHidden())
        dlg.again.click()
        wait(self.app, dlg)
        self.assertTrue(dlg.again.isHidden())
        self.assertIn("found", dlg.status.text())

    def test_use_says_in_the_sheet_when_work_runs_and_how_to_go_back(self):
        from gui import search
        dlg = self.opened(search.open_versions(self.page))
        self.page.running = True
        dlg.use()
        self.assertEqual(dlg.status.text(), search.BUSY)                         # (in the sheet, not behind it)
        self.assertTrue(dlg.isVisible())
        self.page.running = False
        dlg.use()
        self.assertIn("To go back, choose another release", self.page.statusBar().currentMessage())

    def test_pressing_chooser_without_a_tie_lists_every_version(self):
        from gui import search
        self.http.mb = [MB[0], MB[2]]                         # (one clear MusicBrainz winner)
        dlg = self.opened(search.open_pressing(self.page))
        self.assertTrue(dlg.check.isHidden())
        self.assertEqual({r["id"] for r in self.rows(dlg)}, {IDS[0], IDS[2], "701", "702"})
        self.assertEqual(dlg.model.highlight, set())
        self.assertIn("No pressings tie", dlg.status.text())

    def test_pressing_chooser_keeps_to_what_the_files_were_made_from(self):
        from gui import search
        vinyl = dict(MB[1], media=[dict(MB[1]["media"][0], format="Vinyl")])
        self.http.mb = [MB[0], vinyl, MB[2]]
        with mock.patch.object(self.versions.ma, "tie_source", return_value="cd"):
            self.assertEqual(self.versions.versions(self.lookup, self.album())["media"], "cd")
            dlg = self.opened(search.open_pressing(self.page))
        self.assertTrue(dlg.check.isHidden())
        self.assertIn("No pressings tie", dlg.status.text())

    def test_force_release_keeps_one_choice_per_album_and_waits_for_work(self):
        from gui import search
        a = self.page.a
        self.assertTrue(search.force_release(self.page, a, IDS[0]))
        self.assertTrue(search.force_release(self.page, a, "discogs:701"))
        key = _os.path.realpath(str(self.folder))
        self.assertEqual([c["ref"] for c in self.page.forced[key]], ["discogs:701"])
        self.assertFalse(search.force_release(self.page, a, "not a release"))
        self.page.running = True
        self.assertFalse(search.force_release(self.page, a, IDS[1]))
        self.assertEqual(self.page.previews, 2)

    def test_lookups_run_on_a_worker(self):
        from gui import search, work
        with mock.patch.object(search, "start", wraps=work.start) as st:
            dlg = self.opened(search.open_versions(self.page))
        self.assertEqual(st.call_count, 1)
        self.assertIs(st.call_args[0][0], dlg)


class Page(QMainWindow if QApplication else object):
    """The Overview's side of the flow: the selected album, the forced releases, preview()."""

    def __init__(self, a):
        super().__init__()
        self.a, self.forced, self.previews, self.running = a, {}, 0, False
        self.profile = QComboBox()
        self.profile.addItem("default")
        self.win = self

    def selected(self):
        return self.a

    def preview(self):
        self.previews += 1


if __name__ == "__main__":
    unittest.main(argv=[sys.argv[0]])
