# Tests for 0.8.0's stricter lookups, with fakes and a simulated clock (no network):
# - a failed MusicBrainz link or ISRC lookup keeps the files' own release link and is reported;
# - a failed disc ID lookup no longer lets weak clues pick the pressing automatically, and is
#   asked once more after the search;
# - the MusicBrainz/Discogs outage breaker in Http.
import collections
import json
import sqlite3
import sys
import threading
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import test_engine as te  # noqa: E402  (its settings and state dirs are temporary)
import match_albums as ma  # noqa: E402
import match_core as mc  # noqa: E402

TAG_LINK = "11111111-1111-1111-1111-111111111111"


# ---- Q1: link and ISRC lookups ---------------------------------------------------------
class LinkLookups(unittest.TestCase):
    class H:
        def __init__(self, fail_isrc=(), fail_url=False):
            self.fail_isrc, self.fail_url, self.calls = set(fail_isrc), fail_url, []

        def get_json(self, url, host):
            self.calls.append(url)
            if "/isrc/" in url:
                code = url.split("/isrc/")[1].split("?")[0]
                if code in self.fail_isrc:
                    raise ma.ProviderUnavailable("mb", "rate_limited", "giving up (HTTP 503)")
                return {"recordings": [{"releases": [{"id": "r1"}, {"id": "r2" if code == "A" else "r3"}]}]}
            if "/ws/2/url" in url:
                if self.fail_url:
                    raise ma.ProviderUnavailable("mb", "outage", "giving up (URLError)")
                return {"relations": [{"release": {"id": "shop-rel"}}]}
            return {}

    def test_a_failed_isrc_lookup_is_reported(self):
        # (the audit's link_demo: the failure was not said.) 0.8.1: the files' own MusicBrainz
        # link is another tagger's word, a candidate (tag_release_mb), never in link_releases
        album = {"tag_mb_links": {TAG_LINK}, "isrcs": ["GBAAA0000001"], "tag_urls": []}
        ma.link_releases(self.H(fail_isrc={"GBAAA0000001"}), album)
        self.assertEqual(album["link_releases"], set())
        self.assertEqual(album["isrc_releases"], set())
        self.assertEqual(album["clue_failures"]["musicbrainz link lookup"][1], "rate_limited")
        errors = {}
        notes = ma.clue_failure_notes(album, errors)
        self.assertIn("links or ISRCs failed", notes[0])
        self.assertEqual(errors, {"musicbrainz": "rate_limited"})

    def test_a_failed_url_lookup_is_reported(self):
        # (0.8.1: the tags' own MusicBrainz link is no "source link": not kept here either)
        album = {"tag_mb_links": {TAG_LINK}, "tag_urls": ["https://x.bandcamp.com/album/y"], "isrcs": []}
        ma.link_releases(self.H(fail_url=True), album)
        self.assertEqual(album["link_releases"], set())
        self.assertIn("musicbrainz link lookup", album["clue_failures"])

    def test_isrcs_failing_partway_give_no_isrc_evidence(self):
        # A answers {r1, r2}; B fails: r1/r2 would be a looser match than all codes' releases
        album = {"isrcs": ["A", "B"]}
        ma.link_releases(self.H(fail_isrc={"B"}), album)
        self.assertEqual(album["isrc_releases"], set())
        album = {"isrcs": ["A", "C"]}
        ma.link_releases(self.H(), album)                     # (all answer: the intersection)
        self.assertEqual(album["isrc_releases"], {"r1"})
        self.assertNotIn("clue_failures", album)

    def test_a_disc_id_failure_recorded_by_mb_search_is_not_overwritten(self):
        errors = {"musicbrainz": "outage"}
        ma.clue_failure_notes({"clue_failures": {"musicbrainz link lookup": ("503", "rate_limited")}}, errors)
        self.assertEqual(errors, {"musicbrainz": "outage"})


class IsrcDownHttp(te.FakeHttp):
    def get_json(self, url, host):
        if host == "mb" and "/isrc/" in url:
            self.calls.append((host, url))
            raise ma.ProviderUnavailable("mb", "rate_limited", "giving up (HTTP 503)")
        return super().get_json(url, host)


