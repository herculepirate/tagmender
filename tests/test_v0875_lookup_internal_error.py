# 0.8.75: a look-up stopped with "maximum recursion depth exceeded" (seen in the app, 2026-11-18).
# - An album changed since it was listed (a track added or renamed: its row's key is no album's
#   now) sent plan_each's scan round and round: _albums_of_keys fell back to eng.albums_in, which
#   in plan_each is the look-up's own stand-in, reading through _albums_of_keys again. One album
#   at a time it recursed without end; several at once it hung on the scan's lock. The album is
#   now read as it is and looked up under the key its row was listed with.
# - An internal error while one album is looked up or planned makes that album's row Unresolved
#   ("internal error: ..."), logged; the look-up goes on with the others.
# - A failure the app shows gives the frames nearest the error, repeats collapsed; the log has
#   the whole traceback.
# No network, generated audio.
import logging
import os
import subprocess
import sys
import threading
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_gui as tg  # noqa: E402  (sets the isolated config, offscreen Qt)
import test_engine as te  # noqa: E402
import engine  # noqa: E402
import plan as planmod  # noqa: E402
from test_gui import wait  # noqa: E402


def add_track(folder: Path, n: int, title: str, artist: str):
    subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "sine=frequency=900:duration=5",
                    "-ar", "8000", "-ac", "1", "-metadata", f"title={title}", "-metadata", f"album={title}",
                    "-metadata", f"albumartist={artist}", "-metadata", f"track={n}",
                    str(folder / f"{n:02d}.flac"), "-y"], check=True)


class CloningHttp(te.FakeHttp):
    """FakeHttp that plan_each may clone: several albums looked up at once."""
    def thread_clone(self):
        return self

    def close_clone(self):
        pass


class ChangedSinceListed(te.Base):
    def setUp(self):
        super().setUp()
        self.g = self.album("Band G", "Album G")
        self.h = self.album("Band H", "Album H")
        self.http = CloningHttp(dg=[te.dg_release(920, "Band G", "Album G"), te.dg_release(921, "Band H", "Album H")])

    def listed_then_changed(self, workers):
        e = self.engine(self.http)
        e.lookup_workers = workers
        keys = [a["key"] for a in e.listing(str(self.lib))["albums"]]
        add_track(self.g, 4, "Album G", "Band G")            # (a track added since it was listed)
        got, err = [], []

        def run():
            try:
                e.plan_each(str(self.lib), keys, lambda k, p: got.append((k, p)))
            except BaseException as exc:                  # (RecursionError before 0.8.75)
                err.append(exc)
        t = threading.Thread(target=run, daemon=True)
        t.start()
        t.join(60)
        self.assertFalse(t.is_alive(), "the look-up hung (the scan's lock taken twice)")
        self.assertEqual(err, [])
        return keys, dict(got)

    def check(self, keys, last):
        g_key = next(k for k in keys if "Album G" in k)
        self.assertEqual(sorted(last), sorted(keys))
        rows = last[g_key]["albums"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["key"], g_key)                # (its row: the one listed)
        self.assertEqual(len(rows[0]["album_files"]), 4)       # (read as it is now)

    def test_one_album_at_a_time(self):
        self.check(*self.listed_then_changed(1))

    def test_several_albums_at_once(self):
        self.check(*self.listed_then_changed(3))

    def test_albums_of_keys_never_asks_a_stand_in(self):
        e = self.engine(self.http)
        key = e.listing(str(self.lib))["albums"][0]["key"]
        e.albums_in = lambda p, skip=(): self.fail("the stand-in was asked")
        got = engine._albums_of_keys(e, str(self.lib), {key.replace("01.flac", "09.flac")})
        self.assertEqual(len(got), 2)


