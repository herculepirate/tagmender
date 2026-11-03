"""Offline tests for physical evidence (M2 group C): Discogs matrix / runout, SID and label
codes compared with the disc scans' OCR, OCR of the front cover embedded in the files when
a folder has no scans, and the profile's `ocr: auto|on|off`.

Run:  python3 tests/test_physical.py [plugin-dir]
Needs mutagen and ffmpeg (ffmpeg only to build tiny synthetic albums). No network: lookups
go to recorded-style stubs, OCR to stubs (tesseract is never needed).
"""
import os as _os, tempfile as _tempfile  # noqa: E401
_os.environ["XDG_CONFIG_HOME"] = _tempfile.mkdtemp(prefix="tm-config-")
_os.environ["XDG_STATE_HOME"] = _tempfile.mkdtemp(prefix="tm-state-")
_os.environ.setdefault("TAGMENDER_TAGGER_INI", "/nonexistent/Picard.ini")

import io
import shutil
import subprocess
import sys
import tempfile
import types
import unittest
import urllib.parse as up
from unittest import mock
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
PLUGIN = Path(_os.environ.get("TAGMENDER_DISCOGS_PLUGIN") or
              (sys.argv[1] if len(sys.argv) > 1 and not sys.argv[1].startswith("-") else
               "../picard-discogs-enrichment")).expanduser()
if len(sys.argv) > 1 and not sys.argv[1].startswith("-"):
    sys.argv.pop(1)
_os.environ["TAGMENDER_DISCOGS_PLUGIN"] = str(PLUGIN)

import match_core as mc  # noqa: E402
import match_albums as ma  # noqa: E402
import profiles as pf  # noqa: E402

CFG = dict(mc.DEFAULT_CFG, preferred_countries=[])
MATRIX = "QDISS0201CDD-1 15 A00"


def album(**kw):
    a = {"artist": "Artist Alpha", "title": "First Light", "year": 2010, "discs": 1,
         "tracks": [{"disc": 1, "num": i + 1, "title": f"T{i}", "secs": s}
                    for i, s in enumerate((200.0, 305.0, 410.0))],
         "barcodes": set(), "catnos": set()}
    a.update(kw)
    return a


def cand(**kw):
    c = {"source": "discogs", "id": "1", "url": "u", "artist": "Artist Alpha", "title": "First Light",
         "year": 2010, "country": "Germany", "label": "Lbl", "catnos": {mc.norm_code("QDISS0201CDD")},
         "barcodes": set(), "disc_count": 1, "matrix": [],
         "tracks": [{"disc": 1, "title": f"T{i}", "secs": s} for i, s in enumerate((200, 305, 410))]}
    c.update(kw)
    return c


def mb_release(rid, artist, title, year, lens_ms, catno=""):
    return {"id": rid, "title": title, "date": str(year), "country": "DE",
            "artist-credit": [{"name": artist, "joinphrase": ""}],
            "label-info": [{"catalog-number": catno, "label": {"name": "L"}}] if catno else [],
            "media": [{"format": "CD", "tracks": [{"title": f"T{i}", "length": ms} for i, ms in enumerate(lens_ms)]}]}


def make_album(root, artist, folder, picture=None):
    """Three short FLAC tracks, the first with an embedded front cover when given."""
    d = Path(root) / artist / folder
    d.mkdir(parents=True)
    for i in range(3):
        subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i",
                        f"sine=frequency={300 + 50 * i}:duration={10 * (i + 1)}", "-ar", "8000", "-ac", "1",
                        "-metadata", f"title=T{i}", "-metadata", f"track={i + 1}",
                        str(d / f"{i + 1:02d}.flac"), "-y"], check=True)
    if picture:
        from mutagen.flac import FLAC, Picture
        f = FLAC(d / "01.flac")
        p = Picture()
        p.type, p.mime, p.data = 3, "image/png", picture
        f.add_picture(p)
        f.save()
    return d


def png_bytes(size=(300, 300)):
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", size, (200, 30, 30)).save(buf, "PNG")
    return buf.getvalue()


