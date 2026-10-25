# Cover replacement and watched folders, second independent review: each test failed before its fix.
import os as _os, tempfile as _tempfile  # noqa: E402
_os.environ.setdefault("XDG_CONFIG_HOME", _tempfile.mkdtemp(prefix="tm-config-"))
_os.environ.setdefault("XDG_STATE_HOME", _tempfile.mkdtemp(prefix="tm-state-"))
import sys
from pathlib import Path
REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "tests"))
import base64
import logging
import socket
import unittest
from pathlib import Path
from unittest import mock

import artwork
import executor
import plan as pl
import tagio
import test_cover_replace as tcr
from test_cover_replace import jpeg, size, give, Fake, Engine, MBID, _no_network
from test_executor import Base, audio, target


class H(Base):
    def setUp(self):
        super().setUp()
        p = mock.patch.object(socket, "create_connection", _no_network)
        p.start()
        self.addCleanup(p.stop)

    def run_found(self, prof, data, files=None, prov_kw=None):
        files = files or [self.f1, self.f2]
        prop = self.proposal(files=[str(f) for f in files],
                             targets=[target(n + 1, f"T{n}", musicbrainz_albumid=[MBID]) for n in range(len(files))])
        prov = Fake(self.tmp / "staging", data)
        prov.min_px = int(prof["artwork"]["min_px"])
        prov.enlarge = bool(prof["artwork"].get("enlarge", True))
        with mock.patch.object(artwork, "chain", lambda *a, **k: [prov]):
            Engine(prof).add_artwork(prop)
        p = pl.build([prop], prof)
        res = executor.Executor(p).run()
        return prop, p, res


class FrontDetection(H):
    # 1. a track whose only picture is a BACK cover (type 4) is taken for a small front cover:
    #    the back cover is deleted and replaced (before 2cfe3b9 a track with any embedded
    #    picture was never touched)
    def test_only_a_back_cover_embedded_is_kept(self):
        for f in (self.f1, self.f2):
            give(f, jpeg(300, 300, (1, 2, 3)), ptype=4)
        prof = self.profile(artwork={"enabled": True, "embed_front": True, "files": False, "min_px": 500})
        self.run_found(prof, jpeg(900, 900))
        from mutagen.flac import FLAC
        types = sorted(p.type for p in FLAC(self.f1).pictures)
        self.assertIn(4, types, f"the back cover was deleted: pictures now {types}")

    # 2. MP3: the front APIC has a description, the back cover's is empty - the new front is
    #    added as APIC:"" and overwrites the back cover
    def test_mp3_back_cover_with_empty_desc_is_kept(self):
        f = audio(self.album / "03 c.mp3", 660, album="Some Album", title="c", artist="Some Artist")
        from mutagen.id3 import APIC, ID3
        t = ID3(f)
        t.add(APIC(encoding=3, mime="image/jpeg", type=3, desc="Front", data=jpeg(300, 300)))
        t.add(APIC(encoding=3, mime="image/jpeg", type=4, desc="", data=jpeg(200, 100, (9, 9, 9))))
        t.save(f)
        prof = self.profile(artwork={"enabled": True, "embed_front": True, "files": False, "min_px": 500})
        _prop, _p, res = self.run_found(prof, jpeg(800, 800), files=[f])
        self.assertEqual(res["counts"], {"done": 1}, res)
        got = sorted((a.type, size(a.data)) for a in ID3(f).getall("APIC"))
        self.assertEqual(got, [(3, (800, 800)), (4, (200, 100))], "the back cover was overwritten")


class SharedFolder(H):
    # 3. one album of a folder holding another album too: the folder's cover.jpg is the other
    #    album's (or no one's) - it is taken for "the album's own cover", enlarged and embedded
    def test_shared_folders_cover_is_not_this_albums_own(self):
        audio(self.album / "09 other.flac", 880, album="Another Album", title="z", artist="Another Artist")
        (self.album / "cover.jpg").write_bytes(jpeg(300, 300, (0, 200, 0)))      # not this album's
        prof = self.profile(artwork={"enabled": True, "embed_front": True, "files": True, "min_px": 500})
        prop, p, res = self.run_found(prof, None)                                # nothing found online
        self.assertTrue(artwork.embedded_front(self.f1) is None,
                          f"the shared folder's cover.jpg was embedded ({(prop.artwork or {}).get('source')})")


