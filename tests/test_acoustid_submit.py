# Submit AcoustIDs (acoustid_submit.py, gui/acoustid_submit.py), offscreen with stubbed HTTP and a
# stubbed fpcalc: the user key as its own 0600 credential, the tracks read from a plan entry (the
# preview's recording IDs over the files' own), Picard's submission fields (mbid, or the names when
# the lengths differ by more than 30 s) with fileformat and bitrate, the request AcoustID gets
# (client, user, clientversion, format, indexed fields), batches under 1 MB, errors without keys,
# submission_status (a POST too: no key in a URL), the real http_post against a stubbed urlopen, and
# the sheets: set-up, confirmation (lists every track, recording ID and where it came from; only the
# action sends), status with Check Again, failures. No network: an unexpected request fails.
# Generated audio in a temp folder; the files are checked byte for byte afterwards.
# Patch sites: acoustid_submit.http_post / urllib.request.urlopen / _fpcalc / shutil.which.
import os as _os, tempfile as _tempfile  # noqa: E402
_os.environ["XDG_CONFIG_HOME"] = _tempfile.mkdtemp(prefix="tm-config-")
_os.environ["XDG_STATE_HOME"] = _tempfile.mkdtemp(prefix="tm-state-")
_os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
_os.environ.setdefault("TAGMENDER_TAGGER_INI", "/nonexistent")
for _k in ("TAGMENDER_ACOUSTID_KEY", "TAGMENDER_ACOUSTID_USER_KEY"):    # (never the user's real keys)
    _os.environ.pop(_k, None)

import hashlib
import io
import json
import logging
import socket
import shutil
import stat
import subprocess
import urllib.error
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
try:
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication, QDialog, QMainWindow
except ImportError:                                           # GUI is optional
    QApplication = None

import acoustid_submit as acs  # noqa: E402
import credentials  # noqa: E402

REAL_POST = acs.http_post                                     # (before any test patches it)

APP, USER = "app-key-SECRET-1", "user-key-SECRET-2"
RIDS = ("0a8e97fd-4f6a-4bd7-8e4b-6d3a0b2ad2f1", "1b9f08ae-5a7b-4ce8-9f5c-7e4b1c3be3a2")


def make_album(d: Path):
    """Two FLACs (one with its recording ID in its tags), an MP3 (ID from the preview) and a FLAC
    with no ID anywhere."""
    from mutagen.flac import FLAC
    files = []
    for i, ext in enumerate(("flac", "mp3", "flac")):
        f = d / f"0{i + 1} t{i}.{ext}"
        subprocess.run(["ffmpeg", "-v", "quiet", "-y", "-f", "lavfi",
                        "-i", f"sine=frequency={440 + 100 * i}:duration=3",
                        *(["-b:a", "128k"] if ext == "mp3" else []), str(f)], check=True)
        files.append(f)
    tags = FLAC(files[0])
    tags.update({"title": "One", "tracknumber": "1", "musicbrainz_trackid": RIDS[0], "album": "Alpha"})
    tags.save()
    tags = FLAC(files[2])
    tags.update({"title": "Three", "tracknumber": "3"})
    tags.save()
    album = {"source_dir": str(d), "status": "ready", "files": [
        {"src": str(files[0]), "dst": str(files[0]), "op": "none", "changes": {}},
        {"src": str(files[1]), "dst": str(d / "moved" / files[1].name), "op": "move",
         "changes": {"title": ["Two"], "tracknumber": ["2"], "musicbrainz_recordingid": [RIDS[1]],
                     "artist": ["Band"], "date": ["2005-03-01"]}},
        {"src": str(files[2]), "dst": str(files[2]), "op": "none", "changes": {}}]}
    return album, files


def digest(files):
    return {str(f): hashlib.sha256(f.read_bytes()).hexdigest() for f in files}


def fake_fp(path):
    return {"duration": 3, "fingerprint": "AQAA" + Path(path).stem.replace(" ", "")}


