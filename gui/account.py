"""gui/account.py - Your MusicBrainz account in the app: log in, collections and ratings (Picard's).

Everything that talks to MusicBrainz is an action you choose, confirmed first in a sheet that
says exactly what is sent and where, and runs on a worker (gui.work.start); the window never waits.
Nothing is bundled: you bring your own MusicBrainz application (client ID and secret, entered in
Preferences > Keys) - mb_account.py has the details.

Entry points (the window, the inspector and Preferences call these):
  account_of(win)                      the window's mb_account.Account (one per window; no window:
                                       one shared account)
Every "what is sent" sheet also names the refresh (POST /oauth2/token, to the server the login
came from) sent first when the login has expired.
  AccountPane(win, open_keys=None)     Preferences pane "Account": sign-in server, who is logged in,
                                       Log In… / Log Out…, where to register an application
  log_in(parent, acct, done=None)      the browser opens MusicBrainz; a sheet takes the code it shows,
                                       another names the requests that send it (field names, no values)
  log_out(parent, acct, done=None)     confirmed; the login revoked on MusicBrainz and deleted here
  open_collections(parent, album, acct)   Album > Collections…: a sheet names the GETs that read your
                                       collections; tick the ones that should hold the album's release,
                                       then a sheet lists each PUT/DELETE to send
  submit_ratings(parent, album, edit, acct)  Album > Submit Ratings to MusicBrainz…: the ratings (your
                                       edits', else the files') and the XML that is POSTed, then sent
  StarRating                           0-5 stars, keyboard and mouse; changed(stars or None)
  RatingBox(inspector)                 "Rating" for the inspector's Tags tab: the file's (or album's)
                                       rating, and your stars as an edit of the plan
                                       (inspector.set_tag("~rating", ["4"], file=...)) - written by Apply
                                       as any edit: staged, verified, journaled and undoable
  release_of(album)                    the album's MusicBrainz release ID, or "\""""
from __future__ import annotations

import html
import re

from PySide6.QtCore import QByteArray, QEvent, QRectF, QSize, Qt, QUrl, Signal
from PySide6.QtGui import QColor, QDesktopServices, QIcon, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit,
                               QPlainTextEdit, QPushButton, QScrollArea, QToolButton, QVBoxLayout, QWidget)

from . import sheets, style
from .common import heading, plural, section
from .i18n import N_, tr
from .work import start

import mb_account as mba  # noqa: E402  (gui.common put the repo root on the path)

ACCOUNT_PANE = (N_("Account"), "key-round")              # the Preferences tab: name and icon
STAR, NO_STAR = "★", "☆"
# where a pane's how-to and its Login line send you for the client ID and secret: under Keys (a
# window of its own), or on the same pane (Preferences > Account, where the fields are below the
# how-to and above the Login line); whole sentences, so every language can say them its own way
NO_KEYS = {False: N_("Not logged in. Enter your application's client ID and secret under Keys first."),
           True: N_("Not logged in. Enter your application's client ID and secret above first.")}
HOW_KEYS = {False: N_("2. Enter its client ID and secret in Preferences > Keys (kept only on this computer, "
                      "readable by you alone)."),
            True: N_("2. Enter its client ID and secret below (kept only on this computer, readable by you "
                     "alone).")}


_shared = []                                          # the account of no window (made once)


def account_of(win) -> mba.Account:
    """The window's account (made once): it keeps a login page's PKCE verifier until the code comes.
    No window (or one that cannot hold it): one shared account, never one of its own."""
    win = win.window() if isinstance(win, QWidget) else win
    acct = getattr(win, "_mb_account", None) if win is not None else None
    if acct is None:
        if not _shared:
            _shared.append(mba.Account())
        acct = _shared[0]
        if win is not None:
            try:
                win._mb_account = acct
            except AttributeError:
                pass
    return acct


def headers_text(login=True) -> str:
    """The headers a request goes with, under it in every "what is sent" box (not translated: as sent)."""
    return ("  with your login (Authorization: Bearer …) and " if login else "  with ") + \
        f"User-Agent: {mba.USER_AGENT}"


def refresh_text(acct) -> str:
    """The line every "what is sent" sheet ends with: the refresh sent first when the login expired."""
    return (tr("First, if your login has expired:\n") + mba.refresh_request(acct.login_host()))


