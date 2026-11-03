# Tests for the optional web search (match_albums.web_search and friends), the junk-code
# filter, the other spellings of a code asked of MusicBrainz and Discogs, the monthly caps,
# and what `doctor` says about it. Every answer is stubbed: no network, no real config.
import os as _os, tempfile as _tempfile  # noqa: E402
_os.environ["XDG_CONFIG_HOME"] = _tempfile.mkdtemp(prefix="tm-config-")
_os.environ["XDG_STATE_HOME"] = _tempfile.mkdtemp(prefix="tm-state-")

import io
import json
import re
import shutil
import sqlite3
import sys
import unittest
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
import credentials  # noqa: E402
import doctor  # noqa: E402
import exclusions as ex  # noqa: E402
import match_albums as ma  # noqa: E402
import match_core as mc  # noqa: E402
import profiles as pf  # noqa: E402

LINK = "https://www.discogs.com/release/22314946-Exhorder-Slaughter-In-The-Vatican-The-Law"
ENV = ("TAGMENDER_MOJEEK_KEY", "TAGMENDER_MARGINALIA_KEY", "TAVILY_API_KEY",
       "TAGMENDER_WEB_SEARCH")


class Stubbed(unittest.TestCase):
    """urllib's urlopen replaced: each request is recorded, the engines answer from `serve`."""

    def setUp(self):
        self.env = {k: _os.environ.pop(k, None) for k in ENV}
        self.saved = ma.urllib.request.urlopen
        self.asked = []
        self.down = set()
        ma.urllib.request.urlopen = self.fake
        for f in ex.CONFIG_DIR.glob("*"):
            if f.is_file():
                f.unlink()
        state = ex.STATE_DIR / "state.db"
        if state.exists():
            state.unlink()

    def tearDown(self):
        ma.urllib.request.urlopen = self.saved
        for k, v in self.env.items():
            if v is not None:
                _os.environ[k] = v
            else:
                _os.environ.pop(k, None)

    def answer(self, url):
        if "mojeek.com" in url:
            return {"response": {"status": "OK", "results": [{"url": LINK, "title": "Exhorder"}]}}
        if "marginalia" in url:
            return {"license": "CC-BY-NC-SA 4.0", "results": [{"url": LINK, "title": "Exhorder"}]}
        if "api.tavily.com" in url:
            return {"results": [{"url": LINK, "content": "QDISS0201CDD"}]}
        return None

    def fake(self, req, timeout=None):
        url = req.full_url if hasattr(req, "full_url") else req
        self.asked.append((url, dict(req.header_items()) if hasattr(req, "header_items") else {},
                           getattr(req, "data", None)))
        host = urllib.parse.urlsplit(url).hostname or ""
        if any(d in host for d in self.down):
            raise urllib.error.URLError("refused")
        body = self.answer(url)
        if body is None:
            raise urllib.error.HTTPError(url, 404, "not found", {}, None)
        return io.BytesIO(json.dumps(body).encode())

    def http(self, engine="none", cap=None):
        return ma.Http(sqlite3.connect(":memory:"), {"mb_delay": 0, "discogs_delay": 0,
                                                     "web_search": {"engine": engine, "monthly_cap": cap}}, "")


class DefaultIsNoWebSearch(Stubbed):
    """User decision 2026-10-31: the app works for anyone with free services; no AI search
    engine by default, and no DuckDuckGo scrape (it answered its CAPTCHA page 480 of 480)."""

    def test_nothing_is_asked_of_the_web_by_default(self):
        _os.environ["TAVILY_API_KEY"] = "tvly-secret"              # keys left over: not used
        _os.environ["TAGMENDER_MOJEEK_KEY"] = "mjk-k"
        h = self.http()
        self.assertEqual(ma.web_search(h, '"QDISS0201CDD" Exhorder'), ("", ""))
        self.assertEqual(ma.web_release_links(h, ["QDISS0201CDD"], "Exhorder"), ([], []))
        self.assertEqual(self.asked, [])

    def test_the_default_profile_chooses_no_engine(self):
        cfg = pf.default().matcher_cfg({})
        self.assertEqual(cfg["web_search"], {"engines": [], "monthly_cap": None})
        self.assertEqual(ma.web_engine(cfg)[0], "none")
        self.assertEqual(ma.web_engines(cfg), [])

    def test_duckduckgo_is_never_asked(self):
        # every engine setting, through web_release_links and investigate: no DuckDuckGo request
        _os.environ.update({"TAGMENDER_MOJEEK_KEY": "k", "TAVILY_API_KEY": "tvly-k"})
        for engine in ("none",) + tuple(ma.WEB_ENGINES):
            self.answer = lambda url: {}                             # nothing found anywhere
            h = self.http(engine)
            ma.web_release_links(h, ["QDISS0201CDD"], "Exhorder")
            run_investigation(h, "MCPS. QDISSO201CDD\nSPV 91978", web=engine)
        hosts = {urllib.parse.urlsplit(u).hostname or "" for u, _h, _d in self.asked}
        self.assertTrue(hosts)
        self.assertFalse([h for h in hosts if "duckduckgo" in h], hosts)


