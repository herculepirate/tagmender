# Twenty-fifth independent review (A): recover's message for an album it put back now gives a
# command to do it again - `tagmender run "<album folder>"` (executor.py ~1892) - and the
# executor no longer re-runs the saved plan itself. The command names no profile: `tagmender
# run` without --profile plans with the DEFAULT profile (tagmender.py _profile ->
# pf.default()), not the profile the interrupted job used. For a job of a named copy profile
# ("Lossy": copy into the lossy library) the suggested command plans something else - the
# default profile's operation (tagging the originals in place, or an unattended default
# profile applying it without asking). The message should carry the job's profile
# (`--profile Lossy`), which the job's plan records.
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _review25a_common as c  # noqa: E402

sys.path.insert(0, str(c.REPO / "tests" / "fuzz"))
from fuzz_crash import run_child  # noqa: E402


class RecoverMessageProfile(c.Case):
    def test_named_profile_in_the_command(self):
        self.album(self.lib / "Artist A" / "2001 - First", "First", "Artist A", n=3)
        prof = c.pf.from_dict({"name": "Lossy", "sources": [{"path": str(self.lib)}], "tag_level": "enthusiast",
                               "audiophile": {"lyrics": False, "acoustid": False, "replaygain": False},
                               "operation": "copy", "destination": str(self.dest)})
        cf = str(self.root / "counter")
        for n in range(1, 200):                      # the first kill point inside the album
            c.ex.STATE_DIR = self.root / f"state-{n}"      # (a fresh state folder for each try)
            p = self.plan(prof)
            self.assertEqual(p["profile"], "Lossy")
            if os.path.exists(cf):
                os.unlink(cf)
            code = run_child(lambda: c.executor.Executor(p).run(), n, cf)
            evs = [e.get("ev") for e in c.executor.Journal(c.executor.job_dir(p["job"])).read()]
            if code != 0 and "album_begin" in evs and "album_done" not in evs:
                break
            if code == 0:
                self.skipTest("the job was not cut short inside the album")
        else:
            self.skipTest("the job was not cut short inside the album")
        out = c.executor.recover(p["job"])
        again = [x for x in out if "tagmender run" in x]
        self.assertTrue(again, out)
        self.assertIn("--profile", again[0], "the command recover gives would plan with the default profile, "
                                             "not the job's (Lossy):\n" + again[0])


if __name__ == "__main__":
    unittest.main()
