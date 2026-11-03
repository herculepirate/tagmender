"""gui/setup.py - The Setup page and the "not used: no ... key" notices.

Every user brings their own keys: nothing is bundled. The page lists everything TagMender can
use (requirements.ENTRIES: keys, the MusicBrainz login, optional programs and modules), each as
a row: its status (an icon and a word: Set / Not set / Fix permissions / Installed / Missing),
what it enables, what stops working without it, where to get it, and a field to enter a key
(typed as dots and never shown again; an e-mail address or an address is shown as typed), a
button to the Account pane for the MusicBrainz login, or the command that installs a program.

  Setup(win)                    the page (sidebar: Setup > Setup; Help > Set Up TagMender…)
  first_run(win) -> bool        opens Setup with a welcome line when nothing is set up yet
  open_setup(win, id=None)      shows the page and the row of `id`
  needs(parent, ids, headline)  a sheet saying what is missing, with Open Setup; True if it showed
  plan_notices(plan)            [(id, text)] for a preview's provider notes (the Overview banner)
  error_notices(errors)         the notices for an album's provider errors (the inspector)
  rich(text, link_id=None)      a notice as rich text: its web address and "Open Setup" as links

Every word shown goes through tr(): the Entry fields here, the notices in requirements (its
`translate` hook is tr; its texts are marked N_ and extracted with gui/'s); a key's
value is never read back into a widget."""
from __future__ import annotations

import html
import re

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QApplication, QFrame, QHBoxLayout, QLabel, QLineEdit, QPushButton, QScrollArea,
                               QVBoxLayout, QWidget)

import requirements as rq

from . import icons, sheets, style
from .common import _gui_prefs, _set_gui_pref, heading, plural, section, separator
from .i18n import N_, tr

rq.translate = tr                                       # (notices for the app: in the language chosen)

PAGE = "Setup"                                          # the page's name in the window
FIRST_RUN_PREF = "setup_seen"                          # gui.json: Setup was shown once, or skipped
OPEN = N_("Open Setup")
LATER = N_("Set Up Later")
WELCOME = N_("Welcome to TagMender. Every user brings their own keys: nothing is bundled. TagMender works "
             "without any, but a Discogs token and an AcoustID key give the best results. Add what you want "
             "below; you can come back any time from Help > Set Up TagMender.")
# the page's sections, in order; an entry no section names is listed under "Other" (none is left out)
SECTIONS = ((N_("Required for best results"), ("discogs", "acoustid", "fpcalc", "ffmpeg")),
            (N_("Covers and artist photos"), ("fanart", "audiodb", "lastfm")),
            (N_("Disc lookups"), ("gnudb",)),
            (N_("Your MusicBrainz account"), ("mb_client_id", "mb_client_secret", "musicbrainz_login")),
            (N_("Sending fingerprints"), ("acoustid_user",)),
            (N_("Web search (off by default)"), ("mojeek", "marginalia", "tavily")),
            (N_("Optional programs"), ("tesseract", "pyzbar", "discid", "qt_multimedia")))
# a status: (icon, colour key, word); "not_set" and "missing" are muted for an optional entry
STATES = {rq.SET: ("circle-check", "done", N_("Set")),
          rq.NOT_SET: ("circle-alert", "unresolved", N_("Not set")),
          rq.UNSAFE: ("triangle-alert", "failed", N_("Fix permissions")),
          rq.INSTALLED: ("circle-check", "done", N_("Installed")),
          rq.MISSING: ("circle-alert", "unresolved", N_("Missing"))}
LOGIN_WORDS = {rq.SET: N_("Logged in"), rq.NOT_SET: N_("Not logged in"), rq.UNSAFE: N_("Fix permissions")}
# a preview's provider note -> the entries it is about (the run notes of engine.py)
NOTE_IDS = {"discogs": ("discogs",), "acoustid": ("fpcalc", "acoustid"), "ocr": ("tesseract", "pyzbar"),
            "gnudb": ("gnudb",), "fanart": ("fanart",), "audiodb": ("audiodb",), "lastfm": ("lastfm",)}