class Engines(Stubbed):
    def test_mojeek_from_its_0600_file(self):
        credentials.store("mojeek", "mjk-k")
        self.assertEqual(ma.web_search(self.http("mojeek"), "x")[1], "mojeek")

    def test_a_key_file_others_can_read_is_refused_with_a_note(self):
        p = credentials.store("mojeek", "mjk-k")
        p.chmod(0o644)
        notes = []
        self.assertEqual(ma.web_search(self.http("mojeek"), "x", notes=notes), ("", ""))
        self.assertIn("chmod 600", " ".join(notes))
        self.assertEqual(self.asked, [])

    def test_mojeek_key_never_stored(self):
        _os.environ["TAGMENDER_MOJEEK_KEY"] = "mjk-secret"
        h = self.http("mojeek")
        self.assertEqual(ma.web_release_links(h, ["QDISS0201CDD"], "Exhorder"), (["22314946"], []))
        url = self.asked[0][0]
        self.assertTrue(url.startswith("https://www.mojeek.com/search?"), url)
        qs = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
        self.assertEqual((qs["fmt"], qs["api_key"]), (["json"], ["mjk-secret"]))
        stored = " ".join(u + b for u, b in h.db.execute("SELECT url, body FROM http_cache"))
        self.assertNotIn("mjk-secret", stored)
        self.asked.clear()                                           # cached under the key-less URL
        self.assertEqual(ma.web_release_links(h, ["QDISS0201CDD"], "Exhorder"), (["22314946"], []))
        self.assertEqual(self.asked, [])

    def test_mojeek_without_a_key_is_off_with_one_note(self):
        notes = []
        h = self.http("mojeek")
        ma.web_search(h, "x", notes=notes)
        ma.web_search(h, "y", notes=notes)
        self.assertEqual(len(notes), 1)
        self.assertIn("no Mojeek API key", notes[0])
        self.assertEqual(self.asked, [])

    def test_marginalia_public_key_in_the_header(self):
        h = self.http("marginalia")
        self.assertEqual(ma.web_release_links(h, ["QDISS0201CDD"], "Exhorder"), (["22314946"], []))
        url, headers, _ = self.asked[0]
        self.assertTrue(url.startswith("https://api2.marginalia-search.com/search?query="), url)
        self.assertEqual(headers.get("Api-key"), "public")

    def test_marginalia_own_key(self):
        _os.environ["TAGMENDER_MARGINALIA_KEY"] = "mrg-secret"
        h = self.http("marginalia")
        ma.web_search(h, "x")
        self.assertEqual(self.asked[0][1].get("Api-key"), "mrg-secret")
        stored = " ".join(u + b for u, b in h.db.execute("SELECT url, body FROM http_cache"))
        self.assertNotIn("mrg-secret", stored)

    def test_tavily_only_when_chosen_and_the_key_is_never_stored(self):
        _os.environ["TAVILY_API_KEY"] = "https://mcp.tavily.com/mcp/?tavilyApiKey=tvly-secret"   # pasted whole
        h = self.http("tavily")
        self.assertEqual(ma.web_release_links(h, ["QDISS0201CDD"], "Exhorder"), (["22314946"], []))
        url, headers, data = self.asked[0]
        self.assertIn("api.tavily.com", url)
        self.assertEqual(headers.get("Authorization"), "Bearer tvly-secret")
        self.assertNotIn(b"tvly-secret", data)
        stored = " ".join(u + b for u, b in h.db.execute("SELECT url, body FROM http_cache"))
        self.assertNotIn("tvly-secret", stored)

    def test_an_engine_that_is_down_gives_nothing_and_no_error(self):
        _os.environ["TAGMENDER_MOJEEK_KEY"] = "mjk-k"
        self.down.add("mojeek.com")
        h = self.http("mojeek")
        self.assertEqual(ma.web_release_links(h, ["QDISS0201CDD", "5013929020160"], "Exhorder"), ([], []))

    def test_standalone_run_takes_the_environment(self):
        _os.environ["TAGMENDER_WEB_SEARCH"] = "marginalia"
        self.assertEqual(ma.web_engine({})[0], "marginalia")
        self.assertEqual(ma.web_engine({"web_search": {"engine": "none"}})[0], "none")   # a profile wins


class Caps(Stubbed):
    def test_the_monthly_cap_stops_the_search_with_one_note(self):
        _os.environ["TAGMENDER_MOJEEK_KEY"] = "mjk-k"
        h, notes = self.http("mojeek", cap=2), []
        for word in ("a", "b", "c", "d"):
            ma.web_search(h, word, notes=notes)
        self.assertEqual(len(self.asked), 2)
        self.assertEqual(ma.web_search_count("mojeek"), 2)
        self.assertEqual(len(notes), 1)
        self.assertIn("monthly cap of 2", notes[0])

    def test_a_cached_answer_is_not_counted_and_still_served_at_the_cap(self):
        _os.environ["TAGMENDER_MOJEEK_KEY"] = "mjk-k"
        h = self.http("mojeek", cap=1)
        first = ma.web_search(h, "a")
        self.assertEqual(ma.web_search(h, "a"), first)
        self.assertEqual((len(self.asked), ma.web_search_count("mojeek")), (1, 1))

    def test_an_engine_that_is_down_uses_none_of_the_cap(self):
        # an engine that refuses: refused 3 times, then paused; nothing reached it
        _os.environ["TAGMENDER_MOJEEK_KEY"] = "mjk-k"
        self.down.add("mojeek.com")
        h, notes = self.http("mojeek", cap=2), []
        for word in ("a", "b", "c", "d", "e"):
            self.assertEqual(ma.web_search(h, word, notes=notes), ("", "mojeek"))
        self.assertEqual(len(self.asked), 3)                          # then paused for 30 minutes
        self.assertEqual(ma.web_search_count("mojeek"), 0)
        self.assertEqual(notes, [])                                   # no false "cap is reached"
        self.down.clear()
        h.down.clear()                                                # the pause is over, it is back
        ma.web_search(h, "f")
        self.assertEqual((len(self.asked), ma.web_search_count("mojeek")), (4, 1))

    def test_an_answer_with_an_http_error_counts(self):
        _os.environ["TAGMENDER_MOJEEK_KEY"] = "mjk-k"
        self.answer = lambda url: None                                # 404: the engine answered
        ma.web_search(self.http("mojeek"), "a")
        self.assertEqual(ma.web_search_count("mojeek"), 1)

    def test_a_server_error_that_ends_the_search_counts(self):
        # 503 on every attempt: Http gives up with ProviderUnavailable("... (HTTP 503)"), and
        # the engine was reached, so the search counts
        _os.environ["TAGMENDER_MOJEEK_KEY"] = "mjk-k"

        def fail(req, timeout=None):
            self.asked.append((req.full_url, {}, None))
            raise urllib.error.HTTPError(req.full_url, 503, "busy", {}, None)
        ma.urllib.request.urlopen = fail
        saved = ma.time.sleep
        ma.time.sleep = lambda s: None
        try:
            self.assertEqual(ma.web_search(self.http("mojeek"), "a"), ("", "mojeek"))
        finally:
            ma.time.sleep = saved
        self.assertEqual((len(self.asked), ma.web_search_count("mojeek")), (1, 1))

    def test_a_tavily_answer_that_is_not_json_counts(self):
        _os.environ["TAVILY_API_KEY"] = "tvly-k"

        def broken(req, timeout=None):
            self.asked.append((req.full_url, {}, None))
            return io.BytesIO(b"<html>oops</html>")
        ma.urllib.request.urlopen = broken
        # Tavily's broken answer counts; Marginalia, added after it, is asked next (broken too)
        self.assertEqual(ma.web_search(self.http("tavily"), "a"), ("", "marginalia"))
        self.assertEqual((len(self.asked), ma.web_search_count("tavily"), ma.web_search_count("marginalia")),
                         (2, 1, 1))

    def test_what_reached_the_engine(self):
        HTTPError = urllib.error.HTTPError
        for exc, reached in ((HTTPError("u", 400, "bad", {}, None), True),
                             (urllib.error.URLError("refused"), False),
                             (TimeoutError("timed out"), False),
                             (ma.ProviderUnavailable("mojeek", "outage", "skipped: failed repeatedly"), False),
                             (ma.ProviderUnavailable("mojeek", "outage", "giving up on u (URLError: refused)"), False),
                             (ma.ProviderUnavailable("tavily", "outage", "TimeoutError"), False),
                             (ma.ProviderUnavailable("mojeek", "outage", "giving up on u (HTTP 503)"), True),
                             (ma.ProviderUnavailable("tavily", "outage", "ValueError"), True)):
            self.assertEqual(ma._reached_engine(exc), reached, exc)

    def test_tavily_cached_answer_is_free_and_served_at_the_cap(self):
        _os.environ["TAVILY_API_KEY"] = "tvly-k"
        h = self.http("tavily", cap=1)
        first = ma.web_search(h, "a")
        self.assertEqual(first, (LINK, "tavily"))
        self.assertEqual(ma.web_search(h, "a"), first)                    # from the cache, at the cap
        self.assertEqual((len(self.asked), ma.web_search_count("tavily")), (1, 1))
        keys = [u for (u,) in h.db.execute("SELECT url FROM http_cache")]
        self.assertEqual(keys, [ma.post_cache_key(ma.TAVILY_URL, {"query": "a", "max_results": 10})])
        # a new search: Tavily is at its cap and hands over to Marginalia (added after it)
        self.assertEqual(ma.web_search(h, "b"), (LINK, "marginalia"))
        self.assertEqual(len(self.asked), 2)
        self.assertIn("marginalia", self.asked[1][0])
        self.assertEqual(ma.web_search_count("tavily"), 1)

    def test_a_stale_cached_answer_asked_again_counts(self):
        # --recheck-days: get_text asks again for an answer older than Http.max_age
        _os.environ["TAGMENDER_MOJEEK_KEY"] = "mjk-k"
        h, notes = self.http("mojeek", cap=1), []
        ma.web_search(h, "a")
        h.db.execute("UPDATE http_cache SET ts=1")
        h.max_age = 1
        self.assertEqual(ma.web_search(h, "a", notes=notes), ("", ""))
        self.assertEqual(len(self.asked), 1)
        self.assertIn("monthly cap of 1", " ".join(notes))
        h.max_age = None                                              # no recheck: the old answer is free
        self.assertEqual(ma.web_search(h, "a")[0], LINK)

    def test_the_count_is_kept_per_engine_and_month_in_the_state_db(self):
        _os.environ["TAGMENDER_MOJEEK_KEY"] = "mjk-k"
        ma.web_search(self.http("mojeek"), "a")
        ma.web_search(self.http("marginalia"), "a")
        db = sqlite3.connect(ex.STATE_DIR / "state.db")
        rows = sorted(db.execute("SELECT engine, month, count FROM web_search_usage"))
        self.assertEqual(rows, [("marginalia", ma._month(), 1), ("mojeek", ma._month(), 1)])
        self.assertEqual(ma.web_search_count("mojeek", month="1999-01"), 0)

    def test_default_caps(self):
        for engine in ma.WEB_ENGINES:
            self.assertEqual(ma.web_engine({"web_search": {"engine": engine}})[1], ma.WEB_CAPS[engine])
        self.assertEqual(ma.web_engine({"web_search": {"engine": "mojeek", "monthly_cap": 0}})[1], 0)