def release_of(album) -> str:
    """The album's MusicBrainz release ID (the release the plan writes, else the files'), or ""."""
    if not album:
        return ""
    rel = album.get("release") or {}
    got = (rel.get("musicbrainz_albumid") or [""])[0] if isinstance(rel, dict) else ""
    ident = album.get("identification") or {}
    if not got and ident.get("provider") == "musicbrainz":
        got = ident.get("release_id") or ""
    if not got:
        for fe in album.get("files") or []:
            got = ((fe.get("changes") or {}).get("musicbrainz_albumid") or [""])[0]
            if got:
                break
    return str(got) if mba.MBID.match(str(got or "")) else ""


def album_title(album) -> str:
    rel = album.get("release") if isinstance(album.get("release"), dict) else {}
    return str((rel.get("album") or [""])[0] or album.get("label") or tr("this album"))


def _failed(parent, headline):
    return lambda msg: sheets.warning(parent, headline, msg.split("\n\n")[0])


def _send_sheet(parent, headline, body, sent, action, destructive=False):
    """A sheet that names what is sent (sent: the requests and body, word for word, monospace).
    destructive: Cancel is the default (Return never sends it), the action marked so."""
    s = sheets.Sheet(parent, headline, body, "circle-alert", "attention", destructive=destructive)
    t = QPlainTextEdit(sent)
    t.setReadOnly(True)
    t.setFont(style.mono())
    t.setAccessibleName(tr("What is sent"))
    t.setMinimumHeight(120)
    s.extra.addWidget(t)
    s.add_button(tr("Cancel"), accept=False, default=destructive)
    b = s.add_button(action, default=not destructive)
    if destructive:
        b.setProperty("role", "destructive")
    s.sent_text = sent
    return s


def _confirm_send(parent, headline, body, sent, action, destructive=False) -> bool:
    s = _send_sheet(parent, headline, body, sent, action, destructive)
    if not sheets.blocking():
        sheets.shown.append(("send", headline, body + "\n\n" + sent))
        s.deleteLater()
        return False
    return sheets.run(s, "send") == QDialog.Accepted


# ---- log in / out ------------------------------------------------------------------------------
def ask_code(parent, url) -> tuple[str, bool]:
    """The sheet the code is pasted into (the login page's address there too, to copy)."""
    s = sheets.Sheet(parent, tr("Paste the code from MusicBrainz"),
                     tr("Your browser shows MusicBrainz's login page. Allow access, then copy the code it "
                        "shows and paste it here. If no browser opened, copy this address into one:"),
                     "key-round", "accent")
    link = QLineEdit(url)
    link.setReadOnly(True)
    link.setAccessibleName(tr("Login page address"))
    s.extra.addWidget(link)
    code = QLineEdit()
    code.setEchoMode(QLineEdit.Password)                 # (a code is a secret too)
    code.setPlaceholderText(tr("Code"))
    code.setAccessibleName(tr("Code from MusicBrainz"))
    s.extra.addWidget(code)
    s.add_button(tr("Cancel"), accept=False)
    s.add_button(tr("Continue"), default=True)          # (nothing sent yet: the next sheet's Log In sends it)
    if not sheets.blocking():
        sheets.shown.append(("ask_code", s.headline_text, s.body_text))
        s.deleteLater()
        return "", False
    code.setFocus()
    ok = sheets.run(s, "ask_code") == QDialog.Accepted
    return code.text().strip(), ok


def login_text(acct) -> tuple[str, str]:
    """(body, sent) of the login's confirmation: the two requests, the form fields by name
    (their values - the code, your client secret - are never shown)."""
    host = acct.server["host"]
    fields = "grant_type=authorization_code, code, client_id, client_secret, redirect_uri"
    if acct.server["pkce"]:
        fields += ", code_verifier"
    body = (tr("TagMender sends the code to {host} with your application's client ID and secret, which "
               "answers with your login, then asks it for your user name with that login. The login is "
               "kept on this computer only, readable by you alone.").format(host=host))
    sent = (f"POST https://{host}/oauth2/token\n  form fields: {fields}\n{headers_text(login=False)}\n"
            f"GET https://{host}/oauth2/userinfo\n"           # (the requests as sent: not translated)
            + headers_text().replace("your login", "your new login"))
    return body, sent


