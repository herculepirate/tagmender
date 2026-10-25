# Watched folders, first independent review (of d662160): each test failed before its fix.
import os
import sys
import time
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "tests"))
import test_watch_folders as tw  # noqa: E402  (sets XDG dirs; DropBase builds real FLAC drops)
import executor  # noqa: E402
import profiles as pf  # noqa: E402
import watcher  # noqa: E402

LATER = time.time() + 7200


class H(tw.DropBase):
    def later(self, secs=7200):
        return mock.patch.object(watcher.time, "time", return_value=time.time() + secs)


class Review(H):

    # 1. an approved plan that could not be carried out leaves the album stuck as "planned"
    def test_failed_approval_is_planned_again(self):
        prof = self.profile(unattended=False)
        d = self.drop("Band X", "Album X")
        w = watcher.Watcher([prof], once=True)
        self.assertEqual(list(w.tick().values()), ["planned"])
        [p] = watcher.pending_plans()
        self.dest.rmdir()                                   # the library drive is out when approving
        res = watcher.apply_pending(p["path"])
        self.assertEqual(res["albums"][0]["status"], "conflict", res)
        self.assertEqual(watcher.pending_plans(), [])        # the plan is gone from the list
        self.dest.mkdir()                                   # drive back
        with self.later():
            res = w.tick()
        self.assertTrue(d.exists())
        # BUG: nothing plans the album again; it sits in the inbox for ever, nothing waiting
        self.assertTrue(res or watcher.pending_plans(),
                        f"album never planned again; processed row: {watcher.recent(5)}")

    # 2. an asks-first album that is unresolved / hit a provider outage gets a NEW pending
    #    plan on every retry (the old ones stay in the list)
    def test_unresolved_asks_first_does_not_pile_up_plans(self):
        prof = self.profile(unattended=False)
        self.drop("Nobody", "Nothing Known")
        w = watcher.Watcher([prof], once=True)
        r1 = w.tick()
        n1 = len(watcher.pending_plans())
        with self.later(prof["retry"]["after_days"] * 86400 + 7200):
            r2 = w.tick()
        n2 = len(watcher.pending_plans())
        self.assertLessEqual(n2, max(n1, 1), f"pass 1 {r1} -> {n1} plan(s); retry {r2} -> {n2} plan(s)")

    # 3. watch --add FOLDER --profile <typo> takes the folder away from the profile watching it
    def test_cli_add_with_unknown_profile_keeps_the_existing_watch(self):
        self.profile()                                      # "Inbox" watches self.inbox
        import contextlib
        import io
        import tagmender
        with contextlib.redirect_stderr(io.StringIO()), contextlib.redirect_stdout(io.StringIO()):
            code = tagmender.main(["watch", "--add", str(self.inbox), "--profile", "Inbx"])
        self.assertNotEqual(code, 0)
        watched = [s for s in pf.load("Inbox")["sources"] if s.get("watch")]
        self.assertEqual(len(watched), 1, "the failed --add removed the folder from profile Inbox")

    # 4. tag-in-place profile that asks first: the approved album is identified again
    def test_in_place_approved_album_is_not_identified_again(self):
        prof = self.profile(unattended=False, operation="in_place", destination="")
        self.drop("Band X", "Album X")
        w = watcher.Watcher([prof], once=True)
        self.assertEqual(list(w.tick().values()), ["planned"])
        [p] = watcher.pending_plans()
        res = watcher.apply_pending(p["path"])
        self.assertEqual(res["albums"][0]["status"], "done", res)
        calls = len(self.http.calls)
        with self.later():
            again = w.tick()
        self.assertEqual(again, {}, "approved album taken for a new drop")
        self.assertEqual(len(self.http.calls), calls)


class ServiceExit(unittest.TestCase):
    # 5. `tagmender watch` that finds another watcher exits 0: under systemd Restart=on-failure
    #    the service is then dead for good (and install_service already said "running")
    def test_second_watcher_exit_code(self):
        import contextlib
        import io
        import tagmender
        with watcher.run_lock() as held:
            self.assertTrue(held)
            with mock.patch.object(watcher.Watcher, "_watched_profiles", return_value=[]), \
                    contextlib.redirect_stderr(io.StringIO()):
                code = tagmender.main(["watch"])
        self.assertNotEqual(code, 0, "exits 0: systemd Restart=on-failure never retries the service")




