# The MusicBrainz account in the app (gui/account.py), offscreen with stubbed HTTP: the
# Preferences pane's states, Log In (the browser is never opened: QDesktopServices.openUrl is
# patched) and Log Out, collections and rating submission - each confirmed in a sheet that shows
# exactly what is sent, nothing sent when it is declined, the work on workers - and the inspector's
# star rating as an edit of the plan. No network, no real keys or config.
# Patch sites: gui.account.confirm_send (the "what is sent" sheet), gui.account.ask_code (the code
# sheet), gui.sheets.question / confirm / information / warning.
import os as _os, tempfile as _tempfile  # noqa: E402
_os.environ["XDG_CONFIG_HOME"] = _tempfile.mkdtemp(prefix="tm-config-")
_os.environ["XDG_STATE_HOME"] = _tempfile.mkdtemp(prefix="tm-state-")
_os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
_os.environ.setdefault("TAGMENDER_TAGGER_INI", "/nonexistent")

import shutil
import subprocess
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
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication, QMainWindow
except ImportError:                                           # GUI is optional
    QApplication = None

from test_mb_account import COL, COL2, REC, REL, FakeHttp, token_answer  # noqa: E402

FFMPEG = shutil.which("ffmpeg")
APP = QApplication.instance() or QApplication([]) if QApplication else None


def wait(cond, ms=5000):
    end = time.monotonic() + ms / 1000
    while not cond() and time.monotonic() < end:
        QTest.qWait(10)
    return cond()


def drain(owner):
    """Every worker owner started finished (a thread destroyed while it runs aborts the process)."""
    from gui.work import _alive
    wait(lambda: not any(_alive(t) for t, _w in getattr(owner, "_threads", [])))
    QTest.qWait(20)


def flac(p, **tags):
    meta = sum((["-metadata", f"{k}={v}"] for k, v in tags.items()), [])
    subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
                    "-c:a", "flac", *meta, "-y", str(p)], check=True)
    return p


@unittest.skipIf(QApplication is None, "PySide6 is needed")
class Base(unittest.TestCase):
    def setUp(self):
        import mb_account as mba
        from gui import sheets
        self.mba, self.sheets = mba, sheets
        env = mock.patch.dict(_os.environ, {"TAGMENDER_MB_CLIENT_ID": "my-client-id",
                                            "TAGMENDER_MB_CLIENT_SECRET": "CLIENT-SECRET-xyz"})
        env.start()
        self.addCleanup(env.stop)
        mock.patch.object(mba, "MIN_INTERVAL", 0).start()
        self.told = []
        for kind in ("information", "warning"):
            mock.patch.object(sheets, kind, lambda _p, h, b=None, k=kind: self.told.append((k, h, b))).start()
        self.addCleanup(mock.patch.stopall)
        mba._forget()
        self.addCleanup(mba._forget)
        self.addCleanup(self.flush)                     # runs last: the widgets go now, not at exit
        self.win = QMainWindow()
        self.addCleanup(self.win.deleteLater)
        self.routes = {("POST", "https://musicbrainz.org/oauth2/token"): token_answer(1),
                       ("GET", "https://musicbrainz.org/oauth2/userinfo"): (200, {"sub": "alice"}),
                       ("POST", "https://musicbrainz.org/oauth2/revoke"): (200, b"")}
        self.http = FakeHttp(self.routes)
        self.acct = mba.Account(http=self.http)

    def tearDown(self):
        drain(self.win)

    @staticmethod
    def flush():
        """Delete the widgets deleteLater left (alive at exit, they crash Qt's shutdown)."""
        from PySide6.QtCore import QEvent
        QApplication.sendPostedEvents(None, QEvent.DeferredDelete)

    def login(self):
        self.acct.authorize_url()
        self.acct.exchange("the-code")

    def sent(self, method):
        return [c for c in self.http.calls if c[0] == method]