class Http:
    """Records requests; answers from a queue (posts: /submit, gets: /submission_status, both sent as
    POST); an unexpected request fails the test."""
    def __init__(self, test, posts=(), gets=()):
        self.test, self.posts, self.gets = test, list(posts), list(gets)
        self.sent = []

    def post(self, url, fields):
        self.sent.append(("POST", url, list(fields)))
        queue = self.gets if url.endswith("/submission_status") else self.posts
        if not queue:
            self.test.fail(f"unexpected POST {url}")
        a = queue.pop(0)
        return a(fields) if callable(a) else a


def ok_answer(fields):
    n = len({k.split(".")[1] for k, _v in fields if k.startswith("fingerprint.")})
    return {"status": "ok", "submissions": [{"index": str(i), "id": 100 + i, "status": "pending"} for i in range(n)]}


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="tm-acs-"))
        self.album, self.files = make_album(self.tmp)
        self.before = digest(self.files)
        for name in ("acoustid", acs.USER):
            credentials.path_of(name).unlink(missing_ok=True)
        self.p_fp = mock.patch.object(acs, "_fpcalc", side_effect=fake_fp)
        self.p_fp.start()
        self.p_which = mock.patch.object(acs.shutil, "which", return_value="/usr/bin/fpcalc")
        self.p_which.start()
        self.http = Http(self)
        self.p_post = mock.patch.object(acs, "http_post", side_effect=lambda u, f: self.http.post(u, f))
        self.p_post.start()
        # (no network: anything that gets past http_post's patch fails the test)
        self.p_open = mock.patch.object(acs.urllib.request, "urlopen", side_effect=AssertionError("network"))
        self.p_sock = mock.patch.object(socket, "create_connection", side_effect=AssertionError("network"))
        self.p_open.start()
        self.p_sock.start()

    def tearDown(self):
        for p in (self.p_fp, self.p_which, self.p_post, self.p_open, self.p_sock):
            p.stop()
        self.assertEqual(digest(self.files), self.before, "a music file changed")
        shutil.rmtree(self.tmp, ignore_errors=True)

    def keys(self):
        credentials.store("acoustid", APP)
        acs.store_user_key(USER)