class Matrix(unittest.TestCase):
    def test_codes_are_normalised_conservatively(self):
        self.assertEqual(mc.matrix_tokens("QDISSO201CDD-1  15 AOO"), ["qd1ss0201cdd", "1", "15", "a00"])
        self.assertEqual(mc.matrix_tokens(MATRIX), mc.matrix_tokens("qdiss0201cdd 1 15 a00"))
        self.assertEqual(mc.matrix_tokens("S5B8"), ["s5b8"])             # S/5, B/8 are not folded

    def test_a_short_code_never_matches(self):
        """'1', 'A', 'A-1': on every sleeve (track and side numbers)."""
        for value in ("1", "A", "A-1", "B2", "15"):
            self.assertEqual(mc.matrix_read("Side A 1. Intro  A-1 B2 15", value), "", value)

    def test_the_matrix_read_on_the_disc_agrees(self):
        c = cand(matrix=[("Matrix / Runout", MATRIX)])
        agree, against = mc.pressing_clues("ring: QDISSO201CDD-1  15 AOO", c)
        self.assertIn("matrix", agree)
        self.assertEqual(against, [])
        # OCR split the code over lines (tesseract's sparse mode): still one code
        self.assertIn("matrix", mc.pressing_clues("QDISS0201CDD\n-1\n15\nA00", c)[0])

    def test_the_catalogue_number_alone_is_no_matrix_agreement(self):
        """The matrix holds the catalogue number; the back cover printing it says nothing new,
        nor does a number after it that ends elsewhere ("QDISS0201CDD 1999")."""
        c = cand(matrix=[("Matrix / Runout", MATRIX)])
        for back in ("QDISS0201CDD", "QDISS0201CDD 1999", "QDISS0201CDD\n1. Intro", "QDISS0201CDD-1"):
            agree, _ = mc.pressing_clues(back, c)
            self.assertNotIn("matrix", agree, back)
            self.assertIn("catalogue number", agree, back)

    def test_sid_and_label_codes_are_weak_clues(self):
        c = cand(matrix=[("Mastering SID Code", "IFPI L041"), ("Mould SID Code", "IFPI 94X1"),
                         ("Label Code", "LC 0123")])
        agree, _ = mc.pressing_clues("ifpi 94X1   LC O123", c)
        self.assertIn("sid code", agree)
        self.assertIn("label code", agree)
        self.assertLess(mc.CLUE_WEIGHTS["sid code"], mc.CLUE_WEIGHTS["matrix"])
        self.assertLess(mc.CLUE_WEIGHTS["matrix"], mc.CLUE_WEIGHTS["catalogue number"])

    def test_a_matrix_the_ring_reads_with_other_digits_is_against(self):
        c = cand(matrix=[("Matrix / Runout", MATRIX)])
        _, against = mc.pressing_clues("x", c, ring_text="QDISS0274CDD-1 15 A00")
        self.assertTrue(any(x.startswith("disc ring reads") for x in against), against)
        # one digit off may be a misreading; another pressing's code in the page text is no conflict
        self.assertEqual(mc.pressing_clues("x", c, ring_text="QDISS0202CDD-1")[1], [])
        self.assertEqual(mc.pressing_clues("QDISS0274CDD-1 15 A00", c)[1], [])
        # a disc of the set listed with its own matrix: not against
        two = cand(matrix=[("Matrix / Runout", MATRIX), ("Matrix / Runout", "QDISS0274CDD-2 15 A00")])
        self.assertEqual(mc.pressing_clues("x", two, ring_text="QDISS0274CDD-2")[1], [])

    def test_the_matrix_tells_tied_pressings_apart(self):
        """Two pressings with the same numbers and lengths: the disc's matrix names one."""
        a = cand(id="10", matrix=[("Matrix / Runout", "QDISS0201CDD-1 15 A00")])
        b = cand(id="11", matrix=[("Matrix / Runout", "QDISS0201CDD-2 22 B07")])
        ring = "QDISSO201CDD-2 22 BO7"
        alb = album(ring_text=ring)
        scored = [mc.score_candidate(alb, c, CFG, ocr_text=ring) for c in (a, b)]
        self.assertEqual(scored[0]["confidence"], scored[1]["confidence"])    # no points, a clue
        r = mc.classify(scored, CFG)
        self.assertEqual(r["best"]["cand"]["id"], "11")
        self.assertTrue(any("pressing chosen by matrix" in n for n in r["best"]["notes"]), r["best"]["notes"])
        self.assertTrue(any("agrees (read on the scans)" in n for n in r["best"]["notes"]))

    def test_a_matrix_never_makes_a_poor_match_auto(self):
        bad = cand(matrix=[("Matrix / Runout", "ABCD-77123-A1")], catnos=set(),
                   tracks=[{"disc": 1, "title": f"T{i}", "secs": s} for i, s in enumerate((90, 500, 700))])
        s = mc.score_candidate(album(ring_text="ABCD 77123 A1"), bad, CFG, ocr_text="ABCD 77123 A1")
        self.assertIn("matrix", s["clues"])
        self.assertNotEqual(mc.classify([s], CFG)["status"], "auto")
        plain = mc.score_candidate(album(), bad, CFG, ocr_text="ABCD 99")
        self.assertEqual(s["confidence"], plain["confidence"])

    def test_a_conflicting_matrix_stops_the_scans_picking_that_pressing(self):
        a = cand(id="10", label="Nuclear Blast", matrix=[("Matrix / Runout", "QDISS0201CDD-1 15 A00")])
        b = cand(id="11", label="Other Label", matrix=[("Matrix / Runout", "QDISS0201CDD-2 22 B07")])
        ring = "QDISS0388CDD-1 15 A00"                 # another pressing's, read clearly
        text = "Nuclear Blast " + ring
        scored = [mc.score_candidate(album(ring_text=ring), c, CFG, ocr_text=text) for c in (a, b)]
        self.assertIn("label", scored[0]["clues"])
        self.assertTrue(scored[0]["clues_against"])
        pick = mc.choose_pressing(scored)
        self.assertFalse(pick and "label" in pick[1], pick and pick[1])     # the label no longer picks it

    def test_a_piece_of_the_catalogue_number_is_no_matrix_agreement(self):
        """CD matrices are often the catalogue number plus a suffix ("NB 1234-2 #1"); a back
        cover printing "NB 1234-2" holds "NB1234", a piece of the number, not of the matrix."""
        c = cand(catnos={mc.norm_code("NB 1234-2")},
                 matrix=[("Matrix / Runout", "NB 1234-2 #1"), ("Matrix / Runout", "NB 1234-2 01"),
                         ("Matrix / Runout", "NB1234-2 DADC")])
        agree, _ = mc.pressing_clues("Nuclear Blast GmbH NB 1234-2 Made in Germany", c)
        self.assertNotIn("matrix", agree)
        self.assertIn("catalogue number", agree)
        # the matrix read whole on the ring still agrees
        self.assertIn("matrix", mc.pressing_clues("x", c, ring_text="NB1234-2 DADC")[0])

    def test_a_disc_code_only_one_entry_lists_tells_nothing(self):
        """Two tied pressings of one label, only one entered with its label code: the code on
        the back cover is on both copies, so it must not pick the one that lists it."""
        a = cand(id="10", catnos={mc.norm_code("NB 1234-2")}, matrix=[("Label Code", "LC 07158")])
        b = cand(id="11", catnos={mc.norm_code("NB 1234-2")}, matrix=[])
        scored = [mc.score_candidate(album(), c, CFG, ocr_text="LC 07158  NB 1234-2") for c in (a, b)]
        self.assertIn("label code", scored[0]["clues"])
        pick = mc.choose_pressing(scored)
        self.assertFalse(pick and "label code" in pick[1], pick and pick[1])
        # nor a matrix or SID code only one of them lists
        a = cand(id="10", matrix=[("Matrix / Runout", MATRIX), ("Mould SID Code", "IFPI 94X1")])
        b = cand(id="11", matrix=[])
        scored = [mc.score_candidate(album(), c, CFG, ocr_text=MATRIX + " IFPI 94X1") for c in (a, b)]
        pick = mc.choose_pressing(scored)
        self.assertFalse(pick and ({"matrix", "sid code"} & set(pick[1])), pick and pick[1])
        # both list one: then it tells them apart
        b = cand(id="11", matrix=[("Matrix / Runout", "QDISS0201CDD-2 22 B07")])
        scored = [mc.score_candidate(album(), c, CFG, ocr_text=MATRIX) for c in (a, b)]
        self.assertEqual(mc.choose_pressing(scored)[0]["cand"]["id"], "10")

    def test_discogs_identifiers_are_kept_on_the_candidate(self):
        dm = ma.load_plugin(str(PLUGIN))
        rel = {"id": 77, "title": "First Light", "year": 2010, "uri": "https://www.discogs.com/release/77",
               "artists": [{"name": "Artist Alpha"}], "labels": [{"name": "Lbl", "catno": "QDISS0201CDD"}],
               "country": "UK", "tracklist": [{"type_": "track", "position": "1", "title": "T0", "duration": "3:20"}],
               "identifiers": [{"type": "Barcode", "value": "5013929020160"},
                               {"type": "Matrix / Runout", "value": MATRIX, "description": "Variant 1"},
                               {"type": "Mould SID Code", "value": "IFPI 94X1"},
                               {"type": "Label Code", "value": "LC 0123"},
                               {"type": "Rights Society", "value": "MCPS"},
                               {"type": "Matrix / Runout", "value": ""}]}
        c = ma.discogs_candidate(rel, dm)
        self.assertEqual(c["matrix"], [("Matrix / Runout", MATRIX), ("Mould SID Code", "IFPI 94X1"),
                                       ("Label Code", "LC 0123")])