class JunkAndOrder(unittest.TestCase):
    def test_junk_codes(self):
        for code in ("0000000000000", "7777777777777", "ABC1", "12 34", "5013929020161", "MCPS", "QDISS"):
            self.assertTrue(ma.junk_code(code), code)
        for code in ("5013929020160", "QDISS0201CDD", "SPV 91978", "016861841126"):
            self.assertFalse(ma.junk_code(code), code)

    def test_at_most_three_strongest_first(self):
        text = ("MADE IN EU / MCPS. QDISSO201CDD\nSPV 91978\nAMG 099\nFO532CD\n5 013929 020160\n"
                "0000000000000\n" + mc.COMPLETED + "4006030021223")
        codes = mc.codes_from_ocr_text(text, limit=10)
        got = ma.strong_codes(text, codes, decoded={"0016861841126", "0000000000000"})
        self.assertEqual(got[:2], ["0016861841126", "5013929020160"])    # decoded, then printed whole
        self.assertEqual(len(got), 3)
        self.assertEqual(mc.norm_code(got[2]), mc.norm_code("QDISS0201CDD"))
        self.assertNotIn("0000000000000", got)
        self.assertNotIn("4006030021223", ma.strong_codes(text, codes, limit=10))   # only completed

    def test_a_catalogue_number_of_digits_only(self):
        # Warner's "9362-47305-2": no barcode, so no check digit is asked of it
        for code in ("9362-47305-2", "4509912342", "9362 47305 2"):
            self.assertFalse(ma.junk_code(code, "catno"), code)
        self.assertFalse(ma.junk_code("4509912342"))                     # not a barcode's length
        for code in ("1111111111", "12-34"):
            self.assertTrue(ma.junk_code(code, "catno"), code)
        self.assertTrue(ma.junk_code("5013929020161", "barcode"))
        self.assertTrue(ma.junk_code("QDISS0201CDD", "barcode"))
        # searched only as printed with its separators; a bare run of digits may be a phone number
        text = "Warner Bros. 9362-47305-2 call 4509912342"
        got = ma.strong_codes(text, {"barcodes": [], "catnos": ["9362-47305-2", "4509912342"]})
        self.assertEqual(got, ["9362-47305-2"])
        self.assertEqual(ma.code_kinds({"barcodes": ["5013929020160"], "catnos": ["9362-47305-2"]}, {"0016861841126"}),
                         {"5013929020160": "barcode", "9362-47305-2": "catno", "0016861841126": "barcode"})

    def test_a_reading_assembled_from_letters_taken_as_digits_is_not_whole(self):
        self.assertFalse(ma._read_whole("FO501CD", "FOSO1 cp"))
        self.assertTrue(ma._read_whole("QDISS0201CDD", "MCPS. QDISSO201CDD"))
        self.assertTrue(ma._read_whole("SPV 91978", "spv-91978"))

    def test_web_search_stops_at_the_first_link(self):
        asked = []

        def search(http, query, cfg=None, notes=None):
            asked.append(query)
            return (LINK if "SPV" in query else ""), "mojeek"
        saved = ma.web_search
        ma.web_search = search
        try:
            cfg = {"web_search": {"engine": "mojeek"}}
            got = ma.web_release_links(None, ["5013929020160", "SPV 91978", "QDISS0201CDD", "AMG 099"],
                                       "Exhorder", cfg=cfg)
        finally:
            ma.web_search = saved
        self.assertEqual(got, (["22314946"], []))
        self.assertEqual(asked, ['"5013929020160" Exhorder', '"SPV 91978" Exhorder'])