class TestPane(Base):
    def test_states(self):
        from gui import account
        with mock.patch.dict(_os.environ, {"TAGMENDER_MB_CLIENT_ID": "", "TAGMENDER_MB_CLIENT_SECRET": ""}):
            pane = account.AccountPane(self.win, open_keys=lambda: None, acct=self.acct)
            pane.setParent(self.win)
            self.assertFalse(pane.in_button.isEnabled())
            self.assertIn("client ID and secret", pane.state.text())
            self.assertFalse(pane.keys_button.isHidden())
        pane.refresh()
        self.assertTrue(pane.in_button.isEnabled())
        self.assertEqual(pane.state.text(), "Not logged in")
        self.assertIn("musicbrainz.org/account/applications", pane.how.text())
        self.assertIn("Installed application", pane.how.text())
        self.login()
        pane.refresh()
        self.assertEqual(pane.state.text(), "Logged in as alice")
        self.assertTrue(pane.in_button.isHidden())
        self.assertFalse(pane.out_button.isHidden())
        self.assertFalse(pane.server.isEnabled())

    def test_a_key_file_others_can_read_shows_the_fix_not_a_crash(self):
        from gui import account
        import credentials as cr
        with mock.patch.dict(_os.environ, {"TAGMENDER_MB_CLIENT_ID": "", "TAGMENDER_MB_CLIENT_SECRET": ""}):
            p = cr.path_of("mb_client_secret")
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text("x")
            _os.chmod(p, 0o644)
            self.addCleanup(p.unlink, True)
            pane = account.AccountPane(self.win, acct=self.acct)
            pane.setParent(self.win)
            self.assertIn("chmod 600", pane.state.text())
            self.assertFalse(pane.in_button.isEnabled())
            self.assertFalse(account.log_in(self.win, self.acct))
        self.assertIn("chmod 600", self.told[-1][2])
        self.assertEqual(self.http.calls, [])

    def test_choosing_the_metabrainz_server(self):
        from gui import account
        pane = account.AccountPane(self.win, acct=self.acct)
        pane.setParent(self.win)
        pane.server.setCurrentIndex(pane.server.findData("metabrainz"))
        self.assertEqual(self.acct.provider, "metabrainz")
        self.assertIn("metabrainz.org", pane.how.text())

    def test_account_of_is_one_per_window(self):
        from gui import account
        self.assertIs(account.account_of(self.win), account.account_of(self.win))

    def test_a_pane_with_no_window_shares_one_account(self):
        from gui import account
        mock.patch.object(account, "_shared", []).start()
        pane = account.AccountPane(None)
        self.addCleanup(pane.deleteLater)
        self.assertIs(pane.acct, account.account_of(None))
        other = account.AccountPane(None)
        self.addCleanup(other.deleteLater)
        self.assertIs(other.acct, pane.acct)