class CatnoHttp:
    """MusicBrainz finds the release only by its catalogue number; Discogs has nothing."""
    def __init__(self):
        self.db = None
        self.rel = mb_release("mb-cover", "Cover Artist", "Cover Album", 2001, [10000, 20000, 30000], "ZZQ-4471")
        self.calls = []

    def get_json(self, url, host):
        self.calls.append((host, url))
        if host == "mb" and "/ws/2/release/?" in url:
            q = up.unquote(url.split("query=")[1].split("&")[0])
            return {"releases": [{"id": "mb-cover", "track-count": 3}] if 'catno:"ZZQ-4471"' in q else []}
        if host == "mb" and "/ws/2/release/mb-cover" in url:
            return self.rel
        if host == "discogs":
            return {"results": []}
        return None


def dg_release(rid, label, matrix):
    return {"id": rid, "title": "First Light", "year": 2010, "country": "Germany",
            "uri": f"https://www.discogs.com/release/{rid}", "artists": [{"name": "Artist Alpha"}],
            "labels": [{"name": label, "catno": "QDISS0201CDD"}], "formats": [{"name": "CD", "qty": "1"}],
            "tracklist": [{"type_": "track", "position": str(i + 1), "title": f"T{i}", "duration": f"0:{s}"}
                          for i, s in enumerate((10, 20, 30))],
            "identifiers": [{"type": "Matrix / Runout", "value": matrix}]}


