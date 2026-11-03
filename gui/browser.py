"""gui/browser.py - Releases that come from outside the window: Picard's Lookup CD and Picard's
browser integration (the "tagger" buttons on musicbrainz.org).

- CdSheet (File > Look Up CD…, Ctrl+K): the disc's table of contents from the
  CD in a drive (python-discid, optional), an EAC/XLD rip log or a pasted TOC (cdlookup); Look
  Up, after the sheet has said what is sent, lists the releases with that disc in the search
  sheet's table (gui.search); Use This Release previews the selected album again with it.
- LocalServer: a small HTTP server on 127.0.0.1 only (port 8000, or the next free one), off
  unless turned on in Preferences > General. GET /openalbum?id=<MBID> (and Picard's /?id=,
  /opennat?id=) opens the search sheet with that ID filled in; Search sends it to MusicBrainz and
  the person chooses the release. A request itself sends, plans and writes nothing, and a search
  sheet already open is never replaced (its words and results stay; a note says what was asked).
  Requests naming another host (DNS rebinding), or with any method but GET, are refused, and so
  are a web page's requests that carry another site's Origin, or that the browser marks as made
  from another site (Sec-Fetch-Site) without being a navigation or coming from musicbrainz.org.
  As in Picard, a plain GET without those headers is taken from anyone on this computer, so any
  page may still ask: asks closer than ASK_GAP apart are answered 429, and after one is refused
  the gap is ASK_BACKOFF until things are quiet again. It runs on its own threads; stop() shuts
  it down, and once it returns no request reaches the app.
- BrowserIntegration: the server for a window, started and stopped from the preferences, its
  requests handed to the UI thread (a queued Qt signal).

Entry points: open_cd_lookup(page, album=None, toc=None, log=None), COMMANDS / MENU /
handlers(ov) (gui.search's form), open_release(page, kind, mbid), install(win) -> the window's
BrowserIntegration (win.browser_integration), add_preferences(form, win) (the General pane's
rows). `page` is the Overview (selected(), forced, preview(), profile, win)."""
from __future__ import annotations

import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from PySide6.QtCore import QObject, Qt, QUrl, Signal, Slot
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QHBoxLayout, QLabel, QLineEdit, QPushButton,
                               QSpinBox, QVBoxLayout, QWidget)

import cdlookup
from version import VERSION

from . import icons, search, sheets, style, versions
from .common import _gui_prefs, _set_gui_pref, load_profile
from .i18n import N_, tr
from .work import start

# --------------------------------------------------------------------------
# Look Up CD
# --------------------------------------------------------------------------
COMMANDS = {
    "lookup_cd": (N_("Look Up &CD…"), "Ctrl+K", "disc-3",
                  N_("Find the release of a CD in the drive, a rip log or a table of contents")),
}
MENU = tuple(COMMANDS)
# no python-discid: the plain words (Help, docs/FIRST_RUN.md, says how to install it)
NO_DISCID = N_("Reading a CD from the drive is not available: python-discid (with libdiscid) is not installed; "
               "Setup says how to install it. A rip log or a pasted table of contents works without it.")


def handlers(ov):
    """The commands' actions; ov() gives the Overview."""
    return {"lookup_cd": lambda: open_cd_lookup(ov())}


def open_cd_lookup(page, album=None, toc=None, log=None):
    """The Look Up CD sheet for the selected album (or `album`); a TOC text or a log path is read
    at once (the lookup itself waits for Look Up)."""
    album = search._album(page, album)
    dlg = search._keep(page, "_cd_sheet", CdSheet(page, album))
    if log:
        dlg.use_log(log)
    elif toc:
        dlg.paste.setText(toc)
        dlg.use_text()
    return dlg


