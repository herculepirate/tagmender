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


def mixed_folder(mix: Path):
    """A folder holding two albums of loose tracks, Album X (x1-x3) and Album Y (y1-y3)."""
    mix.mkdir(parents=True, exist_ok=True)
    for who, n in (("X", 1), ("X", 2), ("X", 3), ("Y", 1), ("Y", 2), ("Y", 3)):
        subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i",
                        f"sine=frequency={300 + 70 * n + (0 if who == 'X' else 500)}:duration={4 + n}",
                        "-ar", "8000", "-ac", "1", "-metadata", f"title={who}{n}",
                        "-metadata", f"album=Album {who}", "-metadata", f"albumartist=Band {who}",
                        "-metadata", f"artist=Band {who}", "-metadata", f"track={n}",
                        str(mix / f"{who.lower()}{n}.flac"), "-y"], check=True)
    return mix


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


class TwoAlbumsOfOneFolder(te.Base):
    """A folder holding two albums, X and Y, each a row: X changed or moved away never takes
    Y's row (Y is looked up under its own key, X's row gets no album of Y's)."""
    def setUp(self):
        super().setUp()
        self.mix = mixed_folder(self.lib / "Mixed")
        self.http = CloningHttp(dg=[te.dg_release(930, "Band X", "Album X"), te.dg_release(931, "Band Y", "Album Y")])

    def keys(self, e):
        keys = {("x" if "/x1.flac" in a["key"] else "y"): a["key"] for a in e.listing(str(self.lib))["albums"]}
        self.assertEqual(sorted(keys), ["x", "y"])
        return keys

    def look_up(self, e, order, workers):
        e.lookup_workers = workers
        got = []
        e.plan_each(str(self.lib), order, lambda k, p: got.append((k, p)))
        return dict(got)

    def test_x_moved_away_never_takes_y(self):
        for workers in (1, 3):
            for first in ("x", "y"):
                with self.subTest(workers=workers, first=first):
                    e = self.engine(self.http)
                    keys = self.keys(e)
                    gone = {f: f.read_bytes() for f in self.mix.glob("x*.flac")}
                    for f in gone:
                        f.unlink()                                   # (X applied: moved away)
                    try:
                        last = self.look_up(e, [keys[first], keys["y" if first == "x" else "x"]], workers)
                    finally:
                        for f, b in gone.items():
                            f.write_bytes(b)
                    self.kept_as_listed(last[keys["x"]]["albums"], keys["x"])   # (X's row: no album of Y's)
                    ys = last[keys["y"]]["albums"]
                    self.assertEqual([a["key"] for a in ys], [keys["y"]])
                    self.assertTrue(all("y1.flac" in " ".join(a["album_files"]) for a in ys))

    def kept_as_listed(self, rows, key):
        self.assertEqual([(a["key"], a["status"]) for a in rows], [(key, "listed")])
        self.assertIn(engine.CHANGED_SINCE_LISTED, rows[0]["notes"])
        self.assertFalse(planmod.has_work(rows[0]))
        self.assertFalse(any("/y" in f for f in rows[0]["album_files"]))

    def test_refresh_of_x_alone_x_moved_away(self):
        """Refresh of X's row alone (a selection): Y is another row of the list - never X's."""
        for workers in (1, 3):
            with self.subTest(workers=workers):
                e = self.engine(self.http)
                keys = self.keys(e)
                e.listed_keys = set(keys.values())                # (the app: every row of its list)
                gone = {f: f.read_bytes() for f in self.mix.glob("x*.flac")}
                for f in gone:
                    f.unlink()
                try:
                    last = self.look_up(e, [keys["x"]], workers)
                finally:
                    for f, b in gone.items():
                        f.write_bytes(b)
                self.assertEqual(list(last), [keys["x"]])
                self.kept_as_listed(last[keys["x"]]["albums"], keys["x"])

    def test_refresh_of_x_alone_a_track_of_x_renamed(self):
        for workers in (1, 3):
            with self.subTest(workers=workers):
                e = self.engine(self.http)
                keys = self.keys(e)
                e.listed_keys = set(keys.values())
                old, new = self.mix / "x2.flac", self.mix / "x2 renamed.flac"
                old.rename(new)
                try:
                    last = self.look_up(e, [keys["x"]], workers)
                finally:
                    new.rename(old)
                xs = last[keys["x"]]["albums"]
                self.assertEqual([a["key"] for a in xs], [keys["x"]])          # (X as it is now, its row)
                self.assertIn(str(new), xs[0]["album_files"])
                self.assertFalse(any("/y" in f for f in xs[0]["album_files"]))

    def test_which_album_is_the_rows_cannot_be_told_the_row_stays(self):
        """X renamed, and Y no row of this look-up (no list told): two albums free - X's row stays
        as listed, saying so, never dropped."""
        for workers in (1, 3):
            with self.subTest(workers=workers):
                e = self.engine(self.http)
                keys = self.keys(e)
                old, new = self.mix / "x2.flac", self.mix / "x2 renamed.flac"
                old.rename(new)
                try:
                    last = self.look_up(e, [keys["x"]], workers)
                finally:
                    new.rename(old)
                self.kept_as_listed(last[keys["x"]]["albums"], keys["x"])

    def test_x_changed_is_x_and_y_stays_y(self):
        for workers in (1, 3):
            for first in ("x", "y"):
                with self.subTest(workers=workers, first=first):
                    e = self.engine(self.http)
                    keys = self.keys(e)
                    added = self.mix / "x4.flac"
                    subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "sine=frequency=1300:duration=9",
                                    "-ar", "8000", "-ac", "1", "-metadata", "title=X4", "-metadata", "album=Album X",
                                    "-metadata", "albumartist=Band X", "-metadata", "artist=Band X",
                                    "-metadata", "track=4", str(added), "-y"], check=True)
                    try:
                        last = self.look_up(e, [keys[first], keys["y" if first == "x" else "x"]], workers)
                    finally:
                        added.unlink()
                    xs, ys = last[keys["x"]]["albums"], last[keys["y"]]["albums"]
                    self.assertEqual([a["key"] for a in xs], [keys["x"]])      # (X, as it is now, its own row)
                    self.assertIn(str(added), xs[0]["album_files"])
                    self.assertEqual([a["key"] for a in ys], [keys["y"]])


