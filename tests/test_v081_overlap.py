# 0.8.1 speed: the Discogs search on a worker while MusicBrainz is searched, and the scans
# read while the databases are searched. Only the order of the work changes: the plan and
# the result are the same as with everything in turn (CONCURRENT_SOURCES/CONCURRENT_SCANS off).
import os as _os, tempfile as _tempfile  # noqa: E402
_os.environ.setdefault("XDG_CONFIG_HOME", _tempfile.mkdtemp(prefix="tm-config-"))
_os.environ.setdefault("XDG_STATE_HOME", _tempfile.mkdtemp(prefix="tm-state-"))

import json
import sqlite3
import sys
import threading
import time
import types
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_engine as te  # noqa: E402  (its fixtures; sets the plugin path and the defaults)
from test_engine import mb_release, dg_release  # noqa: E402

import match_albums as ma  # noqa: E402
import match_core as mc  # noqa: E402

DELAY = 0.05             # mb_delay and discogs_delay in these tests
LATENCY = 0.03           # each simulated request's time on the wire


def _host(url):
    if "musicbrainz.org" in url:
        return "mb"
    if "api.discogs.com" in url:
        return "discogs"
    return url.split("/")[2]


class Wire:
    """urllib.request.urlopen for match_albums.Http: te.FakeHttp's answers, each request
    taking LATENCY seconds, every request recorded (host, start, end, thread name)."""

    def __init__(self, fake, latency=LATENCY, discogs_auth_fails=False):
        self.fake, self.latency, self.auth_fails = fake, latency, discogs_auth_fails
        self.log, self.lock = [], threading.Lock()

    def __call__(self, req, timeout=None):
        url = req.full_url
        host = _host(url)
        t0 = time.time()
        time.sleep(self.latency)
        with self.lock:
            self.log.append((host, t0, time.time(), threading.current_thread().name))
        if host == "discogs" and self.auth_fails:
            raise urllib.error.HTTPError(url, 401, "Unauthorized", {}, None)
        body = self.fake.get_json(url, host) if host in ("mb", "discogs") else None
        if body is None:
            raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)
        data = json.dumps(body).encode()

        class R:
            def read(self):
                return data

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False
        return R()


def leftover_threads(within=0.0):
    """The look-ups' worker threads still running (after waiting up to WITHIN seconds: a
    reading made ahead and not needed finishes on its own after a look-up that succeeded)."""
    end = time.time() + within
    while within and time.time() < end and _leftover():
        time.sleep(0.02)
    return _leftover()


def _leftover():
    return [t.name for t in threading.enumerate()
            if t.is_alive() and (t.name.startswith("discogs-search") or t.name.startswith("scan-reads"))]


def scrub(o):
    """A plan or result without what differs from run to run (times, job ids, temp paths)."""
    if isinstance(o, dict):
        return {k: scrub(v) for k, v in o.items() if k not in ("checked", "job", "created", "time", "ts")}
    if isinstance(o, list):
        return [scrub(v) for v in o]
    return o