class TestLogInOut(Base):
    def confirmer(self, answer, asked):
        def confirm(_p, headline, body, sent, action, destructive=False):
            asked.append((headline, body, sent, action, destructive, len(self.http.calls)))
            return answer
        return confirm

    def test_log_in_opens_the_page_takes_the_code_on_a_worker(self):
        from gui import account, work
        opened = []
        names = []
        asked = []
        with mock.patch.object(self.sheets, "question", return_value=True) as q, \
                mock.patch.object(account.QDesktopServices, "openUrl", lambda u: opened.append(u.toString())), \
                mock.patch.object(account, "ask_code", return_value=("the-code", True)), \
                mock.patch.object(account, "confirm_send", self.confirmer(True, asked)), \
                mock.patch.object(account, "start", wraps=work.start) as st:
            self.assertTrue(account.log_in(self.win, self.acct, done=names.append))
            self.assertTrue(wait(lambda: names))
        self.assertEqual(names, ["alice"])
        headline, body, sent, action, _d, before = asked[0]
        self.assertEqual(before, 0)                       # (asked before anything was sent)
        self.assertEqual(sent.splitlines()[0], "POST https://musicbrainz.org/oauth2/token")
        for name in ("grant_type", "code", "client_id", "client_secret", "redirect_uri"):
            self.assertIn(name, sent)
        self.assertNotIn("code_verifier", sent)
        self.assertIn("GET https://musicbrainz.org/oauth2/userinfo", sent)
        self.assertEqual(sent.count("User-Agent: TagMender/"), 2)            # (each request with its headers)
        for value in ("the-code", "CLIENT-SECRET-xyz", "my-client-id", "ACCESS-tok-1"):
            self.assertNotIn(value, sent + body)          # (the fields by name, never their values)
        self.assertEqual([c[:2] for c in self.http.calls],
                         [("POST", "https://musicbrainz.org/oauth2/token"),
                          ("GET", "https://musicbrainz.org/oauth2/userinfo")])
        self.assertEqual(st.call_count, 1)
        self.assertIn("musicbrainz.org", q.call_args[0][1])
        self.assertTrue(opened[0].startswith("https://musicbrainz.org/oauth2/authorize?"))
        self.assertTrue(self.acct.logged_in())
        self.assertIn("alice", self.told[-1][1])

    def test_metabrainz_login_names_the_verifier(self):
        from gui import account
        self.acct.provider = "metabrainz"
        self.assertIn("code_verifier", account.login_text(self.acct)[1])
        self.assertIn("POST https://metabrainz.org/oauth2/token", account.login_text(self.acct)[1])

    def test_declining_what_the_login_sends_sends_nothing(self):
        from gui import account
        asked = []
        with mock.patch.object(self.sheets, "question", return_value=True), \
                mock.patch.object(account.QDesktopServices, "openUrl"), \
                mock.patch.object(account, "ask_code", return_value=("the-code", True)), \
                mock.patch.object(account, "confirm_send", self.confirmer(False, asked)):
            self.assertFalse(account.log_in(self.win, self.acct))
        self.assertEqual(len(asked), 1)
        self.assertEqual(self.http.calls, [])
        self.assertFalse(self.acct.logged_in())

    def test_declined_or_no_keys_sends_nothing(self):
        from gui import account
        with mock.patch.object(self.sheets, "question", return_value=False), \
                mock.patch.object(account.QDesktopServices, "openUrl") as op:
            self.assertFalse(account.log_in(self.win, self.acct))
            op.assert_not_called()
        with mock.patch.dict(_os.environ, {"TAGMENDER_MB_CLIENT_ID": ""}):
            self.assertFalse(account.log_in(self.win, self.acct))
        self.assertEqual(self.told[-1][0], "warning")
        self.assertEqual(self.http.calls, [])

    def test_code_sheet_offscreen_never_blocks(self):
        from gui import account
        self.assertEqual(account.ask_code(self.win, "https://x"), ("", False))
        self.assertEqual(self.sheets.shown[-1][0], "ask_code")

    def test_log_out_confirmed_revokes(self):
        from gui import account
        self.login()
        done, asked = [], []
        n, name = len(self.http.calls), self.acct.username
        with mock.patch.object(account, "confirm_send", self.confirmer(True, asked)):
            account.log_out(self.win, self.acct, done=done.append)
            self.assertTrue(wait(lambda: done))
        headline, body, sent, action, destructive, before = asked[0]
        self.assertEqual((action, destructive, before), ("Log Out", True, n))
        self.assertEqual(sent.splitlines()[0], "POST https://musicbrainz.org/oauth2/revoke")
        self.assertIn("User-Agent: TagMender/", sent)
        self.assertEqual(headline, f"Log out of MusicBrainz as {name}?")     # (one whole sentence)
        for name in ("token", "client_id", "client_secret"):
            self.assertIn(name, sent)
        for value in ("REFRESH-tok-1", "CLIENT-SECRET-xyz", "my-client-id"):
            self.assertNotIn(value, sent + body)
        self.assertEqual(done, [True])
        self.assertFalse(self.acct.logged_in())
        self.assertEqual(self.sent("POST")[-1][1], "https://musicbrainz.org/oauth2/revoke")

    def test_log_out_declined_keeps_the_login(self):
        from gui import account
        self.login()
        n = len(self.http.calls)
        with mock.patch.object(account, "confirm_send", self.confirmer(False, [])):
            self.assertFalse(account.log_out(self.win, self.acct))
        self.assertTrue(self.acct.logged_in())
        self.assertEqual(len(self.http.calls), n)

    def test_destructive_send_sheet_defaults_to_cancel(self):
        from gui import account
        s = account._send_sheet(self.win, "Log out?", "body", "POST x", "Log Out", destructive=True)
        self.assertEqual(s.default_button().text(), "Cancel")
        self.assertEqual(s.buttons["Log Out"].property("role"), "destructive")
        s.deleteLater()