_URL = re.compile(r"https?://[^\s)<>\"']+[^\s)<>\"'.,;:]")


def sections() -> list:
    """[(title, [ids])]: SECTIONS, then every other entry under "Other"."""
    named = {i for _t, ids in SECTIONS for i in ids}
    out = [(t, [i for i in ids if i in rq.ids()]) for t, ids in SECTIONS]
    rest = [i for i in rq.ids() if i not in named]
    return [(t, ids) for t, ids in out + [(N_("Other"), rest)] if ids]


def state_look(id, state) -> tuple:
    """(icon, colour key, word) for a row's status."""
    e = rq.entry(id)
    icon, colour, word = STATES.get(state, STATES[rq.NOT_SET])
    if state in (rq.NOT_SET, rq.MISSING) and e.group != rq.RECOMMENDED:
        icon, colour = "circle-dot", "muted"              # (optional: nothing is wrong without it)
    if e.kind == "login":
        word = LOGIN_WORDS.get(state, word)
    return icon, colour, tr(word)


def plain(note) -> str:
    """A run note without its reason code ("missing_credentials: Discogs is not used: ...")."""
    code, sep, rest = str(note).partition(": ")
    return rest if sep and code == "missing_credentials" else str(note)


def rich(text, link_id=None) -> str:
    """A notice as rich text: its web addresses as links, then "Open Setup" (link "setup:<id>")."""
    out, last = [], 0
    for m in _URL.finditer(text):
        out.append(html.escape(text[last:m.start()]))
        out.append(f'<a href="{html.escape(m.group(0))}">{html.escape(m.group(0))}</a>')
        last = m.end()
    out.append(html.escape(text[last:]))
    body = "".join(out)
    if link_id is not None:
        body += f' <a href="setup:{html.escape(link_id)}">{html.escape(tr(OPEN))}</a>'
    return body


def follow(win, link):
    """A notice's link: "setup:<id>" opens the row, a web address the browser."""
    if link.startswith("setup:"):
        open_setup(win, link.split(":", 1)[1] or None)
    else:
        QDesktopServices.openUrl(QUrl(link))


# ---- what a preview or an album lacks -------------------------------------------------------
def plan_notices(plan) -> list:
    """[(id, notice)] for the provider notes of a preview that a key, login or program would end:
    one line each, in the app's words (where="app"). Other notes (a host down) are not here."""
    out, seen = [], set()
    for prov, note in ((plan or {}).get("provider_notes") or {}).items():
        ids = NOTE_IDS.get(prov) or ((prov,) if prov in rq.ids() else ())
        found = [(i, rq.problem(i, "app")) for i in ids]
        found = [(i, t) for i, t in found if t]
        if not found and str(note).startswith("missing_credentials") and ids:
            found = [(ids[0], plain(note))]                 # (a token the service rejected)
        for i, t in found:
            if t not in seen:
                seen.add(t)
                out.append((i, t))
    return out


def other_notes(plan) -> list:
    """The provider notes plan_notices does not say: "prov: note"."""
    told = {i for i, _t in plan_notices(plan)}
    out = []
    for prov, note in ((plan or {}).get("provider_notes") or {}).items():
        ids = NOTE_IDS.get(prov) or ((prov,) if prov in rq.ids() else ())
        if not any(i in told for i in ids):
            out.append(f"{prov}: {plain(note)}")
    return out


def error_notices(errors) -> list:
    """The notices for an album's provider errors that a missing key explains
    ({"discogs": "missing_credentials"}); [] when none does."""
    out = []
    for prov, kind in (errors or {}).items():
        if kind != "missing_credentials":
            continue
        for i in NOTE_IDS.get(prov) or ((prov,) if prov in rq.ids() else ()):
            t = rq.problem(i, "app")
            if t and t not in out:
                out.append(t)
    return out


