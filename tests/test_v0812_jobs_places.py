# 0.8.12: the Jobs page says which albums each job handled and where they went (from its plan):
# "Artist / 1973 - Album [edition]" and the folder, a set's Disc folders folded into the album's,
# with Open Folder.
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_engine import Base  # noqa: E402  (isolated settings and state)


class JobPlaces(Base):
    def test_albums_and_their_folders_from_the_plan(self):
        import executor
        from gui import jobs
        d = Path(tempfile.mkdtemp())
        plan = {"albums": [
            {"source_dir": "/in/Piano Man", "files": [
                {"src": "/in/Piano Man/2.0/1.flac", "dst": "/lib/Billy Joel/1973 - Piano Man [2024 SACD]/Disc 02/201.flac"},
                {"src": "/in/Piano Man/5.0/1.flac", "dst": "/lib/Billy Joel/1973 - Piano Man [2024 SACD]/Disc 03/301.flac"}]},
            {"source_dir": "/in/Other", "files": [{"src": "/in/Other/1.flac", "dst": "/lib/Band/2001 - Other/01.flac"}]},
            {"source_dir": "/in/Nothing", "files": []}]}
        (d / "plan.json").write_text(json.dumps(plan))
        with mock.patch.object(executor, "job_dir", lambda _j: d):
            got = jobs.job_places("j1")
        self.assertEqual(got, [("Billy Joel / 1973 - Piano Man [2024 SACD]", "/lib/Billy Joel/1973 - Piano Man [2024 SACD]",
                                "/in/Piano Man"),
                               ("Band / 2001 - Other", "/lib/Band/2001 - Other", "/in/Other")])
        with mock.patch.object(executor, "job_dir", lambda _j: d / "gone"):
            self.assertEqual(jobs.job_places("j2"), [])

    def test_an_album_the_job_did_not_finish_was_not_filed(self):
        import executor
        from gui import jobs
        d = Path(tempfile.mkdtemp())
        plan = {"albums": [
            {"source_dir": "/in/A", "files": [{"src": "/in/A/1.flac", "dst": "/lib/X/2001 - A/01.flac"}]},
            {"source_dir": "/in/B", "files": [{"src": "/in/B/1.flac", "dst": "/lib/Y/2002 - B/01.flac"}]}]}
        (d / "plan.json").write_text(json.dumps(plan))
        (d / "journal.jsonl").write_text("\n".join(json.dumps(e) for e in (
            {"ev": "album_begin", "album": 0}, {"ev": "album_done", "album": 0},
            {"ev": "album_begin", "album": 1}, {"ev": "failed", "album": 1})) + "\n")
        with mock.patch.object(executor, "job_dir", lambda _j: d):
            self.assertEqual(jobs.job_places("j"), [("X / 2001 - A", "/lib/X/2001 - A", "/in/A")])

    def test_a_plan_not_as_written_shows_nothing_rather_than_failing(self):
        import executor
        from gui import jobs
        d = Path(tempfile.mkdtemp())
        with mock.patch.object(executor, "job_dir", lambda _j: d):
            for bad in ([], {"albums": {"a": 1}}, {"albums": ["x", None]},
                        {"albums": [{"files": [None, {"dst": None}, {"dst": 5}, "x"]}]}):
                (d / "plan.json").write_text(json.dumps(bad))
                self.assertEqual(jobs.job_places("j"), [], bad)

    def test_a_page_without_a_window_leaves_no_thread_running(self):
        # (a Jobs page on its own - as the Recover test makes one - started a worker no window
        # waited for: the process aborted at exit with "QThread: Destroyed while thread is running")
        import os
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        try:
            import gui
            from gui.work import _alive
        except ImportError as exc:                       # pragma: no cover
            self.skipTest(f"no PySide6: {exc}")
        gui.QApplication.instance() or gui.QApplication([])
        page = gui.Jobs(None)
        page.fill_places(["j-none"])                  # (a job whose plan is not read yet)
        self.assertEqual([th for th, _w in getattr(page, "_threads", []) if _alive(th)], [])


if __name__ == "__main__":
    unittest.main()