def _open_file(parent, title, filters) -> str:
    """A window-modal file picker (gui.sheets has the folder and save-as ones): the file chosen,
    or "" (offscreen: recorded in sheets.shown, "")."""
    from PySide6.QtWidgets import QDialog, QFileDialog
    dlg = sheets._file_dialog(parent, title, "")
    dlg.setFileMode(QFileDialog.ExistingFile)
    dlg.setNameFilter(filters)
    dlg.setLabelText(QFileDialog.Accept, tr("Open"))
    if not sheets.blocking():
        sheets.shown.append(("open_file", title, ""))
        dlg.deleteLater()
        return ""
    ok = dlg.exec() == QDialog.Accepted and dlg.selectedFiles()
    out = dlg.selectedFiles()[0] if ok else ""
    dlg.deleteLater()
    return out


def sent_words(mb=True, discogs=False) -> str:
    """What Look Up sends, as a short list (the disc ID itself is in the headline)."""
    if not mb:
        return tr("MusicBrainz is off in this profile, so Look Up sends nothing.")
    lines = [tr("Sent to MusicBrainz (musicbrainz.org) and the CUETools database (db.cue.tools): the disc ID and "
                "its track layout.")]
    if discogs:
        lines.append(tr("Sent to Discogs (api.discogs.com): the Discogs release IDs found, with your Discogs "
                        "token."))
    lines.append(tr("Nothing else."))
    return "\n".join(lines)