class TestCollections(Base):
    def setUp(self):
        super().setUp()
        self.routes.update({
            ("GET", f"{self.mba.WS}/collection"): (200, {"collections": [
                {"id": COL, "name": "Owned", "entity-type": "release", "release-count": 2},
                {"id": COL2, "name": "Wish list", "entity-type": "release", "release-count": 0}]}),
            ("GET", f"{self.mba.WS}/release/{REL}"): (200, {"collections": [{"id": COL}]}),
            ("PUT", f"{self.mba.WS}/collection/{COL2}/releases/{REL}"): (200, b""),
            ("DELETE", f"{self.mba.WS}/collection/{COL}/releases/{REL}"): (200, b"")})
        self.login()
        self.album = {"release": {"musicbrainz_albumid": [REL], "album": ["Alpha"]}, "files": []}

    def run_sheet(self, answer, read=True):
        """answer: the send sheet's; read: the sheet before anything is read."""
        from gui import account
        seen, asked = [], []

        def choose(s):
            seen.append({c: b.isChecked() for c, b in s.boxes.items()})
            for c, b in s.boxes.items():
                b.setChecked(c == COL2)
            return s.changes()

        def confirm(_p, headline, body, sent, action, destructive=False):
            asked.append((headline, body, sent, action))
            return read if len(asked) == 1 else answer
        with mock.patch.object(account, "confirm_send", confirm):
            self.assertEqual(account.open_collections(self.win, self.album, self.acct, choose=choose), read)
            if read:
                wait(lambda: len(asked) > 1)
            QTest.qWait(50)
            if read and answer:
                wait(lambda: self.told)
        return seen, asked

    def test_reading_is_confirmed_first_with_each_request(self):
        n = len(self.http.calls)
        _seen, asked = self.run_sheet(False)
        headline, body, sent, action = asked[0]
        self.assertEqual((headline, action), ("Read your MusicBrainz collections?", "Read Collections"))
        self.assertIn("as alice", body)
        self.assertIn(f"GET {self.mba.WS}/collection?limit=100&fmt=json", sent)
        self.assertIn(f"GET {self.mba.WS}/release/{REL}?inc=user-collections&fmt=json", sent)
        self.assertIn("POST https://musicbrainz.org/oauth2/token", sent)
        self.assertIn("refresh_token, client_id, client_secret", sent)
        gets = [c[1] for c in self.http.calls[n:] if c[0] == "GET"]
        self.assertEqual(gets, [line.split(" ", 1)[1] for line in sent.splitlines() if line.startswith("GET ")])

    def test_declining_the_read_sends_nothing(self):
        n = len(self.http.calls)
        _seen, asked = self.run_sheet(True, read=False)
        self.assertEqual(len(asked), 1)
        self.assertEqual(self.http.calls[n:], [])

    def test_ticks_then_confirms_each_request_then_sends(self):
        seen, asked = self.run_sheet(True)
        self.assertEqual(seen, [{COL: True, COL2: False}])
        headline, body, sent, action = asked[1]
        self.assertIn("as alice", body)
        self.assertIn("Added to “Wish list”", body)
        self.assertIn("Removed from “Owned”", body)
        v = self.mba.VERSION
        requests, refresh = sent.split("\n\n")
        lines = requests.splitlines()
        self.assertEqual(lines[0::2], [
            f"PUT {self.mba.WS}/collection/{COL2}/releases/{REL}?client=TagMender-{v}",
            f"DELETE {self.mba.WS}/collection/{COL}/releases/{REL}?client=TagMender-{v}"])
        for line in lines[1::2]:                                       # (the headers sent with each)
            self.assertIn("Authorization: Bearer …", line)
            self.assertIn(f"User-Agent: {self.mba.USER_AGENT}", line)
        requests = "\n".join(lines[0::2])
        self.assertIn("POST https://musicbrainz.org/oauth2/token", refresh)
        self.assertEqual(action, "Send 2 Changes")
        self.assertEqual([c[1] for c in self.sent("PUT") + self.sent("DELETE")],
                         [line.split(" ", 1)[1] for line in requests.splitlines()])
        self.assertIn("Changed 2 collections", self.told[-1][1])

    def test_declined_sends_nothing(self):
        self.run_sheet(False)
        self.assertEqual(self.sent("PUT") + self.sent("DELETE"), [])

    def test_the_first_change_failing_says_none_changed(self):
        self.routes[("PUT", f"{self.mba.WS}/collection/{COL2}/releases/{REL}")] = (404, {"error": "Not found"})
        self.run_sheet(True)
        kind, headline, body = self.told[-1]
        self.assertEqual((kind, headline), ("warning", "The collections did not change."))
        self.assertIn("Not found", body)
        self.assertEqual(self.sent("DELETE"), [])

    def test_an_unexpected_failure_says_some_may_have_changed(self):
        self.routes[("PUT", f"{self.mba.WS}/collection/{COL2}/releases/{REL}")] = lambda *a: 1 / 0
        self.run_sheet(True)
        kind, headline, _body = self.told[-1]
        self.assertEqual(kind, "warning")
        self.assertIn("may have changed", headline)
        self.assertEqual(self.sent("DELETE"), [])

    def test_a_later_change_failing_names_the_ones_that_went_through(self):
        self.routes[("DELETE", f"{self.mba.WS}/collection/{COL}/releases/{REL}")] = \
            (404, {"error": "Not found"})
        self.run_sheet(True)
        kind, headline, body = self.told[-1]
        self.assertEqual((kind, headline), ("warning", "Some of the collections changed."))
        self.assertIn("added to “Wish list”", body)
        self.assertIn("Not found", body)

    def test_no_release_or_no_login(self):
        from gui import account
        n = len(self.http.calls)
        self.assertFalse(account.open_collections(self.win, {"files": []}, self.acct))
        self.acct.logout()
        self.assertFalse(account.open_collections(self.win, self.album, self.acct))
        self.assertEqual(len([c for c in self.http.calls[n:] if "/ws/2/" in c[1]]), 0)

    def test_release_of(self):
        from gui import account
        self.assertEqual(account.release_of(self.album), REL)
        self.assertEqual(account.release_of({"identification": {"provider": "musicbrainz", "release_id": REL}}), REL)
        self.assertEqual(account.release_of({"identification": {"provider": "discogs", "release_id": "123"}}), "")


