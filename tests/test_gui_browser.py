# Look Up CD and browser integration (gui/browser.py), offscreen with stubbed HTTP: the CD sheet
# reads a pasted TOC, a generated rip log or a stand-in drive, says what Look Up sends before
# anything is sent, lists the releases and hands the chosen one to the forced-release flow
# (page.forced, then a preview; nothing is written). The browser-integration server is tested
# over a real loopback socket: 127.0.0.1 only, the next free port, Host and Origin checks, GET
# only, a clean stop, and its requests reaching the UI thread. No network, no drive.
import os as _os, tempfile as _tempfile  # noqa: E402
_os.environ["XDG_CONFIG_HOME"] = _tempfile.mkdtemp(prefix="tm-config-")
_os.environ["XDG_STATE_HOME"] = _tempfile.mkdtemp(prefix="tm-state-")
_os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
_os.environ.setdefault("TAGMENDER_TAGGER_INI", "/nonexistent")

import http.client
import shutil
import socket
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "tests"))
try:
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication, QComboBox, QFormLayout, QMainWindow, QWidget
except ImportError:                                           # GUI is optional
    QApplication = None

import match_core as mc  # noqa: E402
from test_cdlookup import CTDB_MB, EXACT, TOC, FakeHttp, eac_log, fake_discid  # noqa: E402

MBID = "5b11f4ce-a62d-471e-81fc-a69a8278c7da"


def settle(app, n=5):
    for _ in range(n):
        app.processEvents()
        time.sleep(0.005)


def wait(app, dlg, timeout=20):
    from gui.work import _alive
    end = time.time() + timeout
    while time.time() < end and any(_alive(t) for t, _w in getattr(dlg, "_threads", [])):
        app.processEvents()
        time.sleep(0.01)
    settle(app)


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def get(port, path, host=None, method="GET", headers=None):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    c.putrequest(method, path, skip_host=True)
    c.putheader("Host", host if host is not None else f"127.0.0.1:{port}")
    for k, v in (headers or {}).items():
        c.putheader(k, v)
    c.endheaders()
    r = c.getresponse()
    out = (r.status, r.read().decode("utf-8"), dict(r.getheaders()))
    c.close()
    return out


class Page(QMainWindow if QApplication else object):
    """The Overview's side of the flow: the selected album, the forced releases, preview()."""

    def __init__(self, a):
        super().__init__()
        self.a, self.forced, self.previews, self.running = a, {}, 0, False
        self.profile = QComboBox()
        self.profile.addItem("default")
        self.win = self
        self.pages = {"Overview": self}
        self.went = []

    def selected(self):
        return self.a

    def preview(self):
        self.previews += 1

    def go(self, name):
        self.went.append(name)


