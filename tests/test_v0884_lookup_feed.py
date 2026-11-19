# 0.8.84 LKP-1: an album asked for while a look-up runs joins it (engine.LookupFeed) when it can be
# looked up as the look-up's own albums are (its sources, profile, folder, your edits and
# choices): it starts as soon as one of the look-up's workers is free - never more than
# engine.LOOKUP_WORKERS albums at once - and is planned with them, as one look-up of them all.
# One that cannot waits for the look-up to end (queued), and the status bar says why. Cancel
# stops it all: an album that joined and was not started is not looked up. No network.
import os
import sys
import threading
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_gui as tg  # noqa: E402  (sets the isolated config, offscreen Qt)
import test_engine as te  # noqa: E402
import engine  # noqa: E402
import plan as pl  # noqa: E402
import profiles as pf  # noqa: E402
from test_gui import wait  # noqa: E402


class CloningHttp(te.FakeHttp):
    """FakeHttp that plan_each may clone: several albums looked up at once."""
    def thread_clone(self):
        return self

    def close_clone(self):
        pass


def rows_of(albums):
    """What a plan says of each album: what Apply would do with it."""
    return sorted((a.get("key") or "", a.get("status"), a.get("label"),
                   tuple(sorted((os.path.basename(f.get("src") or ""), f.get("op"), f.get("dst"))
                                for f in a.get("files") or [])),
                   tuple(a.get("notes") or []))
                  for a in albums)


# ---- the engine: plan_each with a feed --------------------------------------------------------
class Stub:
    def __init__(self, n):
        self.folder, self.files, self.others = Path(f"/none/{n}"), [Path(f"/none/{n}/1.flac")], []


class FeedStubs(unittest.TestCase):
    """plan_each with stand-in albums: who starts when, and how many at once."""
    def setUp(self):
        import exclusions as ex
        import tempfile
        self.tmp = Path(tempfile.mkdtemp())
        self.saved = (ex.CONFIG_DIR, ex.STATE_DIR)
        ex.CONFIG_DIR, ex.STATE_DIR = self.tmp / "c", self.tmp / "s"
        ex._write(ex.rules_file(), {"rules": []})
        self.addCleanup(lambda: setattr(ex, "CONFIG_DIR", self.saved[0]) or setattr(ex, "STATE_DIR", self.saved[1]))
        self.groups = [Stub(i) for i in range(6)]
        self.keys = [engine.group_key(g.folder, g.files) for g in self.groups]

    def run_it(self, identify, feed=None, keys=None, workers=3, cancel=None):
        prof = pf.from_dict({"name": "default", "providers": {"web": {"enabled": False}},
                             "artwork": {"enabled": False}})
        emitted, started = [], []
        with mock.patch.object(engine.Engine, "albums_in", lambda s, p, skip=(): list(self.groups)), \
                mock.patch.object(engine.Engine, "identify_all", lambda s, g: identify(g)), \
                mock.patch.object(engine.Engine, "add_artwork", lambda s, p: None), \
                mock.patch.object(engine.Engine, "apply_level", lambda s, p: None):
            e = engine.Engine(prof, http=CloningHttp())
            e.lookup_workers = workers
            n = e.plan_each("/none", keys if keys is not None else self.keys,
                            lambda k, plan: emitted.append((k, plan)),
                            progress=lambda i, what: started.append(i), cancel=cancel, feed=feed)
        return n, emitted, started

    @staticmethod
    def prop(g):
        return pl.Proposal(source_dir=str(g.folder), files=[str(f) for f in g.files], status="unresolved",
                           reason="x")

    def test_LKP1_never_more_than_the_workers_at_once(self):
        """One album given, five joining while it runs: three at once at most, and three reached."""
        lock, now, most = threading.Lock(), [0], [0]
        first_in = threading.Event()
        go = threading.Event()
        feed = engine.LookupFeed(self.keys[:1])

        def identify(g):
            with lock:
                now[0] += 1
                most[0] = max(most[0], now[0])
            if g is self.groups[0]:
                first_in.set()
            go.wait(0.3 if g is not self.groups[0] else 20)      # (each holds its worker a while)
            with lock:
                now[0] -= 1
            return [self.prop(g)]
        out = {}
        t = threading.Thread(target=lambda: out.setdefault("r", self.run_it(identify, feed, self.keys[:1])))
        t.start()
        self.assertTrue(first_in.wait(20))
        self.assertTrue(feed.add(self.keys[1:]))
        threading.Timer(1.5, go.set).start()
        t.join(60)
        self.assertFalse(t.is_alive())
        n, emitted, started = out["r"]
        self.assertEqual(n, 6)
        self.assertEqual(sorted(started), list(range(6)))
        self.assertEqual(most[0], 3)
        self.assertFalse(feed.add(["late"]))                     # (ended: one asked now waits)

    def test_LKP1_the_whole_plan_is_in_the_order_given_then_joined(self):
        feed = engine.LookupFeed(self.keys[:2])

        def identify(g):
            if g is self.groups[0]:
                feed.add(self.keys[2:])
            return [self.prop(g)]
        n, emitted, _s = self.run_it(identify, feed, self.keys[:2])
        self.assertEqual(n, 6)
        last = emitted[6:]                                        # (the plan of them all)
        self.assertEqual([k for k, _p in last], self.keys)
        _n, alone, _s = self.run_it(lambda g: [self.prop(g)], None, self.keys, workers=1)
        self.assertEqual([rows_of(p["albums"]) for _k, p in last],
                         [rows_of(p["albums"]) for _k, p in alone[6:]])


