# Look Up CD without Qt (cdlookup.py): a table of contents pasted in its several forms, read from
# a generated EAC log (an Enhanced CD's data track left out), read from a drive through a stand-in
# python-discid module, and looked up through match_albums.toc_releases with stubbed HTTP. No
# drive, no network: an unknown URL fails the test.
import os as _os, tempfile as _tempfile  # noqa: E402
_os.environ["XDG_CONFIG_HOME"] = _tempfile.mkdtemp(prefix="tm-config-")
_os.environ["XDG_STATE_HOME"] = _tempfile.mkdtemp(prefix="tm-state-")
_os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
_os.environ.setdefault("TAGMENDER_TAGGER_INI", "/nonexistent")

import shutil
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

import cdlookup  # noqa: E402
import match_albums as ma  # noqa: E402
import match_core as mc  # noqa: E402

try:
    import PySide6  # noqa: F401  (gui.versions builds the rows; the gui package needs PySide6)
except ImportError:
    PySide6 = None

TOC = "1 3 30151 150 10151 20151"
EXACT, FUZZY, CTDB_MB = ("0a8e97fd-4f6a-4bd7-8e4b-6d3a0b2ad2f1", "1b9f08ae-5a7b-4ce8-9f5c-7e4b1c3be3a2",
                         "2c0f19bf-6b7c-4df9-a05d-8f5c2d4cf4b3")


def eac_log(data_track=False):
    rows = [(1, 0, 10000), (2, 10001, 20000), (3, 20001, 30000)]
    if data_track:                                    # (an Enhanced CD: the data session 11400 sectors on)
        rows.append((4, 30000 + ma.SESSION_GAP + 1, 60000))
    lines = ["Exact Audio Copy V1.0 beta 3 from 29. August 2011", "", "EAC extraction logfile from 1. January 2020",
             "", "Band / Alpha", "", "TOC of the extracted CD", "",
             "     Track |   Start  |  Length  | Start sector | End sector ",
             "    ---------------------------------------------------------"]
    lines += [f"       {n}  |  0:00.00 |  2:13.25 |     {s:>5}    |    {e:>5}   " for n, s, e in rows]
    return "\r\n".join(lines + ["", "Range status and errors", ""])


def mb_rel(rid, title):
    return {"id": rid, "title": title, "date": "2005", "country": "GB", "barcode": "5013929020153",
            "artist-credit": [{"name": "Band", "joinphrase": "", "artist": {"id": "a-1", "name": "Band"}}],
            "label-info": [{"label": {"name": "Label"}, "catalog-number": "LAB 1"}],
            "media": [{"format": "CD", "position": 1, "track-count": 3, "discs": []}]}


class FakeHttp:
    """MusicBrainz's disc ID answers (exact or fuzzy), a release, CTDB's links and a Discogs
    release; anything else is recorded in `unexpected` and raised."""

    def __init__(self, exact=True, down=(), ctdb_discogs=("701",), ctdb_mb=(CTDB_MB,), discogs_refuses=False):
        self.exact, self.down, self.ctdb_discogs, self.ctdb_mb = exact, set(down), ctdb_discogs, ctdb_mb
        self.discogs_refuses = discogs_refuses
        self.calls, self.unexpected = [], []

    def get_json(self, url, host):
        self.calls.append((host, url))
        if host in self.down:
            raise ma.ProviderUnavailable(host, "outage", "HTTP 503")
        did = ma.mb_discid(TOC)
        if host == "mb" and f"/ws/2/discid/{did}?toc={TOC.replace(' ', '+')}&cdstubs=no" in url:
            rels = [mb_rel(EXACT, "Alpha")] if self.exact else [mb_rel(FUZZY, "Alpha (fuzzy)")]
            if self.exact:
                rels[0]["media"][0]["discs"] = [{"id": did}]
            return {"id": did, "releases": rels} if self.exact else {"releases": rels}
        for rid in self.ctdb_mb:
            if host == "mb" and f"/ws/2/release/{rid}?" in url:
                return mb_rel(rid, "Alpha (CTDB)")
        if host == "discogs" and self.discogs_refuses:
            raise ma.AuthError("Discogs rejected the token (HTTP 401)")
        if host == "discogs" and url == "https://api.discogs.com/releases/701":
            return {"id": 701, "title": "Alpha", "artists": [{"name": "Band"}],
                    "labels": [{"name": "DG", "catno": "DG-1"}],
                    "formats": [{"name": "CD", "qty": "1"}], "country": "UK", "year": 2005}
        self.unexpected.append((host, url))
        raise AssertionError(f"unexpected request: {host} {url}")

    def get_text(self, url, host, *a, **kw):
        self.calls.append((host, url))
        if host == "ctdb" and "db.cue.tools/lookup2.php" in url and f"toc={ma.ctdb_toc(TOC)}" in url:
            if host in self.down:
                raise ma.ProviderUnavailable(host, "outage", "HTTP 503")
            tags = [f'<metadata source="musicbrainz" id="{rid}"/>' for rid in self.ctdb_mb]
            tags += [f'<metadata source="discogs" id="{d}"/>' for d in self.ctdb_discogs]
            return "<ctdb>" + "".join(tags) + "</ctdb>"
        self.unexpected.append((host, url))
        raise AssertionError(f"unexpected request: {host} {url}")


