# v0.8.70: the command line says where a look-up is (stderr only); doctor treats missing optional keys as warnings.
import os as _os, tempfile as _tempfile  # noqa: E402
_os.environ["XDG_CONFIG_HOME"] = _tempfile.mkdtemp(prefix="tm-config-")
_os.environ["XDG_STATE_HOME"] = _tempfile.mkdtemp(prefix="tm-state-")
for _k in ("DISCOGS_TOKEN", "ACOUSTID_API_KEY", "ACOUSTID_KEY"):
    _os.environ.pop(_k, None)

import contextlib
import io
import sys
import time
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
import doctor  # noqa: E402
import stagetimes  # noqa: E402
import tagmender  # noqa: E402


class TTY(io.StringIO):
    def isatty(self):
        return True


class PlanProgressTest(unittest.TestCase):
    def test_non_tty_one_plain_line_per_album(self):
        err = io.StringIO()
        p = tagmender.PlanProgress(err)
        try:
            p(1, 7, "/m/Artist/2000 - Album")
            stagetimes.back_off("musicbrainz", 5)
            stagetimes.back_off("musicbrainz", 5)          # throttled: one wait line
            p(2, 7, "/m/Other/Thing")
        finally:
            p.done()
        lines = err.getvalue().splitlines()
        self.assertEqual(lines[0], "Looking up 2/7: Artist – 2000 - Album")
        self.assertEqual(lines[1], "waiting for MusicBrainz (busy, retrying in 5 s)")
        self.assertEqual(lines[2], "Looking up 3/7: Other – Thing")
        self.assertEqual(len(lines), 3)
        self.assertNotIn("\r", err.getvalue())
        self.assertNotIn("%", err.getvalue())

    def test_tty_rewrites_one_line_with_stage_and_backoff(self):
        err = TTY()
        p = tagmender.PlanProgress(err)
        try:
            p(0, 3, "/m/A/B")
            with stagetimes.album():
                with stagetimes.stage("musicbrainz"):
                    stagetimes.back_off("musicbrainz", 4)
        finally:
            p.done()
        out = err.getvalue()
        self.assertNotIn("\n", out)
        self.assertIn("\rLooking up 1/3: A – B (MusicBrainz)", out)
        self.assertIn("waiting for MusicBrainz (busy, retrying in 4 s)", out)
        self.assertTrue(out.endswith("\r"))              # cleared at the end

    def test_stage_listener_removed_after_done(self):
        p = tagmender.PlanProgress(io.StringIO())
        p.done()
        self.assertEqual(stagetimes._STAGE, [])
        self.assertEqual(stagetimes._BACK_OFF, [])


class PlanStdoutTest(unittest.TestCase):
    def _run(self, tty):
        fake = {"albums": [], "provider_notes": {}}

        class Eng:
            notes = {}

            def __init__(self, *a, **k):
                pass

            def plan(self, path, progress=None, **k):
                progress(0, 1, "/m/A/B")
                return fake
        out, err = io.StringIO(), (TTY() if tty else io.StringIO())
        with mock.patch("engine.Engine", Eng), mock.patch("plan.preview", return_value="PLAN TEXT"), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = tagmender.main(["plan", _tempfile.gettempdir()])
        return code, out.getvalue(), err.getvalue()

    def test_stdout_identical_and_progress_on_stderr(self):
        c1, o1, e1 = self._run(False)
        c2, o2, e2 = self._run(True)
        self.assertEqual((c1, c2), (0, 0))
        self.assertEqual(o1, o2)
        self.assertEqual(o1, "PLAN TEXT\n")
        self.assertIn("Looking up 1/1: A – B", e1)
        self.assertIn("Looking up 1/1: A – B", e2)


class DoctorOptionalKeysTest(unittest.TestCase):
    def test_no_keys_is_warn_and_exit_zero(self):
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(doctor, "checks", wraps=doctor.checks) as _c:
            found = {c.name: c for c in doctor.registry_checks()}
        d = found["Discogs token"]
        self.assertEqual(d.status, "warn")
        self.assertIn("Preferences › Keys", d.fix)
        self.assertIn("MusicBrainz only", d.fix)
        self.assertFalse([c for c in found.values() if c.status == "fail"])
        registry = list(doctor.registry_checks())
        with mock.patch.object(doctor, "checks", return_value=registry), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = tagmender.main(["doctor"])
        self.assertEqual(code, 0)
        self.assertIn("WARN  Discogs token", out.getvalue())

    def test_genuine_failure_still_exits_one(self):
        bad = doctor.Check("config folder", "fail", "/x: permission denied")
        out = io.StringIO()
        with mock.patch.object(doctor, "checks", return_value=[bad]), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(tagmender.main(["doctor"]), 1)
            self.assertEqual(tagmender.main(["doctor", "--json"]), 1)


if __name__ == "__main__":
    unittest.main()
