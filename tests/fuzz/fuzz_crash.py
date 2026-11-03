# Kill a forked child at the N-th file primitive / journal write, for every N, then
# recover, undo, undo again; check the library comes back exactly and nothing hidden stays.
import os as _os, tempfile as _tempfile  # noqa: E402
_os.environ["XDG_CONFIG_HOME"] = _tempfile.mkdtemp(prefix="tm-config-")
_os.environ["XDG_STATE_HOME"] = _tempfile.mkdtemp(prefix="tm-state-")

import hashlib
import os
import shutil
import sys
import tempfile
import traceback
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "tests"))
import executor  # noqa: E402
import payload  # noqa: E402
import plan as pl  # noqa: E402
import tagio  # noqa: E402
import test_executor as te  # noqa: E402
from test_executor import Base, target, SHM  # noqa: E402

_CACHE = Path(tempfile.mkdtemp(prefix="audio-cache-"))
_real_audio = te.audio


def cached_audio(p, freq=440, **tags):
    key = hashlib.sha1(repr((p.suffix, freq, sorted(tags.items()))).encode()).hexdigest()
    c = _CACHE / (key + p.suffix)
    if not c.exists():
        _real_audio(c, freq, **tags)
    p.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(c, p)
    return p


te.audio = cached_audio
audio = cached_audio

PRIMS = [("os", "link"), ("os", "unlink"), ("os", "rename"), ("os", "replace"), ("os", "rmdir"),
         ("os", "mkdir")]


def armed(n, counter_file):
    """Patches that die at the n-th call of any primitive (counting all of them)."""
    state = {"k": 0}
    patches = []

    def tick(label):
        state["k"] += 1
        if state["k"] == n:
            Path(counter_file).write_text(label)
            os._exit(9)
    for mod, name in PRIMS:
        real = getattr(os, name)

        def make(real=real, name=name):
            def f(*a, **k):
                tick(f"before {name} {a[:2]}")
                r = real(*a, **k)
                tick(f"after {name} {a[:2]}")
                return r
            return f
        patches.append(mock.patch.object(os, name, make()))
    realw = executor.Journal.write

    def jw(j, **ev):
        tick(f"before journal {ev.get('ev')} {ev.get('state', '')}")
        realw(j, **ev)
        tick(f"after journal {ev.get('ev')} {ev.get('state', '')}")
    patches.append(mock.patch.object(executor.Journal, "write", jw))
    realc = shutil.copyfileobj

    def cp(a, b, length=0):
        state["k"] += 1
        if state["k"] == n:
            b.write(a.read(37))
            b.flush()
            Path(counter_file).write_text(f"mid copy {getattr(a, 'name', '')}")
            os._exit(9)
        return realc(a, b, length) if length else realc(a, b)
    patches.append(mock.patch.object(shutil, "copyfileobj", cp))
    return patches


def run_child(fn, n, counter_file):
    pid = os.fork()
    if pid == 0:
        try:
            ps = armed(n, counter_file)
            for p in ps:
                p.start()
            fn()
        except BaseException as exc:          # noqa: BLE001
            Path(counter_file).write_text(f"raised {exc!r}")
            os._exit(3)
        os._exit(0)
    _, status = os.waitpid(pid, 0)
    return os.waitstatus_to_exitcode(status)


def tree(roots, skip):
    out = {}
    for r in roots:
        for p in Path(r).rglob("*"):
            if any(str(p).startswith(str(s)) for s in skip) or not p.is_file():
                continue
            if p.suffix.lower() in (".flac", ".mp3"):
                try:
                    out[str(p)] = ("audio", tagio.read(p), payload.digest(p))
                except Exception as exc:
                    out[str(p)] = ("broken", str(exc))
            else:
                out[str(p)] = ("bytes", hashlib.sha256(p.read_bytes()).hexdigest())
    return out


def hidden(roots):
    return sorted(str(p) for r in roots for p in Path(r).rglob("*") if ".tm-" in p.name)