class OneAlbumsInternalError(te.Base):
    def setUp(self):
        super().setUp()
        self.album("Band G", "Album G")
        self.album("Band H", "Album H")
        self.http = CloningHttp(dg=[te.dg_release(920, "Band G", "Album G"), te.dg_release(921, "Band H", "Album H")])

    def run_each(self, workers, patch, why=engine.LOOKUP_FAILED, logged="RecursionError", **prof):
        e = self.engine(self.http, **prof)
        e.lookup_workers = workers
        keys = [a["key"] for a in e.listing(str(self.lib))["albums"]]
        g_dir = self.lib / "Band G" / "2005 - Album G"
        before = {f: f.read_bytes() for f in g_dir.iterdir()}
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
        self.assertIn(why, g[0]["identification"]["reason"])
        self.assertEqual(g[0]["key"], next(k for k in keys if "Album G" in k))   # (its own row)
        # (never applicable: nothing of it is written, marked or moved)
        self.assertEqual(g[0]["files"], [])
        self.assertFalse(planmod.has_work(g[0]))
        self.assertIn([a["status"] for a in h], (["ready"], ["unchanged"]))    # (applied by a run before: as it is)
        self.assertTrue(any(logged in x for x in logs.output), logs.output)   # (with its traceback)
        import executor
        whole = dict(last[keys[0]], albums=[a for k in keys for a in last[k]["albums"]])
        executor.Executor(whole).run()
        self.assertEqual({f: f.read_bytes() for f in g_dir.iterdir()}, before)
        return g[0]

    def looking_up_g_fails(self, exc=RecursionError("maximum recursion depth exceeded")):
        real = engine.Engine.proposals

        def proposals(e, path, progress=None, cancel=None, only=None):
            if only and any("Album G" in k for k in only):
                raise exc
            return real(e, path, progress, cancel, only=only)
        return mock.patch.object(engine.Engine, "proposals", proposals)

    def identifying_g_fails(self):
        real = engine.Engine.identify_all

        def identify_all(e, g):
            if "Album G" in str(g.folder):
                raise RuntimeError("the database said no")
            return real(e, g)
        return mock.patch.object(engine.Engine, "identify_all", identify_all)

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

    def test_a_folder_that_could_not_be_read_says_so(self):
        self.run_each(1, self.looking_up_g_fails(OSError(5, "Input/output error")),
                      why=engine.FOLDER_UNREAD, logged="OSError")

    def test_marked_unresolved_never_applies_to_an_error_row(self):
        for workers in (1, 3):
            with self.subTest(workers=workers):
                self.run_each(workers, self.looking_up_g_fails(), unresolved={"action": "mark"})
                self.run_each(workers, self.planning_g_fails(), unresolved={"action": "mark"})

    def test_moved_unresolved_never_moves_an_error_row(self):
        holding = self.tmp / "unresolved"
        holding.mkdir()
        for workers in (1, 3):
            with self.subTest(workers=workers):
                self.run_each(workers, self.looking_up_g_fails(),
                              unresolved={"action": "move", "move_to": str(holding)})
        self.assertEqual(list(holding.iterdir()), [])

    def test_a_long_error_still_reads_unresolved(self):
        real = engine.Engine.identify_all

        def identify_all(e, g):
            if "Album G" in str(g.folder):
                raise RuntimeError("no " * 300)
            return real(e, g)
        g = self.run_each(1, mock.patch.object(engine.Engine, "identify_all", identify_all),
                          why="could not be looked up: no no", logged="RuntimeError")
        self.assertLessEqual(len(g["identification"]["reason"]), 250)
        # (told by its code: words cut short at any length still read Unresolved)
        import reasons
        cut = {"albums": [{"status": "skipped", "files": [], "identification": {
            "reason": reasons.encode("could not be looked up: " + "x" * 400, ["lookup_failed"])}}]}
        self.assertEqual(engine._unresolved_rows(cut)["albums"][0]["status"], "unresolved")

    def test_an_album_whose_identification_failed_is_never_applicable_either(self):
        for action in ({"action": "mark"}, {"action": "move", "move_to": str(self.tmp / "u")}):
            with self.subTest(action=action["action"]):
                self.run_each(1, self.identifying_g_fails(), why="could not be looked up: the database said no (see log)",
                              logged="RuntimeError", unresolved=action)


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

    def test_a_chained_cause_is_one_line(self):
        from gui import work
        try:
            try:
                {}["x"]
            except KeyError as exc:
                raise ValueError("bad row") from exc
        except ValueError as exc:
            text = work.short_traceback(exc)
        self.assertIn("ValueError: bad row\n  (caused by: KeyError: 'x')", text)

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
        self.assertIn("could not be looked up: internal error (see log)", g["identification"]["reason"])
        from gui.models import can_apply
        self.assertFalse(can_apply(g))                                    # (never ticked, never applied)
        self.assertNotIn(g, ov.to_apply())
        self.assertNotEqual(self.row("Album N")["status"], "listed")      # (looked up all the same)

    def test_an_error_the_engine_did_not_keep_to_one_album_leaves_the_others_as_listed(self):
        ov = self.ov

        def plan_each(e, path, keys, emit, **kw):
            raise RecursionError("maximum recursion depth exceeded")
        failed, said = [], []
        with mock.patch.object(engine.Engine, "plan_each", plan_each), \
                mock.patch.object(type(ov), "oops", lambda s, msg: failed.append(msg)), \
                mock.patch.object(self.win.statusBar(), "showMessage", lambda m, *a: said.append(m)), \
                self.assertLogs("tagmender", "ERROR") as logs:
            ov.look_up("profile", list(ov.model.albums))
            self.assertTrue(wait(lambda: not ov.running, self.app, timeout=60), ov.summary.text())
        self.assertEqual(failed, [])
        # (never looked up: as they were - no row names another album's error)
        self.assertEqual([a["status"] for a in ov.model.albums], ["listed", "listed"])
        self.assertTrue(any("could not be looked up: internal error (see log)" in m for m in said), said)
        self.assertTrue(any("RecursionError" in x for x in logs.output))

    def test_cancel_still_stops_a_look_up(self):
        ov = self.ov
        gate, entered = threading.Event(), threading.Event()
        real = engine.Engine.proposals

        def proposals(e, path, progress=None, cancel=None, only=None):  # noqa: F811
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