class Toc(unittest.TestCase):
    def test_pasted_forms(self):
        self.assertEqual(cdlookup.parse_toc(TOC), TOC)
        self.assertEqual(cdlookup.parse_toc("  1+3+30151+150+10151+20151\n"), TOC)
        self.assertEqual(cdlookup.parse_toc("1  3 30151\t150 10151 20151"), TOC)
        self.assertEqual(cdlookup.parse_toc(f"https://musicbrainz.org/cdtoc/attach?id=x&tracks=3&toc="
                                            f"{TOC.replace(' ', '+')}"), TOC)
        self.assertEqual(cdlookup.parse_toc("0:10001:20001:30001"), TOC)       # (CUETools DB's form)
        self.assertEqual(cdlookup.parse_toc(eac_log()), TOC)                     # (a log's text pasted)

    def test_what_is_wrong_in_plain_words(self):
        for text, words in (("", "Paste"), ("hello", "not a table of contents"),
                            ("1 3 30151 150 10151", "3 tracks but gives 2"), ("2 3 30151 150 10151", "track 1"),
                            ("1 3 30151 150 20151 10151", "rise"), ("1 3 30151 100 10151 20151", "rise"),
                            ("1 3 20000 150 10151 20151", "length"), ("1 3 999999 150 10151 20151", "99 minutes"),
                            ("1 100 30151 " + " ".join(str(150 + i) for i in range(100)), "1 to 99"),
                            (ma.mb_discid(TOC), "disc ID")):
            with self.assertRaises(cdlookup.CdError, msg=text) as cm:
                cdlookup.parse_toc(text)
            self.assertIn(words, str(cm.exception), text)

    def test_disc_and_its_words(self):
        d = cdlookup.disc(TOC, "the table of contents you pasted")
        self.assertEqual((d["id"], d["tracks"], d["seconds"]), (ma.mb_discid(TOC), 3, 400))
        self.assertEqual(cdlookup.describe(d), "3 tracks, 6:40, from the table of contents you pasted")
        sent = cdlookup.sent_text(d)
        for words in (d["id"], "3 track offsets", "MusicBrainz", "CUETools database", "release IDs", "Nothing else"):
            self.assertIn(words, sent)
        self.assertNotIn("Discogs", sent)
        self.assertIn("api.discogs.com", cdlookup.sent_text(d, discogs=True))
        self.assertIn("Discogs token", cdlookup.sent_text(d, discogs=True))
        self.assertIn("sends nothing", cdlookup.sent_text(d, mb=False, discogs=True))
        self.assertEqual(cdlookup.submit_url(d), f"https://musicbrainz.org/cdtoc/attach?id={d['id']}&tracks=3"
                                                 f"&toc={TOC.replace(' ', '+')}")
        self.assertEqual(cdlookup.length_text(3725), "1:02:05")