def logout_text(acct) -> tuple[str, str]:
    """(body, sent) of the logout's confirmation, the form fields by name."""
    host = mba.PROVIDERS.get(acct._saved().get("provider"), acct.server)["host"]
    body = (tr("TagMender asks {host} to revoke its access to your account, and deletes the login saved "
               "on this computer (even when the request fails). To use your account again, log in again.")
            .format(host=host))
    sent = (f"POST https://{host}/oauth2/revoke\n  form fields: token (your refresh token), client_id, client_secret\n"
            + headers_text(login=False))
    return body, sent


def log_in(parent, acct, done=None):
    """Log in: say what happens, open the browser, take the code, confirm what is sent, then
    exchange it on a worker."""
    try:
        url = acct.authorize_url()
    except mba.AccountError as e:
        sheets.warning(parent, tr("TagMender needs your MusicBrainz application first."), str(e))
        return False
    host = acct.server["host"]
    if not sheets.question(parent, tr("Log in to {host}?").format(host=host),
                           tr("Your browser opens {host}, where you allow TagMender (as your own application) "
                              "to read your profile and tags, and to save ratings and collections when you ask. "
                              "Nothing is sent until you choose an action that sends it, and each "
                              "one asks first. The login is kept on this computer only, readable by you alone.")
                           .format(host=host),
                           action=tr("Open Browser")):
        return False
    QDesktopServices.openUrl(QUrl(url))
    code, ok = ask_code(parent, url)
    if not ok or not code:
        return False
    body, sent = login_text(acct)
    if not confirm_send(parent, tr("Send the code to {host}?").format(host=host), body, sent, tr("Log In")):
        return False

    def finished(name):
        sheets.information(parent, tr("Logged in to MusicBrainz as {name}.").format(name=name),
                           tr("Collections… and Submit Ratings… are in the Album menu."))
        if done:
            done(name)
    start(parent, lambda _w: acct.exchange(code), finished, failed=_failed(parent, tr("Logging in did not work.")))
    return True


def log_out(parent, acct, done=None):
    name = acct.username
    body, sent = logout_text(acct)
    headline = tr("Log out of MusicBrainz as {name}?").format(name=name) if name else tr("Log out of MusicBrainz?")
    if not confirm_send(parent, headline, body, sent, tr("Log Out"),
                        destructive=True):
        return False

    def finished(revoked):
        if not revoked:
            sheets.warning(parent, tr("Logged out on this computer."),
                           tr("MusicBrainz did not confirm it revoked the access; you can revoke it on "
                              "your account's applications page."))
        if done:
            done(revoked)
    start(parent, lambda _w: acct.logout(), finished, failed=_failed(parent, tr("Logging out did not work.")))
    return True