class Engine(Base):
    def test_user_key_is_its_own_0600_credential(self):
        self.assertIn(acs.USER, credentials.FILES)
        self.assertEqual(acs.user_key(), "")
        p = acs.store_user_key(USER)
        self.assertEqual(stat.S_IMODE(p.stat().st_mode), 0o600)
        self.assertEqual(acs.user_key(), USER)
        self.assertEqual(credentials.source(acs.USER), "file")
        self.assertNotEqual(credentials.path_of(acs.USER), credentials.path_of("acoustid"))
        self.assertIn("acoustid.org/api-key", credentials.LABELS[acs.USER])

    def test_missing_names_both_keys_and_where_to_get_them(self):
        need = " ".join(acs.missing())
        self.assertIn(acs.APP_KEY_URL, need)
        self.assertIn(acs.USER_KEY_URL, need)
        self.keys()
        self.assertEqual(acs.missing(), [])
        with mock.patch.object(acs.shutil, "which", return_value=None):
            self.assertIn("fpcalc", " ".join(acs.missing()))

    def test_tracks_from_the_plan_and_the_files(self):
        tr = acs.tracks_of(self.album)
        self.assertEqual([t.recording_id for t in tr], [RIDS[0], RIDS[1], ""])
        self.assertEqual([t.id_from for t in tr], ["tags", "preview", ""])
        self.assertEqual(tr[1].path, str(self.files[1]))              # (not moved yet: read where it is)
        self.assertEqual((tr[1].title, tr[1].tracknumber, tr[1].year), ("Two", "2", "2005"))
        self.assertEqual((tr[0].fileformat, tr[1].fileformat), ("FLAC", "MP3"))
        self.assertTrue(tr[1].bitrate and 100 <= tr[1].bitrate <= 160)
        self.assertIn("No recording ID", tr[2].problem)
        acs.fingerprint(tr)
        self.assertEqual([t.sendable for t in tr], [True, True, False])
        self.assertEqual(acs._fpcalc.call_count, 2)                  # (a track not to be sent is not read)

    def test_fpcalc_failure_is_not_sent(self):
        tr = acs.tracks_of(self.album)
        with mock.patch.object(acs, "_fpcalc", return_value=None):
            acs.fingerprint(tr)
        self.assertFalse(any(t.sendable for t in tr))
        self.assertIn("fpcalc", tr[0].problem)

    def test_fields_follow_picard(self):
        t = acs.Track(path="x", title="One", artist="A", album="Al", albumartist="AA", tracknumber="1",
                      discnumber="1", year="2005", recording_id=RIDS[0], fileformat="FLAC", bitrate=900,
                      fingerprint="AQAA", duration=200)
        self.assertEqual(t.args(), {"fingerprint": "AQAA", "duration": "200", "mbid": RIDS[0],
                                    "fileformat": "FLAC", "bitrate": "900"})
        t.length_ms = 200000 + 29000
        self.assertIn("mbid", t.args())
        t.length_ms = 200000 + 31000                                 # (lengths too far apart: names instead)
        a = t.args()
        self.assertNotIn("mbid", a)
        self.assertEqual((a["track"], a["artist"], a["album"], a["albumartist"], a["trackno"], a["discno"],
                          a["year"]), ("One", "A", "Al", "AA", "1", "1", "2005"))
        self.assertEqual(acs._length_ms("3:25"), 205000)

    def test_describe_says_what_is_sent_without_keys(self):
        self.keys()
        tr = acs.fingerprint(acs.tracks_of(self.album))
        text = acs.describe(tr)
        for rid in RIDS:
            self.assertIn(rid, text)
        self.assertIn("fileformat=MP3", text)
        self.assertIn("Not sent", text)
        self.assertIn("user key", text)
        self.assertNotIn(APP, text)
        self.assertNotIn(USER, text)
        self.assertNotIn(str(self.tmp), text)                        # (no paths)
        lines = text.splitlines()
        one = next(x for x in lines if x.strip().startswith("1. "))
        two = next(x for x in lines if x.strip().startswith("2. "))
        self.assertIn(RIDS[0], one)
        self.assertIn("recording ID from the file's tags", one)
        self.assertIn(RIDS[1], two)
        self.assertIn("not yet written to the file", two)
        self.assertIn("length not checked", two)                     # (no MusicBrainz length in this plan)

    def test_plan_length_decides_whether_the_id_is_sent(self):
        """The plan's file entry carries MusicBrainz's track length; fpcalc says 3 s."""
        self.keys()
        self.album["files"][0]["length"] = "0:20"                    # (17 s off: the ID is sent)
        self.album["files"][1]["length"] = "3:40"                    # (217 s off: names instead)
        tr = acs.fingerprint(acs.tracks_of(self.album))
        self.assertEqual([t.length_ms for t in tr[:2]], [20000, 220000])
        self.http.posts = [ok_answer]
        acs.submit(tr)
        fields = dict(self.http.sent[-1][2])
        self.assertEqual(fields["mbid.0"], RIDS[0])
        self.assertNotIn("mbid.1", fields)
        self.assertNotIn(RIDS[1], [v for _k, v in self.http.sent[-1][2]])
        self.assertEqual((fields["track.1"], fields["artist.1"]), ("Two", "Band"))
        text = acs.describe(tr)
        self.assertNotIn("length not checked", text)
        self.assertEqual(acs._length_ms(220000), 220000)              # (milliseconds work too)

    def test_submit_request_and_answer(self):
        self.keys()
        tr = acs.fingerprint(acs.tracks_of(self.album))
        self.http.posts = [ok_answer]
        buf = io.StringIO()
        h = logging.StreamHandler(buf)
        logging.getLogger().addHandler(h)
        logging.getLogger().setLevel(logging.DEBUG)
        try:
            acs.submit(tr)
        finally:
            logging.getLogger().removeHandler(h)
        (method, url, fields), = self.http.sent
        self.assertEqual((method, url), ("POST", "https://api.acoustid.org/v2/submit"))
        f = dict(fields)
        self.assertEqual((f["client"], f["user"], f["format"]), (APP, USER, "json"))
        self.assertTrue(f["clientversion"])
        self.assertEqual((f["mbid.0"], f["mbid.1"]), RIDS)
        self.assertEqual((f["duration.0"], f["fileformat.1"]), ("3", "MP3"))
        self.assertTrue(f["fingerprint.1"].startswith("AQAA"))
        self.assertNotIn("mbid.2", f)
        self.assertFalse(any(str(self.tmp) in v for v in f.values()))
        self.assertEqual([t.submission_id for t in tr], [100, 101, None])
        self.assertEqual(acs.summary(tr), "2 waiting to be added, 1 not sent")
        self.assertNotIn(APP, buf.getvalue())
        self.assertNotIn(USER, buf.getvalue())

    def test_refused_without_keys_in_the_message(self):
        self.keys()
        tr = acs.fingerprint(acs.tracks_of(self.album))
        self.http.posts = [{"status": "error", "error": {"code": 6, "message": f"invalid user API key {USER}"},
                            "http": 400}]
        with self.assertRaises(acs.SubmitError) as cm:
            acs.submit(tr)
        self.assertNotIn(USER, str(cm.exception))
        self.assertIn("user key", str(cm.exception))

    def test_no_keys_or_nothing_sendable(self):
        tr = acs.fingerprint(acs.tracks_of(self.album))
        with self.assertRaises(acs.SubmitError):
            acs.submit(tr)
        self.keys()
        with self.assertRaises(acs.SubmitError):
            acs.submit([tr[2]])
        self.assertEqual(self.http.sent, [])

    def test_too_large_splits_and_busy_retries(self):
        self.keys()
        tr = acs.fingerprint(acs.tracks_of(self.album))
        self.http.posts = [{"status": "error", "error": {"message": "HTTP 413"}, "http": 413},
                           {"status": "error", "error": {"code": 14, "message": "too many requests"}},
                           ok_answer, ok_answer]
        slept = []
        acs.submit(tr, sleep=slept.append)
        self.assertEqual(slept, [acs.MIN_DELAY, 1.0, acs.MIN_DELAY, acs.MIN_DELAY])   # (spaced, then a pause)
        self.assertEqual(len(self.http.sent), 4)
        self.assertEqual([len([k for k, _ in f if k.startswith("mbid.")]) for _m, _u, f in self.http.sent],
                         [2, 1, 1, 1])
        self.assertEqual([t.submission_id for t in tr], [100, 100, None])
        self.assertEqual(len(acs.batches([tr[0]] * 10, limit=acs._size(tr[0]) * 3 + 1)), 4)

    def test_submission_status(self):
        self.keys()
        tr = acs.fingerprint(acs.tracks_of(self.album))
        self.http.posts = [ok_answer]
        acs.submit(tr)
        self.http.gets = [{"status": "ok", "submissions": [
            {"id": 100, "status": "imported", "response": {"id": "acoustid-1"}},
            {"id": 101, "status": "pending"}]}]
        acs.check_status(tr)
        _m, url, fields = self.http.sent[-1]
        self.assertEqual(url, "https://api.acoustid.org/v2/submission_status")
        self.assertEqual(acs.http_post.call_args.args[0], acs.API + "/submission_status")
        self.assertEqual([v for k, v in fields if k == "id"], ["100", "101"])
        self.assertEqual(dict(fields)["client"], APP)
        self.assertNotIn("user", dict(fields))                       # (the user key is not needed to ask)
        self.assertEqual((tr[0].status, tr[0].acoustid, tr[1].status), ("imported", "acoustid-1", "pending"))
        self.assertEqual(acs.summary(tr), "1 added, 1 waiting to be added, 1 not sent")
        self.http.gets = [{"status": "ok", "submissions": [{"id": 101, "status": "imported"}]}]
        acs.check_status(tr)
        self.assertEqual([v for k, v in self.http.sent[-1][2] if k == "id"], ["101"])   # (only those waiting)

    def test_blocked_entry_reads_src_not_the_other_file_at_dst(self):
        # (plan.py: "{dst} already exists (a different file)": blocked, nothing moved yet)
        entry = dict(self.album["files"][1], dst=str(self.files[0]), status="blocked")
        tr, = acs.tracks_of({"files": [entry]})
        self.assertEqual(tr.path, str(self.files[1]))
        self.assertEqual((tr.title, tr.recording_id, tr.id_from, tr.fileformat), ("Two", RIDS[1], "preview", "MP3"))
        entry["op"] = "none"
        self.assertEqual(acs.tracks_of({"files": [entry]})[0].path, str(self.files[1]))

    def test_a_moved_file_is_read_where_the_plan_put_it(self):
        dst = self.tmp / "moved" / self.files[1].name
        dst.parent.mkdir()
        shutil.copy2(self.files[1], dst)
        entry = dict(self.album["files"][1], src=str(self.tmp / "gone.mp3"), dst=str(dst))
        tr, = acs.tracks_of({"files": [entry]})
        self.assertEqual((tr.path, tr.fileformat, tr.problem), (str(dst), "MP3", ""))
        entry["op"] = "none"                                          # (not a move: dst is not this file)
        tr, = acs.tracks_of({"files": [entry]})
        self.assertEqual(tr.path, str(self.tmp / "gone.mp3"))
        self.assertIn("not there", tr.problem)

    def test_cancelled_fingerprinting_reads_nothing(self):
        tr = acs.fingerprint(acs.tracks_of(self.album), cancelled=lambda: True)
        self.assertEqual([t.fingerprint for t in tr], ["", "", ""])
        self.assertEqual(acs._fpcalc.call_count, 0)

    def test_a_later_batch_refused_keeps_what_was_sent(self):
        self.keys()
        tr = acs.fingerprint(acs.tracks_of(self.album))
        self.http.posts = [ok_answer, {"status": "error", "error": {"code": 6, "message": f"invalid user key {USER}"}}]
        with mock.patch.object(acs, "MAX_PAYLOAD", acs._size(tr[0]) + 1):
            acs.submit(tr, sleep=lambda s: None)
        self.assertEqual(len(self.http.sent), 2)
        self.assertEqual((tr[0].submission_id, tr[1].submission_id), (100, None))
        self.assertTrue(tr[1].status.startswith("Not sent"), tr[1].status)
        self.assertNotIn(USER, tr[1].status)
        self.assertEqual(acs.summary(tr), "1 waiting to be added, 2 not sent")

    def test_busy_gives_up_after_max_attempts(self):
        self.keys()
        tr = acs.fingerprint(acs.tracks_of(self.album))
        self.http.posts = [{"status": "error", "error": {"code": 14, "message": "too many requests"}}] * 3
        slept = []
        with self.assertRaises(acs.SubmitError):
            acs.submit(tr, sleep=slept.append)
        self.assertEqual(len(self.http.sent), acs.MAX_ATTEMPTS)
        self.assertEqual(slept, [1.0, acs.MIN_DELAY, 2.0, acs.MIN_DELAY])

    def test_status_error_without_the_key(self):
        self.keys()
        tr = acs.fingerprint(acs.tracks_of(self.album))
        tr[0].submission_id = 100
        self.http.gets = [{"status": "error", "error": {"code": 4, "message": f"invalid API key {APP}"}}]
        with self.assertRaises(acs.SubmitError) as cm:
            acs.check_status(tr)
        self.assertNotIn(APP, str(cm.exception))
        self.assertIn("application key", str(cm.exception))
        # (through http_post, a form POST with the key in the body, never in a URL: see RealPost)
        self.assertEqual(acs.http_post.call_args.args[0], acs.API + "/submission_status")

    def test_answers_that_are_not_an_object(self):
        self.keys()
        tr = acs.fingerprint(acs.tracks_of(self.album))
        for answer in (["ok"], "ok", None):
            self.http.posts = [answer]
            with self.assertRaises(acs.SubmitError) as cm:
                acs.submit(tr)
            self.assertIn("could not be read", str(cm.exception))


