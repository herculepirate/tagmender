"""Optional AI (0.8.69, rule AI-1) in the matcher and the engine, providers stubbed (no network).

An AI catalogue number picks a release only when a database release carries it, and never as a
sure pick; a release only AI's searches found is held (never filed unattended); an album a
barcode proves makes no AI call; with AI off (or giving nothing) the plan is the same.

Run:  python3 tests/test_v0869_ai_engine.py
Needs mutagen and ffmpeg (ffmpeg only to build the tiny synthetic albums).
"""
import os as _os, tempfile as _tempfile  # noqa: E401
_os.environ["XDG_CONFIG_HOME"] = _tempfile.mkdtemp(prefix="tm-config-")
_os.environ["XDG_STATE_HOME"] = _tempfile.mkdtemp(prefix="tm-state-")

import json
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import types
import unittest
import urllib.parse as up
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import ai_assist  # noqa: E402
import ai_providers as ap  # noqa: E402
import match_core as mc  # noqa: E402
import match_albums as ma  # noqa: E402

CFG = dict(mc.DEFAULT_CFG, preferred_countries=[])
LENS = [10000, 20000, 30000]


def _upc(body):
    return next(body + str(d) for d in range(10) if mc.valid_barcode(body + str(d)))


UPC = _upc("07567826432")


def mb_release(rid, title, catno, barcode="", country="JP"):
    return {"id": rid, "title": title, "date": "2010", "country": country, "barcode": barcode,
            "artist-credit": [{"name": "Artist Alpha", "joinphrase": ""}],
            "label-info": [{"catalog-number": catno, "label": {"name": "Label Co"}}],
            "media": [{"format": "CD", "tracks": [{"title": f"T{i}", "length": ms} for i, ms in enumerate(LENS)]}]}


class FakeHttp:
    """MusicBrainz only. A title search finds the releases listed under that title; a catno
    search finds the releases carrying that number; a barcode search the one with it."""

    def __init__(self, releases, titles):
        self.db = None
        self.mb = {r["id"]: r for r in releases}
        self.titles = titles                       # title -> [ids]
        self.calls = []

    def get_json(self, url, host):
        self.calls.append((host, url))
        if host != "mb":
            return {}
        if "/ws/2/release/?" in url or "/ws/2/release?query" in url:
            q = up.unquote(url.split("query=")[1].split("&")[0])
            ids = [rid for t, rids in self.titles.items() if f'release:"{t}"' in q for rid in rids]
            ids += [r["id"] for r in self.mb.values() if r["label-info"][0]["catalog-number"]
                    and f'catno:"{r["label-info"][0]["catalog-number"]}"' in q]
            ids += [r["id"] for r in self.mb.values() if r["barcode"] and f'barcode:{r["barcode"]}' in q]
            return {"releases": [{"id": rid, "track-count": 3} for rid in dict.fromkeys(ids)]}
        if "/ws/2/release/" in url:
            return self.mb.get(url.split("/release/")[1].split("?")[0])
        return {}

    def asked(self, needle):
        return [u for _h, u in self.calls if needle in up.unquote(u)]


def make_album(root, title, barcode="", notes=""):
    d = Path(root) / "Artist Alpha" / f"2010 - {title}"
    d.mkdir(parents=True)
    for i in range(3):
        meta = ["-metadata", f"title=T{i}", "-metadata", f"album={title}", "-metadata", "albumartist=Artist Alpha",
                "-metadata", "date=2010", "-metadata", f"track={i + 1}"]
        if barcode:
            meta += ["-metadata", f"barcode={barcode}"]
        subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i",
                        f"sine=frequency={300 + 50 * i}:duration={10 * (i + 1)}", "-ar", "8000", "-ac", "1",
                        *meta, str(d / f"{i + 1:02d}.flac"), "-y"], check=True)
    if notes:
        (d / "info.txt").write_text(notes, encoding="utf-8")
    return {"AlbumArtistFolder": "Artist Alpha", "AlbumFolder": d.name, "FullPath": str(d), "Status": "untagged"}


