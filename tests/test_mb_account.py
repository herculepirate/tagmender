# The MusicBrainz account (mb_account.py): OAuth2 with your own application (Picard's
# out-of-band flow, both sign-in servers), the 0600 login file, refresh and logout, collections,
# rating submission (Picard's XML), and ratings in files as Picard keeps them (POPM, RATING:<e-mail>,
# WM/SharedUserRating). Stubbed HTTP only (FakeHttp: an unknown URL fails the test), temporary
# config folders, generated audio; no network, no real keys.
import os as _os, tempfile as _tempfile  # noqa: E402
_os.environ["XDG_CONFIG_HOME"] = _tempfile.mkdtemp(prefix="tm-config-")
_os.environ["XDG_STATE_HOME"] = _tempfile.mkdtemp(prefix="tm-state-")
_os.environ.setdefault("TAGMENDER_TAGGER_INI", "/nonexistent")

import io
import json
import logging
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import time
import types
import unittest
import urllib.parse as up
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

import credentials as cr  # noqa: E402
import mb_account as mba  # noqa: E402

FFMPEG = shutil.which("ffmpeg")
REL = "0a8e97fd-4f6a-4bd7-8e4b-6d3a0b2ad2f1"
COL = "1b9f08ae-5a7b-4ce8-9f5c-7e4b1c3be3a2"
COL2 = "2c0f19bf-6b7c-4df9-a05d-8f5c2d4cf4b3"
REC = "3d1f2ac0-7c8b-4e0a-b16e-9a6d3e5df5c4"
SECRET_BITS = ("CLIENT-SECRET-xyz", "ACCESS-tok-1", "REFRESH-tok-1", "ACCESS-tok-2", "REFRESH-tok-2", "the-code")


class FakeHttp:
    """Answers by (method, url without query); records every call."""

    def __init__(self, routes):
        self.routes, self.calls = routes, []

    def __call__(self, method, url, headers, body=None):
        self.calls.append((method, url, dict(headers), body))
        base = url.split("?")[0]
        got = self.routes.get((method, base))
        if got is None:
            raise AssertionError(f"unexpected request {method} {base}")
        if callable(got):
            got = got(method, url, headers, body)
        status, data = got
        return status, (json.dumps(data).encode() if isinstance(data, (dict, list)) else data)


def form(body):
    return dict(up.parse_qsl(body.decode()))


def token_answer(n=1, expires=3600):
    return 200, {"access_token": f"ACCESS-tok-{n}", "refresh_token": f"REFRESH-tok-{n}",
                 "expires_in": expires, "token_type": "Bearer"}


class Base(unittest.TestCase):
    def setUp(self):
        self.env = mock.patch.dict(_os.environ, {"TAGMENDER_MB_CLIENT_ID": "my-client-id",
                                                 "TAGMENDER_MB_CLIENT_SECRET": "CLIENT-SECRET-xyz"})
        self.env.start()
        self.addCleanup(self.env.stop)
        mock.patch.object(mba, "MIN_INTERVAL", 0).start()
        self.addCleanup(mock.patch.stopall)
        mba._forget()
        self.addCleanup(mba._forget)
        self.now = [1000.0]
        self.log = io.StringIO()
        h = logging.StreamHandler(self.log)
        logging.getLogger().addHandler(h)
        logging.getLogger().setLevel(logging.DEBUG)
        self.addCleanup(logging.getLogger().removeHandler, h)

    def account(self, routes, provider=None):
        self.http = FakeHttp(routes)
        return mba.Account(http=self.http, provider=provider, clock=lambda: self.now[0])

    def logged_in(self, extra=None, provider="musicbrainz"):
        host = mba.PROVIDERS[provider]["host"]
        routes = {("POST", f"https://{host}/oauth2/token"): token_answer(1),
                  ("GET", f"https://{host}/oauth2/userinfo"): (200, {"sub": "alice"})}
        routes.update(extra or {})
        acct = self.account(routes, provider)
        acct.authorize_url()
        acct.exchange("the-code")
        return acct

    def tearDown(self):
        text = self.log.getvalue()
        for bit in SECRET_BITS:
            self.assertNotIn(bit, text, "a secret reached the log")


