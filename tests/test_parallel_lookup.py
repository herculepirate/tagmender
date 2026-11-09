# Several albums looked up at once (Engine.plan_each with lookup_workers, overview.run_batches):
# they really overlap, the results are the same and in the same order as one at a time,
# nothing is lost on failure or Cancel. No network, no real music.
import os as _os, tempfile as _tempfile  # noqa: E402
_os.environ["XDG_CONFIG_HOME"] = _tempfile.mkdtemp(prefix="tm-config-")
_os.environ["XDG_STATE_HOME"] = _tempfile.mkdtemp(prefix="tm-state-")
_os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

import engine  # noqa: E402
import exclusions as ex  # noqa: E402
import match_albums as ma  # noqa: E402
import plan as pl  # noqa: E402
import profiles as pf  # noqa: E402
import tagio  # noqa: E402,F401
from gui import overview  # noqa: E402


class Http:
    db = None

    def __init__(self):
        self.closed = 0

    def thread_clone(self):
        return Http()

    def close_clone(self):
        self.closed += 1


class Group:
    def __init__(self, n):
        self.folder, self.files, self.others = Path(f"/none/{n}"), [Path(f"/none/{n}/1.flac")], []


def prop(g):
    return pl.Proposal(source_dir=str(g.folder), files=[str(f) for f in g.files], status="unresolved", reason="x")