class StubProvider(ap.Provider):
    """Answers each task from `answers` (task -> JSON); counts its calls. Never the network."""
    name = "openai"

    def __init__(self, answers):
        super().__init__(key="sk-stub-0123456789", model="stub-model")
        self.answers, self.calls = answers, []

    def capabilities(self):
        return {"text", "vision", "web"}

    def _task(self, text):
        for task, words in (("notes", "List the catalogue numbers"), ("vision", "Read these scans"),
                            ("queries", "Suggest up to four"), ("web", "Search the web")):
            if text.startswith(words):
                return task
        return "other"

    def json_task(self, system, text, schema, images=()):
        task = self._task(text)
        self.calls.append(task)
        return json.dumps(self.answers.get(task, {"clues": []} if task in ("notes", "vision") else
                                           {"queries": []} if task == "queries" else {})), {}

    def web_task(self, system, text):
        self.calls.append("web")
        return json.dumps(self.answers.get("web", {"releases": []})), self.answers.get("web_cited", []), {}


NOTES = "日本盤。品番は「ＳＩＣＰ－１０１５」です。\n"   # (full-width: the notes reader misses it)


class Matcher(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def setUp(self):
        self.root = Path(tempfile.mkdtemp(dir=self.tmp))
        db = ap.ex.STATE_DIR / "state.db"
        if db.exists():
            c = sqlite3.connect(db)
            c.execute("DROP TABLE IF EXISTS ai_answers")
            c.commit()
            c.close()

    def assistant(self, answers):
        cfg = ap.load_config()
        cfg["consent"] = True
        stub = StubProvider(answers)
        return ai_assist.Assistant(cfg=cfg, providers=[stub]), stub

    def tie_http(self):
        # (mb-a is the pick when nothing tells them apart; only mb-b carries the notes' number)
        return FakeHttp([mb_release("mb-a", "First Light", "ABCD-200"),
                         mb_release("mb-b", "First Light", "SICP-1015")],
                        {"First Light": ["mb-a", "mb-b"]})

    def run_album(self, http, row, **args):
        return ma.process(row, http, CFG, None, types.SimpleNamespace(ocr=False, use_discogs=False, **args))

    @staticmethod
    def plan_of(o):
        return {k: v for k, v in o.items() if not k.startswith("ai") and k != "checked"}

    def test_ai_catno_picks_the_release_that_carries_it_but_holds_it(self):
        """AI-1: the two pressings tie; the Japanese info file names the catalogue number only one
        carries. That one is suggested for you to choose; the pick stays the databases' own."""
        row = make_album(self.root, "First Light", notes=NOTES)
        off = self.run_album(self.tie_http(), row)
        self.assertNotEqual(off["status"], "auto", off["why"])
        self.assertEqual(off["best"]["id"], "mb-a")
        self.assertNotIn("sicp1015", ma.read_album(Path(row["FullPath"]), "Artist Alpha", row["AlbumFolder"])["catnos"])
        ai, stub = self.assistant({"notes": {"clues": [
            {"kind": "catno", "value": "SICP-1015", "quote": "品番は「ＳＩＣＰ－１０１５」です。"},
            {"kind": "country", "value": "JP", "quote": "日本盤。"}]}})
        on = self.run_album(self.tie_http(), row, ai=ai)
        self.assert_not_raised(off, on)
        self.assertEqual(on["best"]["id"], "mb-a")
        self.assertEqual(on["status"], "review")
        self.assertEqual(on["ai_tie_pick"], "mb:mb-b")
        self.assertFalse(on.get("ai_held"))
        self.assertTrue(any(w.startswith("AI-1: ai:openai/stub-model read catalogue number SICP-1015")
                            and "mb:mb-b carries it" in w for w in on["ai_notes"]), on["ai_notes"])
        self.assertEqual(on["why"], off["why"])                       # (the databases' reasons, unchanged)
        self.assertTrue(any("country JP" in n and "not used by the matcher" in n for n in on["ai_notes"]))
        self.assertTrue(any(n.startswith("ai:openai/stub-model notes: sent names; notes text") for n in on["ai_notes"]),
                        on["ai_notes"])
        import engine
        self.assertEqual(engine.evidence_of(on, on["best"])[-len(on["ai_notes"]):], on["ai_notes"])
        self.assertIn("notes", stub.calls)
        self.assertEqual(on["ai"]["calls"], len(stub.calls))
        # the engine never files the pressing on it: at most what every pressing shares, for review
        import engine
        import profiles as pf
        eng = engine.Engine(pf.from_dict({"name": "default", "providers": {"web": {"enabled": False}},
                                          "artwork": {"enabled": False}}), http=self.tie_http())
        with mock.patch.object(ma, "process", return_value=on):
            p = eng.identify(eng.albums_in(Path(row["FullPath"]))[0])
        self.assertNotEqual(p.tier, "exact", (p.status, p.tier, p.reason))

    def test_a_tie_spelled_otherwise_writes_what_ai_off_writes(self):
        """AI_REVIEW-13: the tied releases spell their track titles otherwise; AI's clue points to
        the other one. Every target written (album known, Partial, exact) is AI-off's."""
        rel_b = mb_release("mb-b", "First Light", "SICP-1015")
        for i, t in enumerate(rel_b["media"][0]["tracks"]):
            t["title"] = f"t{i}"                                       # (the same, spelled otherwise)

        def http():
            return FakeHttp([mb_release("mb-a", "First Light", "ABCD-200"), rel_b], {"First Light": ["mb-a", "mb-b"]})
        row = make_album(self.root, "First Light", notes=NOTES)
        off = self.run_album(http(), row)
        ai, _stub = self.assistant({"notes": {"clues": [
            {"kind": "catno", "value": "SICP-1015", "quote": "品番は「ＳＩＣＰ－１０１５」です。"}]}})
        on = self.run_album(http(), row, ai=ai)
        self.assertEqual(on["ai_tie_pick"], "mb:mb-b")
        import engine
        import profiles as pf
        plans = []
        for o in (off, on):
            eng = engine.Engine(pf.from_dict({"name": "default", "providers": {"web": {"enabled": False}},
                                              "artwork": {"enabled": False}}), http=http())
            with mock.patch.object(ma, "process", return_value=o):
                p = eng.identify(eng.albums_in(Path(row["FullPath"]))[0])
            plans.append((p.status, p.tier, p.release_id, json.dumps(p.targets, sort_keys=True, default=str)))
        self.assertEqual(plans[1], plans[0])

    def test_a_code_no_release_carries_changes_nothing(self):
        row = make_album(self.root, "First Light", notes="Каталог: ZZZ-9999\n")
        off = self.run_album(self.tie_http(), row)
        ai, _stub = self.assistant({"notes": {"clues": [
            {"kind": "catno", "value": "ZZZ-9999", "quote": "Каталог: ZZZ-9999"}]}})
        on = self.run_album(self.tie_http(), row, ai=ai)
        self.assertEqual(self.plan_of(on), self.plan_of(off))
        self.assertTrue(any("ZZZ-9999" in n and "no release found carries it" in n for n in on["ai_notes"]))

    def test_proven_album_makes_no_ai_call(self):
        http = FakeHttp([mb_release("mb-a", "First Light", "SICP-1015", barcode=UPC),
                         mb_release("mb-b", "First Light", "ABCD-200")], {"First Light": ["mb-a", "mb-b"]})
        row = make_album(self.root, "First Light", barcode=UPC, notes=NOTES)
        ai, stub = self.assistant({"notes": {"clues": []}})
        o = self.run_album(http, row, ai=ai)
        self.assertEqual((o["status"], o["best"]["id"]), ("auto", "mb-a"))
        self.assertEqual((stub.calls, ai.calls), ([], 0))
        self.assertNotIn("ai_notes", o)

    def test_ai_off_or_giving_nothing_plans_the_same(self):
        row = make_album(self.root, "First Light", notes=NOTES)
        base = self.run_album(self.tie_http(), row)
        none = self.run_album(self.tie_http(), row, ai=None)
        ai, stub = self.assistant({})
        empty = self.run_album(self.tie_http(), row, ai=ai)
        self.assertEqual(self.plan_of(none), self.plan_of(base))
        self.assertEqual(self.plan_of(empty), self.plan_of(base))
        self.assertTrue(stub.calls)                                   # (asked, and it gave nothing)

    def test_outage_is_planned_as_if_ai_were_off(self):
        row = make_album(self.root, "First Light", notes=NOTES)
        base = self.run_album(self.tie_http(), row)
        ai, stub = self.assistant({})
        stub.json_task = mock.Mock(side_effect=ap.AIUnavailable("openai", "outage", "URLError"))
        stub.web_task = stub.json_task
        o = self.run_album(self.tie_http(), row, ai=ai)
        self.assertEqual(self.plan_of(o), self.plan_of(base))
        self.assertTrue(any("outage" in n for n in o["ai_notes"]), o["ai_notes"])
        self.assertEqual(ai.calls, 0)

    def assert_not_raised(self, off, on):
        """AI-1's invariant: status never rises, the AI-off pick stays unless a true tie was broken."""
        rank = {"nomatch": 0, "error": 0, "review": 1, "auto": 2}
        self.assertLessEqual(rank.get(on["status"], 0), rank.get(off["status"], 0), (off["status"], on["status"]))
        self.assertEqual(on["status"], off["status"])
        self.assertEqual(on.get("best"), off.get("best"))              # (what is written: never AI's choice)
        self.assertEqual(on.get("album_only"), off.get("album_only"))

    def test_a_release_only_ai_found_is_shown_never_the_pick(self):
        """The files' title finds nothing; AI's suggested original title finds a release that fits
        the files: shown as an alternative for you to choose, the album stays as AI-off left it."""
        http = FakeHttp([mb_release("mb-a", "First Light", "SICP-1015")], {"First Light": ["mb-a"]})
        row = make_album(self.root, "Erstes Licht")
        off = self.run_album(http, row)
        self.assertIsNone(off.get("best"))
        ai, _stub = self.assistant({"queries": {"queries": [{"kind": "title", "text": "First Light"}]}})
        on = self.run_album(http, row, ai=ai)
        self.assert_not_raised(off, on)
        self.assertIsNone(on.get("best"))
        self.assertTrue(on["ai_alternatives"][0].startswith("mb:mb-a "), on["ai_alternatives"])
        self.assertNotIn("ai_held", on)
        # the engine plans it as with AI off (no hold of its own), the alternative in the evidence
        import engine
        import profiles as pf
        eng = engine.Engine(pf.from_dict({"name": "default", "providers": {"web": {"enabled": False}},
                                          "artwork": {"enabled": False}}), http=http)
        self.assertIsNone(eng.ai)                                     # (a profile's AI is off by default)
        with mock.patch.object(ma, "process", return_value=off):
            p_off = eng.identify(eng.albums_in(Path(row["FullPath"]))[0])
        with mock.patch.object(ma, "process", return_value=on):
            p_on = eng.identify(eng.albums_in(Path(row["FullPath"]))[0])
        self.assertEqual((p_on.status, p_on.tier, p_on.release_id), (p_off.status, p_off.tier, p_off.release_id))
        self.assertTrue(any("found by AI's searches" in n for n in p_on.evidence), p_on.evidence)

    def test_an_ai_found_release_never_outranks_the_review_pick(self):
        """AI_REVIEW-12: the album is at review (a tie); AI's search finds a release scoring better:
        the pick, its tier and tags stay; the find is an alternative."""
        http = self.tie_http()
        http.mb["mb-c"] = mb_release("mb-c", "First Light", "SICP-1015")
        row = make_album(self.root, "First Light", notes=NOTES)
        off = self.run_album(self.tie_http(), row)
        ai, _stub = self.assistant({"queries": {"queries": [{"kind": "title", "text": "Hikari"}]}})
        http.titles["Hikari"] = ["mb-c"]
        on = self.run_album(http, row, ai=ai)
        self.assert_not_raised(off, on)
        self.assertEqual(on["best"]["id"], off["best"]["id"])
        self.assertTrue(any(a.startswith("mb:mb-c ") for a in on["ai_alternatives"]))

    def test_a_cited_web_release_is_fetched_scored_and_shown_only(self):
        http = FakeHttp([mb_release("mb-a", "First Light", "SICP-1015")], {})
        row = make_album(self.root, "Erstes Licht")
        url = "https://musicbrainz.org/release/0a1b2c3d-1111-2222-3333-444455556666"
        http.mb["0a1b2c3d-1111-2222-3333-444455556666"] = dict(
            mb_release("0a1b2c3d-1111-2222-3333-444455556666", "First Light", "SICP-1015"))
        off = self.run_album(http, row)
        ai, _stub = self.assistant({"web": {"releases": [{"url": url, "why": "the label's page"}]},
                                    "web_cited": [url]})
        on = self.run_album(http, row, ai=ai)
        self.assert_not_raised(off, on)
        self.assertTrue(on["ai_alternatives"][0].startswith("mb:0a1b2c3d-1111-2222-3333-444455556666 "))

    def test_nomatch_never_becomes_review_on_an_ai_code(self):
        """AI_REVIEW-1 (the reviewer's probe): a lone weak candidate carrying a code the notes name
        for another album does not turn nomatch into review."""
        import ai_assist as A
        s1 = {"cand": {"source": "mb", "id": "unrelated", "catnos": ["abc1234"], "barcodes": []},
              "confidence": 30.0, "clues": {}}
        mb = {"status": "nomatch", "best": s1, "scored": [s1], "why": ["low"]}
        final = {"status": "nomatch", "best": s1, "why": ["low"]}
        ai, _stub = self.assistant({"notes": {"clues": [{"kind": "catno", "value": "ABC-1234", "quote": "ABC-1234"}]}})
        out = {}
        with mock.patch.object(ma, "probe_search", lambda *a, **k: (a[6], a[7], [], [])):
            _m, _d, f2 = A.clue_step(None, {"artist": "A", "title": "T", "folder": "A - T", "tracks": [], "catnos": []},
                                     {"auto_min_margin": 8.0}, None, mb, None, final, "",
                                     "Cat no: ABC-1234 (a different album advertised)", [], True, False, out, ai)
        self.assertEqual(f2["status"], "nomatch")
        self.assertIsNone(out.get("ai_tie_pick"))

    def test_a_tie_between_countries_is_left_to_the_country_order(self):
        """TIE-2 holds: AI breaks a tie only between releases of one country, format and type."""
        http = FakeHttp([mb_release("mb-a", "First Light", "ABCD-200", country="US"),
                         mb_release("mb-b", "First Light", "SICP-1015", country="JP")],
                        {"First Light": ["mb-a", "mb-b"]})
        row = make_album(self.root, "First Light", notes=NOTES)
        off = self.run_album(http, row)
        ai, _stub = self.assistant({"notes": {"clues": [
            {"kind": "catno", "value": "SICP-1015", "quote": "品番は「ＳＩＣＰ－１０１５」です。"}]}})
        on = self.run_album(http, row, ai=ai)
        self.assert_not_raised(off, on)
        self.assertNotIn("ai_tie_pick", on)

    def test_an_excluded_release_never_becomes_the_pick(self):
        """The exclusion rules still hold: a tie AI would break toward a release a rule excludes stays."""
        row = make_album(self.root, "First Light", notes=NOTES)
        off = self.run_album(self.tie_http(), row)
        ai, _stub = self.assistant({"notes": {"clues": [
            {"kind": "catno", "value": "SICP-1015", "quote": "品番は「ＳＩＣＰ－１０１５」です。"}]}})
        real, fields = ma.exclusions.gate, ma.release_fields

        def gate(paths, stage, rules, release=None, **kw):
            if release and release.get("_id") == ["mb-b"]:        # (a rule excluding mb-b)
                return types.SimpleNamespace(all_excluded=True)
            return real(paths, stage, rules, **kw)
        with mock.patch.object(ma.exclusions, "gate", gate), \
                mock.patch.object(ma, "release_fields", lambda c: dict(fields(c), _id=[str(c["id"])])):
            on = self.run_album(self.tie_http(), row, ai=ai)
        self.assert_not_raised(off, on)
        self.assertEqual(on["best"]["id"], "mb-a")
        self.assertNotIn("ai_tie_pick", on)
        self.assertTrue(any("is not suggested" in n for n in on["ai_notes"]))


class Profiles(unittest.TestCase):
    def test_ai_off_by_default_validated_and_migrated(self):
        import profiles as pf
        p = pf.from_dict({"name": "x"})
        self.assertEqual(p.ai_settings()["enabled"], False)
        self.assertEqual(p.ai_settings()["tasks"], list(pf.AI_TASKS))
        on = pf.from_dict({"name": "x", "ai": {"enabled": True, "tasks": ["notes"]}})
        self.assertEqual(on.ai_settings(), {"enabled": True, "tasks": ["notes"]})
        self.assertTrue(pf.validate({"name": "x", "ai": {"enabled": "yes", "tasks": ["mind-reading"]}}))
        self.assertIsNone(ai_assist.for_profile(on))                 # (no provider set up, no consent)

    def test_profile_tasks_limit_what_is_asked(self):
        cfg = ap.load_config()
        cfg["consent"] = True
        stub = StubProvider({})
        a = ai_assist.Assistant(tasks=["explain"], cfg=cfg, providers=[stub])
        self.assertEqual(a.read_notes({}, "Каталог: SICP-1015"), [])
        self.assertEqual(stub.calls, [])

    def test_estimate_before_a_batch(self):
        cfg = ap.load_config()
        est = ai_assist.estimate(10, ("notes", "queries"), cfg)
        self.assertEqual(est["requests"], 20)
        self.assertIsNone(est["cost"])                                # (no price entered: none guessed)


if __name__ == "__main__":
    unittest.main()