def key_notices() -> list:
    """The notices of the recommended keys that are not set (a "Key missing" reason's tooltip)."""
    return [t for t in (rq.problem(e.id, "app") for e in rq.ENTRIES
                        if e.group == rq.RECOMMENDED and e.kind == "key") if t]


# ---- the window -------------------------------------------------------------------------------
def main_window(w):
    """The main window `w` belongs to (Preferences: the window it was opened from), else None."""
    win = w.window() if isinstance(w, QWidget) else w
    for c in (win, getattr(win, "win", None)):
        if isinstance(getattr(c, "pages", None), dict) and PAGE in c.pages:
            return c
    if win is not None and not isinstance(win, QWidget):
        return None                                       # (a stand-in window: no page to show)
    for top in QApplication.topLevelWidgets():
        if isinstance(getattr(top, "pages", None), dict) and PAGE in top.pages:
            return top
    return None


def open_setup(win, id=None) -> bool:
    """Show the Setup page (and the row of `id`); False when there is no main window to show it in
    (a caller without one: Preferences opens instead, when it can)."""
    main = main_window(win)
    if main is None:
        if callable(getattr(win, "preferences", None)):
            win.preferences()
        return False
    main.go(PAGE)
    main.showNormal() if main.isMinimized() else None
    main.raise_()
    main.activateWindow()
    if id:
        main.pages[PAGE].show_row(id)
    return True


def missing(ids) -> list:
    """[(id, notice)] of the entries in `ids` that are not ready (a notice said once)."""
    out, seen = [], set()
    for i in ids:
        t = rq.problem(i, "app")
        if t and t not in seen:
            seen.add(t)
            out.append((i, t))
    return out


def needs(parent, ids, headline, fallback=None, extra="") -> bool:
    """When something in `ids` is missing: a sheet saying exactly what (the notices) with Cancel and
    Open Setup (its row); True when the sheet was shown. `fallback`: the id said when the caller
    knows it is missing though no file says so (a login refused by the server)."""
    found = missing(ids)
    if not found and fallback:
        found = [(fallback, rq.notice(fallback, "app", rq.NOT_SET))]
    if not found:
        return False
    body = "\n\n".join(t for _i, t in found) + (f"\n\n{extra}" if extra else "")
    if sheets.ask(parent, headline, body, (tr("Cancel"), tr(OPEN)), tr(OPEN)) == tr(OPEN):
        open_setup(parent, found[0][0])
    return True


def first_run(win) -> bool:
    """On the first start with nothing set up (no key, no login, Setup not shown before): open on
    Setup with its welcome line, once only (it is recorded as shown). True when it did."""
    if _gui_prefs().get(FIRST_RUN_PREF) or not rq.nothing_set_up():
        return False
    win.go(PAGE)
    win.pages[PAGE].welcome(True)
    _set_gui_pref(FIRST_RUN_PREF, True)
    return True


# ---- the page --------------------------------------------------------------------------------
def _caption(text, rich_text=False):
    lb = QLabel(text)
    lb.setProperty("role", "caption")
    lb.setWordWrap(True)
    if rich_text:
        lb.setTextFormat(Qt.RichText)
        lb.setTextInteractionFlags(Qt.TextBrowserInteraction)
    else:
        lb.setTextInteractionFlags(Qt.TextSelectableByMouse)
    return lb