class FeedReal(te.Base):
    """plan_each with real albums (generated audio, a stand-in Discogs): the albums that joined
    are planned exactly as one look-up of them all plans them."""
    def setUp(self):
        super().setUp()
        self.g = self.album("Band G", "Album G")
        self.g2 = self.album("Band G", "Album G", folder="2005 - Album G (other rip)")   # (MOV-4: one release twice)
        self.h = self.album("Band H", "Album H")
        self.dest = self.tmp / "library"
        self.dest.mkdir()
        self.http = CloningHttp(dg=[te.dg_release(920, "Band G", "Album G"), te.dg_release(922, "Band H", "Album H")])

    def eng(self, workers):
        e = self.engine(self.http, operation="copy", destination=str(self.dest))
        e.lookup_workers = workers
        return e

    def keys(self):
        keys = {Path(a["source_dir"]).name: a["key"] for a in self.eng(1).listing(str(self.lib))["albums"]}
        return [keys["2005 - Album G"], keys["2005 - Album G (other rip)"], keys["2005 - Album H"]]

    def last(self, emitted):
        out = {}
        for k, p in emitted:
            out[k] = p
        return {k: rows_of(p["albums"]) for k, p in out.items()}

    def test_LKP1_results_equal_the_sequential_look_up(self):
        keys = self.keys()
        alone = []
        self.eng(1).plan_each(str(self.lib), keys, lambda k, p: alone.append((k, p)))
        # (the first album alone, the other two joining while it is looked up)
        feed = engine.LookupFeed(keys[:1])
        real = engine.Engine.identify_all
        h_in = threading.Event()

        def gated(e, g):
            if g.folder.name == "2005 - Album G":
                feed.add(keys[1:])
                self.assertTrue(h_in.wait(30), "the albums that joined did not start meanwhile")
            elif g.folder.name == "2005 - Album H":
                h_in.set()
            return real(e, g)
        fed = []
        with mock.patch.object(engine.Engine, "identify_all", gated):
            n = self.eng(3).plan_each(str(self.lib), keys[:1], lambda k, p: fed.append((k, p)), feed=feed)
        self.assertEqual(n, 3)
        self.assertEqual(self.last(fed), self.last(alone))
        statuses = {r[1] for rs in self.last(alone).values() for r in rs}
        self.assertTrue(statuses & {"ready", "unchanged"}, statuses)       # (really identified)
        # (MOV-4: one of the two copies of the release is kept apart, as in one look-up of them all)
        notes = " ".join(n for rs in self.last(fed).values() for r in rs for n in r[4])
        self.assertIn("copy", notes)

    def test_LKP1_cancel_leaves_an_album_that_joined_not_looked_up(self):
        keys = self.keys()
        feed = engine.LookupFeed(keys[:1])
        stop, go = threading.Event(), threading.Event()
        real, seen = engine.Engine.identify_all, []

        def gated(e, g):
            seen.append(g.folder.name)
            if g.folder.name == "2005 - Album G":
                feed.add(keys[2:])                       # (one at a time: it waits for this one)
                stop.set()                               # (Cancel)
                go.wait(30)
            return real(e, g)
        got, started = [], []
        with mock.patch.object(engine.Engine, "identify_all", gated):
            t = threading.Thread(target=lambda: self.eng(1).plan_each(
                str(self.lib), keys[:1], lambda k, p: got.append(k), progress=lambda i, w: started.append(i),
                cancel=stop.is_set, feed=feed))
            t.start()
            go.set()
            t.join(60)
        self.assertNotIn("2005 - Album H", seen)
        self.assertNotIn(keys[2], got)
        self.assertNotIn(1, started)
        self.assertFalse(feed.add(keys[1:2]))


