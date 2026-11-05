# Speed without a change in what is decided: the files' metadata is kept from one watcher pass
# to the next (and from List first to Look Up in the app), the watcher's snapshot stats each
# entry once, and the Audiophile measurements run side by side, each file's audio hashed once.
import os as _os, tempfile as _tempfile
_os.environ["XDG_CONFIG_HOME"] = _tempfile.mkdtemp(prefix="tm-config-")
_os.environ["XDG_STATE_HOME"] = _tempfile.mkdtemp(prefix="tm-state-")
import os
import subprocess
import sys
import threading
import time
import types
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "tests"))
import mutagen  # noqa: E402
import engine  # noqa: E402
import executor  # noqa: E402
import exclusions as ex  # noqa: E402
import levels  # noqa: E402
import payload  # noqa: E402
import profiles as pf  # noqa: E402
import tagio  # noqa: E402
import watcher  # noqa: E402
import test_engine as te  # noqa: E402
from test_watch_folders import DropBase, settle  # noqa: E402


class Opens:
    """Counts mutagen.File calls, per path."""
    def __init__(self):
        self.calls = []
        self.real = mutagen.File

    def __call__(self, path, *a, **k):
        self.calls.append(str(path))
        return self.real(path, *a, **k)


def counting(target, name):
    real = getattr(target, name)
    calls = []

    def wrapper(path, *a, **k):
        calls.append(str(path))
        return real(path, *a, **k)
    return mock.patch.object(target, name, wrapper), calls


# --------------------------------------------------------------------------
# 1. the files' metadata kept across passes
# --------------------------------------------------------------------------
class WatcherKeepsMetadata(DropBase):
    def test_a_second_pass_opens_nothing_and_a_retagged_file_alone_is_read_again(self):
        prof = self.profile(unattended=False)
        d = self.drop("Band X", "Album X", n=3)
        w = watcher.Watcher([prof], once=True)
        first = w.tick()
        self.assertEqual(list(first.values()), ["planned"], first)
        c = Opens()
        with mock.patch.object(mutagen, "File", c):
            self.assertEqual(w.tick(), {})
        self.assertEqual(c.calls, [])                                    # nothing opened again
        # one file retagged (its ctime changes): only it is read again, and the album is due
        f = sorted(d.glob("*.flac"))[0]
        tagio.write(f, {"comment": ["retagged"]})
        old = time.time() - 7200
        for p in (f, d, self.inbox):                 # (settled: the other files' stamps untouched)
            os.utime(p, (old, old))
        said = []
        due = watcher.Watcher.due

        def recording(self_, *a, **k):
            r = due(self_, *a, **k)
            said.append(r)
            return r
        patch, reads = counting(ex, "_read_meta")
        with patch, mock.patch.object(watcher.Watcher, "due", recording), \
                mock.patch.object(engine.Engine, "identify_all", side_effect=RuntimeError("no lookup here")):
            w.tick()
        self.assertEqual(said, [(True, "changed")])
        self.assertEqual(reads, [str(f)])

    def test_what_a_pass_did_not_see_is_dropped(self):
        prof = self.profile(unattended=False)
        d = self.drop("Band X", "Album X", n=2)
        w = watcher.Watcher([prof], once=True)
        w.tick()
        self.assertEqual({k[0] for k in w._meta}, {str(f) for f in d.glob("*.flac")})
        gone = sorted(d.glob("*.flac"))[0]
        gone.unlink()
        settle(self.inbox)
        w.tick()
        self.assertNotIn(str(gone), {k[0] for k in w._meta})


