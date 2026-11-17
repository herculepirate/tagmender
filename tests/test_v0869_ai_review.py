"""Optional AI (0.8.69): the independent review's findings, each pinned (AI_REVIEW-n), mocked only.

3 a 429 is asked once more at most, a quota error never, and keeps failing -> paused
4 only answers that parse are cached; the cache key holds the provider's address (never the key)
5 every HTTP request takes one of the monthly cap first, atomically (threads, processes)
6 codes must be written whole; a scan code needs where it is printed; catalogue-number searches
  only as another way of writing a known number
7 an AI key makes covers only once AI is allowed and a profile turns it on with covers
8 plain http only to this computer
9 the first-use sheet is made from what the requests really send; explain/ties not listed
10 Anthropic: room for thinking (max_tokens), low effort (dropped if refused), pause_turn
   continued, Sonnet suggested first
11 Anthropic: only the URLs the answer cites count as citations

Run:  python3 tests/test_v0869_ai_review.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import test_v0869_ai_providers as P  # noqa: E402  (its isolated XDG folders, wire and helpers)

import json  # noqa: E402
import threading  # noqa: E402
import time  # noqa: E402
import unittest  # noqa: E402
from unittest import mock  # noqa: E402

import ai_assist  # noqa: E402
import ai_providers as ap  # noqa: E402



class RateLimits(P.Base):
    def test_429_asked_once_more_at_most_then_paused(self):
        cfg = self.setup(openai=P.OPENAI_KEY)
        wire = P.Wire(P.http_error(429, {"Retry-After": "7"}))
        a = ai_assist.Assistant(cfg=cfg)
        with mock.patch.object(ap.urllib.request, "urlopen", wire):
            for n in range(ap.PAUSE_AFTER + 1):
                a.read_notes({}, f"Cat ABC-10{n}")
        self.assertEqual(len(wire.sent), ap.PAUSE_AFTER * ap.ATTEMPTS_429)   # (then left alone)

    def test_quota_error_never_retried_and_pauses(self):
        cfg = self.setup(openai=P.OPENAI_KEY)
        quota = P.urllib.error.HTTPError("https://x", 429, "quota", {}, P.io.BytesIO(
            b'{"error": {"code": "insufficient_quota", "message": "You exceeded your current quota"}}'))
        wire = P.Wire(quota)
        a = ai_assist.Assistant(cfg=cfg)
        with mock.patch.object(ap.urllib.request, "urlopen", wire):
            a.read_notes({}, "Cat ABC-101")
            a.read_notes({}, "Cat ABC-102")
        self.assertEqual(len(wire.sent), 1)
        self.assertTrue(any("quota" in n for n in a.notes), a.notes)

    def test_a_long_retry_after_is_not_sat_through(self):
        cfg = self.setup(openai=P.OPENAI_KEY)
        waits = []
        wire = P.Wire(P.http_error(429, {"Retry-After": "600"}))
        with mock.patch.object(ap, "SLEEP", waits.append), mock.patch.object(ap.urllib.request, "urlopen", wire):
            ai_assist.Assistant(cfg=cfg).read_notes({}, "Cat ABC-101")
        self.assertEqual((len(wire.sent), waits), (1, []))


class Cache(P.Base):
    def test_a_discarded_answer_is_not_cached(self):
        cfg = self.setup(openai=P.OPENAI_KEY)
        good = {"clues": [{"kind": "catno", "value": "ABC-101", "quote": "Cat ABC-101"}]}
        wire = P.Wire(P.openai_chat("garbage"), P.openai_chat(json.dumps(good)))
        with mock.patch.object(ap.urllib.request, "urlopen", wire):
            self.assertEqual(ai_assist.Assistant(cfg=cfg).read_notes({}, "Cat ABC-101"), [])
            again = ai_assist.Assistant(cfg=cfg).read_notes({}, "Cat ABC-101")
        self.assertEqual(len(wire.sent), 2)
        self.assertEqual([c["value"] for c in again], ["ABC-101"])

    def test_cache_key_holds_the_address_never_the_key(self):
        a = ap.cache_key("compatible", "m", "notes", {"x": 1}, url="http://localhost:11434/v1")
        b = ap.cache_key("compatible", "m", "notes", {"x": 1}, url="https://openrouter.ai/api/v1")
        self.assertNotEqual(a, b)
        cfg = self.setup(compatible="sk-compat-0123456789")
        cfg["providers"]["compatible"].update(url="http://localhost:11434/v1", model="m")
        wire = P.Wire(P.openai_chat(json.dumps({"clues": []})))
        with mock.patch.object(ap.urllib.request, "urlopen", wire), \
                mock.patch.object(ap, "cache_key", wraps=ap.cache_key) as ck:
            ai_assist.Assistant(cfg=cfg).read_notes({}, "Cat ABC-101")
        self.assertEqual(ck.call_args.kwargs["url"], "http://localhost:11434/v1")
        self.assertNotIn("sk-compat", json.dumps(ck.call_args.args, default=str))


class Cap(P.Base):
    def test_every_http_request_counts(self):
        cfg = self.setup(anthropic=P.CLAUDE_KEY)
        url = "https://musicbrainz.org/release/0a1b2c3d-1111-2222-3333-444455556666"
        ok = {"content": [{"type": "text", "text": json.dumps({"releases": [{"url": url, "why": "x"}]}),
                           "citations": [{"type": "web_search_result_location", "url": url}]}],
              "stop_reason": "end_turn", "usage": {"input_tokens": 1, "output_tokens": 1}}
        with mock.patch.object(ap.urllib.request, "urlopen", P.Wire(P.http_error(400), ok)):
            ai_assist.Assistant(cfg=cfg).research({"title": "T"})
        self.assertEqual(ap.usage("anthropic")["requests"], 2)          # (the fallback request too)

    def test_cap_reserved_atomically_across_threads(self):
        cfg = self.setup(openai=P.OPENAI_KEY)
        cfg["monthly_cap"] = {"openai": 1}
        sent, gate = [], threading.Barrier(4)

        def slow(req, timeout=None):
            sent.append(req.full_url)
            time.sleep(0.2)
            return P.Resp(json.dumps(P.openai_chat(json.dumps({"clues": []}))).encode())

        def one(n):
            gate.wait()
            ai_assist.Assistant(cfg=cfg).read_notes({}, f"Cat ABC-10{n}")
        with mock.patch.object(ap.urllib.request, "urlopen", slow):
            ts = [threading.Thread(target=one, args=(n,)) for n in range(4)]
            for t in ts:
                t.start()
            for t in ts:
                t.join()
        self.assertEqual(len(sent), 1)
        self.assertEqual(ap.usage("openai")["requests"], 1)


class Written(P.Base):
    def test_codes_written_whole(self):
        w = ai_assist.written
        self.assertTrue(w("SICP-1015", "品番 ＳＩＣＰ－１０１５"))
        self.assertTrue(w("SICP-1015", "SICP 1015"))
        self.assertFalse(w("SICP-1015", "SICP-10157"))                 # (inside a longer number)
        self.assertFalse(w("SICP-1015", "XSICP-1015"))
        self.assertFalse(w("AB12", "grab 12"))                          # (joined from a word's end)

    def test_notes_code_inside_a_longer_one_is_dropped(self):
        cfg = self.setup(openai=P.OPENAI_KEY)
        ans = {"clues": [{"kind": "catno", "value": "SICP-1015", "quote": "SICP-10157"}]}
        a = ai_assist.Assistant(cfg=cfg)
        with mock.patch.object(ap.urllib.request, "urlopen", P.Wire(P.openai_chat(json.dumps(ans)))):
            self.assertEqual(a.read_notes({}, "Number SICP-10157"), [])

    def test_a_scan_code_needs_where_it_is_printed(self):
        cfg = self.setup(openai=P.OPENAI_KEY)
        ans = {"clues": [{"kind": "catno", "value": "SICP-1015", "where": " ", "image": 0}]}
        with mock.patch.object(ap.urllib.request, "urlopen", P.Wire(P.openai_chat(json.dumps(ans)))):
            self.assertEqual(ai_assist.Assistant(cfg=cfg).read_scans([b"x"], {}), [])

    def test_catalogue_number_searches_only_known_numbers_rewritten(self):
        cfg = self.setup(openai=P.OPENAI_KEY)
        ans = {"queries": [{"kind": "catno", "text": "SICP-10159"}, {"kind": "catno", "text": "SICP 10157~9"},
                           {"kind": "title", "text": "Hikari"}]}
        with mock.patch.object(ap.urllib.request, "urlopen", P.Wire(P.openai_chat(json.dumps(ans)))):
            got = ai_assist.Assistant(cfg=cfg).suggest_queries({"title": "Light"}, ["SICP-10157-9"])
        self.assertEqual([q["text"] for q in got], ["SICP 10157~9", "Hikari"])


class Covers(P.Base):
    def profile(self, enabled, tasks=None):
        import profiles as pf
        d = pf.profiles_dir()
        d.mkdir(parents=True, exist_ok=True)
        ai = {"enabled": enabled}
        if tasks is not None:
            ai["tasks"] = tasks
        (d / "mine.json").write_text(json.dumps({"name": "mine", "ai": ai}))
        self.addCleanup((d / "mine.json").unlink, missing_ok=True)

    def test_an_ai_key_makes_covers_only_when_allowed_and_turned_on(self):
        import covers_ai
        cfg = self.setup(openai=P.OPENAI_KEY)
        cfg["consent"] = False
        ap.save_config(cfg)
        self.profile(True)
        self.assertTrue(covers_ai.not_ready())                              # (not allowed yet)
        self.assertEqual(covers_ai.service()["key"], "")
        cfg["consent"] = True
        ap.save_config(cfg)
        self.profile(False)
        self.assertTrue(covers_ai.not_ready())                              # (no profile turns AI on)
        self.profile(True, ["notes"])
        self.assertTrue(covers_ai.not_ready())                              # (covers not among its tasks)
        self.profile(True)
        self.assertEqual(covers_ai.not_ready(), "")
        self.assertEqual(covers_ai.service()["provider"], "openai")


class Addresses(P.Base):
    def test_plain_http_only_to_this_computer(self):
        cfg = ap.load_config()
        for url, ok in (("http://localhost:11434/v1", True), ("http://127.0.0.1:1234/v1", True),
                        ("https://openrouter.ai/api/v1", True), ("http://openrouter.ai/api/v1", False),
                        ("http://192.168.1.5:11434/v1", False)):
            cfg["providers"]["compatible"]["url"] = url
            self.assertEqual(not ap.config_problems(cfg), ok, url)
            p = ap.CompatibleProvider(url=url, model="m", key="k")
            self.assertEqual(not p.ready(), ok, url)


class Sheet(P.Base):
    def test_the_sheet_lists_what_each_request_sends(self):
        sent = {}

        class Capture(ap.Provider):
            name = "openai"

            def capabilities(self):
                return {"text", "vision", "web"}

            def json_task(self, system, text, schema, images=()):
                task = next(t for t in ("notes", "vision", "queries") if text.startswith(ai_assist.ASK[t][:20]))
                sent[task] = text
                return json.dumps({"clues": []} if task != "queries" else {"queries": []}), {}

            def web_task(self, system, text):
                sent["web"] = text
                return json.dumps({"releases": []}), [], {}
        cfg = dict(ap.load_config(), consent=True)
        a = ai_assist.Assistant(cfg=cfg, providers=[Capture(key="k", model="m")])
        facts = ai_assist.album_facts({"artist": "A", "title": "T", "folder": "2010 - T", "year": 2010,
                                       "tracks": [{"title": "One"}], "discs": 1})
        a.read_notes(facts, "notes", "scan")
        a.read_scans([b"img"], facts)
        a.suggest_queries(facts, ["ABC-1"])
        a.research(facts, ["ABC-1"])
        listed = dict(ai_assist.what_is_sent())
        for task, text in sent.items():
            album = json.loads(text.split(">>>\n", 1)[1].split("\n<<<END", 1)[0])
            self.assertTrue(set(album) <= set(listed[task]), (task, set(album) - set(listed[task])))
            self.assertIn("tracks", listed[task])
        self.assertNotIn("explain", listed)
        self.assertNotIn("ties", listed)
        for fields in listed.values():
            for f in fields:
                self.assertIn(f, ai_assist.FIELD_WORDS)
        from gui import preferences
        self.assertTrue(set(ai_assist.FIELD_WORDS) <= set(preferences.AI_FIELD_WORDS))


class Anthropic(P.Base):
    def test_room_to_think_low_effort_and_a_refused_effort_dropped(self):
        cfg = self.setup(anthropic=P.CLAUDE_KEY)
        wire = P.Wire(P.http_error(400), P.claude_msg(json.dumps({"clues": []})))
        with mock.patch.object(ap.urllib.request, "urlopen", wire):
            ai_assist.Assistant(cfg=cfg).read_notes({}, "Cat ABC-101")
        first, second = wire.sent[0][2], wire.sent[1][2]
        self.assertGreaterEqual(first["max_tokens"], 16000)
        self.assertEqual(first["output_config"]["effort"], "low")
        self.assertNotIn("effort", second["output_config"])
        self.assertIn("format", second["output_config"])

    def test_paused_web_turn_is_continued(self):
        cfg = self.setup(anthropic=P.CLAUDE_KEY)
        url = "https://www.discogs.com/release/42-X"
        paused = {"content": [{"type": "server_tool_use", "id": "s1", "name": "web_search", "input": {}}],
                  "stop_reason": "pause_turn", "usage": {"input_tokens": 5, "output_tokens": 1}}
        done = {"content": [{"type": "text", "text": json.dumps({"releases": [{"url": url, "why": "x"}]}),
                             "citations": [{"type": "web_search_result_location", "url": url}]}],
                "stop_reason": "end_turn", "usage": {"input_tokens": 5, "output_tokens": 1}}
        wire = P.Wire(paused, done)
        with mock.patch.object(ap.urllib.request, "urlopen", wire):
            got = ai_assist.Assistant(cfg=cfg).research({"title": "T"})
        self.assertEqual(got["discogs"], ["42"])
        self.assertEqual(wire.sent[1][2]["messages"][-1]["role"], "assistant")

    def test_sonnet_suggested_first(self):
        self.assertEqual(ap.suggested_model("anthropic", ["claude-haiku-5-5", "claude-opus-5-5", "claude-sonnet-5-5"]),
                         "claude-sonnet-5-5")
        self.assertEqual(ap.make("anthropic", ap.load_config(), key="k").model, "claude-sonnet-5-5")

    def test_only_cited_urls_count(self):
        cfg = self.setup(anthropic=P.CLAUDE_KEY)
        seen = "https://www.discogs.com/release/7-Seen-Not-Cited"
        answer = {"content": [
            {"type": "web_search_tool_result", "tool_use_id": "s1",
             "content": [{"type": "web_search_result", "url": seen, "title": "x"}]},
            {"type": "text", "text": json.dumps({"releases": [{"url": seen, "why": "x"}]})}],
            "stop_reason": "end_turn", "usage": {"input_tokens": 1, "output_tokens": 1}}
        with mock.patch.object(ap.urllib.request, "urlopen", P.Wire(answer)):
            got = ai_assist.Assistant(cfg=cfg).research({"title": "T"})
        self.assertEqual(got, {"mb": [], "discogs": []})


if __name__ == "__main__":
    unittest.main()