@unittest.skipIf(FFMPEG is None, "ffmpeg is needed for the test audio")
class TestRatings(Base):
    def setUp(self):
        super().setUp()
        self.tmp = Path(tempfile.mkdtemp(prefix="tm-gui-rating-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.f1 = flac(self.tmp / "1.flac", title="One", MUSICBRAINZ_TRACKID=REC)
        import tagio
        f = tagio.open_file(self.f1)
        self.mba.write_rating(f, 2)
        f.save()
        self.album = {"source_dir": str(self.tmp), "status": "ready",
                      "files": [{"src": str(self.f1), "changes": {}}]}
        self.routes[("POST", f"{self.mba.WS}/rating")] = (200, b"<message>OK</message>")

    def submit(self, answer, edit=None):
        from gui import account
        asked = []

        def confirm(_p, headline, body, sent, action, destructive=False):
            asked.append((headline, body, sent, action))
            return answer
        with mock.patch.object(account, "confirm_send", confirm):
            self.assertTrue(account.submit_ratings(self.win, self.album, edit, self.acct))
            wait(lambda: asked)
            if answer:
                wait(lambda: self.told)
        return asked

    def test_submit_shows_the_xml_then_posts_it(self):
        self.login()
        asked = self.submit(True, {"tags": {}, "file_tags": {str(self.f1): {"~rating": ["4"]}}})
        headline, body, sent, action = asked[0]
        self.assertEqual(headline, "Submit 1 rating to MusicBrainz?")
        self.assertIn("One: ★★★★☆", body)
        self.assertIn(f"POST {self.mba.WS}/rating?client=TagMender-{self.mba.VERSION}", sent)
        xml = self.mba.ratings_xml({REC: 4})
        from gui import account
        shown = account.pretty_xml(xml)
        self.assertIn(shown, sent)                                    # (one element a line, for reading)
        self.assertGreater(len(shown.splitlines()), 4)
        self.assertEqual("".join(x.lstrip(" ") for x in shown.splitlines()), xml)   # (the same bytes, sent)
        self.assertIn("POST https://musicbrainz.org/oauth2/token\n  form fields: grant_type=refresh_token", sent)
        self.assertEqual(self.sent("POST")[-1][3], xml.encode())
        self.assertIn("Submitted 1 rating", self.told[-1][1])

    def test_declined_sends_nothing(self):
        self.login()
        self.submit(False)
        self.assertNotIn(f"{self.mba.WS}/rating", [c[1].split("?")[0] for c in self.http.calls])

    def test_offscreen_sheet_records_what_would_be_sent(self):
        from gui import account
        self.assertFalse(account._confirm_send(self.win, "Submit?", "body", "POST x\n\n<xml/>", "Submit"))
        self.assertEqual(self.sheets.shown[-1][0], "send")
        self.assertIn("<xml/>", self.sheets.shown[-1][2])

    def test_star_rating_mouse_and_keys(self):
        from gui import account
        w = account.StarRating()
        got = []
        w.changed.connect(got.append)
        w.buttons[2].click()
        w.buttons[2].click()                               # (the same star again: no rating)
        QTest.keyClick(w, Qt.Key_5)
        QTest.keyClick(w, Qt.Key_Left)
        QTest.keyClick(w, Qt.Key_Delete)
        self.assertEqual(got, [3, None, 5, 4, None])
        w.set_value(2)
        self.assertEqual(w.accessibleName(), "Rating")                 # (stable: the value is its description)
        self.assertEqual(w.accessibleDescription(), "2 of 5 stars")
        self.assertEqual([b.text() for b in w.buttons], ["★", "★", "☆", "☆", "☆"])

    def test_rating_box_reads_the_file_and_sets_an_edit(self):
        from gui import account

        class Owner:
            def __init__(self):
                self.calls, self.edit = [], None

            def can_edit(self, _a):
                return True

            def edit_of(self, _a):
                return self.edit

            def set_tag(self, field_, value, file=None, album=None):
                self.calls.append((field_, value, file))
        owner = Owner()
        box = account.RatingBox(owner)
        box.show_album(self.album, str(self.f1))
        self.assertTrue(wait(lambda: box.stars.value() == 2))
        box.stars.buttons[3].click()
        self.assertEqual(owner.calls, [("~rating", ["4"], str(self.f1))])
        owner.edit = {"tags": {}, "file_tags": {str(self.f1): {"~rating": ["5"]}}}
        box.show_album(self.album, str(self.f1))
        self.assertEqual((box.stars.value(), box.note.text()), (5, "Your rating"))
        drain(box)

    def test_rating_box_a_star_clicked_while_the_file_is_read_stays(self):
        from gui import account
        calls = []

        class Owner:
            def can_edit(self, _a):
                return True

            def edit_of(self, _a):
                return None

            def set_tag(self, field_, value, file=None, album=None):
                calls.append((field_, value))
        go, reading = threading.Event(), threading.Event()
        real = self.mba.read_rating

        def slow(path, *a, **k):
            reading.set()
            go.wait(5)
            return real(path, *a, **k)
        box = account.RatingBox(Owner())
        with mock.patch.object(self.mba, "read_rating", slow):
            box.show_album(self.album, str(self.f1))
            self.assertTrue(reading.wait(5))
            box.stars.buttons[4].click()                   # (5 stars, before the file's 2 arrive)
            go.set()
            drain(box)
        self.assertEqual(calls, [("~rating", ["5"])])
        self.assertEqual((box.stars.value(), box.note.text()), (5, "Your rating"))
        # (and an edit shown while a read still runs)
        go.clear()
        reading.clear()
        with mock.patch.object(self.mba, "read_rating", slow):
            box.show_album(self.album, None)
            self.assertTrue(reading.wait(5))
            box.owner.edit_of = lambda _a: {"tags": {"~rating": ["1"]}}
            box.show_album(self.album, None)
            go.set()
            drain(box)
        self.assertEqual((box.stars.value(), box.note.text()), (1, "Your rating"))


if __name__ == "__main__":
    unittest.main(argv=[sys.argv[0]])