class Variants(unittest.TestCase):
    def test_spellings(self):
        self.assertEqual(ma.code_variants("QDISSO201CDD")[:3], ["QDISS0201CDD", "QDISS 0201 CDD", "QDISS-0201-CDD"])
        self.assertEqual(ma.code_variants("SPV 91978"), ["SPV91978", "SPV-91978", "SPV.91978"])
        self.assertEqual(ma.code_variants("QD1SS0201CDD")[0], "QDISS0201CDD")
        self.assertNotIn("SPV 91978", ma.code_variants("SPV 91978"))
        # O/0 and I/1 only in a code that mixes letters and digits: digits stay digits,
        # letters stay letters
        self.assertEqual(ma._words_as_letters("10010"), "10010")
        self.assertEqual(ma._numbers_as_digits("OASIS"), "OASIS")
        got = ma.code_variants("9362-47305-2")
        self.assertEqual(got[0], "9362473052")
        self.assertFalse([f for f in got if re.search("[OI]", f)], got)
        self.assertEqual(ma.barcode_spellings("5013929020160"), ["5 013929 020160"])
        self.assertEqual(ma.barcode_spellings("016861841126"), ["0 16861 84112 6"])

    def serve(self, hit_on):
        asked = []

        class H:
            def get_json(self, url, host):
                asked.append(urllib.parse.unquote(url))
                if hit_on in urllib.parse.unquote(url):
                    return {"releases": [{"id": "mb-1"}]} if host == "mb" else {"results": [{"id": 77}]}
                return {}
        return H(), asked

    def test_mb_catno_and_discogs_catno_until_one_finds_it(self):
        h, asked = self.serve('catno:"QDISS 0201 CDD"')
        dg, mb = ma.variant_release_links(h, ["QDISSO201CDD"], [])
        self.assertEqual((dg, mb), ([], ["mb-1"]))
        self.assertIn('catno:"QDISS0201CDD"', asked[0])
        self.assertIn("catno=QDISS0201CDD", asked[1])
        self.assertTrue(asked[-1].endswith("catno=QDISS 0201 CDD"), asked[-1])   # stopped at the hit
        self.assertFalse(any("q=" in u.split("&")[-1][:2] for u in asked))

    def test_discogs_free_text_when_no_spelling_finds_it(self):
        h, asked = self.serve("&q=QDISSO201CDD")
        dg, mb = ma.variant_release_links(h, ["QDISSO201CDD"], [])
        self.assertEqual((dg, mb), (["77"], []))
        self.assertTrue(asked[-1].endswith("&q=QDISSO201CDD"))

    def test_a_barcode_as_printed_on_discogs(self):
        h, asked = self.serve("barcode=5 013929 020160")
        self.assertEqual(ma.variant_release_links(h, ["5013929020160"], []), (["77"], []))
        self.assertFalse(any("musicbrainz" in u for u in asked))     # mb_search asked its forms already

    def test_a_code_a_candidate_lists_is_not_searched_again(self):
        h, asked = self.serve("nothing")
        known = [{"catnos": {mc.norm_code("QDISS0201CDD")}, "barcodes": {"5013929020160"}}]
        self.assertEqual(ma.variant_release_links(h, ["QDISSO201CDD", "5013929020160"], known), ([], []))
        self.assertEqual(asked, [])

    def test_a_digits_only_catalogue_number_gets_catalogue_spellings(self):
        h, asked = self.serve("nothing")
        ma.variant_release_links(h, ["9362 47305 2"], [], kinds={"9362 47305 2": "catno"})
        self.assertIn('catno:"9362473052"', asked[0])
        self.assertFalse(any("barcode=" in u for u in asked), asked)

    def test_a_database_that_fails_is_a_note_not_an_error(self):
        asked, notes = [], []

        class H:
            def get_json(self, url, host):
                asked.append((host, urllib.parse.unquote(url)))
                if host == "mb":
                    raise ma.ProviderUnavailable("mb", "rate_limited", "giving up (HTTP 503)")
                return {"results": [{"id": 77}]} if "q=QDISSO201CDD" in urllib.parse.unquote(url) else {}
        got = ma.variant_release_links(H(), ["QDISSO201CDD", "SPV 91978"], [], notes=notes)
        self.assertEqual(got, (["77"], []))                              # Discogs still asked
        self.assertEqual([h for h, _u in asked].count("mb"), 1)          # MusicBrainz no more after it failed
        self.assertEqual(len(notes), 1)
        self.assertIn("MusicBrainz search of other spellings of the codes failed", notes[0])

        class Rejected:
            def get_json(self, url, host):
                raise ma.AuthError("Discogs rejected the token (HTTP 401)")
        with self.assertRaises(ma.AuthError):
            ma.variant_release_links(Rejected(), ["QDISSO201CDD"], [], use_mb=False)

    def test_answers_are_cached(self):
        calls = []

        def fake(req, timeout=None):
            calls.append(req.full_url)
            return io.BytesIO(b'{"releases": [], "results": []}')
        saved = ma.urllib.request.urlopen
        ma.urllib.request.urlopen = fake
        try:
            h = ma.Http(sqlite3.connect(":memory:"), {"mb_delay": 0, "discogs_delay": 0}, "t")
            ma.variant_release_links(h, ["QDISSO201CDD"], [])
            n = len(calls)
            ma.variant_release_links(h, ["QDISSO201CDD"], [])
        finally:
            ma.urllib.request.urlopen = saved
        self.assertGreater(n, 0)
        self.assertEqual(len(calls), n)