class CdSheet(search._Base):
    STATUS_NAME = N_("Lookup status")
    AGAIN = N_("Choose Look Up to try again.")

    def __init__(self, page, album=None):
        name = Path(album["source_dir"]).name if album else ""
        hint = (tr("Find the release of {name} from its CD. Read the disc in the drive, open its rip log or paste "
                   "its table of contents, then look it up.").format(name=name) if album else
                tr("Find a CD's release on MusicBrainz. Select an album in Overview to use a release for it."))
        super().__init__(page, album, tr("Look Up CD"), hint, (versions.COLS[0], cdlookup.FIT_COL) + versions.COLS[1:],
                         tr("Releases with this disc"))
        self.disc, self.reader, self.said = None, None, (True, True)
        # the three ways in: the drive, a rip log, a pasted TOC
        bar = QHBoxLayout()
        bar.setSpacing(style.SPACE["s"])
        self.drive = QComboBox()
        self.drive.setAccessibleName(tr("CD drive"))
        self.drive.setSizeAdjustPolicy(QComboBox.AdjustToContents)
        self.read_cd = QPushButton(tr("Read CD"))
        self.read_cd.setIcon(icons.icon("disc-3", "ink"))
        self.read_cd.setAccessibleName(tr("Read the CD in the drive"))
        self.read_cd.setAutoDefault(False)
        self.read_cd.clicked.connect(self.read_drive)
        self.log_button = QPushButton(tr("Open Rip Log…"))
        self.log_button.setIcon(icons.icon("folder-open", "ink"))
        self.log_button.setAccessibleName(tr("Open an EAC or XLD rip log"))
        self.log_button.setAutoDefault(False)
        self.log_button.clicked.connect(self.choose_log)
        bar.addWidget(self.drive)
        bar.addWidget(self.read_cd)
        bar.addWidget(self.log_button)
        self.paste = QLineEdit()
        self.paste.setPlaceholderText(tr("Or paste a table of contents: 1 12 242457 150 17437 …"))
        self.paste.setAccessibleName(tr("Table of contents"))
        self.paste.setClearButtonEnabled(True)
        self.paste.returnPressed.connect(self.use_text)
        bar.addWidget(self.paste, 1)
        self.top.addLayout(bar)
        from . import setup
        self.no_drive = QLabel(setup.rich(tr(NO_DISCID), "discid"))     # (with Open Setup: its row)
        self.no_drive.setTextFormat(Qt.RichText)
        self.no_drive.setTextInteractionFlags(Qt.TextBrowserInteraction)
        self.no_drive.setAccessibleName(tr(NO_DISCID))
        self.no_drive.linkActivated.connect(lambda link: (link.startswith("setup:") and self.reject(),
                                                          setup.follow(page, link)))
        self.no_drive.setProperty("role", "caption")
        self.no_drive.setWordWrap(True)
        self.top.addWidget(self.no_drive)
        # the disc read, what Look Up sends, and Look Up
        row = QHBoxLayout()
        row.setSpacing(style.SPACE["s"])
        col = QVBoxLayout()
        col.setSpacing(2)
        self.disc_label = QLabel("")
        self.disc_label.setProperty("role", "headline")
        self.disc_label.setWordWrap(True)
        self.disc_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.disc_id = QLabel("")
        self.disc_id.setProperty("role", "mono")
        self.disc_id.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.disc_id.setAccessibleName(tr("Disc ID"))
        self.sent = QLabel("")
        self.sent.setProperty("role", "caption")             # (what is sent: a group of its own, under the disc)
        self.sent.setWordWrap(True)
        self.sent.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.sent.setAccessibleName(tr("What Look Up sends"))
        col.addWidget(self.disc_label)
        col.addWidget(self.disc_id)
        col.addSpacing(style.SPACE["s"])
        col.addWidget(self.sent)
        row.addLayout(col, 1)
        self.look = QPushButton(tr("Look Up"))
        self.look.setIcon(icons.icon("search", "ink"))
        self.look.setAccessibleName(tr("Look up the disc on MusicBrainz"))
        self.look.setAutoDefault(False)
        self.look.clicked.connect(self.look_up)
        row.addWidget(self.look, 0, Qt.AlignTop)
        self.top.addLayout(row)
        self.submit = QPushButton(tr("Submit Disc ID…"))
        self.submit.setAccessibleName(tr("Submit the disc ID to MusicBrainz in the browser"))
        self.submit.setAutoDefault(False)
        self.submit.clicked.connect(self.submit_disc)
        self.button_bar.insertWidget(1, self.submit)
        self.fill_drives()
        self.show_disc()
        self.say(tr("Read the CD, open its rip log or paste its table of contents."))
        self.paste.setFocus()

    # ---- the disc ----------------------------------------------------------
    def fill_drives(self):
        names = cdlookup.drives()
        self.drive.clear()
        for d in names:
            self.drive.addItem(d, d)
        can = cdlookup.can_read_drive()
        self.drive.setVisible(can and len(names) > 1)        # (one drive: nothing to choose)
        self.read_cd.setEnabled(can)
        self.read_cd.setToolTip("" if can else tr(NO_DISCID))
        self.no_drive.setVisible(not can)

    def show_disc(self):
        d = self.disc
        self.disc_label.setVisible(d is not None)
        self.disc_id.setVisible(d is not None)
        self.sent.setVisible(d is not None)
        self.look.setEnabled(d is not None)
        self.submit.setEnabled(d is not None)
        if d is None:
            return
        self.disc_label.setText(cdlookup.describe(d))
        self.disc_id.setText(tr("Disc ID {0}").format(d['id']))     # (a line of its own, to select and copy)
        self.disc_label.setAccessibleName(tr("Disc: {0}, {1}").format(d['id'], cdlookup.describe(d)))
        self.said = self.providers()
        self.sent.setText(sent_words(*self.said))

    def providers(self) -> tuple:
        """(MusicBrainz on, Discogs on) in the page's profile, for what Look Up says it sends (both
        on when the profile cannot be read: the sheet then says more, never less)."""
        try:
            prof = load_profile(search._profile(self.page))
            return bool(prof.provider_on("musicbrainz")), bool(prof.provider_on("discogs"))
        except Exception:
            return True, True

    def set_disc(self, d):
        """A disc was read: shown with what Look Up sends; nothing is sent yet."""
        self.disc = d
        self.model.set_rows([])
        self.show_disc()
        self.update_buttons()
        self.say(tr("Look Up finds the releases with this disc."))
        self.look.setFocus()

    def _read(self, fn, busy):
        """Read a disc on a worker (the drive and the disk may be slow); a newer read wins."""
        self.stop()
        self.say(busy)
        me = None

        def current():
            return me is not None and me is self.reader

        def done(d):
            if current():
                self.set_disc(d)

        def failed(msg):
            if current():
                self.say(msg.strip().splitlines()[0] if msg.strip() else tr("The disc could not be read."))

        def work(_w):
            try:
                return fn()
            except cdlookup.CdError as exc:
                raise RuntimeError(str(exc)) from None
        me = self.reader = start(self, work, done, None, failed)

    def read_drive(self):
        if not cdlookup.can_read_drive():
            self.say(tr(NO_DISCID))
            return
        device = self.drive.currentData() or None
        self._read(lambda: cdlookup.read_drive(device), tr("Reading the CD…"))

    def choose_log(self):
        path = _open_file(self, tr("Open a Rip Log"), tr("Rip logs (*.log *.txt);;All files (*)"))
        if path:
            self.use_log(path)

    def use_log(self, path):
        self._read(lambda: cdlookup.read_log(path), tr("Reading the rip log…"))

    def use_text(self):
        try:
            d = cdlookup.from_text(self.paste.text())
        except cdlookup.CdError as exc:
            self.say(str(exc))
            return
        self.set_disc(d)

    # ---- the lookup --------------------------------------------------------
    def first_step(self):
        look = getattr(self, "look", None)
        if look is None or getattr(self, "disc", None) is None or self.model.rowCount():
            return None
        return look

    def look_up(self):
        d = self.disc
        if d is None:
            return
        if self.providers() != self.said:                  # (the profile changed: say what is sent now)
            self.show_disc()
            self.say(tr("The profile's services changed. Check what Look Up sends, then choose Look Up again."))
            return
        discogs = self.said[1]
        self.run(lambda lk, w: cdlookup.lookup(lk, d, cancel=lambda: w.cancelled, discogs=discogs), self.found,
                 tr("Looking up the disc on MusicBrainz and the CUETools database…"))

    def found(self, res):
        if res.get("disc") is not self.disc:
            return                                         # (an older disc's lookup, answered late)
        rows = res["rows"]
        self.model.set_rows(rows)
        search._fit_columns(self.table)
        self.say(cdlookup.result_text(res) + (tr(" Select one, then Use This Release.") if rows and self.album else ""))
        if rows:
            self.table.selectRow(0)
            self.table.setFocus()                          # (the choice is next: Use This Release the one accent)
        self.update_buttons()

    def submit_disc(self):
        """Picard's Submit disc ID: the browser opens MusicBrainz's attach page, after a sheet that
        says exactly what that page is given."""
        d = self.disc
        if d is None:
            return
        from . import account
        url = cdlookup.submit_url(d)
        if account.confirm_send(self, tr("Open MusicBrainz to submit this disc ID?"),
                                tr("Your browser opens this address on musicbrainz.org, with the disc ID {0}, its {1} "
                                   "tracks and its table of contents in it. There you choose the release it belongs "
                                   "to and sign in; TagMender itself sends nothing.").format(d['id'], d['tracks']),
                                tr("Opened in your browser:") + "\n" + url, tr("Open MusicBrainz")):
            QDesktopServices.openUrl(QUrl(url))

    def stop(self):
        super().stop()
        if getattr(self, "reader", None) is not None:
            self.reader.cancelled = True
            self.reader = None