class AccountPane(QWidget):
    """Preferences > Account: who is logged in, Log In… / Log Out…, and how to set it up."""

    def __init__(self, win=None, open_keys=None, acct=None, keys_here=False):
        super().__init__()
        self.win, self.open_keys, self.keys_here = win, open_keys, keys_here
        self.acct = acct or account_of(win)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(style.MARGIN, style.MARGIN, style.MARGIN, style.MARGIN)
        lay.setSpacing(style.SPACE["s"])
        lay.addLayout(heading(tr("MusicBrainz account"), tr("Your collections and ratings. You bring "
                                                    "your own MusicBrainz application; nothing is sent until "
                                                    "you choose to send it.")))
        # how to set it up first: read before the fields it explains, at body size
        self.how_head = section(tr("Setting it up"))
        lay.addLayout(self.how_head)
        self.how = QLabel("")
        self.how.setProperty("role", "hint")
        self.how.setWordWrap(True)
        self.how.setTextInteractionFlags(Qt.TextBrowserInteraction)
        self.how.setOpenExternalLinks(True)
        lay.addWidget(self.how)
        lay.addSpacing(style.SPACE["s"])
        self.form = form = QFormLayout()
        form.setLabelAlignment(Qt.AlignRight)
        form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        self.server = QComboBox()
        self.server.setAccessibleName(tr("Sign-in server"))
        for key, p in mba.PROVIDERS.items():
            self.server.addItem(p["label"], key)
        self.server.currentIndexChanged.connect(self.server_changed)
        form.addRow(tr("Sign in at"), self.server)
        self.state = QLabel("")
        self.state.setWordWrap(True)
        self.state.setAccessibleName(tr("Login"))
        login = QWidget()                                # (the state, and its buttons right under it)
        col = QVBoxLayout(login)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(style.SPACE["s"])
        col.addWidget(self.state)
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        self.keys_button = QPushButton(tr("Enter Keys…"))
        self.keys_button.setAccessibleName(tr("Enter client ID and secret"))
        self.keys_button.clicked.connect(lambda: self.open_keys and self.open_keys())
        self.keys_button.setVisible(open_keys is not None)
        self.out_button = QPushButton(tr("Log Out…"))
        self.out_button.setAccessibleName(tr("Log out"))
        self.out_button.clicked.connect(self.log_out)
        self.in_button = QPushButton(tr("Log In…"))
        self.in_button.setAccessibleName(tr("Log in"))
        self.in_button.clicked.connect(self.log_in)        # (a bordered button: the window's Save is the prominent one)
        for b in (self.in_button, self.out_button, self.keys_button):
            row.addWidget(b)
        row.addStretch(1)
        self.button_row = row
        col.addLayout(row)
        form.addRow(tr("Login"), login)
        lb = form.labelForField(login)                   # (beside the state's first line, not the buttons)
        if lb is not None:
            lb.setAlignment(Qt.AlignRight | Qt.AlignTop)
        lay.addLayout(form)
        lay.addStretch(1)
        self.first = self.in_button
        self.refresh()

    def server_changed(self, _i):
        if not self.acct.logged_in():
            self.acct.provider = self.server.currentData()
        self.refresh()

    def refresh(self):
        on = self.acct.logged_in()
        i = self.server.findData(self.acct.provider)
        self.server.blockSignals(True)
        self.server.setCurrentIndex(max(i, 0))
        self.server.blockSignals(False)
        self.server.setEnabled(not on)
        why = ""
        try:
            self.acct.client()
            have_keys = True
        except mba.AccountError as e:                  # (none entered, or a key file others can read)
            have_keys, why = False, str(e)
        if on:
            self.state.setText(tr("Logged in as {username}").format(username=self.acct.username))
        elif have_keys:
            self.state.setText(tr("Not logged in"))
        elif "chmod" in why:
            self.state.setText(tr("Not logged in. {why}").format(why=why))
        else:
            self.state.setText(tr(NO_KEYS[self.keys_here]))
        self.in_button.setVisible(not on)
        self.in_button.setEnabled(have_keys)
        self.out_button.setVisible(on)
        reg = self.acct.server["register"]
        link = f'<a href="{html.escape(reg)}">{html.escape(reg)}</a>' if reg.startswith("http") and " " not in reg \
            else html.escape(reg)
        self.how.setText(
            tr("1. Register an application at {link}: type \"Installed application\", any name "
               "(e.g. TagMender), no callback address needed.").format(link=link) + "<br>"
            + html.escape(tr(HOW_KEYS[self.keys_here])) + "<br>"
            + tr("3. Log In: your browser asks you to allow access, then you paste the code it shows."))

    def log_in(self):
        log_in(self, self.acct, done=lambda _n: self.refresh())

    def log_out(self):
        log_out(self, self.acct, done=lambda _r: self.refresh())