class Row(QFrame):
    """One entry: its status, what it is for, what stops without it, where to get it, and its control."""

    def __init__(self, page, id):
        super().__init__()
        self.page, self.id, self.e = page, id, rq.entry(id)
        self.setObjectName(f"setup_{id}")
        self.field = self.button = self.copy = None
        e = self.e
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 6, 0, 6)
        lay.setSpacing(10)
        self.pic = QLabel()
        lay.addWidget(self.pic, 0, Qt.AlignTop)
        col = QVBoxLayout()
        col.setSpacing(3)
        top = QHBoxLayout()
        self.title = QLabel(tr(e.title))
        self.title.setProperty("role", "headline")
        self.title.setWordWrap(True)
        top.addWidget(self.title, 1)
        self.word = QLabel("")
        self.word.setTextFormat(Qt.RichText)
        top.addWidget(self.word, 0, Qt.AlignTop)
        col.addLayout(top)
        col.addWidget(_caption(tr(e.enables)))
        col.addWidget(_caption(tr("Without it: {0}").format(tr(e.without))))
        get = tr("Get it at") if e.kind in ("key", "login") else tr("More at")
        self.link = _caption(f'{html.escape(get)} <a href="{html.escape(e.url)}">{html.escape(e.url)}</a>',
                             rich_text=True)
        self.link.setOpenExternalLinks(True)
        col.addWidget(self.link)
        self.note = _caption("")                             # (a file others can read: how to fix it)
        self.note.hide()
        col.addWidget(self.note)
        if e.kind == "key":
            col.addLayout(self.key_row())
        elif e.kind == "login":
            col.addLayout(self.login_row())
        else:
            col.addLayout(self.install_row())
        lay.addLayout(col, 1)
        self.refresh()

    def key_row(self):
        import credentials as cr
        from .preferences import key_field
        row = QHBoxLayout()
        self.field = key_field(cr, self.e.credential, self.page.save)   # (the Preferences > Keys field)
        if not self.e.secret:
            self.field.setEchoMode(QLineEdit.Normal)         # (an e-mail address or a web address, as typed)
        self.field.setAccessibleName(tr(self.e.title))
        self.field.setToolTip(tr("{0}: saved only on this computer, readable by you alone, and never shown "
                                 "again").format(tr(self.e.title)))
        self.field.textChanged.connect(lambda _t: self.page.typed_changed())
        row.addWidget(self.field, 1)
        return row

    def login_row(self):
        row = QHBoxLayout()
        self.button = QPushButton(tr("Log In…"))
        self.button.setAutoDefault(False)
        self.button.clicked.connect(self.page.open_account)
        row.addWidget(self.button)
        row.addStretch(1)
        return row

    def install_row(self):
        row = QHBoxLayout()
        self.cmd = QLabel(self.e.install)
        self.cmd.setProperty("role", "mono")
        self.cmd.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.cmd.setAccessibleName(tr("How to install {0}").format(self.e.title))
        self.cmd.setWordWrap(True)
        row.addWidget(self.cmd, 1)
        self.copy = QPushButton(tr("Copy"))
        self.copy.setAutoDefault(False)
        self.copy.setAccessibleName(tr("Copy the command that installs {0}").format(self.e.title))
        self.copy.setToolTip(tr("Copy the command that installs {0}").format(self.e.title))
        self.copy.clicked.connect(lambda: (QApplication.clipboard().setText(self.e.install),
                                           self.page.say(tr("Copied the command."))))
        row.addWidget(self.copy, 0, Qt.AlignTop)
        return row

    def refresh(self):
        state = rq.status(self.id)
        self.state = state
        icon, colour, word = state_look(self.id, state)
        self.pic.setPixmap(icons.icon(icon, colour, 16).pixmap(16, 16))
        self.pic.setAccessibleName(word)
        c = style.status_color(colour).name()
        self.word.setText(f'<span style="color:{c}; font-weight:600">{html.escape(word)}</span>')
        self.word.setAccessibleName(tr("{0}: {1}").format(tr(self.e.title), word))
        src = rq.source(self.id)
        if state == rq.UNSAFE:
            self.note.setText(rq.notice(self.id, "app", state))
        elif self.id == "marginalia" and state == rq.NOT_SET:
            self.note.setText(tr("Without a key of your own, the shared public key is used."))
        elif self.id == "audiodb" and state == rq.NOT_SET:
            self.note.setText(tr("Without a key of your own, the shared test key is used (slow)."))
        elif src == "environment":
            self.note.setText(tr("Set by the environment variable {0}; it wins over a key saved here.")
                              .format(self.e.env))
        else:
            self.note.setText("")
        self.note.setVisible(bool(self.note.text()))
        if self.field is not None:
            from .preferences import SOURCE
            self.field.setPlaceholderText((tr(SOURCE[src]) if src in SOURCE else tr("Set")) if src else
                                          tr("Type or paste it here"))
        if self.button is not None:
            self.button.setText(tr("Account…") if state == rq.SET else tr("Log In…"))
            self.button.setAccessibleName(tr("Open the MusicBrainz account in Preferences"))
            self.button.setToolTip(tr("Log in or out under Preferences > Account"))

    def words(self) -> str:
        """What a search on the page looks in."""
        e = self.e
        return " ".join((tr(e.title), tr(e.enables), tr(e.without), e.id, e.url)).casefold()