# --------------------------------------------------------------------------
# Browser integration: the server
# --------------------------------------------------------------------------
HOST = "127.0.0.1"                   # (never every address: only this computer may ask)
DEFAULT_PORT = 8000                  # (Picard's: musicbrainz.org's tagger buttons ask it)
PORT_TRIES = 100                     # a taken port: the next one, this many at most
PREF_ON, PREF_PORT = "browser_integration", "browser_integration_port"
SERVER = f"TagMender/{VERSION}"
_MBID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I)
# a web page may ask from these sites only (a request a page of another site makes carries its Origin)
_ORIGIN = re.compile(r"^https?://(?:[^./]+\.)*musicbrainz\.org(?::\d+)?$", re.I)
_REFERER = re.compile(r"^https?://(?:[^./]+\.)*musicbrainz\.org(?::\d+)?(?:/|$)", re.I)


class _Handler(BaseHTTPRequestHandler):
    server_version = SERVER
    sys_version = ""
    timeout = 10                     # (a client that never finishes its request is dropped)

    def log_message(self, fmt, *args):   # (no access log: the address and request stay private)
        pass

    def _answer(self, code, text, extra=()):
        body = (text + "\n").encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        for k, v in extra:
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _host_ok(self) -> bool:
        """The Host header names this server: 127.0.0.1 or localhost, at its port. Another name
        is a page of another site that made its name point here (DNS rebinding)."""
        host = (self.headers.get("Host") or "").strip().lower()
        port = self.server.server_address[1]
        return host in {f"127.0.0.1:{port}", f"localhost:{port}"}

    def _fetch_ok(self) -> bool:
        """What the browser says about who made the request (Sec-Fetch-*, sent by current
        browsers): a page of another site may only navigate here (a link the person followed),
        unless it is musicbrainz.org's (its Referer). No such header: an older browser or another
        program, taken as Picard does."""
        site = (self.headers.get("Sec-Fetch-Site") or "").strip().lower()
        if site not in {"cross-site", "same-site"}:
            return True
        if (self.headers.get("Sec-Fetch-Mode") or "").strip().lower() == "navigate":
            return True
        return bool(_REFERER.match((self.headers.get("Referer") or "").strip()))

    def _refuse_method(self):
        self._answer(405, tr("Only GET is answered here."), (("Allow", "GET"),))

    do_POST = do_PUT = do_DELETE = do_PATCH = do_OPTIONS = do_HEAD = do_CONNECT = do_TRACE = _refuse_method

    def do_GET(self):
        if not self._host_ok():
            self._answer(403, tr("Ask 127.0.0.1 or localhost."))
            return
        origin = self.headers.get("Origin")
        cors = ()
        if origin is not None:
            if not _ORIGIN.match(origin.strip()):
                self._answer(403, tr("Only musicbrainz.org may ask from a web page."))
                return
            cors = (("Access-Control-Allow-Origin", re.sub(r"[\r\n]", "", origin.strip())), ("Vary", "Origin"))
        if not self._fetch_ok():
            self._answer(403, tr("Only musicbrainz.org may ask from a web page."))
            return
        url = urlparse(self.path)
        args = parse_qs(url.query)
        action = url.path.rstrip("/") or "/"
        if action == "/" and "id" not in args:
            self._answer(200, SERVER, cors)                 # (what Picard says: who is listening)
            return
        kind = {"/": "release", "/openalbum": "release", "/opennat": "recording"}.get(action)
        if kind is None:
            self._answer(404, tr("Unknown action."), cors)
            return
        mbid = (args.get("id") or [""])[0].strip()
        if not mbid:
            self._answer(400, tr('Missing parameter "id".'), cors)
            return
        if not _MBID.fullmatch(mbid):
            self._answer(400, tr('"id" is not a valid MBID.'), cors)
            return
        try:
            with self.server.gate:                          # (stop() waits for this, then no more)
                gone = self.server.stopped
                taken = None if gone else self.server.on_open(kind, mbid.lower())
        except Exception:                                   # (the app's side failed: say so, keep serving)
            self._answer(500, tr("TagMender could not open it."), cors)
            return
        if gone:
            self._answer(503, tr("TagMender stopped listening."), cors)
        elif taken is False:                                # (asked again too soon: dropped)
            self._answer(429, tr("Asked too soon. Wait a few seconds, then try again."),
                         cors + (("Retry-After", "10"),))
        else:
            self._answer(200, tr("Sent to TagMender: {0}. You can close this tab.").format(mbid.lower()), cors)