class TwoPressingsHttp:
    """Discogs lists two pressings with one catalogue number and other matrices; nothing else."""
    def __init__(self):
        self.db = None
        self.rel = {801: dg_release(801, "Nuclear Blast", "QDISS0201CDD-1 15 A00"),
                    802: dg_release(802, "Other Label", "QDISS0277CDD-2 22 B07")}

    def get_json(self, url, host):
        if host == "discogs" and "/database/search" in url:
            return {"results": [{"id": r, "title": "Artist Alpha - First Light", "year": "2010"} for r in self.rel]}
        if host == "discogs" and "/releases/" in url:
            return self.rel.get(int(url.rsplit("/", 1)[1].split("?")[0]))
        return None


class DiscRingsInThePipeline(unittest.TestCase):
    """The disc-ring OCR reaches the scoring through album["ring_text"]: from _process, and
    from the deep investigation when the first reading found nothing."""
    RING = "QDISSO277CDD-2 22 BO7"            # pressing 802's matrix, read on the disc

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.d = make_album(self.tmp, "Artist Alpha", "2010 - First Light")
        (self.d / "disc.png").write_bytes(png_bytes())
        self.rings = []
        self.scored = []
        real = mc.score_candidate

        def score(*a, **kw):
            s = real(*a, **kw)
            self.scored.append(s)
            return s
        self.patches = [mock.patch.object(ma, "ocr_text", lambda paths: "Nuclear Blast  QDISS0201CDD"),
                        mock.patch.object(ma, "ocr_spines", lambda paths: ""),
                        mock.patch.object(ma, "ocr_barcode_digits", lambda paths, *a, **k: ""),
                        mock.patch.object(ma, "ocr_barcodes", lambda paths, *a, **k: set()),
                        mock.patch.object(ma, "ocr_disc_rings", lambda paths, *a, **k: self.rings.pop(0)),
                        mock.patch.object(ma, "ocr_deep", lambda paths, *a, **k: ""),
                        mock.patch.object(ma, "web_release_links", lambda *a, **k: ([], [])),
                        mock.patch.object(ma, "fingerprint_ready", lambda: False),
                        mock.patch.object(mc, "score_candidate", score)]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_it(self, **kw):
        row = {"AlbumArtistFolder": "Artist Alpha", "AlbumFolder": self.d.name, "FullPath": str(self.d),
               "Status": "untagged"}
        args = types.SimpleNamespace(ocr="on", max_images=15, use_mb=False, use_shops=False, **kw)
        return ma.process(row, TwoPressingsHttp(), CFG, ma.load_plugin(str(PLUGIN)), args)

    def against(self, rid):
        return [x for s in self.scored if str(s["cand"]["id"]) == rid for x in s.get("clues_against") or ()]

    def assert_802_by_its_matrix(self, o):
        """Pressing 802 is picked, and by the matrix read on its disc (not merely 801 dropped)."""
        self.assertIsNotNone(o["best"], o.get("why"))
        self.assertEqual(str(o["best"]["id"]), "802", o.get("why"))
        self.assertTrue(any("matrix" in (s.get("clues") or {}) for s in self.scored
                            if str(s["cand"]["id"]) == "802"), [s.get("clues") for s in self.scored])

    def test_the_ring_reading_is_scored_and_stops_the_label_picking(self):
        self.rings = [self.RING, self.RING]
        o = self.run_it(investigate=False)
        self.assertTrue(any(x.startswith("disc ring reads") for x in self.against("801")), self.against("801"))
        self.assertEqual(self.against("802"), [])
        self.assert_802_by_its_matrix(o)

    def test_the_investigation_fills_the_ring_reading(self):
        self.rings = ["", self.RING]                   # nothing at first, the ring read again
        with mock.patch.object(ma, "_needs_investigation", lambda *a: True):
            o = self.run_it(investigate=True)
        self.assertEqual(self.rings, [])
        self.assertIn("investigated", o)
        self.assertTrue(any(x.startswith("disc ring reads") for x in self.against("801")), self.against("801"))
        self.assert_802_by_its_matrix(o)