class Repeat(H):
    # 4. min_px above the embedding size (embed_max_side, 1200 by default; or a wide cover):
    #    the embedded copy is always "smaller than min_px", so every run replaces it again
    def test_second_run_does_not_replace_again(self):
        for f in (self.f1, self.f2):
            give(f, jpeg(300, 300))
        prof = self.profile(artwork={"enabled": True, "embed_front": True, "files": False, "min_px": 1400})
        _prop, p1, res1 = self.run_found(prof, jpeg(1600, 1600))
        self.assertEqual(res1["counts"], {"done": 1})
        why = artwork.needs_art(self.album, [self.f1, self.f2], prof)
        _prop, p2, res2 = self.run_found(prof, jpeg(1600, 1600))
        self.assertNotEqual(res2["counts"], {"done": 1}, "run 2 rewrote every track again")
        self.assertEqual(why, "", f"run 2 looks again: {why}")

    def test_wide_cover_second_run(self):
        for f in (self.f1, self.f2):
            give(f, jpeg(300, 300))
        prof = self.profile(artwork={"enabled": True, "embed_front": True, "files": False, "min_px": 500})
        self.run_found(prof, jpeg(3000, 1000))
        why = artwork.needs_art(self.album, [self.f1, self.f2], prof)
        self.assertEqual(why, "", f"run 2 looks again: {why}")




# a copy (or move) profile whose album's cover.jpg is not a companion (album at the source
# root: only the tracks' own companions go): the SOURCE cover.jpg is rewritten in place
import unittest
import executor, plan as pl
import test_cover_replace as tcr
from test_cover_replace import jpeg, size


class CopySource(tcr.Replace):
    def _go(self, op):
        prof = self.profile(artwork=self.art(), operation=op, destination=str(self.dest),
                            sources=[{"path": str(self.album)}])
        prop = self.found(prof, jpeg(800, 800))
        p = pl.build([prop], prof)
        a = p["albums"][0]
        res = executor.Executor(p).run()
        return p

    def test_copy_never_writes_the_source(self):
        self._go("copy")
        self.assertEqual((self.album / "cover.jpg").read_bytes(), self.small,
                         "a copy profile rewrote the source library's cover.jpg")

    def test_move_leaves_no_rewritten_cover_behind(self):
        self._go("move")
        left = self.album / "cover.jpg"
        self.assertFalse(left.exists() and size(left.read_bytes()) != (300, 300),
                         "the album moved, but its old folder's cover.jpg was rewritten and left behind")




# disc set: Disc 01/, Disc 02/ with the small cover.jpg in the album folder
import shutil
import unittest
import artwork, executor, plan as pl, tagio
import test_cover_replace as tcr
from test_cover_replace import jpeg, size, give
from test_executor import audio, target


class DiscSet(tcr.Replace):
    def setUp(self):
        super().setUp()
        for f in (self.f1, self.f2):
            d = self.album / ("Disc 01" if f == self.f1 else "Disc 02")
            d.mkdir()
            shutil.move(str(f), str(d / f.name))
        self.f1 = self.album / "Disc 01" / "01 a.flac"
        self.f2 = self.album / "Disc 02" / "02 b.flac"
        (self.album / "rip.cue").unlink()

    def _go(self, **kw):
        prof = self.profile(artwork=self.art(), **kw)
        before = self.originals()
        prop = self.found(prof, jpeg(800, 800))
        prop.targets[1]["discnumber"] = ["2"]
        prop.targets[0]["totaldiscs"] = prop.targets[1]["totaldiscs"] = ["2"]
        p = pl.build([prop], prof)
        a = p["albums"][0]
        res = executor.Executor(p).run()
        self.assertEqual(res["counts"], {"done": 1}, res)
        covers = [x for x in (self.dest if kw.get("operation") == "move" else self.album).rglob("*.jpg")]
        self.assertEqual([size(x.read_bytes()) for x in covers], [(800, 800)], covers)
        executor.undo(p["job"])
        self.assertEqual(self.originals(), before)

    def test_in_place(self):
        self._go()

    def test_move(self):
        self._go(operation="move", destination=str(self.dest))