class AppKeepsMetadata(te.Base):
    def test_look_up_after_list_first_opens_only_the_album_looked_up(self):
        a = self.album("Band A", "Album A")
        b = self.album("Band B", "Album B")
        store = {}
        e = self.engine(te.FakeHttp())
        listed = e.listing(self.lib, meta=store)
        self.assertEqual(len(listed["albums"]), 2)
        key = next(x["key"] for x in listed["albums"] if x["source_dir"] == str(a))
        c = Opens()
        with mock.patch.object(mutagen, "File", c):
            plan = self.engine(te.FakeHttp()).plan(self.lib, only={key}, meta=store)
        self.assertEqual([x["source_dir"] for x in plan["albums"]], [str(a)])
        self.assertTrue(c.calls)
        self.assertFalse([p for p in c.calls if Path(p).parent == b], c.calls)
        # (and as before without the store: every file of the folder opened to find the albums)
        c = Opens()
        with mock.patch.object(mutagen, "File", c):
            self.engine(te.FakeHttp()).plan(self.lib, only={key})
        self.assertTrue([p for p in c.calls if Path(p).parent == b])

    def test_same_albums_and_plan_with_and_without_the_store(self):
        self.album("Band A", "Album A")
        self.album("Band B", "Album B", folder="mixed")
        self.album("Band C", "Album C", folder="mixed")
        store = {}
        plain = self.engine(te.FakeHttp()).listing(self.lib)
        kept = self.engine(te.FakeHttp()).listing(self.lib, meta=store)
        again = self.engine(te.FakeHttp()).listing(self.lib, meta=store)
        strip = lambda p: [{k: v for k, v in x.items()} for x in p["albums"]]    # noqa: E731
        self.assertEqual(strip(plain), strip(kept))
        self.assertEqual(strip(plain), strip(again))

    def test_the_app_hands_its_store_to_the_engine(self):
        import inspect
        from gui import overview
        src = inspect.getsource(overview)
        self.assertIsInstance(overview.META_STORE, dict)
        self.assertEqual(src.count("meta=META_STORE"), 3)               # List first, Look Up, Preview

    def test_a_file_with_a_large_picture_is_not_kept(self):
        d = self.lib / "Pics"
        d.mkdir(parents=True)
        f = d / "01.mp3"
        subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "sine=duration=1", "-ar", "8000",
                        "-c:a", "libmp3lame", "-metadata", "album=Pics", str(f), "-y"], check=True)
        from mutagen.id3 import APIC, ID3
        tags = ID3(f)
        tags.add(APIC(encoding=3, mime="image/jpeg", type=3, desc="front", data=os.urandom(600_000)))
        tags.save(f)
        small = d / "02.mp3"
        subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "sine=duration=1", "-ar", "8000",
                        "-c:a", "libmp3lame", "-metadata", "album=Pics", str(small), "-y"], check=True)
        store = {}
        with ex.meta_memo(store, True):
            big = ex.read_meta(f)
            ex.read_meta(small)
        self.assertGreater(sum(len(v) for vals in big.values() for v in vals), ex.META_STORE_MAX)
        self.assertEqual([k[0] for k in store], [str(small)])
        # (inside one identification it is still read once)
        patch, reads = counting(ex, "_read_meta")
        with patch, ex.meta_memo(store, True):
            with ex.meta_memo():
                ex.read_meta(f)
                ex.read_meta(f)
        self.assertEqual(reads, [str(f)])

    def test_store_hands_out_copies_and_rereads_changed_files(self):
        d = self.album("Band A", "Album A", n=1)
        f = next(d.glob("*.flac"))
        store = {}
        with ex.meta_memo(store):
            ex.read_meta(f)["album"].append("junk")
        with ex.meta_memo(store):
            self.assertEqual(ex.read_meta(f)["album"], ["Album A"])
        tagio.write(f, {"album": ["Other"]})
        with ex.meta_memo(store):
            self.assertEqual(ex.read_meta(f)["album"], ["Other"])


# --------------------------------------------------------------------------
# 2. the snapshot: one stat per entry, the same answer
# --------------------------------------------------------------------------
def old_snapshot(folder, files=None, deep=True):
    """watcher.snapshot as 0.8.0 had it (rglob, is_file/is_dir and stat per entry)."""
    entries, newest, partial = [], 0.0, False
    if files is not None:
        if deep:
            try:
                newest = os.stat(folder).st_mtime
            except OSError:
                pass
        try:
            around = Path(folder).rglob("*") if deep else Path(folder).iterdir()
            for p in around:
                n = p.name.lower()
                if executor.STAGE_TAG in n:
                    continue
                if watcher._partial(p.name):
                    partial = True
                    break
                try:
                    if deep and (p.is_file() or p.is_dir()):
                        newest = max(newest, watcher._changed_at(p.stat()))
                    elif not deep and p.suffix.lower() in ex.AUDIO_EXT and p.is_file():
                        newest = max(newest, watcher._changed_at(p.stat()))
                except OSError:
                    pass
        except OSError:
            pass
    for p in sorted(Path(x) for x in files) if files is not None else sorted(Path(folder).rglob("*")):
        if not p.is_file():
            continue
        name = p.name.lower()
        if executor.STAGE_TAG in name:
            continue
        if watcher._partial(p.name):
            partial = True
            continue
        try:
            st = p.stat()
        except FileNotFoundError:
            continue
        entries.append(p)
        newest = max(newest, watcher._changed_at(st))
    return ex.signature(entries), newest, partial


