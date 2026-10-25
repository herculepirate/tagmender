# Watched folders that file what is dropped into them: a profile watches an inbox and moves
# (or copies) each album it identifies into its library, unattended.
import os as _os, tempfile as _tempfile  # noqa: E402
_os.environ["XDG_CONFIG_HOME"] = _tempfile.mkdtemp(prefix="tm-config-")
_os.environ["XDG_STATE_HOME"] = _tempfile.mkdtemp(prefix="tm-state-")

import os
import shutil
import sys
import time
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "tests"))
import test_engine as te  # noqa: E402
import match_albums as ma  # noqa: E402
import profiles as pf  # noqa: E402
import tagio  # noqa: E402
import watcher  # noqa: E402


def settle(path):
    old = time.time() - 3600
    for f in [path, *Path(path).rglob("*")]:
        os.utime(f, (old, old))


class DropBase(te.Base):
    def setUp(self):
        super().setUp()
        self.inbox = self.tmp / "inbox"
        self.dest = self.tmp / "library"
        self.inbox.mkdir()
        self.dest.mkdir()
        self.http = te.FakeHttp(dg=[te.dg_release(908, "Band X", "Album X"),
                                    te.dg_release(909, "Band Y", "Album Y")])
        p = mock.patch.object(ma, "Http", lambda db, cfg, token: self.http)
        p.start()
        self.addCleanup(p.stop)

    def profile(self, **kw):
        d = {"name": "Inbox", "operation": "move", "destination": str(self.dest),
             "sources": [{"path": str(self.inbox), "watch": True, "recursive": True}],
             "unattended": True, "providers": {"web": {"enabled": False}}, "artwork": {"enabled": False},
             "watch": {"settle_minutes": 1}}
        d.update(kw)
        p = pf.from_dict(d)
        pf.save(p)
        return p

    def drop(self, artist, title, where=None, n=3):
        self.lib = where or self.inbox
        d = self.album(artist, title, n=n, folder=f"{artist} - {title}")
        # album() makes lib/artist/folder; a drop is usually one folder: move it up a level
        target = (where or self.inbox) / d.name
        shutil.move(str(d), str(target))
        try:
            (where or self.inbox).joinpath(artist).rmdir()
        except OSError:
            pass
        settle(self.inbox)
        return target

    def audio_in(self, root):
        return sorted(p for p in Path(root).rglob("*.flac"))