def run_investigation(http, ocr, web="mojeek", carried=(), decoded=(), title="Slaughter In The Vatican",
                      carried_catnos=()):
    """match_albums.investigate on one blank scan whose deep OCR reads `ocr`; the databases'
    own searches find nothing; a scored MusicBrainz candidate lists `carried`. Returns notes."""
    tmp = Path(_tempfile.mkdtemp())
    from PIL import Image
    Image.new("L", (50, 50), 255).save(tmp / "Digi.jpg")
    saved = (ma.ocr_deep, ma.ocr_disc_rings, ma.ocr_barcodes, ma.mb_search, ma.discogs_search, ma.discogs_candidate)
    ma.ocr_deep = lambda paths: ocr
    ma.ocr_disc_rings = lambda paths: ""
    ma.ocr_barcodes = lambda paths: set(decoded)
    ma.mb_search = lambda http, album, cfg: []
    ma.discogs_search = lambda http, album, cfg, dm: []
    ma.discogs_candidate = lambda det, dm: dict(cand(id=str(det.get("id")), source="discogs"))
    try:
        a = {"artist": "Exhorder", "title": title, "year": 1990, "discs": 1, "barcodes": set(), "catnos": set(),
             "tracks": [{"disc": 1, "num": 1, "title": "T", "secs": 200.0}]}
        c = cand(catnos={mc.norm_code(x) for x in carried if not x.isdigit()} | set(carried_catnos),
                 barcodes={x for x in carried if x.isdigit()})
        scored = [mc.score_candidate(a, c, CFG)]
        mb = dict(mc.classify(scored, CFG), scored=scored, source="mb")
        notes = []
        ma.investigate(http, tmp, a, dict(CFG, web_search={"engine": web}), object(), mb, None, mb, "", {},
                       notes, True, True, None)
        return notes
    finally:
        (ma.ocr_deep, ma.ocr_disc_rings, ma.ocr_barcodes, ma.mb_search, ma.discogs_search,
         ma.discogs_candidate) = saved
        shutil.rmtree(tmp, ignore_errors=True)


CFG = dict(mc.DEFAULT_CFG, preferred_countries=[])


def cand(**kw):
    c = {"source": "mb", "id": "x1", "url": "u", "artist": "Exhorder", "title": "Slaughter In The Vatican",
         "year": 1990, "country": "US", "label": "L", "catnos": set(), "barcodes": set(), "disc_count": 1,
         "tracks": [{"disc": 1, "title": "T", "secs": 200}]}
    c.update(kw)
    return c


class Investigate(unittest.TestCase):
    """The order investigate keeps: the strongest codes (at most 3, never junk, none a
    candidate lists), their other spellings on MusicBrainz and Discogs, the web only when
    those found nothing, the album's title only when no code could be read."""

    def setUp(self):
        self.saved_env = _os.environ.get("TAGMENDER_MOJEEK_KEY")
        _os.environ["TAGMENDER_MOJEEK_KEY"] = "mjk-k"
        self.web, self.db = [], []
        self.mb_hit = None
        self.db_raises = None
        outer = self

        class H:
            db = None

            def get_text(self, url, host, key=None, headers=None):
                outer.web.append(urllib.parse.unquote(url))
                return json.dumps({"results": []})

            def get_json(self, url, host):
                u = urllib.parse.unquote(url)
                outer.db.append(u)
                if outer.db_raises and host == outer.db_raises:
                    raise ma.ProviderUnavailable(host, "outage", "giving up (HTTP 503)")
                if outer.mb_hit and outer.mb_hit in u:
                    return {"releases": [{"id": "mb-1"}]}
                return {}
        self.h = H()

    def tearDown(self):
        if self.saved_env is None:
            _os.environ.pop("TAGMENDER_MOJEEK_KEY", None)
        else:
            _os.environ["TAGMENDER_MOJEEK_KEY"] = self.saved_env

    def test_no_web_search_once_another_spelling_found_the_release(self):
        self.mb_hit = 'catno:"QDISS 0201 CDD"'
        run_investigation(self.h, "MADE IN EU / MCPS. QDISSO201CDD")
        self.assertTrue(any(self.mb_hit in u for u in self.db), self.db)
        self.assertEqual(self.web, [])

    def test_junk_never_reaches_the_web_and_three_at_most(self):
        text = ("0000000000000\nAB12\n" + mc.COMPLETED + "4006030021223\nQDISSO201CDD\nSPV 91978\n"
                "AMG 0991\nFO532CD\n5 013929 020160")
        run_investigation(self.h, text)
        self.assertTrue(self.web)
        self.assertLessEqual(len(self.web), 3)
        for junk in ("0000000000000", "AB12", "4006030021223"):
            self.assertFalse([u for u in self.web + self.db if junk in u], junk)
        self.assertIn("5013929020160", self.web[0])                    # a barcode read whole first

    def test_no_title_search_when_a_strong_code_found_nothing(self):
        run_investigation(self.h, "MCPS. QDISSO201CDD")
        self.assertTrue(self.web)
        self.assertFalse([u for u in self.web if "Slaughter" in u], self.web)

    def test_the_title_when_no_code_could_be_read(self):
        run_investigation(self.h, "no code here")
        self.assertEqual(len(self.web), 1)
        self.assertIn('"Slaughter In The Vatican" "Exhorder"', self.web[0])

    def test_a_code_a_candidate_lists_is_not_searched(self):
        run_investigation(self.h, "MCPS. QDISSO201CDD\nSPV 91978", carried=("QDISS0201CDD",))
        self.assertTrue(self.web)
        self.assertFalse([u for u in self.web + self.db if "QDISS" in u.upper()], self.web + self.db)

    def test_a_digits_only_catalogue_number_is_searched_as_one(self):
        # what codes_from_ocr_text read it as decides, not its length: twelve digits spaced
        # look like a barcode to code_kind, but this one was read as a catalogue number, so
        # catalogue spellings, never barcode=
        self.assertEqual(ma.code_kind("9362 47305 212"), "barcode")
        saved = mc.codes_from_ocr_text
        mc.codes_from_ocr_text = lambda text, limit=5: {"barcodes": [], "catnos": ["9362 47305 212"]}
        try:
            run_investigation(self.h, "Warner Bros. 9362 47305 212")
        finally:
            mc.codes_from_ocr_text = saved
        self.assertTrue([u for u in self.db if 'catno:"936247305212"' in u], self.db)
        self.assertFalse([u for u in self.db if "barcode=" in u], self.db)
        # and a candidate listing it as a catalogue number carries it: not searched at all
        self.db.clear()
        self.web.clear()
        mc.codes_from_ocr_text = lambda text, limit=5: {"barcodes": [], "catnos": ["9362 47305 212"]}
        try:
            run_investigation(self.h, "Warner Bros. 9362 47305 212", carried_catnos=("9362 47305 212",))
        finally:
            mc.codes_from_ocr_text = saved
        self.assertFalse([u for u in self.db + self.web if "47305" in u.replace(" ", "")], self.db + self.web)

    def test_a_failed_spelling_search_keeps_what_was_found(self):
        self.db_raises = "mb"
        notes = run_investigation(self.h, "MCPS. QDISSO201CDD")
        self.assertIn("MusicBrainz search of other spellings of the codes failed", " ".join(notes))
        self.assertTrue(self.web)                                       # the investigation went on


