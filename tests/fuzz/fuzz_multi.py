# Multi-album crash fuzzer: kill at every primitive during run (then recover, undo, undo)
# and during undo (then undo again), for plans whose albums interact.
import sys, os
sys.argv = [sys.argv[0]]
_HERE = os.path.dirname(os.path.abspath(__file__))
_src = open(os.path.join(_HERE, "fuzz_crash.py")).read().split('if __name__ == "__main__":')[0]
_src = _src.replace("REPO = Path(__file__).resolve().parent.parent.parent",
                    "REPO = Path(%r)" % os.path.dirname(os.path.dirname(_HERE)))
exec(_src)


def dirs(roots, skip):
    return sorted(str(p) for r in roots for p in Path(r).rglob("*")
                  if p.is_dir() and not any(str(p).startswith(str(s)) for s in skip))


class Multi(Fuzz):
    def roots(self):
        return [self.lib, self.dest]

    def _b_into_a(self, order, squat=None):
        (self.album / "Scans").mkdir()
        (self.album / "Scans" / "back.jpg").write_bytes(b"back")
        other = self.lib / "Other" / "Album2"
        g1 = audio(other / "01 g.flac", 700, album="X", title="g")
        g2 = audio(other / "02 h.flac", 750, album="X", title="h")
        (other / "g.log").write_text("log of B")
        prof = self.profile(operation="move", destination=str(self.lib))
        A = self.proposal()
        B = self.proposal(source_dir=str(other), files=[str(g1), str(g2)],
                          targets=[target(1, "Gee", album="Some Album", artist="Some Artist"),
                                   target(2, "Hey", album="Some Album", artist="Some Artist")])
        third = self.lib / "Third" / "Al3"
        h1 = audio(third / "01 x.flac", 800, album="Y", title="x")
        C = self.proposal(source_dir=str(third), files=[str(h1)], targets=[target(1, "Ex", album="Al3", artist="Third")])
        props = {"A": A, "B": B, "C": C}
        p = pl.build([props[k] for k in order], prof)
        assert all(a["status"] == "ready" for a in p["albums"]), pl.preview(p)
        if squat is not None:
            ai, fi = squat
            d = Path(p["albums"][ai]["files"][fi]["dst"])
            d.parent.mkdir(parents=True, exist_ok=True)
            d.write_bytes(b"squatter")
        return p

    def m_b_into_a_ABC(self):
        return self._b_into_a("ABC")

    def m_b_into_a_BAC(self):
        return self._b_into_a("BAC")

    def m_b_into_a_ABC_Bfails(self):
        return self._b_into_a("ABC", squat=(1, 1))

    def m_b_into_a_BAC_Bfails(self):
        return self._b_into_a("BAC", squat=(0, 1))

    def m_b_into_a_ABC_Cfails(self):
        return self._b_into_a("ABC", squat=(2, 0))

    def _a_into_b(self, order, squat=None):
        # B's source folder is A's destination folder
        bdir = self.lib / "Real Artist" / "2001 - Real Album"
        g1 = audio(bdir / "01 g.flac", 700, album="Other Album", title="g")
        (bdir / "rip.log").write_text("B's own log")
        (bdir / "Art").mkdir()
        (bdir / "Art" / "b.jpg").write_bytes(b"B art")
        prof = self.profile(operation="move", destination=str(self.lib))
        A = self.proposal()
        B = self.proposal(source_dir=str(bdir), files=[str(g1)],
                          targets=[target(1, "Gee", album="Other Album")])
        props = {"A": A, "B": B}
        p = pl.build([props[k] for k in order], prof)
        assert all(a["status"] == "ready" for a in p["albums"]), pl.preview(p)
        if squat is not None:
            ai, fi = squat
            d = Path(p["albums"][ai]["files"][fi]["dst"])
            d.parent.mkdir(parents=True, exist_ok=True)
            d.write_bytes(b"squatter")
        return p

    def m_a_into_b_AB(self):
        return self._a_into_b("AB")

    def m_a_into_b_BA(self):
        return self._a_into_b("BA")

    def m_a_into_b_BA_Afails(self):
        return self._a_into_b("BA", squat=(1, 1))

    SCEN = [n for n in dir() if n.startswith("m_")] if False else [
        "m_b_into_a_ABC", "m_b_into_a_BAC", "m_b_into_a_ABC_Bfails", "m_b_into_a_BAC_Bfails",
        "m_b_into_a_ABC_Cfails", "m_a_into_b_AB", "m_a_into_b_BA", "m_a_into_b_BA_Afails"]

    def check_restored(self, before, where, out):
        probs = super().check_restored(before, where, out)
        now_d = set(dirs(self.roots(), [self.tmp / "state", self.tmp / "config"]))
        extra = sorted(now_d - self._before_dirs)
        missing = sorted(self._before_dirs - now_d)
        probs += [f"[{where}] DIR EXTRA {d}" for d in extra if not any(Path(d).iterdir())]
        probs += [f"[{where}] DIR MISSING {d}" for d in missing]
        return probs

    def test_fuzz(self):
        only = os.environ.get("SCEN")
        findings = []
        cf = str(Path(tempfile.mkdtemp()) / "c")
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
                            h = hidden(self.roots())
                            if h:
                                findings.append(f"{scen} {phase} n={n} at [{label}]: hidden after recover {h} {r1}")
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
                            try:
                                out2 = executor.undo(p["job"])
                            except executor.StepError as exc:
                                out2 = [f"StepError {exc}"]
                            probs = self.check_restored(before, f"{scen} {phase} n={n} at [{label}] 2nd undo", out2)
                        findings += probs
                    except Exception:
                        findings.append(f"{scen} {phase} n={n} at [{label}]: EXC {traceback.format_exc()[-600:]}")
                    if os.path.exists(cf):
                        os.unlink(cf)
                    n += 1
                print(f"{scen} {phase}: {n} kill points", flush=True)
        print("\n".join(findings) if findings else "NO FINDINGS")


import unittest
unittest.main(defaultTest="Multi")