class OneAlbumsInternalError(te.Base):
    def setUp(self):
        super().setUp()
        self.album("Band G", "Album G")
        self.album("Band H", "Album H")
        self.http = CloningHttp(dg=[te.dg_release(920, "Band G", "Album G"), te.dg_release(921, "Band H", "Album H")])

    def run_each(self, workers, patch):
        e = self.engine(self.http)
        e.lookup_workers = workers
        keys = [a["key"] for a in e.listing(str(self.lib))["albums"]]
        got = []
        with patch, self.assertLogs("tagmender", "ERROR") as logs:
            n = e.plan_each(str(self.lib), keys, lambda k, p: got.append((k, p)))
        self.assertEqual(n, 2)
        last = dict(got)
        self.assertEqual(sorted(last), sorted(keys))
        rows = {k: last[k]["albums"] for k in keys}
        g = rows[next(k for k in keys if "Album G" in k)]
        h = rows[next(k for k in keys if "Album H" in k)]
        self.assertEqual([a["status"] for a in g], ["unresolved"])
        self.assertIn("internal error: maximum recursion depth exceeded", g[0]["identification"]["reason"])
        self.assertEqual([a["status"] for a in h], ["ready"])
        self.assertTrue(any("RecursionError" in x for x in logs.output), logs.output)   # (with its traceback)

    def looking_up_g_fails(self):
        real = engine.Engine.proposals

        def proposals(e, path, progress=None, cancel=None, only=None):
            if only and any("Album G" in k for k in only):
                raise RecursionError("maximum recursion depth exceeded")
            return real(e, path, progress, cancel, only=only)
        return mock.patch.object(engine.Engine, "proposals", proposals)

    def planning_g_fails(self):
        real = planmod.Planner._album

        def album(pl, prop):
            if "Album G" in str(prop.source_dir) and prop.files:
                raise RecursionError("maximum recursion depth exceeded")
            return real(pl, prop)
        return mock.patch.object(planmod.Planner, "_album", album)

    def test_looking_one_up_one_at_a_time(self):
        self.run_each(1, self.looking_up_g_fails())

    def test_looking_one_up_several_at_once(self):
        self.run_each(3, self.looking_up_g_fails())

    def test_planning_one(self):
        self.run_each(1, self.planning_g_fails())

    def test_planning_one_several_at_once(self):
        self.run_each(3, self.planning_g_fails())


def deep(n):
    return deeper(n)


def deeper(n):
    return deep(n + 1)


@unittest.skipIf(tg.QApplication is None, "PySide6 not installed")
class FailureShown(tg.Gui):
    def test_the_frames_nearest_the_error_are_shown_and_the_log_has_all(self):
        from gui import work
        seen = []
        with self.assertLogs("tagmender", "ERROR") as logs:
            self.gui.start(self.win, lambda w: deep(0), lambda r: None, failed=seen.append)
            self.assertTrue(wait(lambda: seen, self.app), "no failure")
        msg = seen[0]
        self.assertIn("maximum recursion depth exceeded", msg)
        self.assertIn("in deeper", msg)                        # (where it went wrong)
        self.assertRegex(msg, r"\[previous 2 lines repeated \d{3} more times\]")
        self.assertLess(msg.count("File "), work.SHOWN_FRAMES + 1)
        self.assertLess(len(msg), 4000)
        full = "\n".join(logs.output)
        self.assertGreater(full.count("in deeper"), 50)        # (the log: the whole traceback)
        self.assertIn("in run", full)

    def test_a_short_traceback_keeps_a_plain_one_as_it_is(self):
        from gui import work
        try:
            {}["x"]
        except KeyError as exc:
            text = work.short_traceback(exc)
        self.assertIn("test_a_short_traceback_keeps_a_plain_one_as_it_is", text)
        self.assertTrue(text.rstrip().endswith("KeyError: 'x'"))
        self.assertNotIn("repeated", text)


@unittest.skipIf(tg.QApplication is None, "PySide6 not installed")
class LookUpGoesOn(tg.Gui):
    def setUp(self):
        super().setUp()
        import app_settings
        app_settings.set_value("add_folder", "list")
        self.ov = self.win.pages["Overview"]
        self.ov.refresh_profiles()
        self.ov.profile.setCurrentText("Auto")
        self.ov.set_path(str(self.lib))
        self.assertTrue(wait(lambda: self.ov.plan is not None and not self.ov.running, self.app))

    def row(self, name):
        return next(a for a in self.ov.model.albums if name in a["source_dir"])

    def test_one_albums_internal_error_marks_its_row_and_the_others_are_looked_up(self):
        ov = self.ov
        real = engine.Engine.proposals

        def proposals(e, path, progress=None, cancel=None, only=None):
            if only and any("Album G" in k for k in only):
                raise RecursionError("maximum recursion depth exceeded")
            return real(e, path, progress, cancel, only=only)
        failed = []
        with mock.patch.object(engine.Engine, "proposals", proposals), \
                mock.patch.object(type(ov), "oops", lambda s, msg: failed.append(msg)), \
                self.assertLogs("tagmender", "ERROR"):
            ov.look_up("profile", list(ov.model.albums))
            self.assertTrue(wait(lambda: not ov.running, self.app, timeout=60), ov.summary.text())
        self.assertEqual(failed, [])
        g = self.row("Album G")
        self.assertEqual(g["status"], "unresolved")
        self.assertIn("internal error", g["identification"]["reason"])
        self.assertNotEqual(self.row("Album N")["status"], "listed")      # (looked up all the same)

    def test_an_error_the_engine_did_not_keep_to_one_album_still_leaves_rows(self):
        ov = self.ov

        def plan_each(e, path, keys, emit, **kw):
            emit(keys[0], engine.failed_plan(e, keys[0], RuntimeError("first"), log=False))
            raise RecursionError("maximum recursion depth exceeded")
        failed = []
        with mock.patch.object(engine.Engine, "plan_each", plan_each), \
                mock.patch.object(type(ov), "oops", lambda s, msg: failed.append(msg)), \
                self.assertLogs("tagmender", "ERROR"):
            ov.look_up("profile", list(ov.model.albums))
            self.assertTrue(wait(lambda: not ov.running, self.app, timeout=60), ov.summary.text())
        self.assertEqual(failed, [])
        g, n = self.row("Album G"), self.row("Album N")
        self.assertEqual(g["status"], "unresolved")
        self.assertIn("internal error: first", g["identification"]["reason"])
        self.assertNotEqual(n["status"], "listed")                       # (a row for it all the same)
        self.assertIn("internal error: maximum recursion", n["identification"]["reason"])

    def test_cancel_still_stops_a_look_up(self):
        ov = self.ov
        gate, entered = threading.Event(), threading.Event()
        real = engine.Engine.proposals

        def proposals(e, path, progress=None, cancel=None, only=None):
            entered.set()
            gate.wait(30)
            if only and any("Album G" in k for k in only):
                raise RecursionError("maximum recursion depth exceeded")
            return real(e, path, progress, cancel, only=only)
        failed = []
        with mock.patch.object(engine.Engine, "proposals", proposals), \
                mock.patch.object(type(ov), "oops", lambda s, msg: failed.append(msg)), \
                mock.patch.object(engine, "LOOKUP_WORKERS", 1):
            ov.look_up("profile", list(ov.model.albums))
            self.assertTrue(entered.wait(30))
            ov.cancel()
            gate.set()
            self.assertTrue(wait(lambda: not ov.running, self.app, timeout=60), ov.summary.text())
        self.assertEqual(failed, [])
        self.assertTrue(any(a["status"] == "listed" for a in ov.model.albums))   # (not reached: as they were)


