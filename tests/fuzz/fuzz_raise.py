# Error-injection fuzzer: the N-th file primitive raises OSError(EIO) once (no crash);
# the job goes on (album rollback), then undo (and undo again on problems) must bring the
# library back. Phase "undo": the N-th primitive of the undo raises, then undo again.
import sys, os
sys.argv = [sys.argv[0]]
__file__ = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fuzz_multi.py")
exec(open(__file__).read()
     .split("import unittest\nunittest.main")[0])
import errno


def raising(n):
    state = {"k": 0}
    patches = []

    def tick(label):
        state["k"] += 1
        if state["k"] == n:
            raise OSError(errno.EIO, f"injected at {label}")
    for mod, name in PRIMS:
        real = getattr(os, name)

        def make(real=real, name=name):
            def f(*a, **k):
                tick(f"{name} {a[:2]}")
                return real(*a, **k)
            return f
        patches.append(mock.patch.object(os, name, make()))
    realc = shutil.copyfileobj

    def cp(a, b, length=0):
        tick(f"copy {getattr(a, 'name', '')}")
        return realc(a, b, length) if length else realc(a, b)
    patches.append(mock.patch.object(shutil, "copyfileobj", cp))
    return patches, state


class Raise(Multi):
    SCEN = Fuzz.SCEN + Multi.SCEN

    def roots(self):
        return [self.lib, self.dest] + ([self.shm] if getattr(self, "shm", None) else [])

    def test_fuzz(self):
        only = os.environ.get("SCEN")
        findings = []
        for scen in self.SCEN:
            if only and scen not in only.split(","):
                continue
            for phase in ("run", "undo"):
                n = 1
                while n < 1500:
                    self.fresh()
                    p = getattr(self, scen)()
                    before = tree(self.roots(), [self.tmp / "state", self.tmp / "config"])
                    self._before_dirs = set(dirs(self.roots(), [self.tmp / "state", self.tmp / "config"]))
                    where = f"{scen} {phase} n={n}"
                    try:
                        ps, st = raising(n)
                        for x in ps:
                            x.start()
                        try:
                            if phase == "run":
                                res = executor.Executor(p).run()
                            else:
                                for x in ps:
                                    x.stop()
                                executor.Executor(p).run()
                                for x in ps:
                                    x.start()
                                out0 = executor.undo(p["job"])
                        except Exception as exc:                       # noqa
                            res = f"raised {exc!r}"
                        finally:
                            for x in ps:
                                try:
                                    x.stop()
                                except RuntimeError:
                                    pass
                        if st["k"] < n:
                            break
                        if not executor.Journal(executor.job_dir(p["job"])).read():
                            n += 1
                            continue
                        if executor.pending_jobs():
                            executor.recover(p["job"])
                        h = hidden(self.roots())
                        if h:
                            findings.append(f"{where}: hidden after the job {h}")
                        try:
                            out = executor.undo(p["job"])
                        except executor.StepError as exc:
                            if "already undone" not in str(exc):
                                raise
                            out = ["(already undone)"]
                        probs = self.check_restored(before, where, out)
                        if any(o.startswith("PROBLEM") for o in out):
                            try:
                                out2 = executor.undo(p["job"])
                            except executor.StepError as exc:
                                out2 = [f"StepError {exc}"]
                            probs = self.check_restored(before, where + " 2nd undo", out2)
                        findings += probs
                    except Exception:
                        findings.append(f"{where}: EXC {traceback.format_exc()[-600:]}")
                    n += 1
                print(f"{scen} {phase}: {n} injection points", flush=True)
        print("\n".join(findings) if findings else "NO FINDINGS")


import unittest
unittest.main(defaultTest="Raise")