# ---- collections --------------------------------------------------------------------------------
class CollectionsSheet(sheets.Sheet):
    """Your release collections, ticked where they hold the release; the default saves changes."""

    def __init__(self, parent, title, release, collections, holding):
        super().__init__(parent, tr("Collections for {title}").format(title=title),
                         tr("Tick the collections that should hold this release."), "library", "accent")
        self.release, self.collections, self.holding = release, collections, set(holding)
        self.boxes = {}
        box = QWidget()
        col = QVBoxLayout(box)
        col.setContentsMargins(0, 0, 0, 0)
        for c in collections:
            b = QCheckBox(f"{c['name']} ({plural(c['count'], tr('release'), tr('releases'))})")
            b.setAccessibleName(c["name"])
            b.setChecked(c["id"] in self.holding)
            col.addWidget(b)
            self.boxes[c["id"]] = b
            b.toggled.connect(lambda _on: self.update_save())
        if not collections:
            col.addWidget(QLabel(tr("You have no release collections yet. Make one on musicbrainz.org.")))
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(box)
        scroll.setMinimumHeight(min(240, 28 * max(1, len(collections)) + 8))
        self.extra.addWidget(scroll)
        if not collections:                              # (nothing to tick: nothing to save)
            self.save = None
            self.add_button(tr("OK"), accept=False, default=True)
            return
        self.add_button(tr("Cancel"), accept=False)
        self.save = self.add_button(tr("Save Changes"), default=True)
        self.update_save()

    def update_save(self):
        """Save Changes only once a box differs from what MusicBrainz has (else there is nothing to send)."""
        if getattr(self, "save", None) is not None:
            add, drop = self.changes()
            self.save.setEnabled(bool(add or drop))

    def changes(self) -> tuple[list, list]:
        """(collection ids to add the release to, ids to remove it from)."""
        add = [cid for cid, b in self.boxes.items() if b.isChecked() and cid not in self.holding]
        drop = [cid for cid, b in self.boxes.items() if not b.isChecked() and cid in self.holding]
        return add, drop


def collection_text(acct, release, add, drop, names) -> tuple[str, str]:
    """(body, sent) of the confirmation: which collections change, and each request word for word."""
    lines = [tr("Added to “{0}”").format(names[c]) for c in add] + [tr("Removed from “{0}”")
                                                                    .format(names[c]) for c in drop]
    items = "".join(f"<li>{html.escape(x)}</li>" for x in lines)
    body = (tr("<p>Sent to musicbrainz.org as {username}, with your login:</p>")
            .format(username=html.escape(acct.username)) + f"<ul>{items}</ul>"
            + tr("<p>To undo, change the collections back here or on musicbrainz.org.</p>"))
    sent = "\n".join(r + "\n" + headers_text() for r in
                     [r for c in add for r in mba.collection_requests(c, [release], True)] +
                     [r for c in drop for r in mba.collection_requests(c, [release], False)])
    return body, sent + "\n\n" + refresh_text(acct)


def read_collections_text(acct, release) -> tuple[str, str]:
    """(body, sent) of the sheet before Collections… reads anything: the two GETs word for word."""
    body = (tr("TagMender asks musicbrainz.org, as {username} and with your login, for your release "
               "collections ({PAGE} a request; more requests follow, each with the next offset, when "
               "you have more) and which of them hold this album's release. Nothing changes yet.")
            .format(username=acct.username, PAGE=mba.PAGE))
    sent = "\n".join(r + "\n" + headers_text() for r in mba.collections_requests(release))
    return body, sent + "\n\n" + refresh_text(acct)


LOGIN_IDS = ("mb_client_id", "mb_client_secret", "musicbrainz_login")


def not_logged_in(parent):
    """Collections and Submit Ratings without a login: a sheet saying what is missing (your
    application's client ID and secret, or the login itself), with Open Setup."""
    from . import setup
    setup.needs(parent, LOGIN_IDS, tr("Log in to MusicBrainz first."), fallback="musicbrainz_login")