class Drop(DropBase):
    def test_dropped_folder_is_tagged_and_moved_to_the_library(self):
        prof = self.profile()
        d = self.drop("Band X", "Album X")
        res = watcher.Watcher([prof], once=True).tick()
        self.assertEqual(list(res.values()), ["done"], res)
        self.assertFalse(d.exists())
        self.assertTrue(self.inbox.is_dir())                   # the watched folder itself stays
        moved = self.audio_in(self.dest)
        self.assertEqual(len(moved), 3, moved)
        self.assertEqual(tagio.read(moved[0])["discogs_release_id"], ["908"])
        self.assertEqual(self.audio_in(self.inbox), [])
        self.assertEqual(watcher.Watcher([prof], once=True).tick(), {})

    def test_loose_tracks_dropped_into_the_watched_folder(self):
        prof = self.profile()
        d = self.drop("Band X", "Album X")
        for f in list(d.iterdir()):
            shutil.move(str(f), str(self.inbox / f.name))
        d.rmdir()
        settle(self.inbox)
        res = watcher.Watcher([prof], once=True).tick()
        self.assertEqual(list(res.values()), ["done"], res)
        self.assertEqual(len(self.audio_in(self.dest)), 3)
        self.assertEqual(self.audio_in(self.inbox), [])
        self.assertTrue(self.inbox.is_dir())

    def test_nested_drop_leaves_no_empty_folders_behind(self):
        prof = self.profile()
        sub = self.inbox / "Downloads" / "batch 1"
        sub.mkdir(parents=True)
        self.drop("Band X", "Album X", where=sub)
        res = watcher.Watcher([prof], once=True).tick()
        self.assertEqual(list(res.values()), ["done"], res)
        self.assertEqual(len(self.audio_in(self.dest)), 3)
        self.assertEqual(sorted(p.name for p in self.inbox.iterdir()), [], list(self.inbox.rglob("*")))

    def test_two_drops_in_one_pass(self):
        prof = self.profile()
        self.drop("Band X", "Album X")
        self.drop("Band Y", "Album Y")
        res = watcher.Watcher([prof], once=True).tick()
        self.assertEqual(sorted(res.values()), ["done", "done"], res)
        self.assertEqual(len(self.audio_in(self.dest)), 6)

    def test_library_inside_the_watched_folder_is_not_processed_again(self):
        self.dest = self.inbox / "Library"
        self.dest.mkdir()
        prof = self.profile()
        self.drop("Band X", "Album X")
        w = watcher.Watcher([prof], once=True)
        self.assertEqual(list(w.tick().values()), ["done"])
        calls = len(self.http.calls)
        with mock.patch.object(watcher.time, "time", return_value=time.time() + 7200):
            self.assertEqual(w.tick(), {})              # what it filed is not a new drop
        self.assertEqual(len(self.http.calls), calls)

    def test_copy_profile_keeps_the_drop_and_files_a_copy(self):
        prof = self.profile(operation="copy")
        d = self.drop("Band X", "Album X")
        w = watcher.Watcher([prof], once=True)
        self.assertEqual(list(w.tick().values()), ["done"])
        self.assertEqual(len(self.audio_in(self.dest)), 3)
        self.assertEqual(len(self.audio_in(d)), 3)
        with mock.patch.object(watcher.time, "time", return_value=time.time() + 7200):
            self.assertEqual(w.tick(), {})              # copied once, not on every pass

    def test_not_unattended_waits_for_approval_and_is_not_planned_twice(self):
        prof = self.profile(unattended=False)
        d = self.drop("Band X", "Album X")
        w = watcher.Watcher([prof], once=True)
        self.assertEqual(list(w.tick().values()), ["planned"])
        self.assertTrue(d.exists())
        self.assertEqual(len(watcher.pending_plans()), 1)
        self.assertEqual(w.tick(), {})
        self.assertEqual(len(watcher.pending_plans()), 1)

    def test_copy_keeping_old_times_waits_until_the_folder_is_quiet(self):
        prof = self.profile()
        d = self.drop("Band X", "Album X")
        now = time.time()
        os.utime(d, (now, now))           # files arriving (cp -a keeps each file's old time)
        self.assertEqual(watcher.Watcher([prof], once=True).tick(), {})
        self.assertTrue(d.exists())

    def test_rsync_temporary_file_is_a_download_in_progress(self):
        prof = self.profile()
        d = self.drop("Band X", "Album X")
        (d / ".04 T3.flac.Ab12Cd").write_bytes(b"half")
        settle(self.inbox)
        self.assertEqual(watcher.Watcher([prof], once=True).tick(), {})
        self.assertTrue(watcher._partial(".01 Song.mp3.x9Y8z7"))
        self.assertFalse(watcher._partial(".DS_Store"))

    def test_destination_unmounted_waits(self):
        prof = self.profile()
        d = self.drop("Band X", "Album X")
        self.dest.rmdir()
        w = watcher.Watcher([prof], once=True)
        self.assertEqual(list(w.tick().values()), ["blocked"])
        self.assertTrue(d.exists())
        self.dest.mkdir()
        self.assertEqual(list(w.tick().values()), ["done"])
        self.assertEqual(len(self.audio_in(self.dest)), 3)

    def test_each_watched_folder_uses_its_own_profile(self):
        other_in, other_lib = self.tmp / "inbox2", self.tmp / "library2"
        other_in.mkdir()
        other_lib.mkdir()
        a = self.profile()
        b = self.profile(name="Vinyl", destination=str(other_lib),
                         sources=[{"path": str(other_in), "watch": True, "recursive": True}])
        self.drop("Band X", "Album X")
        self.drop("Band Y", "Album Y", where=other_in)
        settle(other_in)
        res = watcher.Watcher([a, b], once=True).tick()
        self.assertEqual(sorted(res.values()), ["done", "done"], res)
        self.assertEqual(len(self.audio_in(self.dest)), 3)
        self.assertEqual(len(self.audio_in(other_lib)), 3)
        self.assertEqual(tagio.read(self.audio_in(other_lib)[0])["discogs_release_id"], ["909"])

    def test_approved_plan_is_applied_and_its_album_not_identified_again(self):
        self.dest = self.inbox / "Library"
        self.dest.mkdir()
        prof = self.profile(unattended=False)
        d = self.drop("Band X", "Album X")
        w = watcher.Watcher([prof], once=True)
        self.assertEqual(list(w.tick().values()), ["planned"])
        [p] = watcher.pending_plans()
        self.assertEqual(p["profile"], "Inbox")
        self.assertEqual(p["albums"][0][0], str(d))
        res = watcher.apply_pending(p["path"])
        self.assertEqual(res["albums"][0]["status"], "done", res)
        self.assertEqual(watcher.pending_plans(), [])
        self.assertEqual(len(self.audio_in(self.dest)), 3)
        calls = len(self.http.calls)
        with mock.patch.object(watcher.time, "time", return_value=time.time() + 7200):
            self.assertEqual(w.tick(), {})
        self.assertEqual(len(self.http.calls), calls)

    def test_dismissed_plan_is_kept_aside_and_not_made_again(self):
        prof = self.profile(unattended=False)
        d = self.drop("Band X", "Album X")
        w = watcher.Watcher([prof], once=True)
        w.tick()
        [p] = watcher.pending_plans()
        kept = watcher.dismiss_pending(p["path"])
        self.assertTrue(kept.exists())
        self.assertEqual(watcher.pending_plans(), [])
        self.assertTrue(d.exists())
        with mock.patch.object(watcher.time, "time", return_value=time.time() + 7200):
            self.assertEqual(w.tick(), {})

    def test_single_track_dropped(self):
        prof = self.profile()
        d = self.drop("Band X", "Album X")
        one = sorted(d.iterdir())[0]
        shutil.move(str(one), str(self.inbox / one.name))
        shutil.rmtree(d)
        settle(self.inbox)
        res = watcher.Watcher([prof], once=True).tick()
        self.assertEqual(len(res), 1, res)
        # one track of a three-track release is not identified (the matcher's call): it waits
        self.assertEqual(list(res.values()), ["unresolved"])
        self.assertTrue((self.inbox / one.name).exists())

    def test_unidentified_drop_stays_and_is_not_retried_every_pass(self):
        prof = self.profile()
        d = self.drop("Nobody", "Nothing Known")
        w = watcher.Watcher([prof], once=True)
        res = w.tick()
        self.assertEqual(list(res.values()), ["unresolved"])
        self.assertEqual(self.audio_in(self.dest), [])
        calls = len(self.http.calls)
        with mock.patch.object(watcher.time, "time", return_value=time.time() + 7200):
            self.assertEqual(w.tick(), {})
        self.assertEqual(len(self.http.calls), calls)
        self.assertTrue(d.exists())