class TestLogin(Base):
    def test_authorize_url_is_picards_oob_request_with_your_client(self):
        acct = self.account({})
        url = acct.authorize_url()
        parts = up.urlsplit(url)
        q = dict(up.parse_qsl(parts.query))
        self.assertEqual((parts.scheme, parts.netloc, parts.path), ("https", "musicbrainz.org", "/oauth2/authorize"))
        self.assertEqual(q, {"response_type": "code", "client_id": "my-client-id",
                             "redirect_uri": "urn:ietf:wg:oauth:2.0:oob", "access_type": "offline",
                             "scope": "profile tag rating collection"})
        self.assertNotIn("CLIENT-SECRET", url)
        self.assertEqual(self.http.calls, [])            # (opening the page sends nothing itself)

    def test_metabrainz_sign_in_uses_pkce_as_picard_3(self):
        import base64
        import hashlib
        acct = self.account({}, "metabrainz")
        q = dict(up.parse_qsl(up.urlsplit(acct.authorize_url()).query))
        self.assertEqual(q["code_challenge_method"], "S256")
        self.assertIn("musicbrainz:collection", q["scope"])
        want = base64.urlsafe_b64encode(hashlib.sha256(acct._verifier.encode()).digest()).rstrip(b"=").decode()
        self.assertEqual(q["code_challenge"], want)

    def test_no_application_keys_says_where_to_register(self):
        with mock.patch.dict(_os.environ, {"TAGMENDER_MB_CLIENT_ID": "", "TAGMENDER_MB_CLIENT_SECRET": ""}):
            acct = self.account({})
            with self.assertRaises(mba.AccountError) as e:
                acct.authorize_url()
        self.assertIn("musicbrainz.org/account/applications", str(e.exception))
        self.assertIn("Installed application", str(e.exception))

    def test_exchange_saves_tokens_0600_and_the_user_name(self):
        acct = self.logged_in()
        method, url, headers, body = self.http.calls[0]
        self.assertEqual(form(body), {"grant_type": "authorization_code", "code": "the-code",
                                      "client_id": "my-client-id", "client_secret": "CLIENT-SECRET-xyz",
                                      "redirect_uri": "urn:ietf:wg:oauth:2.0:oob"})
        self.assertEqual(self.http.calls[1][2]["Authorization"], "Bearer ACCESS-tok-1")
        p = mba.token_path()
        self.assertEqual(stat.S_IMODE(p.stat().st_mode), 0o600)
        saved = json.loads(p.read_text())
        self.assertEqual((saved["refresh_token"], saved["username"]), ("REFRESH-tok-1", "alice"))
        self.assertEqual(saved["expires"], 1000 + 3600 - 60)
        self.assertTrue(acct.logged_in())
        self.assertEqual(acct.username, "alice")
        self.assertEqual(p.parent, cr.path_of("mb_client_id").parent)

    def test_metabrainz_exchange_sends_the_verifier(self):
        acct = self.logged_in(provider="metabrainz")
        self.assertIn("code_verifier", form(self.http.calls[0][3]))
        self.assertEqual(mba.Account(http=self.http).provider, "metabrainz")    # (remembered)

    def test_bad_code_message_holds_no_secret(self):
        acct = self.account({("POST", "https://musicbrainz.org/oauth2/token"):
                             (400, {"error": "invalid_grant", "error_description": "Code expired"})})
        acct.authorize_url()
        with self.assertRaises(mba.AccountError) as e:
            acct.exchange("the-code")
        self.assertIn("Code expired", str(e.exception))
        for bit in SECRET_BITS:
            self.assertNotIn(bit, str(e.exception))
        self.assertFalse(mba.token_path().exists())

    def test_an_echoed_code_or_secret_never_reaches_the_message(self):
        why = "Code the-code is not valid for my-client-id with secret CLIENT-SECRET-xyz"
        acct = self.account({("POST", "https://musicbrainz.org/oauth2/token"):
                             (400, {"error": "invalid_grant", "error_description": why})})
        acct.authorize_url()
        with self.assertRaises(mba.AccountError) as e:
            acct.exchange("the-code")
        self.assertIn("is not valid", str(e.exception))
        for bit in SECRET_BITS:
            self.assertNotIn(bit, str(e.exception))
        acct = self.logged_in()
        self.now[0] += 4000
        self.http.routes[("POST", "https://musicbrainz.org/oauth2/token")] = \
            (500, {"error_description": "refresh REFRESH-tok-1 failed"})
        with self.assertRaises(mba.AccountError) as e:
            acct.access_token()
        self.assertIn("failed", str(e.exception))
        self.assertNotIn("REFRESH-tok-1", str(e.exception))

    def test_key_file_others_can_read_is_a_plain_account_error(self):
        with mock.patch.dict(_os.environ, {"TAGMENDER_MB_CLIENT_ID": "", "TAGMENDER_MB_CLIENT_SECRET": ""}):
            p = cr.path_of("mb_client_id")
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text("someone-elses-view")
            _os.chmod(p, 0o644)
            self.addCleanup(p.unlink, True)
            acct = self.account({})
            with self.assertRaises(mba.AccountError) as e:
                acct.authorize_url()
        self.assertIn("chmod 600", str(e.exception))
        self.assertEqual(self.http.calls, [])

    def test_login_file_others_can_read_is_refused(self):
        self.logged_in()
        _os.chmod(mba.token_path(), 0o644)
        with self.assertRaises(cr.CredentialError):
            mba._load()
        self.assertFalse(mba.Account(http=self.http).logged_in())

    def test_expired_access_token_is_refreshed_and_a_new_refresh_token_kept(self):
        acct = self.logged_in()
        self.now[0] += 4000
        self.http.routes[("POST", "https://musicbrainz.org/oauth2/token")] = token_answer(2)
        self.assertEqual(acct.access_token(), "ACCESS-tok-2")
        self.assertEqual(form(self.http.calls[-1][3]), {"grant_type": "refresh_token",
                                                        "refresh_token": "REFRESH-tok-1",
                                                        "client_id": "my-client-id",
                                                        "client_secret": "CLIENT-SECRET-xyz"})
        self.assertEqual(json.loads(mba.token_path().read_text())["refresh_token"], "REFRESH-tok-2")
        n = len(self.http.calls)
        self.assertEqual(acct.access_token(), "ACCESS-tok-2")
        self.assertEqual(len(self.http.calls), n)       # (still valid: not refreshed again)

    def test_refused_refresh_logs_out(self):
        acct = self.logged_in()
        self.now[0] += 4000
        self.http.routes[("POST", "https://musicbrainz.org/oauth2/token")] = (400, {"error": "invalid_grant"})
        with self.assertRaises(mba.NotLoggedIn):
            acct.access_token()
        self.assertFalse(acct.logged_in())

    def test_two_workers_refreshing_at_once_stay_logged_in(self):
        """A server that hands out a new refresh token each time refuses the old one: the second
        worker must use the first one's new login, not send the old refresh token again."""
        acct = self.logged_in()
        self.now[0] += 4000
        state = {"good": "REFRESH-tok-1", "n": 1}
        lock = threading.Lock()

        def token(_m, _u, _h, body):
            time.sleep(0.05)                           # (long enough for the other worker to get here)
            with lock:
                if form(body).get("refresh_token") != state["good"]:
                    return 400, {"error": "invalid_grant"}
                state["n"] += 1
                state["good"] = f"REFRESH-tok-{state['n']}"
                return token_answer(state["n"])
        self.http.routes[("POST", "https://musicbrainz.org/oauth2/token")] = token
        got, errors = [], []

        def worker():
            try:
                got.append(mba.Account(http=self.http, clock=lambda: self.now[0]).access_token())
            except Exception as e:                     # (recorded: the test fails below)
                errors.append(e)
        threads = [threading.Thread(target=worker) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(5)
        self.assertEqual(errors, [])
        self.assertEqual(got, ["ACCESS-tok-2", "ACCESS-tok-2"])
        self.assertTrue(acct.logged_in())
        self.assertEqual(json.loads(mba.token_path().read_text())["refresh_token"], "REFRESH-tok-2")

    def test_logout_revokes_the_grant_and_deletes_the_login(self):
        acct = self.logged_in({("POST", "https://musicbrainz.org/oauth2/revoke"): (200, b"")})
        self.assertTrue(acct.logout())
        self.assertEqual(form(self.http.calls[-1][3]), {"token": "REFRESH-tok-1", "client_id": "my-client-id",
                                                        "client_secret": "CLIENT-SECRET-xyz"})
        self.assertFalse(mba.token_path().exists())

    def test_logout_deletes_the_login_even_when_revoking_fails(self):
        acct = self.logged_in({("POST", "https://musicbrainz.org/oauth2/revoke"): (503, b"")})
        self.assertFalse(acct.logout())
        self.assertFalse(mba.token_path().exists())


class TestWebService(Base):
    def test_collections_are_your_release_collections_by_name(self):
        acct = self.logged_in({("GET", f"{mba.WS}/collection"): (200, {"collections": [
            {"id": COL2, "name": "wish list", "entity-type": "release", "release-count": 3, "editor": "alice"},
            {"id": "x", "name": "Artists", "entity-type": "artist", "artist-count": 2},
            {"id": COL, "name": "Owned", "entity-type": "release", "release-count": 10, "editor": "alice"}]})})
        got = acct.collections()
        self.assertEqual([(c["id"], c["count"]) for c in got], [(COL, 10), (COL2, 3)])
        _m, url, headers, _b = self.http.calls[-1]
        self.assertEqual(headers["Authorization"], "Bearer ACCESS-tok-1")
        self.assertIn("fmt=json", url)
        self.assertTrue(headers["User-Agent"].startswith("TagMender/"))

    def test_collections_follow_every_page(self):
        def page(_m, url, _h, _b):
            q = dict(up.parse_qsl(up.urlsplit(url).query))
            start = int(q.get("offset", 0))
            n = min(int(q["limit"]), 230 - start)
            return 200, {"collection-count": 230, "collection-offset": start, "collections": [
                {"id": f"{i:08x}-0000-0000-0000-000000000000", "name": f"c{i:03d}",
                 "entity-type": "release"} for i in range(start, start + n)]}
        acct = self.logged_in({("GET", f"{mba.WS}/collection"): page})
        got = acct.collections()
        self.assertEqual(len(got), 230)
        urls = [c[1] for c in self.http.calls if c[1].startswith(f"{mba.WS}/collection")]
        self.assertEqual(urls, [f"{mba.WS}/collection?limit=100&fmt=json",
                                f"{mba.WS}/collection?limit=100&offset=100&fmt=json",
                                f"{mba.WS}/collection?limit=100&offset=200&fmt=json"])
        self.assertEqual(urls[0], mba.collections_requests(REL)[0].split(" ", 1)[1])

    def test_a_refresh_goes_to_the_server_the_login_came_from(self):
        first = self.account({})                                    # (made before the login: musicbrainz)
        self.assertEqual(first.provider, "musicbrainz")
        self.logged_in({("GET", f"{mba.WS}/collection"): (200, {"collections": []}),
                        ("POST", "https://metabrainz.org/oauth2/token"): token_answer(2)}, "metabrainz")
        first.http = self.http                                      # (musicbrainz.org/oauth2/token unrouted)
        self.now[0] += 7200
        self.assertEqual(first.collections(), [])
        posts = [c[1] for c in self.http.calls if c[0] == "POST"]
        self.assertEqual(posts[-1], "https://metabrainz.org/oauth2/token")
        self.assertEqual(form(self.http.calls[-2][3])["grant_type"], "refresh_token")
        self.assertTrue(first.logged_in())
        self.assertEqual(first.provider, "metabrainz")
        self.assertEqual(first.login_host(), "metabrainz.org")

    def test_release_collections(self):
        acct = self.logged_in({("GET", f"{mba.WS}/release/{REL}"): (200, {"id": REL, "collections": [{"id": COL}]})})
        self.assertEqual(acct.release_collections(REL), {COL})
        self.assertIn("inc=user-collections", self.http.calls[-1][1])

    def test_add_and_remove_send_picards_requests_with_the_client(self):
        path = f"{mba.WS}/collection/{COL}/releases/{REL}"
        acct = self.logged_in({("PUT", path): (200, b"<ok/>"), ("DELETE", path): (200, b"<ok/>")})
        self.assertEqual(acct.add_to_collection(COL, [REL]), 1)
        m, url, _h, body = self.http.calls[-1]
        self.assertEqual((m, url, body), ("PUT", f"{path}?client=TagMender-{mba.VERSION}", b""))
        acct.remove_from_collection(COL, [REL])
        self.assertEqual(self.http.calls[-1][:2], ("DELETE", f"{path}?client=TagMender-{mba.VERSION}"))
        self.assertEqual(mba.collection_requests(COL, [REL], add=False),
                         [f"DELETE {path}?client=TagMender-{mba.VERSION}"])

    def test_many_releases_go_in_batches_of_400(self):
        ids = [f"{i:08x}-0000-4000-8000-000000000000" for i in range(401)]
        self.assertEqual(len(mba.collection_paths(COL, ids)), 2)
        self.assertEqual(mba.collection_paths(COL, ids)[1].rsplit("/", 1)[1], ids[400])

    def test_not_an_mbid_is_refused_before_anything_is_sent(self):
        acct = self.logged_in()
        n = len(self.http.calls)
        for bad in ("../account", REL + "?x=1", ""):
            with self.assertRaises(mba.AccountError):
                acct.add_to_collection(COL, [bad])
        self.assertEqual(len(self.http.calls), n)

    def test_ratings_xml_is_picards(self):
        self.assertEqual(mba.ratings_xml({REC: 4}),
                         '<?xml version="1.0" encoding="UTF-8"?><metadata xmlns="http://musicbrainz.org/ns/mmd-2.0#">'
                         f'<recording-list><recording id="{REC}"><user-rating>80</user-rating></recording>'
                         '</recording-list></metadata>')
        with self.assertRaises(mba.AccountError):
            mba.ratings_xml({REC: 6})

    def test_submit_ratings_posts_the_xml(self):
        acct = self.logged_in({("POST", f"{mba.WS}/rating"): (200, b"<message>OK</message>")})
        self.assertEqual(acct.submit_ratings({REC: 3}), 1)
        m, url, headers, body = self.http.calls[-1]
        self.assertEqual(url, f"{mba.WS}/rating?client=TagMender-{mba.VERSION}")
        self.assertEqual(headers["Content-Type"], "application/xml; charset=utf-8")
        self.assertIn(b"<user-rating>60</user-rating>", body)

    def test_an_access_token_refused_early_is_refreshed_once(self):
        answers = iter([(401, b""), (200, {"collections": []})])
        acct = self.logged_in({("GET", f"{mba.WS}/collection"): lambda *a: next(answers)})
        self.http.routes[("POST", "https://musicbrainz.org/oauth2/token")] = token_answer(2)
        self.assertEqual(acct.collections(), [])
        self.assertEqual(self.http.calls[-1][2]["Authorization"], "Bearer ACCESS-tok-2")

    def test_a_token_another_worker_refreshed_is_not_refreshed_again(self):
        acct = self.logged_in()
        n = len(self.http.calls)
        self.assertEqual(acct.access_token(force=True, refused="ACCESS-tok-0"), "ACCESS-tok-1")
        self.assertEqual(len(self.http.calls), n)      # (the refused one was replaced already)

    def test_a_busy_server_is_asked_once_more_with_the_same_login(self):
        answers = iter([(503, b""), (200, {"collections": []})])
        acct = self.logged_in({("GET", f"{mba.WS}/collection"): lambda *a: next(answers)})
        self.http.routes[("POST", "https://musicbrainz.org/oauth2/token")] = \
            lambda *a: self.fail("a busy server is no reason to refresh the login")
        self.assertEqual(acct.collections(), [])
        gets = [c for c in self.http.calls if c[0] == "GET" and "/ws/2/" in c[1]]
        self.assertEqual([c[2]["Authorization"] for c in gets], ["Bearer ACCESS-tok-1"] * 2)

    def test_every_request_keeps_to_one_a_second(self):
        clock, slept = [500.0], []

        def sleep(s):
            slept.append(s)
            clock[0] += s
        fake = types.SimpleNamespace(monotonic=lambda: clock[0], sleep=sleep, time=time.time)
        with mock.patch.object(mba, "MIN_INTERVAL", 1.0), mock.patch.object(mba, "time", fake), \
                mock.patch.object(mba, "_last", [0.0]):
            acct = self.logged_in({("GET", f"{mba.WS}/collection"): (200, {"collections": []}),
                                   ("POST", "https://musicbrainz.org/oauth2/revoke"): (200, b"")})
            self.assertEqual(slept, [1.0])             # (the code exchange, then who you are)
            acct.collections()
            acct.collections()
            acct.logout()
        self.assertEqual(slept, [1.0] * 4)
        self.assertEqual(len(self.http.calls), 5)

    def test_not_logged_in_sends_nothing(self):
        acct = self.account({})
        with self.assertRaises(mba.NotLoggedIn):
            acct.collections()
        self.assertEqual(self.http.calls, [])

    def test_user_genres(self):
        acct = self.logged_in({("GET", f"{mba.WS}/release/{REL}"): (200, {
            "user-genres": [{"name": "rock", "count": 1}], "user-tags": [{"name": "loud"}, {"name": "90s"}]})})
        self.assertEqual(acct.user_genres(REL), {"genres": ["rock"], "tags": ["loud", "90s"]})
        self.assertIn("inc=user-genres+user-tags", self.http.calls[-1][1])


def audio(p, codec, **tags):
    meta = sum((["-metadata", f"{k}={v}"] for k, v in tags.items()), [])
    subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
                    "-c:a", codec, *meta, "-y", str(p)], check=True)
    return p