@unittest.skipIf(tg.QApplication is None, "PySide6 not installed")
class RefreshASelection(tg.Gui):
    """Refresh of some rows only, from two folders (two batches): a changed album never takes
    another row's album, and a row whose album cannot be told stays, as listed."""
    def test_refresh_of_some_rows_from_two_folders(self):
        import app_settings
        app_settings.set_value("add_folder", "list")
        mix = mixed_folder(self.tmp / "drop" / "Mixed")
        ov = self.win.pages["Overview"]
        ov.refresh_profiles()
        ov.profile.setCurrentText("Auto")
        ov.set_path(str(mix))
        self.assertTrue(wait(lambda: ov.plan is not None and not ov.running, self.app))
        ov.add_to_list([str(self.d)])
        self.assertTrue(wait(lambda: len(ov.model.albums) == 3 and not ov.running, self.app, timeout=60))
        ov.look_up("profile", list(ov.model.albums))
        self.assertTrue(wait(lambda: not ov.running, self.app, timeout=60), ov.summary.text())
        row = lambda w: next(a for a in ov.model.albums                       # noqa: E731
                             if any(f"/{w}1.flac" in f for f in a.get("album_files") or []) or
                             (w == "g" and "Album G" in a["source_dir"]))
        kx, ky = ov.album_key(row("x")), ov.album_key(row("y"))
        y_was = dict(row("y"))
        failed = []
        with mock.patch.object(type(ov), "oops", lambda s, msg: failed.append(msg)):
            old, new = mix / "x2.flac", mix / "x2 renamed.flac"
            old.rename(new)                                        # (a track of X renamed)
            ov.look_up("profile", [row("x"), row("g")], again=True)
            self.assertTrue(wait(lambda: not ov.running, self.app, timeout=60), ov.summary.text())
            self.assertEqual(len(ov.model.albums), 3)
            self.assertEqual(ov.album_key(row("x")), kx)
            self.assertIn(str(new), row("x")["album_files"])
            self.assertEqual(ov.album_key(row("y")), ky)
            self.assertEqual(row("y")["album_files"], y_was["album_files"])
            for f in mix.glob("x*.flac"):
                f.unlink()                                         # (X moved away)
            ov.look_up("profile", [row("x"), row("g")], again=True)
            self.assertTrue(wait(lambda: not ov.running, self.app, timeout=60), ov.summary.text())
        self.assertEqual(failed, [])
        self.assertEqual(len(ov.model.albums), 3)
        x = next(a for a in ov.model.albums if ov.album_key(a) == kx)
        self.assertEqual(x["status"], "listed")
        self.assertIn(engine.CHANGED_SINCE_LISTED, x["notes"])
        from gui.models import can_apply
        self.assertFalse(can_apply(x))
        y = next(a for a in ov.model.albums if ov.album_key(a) == ky)
        self.assertEqual(y["album_files"], y_was["album_files"])
        self.assertEqual(y["status"], y_was["status"])

# (the window's own tests are test_gui's: not run again here)
for _cls in (FailureShown, LookUpGoesOn, RefreshAfterApply, RefreshASelection):
    for _n in dir(tg.Gui):
        if _n.startswith("test_"):
            setattr(_cls, _n, None)


if __name__ == "__main__":
    logging.disable(logging.NOTSET)
    unittest.main()