class Race(H):
    # 6. a pass that began before an approval is applied (GUI: timer check + Apply; CLI:
    #    service pass + `watch --approve`) looks at the half-moved album and plans it again
    def test_pass_running_while_a_plan_is_applied(self):
        import threading
        prof = self.profile(unattended=False)
        d = self.drop("Band X", "Album X")
        w = watcher.Watcher([prof], once=True)
        self.assertEqual(list(w.tick().values()), ["planned"])
        [p] = watcher.pending_plans()
        moved, go = threading.Event(), threading.Event()
        real_step, real_due = executor.Executor.file_step, watcher.Watcher.due

        def step(self_, i, j, fe, a, done_steps):
            real_step(self_, i, j, fe, a, done_steps)
            if j == 0:
                moved.set()
                go.wait(30)
        out = {}

        def due(self_, *a, **kw):              # the user presses Apply right after the pass began
            th = threading.Thread(target=lambda: out.setdefault("res", watcher.apply_pending(p["path"])))
            th.start()
            moved.wait(3)                  # (the approval waits for this pass now)
            self_._th = th
            return real_due(self_, *a, **kw)
        with mock.patch.object(executor.Executor, "file_step", step), \
                mock.patch.object(watcher.Watcher, "due", due), self.later():
            res = w.tick()
            go.set()
            w._th.join(30)
        self.assertEqual(out["res"]["albums"][0]["status"], "done")
        self.assertFalse(d.exists())
        left = watcher.pending_plans()
        self.assertEqual((res, left), ({}, []),
                         f"pass saw the half-moved album: {res}; plans waiting now: "
                         f"{[x['albums'] for x in left]}")



class Inside(H):
    # 7. an existing library inside the watched folder: everything already in it is taken for
    #    a new drop on the first pass (identified, retagged, moved) - only what a watcher job
    #    filed is remembered
    def test_existing_library_inside_the_watched_folder_is_left_alone(self):
        self.dest = self.inbox / "Library"
        self.dest.mkdir()
        prof = self.profile()
        old = self.drop("Band Y", "Album Y", where=self.dest)    # was in the library before watching
        tw.settle(self.inbox)
        res = watcher.Watcher([prof], once=True).tick()
        self.assertEqual(res, {}, f"the existing library album was processed: {res}")

    # 8. unresolved albums moved to an "Unresolved" folder inside the watched folder are
    #    identified again on every later pass
    def test_unresolved_moved_inside_the_watched_folder_not_reidentified(self):
        unres = self.inbox / "Unresolved"
        prof = self.profile(unresolved={"action": "move", "move_to": str(unres), "allow_partial": False})
        unres.mkdir()
        self.drop("Nobody", "Nothing Known")
        w = watcher.Watcher([prof], once=True)
        r1 = w.tick()
        where = sorted(str(p.relative_to(self.inbox)) for p in self.inbox.rglob("*.flac"))
        calls = len(self.http.calls)
        with self.later():
            r2 = w.tick()
        with self.later(14400):
            r3 = w.tick()
        where3 = sorted(str(p.relative_to(self.inbox)) for p in self.inbox.rglob("*.flac"))
        self.assertEqual((r2, r3, len(self.http.calls)), ({}, {}, calls),
                         f"pass1 {r1} files {where}; pass2 {r2}; pass3 {r3}; files now {where3}")


try:
    from PySide6.QtWidgets import QApplication
except ImportError:                                            # GUI is optional
    QApplication = None


