# Twenty-third independent review (A): a job cut short (the machine lost power while TagMender
# was moving an album). `tagmender recover` puts the album back and says "put back as it was
# before the job; run the plan again to do it". Doing just that - `tagmender apply PLAN` with the
# plan the job ran from - is refused: "job ... has already run (use a new plan ...)". The
# message tells the user to do something TagMender will not do.
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
        self.assertIn("run the plan again to do it", out)
        rc, out, err = cli("apply", str(saved), "--yes")
        self.assertEqual(rc, 0, f"recover said to run the plan again; apply says: {err.strip()}")


if __name__ == "__main__":
    unittest.main()
