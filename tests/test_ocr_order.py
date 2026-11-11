"""Offline tests for the order scans are read in, and what OCR learns from them.

- Every scan has a role (back, disc, obi, booklet, front...) from its name or folder.
- Scans are read in the order of their roles' hit rates (shipped counts plus this computer's);
  within a role single panels come before spreads (Alcest - Les Voyages De L'âme: the back
  with its sticker barcode before 28 booklet spreads, of which only 15 are read).
- record() keeps role counts only; export() has no file name, folder or album in it.
- A scan "paid off" when its own reading holds a code the identified release carries.
- Codes read off scans: misreadings ("ZB 44", "Aq30") are not searched; each number is asked
  with the artist first, the wider queries only when that finds nothing.
- The investigation reads the first scans first and stops when the album is then sure.
- Tesseract runs several at once; the readings come back in the scans' order.

Every image is drawn here (PIL); no file of the library is read.
Run:  python3 tests/test_ocr_order.py
"""
import os as _os, tempfile as _tempfile  # noqa: E402
_os.environ["XDG_CONFIG_HOME"] = _tempfile.mkdtemp(prefix="tm-config-")
_os.environ["XDG_STATE_HOME"] = _tempfile.mkdtemp(prefix="tm-state-")

import io
import json
import shutil
import sqlite3
import sys
import tempfile
import threading
import time
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
if len(sys.argv) > 1 and not sys.argv[1].startswith("-"):
    sys.argv.pop(1)                                   # a plugin folder: not needed here

import exclusions as ex  # noqa: E402
import match_core as mc  # noqa: E402
import match_albums as ma  # noqa: E402
import ocr_order  # noqa: E402

try:
    from PIL import Image
    HAVE_PIL = True
except ImportError:
    HAVE_PIL = False


class Isolated(unittest.TestCase):
    """Each test with an empty state folder of its own (this computer's counts)."""
    def setUp(self):
        self.state = Path(tempfile.mkdtemp())
        self.patch = mock.patch.object(ex, "STATE_DIR", self.state)
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        shutil.rmtree(self.state, ignore_errors=True)


class Roles(unittest.TestCase):
    def test_roles_from_names_and_folders(self):
        cases = {"Back.jpg": "back", "rear inlay.png": "back", "CD1.jpg": "disc", "Disc 2.jpg": "disc",
                 "matrix.png": "disc", "obi.jpg": "obi", "Spine.jpg": "spine", "sticker.jpg": "label",
                 "Inlay.jpg": "tray", "Booklet - 01.jpg": "booklet", "img012.jpg": "booklet",
                 "Front.jpg": "front", "folder.jpg": "front", "photo.jpg": "other",
                 "Booklet/07.jpg": "booklet", "Scans/whatever.jpg": "other"}
        for name, role in cases.items():
            self.assertEqual(ocr_order.role_of(Path("/x") / name), role, name)

    def test_every_role_has_shipped_counts(self):
        shipped = json.loads(ocr_order.SHIPPED.read_text(encoding="utf-8"))["roles"]
        self.assertEqual(set(shipped), set(ocr_order.ROLE_NAMES))


class Ranking(Isolated):
    def test_codes_first_pictures_last(self):
        names = ["Front.jpg", "Booklet - 01.jpg", "Back.jpg", "CD.jpg", "photo.jpg"]
        order = [Path(p).name for p in ocr_order.rank(names)]
        self.assertEqual(order[0], "Back.jpg")
        self.assertLess(order.index("CD.jpg"), order.index("Booklet - 01.jpg"))
        self.assertEqual(set(order[-2:]), {"Front.jpg", "Booklet - 01.jpg"})

    def test_the_tie_breaker_orders_within_a_role(self):
        names = ["img1.jpg", "img2.jpg", "img3.jpg"]
        self.assertEqual(ocr_order.rank(names, tie=lambda p: 0 if p == "img3.jpg" else 1)[0], "img3.jpg")

    def test_learned_counts_change_the_order(self):
        names = ["Front.jpg", "Booklet - 01.jpg"]
        self.assertEqual(ocr_order.rank(names)[0], "Booklet - 01.jpg")      # name order: both 1 of 20
        ocr_order.record(["Front.jpg"] * 30, ["Front.jpg"])                   # every front paid off
        self.assertEqual(ocr_order.rank(names)[0], "Front.jpg")

    @unittest.skipUnless(HAVE_PIL, "Pillow needed")
    def test_alcest_the_back_before_28_booklet_spreads(self):
        """img001-img028 booklet spreads, img029 the back with the sticker barcode, img030-031
        the discs; only 15 scans are read."""
        d = Path(tempfile.mkdtemp())
        try:
            for n in range(1, 32):
                size = (2000, 1870) if n == 29 else (2800, 2880) if n >= 30 else (3700, 1880)
                Image.new("L", size, n).save(d / f"img{n:03d}.jpg")
            imgs, _key = ma.list_images(d, 15)
            self.assertEqual([Path(p).name for p in imgs[:3]], ["img029.jpg", "img030.jpg", "img031.jpg"])
            self.assertEqual(len(imgs), 15)
            Image.new("L", (900, 900), 77).save(d / "back.jpg")
            self.assertEqual(Path(ma.list_images(d, 15)[0][0]).name, "back.jpg")
        finally:
            shutil.rmtree(d)

    @unittest.skipUnless(HAVE_PIL, "Pillow needed")
    def test_duplicates_once_and_the_key_names_the_contents(self):
        d = Path(tempfile.mkdtemp())
        try:
            Image.new("L", (900, 900), 1).save(d / "back.jpg")
            shutil.copy(d / "back.jpg", d / "back (copy).jpg")
            Image.new("L", (900, 900), 2).save(d / "cd.jpg")
            imgs, key = ma.list_images(d, 15)
            self.assertEqual(len(imgs), 2)
            self.assertEqual(key, ma.images_key(imgs))
            (d / "cd.jpg").unlink()
            Image.new("L", (900, 900), 3).save(d / "cd.jpg")
            self.assertNotEqual(ma.list_images(d, 15)[1], key)
        finally:
            shutil.rmtree(d)


