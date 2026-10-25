# Strict variant of fuzz_crash.py: an undo where nothing was edited must also end without any
# PROBLEM line (an undo cut short must be finished by running it again), for crashes during
# the job and during the undo, over the scenarios of fuzz_crash.py and fuzz_more.py.
import os
import sys
import tempfile
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fuzz_crash as fc  # noqa: E402
from fuzz_crash import pl, target, audio, executor, tagio, run_child, hidden, tree  # noqa: E402
import fuzz_more  # noqa: E402


class Strict(fuzz_more.FuzzMore):
    def s_copy_rerun(self):
        prof = self.profile(operation="copy", destination=str(self.dest))
        p0 = pl.build([self.proposal()], prof)
        executor.Executor(p0).run()
        return pl.build([self.proposal(targets=[fc.target(1, "First", genre=["Rock"]),
                                                fc.target(2, "Second", genre=["Rock"])])], prof)

    def s_relink_both(self):
        prof = self.profile(operation="move", destination=str(self.dest))
        p0 = pl.build([self.proposal()], prof)
        for fe in p0["albums"][0]["files"]:
            d = Path(fe["dst"])
            d.parent.mkdir(parents=True, exist_ok=True)
            os.link(fe["src"], d)
        return pl.build([self.proposal()], prof)

    SCEN = os.environ.get("SCEN_STRICT", "s_inplace_cover,s_move,s_move_cross,s_copy,s_relink_both,s_relink_copy,"
                                    "s_rename_in_place,s_move_junk,s_copy_rerun,s_move_discs").split(",")

    def test_fuzz(self):
        findings = []
        cf = str(Path(tempfile.mkdtemp()) / "c")
        for scen in self.SCEN:
            for phase in ("run", "undo"):
                n = 1
                while n < 400:
                    self.fresh()
                    p = getattr(self, scen)()
                    before = tree(self.roots(), [self.tmp / "state", self.tmp / "config"])
                    label = ""
                    try:
                        if phase == "run":
                            code = run_child(lambda: executor.Executor(p).run(), n, cf)
                            label = Path(cf).read_text() if os.path.exists(cf) else ""
                            if code == 0:
                                break
                            if not executor.Journal(executor.job_dir(p["job"])).read():
                                n += 1
                                continue
                            r1 = executor.recover(p["job"])
                            out = executor.undo(p["job"])
                        else:
                            executor.Executor(p).run()
                            code = run_child(lambda: executor.undo(p["job"]), n, cf)
                            label = Path(cf).read_text() if os.path.exists(cf) else ""
                            if code == 0:
                                break
                            try:
                                out = executor.undo(p["job"])
                            except executor.StepError as exc:
                                if "already undone" not in str(exc):
                                    raise
                                out = ["(already undone)"]
                        probs = self.check_restored(before, f"{scen} {phase} n={n} at [{label}]", out)
                        if any(o.startswith("PROBLEM") for o in out):
                            probs.append(f"[{scen} {phase} n={n} at [{label}]] PROBLEM: {out}")
                        findings += probs
                    except Exception:
                        findings.append(f"{scen} {phase} n={n} at [{label}]: EXC {traceback.format_exc()[-400:]}")
                    if os.path.exists(cf):
                        os.unlink(cf)
                    n += 1
                print(f"{scen} {phase}: {n} kill points", flush=True)
        print("\n".join(findings) if findings else "NO FINDINGS")


if __name__ == "__main__":
    import unittest
    unittest.main(defaultTest="Strict")