# ---- the app ---------------------------------------------------------------------------------
@unittest.skipIf(tg.QApplication is None, "PySide6 not installed")
class FeedOverview(tg.Gui):
    def setUp(self):
        super().setUp()
        import app_settings
        app_settings.set_value("add_folder", "list")
        self.h = self.album("Band H", "Album H")
        self.g2 = self.album("Band G", "Album G", folder="2005 - Album G (other rip)")
        self.k = self.album("Band K", "Album K")
        self.http.dg[922] = te.dg_release(922, "Band H", "Album H")
        self.http.dg[923] = te.dg_release(923, "Band K", "Album K")
        self.dest = self.tmp / "library"
        self.dest.mkdir(exist_ok=True)
        pf.save(pf.from_dict({"name": "Mover", "operation": "copy", "destination": str(self.dest),
                              "providers": {"web": {"enabled": False}}, "artwork": {"enabled": False}}))
        self.ov = self.win.pages["Overview"]
        self.ov.refresh_profiles()
        self.ov.profile.setCurrentText("Mover")
        self.gates, self.seen = {}, []
        real = engine.Engine.identify_all

        def gated(e, g):
            self.seen.append(g.folder.name)
            for name, gate in self.gates.items():
                if g.folder.name == name:
                    gate.wait(30)
            return real(e, g)
        p = mock.patch.object(engine.Engine, "identify_all", gated)
        p.start()
        self.addCleanup(p.stop)
        self.addCleanup(lambda: [g.set() for g in self.gates.values()])

    def cloning(self):
        """Albums really looked up side by side (the app's Http is cloned per worker)."""
        self.http = CloningHttp(dg=list(self.http.dg.values()))

    def gate(self, name):
        self.gates[name] = threading.Event()
        return self.gates[name]

    def row(self, folder):
        return next(a for a in self.ov.model.albums if Path(a["source_dir"]).name == folder)

    def status(self, folder):
        ov = self.ov
        return ov.model.data(ov.model.index(ov.model.albums.index(self.row(folder)), 0))

    def listed(self):
        ov = self.ov
        ov.set_path(str(self.lib))
        self.assertTrue(wait(lambda: ov.plan is not None and not ov.running, self.app), ov.summary.text())
        self.assertEqual(len(ov.model.albums), 5)

    def test_LKP1_an_album_queued_during_a_one_album_look_up_starts_before_it_ends(self):
        self.cloning()
        ov = self.ov
        self.listed()
        g = self.gate("2005 - Album G")
        ov.look_up("profile", [self.row("2005 - Album G")])
        self.assertTrue(wait(lambda: self.status("2005 - Album G") == "Looking up…", self.app, timeout=30))
        ov.look_up("profile", [self.row("2005 - Album H")])                  # (asked while it runs)
        key = ov.album_key(self.row("2005 - Album H"))
        self.assertEqual(ov.queue, {})
        self.assertIn(key, ov.looking)
        self.assertIn("joined this look-up", self.win.statusBar().currentMessage())
        ov.look_up("profile", [self.row("2005 - Album H")])                  # (not asked twice)
        self.assertEqual(ov._lookup_order.count(key), 1)
        # found while the first album is still at its gate: the look-up has not ended
        self.assertTrue(wait(lambda: self.row("2005 - Album H")["status"] != "listed", self.app, timeout=30),
                        ov.summary.text())
        self.assertTrue(ov.running)
        self.assertEqual(self.row("2005 - Album G")["status"], "listed")
        self.assertEqual(ov._lookup_n, 2)                                   # (the progress counts it)
        g.set()
        self.assertTrue(wait(lambda: not ov.running, self.app), ov.summary.text())
        self.assertEqual(self.row("2005 - Album G")["status"], "ready")
        self.assertEqual(self.row("2005 - Album H")["status"], "ready")
        self.assertEqual((ov.queue, ov.states, ov.looking), ({}, {}, {}))

    def test_LKP1_results_equal_one_look_up_of_them_all(self):
        self.cloning()
        ov = self.ov
        self.listed()
        names = ["2005 - Album G", "2005 - Album G (other rip)", "2005 - Album H"]
        g = self.gate(names[0])
        ov.look_up("profile", [self.row(names[0])])
        self.assertTrue(wait(lambda: self.status(names[0]) == "Looking up…", self.app, timeout=30))
        ov.look_up("profile", [self.row(n) for n in names[1:]])
        self.assertEqual(ov.queue, {})
        g.set()
        self.assertTrue(wait(lambda: not ov.running, self.app), ov.summary.text())
        joined = rows_of([self.row(n) for n in names])
        ov.look_up("profile", [self.row(n) for n in names], again=True)      # (one look-up of them all)
        self.assertTrue(wait(lambda: not ov.running, self.app), ov.summary.text())
        self.assertEqual(joined, rows_of([self.row(n) for n in names]))
        # (MOV-4: the second copy of the release sees the first's folder claimed - only in a plan of
        # them all; looked up after it ended, alone, it would not)
        self.assertEqual(sorted(r[1] for r in joined), ["ready", "ready", "skipped"])
        self.assertIn("another copy of this release", " ".join(n for r in joined for n in r[4]))

    def test_LKP1_cancel_leaves_queued_albums_not_looked_up(self):
        ov = self.ov                                        # (one worker: what joined waits for it)
        self.listed()
        g = self.gate("2005 - Album G")
        ov.look_up("profile", [self.row("2005 - Album G")])
        self.assertTrue(wait(lambda: self.status("2005 - Album G") == "Looking up…", self.app, timeout=30))
        ov.look_up("profile", [self.row("2005 - Album H")])                   # (joins)
        ov.look_up("discogs_only", [self.row("2005 - Album K")])   # (other sources: queued)
        self.assertIn(ov.album_key(self.row("2005 - Album H")), ov.looking)
        self.assertEqual(list(ov.queue), [ov.album_key(self.row("2005 - Album K"))])
        self.assertEqual(self.status("2005 - Album H"), "Waiting…")
        ov.cancel()
        self.assertEqual(ov.queue, {})
        g.set()
        self.assertTrue(wait(lambda: not ov.running, self.app), ov.summary.text())
        self.app.processEvents()
        self.assertFalse(ov.running)
        self.assertEqual(self.row("2005 - Album H")["status"], "listed")
        self.assertEqual(self.row("2005 - Album K")["status"], "listed")
        self.assertNotIn("2005 - Album H", self.seen)
        self.assertNotIn("2005 - Album K", self.seen)
        self.assertEqual(self.status("2005 - Album H"), "Not looked up")

    def test_LKP1_one_that_cannot_join_waits_and_the_status_bar_says_why(self):
        ov = self.ov
        self.listed()
        g = self.gate("2005 - Album G")
        ov.look_up("profile", [self.row("2005 - Album G")])
        ov.look_up("discogs_only", [self.row("2005 - Album H")])
        self.assertEqual(list(ov.queue), [ov.album_key(self.row("2005 - Album H"))])
        self.assertIn("other sources than this look-up's", self.win.statusBar().currentMessage())
        self.assertEqual(self.status("2005 - Album H"), "Waiting…")
        g.set()
        self.assertTrue(wait(lambda: not ov.running and self.row("2005 - Album H")["status"] != "listed",
                             self.app), ov.summary.text())
        self.assertEqual(ov.queue, {})

    def test_LKP1_an_album_already_looked_up_the_same_way_is_not_asked_again(self):
        ov = self.ov
        self.listed()
        ov.look_up("profile", [self.row("2005 - Album H")])
        self.assertTrue(wait(lambda: not ov.running, self.app), ov.summary.text())
        g = self.gate("2005 - Album G")
        ov.look_up("profile", [self.row("2005 - Album G")])
        self.seen.clear()
        ov.look_up("profile", [self.row("2005 - Album H")])                  # (not Refresh: skipped)
        self.assertNotIn(ov.album_key(self.row("2005 - Album H")), ov.looking)
        self.assertEqual(ov.queue, {})
        g.set()
        self.assertTrue(wait(lambda: not ov.running, self.app), ov.summary.text())
        self.assertNotIn("2005 - Album H", self.seen)

    def test_LKP1_an_album_that_joined_a_look_up_not_taking_it_is_looked_up_after(self):
        """A look-up whose plan_each never takes the albums that join (it ended first): they
        are looked up next, never left Waiting."""
        from gui import overview as govw
        ov = self.ov
        self.listed()
        g = self.gate("2005 - Album G")
        real = govw.plan_each
        calls = []

        def deaf(e, path, keys, emit, feed=None, **kw):          # (the feed not read at all)
            calls.append(list(keys))
            return real(e, path, keys, emit, **kw)
        with mock.patch.object(govw, "plan_each", deaf):
            ov.look_up("profile", [self.row("2005 - Album G")])
            self.assertTrue(wait(lambda: self.status("2005 - Album G") == "Looking up…", self.app, timeout=30))
            ov.look_up("profile", [self.row("2005 - Album H")])
            self.assertIn(ov.album_key(self.row("2005 - Album H")), ov.looking)
            g.set()
            self.assertTrue(wait(lambda: not ov.running and self.row("2005 - Album H")["status"] != "listed",
                                 self.app), ov.summary.text())
        self.assertEqual(len(calls), 2)
        self.assertEqual(self.row("2005 - Album H")["status"], "ready")


for _n in dir(tg.Gui):                                  # (the base class's own tests run there)
    if _n.startswith("test_"):
        setattr(FeedOverview, _n, None)


if __name__ == "__main__":
    unittest.main()
