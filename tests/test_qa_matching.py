"""Matching, providers, settings and reports: regressions found by a QA pass. Mocked network,
temporary XDG folders, no music files unless a test makes its own."""
import os as _os, tempfile as _tempfile  # noqa: E402
_os.environ.setdefault("XDG_CONFIG_HOME", _tempfile.mkdtemp(prefix="tm-config-"))
_os.environ.setdefault("XDG_STATE_HOME", _tempfile.mkdtemp(prefix="tm-state-"))
import sqlite3
import sys
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import match_albums as ma  # noqa: E402
import match_core as mc  # noqa: E402


class RetryAfter(unittest.TestCase):
    """A 429 or 503 whose Retry-After is an HTTP date, a fraction, negative or huge is still a
    rate limit: the request gives up as `rate_limited` (retried later), never with a ValueError
    that reads as a broken release or crashes the album, and never sleeps for hours."""

    def ask(self, header, code=429, host="discogs"):
        http = ma.Http(sqlite3.connect(":memory:"), dict(mc.DEFAULT_CFG), "t")
        sleeps = []

        def urlopen(req, timeout=None):
            raise urllib.error.HTTPError(req.full_url, code, "slow down", header, None)
        with mock.patch.object(ma.urllib.request, "urlopen", urlopen), \
                mock.patch.object(ma.time, "sleep", sleeps.append), mock.patch("sys.stderr"):
            url = "https://api.discogs.com/x" if host == "discogs" else "https://musicbrainz.org/ws/2/x"
            with self.assertRaises(ma.ProviderUnavailable) as e:
                http.get_text(url, host)
        return e.exception, sleeps

    def test_http_date_fraction_negative_and_missing_headers(self):
        for header in ({"Retry-After": "Wed, 21 Oct 2015 07:28:00 GMT"}, {"Retry-After": "1.5"},
                       {"Retry-After": "-5"}, {"Retry-After": "soon"}, {}, None):
            for code, host in ((429, "discogs"), (503, "mb")):
                with self.subTest(header=header, code=code):
                    exc, sleeps = self.ask(header, code, host)
                    self.assertEqual(exc.kind, "rate_limited")
                    self.assertTrue(all(s >= 0 for s in sleeps), sleeps)

    def test_a_huge_retry_after_is_capped(self):
        exc, sleeps = self.ask({"Retry-After": "99999"})
        self.assertEqual(exc.kind, "rate_limited")
        self.assertLessEqual(max(sleeps), ma.RETRY_AFTER_MAX + 1)

    def test_values(self):
        self.assertEqual(ma.retry_after({"Retry-After": "40"}), 40)
        self.assertEqual(ma.retry_after({"Retry-After": "1.5"}), 1.5)
        self.assertEqual(ma.retry_after({"Retry-After": "Thu, 01 Jan 1970 00:01:00 GMT"}, now=30), 30)
        self.assertEqual(ma.retry_after({"Retry-After": "Thu, 01 Jan 1970 00:01:00 GMT"}, now=90), 0)
        self.assertEqual(ma.retry_after({"Retry-After": "nan"}), 0)
        self.assertEqual(ma.retry_after(None), 0)


class R:
    """A urlopen answer."""

    def __init__(self, body):
        self.body = body

    def read(self):
        return self.body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        pass


class CacheKeysHoldNoCredentials(unittest.TestCase):
    """AcoustID takes its application key (a credential: `acoustid`) in the query: the answer is
    cached without it, and rows an older version stored with it are dropped."""

    def test_acoustid_lookup_cached_without_the_key(self):
        db = sqlite3.connect(":memory:")
        http = ma.Http(db, dict(mc.DEFAULT_CFG), "t")
        asked = []

        def urlopen(req, timeout=None):
            asked.append(req.full_url)
            return R(b'{"status": "ok", "results": []}')
        url = "https://api.acoustid.org/v2/lookup?client=SECRETKEY&format=json&duration=10&fingerprint=AQAB"
        with mock.patch.object(ma.urllib.request, "urlopen", urlopen), mock.patch.object(ma.time, "sleep"):
            self.assertEqual(http.get_json(url, "acoustid"), {"status": "ok", "results": []})
            self.assertEqual(http.get_json(url, "acoustid"), {"status": "ok", "results": []})
        self.assertEqual(len(asked), 1)                       # the second answer came from the cache
        self.assertIn("client=SECRETKEY", asked[0])           # (the request itself carries it)
        rows = [r[0] for r in db.execute("SELECT url FROM http_cache")]
        self.assertEqual(len(rows), 1)
        self.assertNotIn("SECRETKEY", rows[0])
        self.assertNotIn("client=", rows[0])

    def test_old_rows_with_the_key_are_dropped(self):
        db = sqlite3.connect(":memory:")
        db.execute("CREATE TABLE http_cache (url TEXT PRIMARY KEY, body TEXT, ts REAL DEFAULT 0)")
        db.execute("INSERT INTO http_cache VALUES (?, '{}', 0)",
                   ("https://api.acoustid.org/v2/lookup?client=OLDKEY&format=json",))
        db.execute("INSERT INTO http_cache VALUES ('https://musicbrainz.org/ws/2/release/x?fmt=json', '{}', 0)")
        ma.Http(db, dict(mc.DEFAULT_CFG), "t")
        rows = [r[0] for r in db.execute("SELECT url FROM http_cache")]
        self.assertEqual(rows, ["https://musicbrainz.org/ws/2/release/x?fmt=json"])


if __name__ == "__main__":
    unittest.main()