class LinkLookupsInTheEngine(te.Base):
    def test_the_files_own_link_is_no_proof_and_the_album_is_retried(self):
        # 0.8.1: a MusicBrainz release link in the tags is another tagger's word (a candidate,
        # never "source link"), so with the ISRC lookup lost nothing proves the pressing:
        # retried, as without the link (0.8.0 filed it on the link)
        d = self.album("Band K", "Album K", comment=f"https://musicbrainz.org/release/{TAG_LINK}",
                       isrc="DEXX05000000")
        eng = self.engine(IsrcDownHttp(mb=[te.mb_release(TAG_LINK, "Band K", "Album K")]))
        p = eng.identify(eng.albums_in(d)[0])
        self.assertEqual(p.status, "unresolved", p.reason)
        self.assertEqual(p.provider_errors.get("musicbrainz"), "rate_limited")
        self.assertIn("nothing else proves this pressing", p.reason)

    def test_without_hard_proof_the_album_is_retried_not_filed(self):
        d = self.album("Band L", "Album L", isrc="DEXX05000000")
        eng = self.engine(IsrcDownHttp(mb=[te.mb_release("mb-l", "Band L", "Album L")]))
        p = eng.identify(eng.albums_in(d)[0])
        self.assertEqual(p.status, "unresolved")
        self.assertEqual(p.provider_errors.get("musicbrainz"), "rate_limited")
        self.assertIn("rate_limited", p.reason_codes)
        self.assertIn("nothing else proves this pressing", p.reason)

    def test_answered_lookups_identify_as_before(self):
        d = self.album("Band L2", "Album L2", isrc="DEXX05000000")
        eng = self.engine(te.FakeHttp(mb=[te.mb_release("mb-l2", "Band L2", "Album L2")]))
        p = eng.identify(eng.albums_in(d)[0])
        self.assertEqual(p.status, "identified")
        self.assertFalse(p.provider_errors)


# ---- Q2: a failed disc ID lookup -------------------------------------------------------
def pressing(rid, country, date):
    r = te.mb_release(rid, "Band D", "Album D", country=country)
    r["date"] = date
    r["barcode"] = ""
    return r


class DiscIdHttp(te.FakeHttp):
    """Two CD pressings (US, DE) of one album; the disc ID is attached to the DE one. fails:
    how many disc ID lookups fail (with MusicBrainz's 503) before one answers."""

    def __init__(self, fails):
        super().__init__(mb=[pressing("mb-us", "US", "2005-03-01"), pressing("mb-de", "DE", "2005-02-20")])
        self.fails = fails

    def discid_calls(self):
        return [u for h, u in self.calls if "/ws/2/discid/" in u]

    def get_json(self, url, host):
        if host == "mb" and "/ws/2/discid/" in url:
            self.calls.append((host, url))
            if len(self.discid_calls()) <= self.fails:
                raise ma.ProviderUnavailable("mb", "rate_limited", "giving up (HTTP 503)")
            did = url.split("/discid/")[1].split("?")[0]
            return {"id": did, "releases": [{"id": "mb-de", "media": [{"position": 1, "discs": [{"id": did}]}]}]}
        return super().get_json(url, host)