@unittest.skipIf(QApplication is None, "PySide6 not installed")
class WatchedPage(unittest.TestCase):
    def setUp(self):
        import tempfile
        import gui
        self.gui = gui
        self.app = QApplication.instance() or QApplication([])
        self.tmp = Path(tempfile.mkdtemp())
        (self.tmp / "inbox").mkdir()
        (self.tmp / "lib").mkdir()
        pf.save(pf.from_dict({"name": "Mover", "operation": "move", "destination": str(self.tmp / "lib"),
                              "sources": [{"path": str(self.tmp / "inbox"), "watch": True}],
                              "watch": {"interval_seconds": 600}}))
        p = mock.patch.object(watcher.Watcher, "tick", lambda self: {})
        p.start()
        self.addCleanup(p.stop)
        self.win = gui.Main()
        self.addCleanup(self.settle_threads)

    def settle_threads(self):
        import time as _t
        end = _t.time() + 30
        while _t.time() < end and any(self.gui._alive(th) for pg in self.win.pages.values()
                                      for th, _w in getattr(pg, "_threads", [])):
            self.app.processEvents()
            _t.sleep(0.02)
        self.win.close()

    # 9. "Look every" changed in the dialog reaches the running timer
    def test_look_every_change_reaches_the_running_timer(self):
        wp = self.win.pages["Watched folders"]
        wp.live.setChecked(True)
        self.assertEqual(wp.timer.interval(), 600_000)
        dlg = self.gui.WatchDialog(wp, str(self.tmp / "inbox"), "Mover")
        dlg.every.setValue(1)
        dlg.save()
        wp.reload()
        self.assertEqual(pf.load("Mover")["watch"]["interval_seconds"], 60)
        self.assertEqual(wp.timer.interval(), 60_000)
        wp.live.setChecked(False)

    # 7b. the dialog: a profile that refuses the folder changes no profile
    def test_dialog_failure_keeps_the_existing_watch(self):
        pf.save(pf.from_dict({"name": "Broken", "operation": "move", "destination": str(self.tmp / "lib")}))
        wp = self.win.pages["Watched folders"]
        dlg = self.gui.WatchDialog(wp, str(self.tmp / "inbox"), "Mover")
        dlg.prof.setCurrentText("Broken")
        dlg.path.setText("/")                         # a whole drive: refused by the profile check
        with mock.patch.object(self.gui.QMessageBox, "warning") as warned, \
                mock.patch.object(self.gui.QMessageBox, "information"):
            dlg.save()
        self.assertTrue(warned.called)
        self.assertEqual([s["path"] for s in pf.load("Mover")["sources"] if s.get("watch")], [str(self.tmp / "inbox")])


class CloseMidCheck(unittest.TestCase):
    # 4. closing the app while a watched-folder pass runs waits for it (it was killed: SIGABRT)
    def test_close_waits_for_the_pass(self):
        import subprocess
        import tempfile
        import textwrap
        if QApplication is None:
            self.skipTest("PySide6 not installed")
        inbox = tempfile.mkdtemp()
        code = textwrap.dedent(f"""
            import os, sys, time
            sys.path.insert(0, {str(REPO)!r})
            from PySide6.QtWidgets import QApplication
            from PySide6.QtCore import QTimer
            import profiles as pf, watcher, gui
            pf.save(pf.from_dict({{"name": "W", "sources": [{{"path": {inbox!r}, "watch": True}}]}}))
            def slow_tick(self):
                print("tick started", flush=True)
                time.sleep(3)
                print("tick finished", flush=True)
                return {{}}
            watcher.Watcher.tick = slow_tick
            app = QApplication([])
            win = gui.Main()
            win.show()
            win.pages["Watched folders"].live.setChecked(True)
            QTimer.singleShot(500, win.close)
            sys.exit(app.exec())
        """)
        env = dict(os.environ, XDG_CONFIG_HOME=tempfile.mkdtemp(), XDG_STATE_HOME=tempfile.mkdtemp(),
                   QT_QPA_PLATFORM="offscreen")
        p = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True, timeout=120)
        self.assertEqual(p.returncode, 0, p.stderr[-800:])
        self.assertIn("tick finished", p.stdout)
        self.assertNotIn("Destroyed while thread", p.stderr)


if __name__ == "__main__":
    unittest.main()