class Cli(DropBase):
    def run_cli(self, *argv):
        import contextlib
        import io
        import tagmender
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = tagmender.main(list(argv))
        return code, out.getvalue(), err.getvalue()

    def test_add_list_pending_approve_remove(self):
        pf.save(pf.from_dict({"name": "Lib", "operation": "move", "destination": str(self.dest),
                              "providers": {"web": {"enabled": False}}, "artwork": {"enabled": False},
                              "watch": {"settle_minutes": 1}}))
        code, _o, err = self.run_cli("watch", "--add", str(self.inbox))
        self.assertNotEqual(code, 0)
        self.assertIn("--profile", err)
        code, _o, err = self.run_cli("watch", "--add", str(self.inbox), "--profile", "Lib", "--no-auto")
        self.assertEqual(code, 0, err)
        code, out, _e = self.run_cli("watch", "--list")
        self.assertIn(f"{self.inbox}\tLib\tmoved into {self.dest}\tasks first", out)
        d = self.drop("Band X", "Album X")
        code, out, _e = self.run_cli("watch", "--once")
        self.assertIn("planned", out)
        code, out, _e = self.run_cli("watch", "--pending")
        job = out.split("\t")[0]
        self.assertIn(str(d), out)
        code, out, err = self.run_cli("watch", "--approve", job)
        self.assertIn("done", out, err)
        self.assertFalse(d.exists())
        self.assertEqual(len(self.audio_in(self.dest)), 3)
        code, out, _e = self.run_cli("watch", "--pending")
        self.assertEqual(out, "")
        self.run_cli("watch", "--remove", str(self.inbox))
        self.assertEqual([s for s in pf.load("Lib")["sources"] if s.get("watch")], [])