def open_collections(parent, album, acct, choose=None):
    """Album > Collections…: read your collections (worker), let you tick them, confirm, send.
    choose(sheet) -> (add, drop) or None stands in for the person (tests)."""
    release = release_of(album)
    if not release:
        sheets.information(parent, tr("This album has no MusicBrainz release."),
                           tr("Collections hold MusicBrainz releases; identify the album with one first."))
        return False
    if not acct.logged_in():
        not_logged_in(parent)
        return False
    title = album_title(album)
    body, sent = read_collections_text(acct, release)
    if not confirm_send(parent, tr("Read your MusicBrainz collections?"), body, sent, tr("Read Collections")):
        return False

    def work(_w):
        return acct.collections(), acct.release_collections(release)

    def got(out):
        cols, holding = out
        s = CollectionsSheet(parent, title, release, cols, holding)
        if choose is not None:
            picked = choose(s)
            s.deleteLater()
            if picked is None:
                return
        elif not sheets.blocking():
            sheets.shown.append(("collections", s.headline_text, s.body_text))
            s.deleteLater()
            return
        else:
            if sheets.run(s, "collections") != QDialog.Accepted:
                return
            picked = s.changes()
        add, drop = picked
        if not add and not drop:
            return
        names = {c["id"]: c["name"] for c in cols}
        body, sent = collection_text(acct, release, add, drop, names)
        if not confirm_send(parent, tr("Change your MusicBrainz collections?"), body, sent,
                            tr("Send {0}").format(plural(len(add) + len(drop), tr('Change'), tr('Changes')))):
            return

        def send(_w):
            """(the changes made, the error that stopped the rest or None): each is its own request,
            so the ones before a failure did go through."""
            made = []
            try:
                for c in add:
                    acct.add_to_collection(c, [release])
                    made.append(tr("added to “{0}”").format(names[c]))
                for c in drop:
                    acct.remove_from_collection(c, [release])
                    made.append(tr("removed from “{0}”").format(names[c]))
            except mba.AccountError as e:
                return made, str(e)
            return made, None

        def sent_(out):
            made, error = out
            if error is None:
                sheets.information(parent, tr("Changed {0}.").format(plural(len(made), tr('collection'),
                                                                            tr('collections'))))
            elif made:
                sheets.warning(parent, tr("Some of the collections changed."),
                               tr("Done: {0}. The rest did not change: {error}").format('; '.join(made), error=error))
            else:
                sheets.warning(parent, tr("The collections did not change."), error)
        start(parent, send, sent_, failed=_failed(parent, tr("Some collections may have changed; open "
                                                             "Collections… again to check.")))
    start(parent, work, got, failed=_failed(parent, tr("Your collections could not be read.")))
    return True


# (patch site: tests answer the "what is sent" sheet here)
confirm_send = _confirm_send


# ---- ratings -------------------------------------------------------------------------------------
def stars_text(n) -> str:
    return tr("No rating") if n is None else STAR * n + NO_STAR * (5 - n)


def stars_words(n) -> str:
    """The stars with their number in words, for a screen reader too: "★★★★☆ (4 of 5)"."""
    if n is None:
        return tr("No rating")
    return stars_text(n) + " (" + (tr("0: removes your rating") if n == 0 else tr("{0} of 5").format(n)) + ")"


def ratings_text(acct, rows) -> tuple[str, str]:
    """(body, sent) of the confirmation: each track's stars, then the request and its XML."""
    lines = "".join(f"<li>{html.escape(r['title'])}: {html.escape(stars_words(r['stars']))}</li>" for r in rows[:12])
    more = "<li>" + tr("… and {0} more").format(len(rows) - 12) + "</li>" if len(rows) > 12 else ""
    body = (tr("<p>Sent to musicbrainz.org as {user}, with your login, as your rating of each recording (0 stars "
               "removes yours):</p>").format(user=html.escape(acct.username)) + f"<ul>{lines}{more}</ul>"
            + tr("<p>To undo, rate them again, or change them on musicbrainz.org.</p>"))
    sent = mba.ratings_request() + "\n" + headers_text() + "\nContent-Type: application/xml; charset=utf-8\n\n" + \
        pretty_xml(mba.ratings_xml({r["recording"]: r["stars"] for r in rows})) + \
        "\n" + tr("(Shown one element a line; sent as one line, without the line breaks and indents.)")
    return body, sent + "\n\n" + refresh_text(acct)


def pretty_xml(text) -> str:
    """The XML one element a line, indented, for reading in the sheet: joining its lines with their
    indents taken off gives back exactly the text that is sent (it has no spaces between elements)."""
    out, depth = [], 0
    for tok in re.findall(r"<\?.*?\?>|<[^/!?][^>]*>[^<]*</[^>]+>|</[^>]+>|<[^>]+>", text):
        if tok.startswith("</"):
            depth = max(0, depth - 1)
            out.append("  " * depth + tok)
        elif tok.startswith("<?") or tok.endswith("/>") or "</" in tok:
            out.append("  " * depth + tok)
        else:
            out.append("  " * depth + tok)
            depth += 1
    return "\n".join(out) if "".join(x.lstrip(" ") for x in out) == text else text


