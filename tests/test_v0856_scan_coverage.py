"""SCN-1: an album's scans count as all read (scans_complete - TAG-1 then takes a clue the
earlier reading found and this one does not as disproved, not missing) only when every image of
the folder was read: none left out by the scan cap ("quick" reads 8), none unreadable, none whose
reading failed. A reading that raised in the read-ahead pool counted as a scan read with no text
on it, and was cached so. The investigation says how many scans it read again, not "every"."""
import os as _os, tempfile as _tempfile  # noqa: E402
_os.environ.setdefault("XDG_CONFIG_HOME", _tempfile.mkdtemp(prefix="tm-config-"))
_os.environ.setdefault("XDG_STATE_HOME", _tempfile.mkdtemp(prefix="tm-state-"))
import json
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import match_albums as ma  # noqa: E402
import match_core as mc  # noqa: E402

try:
    from PIL import Image
    HAVE_PIL = True
except ImportError:
    HAVE_PIL = False
HAVE_FFMPEG = shutil.which("ffmpeg") is not None


def scans(d, n):
    for k in range(1, n + 1):
        Image.new("L", (300, 300), k * 10).save(d / f"scan{k:02d}.jpg")


@unittest.skipUnless(HAVE_PIL, "Pillow needed")
class SCN1_Inventory(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.d)

    def test_the_cap_says_what_it_left_out(self):
        scans(self.d, 12)
        inv = {}
        imgs, _key = ma.list_images(self.d, 8, inv)
        self.assertEqual(len(imgs), 8)
        self.assertEqual((inv["found"], inv["listed"], inv["left_out"], inv["unique"]), (12, 8, 4, 12))

    def test_a_copy_after_the_cap_is_not_left_out(self):
        # album 32 of pilot 1: nine files, eight images (one a byte-identical copy)
        scans(self.d, 8)
        shutil.copy(self.d / "scan03.jpg", self.d / "scan03 (copy).jpg")
        inv = {}
        imgs, _key = ma.list_images(self.d, 8, inv)
        self.assertEqual(len(imgs), 8)
        self.assertEqual((inv["found"], inv["duplicates"], inv["unique"], inv["left_out"]), (9, 1, 8, 0))

    def test_same_size_other_contents_is_left_out(self):
        for k in range(9):                              # nine files of one size, each its own
            (self.d / f"s{k}.jpg").write_bytes(b"x" * 500 + bytes([k]))
        inv = {}
        ma.list_images(self.d, 8, inv)
        self.assertEqual((inv["found"], inv["duplicates"], inv["unique"], inv["left_out"]), (9, 0, 9, 1))

    def test_an_unreadable_one_after_the_cap_is_only_itself(self):
        for k in range(5):                              # cap 2; r0, r1 listed; r2 unreadable; r3, r4 its size
            (self.d / f"r{k}.jpg").write_bytes(b"y" * 500 + bytes([k]))
        real = Path.read_bytes
        real_open = open

        def bad(path):
            if Path(path).name == "r2.jpg":
                raise OSError("I/O error")
            return real(path)

        def bad_open(path, *a, **k):
            if Path(str(path)).name == "r2.jpg":
                raise OSError("I/O error")
            return real_open(path, *a, **k)
        with mock.patch.object(Path, "read_bytes", bad), mock.patch("builtins.open", bad_open), \
                mock.patch.object(ma.ocr_order, "rank", lambda found, tie=None: sorted(found)):
            inv = {}
            ma.list_images(self.d, 2, inv)
        self.assertEqual((inv["found"], inv["unreadable"], inv["left_out"], inv["unique"]), (5, 1, 2, 4))

    def test_an_earlier_ones_failing_read_is_not_charged_to_the_next(self):
        # cap 2: a0, a1 listed; then r1, r2, r3 of one other size, r1 unreadable once compared
        for k in range(2):
            (self.d / f"a{k}.jpg").write_bytes(b"a" * 100 + bytes([k]))
        for k in range(1, 4):
            (self.d / f"r{k}.jpg").write_bytes(b"r" * 900 + bytes([k]))
        real_open = open

        def bad_open(path, *a, **k):
            if Path(str(path)).name == "r1.jpg":
                raise OSError("I/O error")
            return real_open(path, *a, **k)
        with mock.patch("builtins.open", bad_open), \
                mock.patch.object(ma.ocr_order, "rank", lambda found, tie=None: sorted(found)):
            inv = {}
            ma.list_images(self.d, 2, inv)
        self.assertEqual((inv["found"], inv["unreadable"], inv["left_out"], inv["unique"]), (5, 0, 3, 5))

    def test_an_unreadable_one_before_the_cap(self):
        scans(self.d, 4)
        real = Path.read_bytes
        with mock.patch.object(Path, "read_bytes",
                               lambda p: (_ for _ in ()).throw(OSError("x")) if p.name == "scan02.jpg" else real(p)):
            inv = {}
            imgs, _key = ma.list_images(self.d, 8, inv)
        self.assertEqual((inv["found"], inv["unreadable"], inv["unique"], inv["left_out"]), (4, 1, 3, 0))
        self.assertEqual(len(imgs), 3)

    def test_same_size_scans_are_told_apart_by_their_start(self):
        # 20 uncompressed scans of one size: those after the cap are not read whole
        for k in range(20):
            (self.d / f"u{k:02d}.jpg").write_bytes(bytes([k]) * 70000 + b"tail")
        reads = []
        real = Path.read_bytes
        with mock.patch.object(Path, "read_bytes", lambda p: reads.append(p.name) or real(p)):
            inv = {}
            ma.list_images(self.d, 8, inv)
        self.assertEqual((inv["left_out"], inv["duplicates"]), (12, 0))
        self.assertEqual(len(reads), 8, "only the 8 listed read whole")

    def test_copies_among_those_after_the_cap(self):
        scans(self.d, 10)
        for k in (9, 10):
            shutil.copy(self.d / f"scan{k:02d}.jpg", self.d / f"scan{k:02d} copy.jpg")
        inv = {}
        ma.list_images(self.d, 8, inv)
        self.assertEqual((inv["found"], inv["duplicates"], inv["unique"], inv["left_out"]), (12, 2, 10, 2))

    def test_everything_listed(self):
        scans(self.d, 5)
        shutil.copy(self.d / "scan01.jpg", self.d / "scan01 (copy).jpg")      # a duplicate is no scan left out
        inv = {}
        imgs, _key = ma.list_images(self.d, 8, inv)
        self.assertEqual(len(imgs), 5)
        self.assertFalse(inv["left_out"])
        self.assertEqual(inv["unreadable"], 0)

    def test_old_callers_unchanged(self):
        scans(self.d, 3)
        imgs, key = ma.list_images(self.d, 15)
        self.assertEqual(len(imgs), 3)
        self.assertEqual(key, ma.images_key(imgs))


