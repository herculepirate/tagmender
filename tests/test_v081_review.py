# 0.8.1 review fixes (no network; fakes, a simulated clock and synthetic files):
# - 1: a hidden track before track 1 matched on its length alone is never automatic: only the
#      exact disc ID or a rip log's / CDTOC tag's TOC with the track in its pregap proves it;
# - 2: a rip's own cue sheet fits whichever way the gaps were split (appended, left out,
#      prepended) and with the hidden track ripped to a file numbered 0;
# - 3: the long-lived metadata store trusts an entry for META_STORE_AGE, holds META_STORE_CAP;
# - 4: an MP4's trkn (0, N) (the total alone) numbers no file 0 unless its name says 00;
# - 5: the scans read ahead are the same readings unless a time budget runs out (wording);
# - 6: a given-up Discogs search on its worker stops at once, also in a back-off pause;
# - 7: one pace per host for the whole process: every Engine's Http and the app's lookups.
import os as _os, tempfile as _tempfile  # noqa: E402
_os.environ.setdefault("XDG_CONFIG_HOME", _tempfile.mkdtemp(prefix="tm-config-"))
_os.environ.setdefault("XDG_STATE_HOME", _tempfile.mkdtemp(prefix="tm-state-"))

import shutil
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))
import test_engine as te  # noqa: E402  (its settings and state dirs are temporary)
import test_v081_accuracy as acc  # noqa: E402
import engine  # noqa: E402
import exclusions as ex  # noqa: E402
import match_albums as ma  # noqa: E402
import match_core as mc  # noqa: E402
import profiles as pf  # noqa: E402

CFG = acc.CFG
WHY = "hidden track matched on its length only"


# ---- 1: the hidden track before track 1 on its length alone -------------------------------
def htoa_album(**kw):
    files = [{"disc": 1, "num": 0, "title": "Hidden", "secs": 95.0}] + [
        {"disc": 1, "num": i + 1, "title": f"T{i}", "secs": s} for i, s in enumerate((200.0, 305.0, 410.0))]
    return acc.album(tracks=files, **kw)


class HiddenTrackProof(unittest.TestCase):
    def test_lengths_alone_are_reviewed(self):
        c = ma.mb_candidate(acc.htoa_release())
        r = acc.pick(htoa_album(), c)                     # (a lossy rip, or no TOC of the disc)
        self.assertTrue(r["best"]["cand"].get("pregap_used"))
        self.assertEqual(acc.outcome(r), ("review", "rel1"), r["why"])
        self.assertIn(WHY, r["why"])

    def test_a_fuzzy_disc_id_is_no_proof_either(self):
        c = ma.mb_candidate(acc.htoa_release())
        r = acc.pick(htoa_album(toc_releases={"rel1"}), c)
        self.assertNotEqual(r["status"], "auto", r["why"])
        self.assertIn(WHY, r["why"])

    def test_the_exact_disc_id_proves_it(self):
        c = ma.mb_candidate(acc.htoa_release())
        r = acc.pick(htoa_album(toc_releases={"rel1"}, exact_toc_releases={"rel1"}), c)
        self.assertEqual(acc.outcome(r), ("auto", "rel1"), r["why"])

    def test_a_fitting_log_toc_proves_it(self):
        infos = [(None, dict(x)) for x in acc.htoa_files()]
        self.assertEqual(ma.htoa_toc_discs(infos, [acc.HTOA_TOC]), [1])
        self.assertEqual(ma.htoa_toc_discs(infos, []), [])
        # a TOC without that pregap (the files' lengths alone) proves nothing
        plain = " ".join(map(str, [1, 3, 150 + 915 * 75, 150, 150 + 200 * 75, 150 + 505 * 75]))
        self.assertEqual(ma.htoa_toc_discs(infos, [plain]), [])
        c = ma.mb_candidate(acc.htoa_release())
        r = acc.pick(htoa_album(htoa_toc_discs=[1], rip_log_toc=True), c)
        self.assertEqual(acc.outcome(r), ("auto", "rel1"), r["why"])

    def test_a_cdtoc_tag_counts_like_a_log(self):
        nums = [int(x) for x in acc.HTOA_TOC.split()]
        tag = "3+" + "+".join(format(x, "X") for x in nums[3:] + [nums[2]])
        self.assertEqual(ma.cdtoc_tag_toc(tag), acc.HTOA_TOC)
        infos = [(None, dict(x, cdtoc=tag)) for x in acc.htoa_files()]
        self.assertEqual(ma.htoa_toc_discs(infos, list(ma.tag_tocs(infos).values())), [1])