class ProfileSetting(unittest.TestCase):
    def test_validate(self):
        self.assertEqual(pf.validate({"name": "x", "providers": {"web": {"search": {"engine": "marginalia"}}}}), [])
        self.assertEqual(pf.validate({"name": "x", "providers": {"web": {"search": "mojeek"}}}), [])
        bad = pf.validate({"name": "x", "providers": {"web": {"search": {"engine": "duckduckgo"}}}})
        self.assertTrue(any("providers.web.search.engine" in p for p in bad), bad)
        bad = pf.validate({"name": "x", "providers": {"web": {"search": {"engine": "mojeek", "monthly_cap": -1}}}})
        self.assertTrue(any("monthly_cap" in p for p in bad), bad)

    def test_into_the_matcher_cfg(self):
        pf.save(pf.from_dict({"name": "Web", "providers": {"web": {"search": {"engine": "marginalia",
                                                                             "monthly_cap": 50}}}}))
        # the older single "engine" reads as a one-item chain
        self.assertEqual(pf.load("Web").matcher_cfg({})["web_search"], {"engines": ["marginalia"], "monthly_cap": 50})
        pf.save(pf.from_dict({"name": "Off", "providers": {"web": {"enabled": False, "search": "mojeek"}}}))
        self.assertEqual(pf.load("Off").web_search()["engines"], [])          # the web provider is off
        for n in ("Web", "Off"):
            pf.path_of(n).unlink()


class Doctor(Stubbed):
    def tearDown(self):
        super().tearDown()
        for n in pf.names():
            pf.path_of(n).unlink()

    def checks(self):
        return {c.name: c for c in doctor.web_search_checks()}

    def test_off_everywhere(self):
        got = self.checks()
        self.assertEqual(got["web search"].status, "ok")
        self.assertIn("off in every profile", got["web search"].detail)

    def test_a_key_file_others_can_read_is_reported(self):
        credentials.store("mojeek", "mjk-secret")
        credentials.path_of("mojeek").chmod(0o664)
        c = self.checks()["web search mojeek"]
        self.assertEqual(c.status, "warn")
        self.assertIn("chmod 600", c.fix)
        self.assertIn("no profile uses it", c.detail)
        self.assertEqual(self.asked, [])                            # doctor sends no search
        self.assertNotIn("mjk-secret", json.dumps(c.__dict__))

    def test_engine_in_use_count_and_cap(self):
        _os.environ["TAGMENDER_MOJEEK_KEY"] = "mjk-secret"
        pf.save(pf.from_dict({"name": "Web", "providers": {"web": {"search": {"engine": "mojeek", "monthly_cap": 1}}}}))
        ma.web_search(self.http("mojeek", cap=1), "x")
        c = self.checks()["web search mojeek"]
        self.assertIn("used by Web", c.detail)
        self.assertIn("1 of 1 searches this month", c.detail)
        self.assertEqual(c.status, "warn")                          # the cap is reached
        self.assertNotIn("mjk-secret", json.dumps(c.__dict__))
        self.assertNotIn("web search", self.checks())   # not "off in every profile"