class Settings(unittest.TestCase):
    def test_watch_settings_are_checked(self):
        base = {"name": "W", "sources": [{"path": "/tmp/in", "watch": True}]}
        for bad in ({"settle_minutes": "ten"}, {"settle_minutes": -1}, {"interval_seconds": 0},
                    {"interval_seconds": True}):
            with self.assertRaises(pf.ProfileError, msg=bad):
                pf.from_dict(dict(base, watch=bad))
        with self.assertRaises(pf.ProfileError):
            pf.from_dict({"name": "W", "sources": [{"path": "/", "watch": True}]})
        pf.from_dict({"name": "W", "sources": [{"path": "/", "watch": False}]})     # read, not watched: fine
        self.assertEqual(pf.from_dict(dict(base, watch={"settle_minutes": 0.5}))["watch"]["interval_seconds"], 300)


class Service(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(_tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        p = mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": str(self.tmp / "cfg")})
        p.start()
        self.addCleanup(p.stop)
        os.environ.pop("APPIMAGE", None)
        self.prof = pf.from_dict({"name": "W", "operation": "move", "destination": "/srv/My Music",
                                  "sources": [{"path": "/media/drop box", "watch": True}],
                                  "unresolved": {"action": "move", "move_to": "/srv/100%/odd"}}, check=False)

    def test_unit_runs_watch_and_is_not_sandboxed(self):
        t = watcher.service_text(python="/usr/bin/python3", script="/opt/my tm/tagmender.py")
        self.assertIn('ExecStart=/usr/bin/python3 -u "/opt/my tm/tagmender.py" watch --nice', t)
        for confining in ("ProtectSystem", "ReadWritePaths", "ReadOnlyPaths", "PrivateTmp", "ProtectHome"):
            self.assertNotIn(confining, t)                   # every folder the user can write, as the app
        shipped = (HERE / "contrib" / "tagmender-watch.service").read_text()
        self.assertNotIn("ProtectSystem=", shipped)
        self.assertNotIn("ReadWritePaths=", shipped)

    def test_appimage_starts_itself(self):
        with mock.patch.dict(os.environ, {"APPIMAGE": "/home/u/Apps/TagMender.AppImage"}):
            t = watcher.service_text()
        self.assertIn("ExecStart=/home/u/Apps/TagMender.AppImage watch --nice", t)

    def test_install_writes_the_unit_and_starts_it_and_stop_disables_it(self):
        calls = []

        def run(cmd, **kw):
            calls.append(cmd[2:])
            out = "active" if cmd[2] == "is-active" else ""
            return mock.Mock(returncode=0, stdout=out, stderr="")
        self.assertEqual(watcher.service_status(run=run), "not installed")
        self.assertEqual(watcher.install_service(run=run), "running")
        f = watcher.service_file()
        self.assertTrue(f.exists())
        self.assertEqual(calls[:2], [["daemon-reload"], ["enable", "--now", watcher.SERVICE]])
        self.assertEqual(watcher.service_status(run=run), "running")
        f.write_text("edited by hand")
        watcher.install_service(run=run)
        self.assertEqual(f.with_name(f.name + ".old").read_text(), "edited by hand")    # kept, not lost
        self.assertEqual(watcher.stop_service(run=run), "stopped")
        self.assertEqual(calls[-1], ["disable", "--now", watcher.SERVICE])
        self.assertTrue(f.exists())

    def test_systemctl_failure_is_reported(self):
        run = lambda cmd, **kw: mock.Mock(returncode=1, stdout="", stderr="Failed to connect to bus")  # noqa: E731
        self.assertIn("Failed to connect to bus", watcher.install_service(run=run))


class Flatpak(unittest.TestCase):
    """In the Flatpak there is no systemctl: the Background portal starts it at login, and
    the watcher is started (and stopped) by the app itself."""

    def setUp(self):
        self.tmp = Path(_tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.saved = watcher.ex.STATE_DIR
        watcher.ex.STATE_DIR = self.tmp / "state"
        self.addCleanup(setattr, watcher.ex, "STATE_DIR", self.saved)
        for p in (mock.patch.object(watcher, "in_flatpak", return_value=True),
                  mock.patch.dict(os.environ, {"HOME": str(self.tmp)})):
            p.start()
            self.addCleanup(p.stop)

    def test_install_asks_the_portal_and_starts_watching_once(self):
        asked, spawned = [], []
        self.assertTrue(watcher.service_supported())
        self.assertEqual(watcher.service_status(), "not installed")
        portal = lambda opts: asked.append(opts) or ""      # noqa: E731
        self.assertEqual(watcher.install_service(portal=portal, spawn=lambda: spawned.append(1)), "running")
        self.assertEqual(asked[0]["commandline"], ["tagmender", "watch", "--nice"])
        self.assertTrue(asked[0]["autostart"])
        self.assertEqual(spawned, [1])
        with watcher.run_lock() as held:                  # a watcher is running now
            self.assertTrue(held)
            self.assertEqual(watcher.service_status(), "running")
            watcher.install_service(portal=portal, spawn=lambda: spawned.append(2))
            self.assertEqual(spawned, [1])                # not a second one
        (self.tmp / ".config" / "autostart").mkdir(parents=True)
        (self.tmp / ".config" / "autostart" / f"{watcher.FLATPAK_ID}.desktop").write_text("[Desktop Entry]")
        self.assertEqual(watcher.service_status(), "stopped")      # starts at the next login

    def test_portal_refusal_is_reported(self):
        self.assertIn("denied", watcher.install_service(portal=lambda o: "denied", spawn=lambda: None))

    def test_stop_turns_autostart_off_and_ends_the_watcher(self):
        import subprocess
        import textwrap
        code = textwrap.dedent(f"""
            import sys, time; sys.path.insert(0, {str(HERE)!r})
            import exclusions as ex; from pathlib import Path
            ex.STATE_DIR = Path({str(self.tmp / 'state')!r})
            import watcher, signal
            w = watcher.Watcher([], once=False)
            signal.signal(signal.SIGTERM, lambda *_: setattr(w, "stop", True))
            w.run()
            print("stopped cleanly")
        """)
        proc = subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE, text=True,
                                env=dict(os.environ, XDG_STATE_HOME=str(self.tmp / "xs"),
                                         XDG_CONFIG_HOME=str(self.tmp / "xc")))
        end = time.time() + 30
        while not watcher.watching_pid() and time.time() < end:
            time.sleep(0.1)
        self.assertEqual(watcher.watching_pid(), proc.pid)
        asked = []
        self.assertEqual(watcher.stop_service(portal=lambda o: asked.append(o) or ""), "stopped")
        self.assertFalse(asked[0]["autostart"])
        out, _ = proc.communicate(timeout=30)
        self.assertIn("stopped cleanly", out)
        self.assertIsNone(watcher.watching_pid())


class Lock(te.Base):
    def test_one_pass_at_a_time_across_processes(self):
        import fcntl
        ex_state = watcher.ex.STATE_DIR
        ex_state.mkdir(parents=True, exist_ok=True)
        w = watcher.Watcher([], once=True)
        with open(ex_state / "watch.lock", "w") as fh:
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.assertEqual(w.tick(), {})
            self.assertIn("another TagMender process", w.skipped)
        w.tick()
        self.assertEqual(w.skipped, "")

    def test_one_watching_process_at_a_time_and_profiles_read_on_every_pass(self):
        w = watcher.Watcher(once=True)
        self.assertFalse(w.fixed)
        with watcher.run_lock() as held:
            self.assertTrue(held)
            w.run()
            self.assertTrue(w.already)
        seen = []
        with mock.patch.object(watcher.Watcher, "_watched_profiles", side_effect=lambda: seen.append(1) or []):
            w2 = watcher.Watcher(once=True)
            w2.run()
        self.assertFalse(w2.already)
        self.assertEqual(len(seen), 2)                  # at start, and again at the pass


if __name__ == "__main__":
    unittest.main()
