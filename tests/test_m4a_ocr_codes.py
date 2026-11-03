"""Offline tests for the M4a "OCR missed small printed codes" rule of the other-pressing audit:
barcodes and codes on seven scan sets were not read.

- A barcode printed sideways along a tray card's edge (BTBAM - The Great Misdirect) is found on
  the scan turned a quarter, and decoded from its bars cut out alone.
- A barcode in a printed frame (Candlemass's digipak) is decoded from the bars alone in a white
  margin; a big barcode found in pieces (Lamb of God - Omens) is joined into one patch.
- Single panels (back, obi, sticker, disc) are read before booklet spreads (Alcest - Les Voyages
  De L'âme: the back was the 29th of 31 scans; only 15 are read).
- The digits under a sideways barcode are read when no decoder reads its bars.
- The rim of a disc is read flat, as the hub is (Omens: "NB 6570-2" along the edge).
- The investigation decodes the bars on every scan, not only the first ones.
- Only barcodes with a valid check digit count.

Every image is drawn here (PIL); no file of the library is read.
Run:  python3 tests/test_m4a_ocr_codes.py [plugin-dir]
"""
import os as _os, tempfile as _tempfile  # noqa: E402
_os.environ["XDG_CONFIG_HOME"] = _tempfile.mkdtemp(prefix="tm-config-")
_os.environ["XDG_STATE_HOME"] = _tempfile.mkdtemp(prefix="tm-state-")

import random
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
if len(sys.argv) > 1 and not sys.argv[1].startswith("-"):
    sys.argv.pop(1)                                   # the plugin folder: not needed here

import match_core as mc  # noqa: E402
import match_albums as ma  # noqa: E402

try:
    import numpy  # noqa: F401
    from PIL import Image, ImageDraw, ImageFont
    HAVE_PIL = True
except ImportError:
    HAVE_PIL = False
try:
    from pyzbar.pyzbar import decode, ZBarSymbol  # noqa: F401
    HAVE_ZBAR = True
except Exception:
    HAVE_ZBAR = False
HAVE_TESSERACT = bool(shutil.which("tesseract"))

_L = ["0001101", "0011001", "0010011", "0111101", "0100011", "0110001", "0101111", "0111011", "0110111", "0001011"]
_G = ["0100111", "0110011", "0011011", "0100001", "0011101", "0111001", "0000101", "0010001", "0001001", "0010111"]
_R = ["1110010", "1100110", "1101100", "1000010", "1011100", "1001110", "1010000", "1000100", "1001000", "1110100"]
_PARITY = ["LLLLLL", "LLGLGG", "LLGGLG", "LLGGGL", "LGLLGG", "LGGLLG", "LGGGLL", "LGLGLG", "LGLGGL", "LGGLGL"]


def ean_bits(code):
    d = [int(c) for c in code]
    return ("101" + "".join((_L if p == "L" else _G)[x] for p, x in zip(_PARITY[d[0]], d[1:7])) + "01010"
            + "".join(_R[x] for x in d[7:]) + "101")


def font(size):
    for f in ("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
              "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"):
        if Path(f).exists():
            return ImageFont.truetype(f, size)
    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        return None


def ean_label(code, module=3, height=150, digits=True):
    """A barcode label as printed: bars, the digits under them, a white quiet zone."""
    bits = ean_bits(code)
    w = (len(bits) + 22) * module
    label = Image.new("L", (w, height + (module * 14 if digits else 0) + 2 * module), 255)
    d = ImageDraw.Draw(label)
    x = 11 * module
    for b in bits:
        if b == "1":
            d.rectangle((x, module, x + module - 1, module + height), fill=0)
        x += module
    if digits and font(module * 11):
        d.text((2 * module, height + 2 * module),
               f"{code[0]}  {code[1:7]}  {code[7:]}", font=font(module * 11), fill=0)
    return label