class Logs(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="tm-cdlog-"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_eac_log_and_enhanced_cd(self):
        for data, enc in ((False, "utf-8"), (True, "utf-16")):
            p = self.tmp / f"rip-{data}.log"
            p.write_bytes(eac_log(data).encode(enc))
            d = cdlookup.read_log(p)
            self.assertEqual(d["toc"], TOC)                # (the data track is not MusicBrainz's)
            self.assertEqual(d["source"], f"the rip log {p.name}")

    def test_log_without_toc_or_unreadable(self):
        p = self.tmp / "checker.log"
        p.write_text("auCDtect: CD records authenticity detector\nThis track looks like CDDA\n")
        with self.assertRaises(cdlookup.CdError) as cm:
            cdlookup.read_log(p)
        self.assertIn("No table of contents found in checker.log", str(cm.exception))
        with self.assertRaises(cdlookup.CdError) as cm:
            cdlookup.read_log(self.tmp / "gone.log")
        self.assertIn("could not be read", str(cm.exception))


def fake_discid(toc=TOC, mcn="5013929020153", fail=None):
    """A stand-in for python-discid (libdiscid is not installed here): read() gives a disc with
    this TOC, the ID libdiscid would give, an MCN and one ISRC."""
    m = types.ModuleType("discid")

    class DiscError(Exception):
        pass
    m.DiscError, m.FEATURES = DiscError, ["read", "mcn", "isrc"]
    m.asked = []

    def read(device=None, features=()):
        m.asked.append((device, tuple(features)))
        if fail:
            raise DiscError(fail)
        tracks = [types.SimpleNamespace(number=1, isrc="GBAAA0500001"), types.SimpleNamespace(number=2, isrc=""),
                  types.SimpleNamespace(number=3, isrc="000000000000")]
        return types.SimpleNamespace(id=ma.mb_discid(toc), toc_string=toc, mcn=mcn, tracks=tracks)
    m.read = read
    m.get_default_device = lambda: "/dev/cdrom"
    return m


class Drive(unittest.TestCase):
    def test_without_python_discid(self):
        with mock.patch.object(cdlookup, "discid_module", return_value=None):
            self.assertFalse(cdlookup.can_read_drive())
            self.assertEqual(cdlookup.drives(), [])
            with self.assertRaises(cdlookup.CdError) as cm:
                cdlookup.read_drive()
        self.assertIn("python-discid (with libdiscid) is not installed", str(cm.exception))

    def test_import_is_lazy(self):
        with mock.patch.dict(sys.modules, {"discid": None}):      # (import discid raises ImportError)
            self.assertIsNone(cdlookup.discid_module())

    def test_read_with_python_discid(self):
        m = fake_discid()
        with mock.patch.dict(sys.modules, {"discid": m}):
            self.assertTrue(cdlookup.can_read_drive())
            self.assertEqual(cdlookup.drives()[0], "/dev/cdrom")
            d = cdlookup.read_drive("/dev/sr0")
        self.assertEqual(m.asked, [("/dev/sr0", ("mcn", "isrc"))])
        self.assertEqual((d["toc"], d["id"], d["mcn"], d["isrcs"]), (TOC, ma.mb_discid(TOC), "5013929020153",
                                                                   {1: "GBAAA0500001"}))
        self.assertEqual(d["source"], "the CD in /dev/sr0")
        self.assertIn("barcode 5013929020153, 1 ISRC", cdlookup.describe(d))
        with mock.patch.dict(sys.modules, {"discid": fake_discid(mcn="0000000000000")}):
            self.assertEqual(cdlookup.read_drive()["mcn"], "")
        with mock.patch.dict(sys.modules, {"discid": fake_discid(fail="no disc in drive")}):
            with self.assertRaises(cdlookup.CdError) as cm:
                cdlookup.read_drive()
        self.assertIn("could not be read: no disc in drive", str(cm.exception))

    def test_one_drive_listed_once(self):
        """/dev/cdrom (libdiscid's default) is a link to /dev/sr0: one drive, offered once."""
        class FakePath:
            def __init__(self, s):
                self.s = s

            def __str__(self):
                return self.s
            __fspath__ = __str__

            def __lt__(self, other):
                return self.s < other.s

            def glob(self, _pat):
                return [FakePath("/dev/sr0"), FakePath("/dev/sr1")]

            def exists(self):
                return True
        links = {"/dev/cdrom": "/dev/sr0"}
        with mock.patch.dict(sys.modules, {"discid": fake_discid()}), \
                mock.patch.object(cdlookup, "Path", FakePath), \
                mock.patch("os.path.realpath", lambda p: links.get(_os.fspath(p), _os.fspath(p))):
            self.assertEqual(cdlookup.drives(), ["/dev/cdrom", "/dev/sr1"])


class AnyDiscogs(FakeHttp):
    """FakeHttp that answers every Discogs release CTDB links."""

    def get_json(self, url, host):
        if host == "discogs" and not self.discogs_refuses and url.startswith("https://api.discogs.com/releases/"):
            self.calls.append((host, url))
            rid = int(url.rsplit("/", 1)[1])
            return {"id": rid, "title": f"Alpha {rid}", "artists": [{"name": "Band"}], "labels": [],
                    "formats": [{"name": "CD", "qty": "1"}], "country": "UK", "year": 2005}
        return super().get_json(url, host)


@unittest.skipIf(PySide6 is None, "PySide6 not installed")
class Lookup(unittest.TestCase):
    def lookup(self, http, use_mb=True, dm=False):
        from gui import versions
        lk = versions.Lookup(http, dict(mc.DEFAULT_CFG), None, use_mb=use_mb, use_discogs=False)
        if dm:                                         # (Discogs on: the release row without the plugin's code)
            lk.use_discogs = True
        return lk

    def test_exact_then_ctdb(self):
        http = FakeHttp()
        lk = self.lookup(http)
        lk.notes = {"discogs": "no Discogs token"}
        res = cdlookup.lookup(lk, cdlookup.disc(TOC))
        self.assertEqual(http.unexpected, [])
        self.assertTrue(any("Discogs is off: no Discogs token" in n for n in res["notes"]), res["notes"])
        self.assertEqual([(r["id"], r["fit"]) for r in res["rows"]],
                         [(EXACT, "This disc ID"), (CTDB_MB, "CUETools database link")])
        first = res["rows"][0]
        self.assertEqual((first["ref"], first["artist"], first["catno"], first["source"]),
                         (EXACT, "Band", "LAB 1", "mb"))
        self.assertTrue(res["attached"])
        self.assertTrue(any("1 Discogs release" in n and "Discogs is off" in n for n in res["notes"]), res["notes"])
        self.assertIn("2 releases found, 1 with this very disc ID.", cdlookup.result_text(res))
        hosts = {h for h, _u in http.calls}
        self.assertEqual(hosts, {"mb", "ctdb"})          # (Discogs off: never asked)

    def test_fuzzy_and_discogs(self):
        http = FakeHttp(exact=False)
        res = cdlookup.lookup(self.lookup(http, dm=True), cdlookup.disc(TOC))
        self.assertEqual(http.unexpected, [])
        self.assertEqual([(r["ref"], r["fit"]) for r in res["rows"]],
                         [(FUZZY, "Same track layout"), (CTDB_MB, "CUETools database link"),
                          ("discogs:701", "CUETools database link")])
        self.assertFalse(res["attached"])
        self.assertIn("none has this disc ID yet", cdlookup.result_text(res))

    def test_failures_are_notes(self):
        http = FakeHttp(down={"mb", "ctdb"})
        res = cdlookup.lookup(self.lookup(http), cdlookup.disc(TOC))
        self.assertEqual(res["rows"], [])
        self.assertIsNone(res["attached"])
        text = cdlookup.result_text(res)
        self.assertIn("MusicBrainz disc ID lookup failed", text)
        self.assertIn("CUETools DB lookup of the disc failed", text)
        self.assertIn("Submit Disc ID", text)

    def test_musicbrainz_off_asks_nothing(self):
        http = FakeHttp()
        res = cdlookup.lookup(self.lookup(http, use_mb=False), cdlookup.disc(TOC))
        self.assertEqual((res["rows"], http.calls), ([], []))
        self.assertIn("MusicBrainz is off", res["notes"][0])

    def test_cancelled_stops_asking(self):
        http = FakeHttp()
        res = cdlookup.lookup(self.lookup(http, dm=True), cdlookup.disc(TOC), cancel=lambda: True)
        self.assertEqual(res["rows"], [])
        self.assertEqual(sorted(h for h, _u in http.calls), ["ctdb", "mb"])     # (only the disc's own lookups)
        self.assertNotIn("inc=", next(u for h, u in http.calls if h == "mb"))

    def test_extra_releases_capped(self):
        ids = tuple(f"{i:08x}-0000-4000-8000-000000000000" for i in range(17))
        http = FakeHttp(ctdb_mb=ids)
        res = cdlookup.lookup(self.lookup(http), cdlookup.disc(TOC))
        self.assertEqual(http.unexpected, [])
        self.assertEqual(sum(1 for _h, u in http.calls if "/ws/2/release/" in u), cdlookup.EXTRA_RELEASES)
        self.assertEqual(len(res["rows"]), 1 + cdlookup.EXTRA_RELEASES)
        self.assertTrue(any("2 more MusicBrainz releases" in n for n in res["notes"]), res["notes"])

    def test_extra_discogs_releases_capped(self):
        ids = tuple(str(700 + i) for i in range(cdlookup.EXTRA_RELEASES + 3))
        http = AnyDiscogs(ctdb_discogs=ids)
        res = cdlookup.lookup(self.lookup(http, dm=True), cdlookup.disc(TOC))
        self.assertEqual(http.unexpected, [])
        self.assertEqual(sum(1 for h, _u in http.calls if h == "discogs"), cdlookup.EXTRA_RELEASES)
        self.assertEqual(sum(1 for r in res["rows"] if r["ref"].startswith("discogs:")), cdlookup.EXTRA_RELEASES)
        self.assertTrue(any("3 more Discogs releases" in n for n in res["notes"]), res["notes"])
        http = AnyDiscogs(ctdb_discogs=ids, discogs_refuses=True)        # (refused: that note, not this one)
        res = cdlookup.lookup(self.lookup(http, dm=True), cdlookup.disc(TOC))
        self.assertFalse(any("more Discogs releases" in n for n in res["notes"]), res["notes"])

    def test_discogs_token_refused_keeps_musicbrainz(self):
        http = FakeHttp(ctdb_discogs=("701", "702"), discogs_refuses=True)
        res = cdlookup.lookup(self.lookup(http, dm=True), cdlookup.disc(TOC))
        self.assertEqual([r["ref"] for r in res["rows"]], [EXACT, CTDB_MB])
        self.assertTrue(any("Discogs rejected your token" in n for n in res["notes"]), res["notes"])
        self.assertEqual(sum(1 for h, _u in http.calls if h == "discogs"), 1)   # (asked once, then no more)

    def test_what_is_sent_is_what_was_said(self):
        """Every service lookup() asks is one sent_text names, with Discogs on and off."""
        names = {"mb": "MusicBrainz", "ctdb": "db.cue.tools", "discogs": "api.discogs.com"}
        d = cdlookup.disc(TOC)
        for discogs in (False, True):
            http = FakeHttp()
            res = cdlookup.lookup(self.lookup(http, dm=True), d, discogs=discogs)
            if not discogs:
                self.assertTrue(any("Look Up did not say it would be asked" in n for n in res["notes"]),
                                res["notes"])
            said = cdlookup.sent_text(d, discogs=discogs)
            hosts = {h for h, _u in http.calls}
            self.assertEqual(hosts, {"mb", "ctdb", "discogs"} if discogs else {"mb", "ctdb"})
            for h in hosts:
                self.assertIn(names[h], said, (discogs, h))
            self.assertEqual("Discogs" in said, discogs)


if __name__ == "__main__":
    unittest.main(argv=[sys.argv[0]])