# Test Connection: each result's look (icon, colour key) and words
CONNECTION_LOOK = {"ok": ("circle-check", "done"), "no_key": ("circle-check", "done"),
                   "busy": ("circle-alert", "unresolved"), "unreachable": ("circle-x", "failed"),
                   "refused": ("triangle-alert", "failed")}
CONNECTION_FOOTER = N_("Sends one small request to each service. Your Discogs token and AcoustID application "
                       "key go only to their own service; nothing about your music is sent.")


def connection_words(r) -> str:
    """A Test Connection result in the app's words (connection.Result)."""
    return {"ok": tr("Answered in {0} ms").format(r.ms),
            "no_key": tr("Answered in {0} ms; no key set up").format(r.ms),
            "busy": tr("Busy ({0}): try again later").format(r.code) if r.code else tr("Busy: try again later"),
            "unreachable": tr("Unreachable"),
            "refused": tr("Key refused")}.get(r.state, r.state)


class ConnectionPanel(QFrame):
    """Test Connection: MusicBrainz, Discogs, AcoustID and the Cover Art Archive, each with
    its answer (an icon and words), asked on a worker thread when the button is pressed."""

    def __init__(self, page):
        super().__init__()
        import connection
        self.page = page
        self.setObjectName("setup_connection")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 6, 0, 6)
        lay.setSpacing(4)
        self.rows = {}
        for name in connection.SERVICES:
            row = QHBoxLayout()
            pic = QLabel()
            row.addWidget(pic)
            title = QLabel(connection.TITLES[name])
            title.setProperty("role", "headline")
            row.addWidget(title, 1)
            word = QLabel()
            word.setProperty("role", "hint")
            row.addWidget(word)
            lay.addLayout(row)
            self.rows[name] = (pic, word)
            self._say(name, tr("Not tested"))
        foot = QHBoxLayout()
        foot.addWidget(_caption(tr(CONNECTION_FOOTER)), 1)
        self.button = QPushButton(tr("Test Connection"))
        self.button.setAutoDefault(False)
        self.button.setAccessibleName(tr("Test the connection to MusicBrainz, Discogs, AcoustID and the Cover "
                                         "Art Archive"))
        self.button.clicked.connect(self.test)
        foot.addWidget(self.button, 0, Qt.AlignTop)
        lay.addLayout(foot)
        self.worker = None

    def _say(self, name, text):
        """One service's result, shown and read out (a screen reader hears the name and the result)."""
        import connection
        word = self.rows[name][1]
        word.setText(text)
        word.setAccessibleName(tr("{0} connection: {1}").format(connection.TITLES.get(name, name), text))

    def test(self):
        import connection
        from .work import start
        self.button.setEnabled(False)
        for name, (pic, _word) in self.rows.items():
            pic.setPixmap(icons.icon("loader-circle", "muted").pixmap(16, 16))
            self._say(name, tr("Testing…"))
        self.worker = start(self, lambda w: connection.test_all(), self.answered, failed=self.failed)

    def answered(self, results):
        for r in results or []:
            pic, word = self.rows.get(r.service, (None, None))
            if pic is None:
                continue
            ic, tint = CONNECTION_LOOK.get(r.state, ("circle-alert", "unresolved"))
            pic.setPixmap(icons.icon(ic, tint).pixmap(16, 16))
            self._say(r.service, connection_words(r))
            word.setToolTip(r.detail)
        self.button.setEnabled(True)

    def failed(self, msg):
        for name, (pic, _word) in self.rows.items():
            pic.setPixmap(icons.icon("circle-x", "failed").pixmap(16, 16))
            self._say(name, tr("Not tested: {0}").format(msg))
        self.button.setEnabled(True)