class _Server(ThreadingHTTPServer):
    daemon_threads = True            # (not joined on close: a slow client never holds up the UI thread;
    #                                   the gate and `stopped` keep its request from reaching the app)
    # (SO_REUSEADDR on Linux: a port left in TIME_WAIT by our own last run is taken again; a port
    # another program listens on still is not, so it is never shared)
    allow_reuse_address = True


class LocalServer:
    """The browser-integration server, stdlib only: on_open(kind, mbid) is called on the server's
    thread for each request it accepts (kind "release" or "recording"); returning False drops the
    ask (answered 429). It is never called once stop() has returned."""

    def __init__(self, on_open, port=DEFAULT_PORT, tries=PORT_TRIES):
        self.on_open, self.want, self.tries = on_open, int(port), int(tries)
        self.httpd, self.thread = None, None

    @property
    def port(self) -> int:
        return self.httpd.server_address[1] if self.httpd else 0

    @property
    def running(self) -> bool:
        return self.httpd is not None

    def start(self) -> int:
        """Listen on 127.0.0.1 at the port asked for, or the next free one; the port. OSError
        when none of them is free."""
        self.stop()
        last = None
        for port in range(self.want, min(self.want + self.tries, 65536)):
            try:
                httpd = _Server((HOST, port), _Handler)
            except OSError as exc:
                last = exc
                continue
            httpd.on_open, httpd.gate, httpd.stopped = self.on_open, threading.Lock(), False
            self.httpd = httpd
            self.thread = threading.Thread(target=httpd.serve_forever, kwargs={"poll_interval": 0.2},
                                           name="tagmender-browser", daemon=True)
            self.thread.start()
            return port
        raise OSError(f"no free port from {self.want} to {min(self.want + self.tries, 65536) - 1}"
                      + (f" ({last.strerror})" if last is not None and last.strerror else ""))

    def stop(self):
        """Stop listening and wait for the server's thread. A request still being answered gets
        503 and no longer reaches on_open (one already inside on_open finishes first)."""
        httpd, thread, self.httpd, self.thread = self.httpd, self.thread, None, None
        if httpd is None:
            return
        with httpd.gate:
            httpd.stopped = True
        httpd.shutdown()
        httpd.server_close()
        if thread is not None:
            thread.join(5)


