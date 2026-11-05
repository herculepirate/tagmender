# 0.8.2 identification fixes (no network; fakes and generated audio only):
# - 1: an Engine's Ignore/Skip rules are its own profile's, also when another profile's Engine
#      was made meanwhile (the matcher no longer reads the profile from the process environment);
# - 2: a web source that does not answer leaves the album an error to retry (web_unavailable),
#      never "no candidate": the Overview does not remember it as looked up, the watcher retries it;
# - 3: every plan entry carries the key of the album it was found as, also a set planned as a
#      row per disc: a Look Up or Refresh of those rows asks for that album again.
import os as _os, tempfile as _tempfile  # noqa: E402
_os.environ.setdefault("XDG_CONFIG_HOME", _tempfile.mkdtemp(prefix="tm-config-"))
_os.environ.setdefault("XDG_STATE_HOME", _tempfile.mkdtemp(prefix="tm-state-"))

import sys
import types
import os
import unittest
from unittest import mock
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))
import test_engine as te  # noqa: E402  (its settings and state dirs are temporary)
import test_web_partial as twp  # noqa: E402
import engine  # noqa: E402
import web_tag  # noqa: E402
import exclusions as ex  # noqa: E402
import match_albums as ma  # noqa: E402
import plan as pl  # noqa: E402
import profiles as pf  # noqa: E402


class DeezerDown(twp.WebHttp):
    def get_json(self, url, host):
        if host == "deezer":
            raise ma.ProviderUnavailable("deezer", "outage", "giving up (TimeoutError)")
        return super().get_json(url, host)


class OwnRules(te.Base):
    def test_another_profiles_engine_made_meanwhile_does_not_change_the_rules(self):
        d = self.album("Band G", "Album G")
        http = te.FakeHttp(dg=[te.dg_release(920, "Band G", "Album G")])
        base = {"providers": {"web": {"enabled": False}}, "artwork": {"enabled": False}}
        pf.save(pf.from_dict(dict(base, name="Mine")))
        pf.save(pf.from_dict(dict(base, name="Inbox")))
        ex.save_rule(ex.Rule.from_dict({"id": "no-g", "name": "no Band G", "action": "ignore", "target": "album",
                                        "when": {"all": [{"field": "albumartist", "op": "equals",
                                                          "value": "Band G"}]}}, "Inbox"), "Inbox")
        e = engine.Engine(pf.load("Mine"), http=http)
        g = e.albums_in(d)[0]
        self.assertEqual(e.identify(g).tier, "exact")
        inbox = engine.Engine(pf.load("Inbox"), http=te.FakeHttp())    # (the watcher's pass starts meanwhile)
        again = e.identify(g)
        self.assertEqual((again.status, again.tier), ("identified", "exact"))
        # (and the Inbox's own engine still keeps its rule)
        self.assertEqual(inbox.identify(inbox.albums_in(d)[0]).tier, "ignored")


class WebDown(twp.WebPartial):
    def test_one_web_source_down_gives_no_web_tags_but_is_no_outage(self):
        # (the others answered: a blocked source must not put every unknown album on quick retry)
        d = self.album("Band W", "Album W")
        eng = self.web_engine(DeezerDown())
        p = eng.identify(eng.albums_in(d)[0])
        self.assertEqual(p.status, "unresolved")
        self.assertNotIn("web", p.provider_errors or {})

    def test_every_web_source_down_is_an_outage_not_no_candidate(self):
        d = self.album("Band W", "Album W")
        eng = self.web_engine(DeezerDown())

        def none_answered(http, album, log=None, sources=None, n_tracks=None, failed=None):
            failed.extend(n for n, _f in web_tag.chosen_sources(sources))
            return []
        with mock.patch.object(web_tag, "album_candidates", none_answered):
            p = eng.identify(eng.albums_in(d)[0])
        self.assertEqual(p.status, "unresolved")
        self.assertEqual(p.provider_errors.get("web"), "outage")
        self.assertIn("web_unavailable", p.reason_codes)
        self.assertNotIn("no_candidate", p.reason_codes)
        a = pl.build([p], eng.profile)["albums"][0]
        self.assertIn("web_unavailable", a["identification"]["reason_codes"])
        # the watcher keeps the unresolved schedule (the web only adds evidence)
        import watcher
        st = watcher.Watcher._status_of(types.SimpleNamespace(), a, p, "planned", eng.profile)
        self.assertEqual(st, "unresolved")
        # the Overview does not remember it as looked up (a Look Up asks again)
        try:
            from gui import overview
        except ImportError:
            self.skipTest("PySide6 not installed")
        ov = types.SimpleNamespace(looked={})
        overview.Overview.remember_looked(ov, [dict(a, key="k")], "stamp")
        self.assertEqual(ov.looked, {})

    def test_the_default_profile_never_takes_rules_from_the_environment(self):
        import exclusions as ex
        with mock.patch.dict(os.environ, {"TAGMENDER_PROFILE": "Someone"}), \
                mock.patch.object(ex, "_raw_rules", lambda scope: {"exclusions": [{"id": "x"}]} if scope == "Someone"
                                  else {"rules": []}):
            self.assertEqual(ex.load_rules(""), [])

    def test_with_the_web_answering_it_is_still_tagged_from_it(self):
        d = self.album("Band W", "Album W")
        eng = self.web_engine(twp.WebHttp([twp.deezer_album(11, "Band W", "Album W", [10, 20, 30])]))
        p = eng.identify(eng.albums_in(d)[0])
        self.assertEqual((p.status, p.tier, p.provider_errors), ("identified", "partial", {}))


class SplitKeys(twp.PerDisc):
    def test_rows_of_a_set_planned_per_disc_carry_the_albums_key(self):
        top = self.two_disc_set()
        http = twp.WebHttp([twp.deezer_album(31, "Band P", "Album P", twp.VERSION_A, titles=twp.TITLES,
                                             date="1991-06-01"),
                            twp.deezer_album(32, "Band P", "Album P (Remix 2024)", twp.VERSION_B, titles=twp.TITLES,
                                             date="2024-03-01")])
        eng = self.engine(http, providers={"web": {"enabled": True, "shops": False, "gnudb": False}})
        plan = eng.plan(str(top))
        self.assertEqual(len(plan["albums"]), 2)
        want = {engine.group_key(g.folder, g.files) for g in eng.albums_in(top)}
        self.assertEqual({a.get("key") for a in plan["albums"]}, want)
        again = eng.plan(str(top), only=want)                  # (what Look Up / Refresh send)
        self.assertEqual(len(again["albums"]), 2)


for _cls in (WebDown, SplitKeys):        # (only this file's tests: not test_web_partial's again)
    for _n in dir(_cls):
        if _n.startswith("test_") and _n not in _cls.__dict__:
            setattr(_cls, _n, None)


if __name__ == "__main__":
    unittest.main(argv=sys.argv[:1])