class Chain(Stubbed):
    """User decision 2026-10-31: providers.web.search.engines, an ordered chain; each engine
    hands over to the next when it is not set up, fails, is at its cap or links no release,
    and Tavily is never the only engine (Marginalia is added after it)."""

    def http_chain(self, engines, cap=None):
        return ma.Http(sqlite3.connect(":memory:"), {"mb_delay": 0, "discogs_delay": 0,
                                                     "web_search": {"engines": engines, "monthly_cap": cap}}, "")

    def hosts(self):
        return [urllib.parse.urlsplit(u).hostname for u, _h, _d in self.asked]

    def test_tavily_that_fails_hands_over(self):
        _os.environ.update({"TAVILY_API_KEY": "tvly-k", "TAGMENDER_MOJEEK_KEY": "mjk"})
        self.down.add("api.tavily.com")
        h = self.http_chain(["tavily", "mojeek"])
        self.assertEqual(ma.web_search(h, "x"), (LINK, "mojeek"))
        self.assertEqual(self.hosts(), ["api.tavily.com", "www.mojeek.com"])
        self.assertEqual((ma.web_search_count("tavily"), ma.web_search_count("mojeek")), (0, 1))
        self.assertEqual(ma.web_engines(h.cfg), [("tavily", 1000), ("mojeek", 1000)])   # no marginalia added

    def test_tavily_with_an_http_error_hands_over_and_counts(self):
        _os.environ.update({"TAVILY_API_KEY": "tvly-k", "TAGMENDER_MOJEEK_KEY": "mjk"})
        answer = self.answer
        self.answer = lambda url: None if "tavily" in url else answer(url)
        h = self.http_chain(["tavily", "mojeek"])
        self.assertEqual(ma.web_search(h, "x"), (LINK, "mojeek"))
        self.assertEqual((ma.web_search_count("tavily"), ma.web_search_count("mojeek")), (1, 1))

    def test_tavily_at_its_cap_hands_over(self):
        _os.environ.update({"TAVILY_API_KEY": "tvly-k", "TAGMENDER_MOJEEK_KEY": "mjk"})
        h, notes = self.http_chain(["tavily", "mojeek"], cap={"tavily": 0}), []
        self.assertEqual(ma.web_search(h, "x", notes=notes), (LINK, "mojeek"))
        self.assertEqual(self.hosts(), ["www.mojeek.com"])
        self.assertIn("(tavily)", notes[0])
        self.assertIn("monthly cap of 0", notes[0])

    def test_tavily_with_no_release_link_hands_over(self):
        _os.environ.update({"TAVILY_API_KEY": "tvly-k", "TAGMENDER_MOJEEK_KEY": "mjk"})
        answer = self.answer
        other = "https://example.com/page"
        self.answer = lambda url: {"results": [{"url": other}]} if "tavily" in url else answer(url)
        h = self.http_chain(["tavily", "mojeek"])
        self.assertEqual(ma.web_search(h, "x"), (LINK, "mojeek"))
        self.assertEqual(ma.web_release_links(h, ["QDISS0201CDD"], "Exhorder"), (["22314946"], []))
        self.assertEqual((ma.web_search_count("tavily"), ma.web_search_count("mojeek")), (2, 2))

    def test_nothing_linked_anywhere_gives_every_answer_and_the_last_engine(self):
        _os.environ.update({"TAVILY_API_KEY": "tvly-k"})
        self.answer = lambda url: {"results": [{"url": "https://example.com/" + urllib.parse.urlsplit(url).hostname}]}
        text, engine = ma.web_search(self.http_chain(["tavily"]), "x")
        self.assertEqual(engine, "marginalia")
        self.assertIn("api.tavily.com", text)
        self.assertIn("api2.marginalia-search.com", text)

    def test_an_engine_not_set_up_hands_over(self):
        h, notes = self.http_chain(["mojeek", "marginalia"]), []
        self.assertEqual(ma.web_search(h, "x", notes=notes), (LINK, "marginalia"))
        self.assertIn("no Mojeek API key", notes[0])
        self.assertEqual(self.hosts(), ["api2.marginalia-search.com"])

    def test_the_first_that_links_ends_the_chain(self):
        _os.environ.update({"TAGMENDER_MOJEEK_KEY": "mjk", "TAVILY_API_KEY": "tvly-k"})
        h = self.http_chain(["mojeek", "tavily"])
        self.assertEqual(ma.web_search(h, "x"), (LINK, "mojeek"))
        self.assertEqual(self.hosts(), ["www.mojeek.com"])

    def test_tavily_alone_gets_marginalia(self):
        _os.environ["TAVILY_API_KEY"] = "tvly-k"
        for ws in ({"engines": ["tavily"]}, {"engine": "tavily"}, "tavily"):
            self.assertEqual([e for e, _c in ma.web_engines({"web_search": ws})], ["tavily", "marginalia"], ws)
        self.down.add("api.tavily.com")
        h = self.http_chain(["tavily"])
        self.assertEqual(ma.web_search(h, "x"), (LINK, "marginalia"))
        self.assertEqual(self.asked[-1][1].get("Api-key"), "public")     # the shared key
        _os.environ["TAGMENDER_MARGINALIA_KEY"] = "mrg-own"
        ma.web_search(h, "y")
        self.assertEqual(self.asked[-1][1].get("Api-key"), "mrg-own")
        # a free engine in the chain already: nothing added
        self.assertEqual([e for e, _c in ma.web_engines({"web_search": {"engines": ["tavily", "mojeek"]}})],
                         ["tavily", "mojeek"])
        self.assertEqual([e for e, _c in ma.web_engines({"web_search": {"engines": ["marginalia", "tavily"]}})],
                         ["marginalia", "tavily"])

    def test_the_older_engine_string_still_works(self):
        _os.environ["TAGMENDER_MOJEEK_KEY"] = "mjk"
        for ws in ({"engine": "mojeek"}, "mojeek", {"engines": [], "engine": "mojeek", "monthly_cap": None}):
            self.assertEqual(ma.web_engines({"web_search": ws}), [("mojeek", 1000)], ws)
        h = self.http("mojeek")                                     # {"engine": "mojeek", ...}
        self.assertEqual(ma.web_search(h, "x"), (LINK, "mojeek"))
        self.assertEqual(ma.web_engine(h.cfg), ("mojeek", 1000))
        self.assertEqual(ma.web_engines({"web_search": {"engine": "none"}}), [])
        self.assertEqual(ma.web_engines({"web_search": {"engine": "duckduckgo"}}), [])

    def test_the_default_makes_no_request(self):
        _os.environ.update({"TAVILY_API_KEY": "tvly-k", "TAGMENDER_MOJEEK_KEY": "mjk", "TAGMENDER_MARGINALIA_KEY": "mrg"})
        cfg = dict(pf.default().matcher_cfg({}), mb_delay=0, discogs_delay=0)
        h = ma.Http(sqlite3.connect(":memory:"), cfg, "")
        self.assertEqual(ma.web_engines(cfg), [])
        self.assertEqual(ma.web_search(h, "x"), ("", ""))
        self.assertEqual(ma.web_release_links(h, ["QDISS0201CDD"], "Exhorder"), ([], []))
        self.assertEqual(self.asked, [])
        self.assertEqual(ma.web_search_answers(), {})

    def test_caps_per_engine(self):
        self.assertEqual(ma.web_engines({"web_search": {"engines": ["marginalia", "mojeek"], "monthly_cap": 7}}),
                         [("marginalia", 7), ("mojeek", 7)])
        self.assertEqual(ma.web_engines({"web_search": {"engines": ["tavily"], "monthly_cap": {"tavily": 20}}}),
                         [("tavily", 20), ("marginalia", 500)])
        # a plain number caps the engines the profile lists; the added Marginalia keeps its 500
        self.assertEqual(ma.web_engines({"web_search": {"engines": ["tavily"], "monthly_cap": 5}}),
                         [("tavily", 5), ("marginalia", 500)])
        self.assertEqual(ma.web_engines({"web_search": {"engines": ["tavily", "marginalia"], "monthly_cap": 5}}),
                         [("tavily", 5), ("marginalia", 5)])
        self.assertEqual(ma.web_engines({"web_search": {"engines": ["tavily"], "monthly_cap": {"marginalia": 9}}}),
                         [("tavily", 1000), ("marginalia", 9)])
        self.assertEqual(ma.web_engines({"web_search": {"engines": ["mojeek", "mojeek"]}}), [("mojeek", 1000)])
        _os.environ["TAGMENDER_MOJEEK_KEY"] = "mjk"
        h = self.http_chain(["mojeek", "marginalia"], cap={"mojeek": 1, "marginalia": 1})
        for word in ("a", "b", "c"):
            ma.web_search(h, word)
        self.assertEqual(self.hosts(), ["www.mojeek.com", "api2.marginalia-search.com"])
        self.assertEqual((ma.web_search_count("mojeek"), ma.web_search_count("marginalia")), (1, 1))

    def test_the_environment_takes_a_list(self):
        _os.environ["TAGMENDER_WEB_SEARCH"] = "mojeek, tavily"
        self.assertEqual([e for e, _c in ma.web_engines({})], ["mojeek", "tavily"])
        _os.environ["TAGMENDER_WEB_SEARCH"] = "tavily"
        self.assertEqual([e for e, _c in ma.web_engines({})], ["tavily", "marginalia"])
        _os.environ["TAGMENDER_WEB_SEARCH"] = "mojeek,mojeek"             # each engine asked once
        self.assertEqual([e for e, _c in ma.web_engines({})], ["mojeek"])

    def test_the_last_answer_is_remembered(self):
        _os.environ["TAGMENDER_MOJEEK_KEY"] = "mjk"
        self.down.add("mojeek.com")
        ma.web_search(self.http_chain(["mojeek", "marginalia"]), "x")
        got = ma.web_search_answers()
        self.assertEqual(set(got), {"marginalia"})                      # mojeek never answered
        self.assertTrue(got["marginalia"][1])                           # with a release link

    def test_an_answer_without_a_release_link_is_remembered_so(self):
        self.answer = lambda url: {"license": "x", "results": [{"url": "https://example.com/x"}]}
        ma.web_search(self.http_chain(["marginalia"]), "x")
        self.assertIs(ma.web_search_answers()["marginalia"][1], False)


