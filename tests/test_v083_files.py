# 0.8.3 file fixes (generated audio only):
# - a move onto a hard link of the track (relink_move): when another program saves the track
#   while TagMender writes the new tags, the step fails with the destination made the track's
#   other name again - that program's edit in both, nothing left unrecorded.
import os as _os, tempfile as _tempfile
_os.environ["XDG_CONFIG_HOME"] = _tempfile.mkdtemp(prefix="tm-config-")
_os.environ["XDG_STATE_HOME"] = _tempfile.mkdtemp(prefix="tm-state-")
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "tests"))
import executor  # noqa: E402
import plan as pl  # noqa: E402
import tagio  # noqa: E402
from test_fileops_review7 import LOSSY, Lossy  # noqa: E402
import _v2_defaults  # noqa: E402
_v2_defaults.use()


class RelinkMoveEditedMeanwhile(Lossy):
    def test_the_destination_is_the_tracks_other_name_again(self):
        home = self.dest / "Real Artist" / "2001 - Real Album"
        home.mkdir(parents=True)
        os.link(self.f1, home / "01.F.mp3")
        os.link(self.f2, home / "02.S.mp3")
        prof = self.profile(operation="move", destination=str(self.dest), formats=LOSSY)
        p = pl.build([self.proposal()], prof)
        fe = p["albums"][0]["files"][0]
        self.assertTrue(fe.get("linked") and fe["op"] == "move", fe)
        real, hit = executor._fsync_dir, []

        def fsync_dir(d):
            real(d)
            if Path(d) == home and not hit:                  # (just after the new tags took dst's name)
                hit.append(1)
                tagio.write(self.f1, {"genre": ["Edited elsewhere"]})
        with mock.patch.object(executor, "_fsync_dir", fsync_dir):
            res = executor.Executor(p).run()
        self.assertTrue(hit)
        self.assertNotEqual(res["counts"], {"done": 1}, res)
        dst = Path(fe["dst"])
        self.assertTrue(self.f1.exists() and dst.exists())
        self.assertTrue(os.path.samefile(self.f1, dst), "dst left a detached file with TagMender's tags")
        self.assertEqual(tagio.read(self.f1).get("genre"), ["Edited elsewhere"])
        self.assertFalse(list(home.glob("*.tagmender*")) + list(home.glob(".*stag*")))


if __name__ == "__main__":
    unittest.main()