# --------------------------------------------------------------------------
# the server, without Qt
# --------------------------------------------------------------------------
@unittest.skipIf(QApplication is None, "PySide6 not installed")
class Server(unittest.TestCase):
    def setUp(self):
        from gui import browser
        self.browser = browser
        self.asked = []
        self.srv = browser.LocalServer(lambda kind, mbid: self.asked.append((kind, mbid)), free_port())
        self.port = self.srv.start()

    def tearDown(self):
        self.srv.stop()

    def test_loopback_only_and_answers(self):
        self.assertEqual(self.srv.httpd.server_address[0], "127.0.0.1")
        status, body, headers = get(self.port, f"/openalbum?id={MBID}&t=1730000000")
        self.assertEqual(status, 200)
        self.assertIn(f"Sent to TagMender: {MBID}", body)
        self.assertEqual(headers["Content-Type"], "text/plain; charset=utf-8")
        self.assertEqual(self.asked, [("release", MBID)])
        self.assertEqual(get(self.port, f"/?id={MBID.upper()}")[0], 200)         # (Picard's "/?id=")
        self.assertEqual(get(self.port, f"/opennat?id={MBID}", host=f"localhost:{self.port}")[0], 200)
        self.assertEqual(self.asked[1:], [("release", MBID), ("recording", MBID)])
        status, body, _h = get(self.port, "/")
        self.assertEqual((status, body.strip()), (200, self.browser.SERVER))
        self.assertEqual(len(self.asked), 3)

    def test_bad_requests(self):
        for path, code in (("/openalbum", 400), ("/openalbum?id=", 400), ("/openalbum?id=nope", 400),
                           (f"/openalbum?id={MBID}x", 400), ("/add?token=1", 404), ("/auth?code=1", 404),
                           ("/favicon.ico", 404)):
            self.assertEqual(get(self.port, path)[0], code, path)
        self.assertEqual(self.asked, [])

    def test_other_hosts_refused(self):
        for host in (f"evil.example:{self.port}", "127.0.0.1", f"127.0.0.1:{self.port + 1}", "",
                     f"0.0.0.0:{self.port}"):
            status, _b, _h = get(self.port, f"/openalbum?id={MBID}", host=host)
            self.assertEqual(status, 403, host)
        self.assertEqual(self.asked, [])

    def test_only_get(self):
        for method in ("POST", "PUT", "DELETE", "OPTIONS", "HEAD", "PATCH"):
            status, _b, headers = get(self.port, f"/openalbum?id={MBID}", method=method)
            self.assertEqual((status, headers.get("Allow")), (405, "GET"), method)
        status, _b, _h = get(self.port, f"/openalbum?id={MBID}", method="BREW")
        self.assertEqual(status, 501)
        self.assertEqual(self.asked, [])

    def test_web_pages_of_other_sites_refused(self):
        status, _b, _h = get(self.port, f"/openalbum?id={MBID}", headers={"Origin": "https://evil.example"})
        self.assertEqual(status, 403)
        status, _b, _h = get(self.port, f"/openalbum?id={MBID}",
                             headers={"Origin": "https://musicbrainz.org.evil.example"})
        self.assertEqual(status, 403)
        self.assertEqual(self.asked, [])
        status, _b, headers = get(self.port, f"/openalbum?id={MBID}",
                                  headers={"Origin": "https://beta.musicbrainz.org"})
        self.assertEqual((status, headers.get("Access-Control-Allow-Origin")), (200, "https://beta.musicbrainz.org"))
        self.assertEqual(self.asked, [("release", MBID)])

    def test_cross_site_requests_refused(self):
        """Sec-Fetch-Site from another site: only a navigation, or musicbrainz.org's page, is taken."""
        url = f"/openalbum?id={MBID}"
        img = {"Sec-Fetch-Site": "cross-site", "Sec-Fetch-Mode": "no-cors", "Sec-Fetch-Dest": "image"}
        self.assertEqual(get(self.port, url, headers=img)[0], 403)
        self.assertEqual(get(self.port, url, headers=dict(img, Referer="https://musicbrainz.org.evil.example/"))[0],
                         403)
        self.assertEqual(self.asked, [])
        self.assertEqual(get(self.port, url, headers=dict(img, Referer="https://musicbrainz.org/release/x"))[0], 200)
        self.assertEqual(get(self.port, url, headers={"Sec-Fetch-Site": "cross-site", "Sec-Fetch-Mode": "navigate"})[0],
                         200)
        self.assertEqual(get(self.port, url, headers={"Sec-Fetch-Site": "none", "Sec-Fetch-Mode": "navigate"})[0], 200)
        self.assertEqual(len(self.asked), 3)

    def test_dropped_ask_answered_429(self):
        self.srv.httpd.on_open = lambda kind, mbid: False
        status, body, headers = get(self.port, f"/openalbum?id={MBID}")
        self.assertEqual((status, headers.get("Retry-After")), (429, "10"))
        self.assertIn("Asked too soon", body)
        self.assertNotIn("Sent to TagMender", body)

    def test_nothing_reaches_the_app_after_stop(self):
        """A request in hand when stop() runs: answered 503, on_open never called after stop()."""
        httpd = self.srv.httpd
        result = {}
        httpd.gate.acquire()                                    # (the handler waits here, as if stop() were running)
        try:
            t = threading.Thread(target=lambda: result.update(r=get(self.port, f"/openalbum?id={MBID}")))
            t.start()
            time.sleep(0.2)
            httpd.stopped = True                                # (what stop() sets under the gate)
        finally:
            httpd.gate.release()
        t.join(5)
        self.assertEqual(result["r"][0], 503)
        self.assertEqual(self.asked, [])

    def test_stop_waits_for_on_open_in_hand(self):
        entered, release, calls = threading.Event(), threading.Event(), []

        def slow(kind, mbid):
            calls.append("in")
            entered.set()
            release.wait(5)
            calls.append("out")
        self.srv.httpd.on_open = slow
        t = threading.Thread(target=lambda: get(self.port, f"/openalbum?id={MBID}"))
        t.start()
        self.assertTrue(entered.wait(5))
        stopped = threading.Event()
        s = threading.Thread(target=lambda: (self.srv.stop(), calls.append("stopped"), stopped.set()))
        s.start()
        self.assertFalse(stopped.wait(0.3))                    # (stop() waits for the ask in hand)
        release.set()
        self.assertTrue(stopped.wait(5))
        s.join(5)
        t.join(5)
        self.assertEqual(calls, ["in", "out", "stopped"])

    def test_next_free_port_and_clean_stop(self):
        other = self.browser.LocalServer(lambda *a: None, self.port)        # (its port is taken: the next one)
        got = other.start()
        try:
            self.assertGreater(got, self.port)
            self.assertEqual(get(got, "/", host=f"127.0.0.1:{got}")[0], 200)
        finally:
            other.stop()
        thread = self.srv.thread
        self.srv.stop()
        self.assertFalse(self.srv.running)
        self.assertFalse(thread.is_alive())
        with self.assertRaises(OSError):
            get(self.port, "/")
        self.srv.stop()                                                      # (twice: nothing to do)
        with socket.socket() as s:                                           # (every port of the range taken)
            s.bind(("127.0.0.1", 0))
            s.listen()
            full = self.browser.LocalServer(lambda *a: None, s.getsockname()[1], tries=1)
            with self.assertRaises(OSError) as cm:
                full.start()
            self.assertIn("no free port", str(cm.exception))

    def test_failing_app_side_still_serves(self):
        def broken(kind, mbid):
            raise RuntimeError("boom")
        self.srv.on_open = broken
        self.srv.httpd.on_open = broken
        self.assertEqual(get(self.port, f"/openalbum?id={MBID}")[0], 500)
        self.assertEqual(get(self.port, "/")[0], 200)


