# Tests for the work commands (scan, plan, apply, run, jobs, undo, recover, unresolved, formats,
# watch) and watcher.py, through tagmender.main, with recorded-style responses (no network).
import os as _os, tempfile as _tempfile  # noqa: E402
_os.environ["XDG_CONFIG_HOME"] = _tempfile.mkdtemp(prefix="tm-config-")
_os.environ["XDG_STATE_HOME"] = _tempfile.mkdtemp(prefix="tm-state-")

import contextlib
import io
import json
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
import test_engine as te  # noqa: E402  (fixtures and FakeHttp)
import exclusions as ex  # noqa: E402
import executor  # noqa: E402
import match_albums as ma  # noqa: E402
import profiles as pf  # noqa: E402
import tagio  # noqa: E402
import tagmender  # noqa: E402
import watcher  # noqa: E402


def run(*argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            code = tagmender.main(list(argv))
        except SystemExit as e:
            code = e.code
    return code, out.getvalue(), err.getvalue()


class Work(te.Base):
    def setUp(self):
        super().setUp()
        self.d = self.album("Band W", "Album W")
        self.http = te.FakeHttp(dg=[te.dg_release(907, "Band W", "Album W")])
        p = mock.patch.object(ma, "Http", lambda db, cfg, token: self.http)
        p.start()
        self.addCleanup(p.stop)
        pf.save(pf.from_dict({"name": "T", "sources": [{"path": str(self.lib)}],
                              "providers": {"web": {"enabled": False}}, "artwork": {"enabled": False}}))

    def test_scan_is_offline_and_lists_albums(self):
        code, out, _ = run("scan", str(self.lib), "--json")
        self.assertEqual(code, 0)
        rows = json.loads(out)
        self.assertEqual((rows[0]["files"], rows[0]["album"], rows[0]["ids"]), (3, "Album W", []))
        self.assertEqual(self.http.calls, [])

    def test_plan_save_apply_jobs_undo(self):
        plan_file = self.tmp / "p.json"
        before = tagio.read(self.d / "01.flac")
        code, out, err = run("--profile", "T", "plan", str(self.lib), "-o", str(plan_file))
        self.assertEqual(code, 0, err)
        self.assertIn("discogs 907", out)
        self.assertIn("Nothing was changed", err)
        self.assertEqual(tagio.read(self.d / "01.flac"), before)
        code, out, _ = run("apply", str(plan_file))                  # not a terminal: never applied silently
        self.assertEqual(code, 3)
        code, out, err = run("apply", str(plan_file), "--yes")
        self.assertEqual(code, 0, out + err)
        job = json.loads(plan_file.read_text())["job"]
        self.assertIn(f"tagmender undo {job}", out)
        self.assertEqual(tagio.read(self.d / "01.flac")["discogs_release_id"], ["907"])
        code, out, _ = run("jobs", "--json")
        self.assertEqual(json.loads(out)[0]["job"], job)
        code, out, _ = run("undo", job)
        self.assertEqual(code, 0, out)
        self.assertEqual(tagio.read(self.d / "01.flac"), before)

    def test_run_with_yes_and_profile_option(self):
        code, out, err = run("run", str(self.lib), "--profile", "T", "--yes")
        self.assertEqual(code, 0, out + err)
        self.assertEqual(tagio.read(self.d / "02.flac")["tagmender"], ["Resolved"])
        code, out, _ = run("run", str(self.lib), "--profile", "T", "--yes")
        self.assertIn("Nothing to change", out)

    def test_unresolved_listing_by_code(self):
        other = self.album("Band U", "Album U")
        for f in other.glob("*.flac"):
            tagio.write(f, {"tagmender": ["Unresolved"], "tagmender_reason": ["ambiguous_release: two pressings"]})
        code, out, _ = run("unresolved", str(self.lib), "--json")
        rows = json.loads(out)
        self.assertEqual([(Path(r["folder"]).name, r["codes"]) for r in rows],
                         [("2005 - Album U", ["ambiguous_release"])])
        code, out, _ = run("unresolved", str(self.lib))
        self.assertIn("ambiguous_release (1)", out)

    def test_formats(self):
        code, out, _ = run("formats", "--json")
        caps = json.loads(out)
        self.assertTrue(caps["FLAC"]["tested here"])
        self.assertFalse(caps["WMA"]["custom fields"])

    def test_bad_profile_is_an_error_not_a_crash(self):
        code, _, err = run("plan", str(self.lib), "--profile", "Nope")
        self.assertEqual(code, 1)
        self.assertIn("no profile named", err)

    def test_recover_with_nothing_pending(self):
        code, out, _ = run("recover")
        self.assertIn("No interrupted jobs", out)


class Watch(te.Base):
    def setUp(self):
        super().setUp()
        self.inbox = self.lib
        self.d = self.album("Band X", "Album X")
        self.http = te.FakeHttp(dg=[te.dg_release(908, "Band X", "Album X")])
        p = mock.patch.object(ma, "Http", lambda db, cfg, token: self.http)
        p.start()
        self.addCleanup(p.stop)
        old = time.time() - 3600
        for f in self.d.rglob("*"):
            os.utime(f, (old, old))
        self.prof = pf.from_dict({"name": "W", "sources": [{"path": str(self.inbox), "watch": True}],
                                  "unattended": True, "providers": {"web": {"enabled": False}},
                                  "artwork": {"enabled": False},
                                  "watch": {"settle_minutes": 1}})
        pf.save(self.prof)

    def test_processes_once_and_never_requeues_its_own_writes(self):
        w = watcher.Watcher([self.prof], once=True)
        first = w.tick()
        self.assertEqual(list(first.values()), ["done"], first)
        self.assertEqual(tagio.read(self.d / "01.flac")["discogs_release_id"], ["908"])
        calls = len(self.http.calls)
        self.assertEqual(w.tick(), {})                       # just written: settling
        later = time.time() + 7200                           # an hour after the settle time
        with mock.patch.object(watcher.time, "time", return_value=later):
            self.assertEqual(w.tick(), {})                   # TagMender's own writes never queue it again
        self.assertEqual(len(self.http.calls), calls)        # not even looked up again

    def test_unsettled_and_partial_downloads_wait(self):
        (self.d / "04.flac.part").write_bytes(b"downloading")
        w = watcher.Watcher([self.prof], once=True)
        self.assertEqual(w.tick(), {})
        (self.d / "04.flac.part").unlink()
        (self.d / "cover.jpg").write_bytes(b"x")              # brand new file: still changing
        self.assertEqual(w.tick(), {})

    def test_changed_album_is_processed_again(self):
        w = watcher.Watcher([self.prof], once=True)
        w.tick()
        tagio.write(self.d / "01.flac", {"comment": ["changed by hand"]})
        for f in self.d.rglob("*"):
            os.utime(f, (time.time() - 3600, time.time() - 3600))
        self.assertEqual(list(w.tick().values()), ["unchanged"])

    def test_provider_outage_is_retried_later_not_marked_forever(self):
        self.http.dg = {}
        self.http.mb_down = True
        w = watcher.Watcher([self.prof], once=True)
        self.assertEqual(list(w.tick().values()), ["provider_error"])
        row = watcher._db().execute("SELECT next_try FROM processed").fetchone()
        self.assertGreater(row[0], time.time() + 300)
        self.assertEqual(w.tick(), {})                        # not before its time

    def test_interrupted_job_is_recovered_on_start(self):
        jd = executor.job_dir("crashed")
        executor.Journal(jd).write(ev="begin", job="crashed")
        watcher.Watcher([self.prof], once=True).tick()
        self.assertEqual(executor.pending_jobs(), [])

    def test_unmounted_watch_root_is_skipped(self):
        prof = pf.from_dict({"name": "G", "sources": [{"path": str(self.tmp / "gone"), "watch": True}]})
        self.assertEqual(watcher.Watcher([prof], once=True).tick(), {})
        self.assertFalse((self.tmp / "gone").exists())

    def test_watch_command_once(self):
        code, out, _ = run("watch", "--once", "--profile", "W")
        self.assertEqual(code, 0)
        self.assertIn("done", out)


if __name__ == "__main__":
    unittest.main()