class DiscIdLookup(te.Base):
    """The audit's discid_demo through the engine: the files' tag date is the US pressing's,
    the disc ID is attached to the DE one."""

    def run_album(self, http):
        d = self.album("Band D", "Album D", date="2005-03-01")
        (d / "rip.log").write_text(te.EAC_LOG)
        eng = self.engine(http, providers={"discogs": {"enabled": False}, "web": {"enabled": False}})
        eng.cfg["preferred_countries"] = ["US", "DE"]
        return eng.identify(eng.albums_in(d)[0])

    def test_answered_disc_id_picks_its_pressing(self):
        http = DiscIdHttp(fails=0)
        p = self.run_album(http)
        self.assertEqual((p.status, p.release_id), ("identified", "mb-de"), p.reason)
        self.assertEqual(len(http.discid_calls()), 1)                   # (no re-ask)

    def test_failed_disc_id_is_an_error_retried_not_an_auto_pick(self):
        http = DiscIdHttp(fails=2)                                      # the re-ask fails too
        p = self.run_album(http)
        self.assertEqual(p.status, "unresolved", (p.release_id, p.why))
        self.assertEqual(p.provider_errors.get("musicbrainz"), "rate_limited")
        self.assertIn("MusicBrainz disc ID failed and nothing else proves this pressing", p.reason)
        self.assertEqual(len(http.discid_calls()), 2)                   # one re-ask, no more

    def test_the_re_ask_after_the_search_can_still_decide(self):
        http = DiscIdHttp(fails=1)
        p = self.run_album(http)
        self.assertEqual((p.status, p.release_id), ("identified", "mb-de"), p.reason)
        self.assertNotIn("musicbrainz", p.provider_errors or {})
        self.assertFalse(any("disc ID lookup failed" in w for w in p.why or []), p.why)

    def test_the_re_ask_is_a_single_try_on_the_real_http(self):
        http = ma.Http(sqlite3.connect(":memory:"), dict(mc.DEFAULT_CFG), "t")
        album = {"tocs": {1: "1 3 4650 150 900 2400"}, "clue_failures": {"musicbrainz disc ID": ("503", "rate_limited")}}
        album["discids"] = [(ma.mb_discid(album["tocs"][1]), album["tocs"][1], None)]
        tries = []

        def fake_open(req, timeout=None):
            tries.append(req.full_url)
            raise urllib.error.HTTPError(req.full_url, 503, "busy", {}, None)
        with mock.patch.object(ma.urllib.request, "urlopen", fake_open), mock.patch.object(ma.time, "sleep"):
            self.assertFalse(ma.retry_disc_ids(http, album))
        self.assertEqual(len(tries), 1)
        self.assertIn("musicbrainz disc ID", album["clue_failures"])

    def test_a_disc_lookup_that_fails_as_a_whole_is_retried_too(self):
        # (an unexpected error from the whole disc lookup: recorded as "disc lookup", which
        # the guard once missed, so the date picked the US pressing automatically)
        http = DiscIdHttp(fails=0)
        with mock.patch.object(ma, "toc_releases", mock.Mock(side_effect=RuntimeError("boom"))):
            p = self.run_album(http)
        self.assertNotEqual(p.status, "identified", (p.release_id, p.why))
        self.assertIn("disc lookup failed and nothing else proves this pressing", p.reason)

    def test_hard_proof(self):
        self.assertTrue(ma.hard_proof({"clues": {"disc TOC": mc.EXACT_TOC_CLUE}, "code": ""}))
        self.assertTrue(ma.hard_proof({"clues": {"source link": "x"}, "code": ""}))
        self.assertFalse(ma.hard_proof({"clues": {"fingerprint": "x"}, "code": ""}))   # (recordings, not the pressing)
        self.assertTrue(ma.hard_proof({"clues": {}, "code": "barcode (OCR)"}))
        self.assertFalse(ma.hard_proof({"clues": {"disc TOC": mc.TOC_CLUE, "isrc": "x", "date": "x"}, "code": ""}))
        self.assertFalse(ma.hard_proof(None))


# ---- S6: the outage breaker ------------------------------------------------------------
class Clock:
    def __init__(self):
        self.t = 1e9

    def time(self):
        return self.t

    def sleep(self, s):
        self.t += s


class R:
    def __init__(self, body):
        self.body = body if isinstance(body, str) else json.dumps(body)

    def read(self, *a):
        return self.body.encode()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        pass