class Counts(Isolated):
    def test_record_keeps_role_counts_only(self):
        ocr_order.record(["/music/Artist/Album/Scans/Back.jpg", "/music/Artist/Album/Scans/Booklet 1.jpg"],
                         ["/music/Artist/Album/Scans/Back.jpg"])
        ocr_order.record(["/music/Other/Album/back.png"], [])
        text = (self.state / "ocr_order.json").read_text(encoding="utf-8")
        for word in ("music", "Artist", "Album", "Scans", ".jpg", ".png", "/"):
            self.assertNotIn(word, text)
        roles = json.loads(text)["roles"]
        self.assertEqual(roles["back"], {"reads": 2, "hits": 1})
        self.assertEqual(roles["booklet"], {"reads": 1, "hits": 0})
        self.assertEqual(ocr_order.counts()["back"]["reads"], 2 + ocr_order._read(ocr_order.SHIPPED)["back"]["reads"])

    def test_export_is_this_computers_counts_without_paths(self):
        ocr_order.record(["/home/someone/Music/X/Y/cd.jpg"], ["/home/someone/Music/X/Y/cd.jpg"])
        data = json.loads(ocr_order.export())
        self.assertEqual(data["roles"]["disc"], {"reads": 1, "hits": 1})
        self.assertNotIn("someone", ocr_order.export())
        self.assertNotIn("/", ocr_order.export())
        self.assertEqual(set(data), {"version", "roles"})

    def test_stats_rows(self):
        ocr_order.record(["back.jpg"], ["back.jpg"])
        rows = ocr_order.stats()
        self.assertEqual({r["role"] for r in rows}, set(ocr_order.ROLE_NAMES))
        back = next(r for r in rows if r["role"] == "back")
        self.assertEqual(back["mine"], {"reads": 1, "hits": 1})
        self.assertEqual([r["rate"] for r in rows], sorted((r["rate"] for r in rows), reverse=True))

    def test_cli_stats_and_export(self):
        import tagmender
        ocr_order.record(["obi.jpg"], ["obi.jpg"])
        buf = io.StringIO()
        with redirect_stdout(buf):
            self.assertEqual(tagmender.main(["ocr", "stats"]), 0)
        self.assertIn("obi", buf.getvalue())
        out = self.state / "share.json"
        with redirect_stdout(io.StringIO()):
            self.assertEqual(tagmender.main(["ocr", "stats", "--export", str(out)]), 0)
        self.assertEqual(json.loads(out.read_text())["roles"]["obi"], {"reads": 1, "hits": 1})
        buf = io.StringIO()
        with redirect_stdout(buf):
            self.assertEqual(tagmender.main(["ocr", "stats", "--export"]), 0)
        self.assertEqual(json.loads(buf.getvalue())["roles"]["obi"], {"reads": 1, "hits": 1})