# --------------------------------------------------------------------------
# Browser integration: the window's side
# --------------------------------------------------------------------------
OFF = N_("Off: no requests are taken.")
ASK_GAP = 1.0                        # seconds: asks closer together than this are dropped
ASK_BACKOFF = 10.0                   # seconds between asks once one was dropped, until ASK_QUIET passes
ASK_QUIET = 60.0


def open_release(page, kind, mbid):
    """A release (or recording) a browser asked for: the search sheet for the selected album,
    with its ID filled in but not searched. Search (or Return) sends the ID to MusicBrainz, and the
    person chooses Use This Release; nothing is sent or planned before that. A search sheet that
    is already open is left as it is (its words, results and search in flight are the person's):
    its status line says what was asked, and None is returned."""
    if not _MBID.fullmatch(mbid or ""):
        return None
    want = "recording" if kind == "recording" else "release"
    word = tr("recording") if want == "recording" else tr("release")
    old = _open_sheet(page)
    if old is not None:
        old.say(tr("A browser asked to open {want} {mbid}. Close this sheet, then ask again from the browser, "
                   "or search for {0}:{mbid} here.").format('rid' if want == 'recording' else 'reid', want=word,
                                                         mbid=mbid))
        return None
    dlg = search.open_search(page, None, "")               # (empty: the sheet searches nothing)
    dlg.kinds.blockSignals(True)                           # (a new tab would search at once)
    try:
        dlg.kinds.setCurrentIndex([k for k, _l in search.SEARCH_KINDS].index(want))
    finally:
        dlg.kinds.blockSignals(False)
    dlg._artist = None
    dlg.query.setText(f"{'rid' if want == 'recording' else 'reid'}:{mbid}")
    dlg.query.setToolTip(tr("{0}: MusicBrainz's search for one {want} by its ID").format(
        'rid' if want == 'recording' else 'reid', want=word))
    dlg.model.set_rows([], dlg.cols(want))
    dlg.table.placeholder = tr("Press Search to look up this {want} on MusicBrainz. Nothing is sent until you "
                               "do.").format(want=word)
    dlg.update_buttons()
    name = Path(dlg.album["source_dir"]).name if dlg.album else ""
    # (the ID itself is in the search field, and whole in the status line's tooltip: the line stays short)
    dlg.say((tr("A browser asked for this {want}. Search sends its ID to MusicBrainz; Use This Release uses it "
                "for {name}.") if name else
             tr("A browser asked for this {want}. Search sends its ID to MusicBrainz; select an album in "
                "Overview to use it.")).format(want=word, name=name))
    dlg.status.setToolTip(tr("The {want} the browser asked for: {mbid}").format(want=word, mbid=mbid))
    dlg.go.setFocus()
    return dlg