class AtOnce(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.saved = (ex.CONFIG_DIR, ex.STATE_DIR)
        ex.CONFIG_DIR, ex.STATE_DIR = self.tmp / "c", self.tmp / "s"
        ex._write(ex.rules_file(), {"rules": []})
        self.groups = [Group(i) for i in range(6)]
        self.keys = [engine.group_key(g.folder, g.files) for g in self.groups]

    def tearDown(self):
        ex.CONFIG_DIR, ex.STATE_DIR = self.saved

    def run_it(self, workers, identify, cancel=None):
        prof = pf.from_dict({"name": "default", "providers": {"web": {"enabled": False}},
                             "artwork": {"enabled": False}})
        emitted, started = [], []
        with mock.patch.object(engine.Engine, "albums_in", lambda s, p, skip=(): list(self.groups)), \
                mock.patch.object(engine.Engine, "identify_all", lambda s, g: identify(g)), \
                mock.patch.object(engine.Engine, "add_artwork", lambda s, p: None), \
                mock.patch.object(engine.Engine, "apply_level", lambda s, p: None):
            e = engine.Engine(prof, http=Http())
            e.lookup_workers = workers
            n = e.plan_each("/none", self.keys, lambda k, plan: emitted.append((k, plan)),
                            progress=lambda i, what: started.append(i), cancel=cancel)
        return n, emitted, started

    def test_albums_really_overlap(self):
        three = threading.Barrier(3, timeout=20)         # (one at a time: this would time out)

        def identify(g):
            if g is self.groups[0] or g is self.groups[1] or g is self.groups[2]:
                three.wait()
            return [prop(g)]
        n, _emitted, started = self.run_it(3, identify)
        self.assertEqual(n, 6)
        self.assertEqual(sorted(started), list(range(6)))

    def test_same_result_and_order_as_one_at_a_time(self):
        def rows(workers):
            _n, em, _s = self.run_it(workers, lambda g: [prop(g)])
            last = em[len(self.keys):]                   # (the final plan of them all)
            return [k for k, _p in last], [[a["source_dir"] for a in p["albums"]] for _k, p in last]
        self.assertEqual(rows(3), rows(1))
        self.assertEqual(rows(3)[0], self.keys)

    def test_one_album_failing_is_that_album_only(self):
        def identify(g):
            if g is self.groups[2]:
                raise RuntimeError("bad album")
            return [prop(g)]
        n, emitted, _s = self.run_it(3, identify)
        self.assertEqual(n, 6)
        self.assertEqual(len({k for k, _p in emitted}), 6)

    def _a_file_gone_while_planning(self, workers):
        gone = self.groups[2]
        gone.files.append(gone.folder / "2.flac")
        self.keys[2] = engine.group_key(gone.folder, gone.files)
        missing = str(gone.files[0])
        read = tagio.read

        def tags_of(path, *a, **k):                      # (the share loses the file just as it is planned)
            if str(path) == missing:
                raise FileNotFoundError(2, "No such file or directory", missing)
            return read(path, *a, **k)

        def identify(g):
            p = prop(g)
            if g is gone:                                # (identified: the planner reads its files)
                p.status, p.provider, p.release_id, p.confidence = "identified", "discogs", "1", 99.0
                p.targets = [{"title": ["T"], "tracknumber": ["1"]} for _f in g.files]
            return [p]
        with mock.patch.object(tagio, "read", tags_of):
            n, emitted, _s = self.run_it(workers, identify)
        self.assertEqual(n, 6)
        self.assertEqual(len({k for k, _p in emitted}), 6)                  # every album has its row
        key = self.keys[2]
        last = [p for k, p in emitted if k == key][-1]["albums"][0]
        self.assertEqual(last["status"], "skipped")                         # (nothing of it is changed)
        self.assertEqual(last["files"], [])
        self.assertIn("No such file", last["identification"]["reason"])

    def test_a_file_gone_stops_that_album_only_one_at_a_time(self):
        self._a_file_gone_while_planning(1)

    def test_a_file_gone_stops_that_album_only_at_once(self):
        self._a_file_gone_while_planning(3)

    def test_the_album_innermost_is_the_one_a_gone_file_belongs_to(self):
        outer = pl.Proposal(source_dir="/d", files=["/d/1.flac"], status="identified")
        inner = pl.Proposal(source_dir="/d/sub", files=["/d/sub/1.flac"], status="identified")
        calls = []

        class Planner:
            def build(self, props):
                calls.append(props)
                if len(calls) == 1:
                    raise FileNotFoundError(2, "gone", "/d/sub/booklet.jpg")
                return {"albums": []}
        _out, groups = engine._build_despite_gone(Planner, [[outer], [inner]])
        self.assertIs(groups[0][0], outer)
        self.assertEqual(groups[1][0].status, "unresolved")

    def test_an_error_on_no_album_of_the_look_up_still_ends_it(self):
        def planner():
            raise FileNotFoundError(2, "gone", "/elsewhere/x")
        q = pl.Proposal(source_dir="/none/1", files=["/none/1/1.flac"], status="identified")
        with self.assertRaises(FileNotFoundError):
            engine._build_despite_gone(planner, [[q]])

    def test_cancel_stops_new_albums_and_ends(self):
        stop = threading.Event()

        def identify(g):
            stop.set()
            return [prop(g)]
        n, _emitted, _s = self.run_it(3, identify, cancel=stop.is_set)
        self.assertLess(n, 6)

    def test_a_worker_failing_outright_ends_the_look_up_with_its_error(self):
        with mock.patch.object(engine, "engine_for_thread", side_effect=RuntimeError("no engine")):
            with self.assertRaisesRegex(RuntimeError, "no engine"):
                self.run_it(3, lambda g: [prop(g)])

    def test_an_http_that_cannot_be_cloned_works_one_at_a_time(self):
        http = Http()
        http.thread_clone = lambda: None
        prof = pf.from_dict({"name": "default", "providers": {"web": {"enabled": False}}})
        e = engine.Engine(prof, http=http)
        self.assertEqual(e._thread_clones(3), [])

    def test_engines_share_no_album_state(self):
        seen = {}

        def identify(g):
            seen.setdefault(threading.current_thread().name, set()).add(str(g.folder))
            return [prop(g)]
        self.run_it(3, identify)
        folders = [f for s in seen.values() for f in s]
        self.assertEqual(len(folders), len(set(folders)))          # (each album looked up once)


class Batches(unittest.TestCase):
    def test_separate_folders_run_side_by_side(self):
        three = threading.Barrier(3, timeout=20)

        def run(b, report, workers):
            self.assertEqual(workers, 1)
            three.wait()
            report("partial", b)
            return 1
        got = []
        n = overview.run_batches(["a", "b", "c", "d", "e", "f"][:3], run, lambda: False, got.append, got.append, 3)
        self.assertEqual((n, sorted(got)), (3, ["a", "b", "c"]))

    def test_one_batch_gets_every_worker(self):
        said = {}

        def run(b, report, workers):
            said["w"] = workers
            return 2
        self.assertEqual(overview.run_batches(["a"], run, lambda: False, print, print, 3), 2)
        self.assertEqual(said["w"], 3)

    def test_a_failing_batch_raises_on_the_look_up_thread(self):
        def run(b, report, workers):
            raise ValueError("nope")
        with self.assertRaisesRegex(ValueError, "nope"):
            overview.run_batches(["a", "b"], run, lambda: False, print, print, 3)

    def test_decoded_barcode_memory_cannot_raise_across_albums(self):
        ma._DECODED_ON.clear()
        ma._decoded_on(["111"], "/one/a.jpg")
        ma._decoded_on(["111"], "/two/a.jpg")                     # (two albums with one barcode)
        self.assertEqual(ma._DECODED_ON["111"], {"/one/a.jpg", "/two/a.jpg"})

    def test_real_connections_are_cloned_and_work_from_other_threads(self):
        with tempfile.TemporaryDirectory() as d:
            saved = ex.STATE_DIR
            ex.STATE_DIR = Path(d)
            try:
                e = engine.Engine(pf.from_dict({"name": "default"}), cache=Path(d) / "c.sqlite")
                clones = e._thread_clones(3)
                self.assertEqual(len(clones), 3)
                self.assertIsNotNone(clones[0].thread_clone())           # (the matcher's own threads clone it again)
                got = []

                def use(c):
                    got.append(c.db.execute("SELECT 1").fetchone()[0])
                    c.close_clone()
                ts = [threading.Thread(target=use, args=(c,)) for c in clones]
                [t.start() for t in ts]
                [t.join() for t in ts]
                self.assertEqual(got, [1, 1, 1])
            finally:
                ex.STATE_DIR = saved


if __name__ == "__main__":
    unittest.main()