@unittest.skipIf(FFMPEG is None, "ffmpeg is needed for the test audio")
class TestRatingsInFiles(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="tm-rating-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def rate(self, path, n, email=mba.DEFAULT_EMAIL):
        import tagio
        f = tagio.open_file(path)
        mba.write_rating(f, n, email)
        f.save()

    def test_mp3_popm_with_picards_email_and_play_count_kept(self):
        from mutagen.id3 import ID3, POPM
        p = audio(self.tmp / "a.mp3", "libmp3lame")
        t = ID3(p)
        t.add(POPM(email=mba.DEFAULT_EMAIL, rating=51, count=7))
        t.add(POPM(email="someone@else", rating=255, count=1))
        t.save()
        self.assertEqual(mba.read_rating(p), 1)
        self.rate(p, 4)
        t = ID3(p)
        mine = t[f"POPM:{mba.DEFAULT_EMAIL}"]
        self.assertEqual((mine.rating, mine.count), (204, 7))
        self.assertEqual(t["POPM:someone@else"].rating, 255)  # (another player's rating left alone)
        self.assertEqual(mba.read_rating(p), 4)
        self.rate(p, None)
        self.assertIsNone(mba.read_rating(p))
        self.assertIn("POPM:someone@else", ID3(p))

    def test_flac_rating_email_key_as_picard(self):
        from mutagen.flac import FLAC
        p = audio(self.tmp / "a.flac", "flac")
        self.rate(p, 3)
        self.assertEqual(FLAC(p)["rating:users@musicbrainz.org"], ["0.6"])
        self.assertEqual([k for k, _ in FLAC(p).tags if k.lower().startswith("rating")],
                         ["RATING:USERS@MUSICBRAINZ.ORG"])  # (stored upper-cased, as Picard's vorbis.py)
        self.assertEqual(mba.read_rating(p), 3)
        f = FLAC(p)
        f["RATING"] = ["1.0"]                          # (no e-mail: not Picard's with an e-mail set)
        f.save()
        self.assertEqual(mba.read_rating(p), 3)
        self.assertEqual(mba.read_rating(p, email=""), 5)

    def test_wma_shared_user_rating_as_picard(self):
        from mutagen.asf import ASF
        p = audio(self.tmp / "a.wma", "wmav2")
        for n, raw in ((1, 19), (2, 39), (3, 59), (4, 79), (5, 99)):
            self.rate(p, n)
            self.assertEqual(int(str(ASF(p)["WM/SharedUserRating"][0].value)), raw)
            self.assertEqual(mba.read_rating(p), n)
        self.rate(p, None)
        self.assertNotIn("WM/SharedUserRating", ASF(p).tags)
        self.assertIsNone(mba.read_rating(p))

    def test_vorbis_key_takes_out_what_a_field_name_cannot_hold(self):
        from mutagen.flac import FLAC
        p = audio(self.tmp / "a.flac", "flac")
        self.rate(p, 4, email="me=you@exämple.org")
        self.assertEqual(FLAC(p)["rating:meyou@exmple.org"], ["0.8"])
        self.assertEqual(mba.read_rating(p, email="me=you@exämple.org"), 4)

    def test_m4a_has_no_rating(self):
        import tagio
        p = audio(self.tmp / "a.m4a", "aac")
        self.assertIsNone(mba.read_rating(p))
        self.assertFalse(mba.holds_rating(p))
        with self.assertRaises(tagio.Unsupported):
            mba.write_rating(tagio.open_file(p), 3)

    def test_rating_email_from_picard_ini(self):
        ini = self.tmp / "Picard.ini"
        ini.write_text("[setting]\nrating_user_email=me@example.org\n")
        with mock.patch.dict(_os.environ, {"TAGMENDER_TAGGER_INI": str(ini)}):
            self.assertEqual(mba.rating_email(), "me@example.org")
        self.assertEqual(mba.rating_email(), mba.DEFAULT_EMAIL)

    def test_plan_rating_row_and_change(self):
        import plan as planmod
        p = audio(self.tmp / "a.flac", "flac")
        self.rate(p, 2)
        fe = mba.plan_rating({"src": str(p), "changes": {"title": ["T"]}, "diff": []}, ["5"])
        self.assertEqual(fe["changes"], {"title": ["T"], "~rating": ["5"]})
        self.assertEqual(fe["diff"], [{"field": "rating", "before": ["2"], "after": ["5"], "action": "changed",
                                       "by": planmod.BY_YOU}])
        fe = mba.plan_rating(fe, None)
        self.assertEqual(fe["changes"]["~rating"], None)
        self.assertEqual(fe["diff"][0]["action"], "removed")
        fe = mba.plan_rating(fe, planmod.KEEP)
        self.assertNotIn("~rating", fe["changes"])
        m4a = audio(self.tmp / "b.m4a", "aac")
        self.assertEqual(mba.plan_rating({"src": str(m4a), "changes": {}, "diff": []}, ["3"])["changes"], {})

    def test_album_ratings_your_edit_wins_and_needs_a_recording(self):
        a = audio(self.tmp / "1.flac", "flac", title="One", MUSICBRAINZ_TRACKID=REC)
        b = audio(self.tmp / "2.flac", "flac", title="Two")
        self.rate(a, 1)
        self.rate(b, 5)
        album = {"files": [{"src": str(a), "changes": {}}, {"src": str(b), "changes": {}}]}
        self.assertEqual(mba.album_ratings(album), [{"file": str(a), "title": "One", "recording": REC, "stars": 1}])
        edit = {"tags": {}, "file_tags": {str(a): {"~rating": ["4"]}}}
        self.assertEqual(mba.album_ratings(album, edit)[0]["stars"], 4)

    def test_album_ratings_a_removed_rating_is_not_sent_and_keep_is_the_files(self):
        import plan as planmod
        a = audio(self.tmp / "1.flac", "flac", title="One", MUSICBRAINZ_TRACKID=REC)
        self.rate(a, 1)
        album = {"files": [{"src": str(a), "changes": {}}]}
        for removed in (None, []):
            self.assertEqual(mba.album_ratings(album, {"file_tags": {str(a): {"~rating": removed}}}), [])
            self.assertEqual(mba.album_ratings(album, {"tags": {"~rating": removed}}), [])
        self.assertEqual(mba.album_ratings(album, {"file_tags": {str(a): {"~rating": planmod.KEEP}}})[0]["stars"], 1)
        self.assertEqual(mba.album_ratings(album, {"tags": {"~rating": ["3"]},
                                                   "file_tags": {str(a): {"~rating": planmod.KEEP}}})[0]["stars"], 1)
        self.assertEqual(mba.album_ratings(album, {"tags": {"~rating": ["3"]}})[0]["stars"], 3)

    def test_album_ratings_an_edit_under_another_path_of_the_file(self):
        a = audio(self.tmp / "1.flac", "flac", title="One", MUSICBRAINZ_TRACKID=REC)
        self.rate(a, 1)
        link = self.tmp / "link"
        link.symlink_to(self.tmp)
        album = {"files": [{"src": str(a), "changes": {}}]}
        edit = {"file_tags": {str(link / "1.flac"): {"~rating": ["5"]}}}
        self.assertEqual(mba.album_ratings(album, edit)[0]["stars"], 5)

    def test_hooks_save_and_read_back(self):
        from mutagen.id3 import ID3
        p = audio(self.tmp / "a.mp3", "libmp3lame")
        t = ID3(p)
        t.save(v2_version=3)
        mba.save_rating(p, ["4"])
        self.assertEqual(ID3(p).version[:2], (2, 3))  # (the tag's own version kept)
        self.assertTrue(mba.rating_reads_back(p, ["4"]))
        self.assertFalse(mba.rating_reads_back(p, ["3"]))
        self.assertFalse(mba.rating_reads_back(p, None))
        mba.save_rating(p, None)
        self.assertTrue(mba.rating_reads_back(p, None))


@unittest.skipIf(FFMPEG is None, "ffmpeg is needed for the test audio")
class TestRatingRoundTripThroughTagio(unittest.TestCase):
    """tagio.write's "~rating" change, per format: saved as Picard saves it, read back as Picard
    reads it, removed again; the file's other tags written beside it."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="tm-rating-rt-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def round_trip(self, p):
        import tagio
        for n in range(mba.RATING_STEPS):              # (0..5 stars, Picard's 6 steps)
            tagio.write(p, {"~rating": [str(n)]})
            self.assertEqual(mba.read_rating(p), n, (p.name, n))
        tagio.write(p, {"~rating": ["3"], "title": ["New"]})
        self.assertEqual((mba.read_rating(p), tagio.read(p)["title"]), (3, ["New"]))
        tagio.write(p, {"~rating": None})
        self.assertIsNone(mba.read_rating(p))
        self.assertEqual(tagio.read(p)["title"], ["New"])
        self.assertNotIn("~rating", tagio.read(p))     # (tagio.read keeps no "~rating": fingerprints unchanged)

    def test_flac(self):
        from mutagen.flac import FLAC
        import tagio
        p = audio(self.tmp / "a.flac", "flac", title="a")
        self.round_trip(p)
        tagio.write(p, {"~rating": ["4"]})
        self.assertEqual(FLAC(p)["rating:users@musicbrainz.org"], ["0.8"])

    def test_ogg_vorbis_and_opus(self):
        from mutagen import File
        import tagio
        for name, codec in (("a.ogg", "libvorbis"), ("a.opus", "libopus")):
            p = audio(self.tmp / name, codec, title="a")
            self.round_trip(p)
            tagio.write(p, {"~rating": ["1"]})
            self.assertEqual(File(p)["rating:users@musicbrainz.org"], ["0.2"])

    def test_mp3_popm_both_id3_versions_kept(self):
        from mutagen.id3 import ID3
        import tagio
        for v in (3, 4):
            p = audio(self.tmp / f"a{v}.mp3", "libmp3lame", title="a")
            t = ID3(p)
            t.save(v2_version=v)
            self.round_trip(p)
            tagio.write(p, {"~rating": ["5"]})
            t = ID3(p)
            self.assertEqual(t.version[:2], (2, v))
            self.assertEqual(t[f"POPM:{mba.DEFAULT_EMAIL}"].rating, 255)
            tagio.write(p, {"~rating": ["2"]})
            self.assertEqual(ID3(p)[f"POPM:{mba.DEFAULT_EMAIL}"].rating, 102)

    def test_mp3_with_the_email_from_picard_ini(self):
        from mutagen.id3 import ID3
        import tagio
        ini = self.tmp / "Picard.ini"
        ini.write_text("[setting]\nrating_user_email=me@example.org\n")
        p = audio(self.tmp / "a.mp3", "libmp3lame")
        with mock.patch.dict(_os.environ, {"TAGMENDER_TAGGER_INI": str(ini)}):
            tagio.write(p, {"~rating": ["3"]})
            self.assertEqual(mba.read_rating(p), 3)
        self.assertEqual(ID3(p)["POPM:me@example.org"].rating, 153)

    def test_wma(self):
        from mutagen.asf import ASF
        import tagio
        p = audio(self.tmp / "a.wma", "wmav2", title="a")
        self.round_trip(p)
        tagio.write(p, {"~rating": ["4"]})
        self.assertEqual(int(str(ASF(p)["WM/SharedUserRating"][0].value)), 79)

    def test_m4a_holds_none_and_nothing_is_written(self):
        import tagio
        p = audio(self.tmp / "a.m4a", "aac", title="a")
        before = p.read_bytes()
        with self.assertRaises(tagio.Unsupported):
            tagio.write(p, {"~rating": ["3"], "title": ["New"]})
        self.assertEqual(p.read_bytes(), before)

    def test_a_rating_that_does_not_read_back_raises(self):
        import tagio
        p = audio(self.tmp / "a.flac", "flac")
        with mock.patch.object(mba, "write_rating", lambda *a, **k: None):
            with self.assertRaisesRegex(ValueError, "~rating did not read back"):
                tagio.write(p, {"~rating": ["4"]})


@unittest.skipIf(FFMPEG is None, "ffmpeg is needed for the test audio")
class TestRatingThroughTheExecutor(unittest.TestCase):
    """Your rating, an edit of the plan, written as every edit is: staged, verified, read back,
    journaled and undoable (plan.apply_edits -> tagio.write -> the executor's read-back)."""

    def setUp(self):
        import exclusions as ex
        import logsetup
        self.tmp = Path(tempfile.mkdtemp(prefix="tm-rating-exec-"))
        saved = (ex.CONFIG_DIR, ex.STATE_DIR)
        ex.CONFIG_DIR, ex.STATE_DIR = self.tmp / "config", self.tmp / "state"
        ex._write(ex.rules_file(), {"rules": []})
        logsetup.reset()

        def restore():
            logsetup.reset()
            ex.CONFIG_DIR, ex.STATE_DIR = saved
            shutil.rmtree(self.tmp, True)
        self.addCleanup(restore)
        self.lib = self.tmp / "lib"
        self.album = self.lib / "Artist" / "Album"
        self.album.mkdir(parents=True)
        self.f1 = audio(self.album / "01 a.flac", "flac", title="a", album="Album", artist="Artist")
        self.f2 = audio(self.album / "02 b.mp3", "libmp3lame", title="b", album="Album", artist="Artist")
        import tagio
        f = tagio.open_file(self.f2)
        mba.write_rating(f, 2)
        tagio.save_id3(f, f.tags)

    def plan_with(self, edit, **profile):
        import plan as pl
        import profiles as pf
        t = [{"album": ["Album"], "albumartist": ["Artist"], "artist": ["Artist"], "title": [x],
              "tracknumber": [str(n)], "totaltracks": ["2"]} for n, x in ((1, "a"), (2, "b"))]
        prop = pl.Proposal(source_dir=str(self.album), files=[str(self.f1), str(self.f2)], targets=t,
                           provider="musicbrainz", release_id=REL, confidence=99.0, margin=20.0,
                           evidence=["test"], release={"album": ["Album"], "albumartist": ["Artist"]})
        prof = pf.from_dict(dict({"name": "t", "sources": [{"path": str(self.lib)}],
                                  "thresholds": {"organize_min_confidence": 95}}, **profile))
        p = pl.build([prop], prof)
        pl.apply_edits(p["albums"][0], edit, prof)
        return p

    def test_written_journaled_and_undone(self):
        import executor
        p = self.plan_with({"tags": {}, "file_tags": {str(self.f1): {"~rating": ["4"]},
                                                      str(self.f2): {"~rating": ["5"]}}})
        changes = {Path(fe["src"]).name: fe["changes"].get("~rating") for fe in p["albums"][0]["files"]}
        self.assertEqual(changes, {"01 a.flac": ["4"], "02 b.mp3": ["5"]})
        res = executor.Executor(p).run()
        self.assertEqual((mba.read_rating(self.f1), mba.read_rating(self.f2)), (4, 5), res)
        self.assertEqual(p["albums"][0]["files"][0]["changes"]["~rating"], ["4"])  # (the plan kept it)
        events = [json.loads(x) for x in (executor.job_dir(p["job"]) / "journal.jsonl").read_text().splitlines()]
        self.assertTrue([e for e in events if e.get("ev") == "file" and e.get("state") == "committed"])
        out = executor.undo(p["job"])
        self.assertFalse([o for o in out if o.startswith("PROBLEM")], out)
        self.assertEqual((mba.read_rating(self.f1), mba.read_rating(self.f2)), (None, 2))

    def test_a_copy_interrupted_after_its_replace_is_recognised_and_put_back(self):
        # (the copy with your rating was in place, "committed" not yet journaled: recovery must
        # know the file as the job's - tagio.read has no "~rating" - and remove it)
        import executor
        dest = self.tmp / "out"
        dest.mkdir()
        p = self.plan_with({"tags": {}, "file_tags": {str(self.f1): {"~rating": ["4"]}}},
                           operation="copy", destination=str(dest))
        fe = p["albums"][0]["files"][0]
        self.assertEqual((fe["op"], fe["changes"].get("~rating")), ("copy", ["4"]))
        real_place = executor._place

        def crash_after_place(stage, dst):
            real_place(stage, dst)
            raise KeyboardInterrupt
        with mock.patch.object(executor, "_place", crash_after_place):
            with self.assertRaises(KeyboardInterrupt):
                executor.Executor(p).run()
        copy = Path(fe["dst"])
        self.assertEqual(mba.read_rating(copy), 4)
        done = executor.recover(p["job"])
        self.assertTrue(any("put back as it was" in d for d in done), done)
        self.assertFalse(copy.exists(), list(dest.rglob("*")))   # (no orphan copy left beside the original)
        self.assertTrue(self.f1.exists() and self.f2.exists())
        self.assertIsNone(mba.read_rating(self.f1))
        self.assertEqual(executor.pending_jobs(), [])

    def test_a_rating_that_did_not_read_back_fails_the_album(self):
        import executor
        p = self.plan_with({"tags": {}, "file_tags": {str(self.f1): {"~rating": ["4"]}}})
        with mock.patch.object(mba, "write_rating", lambda *a, **k: None):
            executor.Executor(p).run()
        self.assertIsNone(mba.read_rating(self.f1))
        events = [json.loads(x) for x in (executor.job_dir(p["job"]) / "journal.jsonl").read_text().splitlines()]
        failed = [e for e in events if e.get("ev") == "album_failed"]
        self.assertTrue(failed and "~rating" in failed[0]["error"], events)


if __name__ == "__main__":
    unittest.main(argv=[sys.argv[0]])