class RealPost(unittest.TestCase):
    """http_post itself, against a stubbed urlopen (and a socket that refuses to connect)."""
    def setUp(self):
        self.reqs = []
        self.p_sock = mock.patch.object(socket, "create_connection", side_effect=AssertionError("network"))
        self.p_sock.start()

    def tearDown(self):
        self.p_sock.stop()

    def post(self, answer):
        def urlopen(req, timeout=None):
            self.reqs.append(req)
            if isinstance(answer, BaseException):
                raise answer
            return io.BytesIO(answer)
        with mock.patch.object(acs.urllib.request, "urlopen", side_effect=urlopen):
            return REAL_POST("https://api.acoustid.org/v2/submit", [("client", APP), ("id", "1"), ("id", "2")])

    def test_request_is_a_form_post(self):
        self.assertEqual(self.post(b'{"status": "ok"}'), {"status": "ok"})
        req, = self.reqs
        self.assertEqual(req.get_method(), "POST")
        self.assertEqual(req.data, f"client={APP}&id=1&id=2".encode())
        self.assertEqual(req.get_header("Content-type"), "application/x-www-form-urlencoded")
        self.assertTrue(req.get_header("User-agent").startswith("TagMender/"))
        self.assertNotIn(APP, req.full_url)

    def test_http_errors(self):
        url = "https://api.acoustid.org/v2/submit"
        doc = self.post(urllib.error.HTTPError(url, 413, "Too Large", {}, io.BytesIO(b"")))
        self.assertEqual(doc["http"], 413)
        body = json.dumps({"status": "error", "error": {"code": 6, "message": "bad"}}).encode()
        doc = self.post(urllib.error.HTTPError(url, 400, "Bad", {}, io.BytesIO(body)))
        self.assertEqual((doc["error"]["code"], doc["http"]), (6, 400))
        doc = self.post(urllib.error.HTTPError(url, 502, "Bad", {}, io.BytesIO(b'["portal"]')))
        self.assertEqual((doc["status"], doc["http"]), ("error", 502))

    def test_unreachable_or_unreadable(self):
        with self.assertRaises(acs.SubmitError) as cm:
            self.post(urllib.error.URLError("down"))
        self.assertIn("down", str(cm.exception))
        for raw in (b"<html>", b'["a list"]', b'"text"'):
            with self.assertRaises(acs.SubmitError) as cm:
                self.post(raw)
            self.assertIn("could not be read", str(cm.exception))


