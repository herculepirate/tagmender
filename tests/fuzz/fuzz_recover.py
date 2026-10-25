import sys, os
sys.argv = [sys.argv[0]]
src = open(__import__("os").path.join(__import__("os").path.dirname(__import__("os").path.abspath(__file__)), "fuzz_crash.py")).read().split('if __name__ == "__main__":')[0]
exec(src)


class FuzzRecover(Fuzz):
    def test_recover_killed(self):
        only = os.environ.get("SCEN")
        step = int(os.environ.get("STEP", "3"))
        findings = []
        cf = str(Path(tempfile.mkdtemp()) / "c")
        for scen in self.SCEN:
            if only and scen not in only.split(","):
                continue
            n = 1
            tried = 0
            while n < 400:
                m = 1
                stop_outer = False
                while m < 200:
                    self.fresh()
                    p = getattr(self, scen)()
                    before = tree(self.roots(), [self.tmp / "state", self.tmp / "config"])
                    code = run_child(lambda: executor.Executor(p).run(), n, cf)
                    if code == 0:
                        stop_outer = True
                        break
                    if not executor.Journal(executor.job_dir(p["job"])).read():
                        break
                    code2 = run_child(lambda: executor.recover(p["job"]), m, cf)
                    label = Path(cf).read_text() if os.path.exists(cf) else ""
                    if code2 == 0:
                        break
                    tried += 1
                    try:
                        r = executor.recover(p["job"])
                        h = hidden(self.roots())
                        if h:
                            findings.append(f"{scen} run n={n} recover m={m} [{label}]: hidden after 2nd recover {h}")
                        out = executor.undo(p["job"])
                        if any(o.startswith("PROBLEM") for o in out):
                            out = executor.undo(p["job"])
                        findings += self.check_restored(before, f"{scen} run n={n} recover m={m} [{label}]", out)
                    except Exception:
                        findings.append(f"{scen} n={n} m={m} [{label}] EXC {traceback.format_exc()[-300:]}")
                    if os.path.exists(cf):
                        os.unlink(cf)
                    m += 1
                if stop_outer:
                    break
                n += step
            print(f"{scen}: {tried} recover kills", flush=True)
        print("\n".join(findings) if findings else "NO FINDINGS")


import unittest
unittest.main(defaultTest="FuzzRecover")