class EmbeddedCover(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.saved = (ma.ocr_text, ma.ocr_barcodes, ma.fingerprint_ready)
        ma.fingerprint_ready = lambda: False
        self.read = []

        def fake_text(paths):
            self.read.append(list(paths))
            return "COVER ARTIST  Cover Album  ZZQ-4471  0 724384 260521"
        ma.ocr_text = fake_text

    def tearDown(self):
        ma.ocr_text, ma.ocr_barcodes, ma.fingerprint_ready = self.saved
        shutil.rmtree(self.tmp, ignore_errors=True)

    def row(self, d):
        return {"AlbumArtistFolder": d.parent.name, "AlbumFolder": d.name, "FullPath": str(d), "Status": "untagged"}

    def test_the_cover_text_keeps_codes_but_not_long_numbers(self):
        ma.ocr_barcodes = lambda paths: {"12345670", "5013929020161", "5013929020160"}
        got = ma.ocr_cover(png_bytes())
        self.assertIn("ZZQ-4471", got["text"])
        self.assertNotIn("724384", got["text"])            # never a barcode read from the text
        self.assertEqual(got["barcodes"], ["5013929020160"])   # only a whole, valid decode

    def test_an_album_without_scans_has_its_embedded_cover_read(self):
        ma.ocr_barcodes = lambda paths: set()
        d = make_album(self.tmp, "Cover Artist", "2001 - Cover Album", picture=png_bytes())
        o = ma.process(self.row(d), CatnoHttp(), CFG, None,
                       types.SimpleNamespace(ocr="on", max_images=15, use_discogs=False))
        self.assertTrue(o.get("cover_read"))
        self.assertIn("ZZQ-4471", o.get("codes_from_ocr", ""))
        self.assertEqual((o["best"] or {}).get("id"), "mb-cover", o.get("why"))
        self.assertNotIn("barcodes_from_ocr", o)

    def test_a_cover_barcode_counts_only_when_decoded_whole(self):
        ma.ocr_barcodes = lambda paths: {"5013929020160"}
        d = make_album(self.tmp, "Cover Artist", "2001 - Cover Album", picture=png_bytes())
        o = ma.process(self.row(d), CatnoHttp(), CFG, None,
                       types.SimpleNamespace(ocr="on", max_images=15, use_discogs=False))
        self.assertEqual(o.get("barcodes_from_ocr"), "5013929020160")

    def test_the_cover_is_a_weak_clue_never_scored_as_scans(self):
        c = cand(source="mb", catnos={mc.norm_code("ZZQ-4471")}, matrix=())
        s = mc.score_candidate(album(cover_text="ZZQ-4471  Lbl"), c, CFG)
        self.assertIn("cover", s["clues"])
        self.assertEqual(s["code"], "")                       # no code points from the cover
        self.assertEqual(s["confidence"], mc.score_candidate(album(), c, CFG)["confidence"])

    def test_the_cover_alone_never_picks_a_tied_pressing(self):
        """Two pressings tie at 95+; only the embedded cover's catalogue number tells them
        apart. The cover may be art fetched for the release a tagger guessed: review, not auto."""
        a = cand(id="21", catnos={mc.norm_code("ZZQ-4471")}, label="Lbl")
        b = cand(id="22", catnos={mc.norm_code("YYR-9902")}, label="Lbl")
        alb = album(cover_text="Artist Alpha  First Light  ZZQ-4471")
        scored = [mc.score_candidate(alb, c, CFG) for c in (a, b)]
        self.assertIn("cover", scored[0]["clues"])
        self.assertEqual(scored[0]["confidence"], scored[1]["confidence"])
        self.assertGreaterEqual(scored[0]["confidence"], 95)
        pick = mc.choose_pressing(scored)
        self.assertTrue(pick is None or "cover" not in pick[1], pick and pick[1])
        r = mc.classify(scored, CFG)
        self.assertEqual(r["status"], "review", (r["why"], r["best"]["notes"]))
        self.assertFalse(any("front cover" in n and "pressing chosen" in n for n in r["best"]["notes"]))

    def test_the_cover_reading_is_cached_per_language_set(self):
        """Installing a language pack reads the embedded cover again, as it does the scans."""
        import sqlite3
        ma.ocr_barcodes = lambda paths: set()
        d = make_album(self.tmp, "Cover Artist", "2001 - Cover Album", picture=png_bytes())
        http = CatnoHttp()
        http.db = sqlite3.connect(":memory:")
        args = types.SimpleNamespace(ocr="on", max_images=15, use_discogs=False, investigate=False)
        with mock.patch.object(ma, "ocr_langs", return_value="eng"):
            ma.process(self.row(d), http, CFG, None, args)
            ma.process(self.row(d), http, CFG, None, args)
        self.assertEqual(len(self.read), 1)                  # cached for the same languages
        with mock.patch.object(ma, "ocr_langs", return_value="eng+jpn"):
            ma.process(self.row(d), http, CFG, None, args)
        self.assertEqual(len(self.read), 2)                  # a new language set reads it again
        kinds = {k.split(":")[0] for (k,) in http.db.execute("SELECT key FROM ocr_cache")}
        self.assertTrue({"cover", "cover-eng+jpn"} <= kinds, kinds)

    def test_blind_runs_never_read_the_cover(self):
        ma.ocr_barcodes = lambda paths: set()
        d = make_album(self.tmp, "Cover Artist", "2001 - Cover Album", picture=png_bytes())
        o = ma.process(self.row(d), CatnoHttp(), CFG, None,
                       types.SimpleNamespace(ocr="on", max_images=15, use_discogs=False, blind=True))
        self.assertFalse(o.get("cover_read"))
        self.assertEqual(self.read, [])

    def test_scans_win_over_the_cover(self):
        ma.ocr_barcodes = lambda paths: set()
        d = make_album(self.tmp, "Cover Artist", "2001 - Cover Album", picture=png_bytes())
        (d / "back.png").write_bytes(png_bytes())
        covers = []
        with mock.patch.object(ma, "ocr_cover", side_effect=lambda data: covers.append(data) or {}), \
                mock.patch.object(ma, "ocr_spines", lambda paths: ""), \
                mock.patch.object(ma, "ocr_barcode_digits", lambda paths, *a, **k: ""), \
                mock.patch.object(ma, "ocr_disc_rings", lambda paths, *a, **k: ""):
            o = ma.process(self.row(d), CatnoHttp(), CFG, None,
                           types.SimpleNamespace(ocr="on", max_images=15, use_discogs=False, investigate=False))
        self.assertFalse(o.get("cover_read"))
        self.assertEqual(covers, [])
        self.assertTrue(self.read)
        self.assertTrue(all(Path(p).name == "back.png" for paths in self.read for p in paths), self.read)

    def test_auto_with_only_a_pdf_leaves_the_cover_alone(self):
        """auto runs OCR for a PDF booklet, but the embedded cover is read only with ocr: on."""
        ma.ocr_barcodes = lambda paths: set()
        d = make_album(self.tmp, "Cover Artist", "2001 - Cover Album", picture=png_bytes())
        (d / "booklet.pdf").write_bytes(b"%PDF-1.4\n%%EOF\n")
        covers = []
        with mock.patch.object(ma, "ocr_cover", side_effect=lambda data: covers.append(data) or {}):
            o = ma.process(self.row(d), CatnoHttp(), CFG, None,
                           types.SimpleNamespace(ocr="auto", max_images=15, use_discogs=False, investigate=False))
        self.assertFalse(o.get("cover_read"))
        self.assertEqual(covers, [])

    def test_off_and_auto_without_scans_read_nothing(self):
        ma.ocr_barcodes = lambda paths: set()
        d = make_album(self.tmp, "Cover Artist", "2001 - Cover Album", picture=png_bytes())
        for mode in ("off", "auto", False):
            o = ma.process(self.row(d), CatnoHttp(), CFG, None,
                           types.SimpleNamespace(ocr=mode, max_images=15, use_discogs=False))
            self.assertFalse(o.get("cover_read"), mode)
        self.assertEqual(self.read, [])

    def test_no_embedded_cover_degrades_quietly(self):
        d = make_album(self.tmp, "Cover Artist", "2001 - Cover Album")
        self.assertEqual(ma.embedded_cover(ma.read_album(d, d.parent.name, d.name)), (None, ""))


class OcrSetting(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_modes(self):
        self.assertEqual([ma.ocr_mode(v) for v in (True, False, None, "on", "OFF", "auto", "x")],
                         ["on", "off", "off", "on", "off", "auto", "off"])

    def test_auto_reads_folders_with_scans_or_a_pdf(self):
        d = self.tmp / "a"
        d.mkdir()
        (d / "cover.jpg").write_bytes(b"x")
        (d / "R-123456-1388801849-4582.jpg").write_bytes(b"x")     # saved from Discogs
        self.assertFalse(ma.ocr_wanted("auto", d))
        self.assertTrue(ma.ocr_wanted("on", d))
        (d / "Scans").mkdir()
        (d / "Scans" / "back.jpg").write_bytes(b"x")
        self.assertTrue(ma.ocr_wanted("auto", d))
        self.assertFalse(ma.ocr_wanted("off", d))
        e = self.tmp / "b"
        e.mkdir()
        (e / "booklet.pdf").write_bytes(b"%PDF")
        self.assertTrue(ma.ocr_wanted("auto", e))

    def test_profile_default_and_check(self):
        self.assertEqual(pf.default()["ocr"], "auto")
        self.assertEqual(pf.validate({"name": "x", "ocr": "on"}), [])
        self.assertTrue(any("ocr must be" in p for p in pf.validate({"name": "x", "ocr": "sometimes"})))

    def test_engine_follows_the_profile_and_ocr_forces_on(self):
        import engine

        class NoHttp:
            db = None

            def get_json(self, url, host):
                raise AssertionError(url)
        prof = pf.from_dict({"name": "default"})
        with mock.patch.object(ma, "ocr_problems", return_value=[]):
            self._modes(engine, NoHttp, prof)
        # OCR that cannot work is off, whatever was asked: no empty readings are cached
        with mock.patch.object(ma, "ocr_problems", return_value=["tesseract binary missing"]), \
                mock.patch("requirements.problem", return_value=""):   # (the same whether Tesseract is installed or not)
            self.assertEqual(engine.Engine(prof, http=NoHttp()).ocr, "off")
            forced = engine.Engine(prof, http=NoHttp(), ocr=True)
            self.assertEqual(forced.ocr, "off")
            # ... and the user is told why (the CLI prints "note: ocr: ...")
            self.assertIn("tesseract binary missing", forced.notes.get("ocr", ""))
        # OCR that works, or was not asked for, leaves no note
        with mock.patch.object(ma, "ocr_problems", return_value=["tesseract binary missing"]), \
                mock.patch("requirements.problem", return_value=""):   # (the same whether Tesseract is installed or not)
            self.assertNotIn("ocr", engine.Engine(prof, http=NoHttp(), ocr="off").notes)
        with mock.patch.object(ma, "ocr_problems", return_value=[]):
            self.assertNotIn("ocr", engine.Engine(prof, http=NoHttp(), ocr=True).notes)

    def test_missing_tesseract_turns_the_engine_ocr_off(self):
        import engine

        class NoHttp:
            db = None
        with mock.patch("shutil.which", return_value=None):
            self.assertEqual(engine.Engine(pf.from_dict({"name": "default"}), http=NoHttp(), ocr=None).ocr, "off")

    def _modes(self, engine, NoHttp, prof):
        self.assertEqual(engine.Engine(prof, http=NoHttp()).ocr, "auto")
        self.assertEqual(engine.Engine(prof, http=NoHttp(), ocr=True).ocr, "on")
        off = pf.from_dict({"name": "Quiet", "ocr": "off"})
        self.assertEqual(engine.Engine(off, http=NoHttp()).ocr, "off")
        self.assertEqual(engine.Engine(off, http=NoHttp(), ocr=True).ocr, "on")

    def test_validate_reads_the_setting(self):
        import validate
        ns = types.SimpleNamespace
        self.assertEqual(validate.ocr_setting(ns(no_ocr=True, ocr="on")), "off")
        self.assertEqual(validate.ocr_setting(ns(no_ocr=False, ocr="on")), "on")
        self.assertEqual(validate.ocr_setting(ns(no_ocr=False, ocr="off")), "off")
        self.assertEqual(validate.ocr_setting(ns(no_ocr=False, ocr="auto")), "auto")
        # unset: the default profile's setting, read (not the fallback's "auto")
        with mock.patch("profiles.default", return_value={"ocr": "off"}) as default:
            self.assertEqual(validate.ocr_setting(ns(no_ocr=False, ocr=None)), "off")
        default.assert_called()
        with mock.patch("profiles.default", return_value={"ocr": "on"}):
            self.assertEqual(validate.ocr_setting(ns(no_ocr=False, ocr=None)), "on")

    def test_validate_without_tesseract(self):
        """auto notes it and goes on without OCR; on stops; off never checks."""
        import contextlib
        import validate
        with mock.patch.object(ma, "check_ocr_ready", side_effect=SystemExit("no tesseract")) as check:
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                self.assertEqual(validate.ocr_ready("auto"), "off")
            self.assertIn("scans are not read", err.getvalue())
            self.assertIn("no tesseract", err.getvalue())
            with self.assertRaises(SystemExit):
                validate.ocr_ready("on")
            check.reset_mock()
            self.assertEqual(validate.ocr_ready("off"), "off")
            check.assert_not_called()
        with mock.patch.object(ma, "check_ocr_ready", return_value=None):
            self.assertEqual(validate.ocr_ready("auto"), "auto")
            self.assertEqual(validate.ocr_ready("on"), "on")


if __name__ == "__main__":
    unittest.main(argv=[sys.argv[0]], verbosity=2)
