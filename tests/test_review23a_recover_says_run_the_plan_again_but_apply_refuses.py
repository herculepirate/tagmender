# Twenty-third independent review (A): a job cut short (the machine lost power while TagMender
# was moving an album). `tagmender recover` put the album back and said "run the plan again to
# do it" - but `tagmender apply PLAN` with the plan the job ran from is refused (a job runs
# once). (Review 24A: running it as a new job raced recover and undo.) Recover now says to plan
# the album again, naming it; the same plan is still refused; a new plan of it runs.
import io
import contextlib
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _review23a_common import Case, executor, pl  # noqa: E402

import tagmender  # noqa: E402


def cli(*argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        rc = tagmender.main(list(argv))
    return rc, out.getvalue(), err.getvalue()


class RecoverThenRunAgain(Case):
    def test_apply_the_plan_again_after_recover(self):
        self.album(self.lib / "Artist A" / "2001 - First", "First", "Artist A")
        p = self.plan(self.profile("move"))
        saved = self.root / "plan.json"
        pl.save(p, saved)
        real = executor.Journal.write

        def write(j, **ev):                              # the power goes as the first track is staged
            real(j, **ev)
            if ev.get("ev") == "file" and ev.get("state") == "staged":
                os._exit(9)
        pid = os.fork()
        if pid == 0:
            try:
                with mock.patch.object(executor.Journal, "write", write):
                    executor.Executor(p).run()
            finally:
                os._exit(0)
        _, status = os.waitpid(pid, 0)
        self.assertEqual(os.waitstatus_to_exitcode(status), 9)
        rc, out, err = cli("recover")
        self.assertIn("plan it again to do it (tagmender run", out)
        self.assertIn(str(self.lib / "Artist A" / "2001 - First"), out)
        rc, out, err = cli("apply", str(saved), "--yes")
        self.assertNotEqual(rc, 0)
        self.assertIn("plan again", err)
        res = executor.Executor(self.plan(self.profile("move"))).run()
        self.assertEqual(res["counts"], {"done": 1}, res)


if __name__ == "__main__":
    unittest.main()