@unittest.skipUnless(HAVE_PIL and HAVE_FFMPEG, "Pillow and ffmpeg needed")
class SCN1_ScansComplete(unittest.TestCase):
    """The album the matcher is given, after the real ma.process with OCR stubbed."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.d = self.tmp / "A" / "2001 - Alb"
        self.d.mkdir(parents=True)
        for i in range(2):
            subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i",
                            f"sine=frequency={300 + 50 * i}:duration={5 * (i + 1)}", "-ar", "8000", "-ac", "1",
                            "-metadata", f"title=T{i}", "-metadata", f"track={i + 1}",
                            str(self.d / f"{i + 1:02d}.flac"), "-y"], check=True)

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def run_it(self, cap, fail=None, concurrent=True):
        import test_match as tm
        seen = []

        def text(imgs):
            if fail and fail in str(imgs[0]):
                raise RuntimeError("the OCR program stopped")
            return "some text"
        orig = ma.mb_search
        with mock.patch.object(ma, "mb_search", lambda h, a, c: seen.append(a) or orig(h, a, c)), \
                mock.patch.object(ma, "CONCURRENT_SCANS", concurrent), \
                mock.patch.object(ma, "ocr_barcodes", lambda imgs: set()), \
                mock.patch.object(ma, "ocr_text", text), \
                mock.patch.object(ma, "ocr_spines", lambda imgs: ""), \
                mock.patch.object(ma, "ocr_barcode_digits", lambda *a, **k: ""), \
                mock.patch.object(ma, "ocr_disc_rings", lambda *a, **k: ""), \
                mock.patch.object(ma, "ocr_deep", lambda *a, **k: ""), \
                mock.patch.object(ma, "pdf_deep", lambda *a, **k: ""):
            row = {"AlbumArtistFolder": "A", "AlbumFolder": self.d.name, "FullPath": str(self.d),
                   "Status": "untagged", "OcrCandidateImages": ""}
            self.out = ma.process(row, tm.FakeHttp(), tm.CFG, ma.load_plugin(tm.PLUGIN_DIR),
                                  types.SimpleNamespace(ocr=True, max_images=cap, investigate=False))
        return seen[0]

    def test_a_scan_left_out_by_the_cap(self):
        scans(self.d, 12)
        a = self.run_it(cap=8)
        self.assertFalse(a["scans_complete"], "4 of the 12 scans were never read")
        self.assertEqual(a["scan_inventory"]["found"], 12)
        self.assertEqual((a["scan_inventory"]["unique"], a["scan_inventory"]["read"]), (12, 8))
        self.assertTrue(self.run_it(cap=12)["scans_complete"], "control: all 12 read")

    def test_the_plan_is_told_the_counts(self):
        scans(self.d, 12)
        a = self.run_it(cap=8)
        cov = ma.scan_coverage(a, {"investigated": {"scans": {"read_again": 3}}})
        self.assertEqual({k: cov[k] for k in ("found", "duplicates", "unique", "read", "left_out", "cap")},
                         {"found": 12, "duplicates": 0, "unique": 12, "read": 8, "left_out": 4, "cap": 8})
        self.assertIs(cov["complete"], False)
        self.assertEqual(cov["investigation"], {"read_again": 3})
        self.assertIsNone(ma.scan_coverage({}, {}), "no inventory taken: nothing told")

    def test_a_copy_of_a_scan_read_leaves_it_complete(self):
        scans(self.d, 8)
        shutil.copy(self.d / "scan05.jpg", self.d / "scan05 (copy).jpg")
        a = self.run_it(cap=8)
        self.assertTrue(a["scans_complete"], "8 unique images, all 8 read")
        inv = a["scan_inventory"]
        self.assertEqual((inv["found"], inv["duplicates"], inv["unique"], inv["read"]), (9, 1, 8, 8))

    def test_a_failed_reading_in_the_read_ahead(self):
        scans(self.d, 6)
        a = self.run_it(cap=8, fail="scan0")      # every page reading raises
        self.assertFalse(a["scans_complete"], "readings that failed are no scans read")
        self.assertEqual(len(a["scan_log"]["unread"]), 6, "each failed page reading is unread")
        self.assertTrue(self.run_it(cap=8)["scans_complete"], "control: none failing")

    def test_a_failed_reading_in_order(self):
        scans(self.d, 6)
        self.assertFalse(self.run_it(cap=8, fail="scan0", concurrent=False)["scans_complete"])


class SCN1_ReadAheadFailure(unittest.TestCase):
    def test_a_raising_reading_is_failed_not_empty(self):
        d = Path(tempfile.mkdtemp())
        try:
            p = d / "s.jpg"
            p.write_bytes(b"x" * 10)
            r = ma._ScanReads.__new__(ma._ScanReads)
            import threading
            r.lock, r.closed, r.started, r.conn = threading.Lock(), False, {}, None
            self.assertIs(r._one("t", str(p), lambda path: 1 / 0), False)
            self.assertEqual(r._one("t", str(p), lambda path: ""), "", "control: an empty page is read")
        finally:
            shutil.rmtree(d)


@mock.patch.object(ma, "_read_ahead_kinds", lambda: {"t"})
class SCN1_CachedBlanks(unittest.TestCase):
    """A "" cached before 0.8.56 may be a failed reading: read once more; a blank page read now
    is cached as BLANK_READING and not read again; a reading with text keeps its cached value."""

    def setUp(self):
        self.d = Path(tempfile.mkdtemp())
        self.db = sqlite3.connect(":memory:")
        self.paths = []
        for n in range(3):
            p = self.d / f"s{n}.jpg"
            p.write_bytes(bytes([n + 1]) * 10)
            self.paths.append(str(p))
        self.db.execute("CREATE TABLE ocr_cache (key TEXT PRIMARY KEY, value TEXT)")
        old = {0: '""', 1: '"CAT-123 printed"', 2: '" "'}
        for n, v in old.items():
            self.db.execute("INSERT INTO ocr_cache VALUES (?,?)", (f"t:{ma.scan_key(self.paths[n])}", v))

    def tearDown(self):
        shutil.rmtree(self.d)

    def test_old_blank_read_again_others_kept(self):
        calls = []

        def read(p):
            calls.append(p)
            return ""
        self.assertEqual(ma.each_scan(self.db, "t", self.paths, read), ["", "CAT-123 printed", ""])
        self.assertEqual(calls, [self.paths[0]], "only the old blank is read again")
        v = self.db.execute("SELECT value FROM ocr_cache WHERE key=?", (f"t:{ma.scan_key(self.paths[0])}",)).fetchone()[0]
        self.assertEqual(json.loads(v), ma.BLANK_READING)
        calls.clear()
        ma.each_scan(self.db, "t", self.paths, read)
        self.assertEqual(calls, [], "a blank page read now is not read again")

    def test_failed_reading_again_stays_uncached(self):
        ma.each_scan(self.db, "t", self.paths, lambda p: 1 / 0)
        v = self.db.execute("SELECT value FROM ocr_cache WHERE key=?", (f"t:{ma.scan_key(self.paths[0])}",)).fetchone()[0]
        self.assertEqual(v, '""', "nothing new cached for it: read again next time")

    def test_the_marker_is_a_string_an_older_version_joins(self):
        self.assertIsInstance(ma.BLANK_READING, str)
        self.assertEqual("\n".join(r for r in [ma.BLANK_READING, "x"] if r), " \nx")

    def test_a_kind_never_read_ahead_keeps_its_blank(self):
        calls = []
        self.db.execute("INSERT INTO ocr_cache VALUES (?,?)", (f"deep:{ma.scan_key(self.paths[0])}", '""'))
        ma.each_scan(self.db, "deep", self.paths[:1], lambda p: calls.append(p) or "x")
        self.assertEqual(calls, [])

    def test_read_ahead_honours_the_marker(self):
        import threading
        f = self.d / "cache.db"
        disk = sqlite3.connect(f)
        disk.execute("CREATE TABLE ocr_cache (key TEXT PRIMARY KEY, value TEXT)")
        for n, v in {0: '""', 2: '" "'}.items():
            disk.execute("INSERT INTO ocr_cache VALUES (?,?)", (f"t:{ma.scan_key(self.paths[n])}", v))
        disk.commit()
        r = ma._ScanReads.__new__(ma._ScanReads)
        r.lock, r.closed, r.started, r.conn = threading.Lock(), False, {}, disk
        r.BUDGETS = {}
        self.assertEqual(r._one("t", self.paths[0], lambda p: "fresh"), "fresh", "old blank: read again")
        self.assertEqual(r._one("t", self.paths[2], lambda p: "never"), "", "a blank page cached the new way")
        disk.close()


@unittest.skipUnless(HAVE_PIL, "Pillow needed")
class SCN1_InvestigationSaysWhatItRead(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp())
        scans(self.d, 8)

    def tearDown(self):
        shutil.rmtree(self.d)

    def note(self, sure):
        album = {"barcodes": set(), "catno_search": [], "artist": "A", "title": "T", "ocr_barcodes": {"1"}}
        final = {"status": "auto", "why": [], "best": {"notes": []}}
        with mock.patch.object(ma, "ocr_deep", lambda paths: ""), \
                mock.patch.object(ma, "pdf_deep", lambda base, db=None: ""), \
                mock.patch.object(ma, "ocr_disc_rings", lambda paths, *a, **k: ""), \
                mock.patch.object(ma, "_needs_investigation", lambda *a: not sure), \
                mock.patch.object(ma, "_investigate_codes", lambda *a: (None, None, final)), \
                mock.patch.object(ma, "web_release_links", lambda *a, **k: ([], [])):
            _m, _d, new, _t = ma.investigate(None, self.d, album, dict(mc.DEFAULT_CFG), None, None, None,
                                             final, "", {}, [], False, False, None)
        return next(n for n in new["best"]["notes"] if n.startswith("investigated"))

    def test_sure_after_the_first_six(self):
        n = self.note(sure=True)
        self.assertNotIn("every scan", n)
        self.assertIn("6 of 8 scans", n)

    def test_all_read(self):
        self.assertIn("8 of 8 scans", self.note(sure=False))

    def test_failed_readings_are_not_read_again(self):
        def deep(paths):
            if "scan02" in str(paths[0]):
                raise RuntimeError("stopped")
            return ""
        with mock.patch.object(ma, "ocr_deep", deep):
            album = {"barcodes": set(), "catno_search": [], "artist": "A", "title": "T", "ocr_barcodes": {"1"}}
            final = {"status": "auto", "why": [], "best": {"notes": []}}
            with mock.patch.object(ma, "pdf_deep", lambda base, db=None: ""), \
                    mock.patch.object(ma, "ocr_disc_rings", lambda paths, *a, **k: ""), \
                    mock.patch.object(ma, "_needs_investigation", lambda *a: True), \
                    mock.patch.object(ma, "_investigate_codes", lambda *a: (None, None, final)), \
                    mock.patch.object(ma, "web_release_links", lambda *a, **k: ([], [])):
                _m, _d, new, _t = ma.investigate(None, self.d, album, dict(mc.DEFAULT_CFG), None, None, None,
                                                 final, "", {}, [], False, False, None)
        n = next(x for x in new["best"]["notes"] if x.startswith("investigated"))
        self.assertIn("7 of 8 scans", n)
        self.assertIn("could not be read", n)

    def investigate_all(self):
        album = {"barcodes": set(), "catno_search": [], "artist": "A", "title": "T", "ocr_barcodes": {"1"}}
        final = {"status": "auto", "why": [], "best": {"notes": []}}
        with mock.patch.object(ma, "ocr_deep", lambda paths: ""), \
                mock.patch.object(ma, "pdf_deep", lambda base, db=None: ""), \
                mock.patch.object(ma, "ocr_disc_rings", lambda paths, *a, **k: ""), \
                mock.patch.object(ma, "_needs_investigation", lambda *a: True), \
                mock.patch.object(ma, "_investigate_codes", lambda *a: (None, None, final)), \
                mock.patch.object(ma, "web_release_links", lambda *a, **k: ([], [])):
            out = {}
            _m, _d, new, _t = ma.investigate(None, self.d, album, dict(mc.DEFAULT_CFG), None, None, None,
                                             final, "", out, [], False, False, None)
        new["investigated"] = out["investigated"]
        return new

    def test_an_earlier_failure_read_now_is_not_told_as_failed(self):
        log = ma.new_scan_log()
        log["unread"] = {str(self.d / "scan02.jpg")}     # its page reading failed earlier in process
        album = {"barcodes": set(), "catno_search": [], "artist": "A", "title": "T", "ocr_barcodes": {"1"},
                 "scan_log": log}
        final = {"status": "auto", "why": [], "best": {"notes": []}}
        with mock.patch.object(ma, "ocr_deep", lambda paths: ""), \
                mock.patch.object(ma, "pdf_deep", lambda base, db=None: ""), \
                mock.patch.object(ma, "ocr_disc_rings", lambda paths, *a, **k: ""), \
                mock.patch.object(ma, "_needs_investigation", lambda *a: True), \
                mock.patch.object(ma, "_investigate_codes", lambda *a: (None, None, final)), \
                mock.patch.object(ma, "web_release_links", lambda *a, **k: ([], [])):
            _m, _d, new, _t = ma.investigate(None, self.d, album, dict(mc.DEFAULT_CFG), None, None, None,
                                             final, "", {}, [], False, False, None)
        n = next(x for x in new["best"]["notes"] if x.startswith("investigated"))
        self.assertIn("8 of 8 scans", n)
        self.assertNotIn("could not be read", n)

    def test_unique_scans_past_the_investigation_cap_stay_unread(self):
        # album 02 of pilot 1: 89 images, the investigation looks at 80 - the rest are not
        # exhausted evidence, they are scans not read
        for k in range(9, 84):
            Image.new("L", (300, 300), k).save(self.d / f"more{k:02d}.png")
        new = self.investigate_all()
        n = next(x for x in new["best"]["notes"] if x.startswith("investigated"))
        self.assertIn("80 of 83 scans", n)
        self.assertIn("3 others not read", n)
        self.assertEqual(new["investigated"]["scans"]["left_out"], 3)

    def test_copies_past_the_investigation_cap_are_not_unread(self):
        for k in range(9, 81):
            Image.new("L", (300, 300), k).save(self.d / f"more{k:02d}.png")
        for k in range(3):
            shutil.copy(self.d / "scan01.jpg", self.d / f"zz copy {k}.jpg")
        new = self.investigate_all()
        n = next(x for x in new["best"]["notes"] if x.startswith("investigated"))
        self.assertIn("80 of 80 scans", n)
        self.assertNotIn("not read", n)
        self.assertEqual(new["investigated"]["scans"]["duplicates"], 3)


if __name__ == "__main__":
    unittest.main()