class Sources(te.Base):
    """The Discogs search on its worker: same plan, MusicBrainz paced, errors as before."""

    def real_http(self, name):
        db = sqlite3.connect(self.tmp / f"{name}.sqlite", timeout=60)
        return ma.Http(db, dict(mc.DEFAULT_CFG, mb_delay=DELAY, discogs_delay=DELAY), "test-token")

    def plan(self, fake, concurrent, name, breaker_mb=False, **prof):
        wire = Wire(fake)
        http = self.real_http(name)
        if breaker_mb:                                   # MusicBrainz down: its breaker open
            http._breaker()[0]["mb"] = [time.time() + 300, False]
        d = self.lib
        with mock.patch.object(ma, "CONCURRENT_SOURCES", concurrent), \
                mock.patch.object(ma.urllib.request, "urlopen", wire), \
                mock.patch.object(ma, "fingerprint_ready", lambda: False):
            eng = self.engine(http, **prof)
            t0 = time.time()
            plan = eng.plan(str(d))
            took = time.time() - t0
        http.db.close()
        return plan, wire, took

    def same_both_ways(self, fake_factory, **kw):
        seq, wire_s, t_s = self.plan(fake_factory(), False, "seq", **kw)
        con, wire_c, t_c = self.plan(fake_factory(), True, "con", **kw)
        self.assertEqual(json.dumps(scrub(seq), sort_keys=True, default=str),
                         json.dumps(scrub(con), sort_keys=True, default=str))
        # the same requests, in the end
        self.assertEqual(sorted(h for h, *_ in wire_s.log), sorted(h for h, *_ in wire_c.log))
        self.assertEqual(leftover_threads(), [])
        return seq, con, wire_s, wire_c, t_s, t_c

    def assert_paced(self, wire):
        for host in ("mb", "discogs"):
            starts = sorted(t0 for h, t0, *_ in wire.log if h == host)
            gaps = [b - a for a, b in zip(starts, starts[1:])]
            # (each request starts at least the delay after the one before; a little slack
            # for the clock between the pacing and the simulated wire)
            self.assertTrue(all(g >= DELAY - 0.005 for g in gaps), (host, gaps))
            # one host, one thread at a time
            spans = sorted((t0, t1) for h, t0, t1, _n in wire.log if h == host)
            self.assertTrue(all(b[0] >= a[1] - 0.001 for a, b in zip(spans, spans[1:])), host)

    def test_same_plan_both_databases_and_discogs_on_its_worker(self):
        self.album("Band C", "Album C")

        def fake():
            return te.FakeHttp(mb=[mb_release("mb-c", "Band C", "Album C", discogs=902)],
                               dg=[dg_release(902, "Band C", "Album C")])
        seq, con, wire_s, wire_c, t_s, t_c = self.same_both_ways(fake)
        self.assertEqual(con["albums"][0]["identification"]["status"], seq["albums"][0]["identification"]["status"])
        self.assertTrue(any(n == "discogs-search" for h, *_x, n in wire_c.log if h == "discogs"))
        self.assertFalse(any(n == "discogs-search" for h, *_x, n in wire_c.log if h == "mb"))
        self.assertFalse(any(n == "discogs-search" for *_x, n in wire_s.log))
        self.assert_paced(wire_c)
        self.assert_paced(wire_s)

    def test_same_plan_discogs_only_and_musicbrainz_only(self):
        self.album("Band B", "Album B")
        self.same_both_ways(lambda: te.FakeHttp(dg=[dg_release(901, "Band B", "Album B")]))
        self.album("Band A", "Album A")
        self.same_both_ways(lambda: te.FakeHttp(mb=[mb_release("mb-a", "Band A", "Album A")],
                                                dg=[dg_release(901, "Band B", "Album B")]))

    def test_same_plan_when_the_databases_disagree(self):
        self.album("Band D", "Album D")
        self.same_both_ways(lambda: te.FakeHttp(
            mb=[mb_release("mb-d", "Band D", "Album D", catno="MB-1", country="US")],
            dg=[dg_release(903, "Band D", "Album D", catno="DG-9", country="Japan",
                           durs=("0:12", "0:20", "0:31"))]))

    def test_musicbrainz_down_still_gives_the_same_discogs_only_result(self):
        self.album("Band B", "Album B")
        seq, con, *_ = self.same_both_ways(lambda: te.FakeHttp(dg=[dg_release(901, "Band B", "Album B")]),
                                           breaker_mb=True)
        a = con["albums"][0]
        self.assertEqual(a["identification"].get("release_id") or a.get("release", {}).get("discogs_release_id"),
                         seq["albums"][0]["identification"].get("release_id")
                         or seq["albums"][0].get("release", {}).get("discogs_release_id"))
        self.assertIn("musicbrainz", json.dumps(con).lower())

    def test_overlap_saves_the_discogs_time(self):
        self.album("Band C", "Album C")

        def fake():
            return te.FakeHttp(mb=[mb_release("mb-c", "Band C", "Album C", discogs=902)],
                               dg=[dg_release(902, "Band C", "Album C")])
        _s, _c, wire_s, wire_c, t_s, t_c = self.same_both_ways(fake)
        dg_time = sum(t1 - t0 for h, t0, t1, n in wire_c.log if n == "discogs-search")
        self.assertGreater(dg_time, 0)
        # most of the Discogs search's time is hidden behind MusicBrainz's
        self.assertLess(t_c, t_s - 0.5 * dg_time, (t_s, t_c, dg_time))