class SnapshotParity(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(_tempfile.mkdtemp())
        self.addCleanup(__import__("shutil").rmtree, self.tmp, True)

    def tree(self, partial_in=None):
        root = self.tmp / f"case-{partial_in}"
        album = root / "Album"
        other = root / "elsewhere"
        for d in (album / "scans" / "inner", album / "CD2", other):
            d.mkdir(parents=True, exist_ok=True)
        stamp = 1_600_000_000
        for i, p in enumerate([album / "01.flac", album / "02.mp3", album / "CD2" / "01.flac",
                               album / "scans" / "front.jpg", album / "scans" / "inner" / "x.png",
                               other / "far.flac", root / "loose.flac", album / f"03.flac{executor.STAGE_TAG}x",
                               album / f"x{executor.STAGE_TAG}y.flac"]):
            p.write_bytes(b"x" * (10 + i))
            os.utime(p, (stamp + i * 7, stamp + i * 7))
        os.symlink(other, album / "linked")                       # (listed, never looked into)
        os.symlink(album / "missing.flac", album / "broken.flac")   # (a broken link)
        os.symlink(album / "01.flac", album / "link.flac")
        if partial_in:
            p = album / partial_in / "05.flac.part" if partial_in != "." else album / "05.flac.part"
            p.write_bytes(b"part")
            os.utime(p, (stamp + 999, stamp + 999))
        for d in (album / "scans" / "inner", album / "scans", album / "CD2", album, other, root):
            os.utime(d, (stamp - 5, stamp - 5))
        return root, album

    def test_same_signature_newest_and_partial_as_before(self):
        for partial_in in (None, ".", "scans", "scans/inner", "CD2"):
            root, album = self.tree(partial_in)
            files_sets = [sorted(p for p in album.rglob("*") if p.suffix in (".flac", ".mp3")),
                          sorted(album.glob("*.flac")), [album / "gone.flac", album / "01.flac"],
                          [root / "loose.flac"]]
            for folder in (album, root, album / "nothing-here"):
                for deep in (True, False):
                    for files in files_sets + [None]:
                        if files is None and not folder.exists():
                            continue
                        with self.subTest(partial_in=partial_in, folder=folder.name, deep=deep,
                                          files=None if files is None else len(files)):
                            self.assertEqual(watcher.snapshot(folder, files, deep),
                                             old_snapshot(folder, files, deep))

    def test_each_entry_is_stat_ed_once(self):
        _root, album = self.tree()
        files = sorted(album.glob("*.flac"))
        calls = []
        real = os.stat

        def stat(p, *a, **k):
            calls.append(str(p))
            return real(p, *a, **k)
        with mock.patch.object(os, "stat", stat):
            watcher.snapshot(album, files, True)
        # (the folder itself; the album's files come from the listing: none stat-ed by name again -
        # but a broken link and a staging file, which the listing passes over, as before)
        self.assertEqual(sorted(Path(c).name for c in calls if c != str(album)),
                         ["broken.flac", f"x{executor.STAGE_TAG}y.flac"])


class AlbumsInSkip(te.Base):
    def test_a_skipped_folder_is_still_skipped_and_others_found_without_resolving_each(self):
        self.album("Band A", "Album A")
        lib2 = self.lib / "Band B"
        self.album("Band B", "Album B")
        os.symlink(lib2, self.lib / "B link")
        e = self.engine(te.FakeHttp())
        names = lambda gs: sorted(g.folder.name for g in gs)          # noqa: E731
        self.assertEqual(names(e.albums_in(self.lib)), ["2005 - Album A", "2005 - Album B"])
        self.assertEqual(names(e.albums_in(self.lib, skip=[lib2])), ["2005 - Album A"])
        self.assertEqual(names(e.albums_in(self.lib, skip=[self.lib / "B link"])), ["2005 - Album A"])
        calls = []
        real = os.path.realpath
        with mock.patch.object(os.path, "realpath", lambda p, *a, **k: calls.append(p) or real(p, *a, **k)):
            e.albums_in(self.lib)
        self.assertFalse([c for c in calls if "Album" in str(c)], calls)     # no skip list: nothing resolved


# --------------------------------------------------------------------------
# 3. Audiophile measurements side by side, each file hashed once
# --------------------------------------------------------------------------
class Measurements(te.Base):
    def setUp(self):
        super().setUp()
        self.d = self.album("Band M", "Album M", n=6)
        self.files = sorted(self.d.glob("*.flac"))
        self.prof = pf.from_dict({"name": "a", "tag_level": "audiophile",
                                  "audiophile": {"acoustid": False, "lyrics": False, "rip_info": False}})
        self.threads = []

    def prop(self):
        return types.SimpleNamespace(status="identified", files=[str(f) for f in self.files],
                                     targets=[{} for _ in self.files], source_dir=str(self.d), why=[])

    def stub(self, kind, pause):
        def measure(path):
            self.threads.append(threading.current_thread().name)
            time.sleep(pause)
            n = int(Path(path).stem)
            return [-10.0 - n, -1.0 - n / 10, 10.0 * n] if kind == "loudness" else 8 + n
        return measure

    def run_once(self, workers, pause=0.15, cache=None):
        import enrich
        prop = self.prop()
        digests, real = [], payload.digest

        def digest(p, *a, **k):
            digests.append(str(p))
            return real(p, *a, **k)
        with mock.patch.object(levels, "loudness", self.stub("loudness", pause)), \
                mock.patch.object(levels, "dynamic_range", self.stub("dr", pause)), \
                mock.patch.object(levels, "measure_workers", lambda: workers), \
                mock.patch.object(enrich, "spectrum_tools", lambda: False), \
                mock.patch.object(payload, "digest", digest):
            t = time.monotonic()
            notes = levels.audiophile(prop, self.prof, None, cache or levels.Cache(None))
            took = time.monotonic() - t
        return prop.targets, notes, took, digests

    def test_side_by_side_gives_the_same_tags_faster(self):
        serial, n1, t1, _ = self.run_once(1)
        self.assertEqual(set(self.threads), {threading.current_thread().name})
        self.threads.clear()
        together, n2, t2, _ = self.run_once(4)
        self.assertEqual(together, serial)
        self.assertEqual(n2, n1)
        self.assertTrue(serial[0]["replaygain_track_gain"] and serial[0]["dynamic_range"])
        self.assertGreater(len(set(self.threads)), 1)
        self.assertLess(t2, t1 * 0.7)

    def test_each_file_hashed_once_and_the_cache_written_and_used(self):
        cache = levels.Cache(self.tmp / "analysis.db")
        first, _n, _t, digests = self.run_once(4, pause=0, cache=cache)
        self.assertEqual(sorted(digests), sorted(str(f) for f in self.files))
        rows = cache.db.execute("SELECT count(*) FROM analysis").fetchone()[0]
        self.assertEqual(rows, 2 * len(self.files))
        self.threads.clear()
        again, _n, _t, digests = self.run_once(4, pause=0, cache=cache)
        self.assertEqual(again, first)
        self.assertEqual(self.threads, [])                               # all from the cache
        self.assertEqual(sorted(digests), sorted(str(f) for f in self.files))
        cache.close()

    def test_a_failed_measurement_stops_the_rest(self):
        started = []

        def measure(path):
            started.append(path)
            if Path(path).stem == "01":
                raise RuntimeError("ffmpeg died")
            time.sleep(0.3)
            return 1
        cache = levels.Cache(self.tmp / "analysis.db")
        with self.assertRaises(RuntimeError):
            levels._measured(cache, "dr", measure, self.files, levels._AudioKeys())
        self.assertLess(len(started), len(self.files))
        cache.close()

    def test_a_cancelled_plan_still_stops_between_albums(self):
        self.album("Band N", "Album N", n=1)
        e = self.engine(te.FakeHttp())
        seen = []
        stop = []

        def identify_all(g):
            seen.append(g.folder.name)
            stop.append(True)
            return []
        with mock.patch.object(e, "identify_all", identify_all):
            e.proposals(self.lib, cancel=lambda: bool(stop))
        self.assertEqual(len(seen), 1)


if __name__ == "__main__":
    unittest.main()