def busy(size, seed=1):
    """A busy picture (a halftoned photograph): random dark and light dots."""
    rnd = random.Random(seed)
    im = Image.new("L", size, 140)
    d = ImageDraw.Draw(im)
    for _ in range(size[0] * size[1] // 40):
        x, y = rnd.randrange(size[0]), rnd.randrange(size[1])
        d.ellipse((x, y, x + 3, y + 3), fill=rnd.choice((20, 60, 220, 250)))
    return im


@unittest.skipUnless(HAVE_PIL, "Pillow and numpy needed")
class Barcodes(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    @unittest.skipUnless(HAVE_ZBAR, "pyzbar not installed")
    def test_a_sideways_barcode_on_a_busy_tray_card_is_decoded(self):
        """BTBAM - The Great Misdirect (Russian CD): 4601250367880 runs up the left edge of
        the back, on a halftoned photograph."""
        back = busy((2300, 1800))
        label = ean_label("4601250367880", module=3, height=160)
        back.paste(label.rotate(90, expand=True), (60, 1100))
        back.save(self.tmp / "10.jpg", quality=90)
        im = Image.open(self.tmp / "10.jpg")
        patches = ma.barcode_patches(im)
        self.assertTrue(any(turned for _g, _b, turned in patches))
        # it is the turned patch, its bars cut out alone, that reads: no upright patch does
        symbols = [ZBarSymbol.EAN13]

        def read(turned_ones):
            return set().union(*[ma._decoded_barcodes(decode, c, symbols)
                                 for g, b, t in patches if t == turned_ones for c in ma._bar_crops(g, b)])
        self.assertEqual(read(True), {"4601250367880"})
        self.assertEqual(read(False), set())
        # whole-page passes get no time: the bar patches alone must read it
        self.assertEqual(ma.ocr_barcodes([self.tmp / "10.jpg"], per_image_s=0.01, total_s=0.01),
                         {"4601250367880"})

    @unittest.skipUnless(HAVE_ZBAR, "pyzbar not installed")
    def test_a_barcode_in_a_printed_frame_is_decoded_from_its_bars(self):
        """Candlemass (Metal Mind digipak): a thin frame round the barcode, close to the bars."""
        page = Image.new("L", (3000, 1400), 250)
        label = ean_label("5907785038684", module=5, height=220)
        page.paste(label, (200, 900))
        d = ImageDraw.Draw(page)
        d.rectangle((200 + 6 * 5, 895, 200 + label.width - 6 * 5, 900 + label.height + 4), outline=0, width=4)
        d.text((900, 950), "Released under licence from: Nuclear Blast GmbH  Made in EU", fill=0)
        page.save(self.tmp / "digi0000.jpg", quality=92)
        self.assertEqual(ma.ocr_barcodes([self.tmp / "digi0000.jpg"], per_image_s=0.01, total_s=0.01),
                         {"5907785038684"})

    def test_a_big_barcode_found_in_pieces_is_one_patch(self):
        """Lamb of God - Omens: 13-pixel bars; its wide black bars have no edges inside, so the
        patch came out as two or three pieces, none of them a whole code."""
        page = Image.new("L", (7000, 5400), 235)
        label = ean_label("4065629657024", module=13, height=400, digits=False)
        page.paste(label, (500, 4800))
        boxes = ma.barcode_regions(page)
        self.assertTrue(boxes)
        x0, _y0, x1, _y1 = boxes[0]
        bars = (500 + 11 * 13, 500 + (11 + 95) * 13)
        self.assertLess(x0, bars[0] + 3 * 13)
        self.assertGreater(x1, bars[1] - 3 * 13)

    @unittest.skipUnless(HAVE_ZBAR, "pyzbar not installed")
    def test_a_big_barcode_found_in_pieces_is_decoded(self):
        """Omens end to end: the joined patch is decoded, the whole-page passes given no time.
        Walking out to the quiet zone (_bars_span) rescues a piece as well, so only with
        both taken away is nothing read; the join alone reads it."""
        page = Image.new("L", (7000, 5400), 235)
        page.paste(ean_label("4065629657024", module=13, height=400, digits=False), (500, 4800))
        p = self.tmp / "Scan-210513-0004.png"
        page.save(p)

        def read():
            return ma.ocr_barcodes([p], per_image_s=0.01, total_s=0.01)
        self.assertEqual(read(), {"4065629657024"})
        # the patches' bars cut out alone, nothing else (the first whole-page read would
        # find so clean a code on its own)
        with mock.patch.object(ma, "_barcode_variants", lambda im: iter(())), \
                mock.patch.object(ma, "_bars_span", lambda g, b: None):
            self.assertEqual(read(), {"4065629657024"})                      # the join alone
            with mock.patch.object(ma, "_merge_bar_rows", lambda found: list(found)):
                self.assertEqual(read(), set())                              # pieces alone: none

    def test_bars_span_walks_past_half_a_code_and_stops_at_a_frame(self):
        g = Image.new("L", (1400, 400), 255)
        label = ean_label("5907785038684", module=4, height=200, digits=False)
        g.paste(label, (300, 100))
        d = ImageDraw.Draw(g)
        start, end = 300 + 11 * 4, 300 + (11 + 95) * 4
        d.rectangle((start - 9 * 4, 90, end + 9 * 4, 310), outline=0, width=3)  # the frame
        half = (start, 140, start + 40 * 4, 260)                               # left half found
        left, right, module, inner_left, inner_right = ma._bars_span(g, half)
        self.assertLessEqual(abs(left - start), 2)
        self.assertLessEqual(abs(right - end), 2)
        self.assertGreaterEqual(module, 3)
        self.assertEqual((inner_left, inner_right), (left, right))            # frame is well clear
        # a frame only 5 bar widths away is walked into, and taken off again
        g.paste(label, (300, 100))
        d.rectangle((start - 9 * 4, 90, end + 9 * 4, 310), outline=255, width=3)
        d.rectangle((start - 5 * 4, 90, end + 5 * 4, 310), outline=0, width=3)
        left, right, module, inner_left, inner_right = ma._bars_span(g, half)
        self.assertLessEqual(abs(inner_left - start), 2)
        self.assertLessEqual(abs(inner_right - end), 2)

    @unittest.skipUnless(HAVE_ZBAR, "pyzbar not installed")
    def test_a_bad_check_digit_is_never_a_barcode(self):
        """Only valid full barcodes count as read: bars drawn for a code whose check
        digit is wrong give nothing."""
        page = Image.new("L", (1600, 900), 250)
        page.paste(ean_label("4601250367881", module=3, height=160), (200, 300))
        page.save(self.tmp / "back.png")
        self.assertEqual(ma.ocr_barcodes([self.tmp / "back.png"], per_image_s=0.5, total_s=1.0), set())

    @unittest.skipUnless(HAVE_ZBAR, "pyzbar not installed")
    def test_the_obi_after_booklet_pages_is_read_within_the_time_box(self):
        """Alcest - Les Chants De L'aurore: the obi (tall and narrow, its barcode under a
        Japanese line) was the last of ten scans; booklet pages used the time box first."""
        paths = []
        for n in range(6):
            p = self.tmp / f"img00{n}.png"
            busy((2800, 1400), seed=n).save(p)
            paths.append(p)
        obi = Image.new("L", (820, 1400), 90)
        label = ean_label("4571614700151", module=2, height=80)
        obi.paste(label, (40, 1000))
        p = self.tmp / "img010.png"
        obi.save(p)
        paths.append(p)
        # the obi's bars are found but their crops read nothing (as on the real obi, under a
        # Japanese line): only the whole-page pass reads it, and its time box (the clock
        # past it after one page) reaches the obi only when scans with bars come first
        clock = [0.0]

        def patches(im, *a):
            return [(im.convert("L"), (10, 10, 60, 60), False)] if im.size == (820, 1400) else []

        def page_codes(decode, image, symbols):
            clock[0] += 100.0
            return real(decode, image, symbols)
        real = ma._decoded_barcodes
        with mock.patch.object(ma, "barcode_patches", patches), \
                mock.patch.object(ma, "_decoded_barcodes", page_codes), \
                mock.patch.object(ma, "time", mock.Mock(time=lambda: clock[0])):
            self.assertEqual(ma.ocr_barcodes(paths, per_image_s=1000.0, total_s=50.0), {"4571614700151"})

    @unittest.skipUnless(HAVE_ZBAR, "pyzbar not installed")
    def test_the_bar_patches_stop_at_their_time_box(self):
        """Within one scan too: a page of text lines that look like bars holds up to six patches."""
        paths = []
        for n in range(3):
            p = self.tmp / f"img{n}.png"
            Image.new("L", (300, 300), 250).save(p)
            paths.append(p)
        clock, seen, crops = [0.0], [], []

        def patches(im, *a):
            seen.append(im.size)
            clock[0] = 1000.0                       # finding them used the time box up
            return [(im.convert("L"), (10, 10, 200, 200), False)] * 3

        def bar_crops(g, box):
            crops.append(box)
            return []
        with mock.patch.object(ma, "barcode_patches", patches), \
                mock.patch.object(ma, "_bar_crops", bar_crops), \
                mock.patch.object(ma, "time", mock.Mock(time=lambda: clock[0])):
            ma.ocr_barcodes(paths, per_image_s=0.0, total_s=0.0, patches_s=1.0)
        self.assertEqual(len(seen), 1)              # only the first scan was searched
        self.assertEqual(crops, [])                 # and none of its patches decoded


@unittest.skipUnless(HAVE_PIL, "Pillow needed")
class ScanOrder(unittest.TestCase):
    def test_single_panels_come_before_booklet_spreads(self):
        """Alcest - Les Voyages De L'âme: img001-img028 are booklet spreads, img029 the back
        with the sticker barcode, img030-031 the discs; only 15 scans are read."""
        d = Path(tempfile.mkdtemp())
        try:
            for n in range(1, 32):
                size = (2000, 1870) if n == 29 else (2800, 2880) if n >= 30 else (3700, 1880)
                Image.new("L", size, n).save(d / f"img{n:03d}.jpg")      # distinct contents
            imgs, _key = ma.list_images(d, 15)
            names = [Path(p).name for p in imgs]
            self.assertEqual(names[:3], ["img029.jpg", "img030.jpg", "img031.jpg"])
            self.assertEqual(len(names), 15)
            Image.new("L", (900, 900), 77).save(d / "back.jpg")             # a name still comes first
            self.assertEqual(Path(ma.list_images(d, 15)[0][0]).name, "back.jpg")
        finally:
            shutil.rmtree(d)

    def test_the_cache_key_changes_with_the_ocr_version(self):
        self.assertEqual(ma.OCR_VERSION, "ocr-3")


@unittest.skipUnless(HAVE_PIL and HAVE_TESSERACT, "Pillow, numpy and tesseract needed")
class PrintedCodes(unittest.TestCase):
    def setUp(self):
        if font(40) is None:
            self.skipTest("no scalable font")
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_the_digits_of_a_sideways_barcode_are_read(self):
        """No decoder: the digits along a sideways barcode are read with it turned upright."""
        back = Image.new("L", (2300, 1800), 225)
        label = ean_label("4601250367880", module=3, height=150)
        back.paste(label.rotate(90, expand=True), (60, 900))
        back.save(self.tmp / "10.png")
        text = ma.ocr_barcode_digits([self.tmp / "10.png"])
        self.assertIn("4601250367880", mc.codes_from_ocr_text(text)["barcodes"], text)
        self.assertEqual(mc.barcodes_read_whole(text, ["4601250367880"]), ["4601250367880"])

    def test_the_catalogue_number_along_a_disc_rim_is_read(self):
        """Lamb of God - Omens: "NB 6570-2" is printed along the edge of the disc."""
        import numpy as np
        size, rad_disc = 2400, 1150
        r0, r1 = rad_disc * 0.86, rad_disc * 0.99
        band = Image.new("L", (int(2 * np.pi * r1), int(r1 - r0)), 200)
        ImageDraw.Draw(band).text((900, 30), "NB 6570-2   LC 07027", font=font(64), fill=0)
        b = np.asarray(band)
        c = size / 2
        yy, xx = np.mgrid[0:size, 0:size]
        rad = np.hypot(xx - c, yy - c)
        ang = np.mod(np.arctan2(yy - c, xx - c), 2 * np.pi)
        disc = np.where(rad <= rad_disc, 160, 255).astype(np.uint8)
        ring = (rad >= r0) & (rad < r1)
        col = (ang[ring] / (2 * np.pi) * b.shape[1]).astype(int) % b.shape[1]
        row = np.clip(((r1 - rad[ring]) / (r1 - r0) * b.shape[0]).astype(int), 0, b.shape[0] - 1)
        disc[ring] = b[row, col]
        disc[rad < 180] = 255
        Image.fromarray(disc).save(self.tmp / "09.png")
        text = ma.ocr_disc_rings([self.tmp / "09.png"])
        self.assertIn("NB 6570-2", mc.codes_from_ocr_text(text)["catnos"], text)


@unittest.skipUnless(HAVE_PIL, "Pillow needed")
class PrintedCodeBudgets(unittest.TestCase):
    """Every tesseract call is stubbed here: these run without tesseract or a font."""
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_the_barcode_digits_stop_at_their_time_box(self):
        """Each patch's digit bands are 7 reads (14 sideways): the budget is checked before each."""
        paths = []
        for n in range(3):
            p = self.tmp / f"img{n}.png"
            Image.new("L", (300, 300), 250).save(p)
            paths.append(p)
        clock, seen, reads = [0.0], [], []

        def patches(im, *a):
            seen.append(im.size)
            clock[0] = 1000.0
            return [(im.convert("L"), (10, 10, 200, 200), True)] * 3
        with mock.patch.object(ma, "barcode_patches", patches), \
                mock.patch.object(ma, "_digit_band_reads", lambda g, b: reads.append(b) or []), \
                mock.patch.object(ma, "time", mock.Mock(time=lambda: clock[0])):
            ma.ocr_barcode_digits(paths, budget_s=1.0)
        self.assertEqual((len(seen), reads), (1, []))

    def test_the_disc_rims_stop_at_their_time_box(self):
        """A rim strip of a big disc scan is a slow read: all discs share one time box."""
        paths = []
        for n in range(3):
            p = self.tmp / f"disc{n}.png"
            Image.new("L", (900, 900), 250).save(p)
            paths.append(p)
        clock, reads = [0.0], []

        def tess(img, psm, *a, **k):
            reads.append(psm)
            clock[0] += 100.0
            return ""
        strip = Image.new("L", (100, 20), 200)
        with mock.patch.object(ma, "find_disc", lambda g: (450, 450, 440)), \
                mock.patch.object(ma, "_hub_strips", lambda *a: [strip] * 3), \
                mock.patch.object(ma, "_rim_strips", lambda *a: [strip] * 2), \
                mock.patch.object(ma, "_tess", tess), \
                mock.patch.object(ma, "time", mock.Mock(time=lambda: clock[0])):
            ma.ocr_disc_rings(paths, budget_s=150.0)
        self.assertEqual(len(reads), 2)             # the third read and the other discs: past it

    def test_the_digit_bands_are_also_read_as_digits_only(self):
        """A smudged digit read as a letter breaks the barcode: each band is read once more
        with a digits-only whitelist."""
        calls = []

        def tess(img, psm, *a, only="", **k):
            calls.append((psm, only))
            return ""
        g = Image.new("L", (600, 400), 250)
        with mock.patch.object(ma, "_tess", tess):
            ma._digit_band_reads(g, (100, 100, 400, 250))
        self.assertIn((7, "0123456789"), calls)
        self.assertIn((7, ""), calls)

    def test_tesseract_gets_the_whitelist_only_when_asked(self):
        cmds = []

        def run(cmd, *a, **k):
            cmds.append(list(cmd))
            return mock.Mock(stdout="")
        img = Image.new("L", (40, 20), 255)
        with mock.patch("subprocess.run", run):
            ma._tess(img, 7, only="0123456789")
            ma._tess(img, 7)
        self.assertEqual(cmds[0][-2:], ["-c", "tessedit_char_whitelist=0123456789"])
        self.assertNotIn("-c", cmds[1])


@unittest.skipUnless(HAVE_PIL, "Pillow needed")
class Investigation(unittest.TestCase):
    def test_the_investigation_decodes_bars_on_every_scan(self):
        """A barcode on a scan past those the first pass read is decoded, searched and,
        decoded whole, taken as the album's barcode."""
        d = Path(tempfile.mkdtemp())
        try:
            Image.new("L", (1200, 900), 200).save(d / "back.jpg")
            album = {"barcodes": set(), "catno_search": [], "artist": "Alcest", "title": "Les Voyages De L'âme"}
            out, notes = {}, []
            seen = []

            def decoded(paths, *a, **k):
                seen.append(list(paths))
                return {"884388712249"}
            with mock.patch.object(ma, "ocr_deep", lambda paths: ""), \
                    mock.patch.object(ma, "pdf_deep", lambda base, db=None: ""), \
                    mock.patch.object(ma, "ocr_disc_rings", lambda paths, *a, **k: ""), \
                    mock.patch.object(ma, "ocr_barcodes", decoded), \
                    mock.patch.object(ma, "web_release_links", lambda *a, **k: ([], [])):
                ma.investigate(None, d, album, dict(mc.DEFAULT_CFG), None, None, None,
                               {"status": "review", "why": []}, "", out, notes, False, False, None)
            self.assertEqual(len(seen), 1)
            self.assertEqual(seen[0], ma.list_images(d, 80)[0])            # all its scans
            self.assertEqual(album["ocr_barcodes"], {"884388712249"})
            self.assertEqual(album["barcodes"], {"884388712249"})
            self.assertIn("884388712249", out["investigated"]["codes"])
            # bars already decoded by the first pass: not decoded again
            seen.clear()
            album = {"barcodes": {"111"}, "ocr_barcodes": {"111"}, "catno_search": [],
                     "artist": "Alcest", "title": "Les Voyages De L'âme"}
            with mock.patch.object(ma, "ocr_deep", lambda paths: ""), \
                    mock.patch.object(ma, "pdf_deep", lambda base, db=None: ""), \
                    mock.patch.object(ma, "ocr_disc_rings", lambda paths, *a, **k: ""), \
                    mock.patch.object(ma, "ocr_barcodes", decoded), \
                    mock.patch.object(ma, "web_release_links", lambda *a, **k: ([], [])):
                ma.investigate(None, d, album, dict(mc.DEFAULT_CFG), None, None, None,
                               {"status": "review", "why": []}, "", {}, [], False, False, None)
            self.assertEqual(seen, [])
            self.assertEqual(album["ocr_barcodes"], {"111"})
        finally:
            shutil.rmtree(d)

    def test_the_first_pass_empty_decode_is_not_redone(self):
        """15 scans or fewer: the investigation's scans are the first pass's, and its empty
        decode (cached as barcodes2) is reused, not searched again for up to ~100 s."""
        import sqlite3
        d = Path(tempfile.mkdtemp())
        try:
            Image.new("L", (1200, 900), 200).save(d / "back.jpg")
            db = sqlite3.connect(":memory:")
            _imgs, key = ma.list_images(d, 15)
            ma._cached(db, "barcodes2", key, lambda: [])            # the first pass read nothing
            seen = []
            album = {"barcodes": set(), "catno_search": [], "artist": "Alcest", "title": "Écailles De Lune"}
            with mock.patch.object(ma, "ocr_deep", lambda paths: ""), \
                    mock.patch.object(ma, "pdf_deep", lambda base, db=None: ""), \
                    mock.patch.object(ma, "ocr_disc_rings", lambda paths, *a, **k: ""), \
                    mock.patch.object(ma, "ocr_barcodes", lambda paths, *a, **k: seen.append(paths) or set()), \
                    mock.patch.object(ma, "web_release_links", lambda *a, **k: ([], [])):
                ma.investigate(None, d, album, dict(mc.DEFAULT_CFG), None, None, None,
                               {"status": "review", "why": []}, "", {}, [], False, False, db)
            self.assertEqual(seen, [])
        finally:
            shutil.rmtree(d)


if __name__ == "__main__":
    unittest.main(verbosity=2)