class SourcesDirect(te.Base):
    """ma.process with a real Http: the worker's errors and its end."""

    def run_process(self, wire, concurrent=True, db_name="c.sqlite", **patches):
        d = self.album("Band C", "Album C")
        db = sqlite3.connect(self.tmp / db_name, timeout=60)
        http = ma.Http(db, dict(mc.DEFAULT_CFG, mb_delay=DELAY, discogs_delay=DELAY), "test-token")
        row = {"AlbumArtistFolder": "Band C", "AlbumFolder": d.name, "FullPath": str(d), "Status": "untagged"}
        args = types.SimpleNamespace(ocr="off", use_shops=False, investigate=False, provider_errors={})
        dm = ma.load_plugin(str(te.PLUGIN))
        ps = [mock.patch.object(ma, "CONCURRENT_SOURCES", concurrent),
              mock.patch.object(ma.urllib.request, "urlopen", wire),
              mock.patch.object(ma, "fingerprint_ready", lambda: False)]
        ps += [mock.patch.object(ma, k, v) for k, v in patches.items()]
        for p in ps:
            p.start()
        try:
            return ma.process(row, http, dict(mc.DEFAULT_CFG, mb_delay=DELAY, discogs_delay=DELAY), dm, args)
        finally:
            for p in ps:
                p.stop()
            db.close()

    def fake(self):
        return te.FakeHttp(mb=[mb_release("mb-c", "Band C", "Album C", discogs=902)],
                           dg=[dg_release(902, "Band C", "Album C")])

    def test_auth_error_from_the_worker_is_raised(self):
        for concurrent in (False, True):
            with self.assertRaises(ma.AuthError):
                self.run_process(Wire(self.fake(), discogs_auth_fails=True), concurrent)
            self.assertEqual(leftover_threads(), [])

    def test_musicbrainz_raising_ends_the_worker(self):
        wire = Wire(self.fake(), latency=0.2)          # a slow Discogs, still searching

        def boom(*a, **k):
            time.sleep(0.1)
            raise RuntimeError("boom")
        t0 = time.time()
        with self.assertRaises(RuntimeError):
            self.run_process(wire, True, mb_search=boom)
        self.assertEqual(leftover_threads(), [])
        # given up: its next request was not made (not the whole search sat through)
        self.assertLess(time.time() - t0, 3.0)
        self.assertLessEqual(len([1 for h, *_ in wire.log if h == "discogs"]), 2)

    def test_same_result_as_in_turn(self):
        a = self.run_process(Wire(self.fake()), False, db_name="a.sqlite")
        b = self.run_process(Wire(self.fake()), True, db_name="b.sqlite")    # (a fresh cache)
        self.assertEqual(json.dumps(scrub(a), sort_keys=True, default=str),
                         json.dumps(scrub(b), sort_keys=True, default=str))


# --------------------------------------------------------------------------
# The scans read while the databases are searched
# --------------------------------------------------------------------------
OCR_S = 0.1              # each simulated reading
PROVIDER_S = 0.04        # each simulated database request


def png(i):
    from PIL import Image
    import io
    b = io.BytesIO()
    Image.new("RGB", (40 + i, 30), (i * 20 % 255, 0, 0)).save(b, "PNG")
    return b.getvalue()


class SlowFake(te.FakeHttp):
    def __init__(self, *a, delay=PROVIDER_S, **k):
        super().__init__(*a, **k)
        self.delay = delay

    def get_json(self, url, host, *a, **k):
        if host in ("mb", "discogs"):
            time.sleep(self.delay)
        return super().get_json(url, host)