class Breaker(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.http = ma.Http(sqlite3.connect(":memory:"), dict(mc.DEFAULT_CFG), "t")
        self.live = collections.Counter()
        self.mode = {"mb": "timeout", "discogs": "ok"}
        for p in (mock.patch.object(ma.urllib.request, "urlopen", self.open),
                  mock.patch.object(ma.time, "sleep", self.clock.sleep),
                  mock.patch.object(ma.time, "time", self.clock.time),
                  mock.patch("sys.stderr")):
            p.start()
            self.addCleanup(p.stop)

    def open(self, req, timeout=None):
        url = req.full_url
        host = "mb" if "musicbrainz" in url else "discogs"
        self.live[host] += 1
        mode = self.mode[host]
        if mode == "timeout":
            self.clock.t += timeout
            raise urllib.error.URLError(TimeoutError("timed out"))
        if mode in (429, 503):
            raise urllib.error.HTTPError(url, mode, "slow down", {"Retry-After": "1"}, None)
        self.clock.t += 0.3
        return R({"ok": url})

    def get(self, n, host="mb"):
        base = "https://musicbrainz.org/ws/2/x" if host == "mb" else "https://api.discogs.com/x"
        return self.http.get_text(f"{base}/{n}", host)

    def test_after_a_give_up_uncached_requests_fail_at_once_and_cached_ones_answer(self):
        self.mode["mb"] = "ok"
        self.get("cached")
        self.mode["mb"] = "timeout"
        with self.assertRaises(ma.ProviderUnavailable) as e:
            self.get(1)
        self.assertEqual(e.exception.kind, "outage")
        live, t = self.live["mb"], self.clock.t
        with self.assertRaises(ma.ProviderUnavailable) as e:
            self.get(2)
        self.assertEqual((e.exception.kind, e.exception.host), ("outage", "mb"))
        self.assertEqual((self.live["mb"], self.clock.t), (live, t))      # no request, no wait
        self.assertIn("ok", self.get("cached"))                          # the cache still answers
        self.assertIn("ok", self.get(3, "discogs"))                      # the other host is not affected

    def test_a_429_storm_never_trips_it(self):
        for mode in (429, 503):                                          # (MusicBrainz's 503: slow down)
            self.mode["mb"] = mode
            for n in range(3):
                before = self.live["mb"]
                with self.assertRaises(ma.ProviderUnavailable) as e:
                    self.get(f"{mode}-{n}")
                self.assertEqual(e.exception.kind, "rate_limited")
                self.assertEqual(self.live["mb"] - before, 6)            # each request really asked

    def test_discogs_5xx_outage_trips_it_and_429_does_not(self):
        self.mode["discogs"] = 429
        with self.assertRaises(ma.ProviderUnavailable):
            self.get(1, "discogs")
        before = self.live["discogs"]
        with self.assertRaises(ma.ProviderUnavailable):
            self.get(2, "discogs")
        self.assertGreater(self.live["discogs"], before)
        self.mode["discogs"] = "timeout"
        with self.assertRaises(ma.ProviderUnavailable):
            self.get(3, "discogs")
        before = self.live["discogs"]
        with self.assertRaises(ma.ProviderUnavailable):
            self.get(4, "discogs")
        self.assertEqual(self.live["discogs"], before)

    def test_a_single_try_re_ask_never_trips_it(self):
        # (retry_disc_ids' re-ask: one network error is no outage of the host)
        for n in range(2):
            with self.assertRaises(ma.ProviderUnavailable):
                self.http.get_text(f"https://musicbrainz.org/ws/2/x/once{n}", "mb", attempts=1)
        self.mode["mb"] = "ok"
        before = self.live["mb"]
        self.assertIn("ok", self.get(2))                                 # asked live: not skipped
        self.assertEqual(self.live["mb"] - before, 1)
        self.mode["mb"] = 500                                            # nor a single 5xx
        with mock.patch.object(ma.urllib.request, "urlopen", lambda req, timeout=None: (_ for _ in ()).throw(
                urllib.error.HTTPError(req.full_url, 500, "oops", {}, None))):
            with self.assertRaises(ma.ProviderUnavailable) as e:
                self.http.get_text("https://musicbrainz.org/ws/2/x/once5xx", "mb", attempts=1)
        self.assertEqual(e.exception.kind, "outage")
        self.mode["mb"] = "ok"
        self.assertIn("ok", self.get(3))
        # the full ladder giving up still trips it
        self.mode["mb"] = "timeout"
        with self.assertRaises(ma.ProviderUnavailable):
            self.get(4)
        before = self.live["mb"]
        with self.assertRaises(ma.ProviderUnavailable):
            self.get(5)
        self.assertEqual(self.live["mb"], before)

    def test_one_trial_after_the_window_closes_it(self):
        with self.assertRaises(ma.ProviderUnavailable):
            self.get(1)
        self.clock.t += ma.Http.BREAKER_S + 1
        self.mode["mb"] = "ok"
        before = self.live["mb"]
        self.assertIn("ok", self.get(2))                                 # the trial
        self.assertEqual(self.live["mb"] - before, 1)
        self.mode["mb"] = "timeout"                                      # closed: a full ladder again
        before = self.live["mb"]
        with self.assertRaises(ma.ProviderUnavailable):
            self.get(3)
        self.assertEqual(self.live["mb"] - before, 6)

    def test_a_failed_trial_opens_it_again_after_one_try(self):
        with self.assertRaises(ma.ProviderUnavailable):
            self.get(1)
        self.clock.t += ma.Http.BREAKER_S + 1
        before = self.live["mb"]
        with self.assertRaises(ma.ProviderUnavailable):
            self.get(2)                                                  # the trial: one try
        self.assertEqual(self.live["mb"] - before, 1)
        with self.assertRaises(ma.ProviderUnavailable):
            self.get(3)
        self.assertEqual(self.live["mb"] - before, 1)                    # open again

    def test_a_trial_that_is_told_to_slow_down_closes_it(self):
        with self.assertRaises(ma.ProviderUnavailable):
            self.get(1)
        self.clock.t += ma.Http.BREAKER_S + 1
        self.mode["mb"] = 503
        with self.assertRaises(ma.ProviderUnavailable) as e:
            self.get(2)
        self.assertEqual(e.exception.kind, "rate_limited")
        self.mode["mb"] = "ok"
        self.assertIn("ok", self.get(3))

    def test_while_one_thread_makes_the_trial_the_others_are_skipped(self):
        # (threads sharing one Http share its breaker; the cache connection is made shareable)
        self.http = ma.Http(sqlite3.connect(":memory:", check_same_thread=False), dict(mc.DEFAULT_CFG), "t")
        with self.assertRaises(ma.ProviderUnavailable):
            self.get(1)
        self.clock.t += ma.Http.BREAKER_S + 1
        entered, release = threading.Event(), threading.Event()

        def slow_open(req, timeout=None):
            self.live["mb"] += 1
            entered.set()
            release.wait(5)
            return R({"ok": 1})
        results = []
        with mock.patch.object(ma.urllib.request, "urlopen", slow_open):
            th = threading.Thread(target=lambda: results.append(
                self.http.get_text("https://musicbrainz.org/ws/2/x/trial", "mb")))
            th.start()
            self.assertTrue(entered.wait(5))
            # (another thread's request, during the trial: skipped, never a second live request)
            with self.assertRaises(ma.ProviderUnavailable):
                ma.Http.get_text(self.http, "https://musicbrainz.org/ws/2/x/other", "mb")
            release.set()
            th.join(5)
        self.assertEqual(self.live["mb"], 6 + 1)
        self.assertTrue(results)


class OutageRun(te.Base):
    """The audit's outage.py: MusicBrainz times out for a run of two albums with rip logs,
    Discogs answers. Album 2 makes no live MusicBrainz request (the breaker is open), and
    both albums are errors retried later, never a no match."""

    def test_two_albums_during_a_musicbrainz_outage(self):
        dg = [te.dg_release(900 + i, "Band X", "Album X", catno=f"CAT-{i}") for i in range(3)]
        fake = te.FakeHttp(dg=dg)
        clock, live = Clock(), collections.Counter()

        def fake_open(req, timeout=None):
            url = req.full_url
            host = url.split("/")[2]
            live[host] += 1
            if "musicbrainz" in host:
                clock.t += timeout
                raise urllib.error.URLError(TimeoutError("timed out"))
            clock.t += 0.3
            ans = fake.get_json(url, "discogs") if "discogs" in host else None
            if ans is None:
                raise urllib.error.HTTPError(url, 404, "not found", {}, None)
            return R(ans)
        http = ma.Http(sqlite3.connect(":memory:"), dict(mc.DEFAULT_CFG), "t")
        props, mb_live = [], []
        with mock.patch.object(ma.urllib.request, "urlopen", fake_open), \
                mock.patch.object(ma.time, "sleep", clock.sleep), mock.patch.object(ma.time, "time", clock.time), \
                mock.patch("sys.stderr"):
            for k in range(2):
                d = self.album("Band X", "Album X", folder=f"200{k} - Album X")
                (d / "rip.log").write_text(te.EAC_LOG)
                eng = self.engine(http)
                http.cfg = eng.cfg
                before = live["musicbrainz.org"]
                props.append(eng.identify(eng.albums_in(d)[0]))
                mb_live.append(live["musicbrainz.org"] - before)
        self.assertGreater(mb_live[0], 0)
        self.assertEqual(mb_live[1], 0)
        for p in props:
            self.assertEqual(p.status, "unresolved", p.reason)
            self.assertEqual(p.provider_errors.get("musicbrainz"), "outage")
            self.assertNotIn("no_candidate", p.reason_codes)


if __name__ == "__main__":
    unittest.main(argv=[sys.argv[0]], verbosity=2)