# ---- 2: the rip's own cue sheet --------------------------------------------------------------
RANGE_CUE = '''CATALOG 0123456789012
FILE "Range.wav" WAVE
  TRACK 01 AUDIO
    INDEX 00 00:00:00
    INDEX 01 00:04:00
  TRACK 02 AUDIO
    INDEX 00 00:13:00
    INDEX 01 00:14:00
  TRACK 03 AUDIO
    INDEX 00 00:32:00
    INDEX 01 00:34:00
'''


class OwnCue(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp())
        self.cue = self.d / "rip.cue"
        self.cue.write_text(RANGE_CUE)

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def fits(self, lengths, zero=None):
        infos = ([(self.d / "00.flac", {"num": 0, "secs": zero, "disc_eff": 1})] if zero is not None else [])
        infos += [(self.d / f"{k + 1:02d}.flac", {"num": k + 1, "secs": s, "disc_eff": 1})
                  for k, s in enumerate(lengths)]
        return ma.cue_fits(self.cue, infos)

    def test_gaps_appended(self):
        self.assertTrue(self.fits((10.0, 20.0, 30.0)))

    def test_gaps_prepended(self):
        self.assertTrue(self.fits((9.0, 19.0, 32.0)))

    def test_hidden_track_file(self):
        self.assertTrue(self.fits((10.0, 20.0, 30.0), zero=4.0))
        self.assertTrue(self.fits((9.0, 19.0, 32.0), zero=4.0))
        self.assertEqual(ma.foreign_cues(self.d, [(self.d / "00.flac", {"num": 0, "secs": 4.0, "disc_eff": 1})] + [
            (self.d / f"{k + 1:02d}.flac", {"num": k + 1, "secs": s, "disc_eff": 1})
            for k, s in enumerate((10.0, 20.0, 30.0))]), [])

    def test_a_hidden_track_of_another_length_is_foreign(self):
        self.assertFalse(self.fits((10.0, 20.0, 30.0), zero=7.0))

    def test_a_truly_foreign_cue_is_still_foreign(self):
        self.assertFalse(self.fits((15.0, 25.0, 30.0)))
        self.assertFalse(self.fits((15.0, 25.0, 30.0), zero=4.0))