class Scans(te.Base):
    NAMES = ["back.png", "tray.png", "disc.png", "booklet 01.png", "booklet 02.png", "obi.png"]

    def setUp(self):
        super().setUp()
        self.d = self.album("Band C", "Album C")
        for i, n in enumerate(self.NAMES):
            (self.d / n).write_bytes(png(i))
        self.calls, self.lock = [], threading.Lock()

    def reader(self, kind, out):
        def read(paths):
            time.sleep(self.ocr_s)
            with self.lock:
                self.calls.append((kind, Path(paths[0]).name, threading.current_thread().name))
            name = Path(paths[0]).name
            return out(name)
        return read

    def run_it(self, concurrent, settles=False, ocr_s=OCR_S, provider_s=PROVIDER_S, db_name=None, mb_search=None):
        self.ocr_s = ocr_s
        self.calls = []
        row = {"AlbumArtistFolder": "Band C", "AlbumFolder": self.d.name, "FullPath": str(self.d),
               "Status": "untagged"}
        args = types.SimpleNamespace(ocr="on", max_images=15, use_shops=False, investigate=False,
                                     provider_errors={})
        http = SlowFake(mb=[mb_release("mb-c", "Band C", "Album C", discogs=902)],
                        dg=[dg_release(902, "Band C", "Album C")], delay=provider_s)
        http.db = sqlite3.connect(self.tmp / (db_name or f"ocr-{concurrent}.sqlite"), timeout=60)
        seen = {}
        real_run_source = ma.run_source

        def run_source(name, finder, album, cfg, ocr_txt=""):
            seen["album"] = album
            return real_run_source(name, finder, album, cfg, ocr_txt)

        def learn(log, final):
            seen["log"] = {k: (sorted(v) if isinstance(v, set) else {p: list(x) for p, x in v.items()})
                           for k, v in log.items()}
        ps = [mock.patch.object(ma, "CONCURRENT_SCANS", concurrent),
              mock.patch.object(ma, "ocr_text", self.reader("text", lambda n: f"TEXT {n} CAT-1")),
              mock.patch.object(ma, "ocr_spines", self.reader("spines", lambda n: f"SPINE {n}" if "b" in n else "")),
              mock.patch.object(ma, "ocr_barcode_digits", self.reader("digits", lambda n: "")),
              mock.patch.object(ma, "ocr_disc_rings", self.reader("rings",
                                                                  lambda n: "RING X1" if n == "disc.png" else "")),
              mock.patch.object(ma, "ocr_barcodes", lambda paths, *a, **k: set()),
              mock.patch.object(ma, "ocr_deep", lambda paths, *a, **k: ""),
              mock.patch.object(ma, "web_release_links", lambda *a, **k: ([], [])),
              mock.patch.object(ma, "fingerprint_ready", lambda: False),
              mock.patch.object(ma, "learn_scan_order", learn),
              mock.patch.object(ma, "run_source", run_source)]
        if settles:
            ps.append(mock.patch.object(ma, "_barcode_settles", lambda final, album: True))
        if mb_search is not None:
            ps.append(mock.patch.object(ma, "mb_search", mb_search))
        for p in ps:
            p.start()
        try:
            t0 = time.time()
            out = ma.process(row, http, dict(mc.DEFAULT_CFG), ma.load_plugin(str(te.PLUGIN)), args)
            took = time.time() - t0
        finally:
            for p in ps:
                p.stop()
        cached = sorted(r[0].split(":")[0] for r in http.db.execute("SELECT key FROM ocr_cache"))
        http.db.close()
        album = seen.get("album") or {}
        return {"out": scrub(out), "printed": album.get("printed_text"), "ring": album.get("ring_text"),
                "log": seen.get("log"), "cached": cached}, took

    def test_same_reading_and_result(self):
        for settles in (False, True):
            a, _ = self.run_it(False, settles, db_name=f"s{settles}a.sqlite")
            b, _ = self.run_it(True, settles, db_name=f"s{settles}b.sqlite")
            self.assertEqual(json.dumps(a, sort_keys=True, default=str), json.dumps(b, sort_keys=True, default=str))
            self.assertIn("RING X1", b["printed"])
            self.assertTrue(any(n.startswith("scan-reads") for *_x, n in self.calls))
            self.assertEqual(leftover_threads(within=3 * OCR_S), [], settles)

    def test_only_the_scans_used_are_logged_and_cached(self):
        b, _ = self.run_it(True, settles=True)
        self.assertIn("scans_read", b["out"])
        n_scans = len(self.NAMES)
        # page text and spines of the first three only; every disc ring; digits (no barcode) of all
        kinds = [k.split("-eng")[0].split("-")[0] if not k.startswith(("disc-", "barcode")) else k
                 for k in b["cached"]]
        self.assertEqual(kinds.count("text"), 3)
        self.assertEqual(kinds.count("spines"), 3)
        self.assertEqual(kinds.count("disc-rings-2"), n_scans)
        self.assertEqual(kinds.count("barcode-digits"), n_scans)
        texts = b["log"]["text"]
        self.assertTrue(all(sum(1 for v in vs if v.startswith("TEXT")) <= 1 for vs in texts.values()))
        self.assertEqual(sum(1 for vs in texts.values() for v in vs if v.startswith("TEXT")), 3)

    def test_a_scan_read_before_is_not_read_again(self):
        self.run_it(True, db_name="again.sqlite")
        b, _ = self.run_it(True, db_name="again.sqlite")
        self.assertEqual(self.calls, [])
        self.assertEqual(b["log"]["read"], [])

    def test_wall_time_about_the_longer_of_the_two(self):
        _, prov = self.run_it(False, ocr_s=0.0, db_name="p.sqlite")
        _, ocr = self.run_it(False, provider_s=0.0, db_name="o.sqlite")
        _, seq = self.run_it(False, db_name="s.sqlite")
        _, con = self.run_it(True, db_name="c.sqlite")
        # in turn: about the sum; together: well under it, about the longer one. (The sum of
        # the two measured alone, not the one sequential run: on a busy CI machine a single
        # run's time is noise - 1.17 s in turn against 1.35 s for the databases alone)
        self.assertLess(con, max(seq, prov + ocr) - 0.5 * min(prov, ocr), (prov, ocr, seq, con))
        self.assertLess(con, max(prov, ocr) + 0.5 * min(prov, ocr) + 0.3, (prov, ocr, seq, con))

    def test_a_failed_look_up_leaves_no_pool_running(self):
        def boom(*a, **k):
            time.sleep(0.05)
            raise RuntimeError("cancelled")
        with self.assertRaises(RuntimeError):
            self.run_it(True, ocr_s=0.3, mb_search=boom)
        self.assertEqual(leftover_threads(), [])
        n = len(self.calls)
        time.sleep(0.4)
        self.assertEqual(len(self.calls), n)               # nothing read after it returned


if __name__ == "__main__":
    unittest.main()