class PaidOff(Isolated):
    CAND = {"barcodes": ["5013929020160"], "catnos": ["qdiss201cdd"],
            "matrix": [("Matrix / Runout", "QDISS0201CDD-1 15 A00")]}

    def test_scans_whose_own_reading_holds_the_releases_code(self):
        log = ma.new_scan_log()
        log["read"] = {"back.jpg", "booklet.jpg", "cd.jpg", "sticker.jpg"}
        log["text"] = {"back.jpg": ["Cat. No. QDISS0201CDD  Made in EU"],
                       "booklet.jpg": ["Lyrics and thanks 1999"],
                       "cd.jpg": ["QDISS0201CDD-1 15 A00"]}
        log["barcodes"] = {"sticker.jpg": {"5013929020160"}}
        self.assertEqual(ma.scans_confirmed(log, self.CAND), ["back.jpg", "cd.jpg", "sticker.jpg"])
        self.assertEqual(ma.scans_confirmed(log, {"barcodes": ["123"], "catnos": ["zz99"]}), [])

    def test_a_barcode_printed_on_the_scan(self):
        log = ma.new_scan_log()
        log["text"] = {"back.jpg": ["5 013929 020160"]}
        self.assertEqual(ma.scans_confirmed(log, self.CAND), ["back.jpg"])

    def test_learning_after_a_sure_result_only(self):
        log = ma.new_scan_log()
        log["read"] = {"/m/a/Back.jpg", "/m/a/Booklet 1.jpg"}
        log["text"] = {"/m/a/Back.jpg": ["QDISS0201CDD"]}
        ma.learn_scan_order(log, {"status": "auto", "best": {"cand": self.CAND}})
        roles = json.loads(ocr_order.export())["roles"]
        self.assertEqual(roles["back"], {"reads": 1, "hits": 1})
        self.assertEqual(roles["booklet"], {"reads": 1, "hits": 0})
        ma.learn_scan_order(ma.new_scan_log(), {"status": "auto", "best": {"cand": self.CAND}})   # nothing read
        self.assertEqual(json.loads(ocr_order.export())["roles"]["back"]["reads"], 1)


class JunkCodes(unittest.TestCase):
    def test_misreadings_are_not_searched(self):
        for junk in ("ZB 44", "Aq30", "AB12"):
            self.assertFalse(ma.plausible_scan_catno(junk), junk)
        for real in ("AMG 099", "NB 6570-2", "FO40CD", "QDISS0201CDD", "HHR 2018-23", "SPV 91978"):
            self.assertTrue(ma.plausible_scan_catno(real), real)

    def test_the_documents_numbers_stay_as_typed(self):
        self.assertEqual(ma.scan_catnos(["ZB 44", "AMG 099"], ""), ["AMG 099"])
        self.assertEqual(ma.scan_catnos(["ZB 44", "AMG 099"], "Catalog: ZB 44"), ["ZB 44", "AMG 099"])


class FakeHttp:
    def __init__(self, answers):
        self.answers, self.asked = answers, []

    def get_json(self, url, source):
        self.asked.append(url)
        for part, data in self.answers.items():
            if part in url:
                return data
        return {}


class NarrowFirst(unittest.TestCase):
    ALBUM = {"title": "Album", "artist": "Artist", "year": None, "barcodes": set(), "catno_search": ["AMG 099"],
             "search_labels": ["Art Music"], "tracks": [{}] * 3, "narrow_codes_first": True}

    def test_mb_wide_queries_only_when_the_artists_find_nothing(self):
        hit = {"releases": [{"id": "r1", "track-count": 3}]}
        http = FakeHttp({"artist%3A%22Artist%22": hit})
        with mock.patch.object(ma, "mb_candidate", lambda det: {"id": det.get("id")}):
            ma.mb_search(http, dict(self.ALBUM), dict(mc.DEFAULT_CFG))
        searches = [u for u in http.asked if "catno" in u]
        self.assertEqual(len(searches), 1, searches)                     # catno + artist only
        http = FakeHttp({})
        ma.mb_search(http, dict(self.ALBUM), dict(mc.DEFAULT_CFG))
        searches = [u for u in http.asked if "catno" in u]
        self.assertEqual(len(searches), 3, searches)                     # + label, + catno alone
        http = FakeHttp({"artist%3A%22Artist%22": hit})
        with mock.patch.object(ma, "mb_candidate", lambda det: {"id": det.get("id")}):
            ma.mb_search(http, dict(self.ALBUM, narrow_codes_first=False), dict(mc.DEFAULT_CFG))
        self.assertEqual(len([u for u in http.asked if "catno" in u]), 3)  # tags' numbers: as before

    def test_discogs_wide_searches_only_when_the_artists_find_nothing(self):
        dm = mock.Mock()
        dm.split_discogs_title = lambda t: ("Artist", "Album")
        dm.clean_artist_name = lambda a: a
        hit = {"results": [{"id": 1, "title": "Artist - Album"}]}
        for answers, want in (({"catno=AMG%20099&artist=Artist": hit}, 1), ({}, 3)):
            http = FakeHttp(answers)
            with mock.patch.object(ma, "discogs_candidate", lambda det, dm: {"id": 1, "tracks": [], "catnos": []}):
                ma.discogs_search(http, dict(self.ALBUM), dict(mc.DEFAULT_CFG), dm)
            self.assertEqual(len([u for u in http.asked if "catno=" in u]), want, http.asked)