def _open_sheet(page):
    """The page's search sheet when it is on screen, else None."""
    dlg = getattr(page, "_search_sheet", None)
    try:
        return dlg if dlg is not None and dlg.isVisible() else None
    except RuntimeError:                                    # (deleted with its window)
        return None


def settings() -> tuple:
    """(on, port) from the app's settings (gui.json)."""
    prefs = _gui_prefs()
    try:
        port = int(prefs.get(PREF_PORT, DEFAULT_PORT))
    except (TypeError, ValueError):
        port = DEFAULT_PORT
    return bool(prefs.get(PREF_ON)), port if 1024 <= port <= 65535 else DEFAULT_PORT


class BrowserIntegration(QObject):
    """The window's server: apply() starts or stops it as the settings say; a request opens the
    release on the UI thread (the signal is queued: the server's thread touches no widget)."""

    asked = Signal(str, str)
    changed = Signal(str)                                   # (the status words, for Preferences)

    def __init__(self, win, page=None):
        super().__init__(win)
        self.win, self._page = win, page
        self.server, self.error = None, ""
        self._last = None                                   # (when the last ask was taken, monotonic)
        self._gap = ASK_GAP                                 # (longer once an ask was dropped)
        self._lock = threading.Lock()                       # (asks come on the server's threads)
        self.asked.connect(self._open, Qt.QueuedConnection)

    def page(self):
        if self._page is not None:
            return self._page
        return (getattr(self.win, "pages", None) or {}).get("Overview")

    def apply(self) -> str:
        on, port = settings()
        if not on:
            self.stop()
        elif self.server is None or self.server.want != port:
            self.start(port)
        return self.status()

    def start(self, port=DEFAULT_PORT, tries=PORT_TRIES) -> int:
        """Listen at `port` or the next free one (of `tries`): the port, or 0 (self.error says why)."""
        self.stop()
        self.error = ""
        srv = LocalServer(self.ask, port, tries)
        try:
            got = srv.start()
        except OSError as exc:
            self.error = str(exc)
            self.changed.emit(self.status())
            return 0
        self.server = srv
        self.changed.emit(self.status())
        return got

    def stop(self):
        before = self.status()
        self.error = ""                                     # (off now: an old start's failure is no news)
        if self.server is not None:
            self.server.stop()
            self.server = None
        if self.status() != before:
            self.changed.emit(self.status())

    def ask(self, kind, mbid) -> bool:
        """A request from the server's thread: handed to the UI thread, or False when it comes too
        soon after the last one (a page asking again and again: once is enough)."""
        with self._lock:
            now = time.monotonic()
            if self._last is not None:
                since = now - self._last
                if since < self._gap:
                    self._gap = ASK_BACKOFF                 # (dropped: ask less often from now on)
                    return False
                if since >= ASK_QUIET:
                    self._gap = ASK_GAP
            self._last = now
        self.asked.emit(kind, mbid)
        return True

    @property
    def port(self) -> int:
        return self.server.port if self.server else 0

    def status(self) -> str:
        if self.server is not None:
            moved = tr(" ({want} was taken)").format(want=self.server.want) \
                if self.server.port != self.server.want else ""
            return tr("Listening on {HOST}:{port}{moved}. Only this computer can reach it.").format(
                HOST=HOST, port=self.server.port, moved=moved)
        if self.error:
            return tr("Not listening: {error}.").format(error=self.error)
        return tr(OFF)

    @Slot(str, str)
    def _open(self, kind, mbid):
        page, win = self.page(), self.win
        if page is None:
            return
        if _open_sheet(page) is not None:                   # (the person's sheet: noted there, nothing taken over)
            open_release(page, kind, mbid)
            return
        if hasattr(win, "go"):
            win.go("Overview")
        if hasattr(win, "isMinimized") and win.isMinimized():
            win.showNormal()
        if hasattr(win, "raise_"):
            win.raise_()
            win.activateWindow()
        open_release(page, kind, mbid)