# ---- 3: the long-lived metadata store ------------------------------------------------------
class MetaStoreBounds(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def test_an_edit_the_stamps_miss_is_read_after_the_age_bound(self):
        f = self.d / "a.flac"
        f.write_bytes(b"x" * 100)
        albums = iter(["Before", "After", "Later"])
        now = [1000.0]

        def read(path):
            return {"album": [next(albums)]}
        store = {}
        with mock.patch.object(ex, "_read_meta", read), mock.patch.object(ex.time, "monotonic", lambda: now[0]):
            with ex.meta_memo(store, True):
                self.assertEqual(ex.read_meta(f)["album"], ["Before"])
            now[0] += ex.META_STORE_AGE - 1                 # (same size, mtime and ctime: kept)
            with ex.meta_memo(store, True):
                self.assertEqual(ex.read_meta(f)["album"], ["Before"])
            now[0] += 2                                     # past the bound: read again
            with ex.meta_memo(store, True):
                self.assertEqual(ex.read_meta(f)["album"], ["After"])
                self.assertEqual(ex.read_meta(f)["album"], ["After"])
        self.assertLessEqual(ex.META_STORE_AGE, 3600)

    def test_the_store_holds_at_most_the_cap(self):
        files = []
        for k in range(6):
            f = self.d / f"{k}.mp3"
            f.write_bytes(b"x")
            files.append(f)
        store = {}
        with mock.patch.object(ex, "META_STORE_CAP", 4), ex.meta_memo(store):
            for f in files:
                ex.read_meta(f)
        self.assertEqual([k[0] for k in store], [str(f) for f in files[2:]])   # the oldest dropped
        self.assertGreaterEqual(ex.META_STORE_CAP, 10_000)


# ---- 4: MP4 trkn (0, N) ----------------------------------------------------------------------
class Mp4TotalOnly(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def m4a(self, name):
        from mutagen.mp4 import MP4
        f = self.d / name
        subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "sine=duration=1", "-ar", "44100",
                        "-c:a", "aac", str(f), "-y"], check=True)
        m = MP4(f)
        m["trkn"] = [(0, 3)]
        m["\xa9alb"] = ["Album"]
        m.save()
        return f

    def infos(self, *names):
        out = [(f, ma.read_file(f)) for f in (self.m4a(n) for n in names)]
        ma.names_from_files(out)
        return out

    def test_total_alone_is_no_number_zero(self):
        (_f, i), = self.infos("Bonus.m4a")
        self.assertFalse(i.get("num0"))

    def test_a_name_saying_00_still_is(self):
        (_f, i), = self.infos("00 - Hidden.m4a")
        self.assertTrue(i.get("num0"))

    def test_the_unnumbered_file_is_not_put_first(self):
        d = self.d / "Band" / "2001 - Album"
        d.mkdir(parents=True)
        for name in ("Bonus.m4a",):
            shutil.move(str(self.m4a(name)), d / name)
        from mutagen.mp4 import MP4
        for k in (1, 2):
            f = self.m4a(f"{k:02d} - T{k}.m4a")
            m = MP4(f)
            m["trkn"] = [(k, 3)]
            m.save()
            shutil.move(str(f), d / f.name)
        a = ma.read_album(d, "Band", "2001 - Album")
        self.assertEqual([Path(t["path"]).name for t in a["tracks"]][0], "01 - T1.m4a")


# ---- 5: the scans read ahead -----------------------------------------------------------------
class ScanReadsWording(unittest.TestCase):
    def test_docstring_says_when_readings_may_differ(self):
        doc = " ".join(ma._ScanReads.__doc__.split())
        self.assertIn("unless a time budget runs out under load", doc)
        self.assertNotIn("changes nothing but when it was made", doc)


# ---- 6: a given-up Discogs search stops in a back-off --------------------------------------
class AbortInBackOff(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp())
        self.http = ma.Http(sqlite3.connect(self.d / "cache.sqlite", check_same_thread=False),
                            dict(mc.DEFAULT_CFG, discogs_delay=0.0, mb_delay=0.0), "t")

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def test_abort_returns_promptly_during_a_retry_after_pause(self):
        asked = threading.Event()

        def urlopen(req, timeout=None):
            asked.set()
            raise urllib.error.HTTPError(req.full_url, 429, "slow down", {"Retry-After": "60"}, None)

        def search(http, album, cfg, dm):
            return http.get_text("https://api.discogs.com/database/search?q=x", "discogs")
        with mock.patch.object(ma.urllib.request, "urlopen", urlopen), \
                mock.patch.object(ma, "discogs_search", search), mock.patch("sys.stderr"):
            ahead = ma._DiscogsAhead.start(self.http, {}, {}, None)
            self.assertIsNotNone(ahead)
            self.assertTrue(asked.wait(5))
            time.sleep(0.2)                                  # (in its 60 s pause now)
            t0 = time.time()
            stopper = threading.Thread(target=ahead.abort, daemon=True)
            stopper.start()
            stopper.join(5)
            self.assertFalse(stopper.is_alive(), "abort waited out the back-off")
            self.assertLess(time.time() - t0, 3)
            self.assertIsInstance(ahead.error, ma.ProviderUnavailable)
            ahead.close()


# ---- 7: one pace per host for the whole process --------------------------------------------
class Clock:
    def __init__(self):
        self.t, self.lock = 5e8, threading.Lock()

    def time(self):
        return self.t

    def sleep(self, s):
        with self.lock:
            self.t += max(0.0, s)


