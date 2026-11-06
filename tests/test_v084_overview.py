# 0.8.4 Overview fixes (offscreen Qt, fake providers, generated audio):
# - the toolbar's count ("1 of 15") is a label beside the thin progress bar, wide enough for it;
# - Look Up asked while a look-up runs is queued and starts when it ends (Cancel clears the queue;
#   an album already running or queued is not asked twice; a changed album profile is queued);
# - each album's row shows what was found as soon as that album is looked up, not at the end;
# - while it runs, a row says Waiting…, Looking up… or Applying…; Apply says which file of which album.
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
import profiles as pf  # noqa: E402
from test_gui import wait  # noqa: E402

SCRATCH = os.environ.get("TAGMENDER_SHOTS", "")


@unittest.skipIf(tg.QApplication is None, "PySide6 not installed")
class V084Overview(tg.Gui):
    def setUp(self):
        super().setUp()
        import app_settings
        app_settings.set_value("add_folder", "list")
        self.d3 = self.album("Band H", "Album H")
        self.http.dg[922] = te.dg_release(922, "Band H", "Album H")
        self.dest = self.tmp / "library"
        self.dest.mkdir()
        pf.save(pf.from_dict({"name": "Mover", "operation": "copy", "destination": str(self.dest),
                              "providers": {"web": {"enabled": False}}, "artwork": {"enabled": False}}))
        self.ov = self.win.pages["Overview"]
        self.ov.refresh_profiles()
        self.ov.profile.setCurrentText("Auto")
        # (an album's look-up waits at its gate until the test opens it)
        self.gates = {}
        real = engine.Engine.identify_all

        def gated(e, g):
            for name, gate in self.gates.items():
                if name in str(g.folder):
                    gate.wait(30)
            return real(e, g)
        p = mock.patch.object(engine.Engine, "identify_all", gated)
        p.start()
        self.addCleanup(p.stop)
        self.addCleanup(lambda: [g.set() for g in self.gates.values()])

    def gate(self, name):
        self.gates[name] = threading.Event()
        return self.gates[name]

    def row(self, name):
        return next(a for a in self.ov.model.albums if name in a["source_dir"])

    def listed(self):
        ov = self.ov
        ov.set_path(str(self.lib))
        self.assertTrue(wait(lambda: ov.plan is not None and not ov.running, self.app), ov.summary.text())
        self.assertEqual(len(ov.model.albums), 3)

    def test_a_look_up_asked_while_one_runs_is_queued_and_runs_after_it(self):
        ov = self.ov
        self.listed()
        g = self.gate("Album G")
        ov.look_up("profile", [self.row("Album G")])
        self.assertTrue(ov.running)
        ov.look_up("profile", [self.row("Album H")])                   # (the button, while it runs)
        ov.look_up("profile", [self.row("Album H"), self.row("Album G")])   # (neither asked twice)
        self.assertEqual(list(ov.queue), [ov.album_key(self.row("Album H"))])
        self.assertIn("1 album queued", ov.summary.text())
        g.set()
        self.assertTrue(wait(lambda: not ov.running and self.row("Album H")["status"] != "listed", self.app),
                        ov.summary.text())
        self.assertEqual(self.row("Album G")["status"], "ready")
        self.assertEqual(self.row("Album H")["status"], "ready")
        self.assertEqual(ov.queue, {})
        self.assertEqual(len(ov.model.albums), 3)

    def test_refresh_while_running_is_queued_too_and_other_work_still_refuses(self):
        ov = self.ov
        self.listed()
        g = self.gate("Album G")
        ov.look_up("profile", [self.row("Album G")])
        ov.table.clearSelection()
        ov.reload()                                                     # (Refresh: every album, again)
        self.assertEqual(set(ov.queue), {ov.album_key(a) for a in ov.model.albums} -
                         {ov.album_key(self.row("Album G"))})
        self.assertFalse(ov.set_path(str(self.d)))                      # (a new folder: not while it runs)
        g.set()
        self.assertTrue(wait(lambda: not ov.running and not ov.queue and
                             all(a["status"] != "listed" for a in ov.model.albums), self.app), ov.summary.text())

    def test_cancel_clears_the_queue(self):
        ov = self.ov
        self.listed()
        g = self.gate("Album G")
        ov.look_up("profile", [self.row("Album G")])
        ov.look_up("profile", [self.row("Album H")])
        self.assertTrue(ov.queue)
        ov.cancel()
        self.assertEqual(ov.queue, {})
        self.assertIn("queued are not looked up", ov.summary.text())
        g.set()
        self.assertTrue(wait(lambda: not ov.running, self.app), ov.summary.text())
        self.app.processEvents()
        self.assertFalse(ov.running)
        self.assertEqual(self.row("Album H")["status"], "listed")

    def test_each_row_is_shown_as_soon_as_its_album_is_looked_up(self):
        ov = self.ov
        self.listed()
        h = self.gate("Album H")
        ov.table.selectRow(ov.proxy.mapFromSource(ov.model.index(ov.model.albums.index(self.row("Album G")), 0)).row())
        ov.look_up("profile", [self.row("Album G"), self.row("Album H")])
        # (Album G found while Album H still waits: its row, and the inspector, show it)
        self.assertTrue(wait(lambda: self.row("Album G")["status"] == "ready", self.app, timeout=30),
                        ov.summary.text())
        self.assertTrue(ov.running)
        self.assertEqual(self.row("Album H")["status"], "listed")
        self.assertIs(ov.selected(), self.row("Album G"))
        self.assertIs(self.win.inspector.album, self.row("Album G"))
        self.assertTrue(self.win.commands["apply"].isEnabled())          # (0.8.6: Apply pauses the look-up)
        self.assertEqual(ov.planned_with[ov.album_key(self.row("Album G"))], "Auto")
        h.set()
        self.assertTrue(wait(lambda: not ov.running, self.app), ov.summary.text())
        self.assertEqual(self.row("Album H")["status"], "ready")
        self.assertEqual(len(ov.model.albums), 3)
        self.assertTrue(self.win.commands["apply"].isEnabled())
        self.assertIn("Ready", ov.summary.text())

    def test_a_release_chosen_for_one_album_looks_up_that_album_alone(self):
        ov = self.ov
        self.listed()
        ov.look_up("profile", list(ov.plan["albums"]))
        self.assertTrue(wait(lambda: not ov.running, self.app))
        seen = []
        real = engine.Engine.plan_each

        def plan_each(e, path, keys, *a, **k):
            seen.append((list(keys), dict(getattr(e, "force", {}) or {})))
            return real(e, path, keys, *a, **k)
        g = self.row("Album G")
        ov.table.selectRow(ov.proxy.mapFromSource(ov.model.index(ov.model.albums.index(g), 0)).row())
        with mock.patch.object(engine.Engine, "plan_each", plan_each), \
                mock.patch.object(self.gui.sheets, "get_text", return_value=("discogs:920", True)):
            ov.choose_release()
            self.assertTrue(wait(lambda: not ov.running, self.app))
        self.assertEqual([keys for keys, _f in seen], [[ov.album_key(g)]])        # (not all the albums)
        self.assertIn(os.path.realpath(g["source_dir"]), seen[0][1])               # (the release chosen)
        self.assertEqual(len(ov.model.albums), 3)

    def test_albums_planned_singly_still_get_folders_of_their_own(self):
        """One planner plans the albums of a look-up, as one plan would: plan_each's albums are
        the same as Engine.plan's."""
        ov = self.ov
        self.listed()
        keys = [ov.album_key(a) for a in ov.model.albums]
        prof = pf.load("Mover")
        whole = engine.Engine(prof).plan(str(self.lib), only=set(keys))
        e, got = engine.Engine(prof), []
        from gui import overview
        n = overview.plan_each(e, str(self.lib), keys, lambda k, p: got.append((k, p)))
        self.assertEqual(n, 3)
        last = dict(got)                                  # (each album: shown at once, then from one plan)
        self.assertEqual(sorted(last), sorted(keys))
        self.assertEqual(len(got), 2 * len(keys))
        dst = lambda albums: sorted(f["dst"] for a in albums for f in a["files"])    # noqa: E731
        self.assertEqual(dst(a for p in last.values() for a in p["albums"]), dst(whole["albums"]))
        self.assertEqual({p["job"] for p in last.values()}, {e.job})

    def test_queued_albums_keep_their_own_profile(self):
        ov = self.ov
        self.listed()
        g = self.gate("Album G")
        ov.look_up("profile", [self.row("Album G")])
        self.assertFalse(ov.model.locked)                                # (an album's profile: chosen now)
        ov.set_album_profile([self.row("Album H")], "Mover")             # (not looked up yet: not queued)
        self.assertEqual(ov.queue, {})
        ov.look_up("profile", [self.row("Album H")])
        ov.set_album_profile([self.row("Album G")], "Mover")             # (running with Auto: again with Mover)
        self.assertIn(ov.album_key(self.row("Album G")), ov.queue)
        g.set()
        self.assertTrue(wait(lambda: not ov.running and not ov.queue and
                             self.row("Album H")["status"] != "listed", self.app), ov.summary.text())
        h, gg = self.row("Album H"), self.row("Album G")
        self.assertEqual((ov.planned_with[ov.album_key(h)], ov.planned_with[ov.album_key(gg)]), ("Mover", "Mover"))
        self.assertTrue(all(f["dst"].startswith(str(self.dest)) for f in h["files"]), h["files"])
        self.assertTrue(all(f["dst"].startswith(str(self.dest)) for f in gg["files"]), gg["files"])
        self.assertEqual(ov.stale_profiles(ov.to_apply()), [])

    def status(self, name):
        ov = self.ov
        return ov.model.data(ov.model.index(ov.model.albums.index(self.row(name)), 0))

    def test_rows_say_waiting_and_looking_up_until_found(self):
        ov = self.ov
        self.listed()
        g = self.gate("Album G")
        ov.look_up("profile", [self.row("Album G"), self.row("Album H")])
        self.assertTrue(wait(lambda: self.status("Album G") == "Looking up…", self.app, timeout=30))
        self.assertEqual(self.status("Album H"), "Waiting…")
        n = next(a for a in ov.model.albums if "Album N" in a["source_dir"])
        ov.look_up("profile", [n])                                       # (queued: waiting too)
        self.assertEqual(self.status("Album N"), "Waiting…")
        g.set()
        self.assertTrue(wait(lambda: not ov.running, self.app), ov.summary.text())
        self.assertEqual((self.status("Album G"), self.status("Album H")), ("Ready", "Ready"))
        self.assertEqual(ov.states, {})
        import json                                                      # (never in the plan)
        self.assertNotIn("Waiting", json.dumps(ov.plan, default=str))
        self.assertNotIn("Looking up", json.dumps(ov.plan, default=str))

    def test_cancel_clears_the_waiting_rows(self):
        ov = self.ov
        self.listed()
        g = self.gate("Album G")
        ov.look_up("profile", [self.row("Album G"), self.row("Album H")])
        self.assertTrue(wait(lambda: self.status("Album G") == "Looking up…", self.app, timeout=30))
        ov.cancel()
        self.assertEqual(self.status("Album H"), "Not looked up")
        g.set()
        self.assertTrue(wait(lambda: not ov.running, self.app), ov.summary.text())
        self.assertEqual(ov.states, {})

    def test_apply_says_which_album_and_which_file(self):
        import executor
        ov = self.ov
        self.listed()
        ov.look_up("profile", list(ov.plan["albums"]))
        self.assertTrue(wait(lambda: not ov.running, self.app), ov.summary.text())
        gate, step = threading.Event(), executor.Executor.file_step
        calls, seen = [], []

        def slow(e, i, j, fe, a, done):
            calls.append(j)
            if len(calls) == 2:
                gate.wait(30)                            # (the second file of the first album)
            return step(e, i, j, fe, a, done)
        real = ov.file_progress
        with mock.patch.object(executor.Executor, "file_step", slow), \
                mock.patch.object(ov, "file_progress", side_effect=lambda *a: (seen.append(a[:3]), real(*a))), \
                mock.patch.object(self.gui.sheets, "confirm", return_value=True), \
                mock.patch.object(self.win, "show_run_report"):
            ov.apply()
            self.assertTrue(wait(lambda: len(calls) == 2, self.app, timeout=30))
            self.assertTrue(wait(lambda: "file 2 of 3" in ov.summary.text(), self.app, timeout=10), ov.summary.text())
            first = Path(ov.plan["albums"][ov._applied_rows[0]]["source_dir"]).name
            second = Path(ov.plan["albums"][ov._applied_rows[1]]["source_dir"]).name
            self.assertIn(f"Applying album 1 of 2: {first} — file 2 of 3: ", ov.summary.text())
            self.assertEqual(self.status(first.split(" - ")[1]), "Applying…")
            self.assertEqual(self.status(second.split(" - ")[1]), "Waiting…")
            gate.set()
            self.assertTrue(wait(lambda: not ov.running, self.app), ov.summary.text())
        self.assertEqual(seen, [(0, 0, 3), (0, 1, 3), (0, 2, 3), (1, 0, 3), (1, 1, 3), (1, 2, 3)])
        self.assertEqual(ov.states, {})
        self.assertNotIn("Applying", self.status("Album G"))

    def test_the_executor_says_each_file_before_its_step(self):
        import executor
        e = engine.Engine(pf.load("Auto"))
        plan = e.plan(str(self.d))
        said = []
        res = executor.Executor(plan, file_progress=lambda *a: said.append(a)).run()
        self.assertEqual(res["status"], "done", res)
        files = [fe["src"] for fe in plan["albums"][0]["files"] if fe["op"] != "none"]
        self.assertEqual(said, [(0, j, len(files), f) for j, f in enumerate(files)])
        self.assertEqual(len(files), 3)

    def test_the_progress_count_fits_beside_the_bar(self):
        ov = self.ov
        self.listed()
        win = self.win
        for width in (1180, 700):
            win.resize(width, 600)
            ov.busy(True)
            ov.progress(0, 15, str(self.d))
            ov.progress(13, 150, str(self.d))
            self.app.processEvents()
            lb = win.progress_count
            self.assertFalse(win.progress.isTextVisible())               # (6 px high: no words inside)
            self.assertEqual(lb.text(), "14 of 150")
            self.assertTrue(lb.isVisibleTo(win))
            self.assertGreaterEqual(lb.width(), lb.fontMetrics().horizontalAdvance(lb.text()))
            self.assertGreaterEqual(win.progress_box.height(), lb.fontMetrics().height())
            if SCRATCH:
                Path(SCRATCH).mkdir(parents=True, exist_ok=True)
                win.grab().save(str(Path(SCRATCH) / f"v084-progress-{width}.png"))
            ov.busy(False)
            self.assertEqual(lb.text(), "")


for _n in dir(tg.Gui):                                  # (the base class's own tests run there)
    if _n.startswith("test_"):
        setattr(V084Overview, _n, None)


if __name__ == "__main__":
    unittest.main()