def submit_ratings(parent, album, edit=None, acct=None):
    """Album > Submit Ratings to MusicBrainz…: read the ratings (worker), confirm, send (worker)."""
    acct = acct or account_of(parent)
    if not acct.logged_in():
        not_logged_in(parent)
        return False

    def got(rows):
        if not rows:
            sheets.information(parent, tr("No ratings to send."),
                               tr("Rate tracks in the inspector first; only tracks with a MusicBrainz recording "
                                  "ID can be rated."))
            return
        body, sent = ratings_text(acct, rows)
        if not confirm_send(parent, tr("Submit {0} to MusicBrainz?").format(plural(len(rows), tr('rating'),
                                                                                   tr('ratings'))), body, sent,
                            tr("Submit {0}").format(plural(len(rows), tr('Rating'), tr('Ratings')))):
            return
        ratings = {r["recording"]: r["stars"] for r in rows}
        start(parent, lambda _w: acct.submit_ratings(ratings),
              lambda n: sheets.information(parent, tr("Submitted {0} to MusicBrainz.").format(plural(n, tr('rating'),
                                                                                                     tr('ratings')))),
              failed=_failed(parent, tr("The ratings were not submitted.")))
    start(parent, lambda _w: mba.album_ratings(album, edit), got,
          failed=_failed(parent, tr("The ratings could not be read.")))
    return True


def star_icon(filled, role, size=16) -> QIcon:
    """Lucide's star (assets/icons/lucide/star.svg), outlined, or filled in the same colour."""
    from . import icons
    if not filled:
        return icons.icon("star", role, size)
    from PySide6.QtSvg import QSvgRenderer
    text = icons._source("star") or ""
    colour = style.color(role)
    text = text.replace('fill="none"', f'fill="{colour}"').replace("currentColor", colour)
    ic = QIcon()
    for ratio in (1.0, 2.0):
        pm = QPixmap(int(size * ratio), int(size * ratio))
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing)
        QSvgRenderer(QByteArray(text.encode())).render(p, QRectF(0, 0, size * ratio, size * ratio))
        p.end()
        pm.setDevicePixelRatio(ratio)
        ic.addPixmap(pm, QIcon.Normal)
        ic.addPixmap(pm, QIcon.Disabled)
    return ic


class StarRating(QWidget):
    """Five stars: click one (again: none), or the keys 0-5, Left/Right, Delete. changed(n | None)."""
    changed = Signal(object)

    RING = 2                                             # (the focus ring's width; the stars sit inside it)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._value = None
        row = QHBoxLayout(self)
        row.setContentsMargins(self.RING, self.RING, self.RING, self.RING)
        row.setSpacing(0)
        self.buttons = []
        for i in range(1, 6):
            b = QToolButton()
            b.setAutoRaise(True)
            b.setFocusPolicy(Qt.NoFocus)                 # (the widget takes focus; the keys move the stars)
            b.setAccessibleName(plural(i, tr("star"), tr("stars")))
            b.clicked.connect(lambda _=False, n=i: self.set_value(None if self._value == n else n, emit=True))
            row.addWidget(b)
            self.buttons.append(b)
        row.addStretch(1)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setAccessibleName(tr("Rating"))            # (stable; the value is its description)
        self._paint()

    def value(self):
        return self._value

    def set_value(self, n, emit=False):
        n = None if n is None else max(0, min(5, int(n)))
        same = n == self._value
        self._value = n
        self._paint()
        if emit and not same:
            self.changed.emit(n)

    def _paint(self):
        """The stars in the current palette, as the app's line icons at glyph size: filled in the accent,
        empty ones outlined in ink once rated (0 stars too), in the secondary ink while there is no
        rating at all."""
        n = self._value or 0
        empty = "muted" if self._value is None else "ink"
        px = style.TYPE["glyph"][0]
        for i, b in enumerate(self.buttons, 1):
            b.setText(STAR if i <= n else NO_STAR)       # (not shown: the icon is; kept for the stars' words)
            b.setToolButtonStyle(Qt.ToolButtonIconOnly)
            b.setIconSize(QSize(px, px))
            b.setIcon(star_icon(i <= n, "accent" if i <= n else empty, px))
        words = tr("No rating") if self._value is None else tr("{0} of 5 stars").format(self._value)
        self.setAccessibleDescription(words)
        self.setToolTip(words)

    def changeEvent(self, e):
        super().changeEvent(e)
        if e.type() in (QEvent.PaletteChange, QEvent.StyleChange):   # (Light or Dark chosen: the new colours)
            self._paint()

    def paintEvent(self, e):
        super().paintEvent(e)
        if self.hasFocus():                              # (the focus ring the app's tool buttons have)
            p = QPainter(self)
            p.setRenderHint(QPainter.Antialiasing)
            p.setPen(QPen(QColor(style.color("accent")), self.RING))
            p.setBrush(Qt.NoBrush)
            last = self.buttons[-1].geometry()
            r = QRectF(self.RING / 2, self.RING / 2, last.right() + self.RING, self.height() - self.RING)
            p.drawRoundedRect(r, style.CONTROL, style.CONTROL)
            p.end()

    def keyPressEvent(self, e):
        k, n = e.key(), self._value or 0
        if Qt.Key_0 <= k <= Qt.Key_5:
            self.set_value(k - Qt.Key_0, emit=True)
        elif k in (Qt.Key_Right, Qt.Key_Up):
            self.set_value(min(5, n + 1), emit=True)
        elif k in (Qt.Key_Left, Qt.Key_Down):
            self.set_value(max(0, n - 1), emit=True)
        elif k in (Qt.Key_Delete, Qt.Key_Backspace):
            self.set_value(None, emit=True)
        else:
            super().keyPressEvent(e)


