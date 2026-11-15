"""0.8.66: a hub close-up read band by band (OCR-7), and a matrix on a disc ring read with an
OCR slip in its letters (OCR-8). Synthetic data only."""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import match_albums as ma  # noqa: E402
import match_core as mc  # noqa: E402


def _font(px):
    from PIL import ImageFont
    try:
        return ImageFont.truetype("DejaVuSans.ttf", px)
    except OSError:
        return ImageFont.load_default()


def hub_closeup(text, size=700, centre=(380, 330), mirrored=False, light=False, boxes=False):
    """A close-up of a disc's hub that runs off the frame (its centre is not the frame's):
    a white clamping area, a dark ring, a band holding TEXT round it, a thin line, grey
    beyond. MIRRORED: photographed through the disc. LIGHT: light print on a dark band,
    with bright blocks beside it when BOXES."""
    import numpy as np
    from PIL import Image, ImageDraw
    cx, cy = centre
    r0, r1 = 225.0, 290.0                                   # the text band
    n, rows = int(2 * np.pi * r1), int(r1 - r0)
    ink, paper = (215, 85) if light else (20, 190)
    flat = Image.new("L", (n, rows), paper)
    d = ImageDraw.Draw(flat)
    d.text((n // 10, int(rows * 0.18)), text, fill=ink, font=_font(int(rows * 0.6)))
    if boxes:
        for x in (n // 10 - 120, n // 10 - 60):
            d.rectangle((x, int(rows * 0.25), x + 40, int(rows * 0.75)), fill=250)
    if mirrored:
        flat = flat.transpose(Image.FLIP_LEFT_RIGHT)
    fa = np.asarray(flat)
    yy, xx = np.mgrid[0:size, 0:size]
    rad = np.hypot(xx - cx, yy - cy)
    th = np.mod(np.arctan2(yy - cy, xx - cx), 2 * np.pi)
    img = np.full((size, size), 150, dtype=np.uint8)
    img[rad < 294] = 40                                      # the thin line outside the band
    band = (rad >= r0) & (rad < r1)
    col = np.clip((th / (2 * np.pi) * n).astype(int), 0, n - 1)
    row = np.clip(((r1 - rad) / (r1 - r0) * rows).astype(int), 0, rows - 1)
    img[band] = fa[row[band], col[band]]
    img[(rad >= 200) & (rad < r0)] = 30                      # the dark ring
    img[rad < 200] = 250                                     # the clamping area and hole
    return Image.fromarray(img)


def read_rings(im):
    d = tempfile.mkdtemp()
    try:
        p = os.path.join(d, "matrix.png")
        im.save(p)
        return ma.ocr_disc_rings([p])
    finally:
        shutil.rmtree(d)


def _can_read():
    """A real tesseract with English, and the font the close-ups are drawn in."""
    if not shutil.which("tesseract"):
        return False
    try:
        from PIL import ImageFont
        ImageFont.truetype("DejaVuSans.ttf", 20)
        langs = subprocess.run(["tesseract", "--list-langs"], capture_output=True, text=True,
                               timeout=30).stdout.split()
    except Exception:
        return False
    return "eng" in langs


CAN_READ = _can_read()
needs_tesseract = unittest.skipUnless(CAN_READ, "tesseract with eng, or the DejaVu Sans font, missing")


class OCR7HubCloseUp(unittest.TestCase):
    @needs_tesseract
    def test_OCR7_mirrored_maker_line_round_an_off_centre_hub_is_read(self):
        text = read_rings(hub_closeup("MASTERED BY TESTLAB", mirrored=True))
        self.assertIn("MASTERED", text.upper(), text)
        self.assertIn("TESTLAB", text.upper().replace(" ", ""), text)

    @needs_tesseract
    def test_OCR7_light_print_on_a_dark_ring_beside_bright_blocks(self):
        text = read_rings(hub_closeup("U.S. OPTICAL DISC", light=True, boxes=True))
        self.assertIn("OPTICAL", text.upper(), text)

    @needs_tesseract
    def test_OCR7_a_small_close_up_is_read_too(self):
        # (under the 800 px the whole-ring strips need: Heatwave's matrix photo is 555 px)
        im = hub_closeup("RUNE 9 CD 02", size=700).resize((520, 520))
        text = read_rings(im)
        self.assertIn("RUNE", text.upper(), text)

    def test_OCR7_the_centre_is_fitted_to_the_rings(self):
        im = hub_closeup("X", size=700, centre=(380, 330)).convert("L")
        cx, cy, r = ma.find_disc(im)
        fx, fy, misfit = ma.hub_centre(im, cx, cy, r)
        self.assertLess(abs(fx - 380), 4)
        self.assertLess(abs(fy - 330), 4)
        self.assertLess(misfit, ma.HUB_FIT)

    def test_OCR7_a_cover_is_no_close_up_and_is_not_read_band_by_band(self):
        import numpy as np
        from PIL import Image
        rng = np.random.default_rng(7)
        blocks = rng.integers(0, 200, size=(14, 14)).astype("uint8")
        cover = Image.fromarray(blocks).resize((700, 700), Image.NEAREST)
        calls = []
        orig = ma._tess_or_tsv
        ma._tess_or_tsv = lambda *a, **k: calls.append(1) or ""
        try:
            found = ma.find_disc(cover)
            out = ma.ocr_hub_closeup(cover, found) if found else ""
        finally:
            ma._tess_or_tsv = orig
        self.assertEqual(out, "")
        self.assertEqual(calls, [])

    @needs_tesseract
    def test_OCR7_each_run_is_kept_the_way_it_reads_surest(self):
        from PIL import Image, ImageDraw
        im = Image.new("L", (520, 70), 255)
        ImageDraw.Draw(im).text((20, 12), "SONOPRESS 8172", fill=0, font=_font(40))
        got = ma._read_stacked([im.transpose(Image.FLIP_LEFT_RIGHT)])
        self.assertEqual(len(got), 1, got)
        self.assertIn("SONOPRESS", got[0][0].upper())

    def test_OCR7_pages_stay_under_what_tesseract_reads(self):
        from PIL import Image
        heights = []
        orig = ma._tess_or_tsv
        ma._tess_or_tsv = lambda img, deadline=None: heights.append(img.height) or ""
        try:
            ma._read_stacked([Image.new("L", (300, 100), 255)] * 90)
        finally:
            ma._tess_or_tsv = orig
        self.assertGreater(len(heights), 1)
        self.assertTrue(all(h <= 12000 + 40 for h in heights), heights)


def _cand(*matrices, catnos=("CDCCR 3",)):
    return {"matrix": [("Matrix / Runout", m) for m in matrices], "catnos": list(catnos), "barcodes": []}


class OCR8MatrixReadWithASlip(unittest.TestCase):
    def test_OCR8_marks_part_words(self):
        self.assertEqual(mc.matrix_tokens("CDCCR3 : ‧MASTERED BY MAYKING‧"),
                         ["cdccr3", "mastered", "by", "mayk1ng"])
        self.assertEqual(mc.matrix_tokens("RUNE 9 CD <02>"), ["rune", "9", "cd", "02"])
        self.assertEqual(mc.matrix_tokens("MASTERED.” BY"), ["mastered", "by"])

    def test_OCR8_a_slip_in_the_letters_on_the_ring_counts(self):
        cand = _cand("CDCCR3 : ‧MASTERED BY MAYKING‧")
        agree, _ = mc.matrix_clues("", cand, ring_text="_MASTERED. BY MAYVEING")
        self.assertIn("matrix", agree)

    def test_OCR8_only_on_the_disc_ring(self):
        # a sleeve's page text is held to the exact reading
        cand = _cand("CDCCR3 : ‧MASTERED BY MAYKING‧")
        agree, _ = mc.matrix_clues("_MASTERED. BY MAYVEING", cand, ring_text="")
        self.assertNotIn("matrix", agree)

    def test_OCR8_digits_are_read_exactly(self):
        for value, ring in (("RUNE9CD 01@ B", "RUNE 9 CD <02> B"),
                            ("A0102309767-0102 13 A00", "A0102309768-0102 13 A00")):
            agree, _ = mc.matrix_clues("", _cand(value, catnos=("Rune 9 CD",)), ring_text=ring)
            self.assertNotIn("matrix", agree, (value, ring))
        # (the same digits with a slip in a word do count)
        agree, _ = mc.matrix_clues("", _cand("SONY DADC A0102309767-0102", catnos=("X 1",)),
                                   ring_text="SONY DAOC A0102309767-0102")
        self.assertIn("matrix", agree)

    def test_OCR8_one_slip_for_every_seven_letters(self):
        letters = [True] * 7
        self.assertTrue(mc._matrix_near("us0pt1cad1sc", "0pt1cal", letters))     # OPTICA
        self.assertFalse(mc._matrix_near("us0f1t1cald1sc", "0pt1cal", letters))  # OFLTICAL: two
        self.assertFalse(mc._matrix_near("pr0ss", "press", [True] * 5))          # under 7 letters

    def test_OCR8_another_maker_is_not_a_slip(self):
        agree, _ = mc.matrix_clues("", _cand("MADE IN GERMANY BY PMDC", catnos=("X 1",)),
                                   ring_text="MADE IN FRANCE BY PMDC")
        self.assertNotIn("matrix", agree)

    def test_OCR8_the_catalogue_number_alone_is_still_nothing(self):
        # "RUNE 9 CD <02>" is the catalogue number and a disc number: Heatwave's three
        # pressings all carry the one, so it tells none of them apart
        agree, _ = mc.matrix_clues("", _cand("RUNE 9 CD <02> U.S. OPTICAL DISC", catnos=("Rune 9 CD",)),
                                   ring_text="RUNE 9 CD <02>")
        self.assertNotIn("matrix", agree)


CFG = dict(mc.DEFAULT_CFG, preferred_countries=[])
LENS = (200.0, 305.0, 410.0)


def album(**kw):
    a = {"artist": "Artist Alpha", "title": "First Light", "year": 1987, "discs": 1,
         "tracks": [{"disc": 1, "num": i + 1, "title": f"T{i}", "secs": s} for i, s in enumerate(LENS)],
         "barcodes": set(), "catnos": set()}
    a.update(kw)
    return a


def pressing(rid, matrix=(), lens=LENS, catno="CDXYZ 3", barcodes=(), country="UK", notes_made_in=()):
    return {"source": "discogs", "id": rid, "url": "u", "artist": "Artist Alpha", "title": "First Light",
            "year": 1987, "country": country, "label": "Lbl", "catnos": {mc.norm_code(catno)},
            "catno_shown": catno, "barcodes": set(barcodes), "disc_count": 1,
            "matrix": [("Matrix / Runout", m) for m in matrix], "notes_made_in": list(notes_made_in),
            "tracks": [{"disc": 1, "title": f"T{i}", "secs": s} for i, s in enumerate(lens)]}


SLEEVE = (186.0, 289.0, 398.0)            # times printed on a tray: up to 16 s off the audio
RING = "MASTERED. BY MAYVEING"            # the disc ring read with a slip


def classify(alb, *cands, text=""):
    scored = [mc.score_candidate(alb, c, CFG, ocr_text=text or alb.get("ring_text") or "") for c in cands]
    return mc.classify(scored, CFG), scored


class EVI9MatrixNamesThePressing(unittest.TestCase):
    def test_EVI9_the_matrix_match_beats_a_sibling_with_better_lengths(self):
        mayking = pressing("25", matrix=["CDXYZ3 : ‧MASTERED BY MAYKING‧"], lens=SLEEVE)
        sono = pressing("38", matrix=["SONOPRESS B-8172 / CDXYZ 3 A"], country="Europe")
        r, scored = classify(album(ring_text=RING), mayking, sono)
        self.assertGreater(scored[1]["confidence"], scored[0]["confidence"])     # lengths favour it
        self.assertEqual(r["best"]["cand"]["id"], "25")
        self.assertTrue(any(str(x).startswith(mc.MATRIX_AGAINST) for x in scored[1]["clues_against"]))

    def test_EVI9_your_barcode_on_the_sibling_keeps_it(self):
        mayking = pressing("25", matrix=["CDXYZ3 : ‧MASTERED BY MAYKING‧"], lens=SLEEVE)
        sono = pressing("38", matrix=["SONOPRESS B-8172 / CDXYZ 3 A"], barcodes=["5013929020160"])
        alb = album(ring_text=RING, barcodes={"5013929020160"})
        r, _ = classify(alb, mayking, sono)
        self.assertEqual(r["best"]["cand"]["id"], "38")

    def test_EVI9_a_sibling_listing_no_matrix_stays(self):
        mayking = pressing("25", matrix=["CDXYZ3 : ‧MASTERED BY MAYKING‧"], lens=SLEEVE)
        bare = pressing("38")
        _, scored = classify(album(ring_text=RING), mayking, bare)
        self.assertFalse(any(str(x).startswith(mc.MATRIX_AGAINST) for x in scored[1].get("clues_against") or ()))
        left, dropped = mc.matrix_drop(scored, CFG)
        self.assertEqual(dropped, [])

    def test_EVI9_another_number_is_another_album_part(self):
        mayking = pressing("25", matrix=["CDXYZ3 : ‧MASTERED BY MAYKING‧"], lens=SLEEVE)
        other = pressing("38", matrix=["SONOPRESS B-8172 / CDQQ 9 A"], catno="CDQQ 9")
        _, scored = classify(album(ring_text=RING), mayking, other)
        self.assertEqual(mc.matrix_drop(scored, CFG)[1], [])

    def test_EVI9_two_matrices_read_leave_nothing_out(self):
        a = pressing("25", matrix=["CDXYZ3 : ‧MASTERED BY MAYKING‧"], lens=SLEEVE)
        b = pressing("38", matrix=["SONOPRESS B-8172 / CDXYZ 3 A"])
        c = pressing("39", matrix=["MADE IN UK BY UNIVERSAL M&L"])
        alb = album(ring_text=RING + "\nSONOPRESS B-8172")
        _, scored = classify(alb, a, b, c)
        self.assertEqual(mc.matrix_drop(scored, CFG)[1], [])

    def test_EVI9_nothing_left_out_for_a_match_with_your_copy_against_it(self):
        mayking = pressing("25", matrix=["CDXYZ3 : ‧MASTERED BY MAYKING‧"], lens=SLEEVE, country="Japan")
        sono = pressing("38", matrix=["SONOPRESS B-8172 / CDXYZ 3 A"], country="Germany")
        alb = album(ring_text=RING)
        _, scored = classify(alb, mayking, sono, text=RING + "\nMade in Germany")
        self.assertTrue(scored[0]["clues_against"])
        self.assertEqual(mc.matrix_drop(scored, CFG)[1], [])


class EVI9Guards(unittest.TestCase):
    """What the review of EVI-9 found: a matrix read must name the pressing, not a plant or a
    country every pressing of the plant shares."""

    def test_EVI9_the_ring_naming_the_siblings_plant_keeps_it(self):
        a = pressing("25", matrix=["MADE IN W. GERMANY BY PDO 1234 A1"], country="Germany")
        b = pressing("38", matrix=["PMDC 98765 01"], country="Germany")
        _, scored = classify(album(ring_text="MADE IN GERMANY BY PMDC"), a, b)
        self.assertEqual(mc.matrix_drop(scored, CFG)[1], [])

    def test_OCR8_a_generic_made_in_line_names_no_pressing(self):
        a = pressing("25", matrix=["MADE IN W. GERMANY BY PDO 1234 A1"], country="Germany")
        for ring, other in (("MADE IN GERMANY BY PMDC", "PMDC 98765 01"), ("MADE IN GERMANY", "98765 01 A")):
            b = pressing("38", matrix=[other], country="Germany")
            r, scored = classify(album(ring_text=ring), a, b)
            self.assertNotIn("matrix", scored[0]["clues"], ring)
            self.assertEqual(mc.matrix_drop(scored, CFG)[1], [], ring)
            # (the ring changes nothing: as if no disc were read)
            plain, _ = classify(album(), a, b)
            self.assertEqual((r["status"], r["best"]["cand"]["id"]),
                             (plain["status"], plain["best"]["cand"]["id"]), ring)

    def test_OCR8_a_generic_line_and_a_side_number_name_no_pressing(self):
        a = pressing("25", matrix=["MADE IN W. GERMANY 1"], country="Germany")
        b = pressing("38", matrix=["98765 01 A"], country="Germany")
        _, scored = classify(album(ring_text="MADE IN GERMANY 1"), a, b)
        self.assertNotIn("matrix", scored[0]["clues"])
        self.assertEqual(mc.matrix_drop(scored, CFG)[1], [])

    def test_EVI9_a_three_letter_plant_on_the_ring_keeps_the_sibling(self):
        a = pressing("25", matrix=["MASTERED BY MAYKING 4471"], lens=SLEEVE)
        b = pressing("38", matrix=["EMI 98765 01"])
        _, scored = classify(album(ring_text="MASTERED BY MAYKING 4471 EMI"), a, b)
        self.assertIn("matrix", scored[0]["clues"])
        self.assertEqual(mc.matrix_drop(scored, CFG)[1], [])

    def test_EVI9_a_plant_both_pressings_share_tells_neither_apart(self):
        a = pressing("25", matrix=["MADE IN GERMANY BY PDO"])
        b = pressing("38", matrix=["PDO 98765 01 A"])
        r, scored = classify(album(ring_text="MADE IN GERMANY BY PDO"), a, b)
        self.assertNotIn("matrix", scored[0]["clues"])
        self.assertEqual(mc.matrix_drop(scored, CFG)[1], [])
        self.assertTrue(scored[1]["matrix_words_seen"])

    def test_EVI9_a_sleeves_plant_line_is_not_the_disc(self):
        a = pressing("25", matrix=["MADE IN GERMANY BY PDO"], lens=SLEEVE)
        b = pressing("38", matrix=["PDO 12345 01"])
        r, scored = classify(album(), a, b, text="Made in Germany by PDO")
        # (a plant and country line every PDO disc carries names no pressing at all now: OCR-8)
        self.assertNotIn("matrix", scored[0]["clues"])
        self.assertEqual(mc.matrix_drop(scored, CFG)[1], [])
        self.assertEqual(r["best"]["cand"]["id"], "38")

    def test_EVI9_a_matrix_that_is_only_the_number_is_none(self):
        a = pressing("25", matrix=["CDXYZ3 : ‧MASTERED BY MAYKING‧"], lens=SLEEVE)
        b = pressing("38", matrix=["CDXYZ 3"])
        self.assertFalse(mc.lists_own_matrix(b))
        _, scored = classify(album(ring_text=RING), a, b)
        self.assertEqual(mc.matrix_drop(scored, CFG)[1], [])

    def test_EVI9_your_disc_id_on_the_sibling_keeps_it(self):
        a = pressing("25", matrix=["CDXYZ3 : ‧MASTERED BY MAYKING‧"], lens=SLEEVE)
        b = pressing("38", matrix=["SONOPRESS B-8172 / CDXYZ 3 A"])
        _, scored = classify(album(ring_text=RING), a, b)
        scored[1]["clues"]["disc TOC"] = mc.EXACT_TOC_CLUE
        self.assertEqual(mc.matrix_drop(scored, CFG)[1], [])

    def test_EVI9_the_ring_reading_is_told_as_the_disc_rings(self):
        a = pressing("25", matrix=["CDXYZ3 : ‧MASTERED BY MAYKING‧"], lens=SLEEVE)
        _, scored = classify(album(ring_text=RING), a)
        self.assertTrue(scored[0]["clues"]["matrix"].endswith(mc.ON_THE_RING))
        self.assertTrue(any("agrees (read on the disc ring)" in n for n in scored[0]["notes"]), scored[0]["notes"])


class CTY2NotesNameWhereItsPartsWereMade(unittest.TestCase):
    def test_CTY2_a_booklet_made_where_its_notes_say_is_nothing_against_it(self):
        c = pressing("25", country="UK", notes_made_in=["DE", "GB"])
        _, against = mc.pressing_clues("Made in West Germany", c)
        self.assertEqual([x for x in against if x.startswith("scans say made in")], [])

    def test_CTY2_without_such_notes_it_stands(self):
        c = pressing("25", country="UK")
        _, against = mc.pressing_clues("Made in West Germany", c)
        self.assertTrue(any(x.startswith("scans say made in DE") for x in against), against)

    def test_CTY2_notes_naming_another_country_change_nothing(self):
        c = pressing("25", country="UK", notes_made_in=["FR"])
        _, against = mc.pressing_clues("Made in West Germany", c)
        self.assertTrue(any(x.startswith("scans say made in DE") for x in against), against)

    def test_CTY2_the_notes_are_read_for_made_in(self):
        self.assertEqual(mc.countries_made_in("Made in West Germany (Booklet)\nMade in United Kingdom (CD Face)"),
                         {"DE", "GB"})


class CAT6PrintedNumbersAreSearched(unittest.TestCase):
    def res(self, *shown):
        return {"scored": [{"cand": {"source": "discogs", "id": str(i), "catno_shown": s}}
                           for i, s in enumerate(shown)]}

    def test_CAT6_a_listed_number_your_scans_print_is_searched(self):
        alb = album(printed_text="Big Beat Records\nCDXYZ 3\nMade in West Germany")
        self.assertEqual(ma.printed_catnos(self.res("CDXYZ 3", "FCD-CCR3-2"), alb, ""), ["CDXYZ 3"])

    def test_CAT6_not_inside_a_longer_number_nor_a_short_one(self):
        alb = album(printed_text="CDXYZ 31\nCD 3")
        self.assertEqual(ma.printed_catnos(self.res("CDXYZ 3", "CD 3"), alb, ""), [])

    def test_CAT6_the_step_asks_only_what_no_search_asked(self):
        asked = []
        orig_search, orig_again, orig_combine = ma.discogs_search, ma._scored_again, ma._combine
        ma.discogs_search = lambda http, probe, cfg, dm: asked.append(list(probe["catno_search"])) or []
        ma._scored_again = lambda res, extra, name, album, cfg, text="": res
        ma._combine = lambda mb, dg, countries=None, prefs=None: {}
        try:
            res = self.res("CDXYZ 3")
            alb = album(printed_text="CDXYZ 3")
            ma.printed_catno_step(None, alb, CFG, None, None, res, {}, "CDXYZ 3", True, [])
            ma.printed_catno_step(None, dict(alb, catno_search=["CDXYZ 3"]), CFG, None, None, res, {},
                                  "CDXYZ 3", True, [])
            ma.printed_catno_step(None, dict(alb, scan_catnos_searched=["CDXYZ3"]), CFG, None, None, res, {},
                                  "CDXYZ 3", True, [])
        finally:
            ma.discogs_search, ma._scored_again, ma._combine = orig_search, orig_again, orig_combine
        self.assertEqual(asked, [["CDXYZ 3"]])

    def test_CAT6_not_when_a_decoded_barcode_settled_it_and_what_it_asked_is_kept(self):
        asked = []
        orig_search, orig_again, orig_combine = ma.discogs_search, ma._scored_again, ma._combine
        ma.discogs_search = lambda http, probe, cfg, dm: asked.append(list(probe["catno_search"])) or []
        ma._scored_again = lambda res, extra, name, album, cfg, text="": res
        ma._combine = lambda mb, dg, countries=None, prefs=None: {}
        try:
            res = self.res("CDXYZ 3")
            settled = {"status": "auto", "best": {"cand": {"barcodes": {"5013929020160"}}}}
            alb = album(printed_text="CDXYZ 3", ocr_barcodes={"5013929020160"})
            ma.printed_catno_step(None, alb, CFG, None, None, res, settled, "CDXYZ 3", True, [])
            self.assertEqual(asked, [])
            alb = album(printed_text="CDXYZ 3")
            ma.printed_catno_step(None, alb, CFG, None, None, res, {}, "CDXYZ 3", True, [])
            self.assertEqual(alb["scan_catnos_searched"], ["CDXYZ 3"])
        finally:
            ma.discogs_search, ma._scored_again, ma._combine = orig_search, orig_again, orig_combine

    def test_CAT6_a_failed_search_is_noted_and_changes_nothing(self):
        orig = ma.discogs_search
        def boom(*a, **k):
            raise RuntimeError("timeout")
        ma.discogs_search = boom
        notes = []
        try:
            res = self.res("CDXYZ 3")
            out = ma.printed_catno_step(None, album(printed_text="CDXYZ 3"), CFG, None, "mb", res, "fin",
                                        "CDXYZ 3", True, notes)
        finally:
            ma.discogs_search = orig
        self.assertEqual(out, ("mb", res, "fin"))
        self.assertTrue(notes and "failed" in notes[0])


if __name__ == "__main__":
    unittest.main()