# a run whose only change is the larger cover (tags already right): undo puts the old one back
import unittest
import artwork, executor, plan as pl
import test_cover_replace as tcr
from test_cover_replace import jpeg, size


class CoverOnly(tcr.Replace):
    def test_cover_only_run_then_undo(self):
        p0 = pl.build([self.found(self.profile(artwork=self.art(replace="never")), None)],
                      self.profile(artwork=self.art(replace="never")))
        executor.Executor(p0).run()                                 # tags written, covers kept
        before = self.originals()
        prof = self.profile(artwork=self.art())
        p = pl.build([self.found(prof, jpeg(900, 900))], prof)
        res = executor.Executor(p).run()
        self.assertEqual(res["counts"], {"done": 1}, res)
        self.assertEqual(size(artwork.embedded_front(self.f1)), (900, 900))
        executor.undo(p["job"])
        self.assertEqual(size(artwork.embedded_front(self.f1)), (300, 300))
        self.assertEqual(self.originals(), before)




# Flatpak: Stop, then Run in the background again while the watcher is mid-pass
import threading
import time
import unittest
from unittest import mock
import test_watch_folders as tw
import watcher


class StopStart(tw.DropBase):
    def test_stop_then_start_leaves_a_watcher(self):
        prof = self.profile()
        w = watcher.Watcher([prof])
        in_pass = threading.Event()

        def slow_tick():
            in_pass.set()
            time.sleep(2)                       # identifying an album
            return {}
        w.tick = slow_tick
        th = threading.Thread(target=w.run, daemon=True)
        with mock.patch.object(watcher, "in_flatpak", return_value=True), \
                mock.patch.object(watcher, "_autostart_file", return_value=self.tmp / "autostart.desktop"):
            th.start()
            in_pass.wait(5)
            time.sleep(0.2)
            self.assertEqual(watcher.stop_service(portal=lambda o: ""), "stopped")
            spawned = []
            self.assertEqual(watcher.install_service(portal=lambda o: "", spawn=lambda: spawned.append(1)), "running")
            th.join(6)
            self.assertTrue(th.is_alive() or spawned,
                            "install_service said 'running', but the old watcher then honoured the stop "
                            "request and nothing was started: no watcher until the next login")




class OwnLargeEnough(H):
    # (low-confidence note) a small cover.jpg beside a large embedded front: the cover file
    # becomes the album's own large front - never shrunk to min_px and called "enlarged"
    def test_small_file_large_embedded(self):
        for f in (self.f1, self.f2):
            give(f, jpeg(1000, 1000))
        (self.album / "cover.jpg").write_bytes(jpeg(300, 300))
        prof = self.profile(artwork={"enabled": True, "embed_front": True, "files": True, "min_px": 500})
        prop, _p, res = self.run_found(prof, None)
        self.assertEqual(res["counts"], {"done": 1}, res)
        self.assertNotIn("enlarged", prop.artwork["source"])
        self.assertEqual(size((self.album / "cover.jpg").read_bytes()), (1000, 1000))


class LockProbe(Base):
    # (low-confidence note) looking at jobs (busy_jobs, prune) is not "a job is changing files"
    def test_busy_jobs_is_not_a_running_job(self):
        jd = executor.job_dir("20260101-000000-look")
        jd.mkdir(parents=True)
        (jd / "journal.jsonl").write_text('{"ev": "begin", "t": 1}\n')
        import threading
        seen = []
        real = executor.job_lock

        def slow(job, active=True):
            ctx = real(job, active)

            class C:
                def __enter__(self_):
                    ctx.__enter__()
                    seen.append(executor.job_running())
                def __exit__(self_, *a):
                    return ctx.__exit__(*a)
            return C()
        with mock.patch.object(executor, "job_lock", slow):
            executor.busy_jobs()
        self.assertEqual(seen, [False])


for _cls in (CopySource, DiscSet, CoverOnly):        # (Replace's own tests run in test_cover_replace)
    for _n in dir(tcr.Replace):
        if _n.startswith("test_") and _n not in _cls.__dict__:
            setattr(_cls, _n, None)


if __name__ == "__main__":
    unittest.main()