class ChainProfile(unittest.TestCase):
    def test_validate(self):
        ok = ({"engines": ["tavily", "marginalia"]}, {"engines": []}, {"engine": "mojeek"},
              {"engines": ["mojeek"], "monthly_cap": {"mojeek": 10}}, {"engines": ["mojeek"], "monthly_cap": 10})
        for search in ok:
            self.assertEqual(pf.validate({"name": "x", "providers": {"web": {"search": search}}}), [], search)
        bad = (({"engines": ["duckduckgo"]}, "engines"), ({"engines": "mojeek"}, "engines"),
               ({"engines": ["none"]}, "engines"), ({"monthly_cap": {"google": 5}}, "monthly_cap"),
               ({"monthly_cap": {"mojeek": -1}}, "monthly_cap"), ({"monthly_cap": "lots"}, "monthly_cap"))
        for search, word in bad:
            got = pf.validate({"name": "x", "providers": {"web": {"search": search}}})
            self.assertTrue(any(word in p for p in got), (search, got))

    def test_into_the_matcher_cfg(self):
        p = pf.from_dict({"name": "W", "providers": {"web": {"search": {"engines": ["tavily", "mojeek", "tavily"],
                                                                         "monthly_cap": {"tavily": 5}}}}})
        self.assertEqual(p.web_search(), {"engines": ["tavily", "mojeek"], "monthly_cap": {"tavily": 5}})
        self.assertEqual(ma.web_engines(p.matcher_cfg({})), [("tavily", 5), ("mojeek", 1000)])
        old = pf.from_dict({"name": "Old", "providers": {"web": {"search": {"engine": "marginalia"}}}})
        self.assertEqual(old.web_search()["engines"], ["marginalia"])
        self.assertEqual(pf.minimal(pf.from_dict({"name": "D"})).get("providers", {}).get("web"), None)

    def test_an_engine_no_longer_offered_is_dropped_when_read(self):
        # a profile saved when TagMender still offered this engine: it loads, without it
        gone = pf.RETIRED_WEB_ENGINES[0]
        for search, left in (({"engines": [gone, "mojeek"], "monthly_cap": {gone: 5, "mojeek": 3}}, ["mojeek"]),
                             ({"engine": gone}, []), (gone, [])):
            data = {"name": "Old", "providers": {"web": {"search": search}}}
            self.assertEqual(pf.validate(data), [], search)
            pf.path_of("Old").parent.mkdir(parents=True, exist_ok=True)
            pf.path_of("Old").write_text(json.dumps(data), encoding="utf-8")
            try:
                loaded = pf.load("Old")
            finally:
                pf.path_of("Old").unlink()
            self.assertEqual(loaded.web_search()["engines"], left, search)
            self.assertEqual([e for e, _c in ma.web_engines(loaded.matcher_cfg({}))], left, search)
            self.assertEqual(data["providers"]["web"]["search"], search)       # what was given is left as it was
        self.assertEqual(ma.web_engines({"web_search": {"engines": [gone]}}), [])
        # a key file left from it is not read
        self.assertNotIn(gone, credentials.FILES)


class ChainDoctor(Stubbed):
    def tearDown(self):
        super().tearDown()
        for n in pf.names():
            pf.path_of(n).unlink()

    def test_the_chain_counts_caps_and_last_answer(self):
        _os.environ["TAVILY_API_KEY"] = "tvly-secret"
        pf.save(pf.from_dict({"name": "Web", "providers": {"web": {"search": {"engines": ["tavily"],
                                                                             "monthly_cap": {"tavily": 1}}}}}))
        self.down.add("api.tavily.com")
        h = ma.Http(sqlite3.connect(":memory:"), dict(pf.load("Web").matcher_cfg({}), mb_delay=0), "")
        ma.web_search(h, "x")
        got = {c.name: c for c in doctor.web_search_checks()}
        chain = got["web search chain"].detail
        self.assertIn("Web: tavily 0/1, then marginalia 1/500", chain)
        self.assertIn("marginalia added", chain)
        self.assertIn("last answer: marginalia", chain)
        self.assertIn("(a release link)", chain)
        self.assertIn("used by Web", got["web search marginalia"].detail)
        self.assertIn("last answered", got["web search marginalia"].detail)
        self.assertNotIn("last answered", got["web search tavily"].detail)
        self.assertNotIn("tvly-secret", json.dumps([c.__dict__ for c in got.values()]))

    def _checks(self, name, search, word="x"):
        pf.save(pf.from_dict({"name": name, "providers": {"web": {"search": search}}}))
        h = ma.Http(sqlite3.connect(":memory:"), dict(pf.load(name).matcher_cfg({}), mb_delay=0), "")
        ma.web_search(h, word)
        return {c.name: c for c in doctor.web_search_checks()}

    def test_no_release_link_is_said(self):
        self.answer = lambda url: {"license": "x", "results": [{"url": "https://example.com/x"}]}
        got = self._checks("Plain", {"engines": ["marginalia"]})
        self.assertIn("(no release link)", got["web search chain"].detail)
        self.assertNotIn("(a release link)", got["web search chain"].detail)

    def test_marginalia_listed_is_not_added(self):
        _os.environ["TAVILY_API_KEY"] = "tvly-k"
        for engines in (["tavily", "marginalia"], ["marginalia"]):
            got = self._checks("Listed", {"engines": engines})
            self.assertIn("Listed: ", got["web search chain"].detail, engines)
            self.assertNotIn("marginalia added", got["web search chain"].detail, engines)

    def test_an_engine_at_its_cap_hands_over(self):
        _os.environ["TAVILY_API_KEY"] = "tvly-k"
        got = self._checks("Capped", {"engines": ["tavily"], "monthly_cap": {"tavily": 1}})
        self.assertEqual(ma.web_search_count("tavily"), 1)
        tavily = got["web search tavily"]
        self.assertEqual(tavily.status, "warn")
        self.assertIn("hands over to the next", tavily.fix)
        self.assertEqual(got["web search marginalia"].status, "ok")


if __name__ == "__main__":
    unittest.main(argv=sys.argv[:1])
