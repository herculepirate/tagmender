# 0.8.69 performance, the window's side: albums listed show up in the Overview as each is read
# (Engine.listing's on_album, detected, so an engine without it still works); folders dropped
# while a listing, a preview or a look-up runs get their rows at once and join the list when it
# ends, exactly once, and Cancel leaves no rows behind; the columns are fitted when rows come or
# go (not on every change), at most every 500 ms on a long list, and the profile column does not
# read the profiles folder per row; a preview of 2,000 albums opens sorted once, its first row
# shown after; a provider's back-off is said in the status bar, counting down. Offscreen, with
# a stand-in listing and synthetic albums in temporary folders only.
import shutil
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
from test_engine import Base  # noqa: E402  (isolated settings and state)

try:
    from PySide6.QtWidgets import QApplication
except ImportError:                          # pragma: no cover
    QApplication = None


def settle(app, ms=50):
    end = time.time() + ms / 1000
    while time.time() < end:
        app.processEvents()
        time.sleep(0.01)


def until(app, cond, timeout=10.0) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        app.processEvents()
        if cond():
            return True
        time.sleep(0.01)
    return bool(cond())


def entry(folder, key):
    """An album as Engine.listing gives it (not looked up)."""
    d = str(folder)
    return {"source_dir": d, "status": "listed", "label": "", "rule": None, "identification": {"status": "listed"},
            "release": {}, "files": [], "companions": [], "artwork": None, "conflicts": [], "skipped": [],
            "notes": [], "album_files": [d + "/01.flac"], "key": key}


class StandIn:
    """Engine.listing as the engine gives it, with on_album: each album sent as soon as it is
    "read". A folder in HOLD sends its first album, then waits for its gate before the rest."""

    def __init__(self, albums, hold=(), on_album=True):
        self.albums = albums                       # folder -> [album]
        self.gates = {str(f): threading.Event() for f in hold}
        self.calls = []
        me = self
        if on_album:
            def listing(_e, path, within=None, meta=None, on_album=None):
                return me.run(path, on_album)
        else:                                      # (an engine of before: no on_album)
            def listing(_e, path, within=None, meta=None):
                return me.run(path, None)
        self.listing = listing

    def run(self, path, on_album):
        self.calls.append((str(path), on_album is not None))
        out = []
        for i, a in enumerate(self.albums.get(str(path), [])):
            if i == 1 and str(path) in self.gates:
                self.gates[str(path)].wait(15)
            out.append(a)
            if on_album is not None:
                on_album(a)
        if str(path) in self.gates:
            self.gates[str(path)].wait(15)
        return {"job": "j", "profile": "default", "operation": "in_place", "listed": True, "albums": out,
                "provider_notes": {}}

    def release(self, path=None):
        for p, g in self.gates.items():
            if path is None or p == str(path):
                g.set()


