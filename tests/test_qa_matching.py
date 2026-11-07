"""Matching, providers, settings and reports: regressions found by a QA pass. Mocked network,
temporary XDG folders, no music files unless a test makes its own."""
import os as _os, tempfile as _tempfile  # noqa: E402
_os.environ.setdefault("XDG_CONFIG_HOME", _tempfile.mkdtemp(prefix="tm-config-"))
_os.environ.setdefault("XDG_STATE_HOME", _tempfile.mkdtemp(prefix="tm-state-"))
import os
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


class WatchFutureTimes(unittest.TestCase):
    """A dropped album holding a file whose modification time is ahead of the clock (copied with
    its times from a device with a wrong clock, or a FAT stick's time zone) settles as any other:
    it was "still changing" until the clock reached that time."""

    def setUp(self):
        import shutil
        import tempfile
        self.d = Path(tempfile.mkdtemp(prefix="tm-qa-watch-"))
        self.addCleanup(shutil.rmtree, self.d, True)

    def test_a_file_dated_next_year_does_not_hold_the_album(self):
        import time
        import watcher
        album = self.d / "Band - Album"
        album.mkdir()
        for n in (1, 2):
            (album / f"0{n} Track.flac").write_bytes(b"x" * 10)
        old = time.time() - 3600
        for f in [album, *album.iterdir()]:
            os.utime(f, (old, old))
        ahead = time.time() + 365 * 86400
        os.utime(album / "02 Track.flac", (ahead, ahead))
        _sig, newest, partial = watcher.snapshot(album, sorted(album.iterdir()))
        self.assertFalse(partial)
        self.assertLessEqual(newest, time.time() + 1)
        # (the status-change time stands for it: the copy made just now is still settling)
        self.assertGreater(newest, time.time() - 600)
        st = (album / "02 Track.flac").stat()
        self.assertEqual(watcher._changed_at(st, now=st.st_ctime + 7200), st.st_ctime)
        self.assertEqual(watcher._changed_at((album / "01 Track.flac").stat()), old)


class MalformedProfileFiles(unittest.TestCase):
    """A profile file with a section of the wrong kind is a ProfileError (said, and that profile
    skipped), never a ValueError or AttributeError: the watcher and the command line catch
    ProfileError, so one hand-edited profile stopped `tagmender watch` for every profile."""

    def setUp(self):
        import json
        import profiles as pf
        self.pf = pf
        pf.profiles_dir().mkdir(parents=True, exist_ok=True)
        self.written = []
        for name, data in (("qa-bad-version", {"version": "two"}), ("qa-bad-unresolved", {"unresolved": []})):
            pf.path_of(name).write_text(json.dumps(data), encoding="utf-8")
            self.written.append(pf.path_of(name))
        self.inbox = Path(_tempfile.mkdtemp(prefix="tm-qa-inbox-"))
        good = pf.from_dict({"name": "qa-good", "sources": [{"path": str(self.inbox), "watch": True}]})
        pf.save(good)
        self.written.append(pf.path_of("qa-good"))

    def tearDown(self):
        import shutil
        for f in self.written:
            f.unlink(missing_ok=True)
        shutil.rmtree(self.inbox, ignore_errors=True)

    def test_load_says_what_is_wrong(self):
        for name in ("qa-bad-version", "qa-bad-unresolved"):
            with self.subTest(name=name), self.assertRaises(self.pf.ProfileError) as e:
                self.pf.load(name)
            self.assertIn(name, str(e.exception))

    def test_the_watcher_skips_it_and_watches_the_others(self):
        import logging
        import watcher
        log = logging.getLogger("qa-watch")
        with self.assertLogs(log, "WARNING") as said:
            got = watcher.Watcher._watched_profiles(log)
        self.assertEqual([p.name for p in got], ["qa-good"])
        self.assertTrue(any("qa-bad-version" in m for m in said.output))

    def test_nan_settle_minutes_is_refused(self):
        with self.assertRaises(self.pf.ProfileError):
            self.pf.from_dict({"name": "x", "watch": {"settle_minutes": float("nan")}})


class CliMissingPath(unittest.TestCase):
    """A folder that is not there (mistyped, a share not mounted) is an error (exit 1), never an
    empty plan or scan that exits 0 for a script to take as done; likewise an unknown job."""

    def cli(self, *argv):
        import contextlib
        import io
        import tagmender
        err = io.StringIO()
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err), \
                mock.patch.object(ma.urllib.request, "urlopen", side_effect=AssertionError("network")):
            code = tagmender.main(list(argv))
        return code, err.getvalue()

    def test_missing_path(self):
        missing = str(Path(_tempfile.mkdtemp(prefix="tm-qa-cli-")) / "not-there")
        for cmd in ("scan", "plan", "run", "unresolved", "retry"):
            with self.subTest(cmd=cmd):
                code, err = self.cli(cmd, missing)
                self.assertEqual(code, 1)
                self.assertIn("no such folder", err)

    def test_an_empty_folder_is_still_fine(self):
        empty = _tempfile.mkdtemp(prefix="tm-qa-cli-")
        self.assertEqual(self.cli("scan", empty)[0], 0)

    def test_unknown_job(self):
        code, err = self.cli("jobs", "20990101-000000-none")
        self.assertEqual(code, 1)
        self.assertIn("no job", err)


if __name__ == "__main__":
    unittest.main()