class RatingBox(QWidget):
    """The inspector's "Rating" row: the rating of the file shown (all files: theirs when they
    agree), and your stars as an edit of the plan. owner: the Inspector (set_tag, clear_tag,
    edit_of, can_edit)."""

    def __init__(self, owner):
        super().__init__()
        self.owner, self.album, self.file = owner, None, None
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(style.SPACE["s"])
        lb = QLabel(tr("Rating"))
        lb.setProperty("role", "caption")
        row.addWidget(lb)
        self.stars = StarRating()
        self.stars.changed.connect(self.rated)
        row.addWidget(self.stars)
        self.note = QLabel("")
        self.note.setProperty("role", "caption")
        row.addWidget(self.note, 1)
        self._token = None

    def show_album(self, album, file=None):
        """Show the rating of album's file (None: all its files), read on a worker."""
        self.album, self.file = album, file
        files = [file] if file else [fe["src"] for fe in (album or {}).get("files") or []]
        mine = self._mine()
        editable = bool(album) and (self.owner.can_edit(album) if hasattr(self.owner, "can_edit") else True)
        self.stars.setEnabled(editable)
        if mine is not None and mine != mba_keep():
            self._token = None                          # (a read still running is not shown)
            self.stars.set_value(mba.stars(mine))
            self.note.setText(tr("Your rating"))
            return
        token = self._token = object()

        def work(_w):
            got = set()
            for f in files:
                try:
                    got.add(mba.read_rating(f))
                except Exception:
                    got.add(None)
            return got

        def done(got):
            if self._token is not token:
                return
            self.stars.set_value(next(iter(got)) if len(got) == 1 else None)
            self.note.setText(tr("Differs between files") if len(got) > 1
                              else tr("No rating") if self.stars.value() is None else "")
        start(self, work, done, failed=lambda _m: None)

    def _mine(self):
        """Your edit's rating value for what is shown, or None."""
        if not self.album or not hasattr(self.owner, "edit_of"):
            return None
        e = self.owner.edit_of(self.album) or {}
        if self.file and "~rating" in ((e.get("file_tags") or {}).get(self.file) or {}):
            return e["file_tags"][self.file]["~rating"] or []
        if "~rating" in (e.get("tags") or {}):
            return e["tags"]["~rating"] or []
        return None

    def rated(self, n):
        if not self.album:
            return
        self._token = None                              # (the file's rating, still being read, is not shown)
        self.owner.set_tag("~rating", None if n is None else [str(n)], file=self.file, album=self.album)
        self.note.setText(tr("Your rating") if n is not None else tr("No rating"))


def mba_keep():
    import plan as planmod
    return planmod.KEEP