@unittest.skipIf(tg.QApplication is None, "PySide6 not installed")
class RefreshAfterApply(tg.Gui):
    """The app's way into it: albums applied (moved away) stay in the list as Done; Refresh
    (Ctrl+R) looks every row up again - an applied one's folder, gone, holds none of its album."""
    def test_refresh_with_applied_albums_in_the_list(self):
        import app_settings
        import profiles as pf
        app_settings.set_value("add_folder", "list")
        k = self.album("Band K", "Album K")
        dest = self.tmp / "library"
        dest.mkdir()
        pf.save(pf.from_dict({"name": "Mover", "operation": "move", "destination": str(dest),
                              "providers": {"web": {"enabled": False}}, "artwork": {"enabled": False}}))
        ov = self.win.pages["Overview"]
        ov.refresh_profiles()
        ov.profile.setCurrentText("Mover")
        ov.set_path(str(self.d))                                   # (one folder, then another added)
        self.assertTrue(wait(lambda: ov.plan is not None and not ov.running, self.app))
        ov.add_to_list([str(k)])
        self.assertTrue(wait(lambda: len(ov.model.albums) == 2 and not ov.running, self.app, timeout=60))
        ov.look_up("profile", list(ov.model.albums))
        self.assertTrue(wait(lambda: not ov.running, self.app, timeout=60), ov.summary.text())
        row = lambda name: next(a for a in ov.model.albums if name in a["source_dir"])   # noqa: E731
        self.assertEqual(row("Album G")["status"], "ready")
        k_was = row("Album K")["status"]
        ov.model.set_left_out([row("Album K")], True)               # (Album G alone applied)
        failed = []
        with mock.patch.object(type(ov), "oops", lambda s, msg: failed.append(msg)), \
                mock.patch.object(self.gui.sheets, "confirm", return_value=True), \
                mock.patch.object(self.win, "show_run_report"):
            ov.apply()
            self.assertTrue(wait(lambda: not ov.running, self.app, timeout=60), ov.summary.text())
            self.assertEqual(row("Album G")["status"], "done")
            self.assertFalse(self.d.exists())                      # (moved away)
            ov.table.clearSelection()
            ov.reload()                                            # (Refresh: every row again)
            self.assertTrue(ov.running)
            self.assertTrue(wait(lambda: not ov.running, self.app, timeout=60), ov.summary.text())
        self.assertEqual(failed, [])                               # (0.8.74: maximum recursion depth exceeded)
        self.assertEqual(row("Album G")["status"], "done")         # (its row stays as it was)
        self.assertEqual(row("Album K")["status"], k_was)            # (looked up again, as before)

# (the window's own tests are test_gui's: not run again here)
for _cls in (FailureShown, LookUpGoesOn, RefreshAfterApply):
    for _n in dir(tg.Gui):
        if _n.startswith("test_"):
            setattr(_cls, _n, None)


if __name__ == "__main__":
    logging.disable(logging.NOTSET)
    unittest.main()