# --------------------------------------------------------------------------
# the window's side
# --------------------------------------------------------------------------
class SearchHttp:
    """MusicBrainz's release and recording searches by ID."""

    def __init__(self):
        self.calls = []

    def get_json(self, url, host):
        self.calls.append((host, url))
        if host == "mb" and ("/ws/2/release/?query=" in url or "/ws/2/recording/?query=" in url):
            return {"releases": [{"id": MBID, "title": "Alpha", "media": [{"format": "CD", "track-count": 3}]}],
                    "recordings": []}
        raise AssertionError(f"unexpected request: {host} {url}")


@unittest.skipIf(QApplication is None, "PySide6 not installed")
class Window(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import gui  # noqa: F401
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        from gui import browser, versions
        self.browser, self.versions = browser, versions
        self.tmp = Path(tempfile.mkdtemp(prefix="tm-browser-"))
        self.http = FakeHttp()
        self.lookup = versions.Lookup(self.http, dict(mc.DEFAULT_CFG), None, use_discogs=False)
        self.patch = mock.patch.object(versions, "make_lookup", side_effect=lambda *_a: self.lookup)
        self.patch.start()
        files = [str(self.tmp / f"{i:02d}.flac") for i in (1, 2, 3)]
        self.album = {"source_dir": str(self.tmp), "status": "ready", "album_files": files,
                      "files": [{"src": f, "dst": f, "op": "tag", "diff": []} for f in files]}
        self.page = Page(self.album)
        self.page.show()
        self.sheets = []
        from gui import sheets
        sheets.shown.clear()

    def tearDown(self):
        for d in self.sheets:
            wait(self.app, d)
            d.close()
        self.patch.stop()
        bi = getattr(self.page, "browser_integration", None)
        if bi is not None:
            bi.stop()
        self.page.close()
        self.page.deleteLater()
        settle(self.app)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def opened(self, dlg):
        self.sheets.append(dlg)
        wait(self.app, dlg)
        return dlg

    def rows(self, dlg):
        proxy = dlg.table._proxy
        return [proxy.data(proxy.index(r, 0), Qt.UserRole) for r in range(proxy.rowCount())]

    # ---- Look Up CD --------------------------------------------------------
    def test_pasted_toc_said_then_looked_up_then_used(self):
        dlg = self.opened(self.browser.open_cd_lookup(self.page, toc=TOC.replace(" ", "+")))
        self.assertEqual(self.http.calls, [])                    # (nothing is sent before Look Up)
        self.assertIn(dlg.disc["id"], dlg.disc_id.text())             # (a line of its own, to copy)
        self.assertNotIn(dlg.disc["id"], dlg.disc_label.text())
        self.assertIn("the disc ID and its track layout", dlg.sent.text())
        self.assertTrue(dlg.look.isDefault() and not dlg.ok.isDefault())   # (Look Up: the next step)
        self.assertTrue(dlg.look.isEnabled() and not dlg.ok.isEnabled())
        dlg.look.click()
        wait(self.app, dlg)
        self.assertEqual([r["ref"] for r in self.rows(dlg)], [EXACT, CTDB_MB])
        self.assertIn("with this very disc ID", dlg.status.text())
        self.assertEqual(self.http.unexpected, [])
        self.assertTrue(dlg.ok.isEnabled())
        self.assertTrue(dlg.ok.isDefault() and not dlg.look.isDefault())   # (rows: Use This Release again)
        self.assertIs(dlg.focusWidget(), dlg.table)                  # (the results to choose from, not Look Up)
        dlg.ok.click()
        settle(self.app)
        key = _os.path.realpath(str(self.tmp))
        self.assertEqual([c["ref"] for c in self.page.forced[key]], [EXACT])
        self.assertEqual(self.page.previews, 1)

    def test_sent_follows_the_profile(self):
        cls = self.browser.CdSheet
        self.lookup.use_discogs = True                            # (it could ask Discogs: only what was said is)
        with mock.patch.object(cls, "providers", return_value=(True, False)):
            dlg = self.opened(self.browser.open_cd_lookup(self.page, toc=TOC))
            self.assertNotIn("Discogs", dlg.sent.text())
            dlg.look_up()
            wait(self.app, dlg)
        self.assertNotIn("discogs", {h for h, _u in self.http.calls})
        self.assertIn("Look Up did not say it would be asked", dlg.status.text())
        self.http.calls.clear()
        with mock.patch.object(cls, "providers", return_value=(True, True)):
            dlg.look_up()                                         # (Discogs turned on since: said first)
            self.assertEqual(self.http.calls, [])
            self.assertIn("services changed", dlg.status.text())
            self.assertIn("api.discogs.com", dlg.sent.text())
            dlg.look_up()
            wait(self.app, dlg)
        self.assertIn("discogs", {h for h, _u in self.http.calls})
        self.assertIn("discogs:701", [r["ref"] for r in self.rows(dlg)])

    def test_sent_reads_the_real_profile(self):
        import profiles as pf
        pf.save(pf.from_dict({"name": "NoDiscogs", "providers": {"discogs": {"enabled": False}}}))
        pf.save(pf.from_dict({"name": "NoMB", "providers": {"musicbrainz": {"enabled": False}}}))
        self.page.profile.addItems(["NoDiscogs", "NoMB"])
        self.page.profile.setCurrentText("NoDiscogs")
        dlg = self.opened(self.browser.open_cd_lookup(self.page, toc=TOC))
        self.assertEqual(dlg.said, (True, False))
        self.assertIn("MusicBrainz", dlg.sent.text())
        self.assertNotIn("Discogs", dlg.sent.text())
        self.page.profile.setCurrentText("NoMB")
        dlg.use_text()
        self.assertEqual(dlg.said, (False, True))
        self.assertIn("sends nothing", dlg.sent.text())

    def test_late_lookup_for_an_older_disc_ignored(self):
        gate = threading.Event()
        http = self.http
        real = http.get_json

        def slow(url, host):
            gate.wait(5)
            return real(url, host)
        http.get_json = slow
        dlg = self.opened(self.browser.open_cd_lookup(self.page, toc=TOC))
        dlg.look_up()
        settle(self.app)
        other = "1 3 40151 150 10151 20151"
        dlg.paste.setText(other)
        dlg.use_text()                                            # (a new disc while the old lookup waits)
        gate.set()
        wait(self.app, dlg)
        self.assertEqual(dlg.disc["toc"], other)
        self.assertEqual(self.rows(dlg), [])

    def test_newer_read_wins(self):
        gate = threading.Event()
        cd = self.browser.cdlookup
        other = "1 3 40151 150 10151 20151"

        def read_log(path):
            if path == "slow.log":
                gate.wait(5)
                return cd.from_text(TOC)
            return cd.from_text(other)
        with mock.patch.object(cd, "read_log", read_log):
            dlg = self.opened(self.browser.open_cd_lookup(self.page))
            dlg.use_log("slow.log")
            dlg.use_log("quick.log")
            self.assertTrue(self.wait_for(lambda: dlg.disc is not None))
            gate.set()
            wait(self.app, dlg)
        self.assertEqual(dlg.disc["toc"], other)

    def test_bad_toc_said(self):
        dlg = self.opened(self.browser.open_cd_lookup(self.page, toc="1 3 nonsense"))
        self.assertIsNone(dlg.disc)
        self.assertIn("not a table of contents", dlg.status.text())
        self.assertFalse(dlg.look.isEnabled())

    def test_rip_log(self):
        p = self.tmp / "Alpha.log"
        p.write_text(eac_log(data_track=True))
        on_ui = []
        real = self.browser.cdlookup.read_log

        def read_log(path):
            on_ui.append(threading.current_thread() is threading.main_thread())
            return real(path)
        with mock.patch.object(self.browser.cdlookup, "read_log", read_log):
            dlg = self.opened(self.browser.open_cd_lookup(self.page, log=str(p)))
        self.assertEqual(on_ui, [False])                          # (the log is read off the UI thread)
        self.assertEqual(dlg.disc["toc"], TOC)
        self.assertIn("from the rip log Alpha.log", dlg.disc_label.text())
        dlg.use_log(str(self.tmp / "missing.log"))
        wait(self.app, dlg)
        self.assertIn("could not be read", dlg.status.text())
        self.assertEqual(dlg.disc["toc"], TOC)                    # (the disc read before stays)
        dlg.choose_log()                                          # (offscreen: the picker is recorded)
        from gui import sheets
        self.assertIn(("open_file", "Open a Rip Log", ""), sheets.shown)

    def test_no_python_discid(self):
        with mock.patch.dict(sys.modules, {"discid": None}):
            dlg = self.opened(self.browser.open_cd_lookup(self.page))
            self.assertFalse(dlg.read_cd.isEnabled())
            self.assertFalse(dlg.no_drive.isHidden())
            self.assertIn("python-discid (with libdiscid) is not installed", dlg.no_drive.text())
            self.assertIn("setup:discid", dlg.no_drive.text())                # (Open Setup: its row)
            self.assertNotIn("pip", dlg.no_drive.text())                  # (no developer words in the sheet)
            dlg.read_drive()
            self.assertIn("python-discid (with libdiscid) is not installed", dlg.status.text())

    def test_drive_read_on_a_worker(self):
        m = fake_discid()
        on_ui = []
        real = m.read

        def read(*a, **k):
            on_ui.append(threading.current_thread() is threading.main_thread())
            return real(*a, **k)
        m.read = read
        with mock.patch.dict(sys.modules, {"discid": m}):
            dlg = self.opened(self.browser.open_cd_lookup(self.page))
            self.assertTrue(dlg.read_cd.isEnabled() and dlg.no_drive.isHidden())
            dlg.read_cd.click()
            wait(self.app, dlg)
        self.assertEqual(on_ui, [False])                          # (the drive is read off the UI thread)
        self.assertEqual(dlg.disc["toc"], TOC)
        self.assertIn("barcode 5013929020153", dlg.disc_label.text())
        self.assertEqual(self.http.calls, [])

    def test_submit_disc_id_asks_first(self):
        dlg = self.opened(self.browser.open_cd_lookup(self.page, toc=TOC))
        from gui import sheets
        with mock.patch.object(self.browser.QDesktopServices, "openUrl") as opened:
            dlg.submit.click()                                    # (offscreen: the sheet says no)
            self.assertEqual(opened.call_count, 0)
            kind, head, body = sheets.shown[-1]
            self.assertEqual(kind, "send")                        # (the sheet every send uses)
            self.assertIn(dlg.disc["id"], body)
            self.assertIn("TagMender itself sends nothing", body)
            self.assertIn("Opened in your browser:\n" + self.browser.cdlookup.submit_url(dlg.disc), body)
            from gui import account
            with mock.patch.object(account, "confirm_send", return_value=True):
                dlg.submit.click()
            self.assertEqual(opened.call_args[0][0].toString(), self.browser.cdlookup.submit_url(dlg.disc))

    def test_without_an_album(self):
        self.page.a = None
        dlg = self.opened(self.browser.open_cd_lookup(self.page, toc=TOC))
        dlg.look_up()
        wait(self.app, dlg)
        self.assertTrue(self.rows(dlg))
        self.assertFalse(dlg.ok.isEnabled())
        self.assertTrue(dlg.browser.isEnabled())                  # (Open in Browser still works)

    def test_commands(self):
        self.assertEqual(self.browser.COMMANDS["lookup_cd"][1], "Ctrl+K")
        from gui import actions
        mine = set(self.browser.COMMANDS)
        others = [v[1] for k, v in actions.COMMANDS.items() if k not in mine and isinstance(v[1], str) and v[1]]
        self.assertNotIn("Ctrl+K", others)
        ours = [v[1] for v in self.browser.COMMANDS.values() if v[1]]
        every = others + ours                                     # (as after actions.COMMANDS.update(...))
        self.assertEqual([k for k in ours if every.count(k) > 1], [])   # (none of ours is used twice)
        dlg = self.opened(self.browser.handlers(lambda: self.page)["lookup_cd"]())
        self.assertIs(self.page._cd_sheet, dlg)

    # ---- browser integration -----------------------------------------------
    def wait_for(self, cond, timeout=5):
        end = time.time() + timeout
        while time.time() < end and not cond():
            self.app.processEvents()
            time.sleep(0.01)
        return cond()

    def test_request_reaches_the_ui_thread(self):
        seen = []
        real = self.browser.open_release

        def record(page, kind, mbid):
            seen.append((threading.current_thread() is threading.main_thread(), kind, mbid))
            return self.opened(real(page, kind, mbid))
        self.versions.make_lookup.side_effect = None
        search_lookup = self.versions.Lookup(SearchHttp(), dict(mc.DEFAULT_CFG), None, use_discogs=False)
        with mock.patch.object(self.versions, "make_lookup", return_value=search_lookup), \
                mock.patch.object(self.browser, "open_release", record):
            bi = self.browser.BrowserIntegration(self.page)
            self.page.browser_integration = bi
            port = bi.start(free_port())
            result = {}
            t = threading.Thread(target=lambda: result.update(r=get(port, f"/openalbum?id={MBID}")))
            t.start()
            self.assertTrue(self.wait_for(lambda: seen))
            t.join(5)
        self.assertEqual(result["r"][0], 200)
        self.assertEqual(seen, [(True, "release", MBID)])
        self.assertEqual(self.page.went, ["Overview"])
        dlg = self.page._search_sheet
        self.assertEqual(dlg.query.text(), f"reid:{MBID}")
        self.assertEqual(search_lookup.http.calls, [])             # (a request sends nothing to MusicBrainz)
        self.assertEqual(self.rows(dlg), [])
        self.assertIn("A browser asked for this release. Search sends its ID to MusicBrainz; Use This "
                      "Release uses it for", dlg.status.text())
        self.assertIn(MBID, dlg.status.toolTip())                     # (whole, out of the short line)
        self.assertIn("Nothing is sent until you do", dlg.table.placeholder)   # (the empty table says what to do)
        self.assertTrue(dlg.go.isDefault() and dlg.go.property("role") == "primary")
        self.assertFalse(dlg.ok.isDefault())
        with mock.patch.object(self.versions, "make_lookup", return_value=search_lookup):
            dlg.go.click()                                         # (the person's Search sends it)
            wait(self.app, dlg)
        self.assertEqual([r["ref"] for r in self.rows(dlg)], [MBID])
        self.assertTrue(search_lookup.http.calls and all("release/?query=" in u for _h, u in search_lookup.http.calls))
        self.assertEqual(self.page.previews, 0)                    # (the person chooses; nothing planned)

    def test_repeated_asks_are_dropped(self):
        opened = []
        b = self.browser
        bi = b.BrowserIntegration(self.page)
        with mock.patch.object(b, "open_release", lambda *a: opened.append(a)):
            self.assertEqual([bi.ask("release", MBID) for _ in range(5)], [True, False, False, False, False])
            settle(self.app)
            self.assertEqual(len(opened), 1)
            bi._last -= b.ASK_GAP + 0.1                           # (one was dropped: the gap is longer now)
            self.assertFalse(bi.ask("release", MBID))
            bi._last -= b.ASK_BACKOFF                             # (after the longer gap: taken)
            self.assertTrue(bi.ask("release", MBID))
            bi._last -= b.ASK_GAP + 0.1
            self.assertFalse(bi.ask("release", MBID))             # (still the longer gap)
            bi._last -= b.ASK_QUIET                               # (quiet for a while: back to the short gap)
            self.assertTrue(bi.ask("release", MBID))
            bi._last -= b.ASK_GAP + 0.1
            self.assertTrue(bi.ask("release", MBID))
            settle(self.app)
        self.assertEqual(len(opened), 4)
        self.assertEqual(self.page.went, ["Overview"] * 4)

    def test_open_search_sheet_is_never_replaced(self):
        from gui import search
        lk = self.versions.Lookup(SearchHttp(), dict(mc.DEFAULT_CFG), None, use_discogs=False)
        with mock.patch.object(self.versions, "make_lookup", return_value=lk):
            mine = self.opened(search.open_search(self.page, None, ""))
            mine.query.setText("my own words")
            bi = self.browser.BrowserIntegration(self.page)
            bi._open("release", MBID)
            settle(self.app)
            self.assertIs(self.page._search_sheet, mine)
            self.assertTrue(mine.isVisible())
            self.assertEqual(mine.query.text(), "my own words")
            self.assertIn(f"A browser asked to open release {MBID}", mine.status.text())
            self.assertEqual(self.page.went, [])                   # (the window was not taken over)
            self.assertEqual(lk.http.calls, [])
            mine.close()
            settle(self.app)
            bi._open("release", MBID)                             # (closed: the ask opens a sheet)
            settle(self.app)
            dlg = self.page._search_sheet
            self.sheets.append(dlg)
            self.assertIsNot(dlg, mine)
            self.assertEqual(dlg.query.text(), f"reid:{MBID}")

    def test_recording_opens_the_recordings_tab(self):
        lk = self.versions.Lookup(SearchHttp(), dict(mc.DEFAULT_CFG), None, use_discogs=False)
        with mock.patch.object(self.versions, "make_lookup", return_value=lk):
            dlg = self.opened(self.browser.open_release(self.page, "recording", MBID))
            self.assertEqual((dlg.kind(), dlg.query.text()), ("recording", f"rid:{MBID}"))
            self.assertEqual(lk.http.calls, [])                    # (the tab changed without searching)
            self.assertIn("Search sends its ID", dlg.status.text())
            dlg.search()
            wait(self.app, dlg)
        self.assertTrue(all("recording/?query=" in u for _h, u in lk.http.calls) and lk.http.calls)
        self.assertIsNone(self.browser.open_release(self.page, "release", "not-an-id"))

    def test_preferences_start_and_stop(self):
        from gui.common import _set_gui_pref
        port = free_port()
        _set_gui_pref(self.browser.PREF_ON, False)
        _set_gui_pref(self.browser.PREF_PORT, port)
        bi = self.browser.install(self.page)
        self.assertIs(self.page.browser_integration, bi)
        self.assertIsNone(bi.server)                               # (off unless turned on)
        holder = QWidget()
        form = QFormLayout(holder)
        box = self.browser.add_preferences(form, self.page)
        self.assertFalse(box._check.isChecked())
        self.assertEqual(box._spin.value(), port)
        box._check.setChecked(True)
        self.assertEqual(bi.port, port)
        self.assertIn(f"Listening on 127.0.0.1:{port}", box._caption.text())
        self.assertEqual(self.browser.settings(), (True, port))
        self.assertEqual(get(port, "/")[0], 200)
        box._check.setChecked(False)
        self.assertIsNone(bi.server)
        self.assertIn("Off: no requests are taken.", box._caption.text())
        with self.assertRaises(OSError):
            get(port, "/")
        box._check.setChecked(True)                                # (on again, then a new port)
        new = free_port()
        box._spin.setValue(new)
        box._spin.editingFinished.emit()
        self.assertEqual(bi.port, new)
        self.assertEqual(self.browser.settings(), (True, new))
        self.assertEqual(get(new, "/")[0], 200)
        with self.assertRaises(OSError):
            get(port, "/")
        with socket.socket() as s:                                 # (no free port)
            s.bind(("127.0.0.1", 0))
            s.listen()
            self.assertEqual(bi.start(s.getsockname()[1], tries=1), 0)
            self.assertIsNone(bi.server)
            self.assertIn("Not listening: no free port", box._caption.text())
        bi.stop()                                                  # (off: the old failure is no news)
        self.assertEqual(bi.status(), "Off: no requests are taken.")
        self.assertIn("Off: no requests are taken.", box._caption.text())
        with socket.socket() as s:                                 # (a failed start, then the checkbox off)
            s.bind(("127.0.0.1", 0))
            s.listen()
            _set_gui_pref(self.browser.PREF_PORT, s.getsockname()[1])
            bi.start(s.getsockname()[1], tries=1)
            self.assertIn("Not listening", box._caption.text())
            box._check.setChecked(False)
            self.assertEqual(bi.status(), "Off: no requests are taken.")
            self.assertIn("Off: no requests are taken.", box._caption.text())
        from PySide6.QtCore import QEvent
        holder.deleteLater()
        self.app.sendPostedEvents(None, QEvent.DeferredDelete)
        with self.assertRaises(RuntimeError):                      # (Preferences closed: the label is gone)
            box._caption.text()
        hooked = []
        with mock.patch.object(sys, "excepthook", lambda *a: hooked.append(a)):
            bi.start(port)                                         # (no stale label: nothing raised)
            bi.stop()
        self.assertEqual(hooked, [])
        _set_gui_pref(self.browser.PREF_PORT, "junk")
        self.assertEqual(self.browser.settings()[1], self.browser.DEFAULT_PORT)


if __name__ == "__main__":
    unittest.main(argv=[sys.argv[0]])