class Fuzz(Base):
    def roots(self):
        return [self.lib, self.dest] + ([self.shm] if getattr(self, "shm", None) else [])

    def fresh(self):
        if getattr(self, "shm", None):
            shutil.rmtree(self.shm, ignore_errors=True)
        self.shm = None
        self.tearDown()
        self.setUp()

    # --- scenarios: each returns a plan, after preparing the files ---------------
    def s_inplace_cover(self):
        from PIL import Image
        front = self.tmp / "front.jpg"
        Image.new("RGB", (60, 60), (1, 2, 3)).save(front)
        return pl.build([self.proposal(artwork={"front": str(front), "source": "t"},
                                       sidecars={str(self.f1): "[00:00.00]la"})], self.profile())

    def s_move(self):
        return pl.build([self.proposal()], self.profile(operation="move", destination=str(self.dest)))

    def s_move_nochange(self):
        t1, t2 = target(1, "First"), target(2, "Second")
        for f, t in ((self.f1, t1), (self.f2, t2)):
            tagio.write(f, dict(t, tagmender=["Resolved"]))
        return pl.build([self.proposal(targets=[t1, t2])], self.profile(operation="move", destination=str(self.dest)))

    def s_move_cross(self):
        self.shm = Path(tempfile.mkdtemp(dir=SHM))
        return pl.build([self.proposal()], self.profile(operation="move", destination=str(self.shm)))

    def s_copy(self):
        return pl.build([self.proposal()], self.profile(operation="copy", destination=str(self.dest)))

    def s_relink(self):
        prof = self.profile(operation="move", destination=str(self.dest))
        p0 = pl.build([self.proposal()], prof)
        d = Path(p0["albums"][0]["files"][0]["dst"])
        d.parent.mkdir(parents=True)
        os.link(self.f1, d)
        return pl.build([self.proposal()], prof)

    def s_relink_nochange(self):
        prof = self.profile(operation="move", destination=str(self.dest))
        t1, t2 = target(1, "First"), target(2, "Second")
        for f, t in ((self.f1, t1), (self.f2, t2)):
            tagio.write(f, dict(t, tagmender=["Resolved"]))
        prop = self.proposal(targets=[t1, t2])
        p0 = pl.build([prop], prof)
        d = Path(p0["albums"][0]["files"][0]["dst"])
        d.parent.mkdir(parents=True)
        os.link(self.f1, d)
        return pl.build([prop], prof)

    def s_rename_in_place(self):
        final = self.lib / "Real Artist" / "2001 - Real Album"
        shutil.move(str(self.album), final)
        shutil.rmtree(self.lib / "Some Artist")
        self.f1, self.f2 = final / "01 a.flac", final / "02 b.flac"
        return pl.build([self.proposal(files=[str(self.f1), str(self.f2)], source_dir=str(final))],
                        self.profile(operation="move", destination=str(self.lib)))

    def s_rollback(self):
        other = self.lib / "Other" / "Album2"
        g = audio(other / "01.flac", 700, album="X", title="x")
        prof = self.profile(operation="move", destination=str(self.dest))
        p = pl.build([self.proposal(),
                      self.proposal(source_dir=str(other), files=[str(g)],
                                    targets=[target(1, "Gee", album="Second Album")])], prof)
        d2 = Path(p["albums"][0]["files"][1]["dst"])
        d2.parent.mkdir(parents=True)
        d2.write_bytes(b"squatter")
        return p

    def s_rollback_cross(self):
        self.shm = Path(tempfile.mkdtemp(dir=SHM))
        prof = self.profile(operation="move", destination=str(self.shm))
        p = pl.build([self.proposal()], prof)
        d2 = Path(p["albums"][0]["files"][1]["dst"])
        d2.parent.mkdir(parents=True)
        d2.write_bytes(b"squatter")
        return p

    SCEN = ["s_inplace_cover", "s_move", "s_move_nochange", "s_move_cross", "s_copy", "s_relink",
            "s_relink_nochange", "s_rename_in_place", "s_rollback", "s_rollback_cross"]

    def check_restored(self, before, where, out):
        now = tree(self.roots(), [self.tmp / "state", self.tmp / "config"])
        # files that existed before (the squatter stays): compare
        problems = []
        for k, v in before.items():
            if k not in now:
                problems.append(f"MISSING {k}")
            elif now[k] != v:
                problems.append(f"CHANGED {k}: {str(v)[:80]} -> {str(now[k])[:80]}")
        for k in now:
            if k not in before:
                problems.append(f"EXTRA {k}")
        problems += [f"HIDDEN {h}" for h in hidden(self.roots())]
        return [f"[{where}] {p}  (out={out})" for p in problems]

    def test_fuzz(self):
        only = os.environ.get("SCEN")
        findings = []
        cf = str(Path(tempfile.mkdtemp()) / "c")
        for scen in self.SCEN:
            if only and scen not in only.split(","):
                continue
            for phase in ("run", "undo"):
                n = 1
                while n < 400:
                    self.fresh()
                    p = getattr(self, scen)()
                    before = tree(self.roots(), [self.tmp / "state", self.tmp / "config"])
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
                            # a second undo, as the docs say
                            try:
                                out2 = executor.undo(p["job"])
                            except executor.StepError as exc:
                                out2 = [f"StepError {exc}"]
                            probs2 = self.check_restored(before, f"{scen} {phase} n={n} at [{label}] 2nd undo", out2)
                            probs = probs2
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
    unittest.main()