@unittest.skipIf(QApplication is None, "PySide6 not installed")
class PerfGui(Base):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        super().setUp()
        import gui
        __import__("app_settings").set_value("add_folder", "list")    # (listed, not looked up)
        self.top = Path(tempfile.mkdtemp(prefix="tm-perf-gui-"))
        self.first, self.other, self.third = (self.top / n for n in ("first", "other", "third"))
        for d in (self.first, self.other, self.third):
            for sub in ("A", "B", "C"):
                (d / sub).mkdir(parents=True)
        self.albums = {str(d): [entry(d / sub, f"{d.name}-{sub}") for sub in ("A", "B", "C")]
                       for d in (self.first, self.other, self.third)}
        self.win = gui.Main()
        self.win.resize(1180, 760)
        self.win.show()
        self.ov = self.win.pages["Overview"]
        settle(self.app)

    def tearDown(self):
        end = time.time() + 20
        while time.time() < end and self.win.threads():
            settle(self.app)
        self.win.close()
        self.win.deleteLater()
        settle(self.app)
        shutil.rmtree(self.top, ignore_errors=True)
        super().tearDown()

    def keys(self, albums=None):
        return [self.ov.album_key(a) for a in (self.ov.model.albums if albums is None else albums)]

    def wait_idle(self, timeout=15):
        until(self.app, lambda: not self.ov.running and not self.win.threads(), timeout)
        settle(self.app, 150)

    # ---- the listing's rows, as each album is read ---------------------------------------------
    def test_on_album_is_detected(self):
        from gui import overview

        class New:
            def listing(self, path, within=None, meta=None, on_album=None):
                return {"albums": [], "on": on_album is not None}

        class Old:
            def listing(self, path, within=None, meta=None):
                return {"albums": [], "on": False}
        seen = []
        self.assertTrue(overview._takes_on_album(New().listing))
        self.assertFalse(overview._takes_on_album(Old().listing))
        self.assertTrue(overview.listing(New(), "/x", on_album=seen.append)["on"])
        self.assertFalse(overview.listing(Old(), "/x", on_album=seen.append)["on"])   # (the list whole)

    def test_a_listing_shows_each_album_as_it_is_read_and_ends_as_its_list(self):
        import engine
        s = StandIn(self.albums, hold=[self.first])
        with mock.patch.object(engine.Engine, "listing", s.listing):
            self.assertTrue(self.ov.set_path(str(self.first)))
            # (the first album's row while the listing still runs: nothing in a plan yet)
            self.assertTrue(until(self.app, lambda: self.keys() == ["first-A"]))
            self.assertTrue(self.ov.running)
            self.assertIsNone(self.ov.plan)
            self.ov.table.selectRow(0)                                     # (you look at it meanwhile)
            s.release()
            self.wait_idle()
        self.assertEqual(self.keys(), ["first-A", "first-B", "first-C"])     # (each once, in its order)
        self.assertIs(self.ov.model.albums, self.ov.plan["albums"])           # (the listing's own list)
        self.assertEqual(self.keys(self.ov.plan["albums"]), ["first-A", "first-B", "first-C"])
        self.assertEqual(s.calls, [(str(self.first), True)])

    def test_an_engine_without_on_album_lists_as_before(self):
        import engine
        s = StandIn(self.albums, on_album=False)
        with mock.patch.object(engine.Engine, "listing", s.listing):
            self.ov.set_path(str(self.first))
            self.wait_idle()
        self.assertEqual(self.keys(self.ov.plan["albums"]), ["first-A", "first-B", "first-C"])
        self.assertIs(self.ov.model.albums, self.ov.plan["albums"])

    def test_a_folder_dropped_while_listing_shows_at_once_and_joins_once(self):
        import engine
        s = StandIn(self.albums, hold=[self.first])
        with mock.patch.object(engine.Engine, "listing", s.listing):
            self.ov.set_path(str(self.first))
            self.assertTrue(until(self.app, lambda: self.ov.running and self.keys() == ["first-A"]))
            start = time.monotonic()
            self.assertEqual(self.ov.add_later([str(self.other)]), [str(self.other)])
            self.assertTrue(until(self.app, lambda: {"other-A", "other-B", "other-C"} <= set(self.keys())))
            self.assertLess(time.monotonic() - start, 2.0)
            self.assertTrue(self.ov.running)                              # (the listing goes on)
            self.assertIsNone(self.ov.plan)                               # (nothing of them in its plan)
            s.release()
            self.wait_idle()
        keys = self.keys(self.ov.plan["albums"])
        self.assertEqual(keys, ["first-A", "first-B", "first-C", "other-A", "other-B", "other-C"])
        self.assertIs(self.ov.model.albums, self.ov.plan["albums"])
        self.assertEqual(self.ov.roots.get("other-A"), str(self.other))   # (read from its own folder)
        self.assertEqual((self.ov.later, self.ov._early, self.ov._provisional), ([], {}, {}))
        # (read once: not again when the listing ended)
        self.assertEqual([c for c in s.calls if c[0] == str(self.other)], [(str(self.other), True)])

    def test_cancel_while_listing_leaves_no_rows_of_a_folder_dropped_meanwhile(self):
        import engine
        s = StandIn(self.albums, hold=[self.first])
        with mock.patch.object(engine.Engine, "listing", s.listing):
            self.ov.set_path(str(self.first))
            self.assertTrue(until(self.app, lambda: self.ov.running and self.keys() == ["first-A"]))
            self.ov.add_later([str(self.other)])
            self.assertTrue(until(self.app, lambda: "other-C" in self.keys()))
            self.ov.cancel()
            self.assertFalse(any(k.startswith("other-") for k in self.keys()))
            s.release()
            self.wait_idle()
        self.assertEqual(self.keys(self.ov.plan["albums"]), ["first-A", "first-B", "first-C"])
        self.assertEqual(self.keys(), ["first-A", "first-B", "first-C"])
        self.assertEqual((self.ov.later, self.ov._early, self.ov._provisional), ([], {}, {}))

    def test_a_folder_dropped_during_a_look_up_shows_each_album_as_it_is_read(self):
        import engine
        s = StandIn(self.albums, hold=[self.other])
        with mock.patch.object(engine.Engine, "listing", s.listing):
            self.ov.set_path(str(self.first))
            self.wait_idle()
            __import__("app_settings").set_value("add_folder", "look_up")
            self.ov.looking = {"first-A": ("default", "profile")}     # (a look-up runs)
            self.ov.busy(True)
            self.ov._work_kind = "lookup"
            try:
                self.ov.add_later([str(self.other)])
                self.assertTrue(until(self.app, lambda: "other-A" in self.keys(self.ov.plan["albums"])))
                self.assertTrue(self.ov._reading)                    # (the rest not read yet)
                self.assertNotIn("other-B", self.keys())
                s.release()
                self.assertTrue(until(self.app, lambda: not self.ov._reading))
                settle(self.app, 150)
                self.assertEqual(self.keys(self.ov.plan["albums"]),
                                 ["first-A", "first-B", "first-C", "other-A", "other-B", "other-C"])
                # (queued, every one of them, the first shown included)
                self.assertEqual(sorted(self.ov.queue), ["other-A", "other-B", "other-C"])
            finally:
                self.ov.looking, self.ov.queue = {}, {}
                self.ov.busy(False)

    def test_a_folder_dropped_while_folders_are_added_is_read_at_once(self):
        import engine
        s = StandIn(self.albums, hold=[self.other])
        with mock.patch.object(engine.Engine, "listing", s.listing):
            self.ov.set_path(str(self.first))
            self.wait_idle()
            self.ov.add_to_list([str(self.other)])                     # (being read: "adding")
            self.assertTrue(until(self.app, lambda: self.ov.running and "other-A" in self.keys()))
            self.assertEqual(self.ov._work_kind, "adding")
            self.ov.add_later([str(self.third)])
            self.assertTrue(until(self.app, lambda: "third-C" in self.keys(self.ov.plan["albums"])))
            self.assertTrue(self.ov.running)                          # (before the adding ended)
            s.release()
            self.wait_idle()
        keys = self.keys(self.ov.plan["albums"])
        self.assertEqual(sorted(keys), sorted(f"{d}-{x}" for d in ("first", "other", "third") for x in "ABC"))
        self.assertEqual(len(keys), len(set(keys)))                     # (each once)
        self.assertEqual(self.ov.later, [])

    # ---- the columns ------------------------------------------------------------------------
    def many(self, n):
        return [dict(entry(self.top / f"m{i:04d}", f"m{i:04d}"), status="ready",
                     identification={"provider": "musicbrainz", "confidence": 90, "tier": "exact", "why": []})
                for i in range(n)]

    def test_columns_are_fitted_when_rows_come_not_on_each_change(self):
        from gui import overview
        ov = self.ov
        ov.views.setCurrentWidget(ov.results)
        settle(self.app, 200)                                              # (shown: its own fit done)
        ov.model.set_plan({"albums": self.many(200)})
        t = ov._fit_timer
        self.assertTrue(t.isActive())
        self.assertGreater(t.remainingTime(), overview.FIT_DELAY // 2)    # (a long list: not at once)
        settle(self.app, overview.FIT_DELAY + 200)
        self.assertFalse(t.isActive())
        ov.model.refresh_rows()                                            # (each row's data changed)
        ov.model.dataChanged.emit(ov.model.index(0, 0), ov.model.index(0, 7))
        self.assertFalse(t.isActive())
        ov.model.replace_rows([], self.many(201)[200:])                    # (a row came)
        self.assertTrue(t.isActive())
        before = t.remainingTime()
        ov.model.replace_rows([], [dict(self.many(1)[0], key="x", source_dir="/x")])
        self.assertLessEqual(t.remainingTime(), before)                     # (not put off by the next one)
        ov.model.set_plan({"albums": self.many(3)})                         # (a short list: at once)
        self.assertLessEqual(t.remainingTime(), 0)

    def test_the_profile_column_reads_the_profiles_once(self):
        from gui import overview
        ov = self.ov
        ov.views.setCurrentWidget(ov.results)
        ov.model.set_plan({"albums": self.many(150)})
        with mock.patch.object(overview, "profile_names", wraps=overview.profile_names) as names:
            ov.profile_delegate.refresh()
            ov.fit_columns()
            ov.fit_columns()
            self.assertEqual(names.call_count, 1)
            ov.refresh_profiles()                                          # (the profiles changed)
            ov.fit_columns()
            self.assertEqual(names.call_count, 3)                          # (refresh_profiles' own, then once)

    def test_a_column_you_sized_stays(self):
        from gui import overview
        ov = self.ov
        ov.views.setCurrentWidget(ov.results)
        ov.model.set_plan({"albums": self.many(100)})
        settle(self.app, overview.FIT_DELAY + 200)
        ov._header_held = True
        ov.table.horizontalHeader().resizeSection(1, 61)
        ov._header_held = False
        ov.model.replace_rows([], self.many(120)[100:])
        settle(self.app, overview.FIT_DELAY + 200)
        self.assertEqual(ov.table.columnWidth(1), 61)

    # ---- a big preview ---------------------------------------------------------------------
    def test_a_preview_of_2000_albums_opens_sorted_once(self):
        ov = self.ov
        ov.path = str(self.first)
        plan = {"job": "j", "profile": "default", "operation": "in_place", "albums": self.many(2000),
                "provider_notes": {}}
        ov.busy(True)                                                       # (as a preview ends)
        sorts = []                                     # (the rows there when a sort was asked for)
        real = ov.proxy.sort
        with mock.patch.object(ov.proxy, "sort", side_effect=lambda *a: (sorts.append(ov.model.rowCount()),
                                                                          real(*a))[1]):
            start = time.monotonic()
            ov.planned(plan)
            took = time.monotonic() - start
            settle(self.app, 300)
        self.assertLess(took, 2.0)
        self.assertEqual([n for n in sorts if n], [])    # (sorted as its rows came in, not again after)
        self.assertEqual(ov.proxy.rowCount(), 2000)
        self.assertTrue(ov.table.selectionModel().hasSelection())          # (the first row shown, after)
        self.assertIs(ov.selected(), ov.model.albums[ov.proxy.mapToSource(ov.proxy.index(0, 0)).row()])
        with mock.patch.object(ov.proxy, "setDynamicSortFilter") as dyn:
            ov.busy(False)                                                  # (as each selection does)
            ov.show_detail()
        dyn.assert_not_called()                                             # (no sorting again)

    # ---- a provider's back-off --------------------------------------------------------------
    def test_a_back_off_wait_is_said_counting_down(self):
        import re
        ov = self.ov
        ov.busy(True)
        try:
            th = threading.Thread(target=ov.back_off.report, args=("musicbrainz", 12.0))
            th.start()
            th.join()
            self.assertTrue(until(self.app, lambda: "Waiting for MusicBrainz" in self.win.statusBar().currentMessage()))
            self.assertRegex(self.win.statusBar().currentMessage(),
                             re.escape("Waiting for MusicBrainz (busy, retrying in ") + r"1[12] s\)")
            ov.backing_off("discogs", 0.3)
            self.assertIn("Discogs", self.win.statusBar().currentMessage())
            settle(self.app, 1300)
            self.assertNotIn("Waiting for", self.win.statusBar().currentMessage())   # (over: cleared)
            ov.backing_off("musicbrainz", 30)
        finally:
            ov.busy(False)
        self.assertEqual(ov.wait_words(), "")                               # (the work ended)

    def test_a_back_off_hook_is_listened_to_and_let_go(self):
        import stagetimes
        from gui import overview
        seen = []
        stop = overview.listen_for_back_off(lambda p, sec: seen.append((p, sec)))
        self.assertTrue(callable(stop))
        stagetimes.back_off("web", 2.0)
        stop()
        stagetimes.back_off("web", 2.0)
        self.assertEqual(seen, [("web", 2.0)])
        with mock.patch.object(stagetimes, "on_back_off", None):
            self.assertIsNone(overview.listen_for_back_off(print))

    def test_a_real_back_off_is_said_in_the_status_bar(self):
        """The matcher's own Http, refused once with a 503 (a stand-in urlopen: nothing is asked
        of the network): the page says the wait as the request's thread reports it."""
        import io
        import sqlite3
        import urllib.error
        import match_albums as ma
        import match_core as mc
        http = ma.Http(sqlite3.connect(":memory:", check_same_thread=False),
                       dict(mc.DEFAULT_CFG, mb_delay=0, discogs_delay=0), "")
        tries = []

        def urlopen(req, timeout=None):
            tries.append(1)
            if len(tries) == 1:
                raise urllib.error.HTTPError(req.full_url, 503, "busy", {"Retry-After": "20"}, None)
            return io.BytesIO(b'{"ok": 1}')
        out = []
        ov = self.ov
        ov.busy(True)
        try:
            with mock.patch.object(ma.urllib.request, "urlopen", urlopen), \
                    mock.patch.object(ma.Http, "_pause", lambda _s, _sec: None), \
                    mock.patch.object(ma, "_tell", lambda text: None):
                th = threading.Thread(target=lambda: out.append(
                    http.get_json("https://musicbrainz.org/ws/2/release/x?fmt=json", "mb")))
                th.start()
                th.join(10)
            self.assertEqual(out, [{"ok": 1}])
            self.assertTrue(until(self.app, lambda: "Waiting for" in self.win.statusBar().currentMessage()))
            self.assertRegex(self.win.statusBar().currentMessage(),
                             r"^Waiting for MusicBrainz \(busy, retrying in 2[01] s\)$")
        finally:
            ov.busy(False)

    def test_a_real_listing_streams_its_albums_into_the_list(self):
        """The engine's own listing of synthetic albums: each album's row comes as it is read
        (on_album), and the list ends as the listing's, each album once."""
        import engine
        from gui import overview
        from test_v0869_perf_listing import album
        lib = self.top / "lib"
        for i in range(4):
            album(lib / f"Band {i}" / f"2001 - Album {i}", f"Band {i}", f"Album {i}", n=2)
        streamed, real = [], engine.Engine.listing

        def listing(e, path, within=None, meta=None, on_album=None):
            def each(a):                                                   # (a share: each folder takes a while)
                streamed.append(a)
                on_album(a)
                time.sleep(0.25)
            return real(e, path, within=within, meta=meta, on_album=each if on_album else None)
        shown = []
        orig = overview.Overview.listed_some
        with mock.patch.object(engine.Engine, "listing", listing), \
                mock.patch.object(overview.Overview, "listed_some",
                                  lambda s_, albums: shown.append(len(albums)) or orig(s_, albums)):
            self.ov.set_path(str(lib))
            self.wait_idle()
        keys = self.keys(self.ov.plan["albums"])
        self.assertEqual(len(keys), 4)
        self.assertEqual(len(set(keys)), 4)
        self.assertEqual([self.ov.album_key(a) for a in streamed], keys)    # (the same albums, in order)
        self.assertGreater(sum(shown), 0)                                   # (rows came before its end)
        self.assertIs(self.ov.model.albums, self.ov.plan["albums"])

    # ---- review fixes -----------------------------------------------------------------------
    def test_a_folder_dropped_during_a_refile_waits_and_is_listed_once(self):
        import engine
        libx = self.top / "lib" / "X"
        libx.mkdir(parents=True)
        (libx / "01.flac").write_bytes(b"")
        self.albums[str(libx)] = [entry(libx, "lib-X"), dict(entry(libx, "lib-X2"), album_files=[str(libx / "02.flac")])]
        s = StandIn(self.albums, hold=[libx])
        with mock.patch.object(engine.Engine, "listing", s.listing):
            self.ov.set_path(str(self.first))
            self.wait_idle()
            a = self.ov.plan["albums"][0]
            a["status"] = "done"
            a["files"] = [{"src": a["album_files"][0], "dst": str(libx / "01.flac"), "op": "move"}]
            with mock.patch.object(type(self.ov), "look_up", lambda *_a, **_k: None):
                self.ov.refile([a], "")
                self.assertTrue(until(self.app, lambda: self.ov.running))
                self.assertEqual(self.ov.add_later([str(libx)]), [str(libx)])
                self.assertEqual(self.ov._reading, [])                    # (not read at once: it waits)
                s.release()
                self.wait_idle()
        keys = self.keys(self.ov.plan["albums"])
        self.assertEqual(len(keys), len(set(keys)), keys)
        self.assertIn("lib-X", keys)

    def test_refound_never_lists_an_album_twice(self):
        ov = self.ov
        ov.path = str(self.first)
        listed = entry(self.other / "A", "other-A")
        done = dict(entry(self.first / "A", "first-A"), status="done")
        ov.plan = {"albums": [done, listed], "listed": True}
        ov.model.set_plan(ov.plan)
        with mock.patch.object(type(ov), "look_up") as look_up:
            ov.refound([(done, str(self.other), [dict(listed), entry(self.other / "B", "other-B")])], "")
        self.assertEqual(self.keys(ov.plan["albums"]), ["other-B", "other-A"])
        self.assertEqual(sorted(ov.album_key(x) for x in look_up.call_args.args[1]), ["other-A", "other-B"])

    def test_a_folder_dropped_into_one_being_added_waits(self):
        import engine
        s = StandIn(self.albums, hold=[self.other])
        with mock.patch.object(engine.Engine, "listing", s.listing):
            self.ov.set_path(str(self.first))
            self.wait_idle()
            self.ov.add_to_list([str(self.other)])
            self.assertTrue(until(self.app, lambda: self.ov.running and "other-A" in self.keys()))
            self.ov.add_later([str(self.other / "B")])                   # (inside the folder being added)
            self.assertEqual(self.ov._reading, [])
            s.release()
            self.wait_idle()
        keys = self.keys(self.ov.plan["albums"])
        self.assertEqual(len(keys), len(set(keys)), keys)

    def test_an_early_read_cancelled_and_dropped_again_is_the_newer_one(self):
        from gui import overview
        ov = self.ov
        ov.path = str(self.first)
        calls = []
        with mock.patch.object(overview, "start", lambda _o, work, done, **kw: calls.append(done)):
            ov.busy(True)
            ov._work_kind = "listing"
            ov.later = [str(self.other)]
            self.assertTrue(ov.read_early([str(self.other)]))
            ov.worker = mock.Mock(cancelled=False)
            ov.cancel()                                                    # (the first read: let go)
            ov.worker = None
            ov.later = [str(self.other)]
            self.assertTrue(ov.read_early([str(self.other)]))
        old, new = calls
        f = str(self.other)
        stale = [(f, entry(self.other / "A", "stale-A"))]
        old(stale)                                                         # (the first read ends late)
        self.assertNotIn("stale-A", self.keys())
        self.assertIsNone(ov._early[f][1])                                 # (still the second one's)
        new([(f, entry(self.other / "A", "other-A"))])
        self.assertEqual(self.keys(), ["other-A"])
        ov.later, ov._early = [], {}
        ov.drop_provisional()
        ov.busy(False)

    def test_look_up_ending_beside_an_apply_leaves_the_rows_in_place(self):
        ov = self.ov
        ov.path = str(self.first)
        ov.plan = {"albums": self.many(5)}
        ov.model.set_plan(ov.plan)
        ov.busy(True)
        ov.apply_worker = object()                                         # (APL-1: an Apply beside it)
        try:
            with mock.patch.object(ov.table, "sortByColumn") as by_column, \
                    mock.patch.object(ov.proxy, "setDynamicSortFilter") as dyn:
                ov.look_up_ended()
            by_column.assert_not_called()
            dyn.assert_not_called()
            self.assertTrue(ov.running)
            with mock.patch.object(ov, "sort_by_status") as sort:
                ov.alongside_ended()                                       # (it ends: sorted once)
            sort.assert_called_once()
        finally:
            ov.apply_worker = None
            ov.busy(False)

    def test_apply_is_not_started_when_work_began_while_you_confirmed(self):
        import gui_screens
        from gui import overview
        ov = self.ov
        ov.path = str(self.first)
        ov.planned(gui_screens.sample_plan(self.top))
        settle(self.app, 100)
        self.assertTrue(ov.actionable())
        chosen = ov.selected_albums()

        def confirm(*_a, **_k):                                            # (a look-up starts meanwhile)
            ov.busy(True)
            return True
        try:
            with mock.patch.object(overview.sheets, "confirm", confirm), \
                    mock.patch.object(type(ov), "start_apply") as start_apply:
                ov.apply()
            start_apply.assert_not_called()
            self.assertIn("Not applied", self.win.statusBar().currentMessage())
            self.assertEqual(ov.selected_albums(), chosen)                  # (your selection kept)
        finally:
            ov.busy(False)

    # ---- a drop's paths, resolved per folder ------------------------------------------------
    def test_a_drop_is_held_through_a_link_to_a_folder_listed(self):
        from gui import manual_match
        import os
        link = self.top / "link"
        os.symlink(self.other, link)
        ov = self.ov
        ov.path = str(self.first)
        ov.plan = {"albums": [dict(entry(link / "A", "la"), album_files=[str(link / "A" / "01.flac")])]}
        new, present = manual_match.new_paths(ov, [str(self.other / "A"), str(self.other / "A" / "01.flac"),
                                                   str(self.first / "B"), str(self.third)])
        self.assertEqual(present, [str(self.other / "A"), str(self.other / "A" / "01.flac"), str(self.first / "B")])
        self.assertEqual(new, [str(self.third)])
        ov.plan = None

    def test_the_target_of_a_linked_album_file_is_held(self):
        import os
        from gui import manual_match
        elsewhere = self.top / "elsewhere"
        elsewhere.mkdir()
        target = elsewhere / "01.flac"
        target.write_bytes(b"")
        link = self.first / "A" / "01.flac"
        os.symlink(target, link)
        ov = self.ov
        ov.path = None
        ov.plan = {"albums": [{"source_dir": str(link.parent), "files": [{"src": str(link)}],
                               "album_files": [str(link)]}]}
        self.assertEqual(manual_match.new_paths(ov, [str(target)]), ([], [str(target)]))
        ov.plan = None


if __name__ == "__main__":
    unittest.main()