@unittest.skipUnless(HAVE_PIL, "Pillow needed")
class Stages(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp())
        for n in range(1, 9):
            Image.new("L", (900, 900), n).save(self.d / f"scan{n}.jpg")

    def tearDown(self):
        shutil.rmtree(self.d)

    def run_it(self, sure):
        read = []
        album = {"barcodes": set(), "catno_search": [], "artist": "A", "title": "T", "ocr_barcodes": {"1"}}
        with mock.patch.object(ma, "ocr_deep", lambda paths: read.extend(paths) or ""), \
                mock.patch.object(ma, "pdf_deep", lambda base, db=None: ""), \
                mock.patch.object(ma, "ocr_disc_rings", lambda paths, *a, **k: ""), \
                mock.patch.object(ma, "_needs_investigation", lambda *a: not sure), \
                mock.patch.object(ma, "web_release_links", lambda *a, **k: ([], [])):
            ma.investigate(None, self.d, album, dict(mc.DEFAULT_CFG), None, None, None,
                           {"status": "review", "why": []}, "", {}, [], False, False, None)
        return read

    def test_sure_after_the_first_six(self):
        read = self.run_it(sure=True)
        self.assertEqual(sorted(read), sorted(ma.list_images(self.d, 80)[0][:6]))

    def test_still_unsure_reads_the_rest(self):
        self.assertEqual(sorted(self.run_it(sure=False)), sorted(ma.list_images(self.d, 80)[0]))


class Parallel(unittest.TestCase):
    def test_results_in_order_and_cached_per_scan(self):
        db = sqlite3.connect(":memory:")
        d = Path(tempfile.mkdtemp())
        try:
            paths = []
            for n in range(6):
                p = d / f"s{n}.jpg"
                p.write_bytes(bytes([n]) * 10)
                paths.append(str(p))
            threads = set()

            def read(p):
                threads.add(threading.get_ident())
                time.sleep(0.05 * (6 - int(Path(p).stem[1:])))    # the first finish last
                return Path(p).stem
            log = ma.new_scan_log()
            with mock.patch.object(ma, "ocr_workers", lambda: 3):
                self.assertEqual(ma.each_scan(db, "t", paths, read, log), [f"s{n}" for n in range(6)])
            self.assertGreater(len(threads), 1)
            self.assertEqual(log["read"], set(paths))
            log = ma.new_scan_log()
            self.assertEqual(ma.each_scan(db, "t", list(reversed(paths)), lambda p: 1 / 0, log),
                             [f"s{n}" for n in reversed(range(6))])                  # cached per scan
            self.assertEqual(log["read"], set())
            self.assertEqual(ma.each_scan(None, "t", paths, read, budget_s=0.0)[1:], [""] * 5)
        finally:
            shutil.rmtree(d)

    def test_a_pool_inside_a_pool_runs_in_turn(self):
        with mock.patch.object(ma, "ocr_workers", lambda: 3):
            self.assertEqual(ma._pmap(lambda x: ma._pmap(lambda y: x * y, [1, 2]), [1, 2, 3]),
                             [[1, 2], [2, 4], [3, 6]])

    @unittest.skipUnless(HAVE_PIL, "Pillow needed")
    def test_deep_tiles_read_in_english_only(self):
        d = Path(tempfile.mkdtemp())
        try:
            Image.new("L", (4000, 2000), 255).save(d / "spread.png")
            calls = []

            def tess(img, psm, langs=None, tsv=False, only="", deadline=None):
                calls.append((psm, langs))
                return ""
            with mock.patch.object(ma, "_tess", tess), mock.patch.object(ma, "ocr_langs", lambda: "eng+jpn"):
                ma.ocr_deep([str(d / "spread.png")])
            self.assertTrue(calls)
            self.assertTrue(all(langs is None for _psm, langs in calls), calls)
        finally:
            shutil.rmtree(d)


class BarcodeSettles(unittest.TestCase):
    def test_only_a_sure_pick_carrying_the_decoded_barcode(self):
        album = {"ocr_barcodes": {"5013929020160"}}
        sure = {"status": "auto", "best": {"cand": {"barcodes": ["5013929020160"]}}}
        self.assertTrue(ma._barcode_settles(sure, album))
        self.assertFalse(ma._barcode_settles(dict(sure, status="review"), album))
        self.assertFalse(ma._barcode_settles({"status": "auto", "best": {"cand": {"barcodes": ["123"]}}}, album))
        self.assertFalse(ma._barcode_settles(sure, {"ocr_barcodes": set()}))


if __name__ == "__main__":
    unittest.main(verbosity=2)