class OnePacePerProcess(unittest.TestCase):
    def setUp(self):
        ma._SHARED_LAST.clear()
        self.addCleanup(ma._SHARED_LAST.clear)

    def answer(self, opened, clock):
        class R:
            def read(self, *a):
                return b"{}"

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        def urlopen(req, timeout=None):
            opened.append(clock.time())
            clock.t += 0.01
            return R()
        return urlopen

    def test_two_shared_https_never_ask_musicbrainz_closer_than_mb_delay(self):
        clock, opened = Clock(), []
        cfg = dict(mc.DEFAULT_CFG, mb_delay=1.0)
        a = ma.share_pace(ma.Http(sqlite3.connect(":memory:"), dict(cfg), ""))
        b = ma.share_pace(ma.Http(sqlite3.connect(":memory:"), dict(cfg), ""))
        with mock.patch.object(ma.urllib.request, "urlopen", self.answer(opened, clock)), \
                mock.patch.object(ma.time, "sleep", clock.sleep), mock.patch.object(ma.time, "time", clock.time):
            for k in range(6):
                (a if k % 2 else b).get_text(f"https://musicbrainz.org/ws/2/release/{k}", "mb")
        self.assertEqual(len(opened), 6)
        gaps = [y - x for x, y in zip(opened, opened[1:])]
        self.assertGreaterEqual(min(gaps), 1.0 - 1e-9, gaps)

    def test_the_engine_and_the_versions_sheet_share_the_pace(self):
        from gui import versions
        d = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, d, True)
        prof = pf.from_dict({"name": "default", "providers": {"web": {"enabled": False}},
                             "artwork": {"enabled": False}})
        eng = engine.Engine(prof, cache=d / "cache.sqlite")
        sheet = versions.paced(ma.Http(sqlite3.connect(d / "cache.sqlite", check_same_thread=False),
                                       dict(mc.DEFAULT_CFG), ""))
        self.assertIs(eng.http.last, ma._SHARED_LAST)
        self.assertIs(sheet.last, ma._SHARED_LAST)
        self.assertIs(eng.http._host_locks, sheet._host_locks)
        self.assertIs(eng.http._host_lock("mb"), sheet._host_lock("mb"))
        self.assertNotIn("get_text", vars(sheet))            # (not wrapped: it can be cloned)
        self.assertIsNotNone(sheet.thread_clone())
        # requests from both, at once on two threads: one at a time, spaced by mb_delay
        for h in (eng.http, sheet):
            h.cfg["mb_delay"] = 0.2
        state = {"now": 0, "most": 0, "times": []}
        lock = threading.Lock()

        class R:
            def read(self, *a):
                return b"{}"

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        def urlopen(req, timeout=None):
            with lock:
                state["now"] += 1
                state["most"] = max(state["most"], state["now"])
                state["times"].append(time.time())
            time.sleep(0.03)
            with lock:
                state["now"] -= 1
            return R()
        with mock.patch.object(ma.urllib.request, "urlopen", urlopen):
            # (each thread on a clone: its own cache connection, the pace shared)
            clones = [h.thread_clone() for _k in range(2) for h in (eng.http, sheet)]
            ts = [threading.Thread(target=c.get_text, args=(f"https://musicbrainz.org/ws/2/y{i}", "mb"))
                  for i, c in enumerate(clones)]
            for t in ts:
                t.start()
            for t in ts:
                t.join()
        for c in clones:
            c.close_clone()
        times = sorted(state["times"])
        self.assertEqual(len(times), 4)
        self.assertEqual(state["most"], 1)
        self.assertGreaterEqual(min(y - x for x, y in zip(times, times[1:])), 0.19, times)
        eng.http.db.close()

    def test_an_http_on_its_own_keeps_its_own_pace(self):
        h = ma.Http(sqlite3.connect(":memory:"), dict(mc.DEFAULT_CFG), "")
        self.assertIsNot(h.last, ma._SHARED_LAST)


if __name__ == "__main__":
    unittest.main()