class Setup(QWidget):
    def __init__(self, win):
        super().__init__()
        self.win = win
        lay = QVBoxLayout(self)
        lay.addLayout(heading(tr("Setup"), tr("Every key, login and optional program TagMender can use: what each "
                                             "is for, what stops without it and where to get it. Keys are kept "
                                             "only on this computer, readable by you alone, and never shown "
                                             "again.")))
        # the welcome line of the first start
        self.banner = QFrame()
        self.banner.setProperty("role", "panel")
        bl = QHBoxLayout(self.banner)
        bl.setContentsMargins(12, 10, 12, 10)
        self.welcome_text = QLabel(tr(WELCOME))
        self.welcome_text.setWordWrap(True)
        bl.addWidget(self.welcome_text, 1)
        self.later = QPushButton(tr(LATER))
        self.later.setAutoDefault(False)
        self.later.setAccessibleName(tr(LATER))
        self.later.setToolTip(tr("Go to Overview; Setup stays in the sidebar"))
        self.later.clicked.connect(self.set_up_later)
        bl.addWidget(self.later, 0, Qt.AlignVCenter)
        self.banner.hide()
        lay.addWidget(self.banner)
        body = QWidget()
        self.body = QVBoxLayout(body)
        self.body.setContentsMargins(0, 0, 8, 0)
        self.body.setSpacing(2)
        self.rows, self.heads = {}, []
        head = QWidget()
        hl = section(tr("Connection"))
        head.setLayout(hl)
        self.body.addWidget(head)
        self.connection = ConnectionPanel(self)
        self.body.addWidget(self.connection)
        for title, ids in sections():
            head = QWidget()
            hl = section(tr(title))
            hl.setContentsMargins(0, 12, 0, 0)
            head.setLayout(hl)
            self.body.addWidget(head)
            mine = []
            for i in ids:
                r = self.rows[i] = Row(self, i)
                self.body.addWidget(r)
                mine.append(r)
            self.heads.append((head, mine))
        self.body.addStretch(1)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll.setAccessibleName(tr("Setup"))
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.scroll.setWidget(body)
        lay.addWidget(self.scroll, 1)
        lay.addWidget(separator())
        foot = QHBoxLayout()
        self.saved = QLabel("")
        self.saved.setProperty("role", "hint")
        self.saved.setWordWrap(True)
        self.saved.setAccessibleDescription(tr("Saved keys"))
        foot.addWidget(self.saved, 1)
        self.again = QPushButton(tr("Check Again"))
        self.again.setAutoDefault(False)
        self.again.setAccessibleName(tr("Check again what is set up and installed"))
        self.again.setToolTip(tr("Look again for keys, the login and programs installed since"))
        self.again.clicked.connect(lambda: (self.refresh(), self.say(tr("Checked again."))))
        foot.addWidget(self.again)
        self.save_button = QPushButton(tr("Save Keys"))
        self.save_button.setProperty("role", "primary")
        self.save_button.setAccessibleName(tr("Save keys"))
        self.save_button.setToolTip(tr("Save the keys typed above on this computer (readable by you alone)"))
        self.save_button.clicked.connect(self.save)
        foot.addWidget(self.save_button)
        lay.addLayout(foot)
        self.typed_changed()

    # ---- the rows ---------------------------------------------------------
    def refresh(self):
        for r in self.rows.values():
            r.refresh()

    def showEvent(self, e):
        super().showEvent(e)
        self.refresh()                                      # (a key saved in Preferences, a login since)

    def retint(self):
        self.refresh()

    def show_row(self, id):
        """Scroll to a row and put the focus on its field or button."""
        r = self.rows.get(id)
        if r is None:
            return
        if r.isHidden():
            self.filter("")
        self.scroll.ensureWidgetVisible(r, 0, 24)
        w = r.field or r.button or r.copy
        if w is not None:
            w.setFocus(Qt.OtherFocusReason)

    def filter(self, text):
        """The window's search field: rows whose words hold the text (a section with none is hidden)."""
        text = (text or "").casefold().strip()
        for head, rows in self.heads:
            shown = 0
            for r in rows:
                on = not text or text in r.words()
                r.setVisible(on)
                shown += on
            head.setVisible(bool(shown))

    def say(self, text):
        self.saved.setText(text)

    # ---- the first start ---------------------------------------------------
    def welcome(self, on):
        self.banner.setVisible(on)

    def set_up_later(self):
        _set_gui_pref(FIRST_RUN_PREF, True)
        self.welcome(False)
        self.win.go("Overview")

    # ---- keys --------------------------------------------------------------
    def fields(self) -> dict:
        return {i: r.field for i, r in self.rows.items() if r.field is not None}

    def typed(self) -> int:
        """Keys typed or pasted and not saved yet."""
        return sum(1 for f in self.fields().values() if f.text().strip())

    def typed_changed(self):
        self.save_button.setEnabled(bool(self.typed()))

    def discard(self):
        for f in self.fields().values():
            f.clear()

    def save(self):
        """Save every key typed (0600 files in the config folder); the fields are emptied at once."""
        n, failed = 0, []
        for i, f in self.fields().items():
            if not f.text().strip():
                continue
            try:
                rq.store(i, f.text())
                n += 1
            except (OSError, ValueError) as exc:
                failed.append(f"{tr(rq.entry(i).title)}: {exc.__class__.__name__}")
            f.clear()
        if n:
            _set_gui_pref(FIRST_RUN_PREF, True)
            self.welcome(False)
        self.refresh()
        self.refresh_preferences()
        words = tr("Saved {0}.").format(plural(n, tr("key"), tr("keys"))) if n else \
            tr("Nothing to save: type a key first.")
        if failed:
            words += " " + tr("Not saved: {0}.").format(", ".join(failed))
        self.say(words)
        self.typed_changed()
        return n

    def refresh_preferences(self):
        """Preferences, when open, says the new keys are set too (its placeholders)."""
        prefs = getattr(self.win, "prefs_window", None)
        if prefs is None:
            return
        import credentials as cr
        from .preferences import SOURCE
        for pane in (prefs.keys, prefs.account):
            for name, e in pane.fields.items():
                if not e.text().strip():
                    src = cr.source(name)
                    e.setPlaceholderText((tr(SOURCE[src]) if src in SOURCE else tr("Set ({src})").format(src=src))
                                         if src else tr("Not set"))
        prefs.account.login.refresh()

    # ---- the MusicBrainz login ---------------------------------------------
    def open_account(self):
        prefs = self.win.preferences()
        prefs.show_tab("Account")
        return prefs

    def may_leave(self) -> bool:
        """Closing the window with keys typed and not saved: Save, Don't Save or stay."""
        n = self.typed()
        if not n:
            return True
        ans = sheets.ask(self, tr("Save the {0} you typed?").format(plural(n, tr("key"), tr("keys"))),
                         tr("Keys are kept only when saved. Don't Save clears what was typed."),
                         buttons=(tr("Cancel"), tr("Don't Save"), tr("Save")), default=tr("Save"),
                         offscreen=tr("Don't Save"))
        if ans == tr("Save"):
            self.save()
        elif ans == tr("Don't Save"):
            self.discard()
        else:
            return False
        return True