@unittest.skipIf(QApplication is None, "PySide6 is not installed")
class Gui(Base):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        import gui  # noqa: F401
        import gui.acoustid_submit as gacs
        from gui import sheets
        cls.gacs, cls.sheets = gacs, sheets

    def setUp(self):
        super().setUp()
        self.win = QMainWindow()
        self.win.preferences = mock.Mock()
        self.sheets.shown.clear()

    def tearDown(self):
        self.win.close()
        self.win.deleteLater()
        self.app.processEvents()
        super().tearDown()

    def wait(self, cond, secs=20):
        end = time.time() + secs
        while not cond() and time.time() < end:
            self.app.processEvents()
            time.sleep(0.01)
        self.app.processEvents()
        self.assertTrue(cond(), "the worker did not finish")

    def settle(self, secs=0.3):
        """Let any worker that should not have started have its chance to send."""
        end = time.time() + secs
        while time.time() < end:
            self.app.processEvents()
            time.sleep(0.01)

    def sent(self, url_end):
        return [x for x in self.http.sent if x[1].endswith(url_end)]

    def test_not_set_up_offers_preferences_and_sends_nothing(self):
        with mock.patch.object(self.sheets, "ask", return_value=self.gacs.SETUP) as ask:
            self.assertIsNone(self.gacs.submit_album(self.win, self.album))
        body = ask.call_args.args[2]
        self.assertIn(acs.APP_KEY_URL, body)
        self.assertIn(acs.USER_KEY_URL, body)
        self.win.preferences.assert_called_once()
        self.assertIsNone(self.gacs.submit_album(self.win, None))
        self.assertEqual(self.sheets.shown[-1][0], "information")
        self.assertEqual(self.http.sent, [])

    def test_not_set_up_cancel_opens_nothing(self):
        with mock.patch.object(self.sheets, "ask", return_value="Cancel"):
            self.assertIsNone(self.gacs.submit_album(self.win, self.album))
        self.win.preferences.assert_not_called()
        self.assertEqual(self.http.sent, [])

    def test_confirm_lists_tracks_and_offscreen_no_sends_nothing(self):
        self.keys()
        s = self.gacs.submit_album(self.win, self.album)
        w = s.worker
        self.wait(lambda: any(k == "confirm" for k, _h, _b in self.sheets.shown))
        self.settle()
        self.assertEqual(self.http.sent, [])                          # (the sheet's "no": nothing sent)
        self.assertIs(s.worker, w)                                    # (no send worker started)
        kind, head, body = [x for x in self.sheets.shown if x[0] == "confirm"][-1]
        for rid in RIDS:
            self.assertIn(rid, body)
        self.assertNotIn(USER, body)
        sh = s.confirm_sheet()
        self.assertEqual(sh.headline_text, "Send this album's fingerprints to AcoustID?")
        self.assertEqual(sh.col.itemAt(0).widget().text(), "Send this album's fingerprints to AcoustID?")
        self.assertEqual(sh.windowModality(), Qt.WindowModal)
        self.assertEqual(sh.default_button().text(), "Submit 2 Fingerprints")
        t = sh.table
        self.assertEqual(t.rowCount(), 3)
        self.assertEqual([t.item(i, 3).text() for i in range(3)], [RIDS[0], RIDS[1], ""])
        self.assertEqual([t.item(i, 4).text() for i in range(3)], ["tags", "preview", ""])
        self.assertIn("not yet written", t.item(1, 4).toolTip())
        self.assertIn("preview", t.item(1, 3).toolTip())
        self.assertIn("file's own tags", t.item(0, 3).toolTip())
        self.assertIn("not yet written", body)                        # (and in what the sheet says is sent)
        self.assertIn("MP3", sh.body_text.split("\n\n")[0])           # (the format, said once)
        self.assertEqual(t.item(0, 2).text(), "recording ID")
        self.assertEqual(t.item(2, 2).text(), "Not sent: no recording ID")   # (why, in its row)
        self.assertIn(f"POST {acs.API}/submit", sh.sent_text)        # (the address, word for word)
        self.assertIn("clientversion=", sh.sent_text)
        self.assertNotIn("TagMender's application key", sh.body_text.split("\n\n")[0])
        sh.deleteLater()

    def test_only_the_action_sends(self):
        self.keys()
        real_run = self.sheets.run
        choice = {}

        def run(dlg, kind="sheet"):
            if kind != "confirm":
                return real_run(dlg, kind)
            self.sheets.shown.append((kind, dlg.windowTitle(), getattr(dlg, "body_text", "")))
            dlg.clicked_text = choice.get("text") or dlg.default_button().text()
            return QDialog.Accepted
        self.http.posts = [ok_answer]
        with mock.patch.object(self.sheets, "run", side_effect=run):
            s = self.gacs.submit_album(self.win, self.album)
            self.wait(lambda: self.sent("/submit"))
            self.wait(lambda: any(k == "status" for k, _h, _b in self.sheets.shown))
            self.assertEqual(len(self.sent("/submit")), 1)
            self.assertEqual([t.submission_id for t in s.tracks], [100, 101, None])
            choice["text"] = "Cancel"                                 # (accepted, but Cancel was clicked)
            s2 = self.gacs.submit_album(self.win, self.album)
            self.wait(lambda: s2.tracks)
            self.settle()
        self.assertEqual(len(self.sent("/submit")), 1)
        self.assertEqual([t.submission_id for t in s2.tracks], [None, None, None])

    def test_nothing_to_send_says_so(self):
        self.keys()
        with mock.patch.object(acs, "_fpcalc", return_value=None):
            s = self.gacs.submit_album(self.win, self.album)
            self.wait(lambda: s.tracks)
        self.settle()
        kinds = [k for k, _h, _b in self.sheets.shown]
        self.assertIn("information", kinds)
        self.assertNotIn("confirm", kinds)
        self.assertEqual(self.sheets.shown[-1][1], "There is nothing to send to AcoustID.")
        self.assertEqual(self.http.sent, [])

    def test_fingerprinting_failure_shows_a_warning(self):
        self.keys()
        with mock.patch.object(acs, "_fpcalc", side_effect=RuntimeError("fpcalc crashed")):
            s = self.gacs.submit_album(self.win, self.album)
            self.wait(lambda: s.error)
        kind, head, body = self.sheets.shown[-1]
        self.assertEqual((kind, head), ("warning", "Submit AcoustIDs did not work."))
        self.assertIn("fpcalc crashed", body)
        self.assertNotIn("Traceback", body)
        self.assertEqual(self.http.sent, [])

    def test_send_then_status_and_check_again(self):
        self.keys()
        s = self.gacs.submit_album(self.win, self.album)
        self.wait(lambda: s.tracks)
        self.http.posts = [ok_answer]
        s.send()
        self.wait(lambda: any(k == "status" for k, _h, _b in self.sheets.shown))
        sh = s.sheet
        self.assertIn("2 waiting", sh.headline_text)
        self.assertEqual(sh.table.item(0, 3).text(), "Waiting to be added")
        self.assertEqual(sh.table.item(2, 3).text(), "No recording ID: not sent.")
        self.assertTrue(sh.check.isEnabled())
        self.assertEqual(s.status_icon(), ("circle-alert", "attention"))     # (waiting: not the green check)
        self.http.gets = [{"status": "ok", "submissions": [
            {"id": 100, "status": "imported", "response": {"id": "acoustid-1"}},
            {"id": 101, "status": "imported", "response": {"id": "acoustid-2"}}]}]
        s.check()
        self.wait(lambda: s.tracks[1].status == "imported" and sh.check.text() == "Check Again")
        self.assertEqual((sh.table.item(0, 3).text(), sh.table.item(1, 4).text()), ("Added", "acoustid-2"))
        self.assertIn("2 added", sh.headline_text)
        self.assertFalse(sh.check.isEnabled())
        self.assertEqual(s.status_icon(), ("circle-check", "identified"))    # (every track sent was added)

    def test_the_sheet_says_when_names_go_in_place_of_the_id(self):
        self.keys()
        s = self.gacs.submit_album(self.win, self.album)
        self.wait(lambda: s.tracks)
        self.settle()
        s.tracks[1].length_ms = s.tracks[1].duration * 1000 + 31000      # (lengths too far apart: names)
        sh = s.confirm_sheet()
        body = sh.body_text.split("\n\n")[0]
        self.assertIn("except for 1 track, whose title, artists, album, track and disc numbers and year are sent "
                      "in place of the recording ID", body)
        self.assertNotIn("other tags", body)
        self.assertIn("no other tags than those listed", sh.sent_text)
        self.assertEqual(sh.sent_text.count(f"{acs.API}/submit"), 1)      # (the address once)
        for words in ("User-Agent: TagMender/", "Content-Type: application/x-www-form-urlencoded", "format=json",
                      "your AcoustID application key (client=)", "your AcoustID user key (user=)"):
            self.assertIn(words, sh.sent_text)
        self.assertNotIn("TagMender's application key", sh.sent_text)    # (nothing bundled: the key is yours)
        self.assertIn("in place of the recording ID", sh.table.item(1, 2).toolTip())
        self.assertTrue(hasattr(sh, "show_sent") and sh.show_sent.text() == "Show exactly what is sent")
        sh.show_sent.setChecked(False)
        self.assertTrue(sh.sent_box.isHidden())
        sh.show_sent.setChecked(True)
        self.assertFalse(sh.sent_box.isHidden())
        sh.deleteLater()

    def test_check_again_error_shows_a_warning_without_the_key(self):
        self.keys()
        s = self.gacs.submit_album(self.win, self.album)
        self.wait(lambda: s.tracks)
        self.http.posts = [ok_answer]
        s.send()
        self.wait(lambda: s.sheet is not None)
        self.http.gets = [{"status": "error", "error": {"code": 4, "message": f"invalid API key {APP}"}}]
        s.check()
        self.wait(lambda: any(k == "warning" for k, _h, _b in self.sheets.shown))
        kind, head, body = self.sheets.shown[-1]
        self.assertEqual(head, "AcoustID did not say how the submission went.")
        self.assertIn("application key", body)
        self.assertNotIn(APP, body)
        self.assertEqual(s.sheet.check.text(), "Check Again")

    def test_refusal_shows_a_warning_without_keys(self):
        self.keys()
        s = self.gacs.submit_album(self.win, self.album)
        self.wait(lambda: s.tracks)
        self.http.posts = [{"status": "error", "error": {"code": 4, "message": f"invalid API key {APP}"}}]
        s.send()
        self.wait(lambda: any(k == "warning" for k, _h, _b in self.sheets.shown))
        _k, head, body = self.sheets.shown[-1]
        self.assertIn("not sent", head)
        self.assertIn("application key", body)
        self.assertNotIn(APP, body)
        self.assertNotIn("Traceback", body)
        self.assertIsNone(s.sheet)


if __name__ == "__main__":
    unittest.main(argv=[sys.argv[0]])