def install(win, page=None) -> BrowserIntegration:
    """The window's browser integration (win.browser_integration), started when Preferences
    turned it on; stopped when the application quits."""
    bi = BrowserIntegration(win, page)
    win.browser_integration = bi
    bi.apply()
    app = QApplication.instance()
    if app is not None:
        app.aboutToQuit.connect(bi.stop)
    return bi


def add_preferences(form, win):
    """Preferences > General: Browser integration, on or off, and its port (saved at once, the
    server started or stopped with it). Returns the row's widget."""
    on, port = settings()
    box = QWidget()
    v = QVBoxLayout(box)
    v.setContentsMargins(0, 0, 0, 0)
    v.setSpacing(2)
    line = QHBoxLayout()
    line.setSpacing(style.SPACE["s"])
    check = QCheckBox(tr("Open releases from the browser"))
    check.setAccessibleName(tr("Open releases from the browser"))
    check.setToolTip(tr("musicbrainz.org's tagger buttons open the release in TagMender"))
    check.setChecked(on)
    spin = QSpinBox()
    spin.setRange(1024, 65535)
    spin.setValue(port)
    spin.setAccessibleName(tr("Browser integration port"))
    spin.setPrefix(tr("Port "))
    spin.setFixedWidth(spin.fontMetrics().horizontalAdvance(tr("Port ") + "65535") + 48)   # (+ padding, arrows)
    spin.setEnabled(on)
    line.addWidget(check)
    line.addWidget(spin)
    line.addStretch(1)
    v.addLayout(line)
    caption = QLabel("")
    caption.setProperty("role", "caption")
    caption.setWordWrap(True)
    v.addWidget(caption)
    bi = getattr(win, "browser_integration", None)

    def show(text=None):
        live = text or (bi.status() if bi is not None else tr(OFF))
        try:
            caption.setText(tr("Only requests from this computer (127.0.0.1) are taken; a release opens in the search "
                               "sheet for you to choose. ") + live)
        except RuntimeError:                                # (Preferences closed: the label is gone)
            pass

    def changed(*_):
        spin.setEnabled(check.isChecked())                  # (a port matters only while it is on)
        _set_gui_pref(PREF_ON, bool(check.isChecked()))
        _set_gui_pref(PREF_PORT, int(spin.value()))
        show(bi.apply() if bi is not None else None)
    check.toggled.connect(changed)
    spin.editingFinished.connect(changed)
    if bi is not None:
        bi.changed.connect(show)
    show()
    from .preferences import _row_label
    form.addRow(_row_label(tr("Browser"), box), box)
    box._check, box._spin, box._caption = check, spin, caption
    return box
