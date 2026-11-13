# OCR-3 (docs/MATCHING_RULES.md): each scan is read in colour and in its own script. A Latin
# cover is read in the Latin packs (never only through eng+rus+jpn at once), a Cyrillic or
# Japanese one in its own pack and English; with no sure script (OSD), Latin first and every
# pack only when that reads too little. Page readings are cached under a new kind.
# Synthetic images only (Pillow-rendered text): no library, no network. Tests that run the
# real tesseract skip when it, a language pack or a font is missing.
import os as _os, tempfile as _tempfile  # noqa: E401
_os.environ["XDG_CONFIG_HOME"] = _tempfile.mkdtemp(prefix="tm-config-")
_os.environ["XDG_STATE_HOME"] = _tempfile.mkdtemp(prefix="tm-state-")
_os.environ.setdefault("TAGMENDER_TAGGER_INI", "/nonexistent")

import shutil  # noqa: E402
import subprocess  # noqa: E402
import sys  # noqa: E402
import tempfile  # noqa: E402
import unittest  # noqa: E402
from pathlib import Path  # noqa: E402
from unittest import mock  # noqa: E402

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
import match_albums as ma  # noqa: E402

LATIN_FONTS = ["/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
               "/usr/share/fonts/dejavu/DejaVuSans.ttf",
               "/usr/share/fonts/TTF/DejaVuSans.ttf"]
CJK_FONTS = ["/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
             "/usr/share/fonts/opentype/noto/NotoSerifCJK-Regular.ttc",
             "/usr/share/fonts/opentype/noto/NotoSerifCJK-Bold.ttc",
             "/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc"]


def _font(cands):
    return next((f for f in cands if Path(f).is_file()), None)


def _packs():
    if not shutil.which("tesseract"):
        return set()
    try:
        return set(subprocess.run(["tesseract", "--list-langs"], capture_output=True, text=True,
                                  timeout=30).stdout.split())
    except Exception:
        return set()


PACKS = _packs()


def _render(path, lines, font, ink=(0, 0, 0), paper=(255, 255, 255), size=40):
    from PIL import Image, ImageDraw, ImageFont
    f = ImageFont.truetype(font, size)
    im = Image.new("RGB", (1400, 90 * len(lines) + 80), paper)
    d = ImageDraw.Draw(im)
    for i, line in enumerate(lines):
        d.text((40, 40 + 90 * i), line, font=f, fill=ink)
    im.save(path, dpi=(300, 300))
    return path


class _Langs(unittest.TestCase):
    def setUp(self):
        self._was = ma._LANGS
        ma._SCAN_LANGS.clear()
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        ma._LANGS = self._was
        ma._SCAN_LANGS.clear()
        self.tmp.cleanup()


@unittest.skipUnless({"eng", "osd"} <= PACKS and _font(LATIN_FONTS), "tesseract eng/osd or a font missing")
class OCR3RealTesseract(_Langs):
    def test_OCR3_a_latin_label_is_read_in_english_with_its_catalogue_number(self):
        # blue print on a dark ground of the same greyscale value (a vinyl label's blue ink):
        # a greyscale copy shows nothing; tesseract's own colour reading finds it
        p = _render(self.dir / "label.png", ["Bestell-Nr. 20 21276-9", "Made in Germany  A-0547 A/C 3A"],
                    _font(LATIN_FONTS), ink=(0, 0, 255), paper=(29, 29, 29))
        ma._LANGS = "+".join(x for x in ("eng", "rus", "jpn") if x in PACKS)
        langs, text = ma.scan_page(p)
        self.assertEqual(langs, "eng")
        self.assertIn("21276-9", text)
        self.assertIn("A-0547", text)
        self.assertIn("21276-9", ma.ocr_text([str(p)]))

    @unittest.skipUnless("rus" in PACKS, "tesseract rus missing")
    def test_OCR3_a_cyrillic_sleeve_is_still_read_in_russian(self):
        p = _render(self.dir / "cyr.png", ["Бестелл-Нр. 20 21276-9", "Мелодия: Всесоюзная фирма грамзаписи",
                                           "Сделано в СССР  С60-12345"], _font(LATIN_FONTS))
        ma._LANGS = "+".join(x for x in ("eng", "rus", "jpn") if x in PACKS)
        langs, text = ma.scan_page(p)
        self.assertIn("rus", langs)
        self.assertIn("Мелодия", text)
        self.assertIn("21276-9", text)

    @unittest.skipUnless("jpn" in PACKS and _font(CJK_FONTS), "tesseract jpn or a CJK font missing")
    def test_OCR3_a_japanese_obi_is_still_read_in_japanese(self):
        p = _render(self.dir / "jpn.png", ["ポポル・ヴー 「ファラオの庭」", "日本コロムビア株式会社 製造",
                                           "定価 2,500円  K22P-123"], _font(CJK_FONTS))
        ma._LANGS = "+".join(x for x in ("eng", "rus", "jpn") if x in PACKS)
        langs, text = ma.scan_page(p)
        self.assertIn("jpn", langs)
        self.assertTrue(any("぀" <= ch <= "ヿ" or "一" <= ch <= "鿿" for ch in text), text)
        self.assertIn("K22P-123", text)


def _fake(script="", latin_words=(), all_words=()):
    """A tesseract stand-in: OSD names SCRIPT ("" - too few characters), page readings give
    these words (confidence 95). Records every command."""
    calls = []

    def run(cmd, **kw):
        calls.append(cmd)
        if "0" in cmd and cmd[cmd.index("--psm") + 1] == "0":
            out = f"Script: {script}\nScript confidence: 5.0\n" if script else ""
            return mock.Mock(stdout=out, stderr="" if script else "Too few characters")
        langs = cmd[cmd.index("-l") + 1]
        words = all_words if "rus" in langs or "jpn" in langs else latin_words
        base = cmd[2]
        if base == "stdout":                                                 # (the spines)
            return mock.Mock(stdout=" ".join(words) + "\n", stderr="")
        Path(base + ".txt").write_text(" ".join(words) + "\n", encoding="utf-8")
        rows = ["level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext"]
        rows += [f"5\t1\t1\t1\t1\t{i}\t0\t0\t1\t1\t95\t{w}" for i, w in enumerate(words)]
        Path(base + ".tsv").write_text("\n".join(rows) + "\n", encoding="utf-8")
        return mock.Mock(stdout="", stderr="")
    return run, calls


class OCR3Routing(_Langs):
    def _scan(self):
        from PIL import Image
        p = self.dir / "scan.png"
        Image.new("RGB", (300, 200), (255, 255, 255)).save(p)
        return p

    def _langs_used(self, calls):
        return [c[c.index("-l") + 1] for c in calls if "-l" in c]

    def test_OCR3_osd_latin_reads_only_the_latin_packs(self):
        ma._LANGS = "eng+deu+rus+jpn"
        run, calls = _fake("Latin", latin_words=["Bestell-Nr.", "20", "21276-9"])
        with mock.patch.object(ma, "_tesseract", run):
            langs, text = ma.scan_page(self._scan())
        self.assertEqual((langs, self._langs_used(calls)), ("eng+deu", ["eng+deu"]))
        self.assertIn("21276-9", text)

    def test_OCR3_osd_cyrillic_reads_russian_and_english(self):
        ma._LANGS = "eng+rus+jpn"
        run, calls = _fake("Cyrillic", all_words=["Мелодия"])
        with mock.patch.object(ma, "_tesseract", run):
            langs, _t = ma.scan_page(self._scan())
        self.assertEqual((langs, self._langs_used(calls)), ("rus+eng", ["rus+eng"]))

    def test_OCR3_osd_japanese_without_the_pack_falls_back_to_latin(self):
        ma._LANGS = "eng+rus"
        run, calls = _fake("Japanese", latin_words=["Popol", "Pharaos", "Germany"])
        with mock.patch.object(ma, "_tesseract", run):
            langs, _t = ma.scan_page(self._scan())
        self.assertEqual((langs, self._langs_used(calls)), ("eng", ["eng"]))

    def test_OCR3_no_sure_script_and_good_english_never_tries_every_pack(self):
        ma._LANGS = "eng+rus+jpn"
        run, calls = _fake("", latin_words=["Popol", "Vuh", "Bestell-Nr.", "21276-9", "Garten"])
        with mock.patch.object(ma, "_tesseract", run):
            langs, text = ma.scan_page(self._scan())
        self.assertEqual((langs, self._langs_used(calls)), ("eng", ["eng"]))
        self.assertIn("Garten", text)

    def test_OCR3_no_sure_script_and_poor_english_keeps_the_richer_reading_and_the_latin_one(self):
        ma._LANGS = "eng+rus+jpn"
        run, calls = _fake("", latin_words=["K22P-123"], all_words=["日本コロムビア", "株式会社", "定価", "K22P-123"])
        with mock.patch.object(ma, "_tesseract", run):
            langs, text = ma.scan_page(self._scan())
        self.assertEqual(self._langs_used(calls), ["eng", "eng+rus+jpn"])
        self.assertEqual(langs, "eng+rus+jpn")
        self.assertIn("株式会社", text)
        self.assertIn("K22P-123", text)

    def test_OCR3_no_sure_script_poor_english_and_junk_keeps_english(self):
        ma._LANGS = "eng+rus+jpn"
        run, calls = _fake("", latin_words=["AA", "S"], all_words=["る", "ーー", "ンー", "S"])
        with mock.patch.object(ma, "_tesseract", run):
            langs, _t = ma.scan_page(self._scan())
        self.assertEqual((langs, self._langs_used(calls)), ("eng", ["eng", "eng+rus+jpn"]))

    def test_OCR3_osd_failing_outright_falls_back_to_english(self):
        ma._LANGS = "eng+rus+jpn"
        run, calls = _fake("", latin_words=["Popol", "Vuh", "Garten"])

        def failing(cmd, **kw):
            if cmd[cmd.index("--psm") + 1] == "0":
                raise subprocess.TimeoutExpired(cmd, 30)
            return run(cmd, **kw)
        with mock.patch.object(ma, "_tesseract", failing):
            langs, _t = ma.scan_page(self._scan())
        self.assertEqual((langs, self._langs_used(calls)), ("eng", ["eng"]))

    def test_OCR3_latin_only_settings_run_no_osd(self):
        ma._LANGS = "eng+deu"
        run, calls = _fake("Cyrillic", latin_words=["Garten"])
        with mock.patch.object(ma, "_tesseract", run):
            self.assertEqual(ma.scan_page(self._scan())[0], "eng+deu")
            self.assertEqual(ma.scan_langs(self._scan()), "eng+deu")
        self.assertFalse([c for c in calls if c[c.index("--psm") + 1] == "0"])

    def test_OCR3_spines_are_read_in_the_script_the_page_was(self):
        from PIL import Image
        ma._LANGS = "eng+rus+jpn"
        p = self.dir / "tray.png"
        Image.new("RGB", (1380, 1080), (255, 255, 255)).save(p)            # a tray card's shape
        run, calls = _fake("Latin", latin_words=["Garten"])
        with mock.patch.object(ma, "_tesseract", run):
            ma.scan_page(p)
            del calls[:]
            ma.ocr_spines([str(p)])
        self.assertTrue(calls)
        self.assertEqual(set(self._langs_used(calls)), {"eng"})

    def test_OCR3_page_readings_have_a_new_cache_kind(self):
        ma._LANGS = "eng+rus+jpn"
        for base in ("text", "spines", "cover"):
            k = ma.page_kind(base)
            self.assertNotIn(k, (base, f"{base}-eng+rus+jpn"))
            self.assertTrue(k.startswith(base + "-") and k.endswith("eng+rus+jpn"))
        ma._LANGS = "eng"
        self.assertNotEqual(ma.page_kind("text"), "text")          # greyscale readings are not reused

    def test_OCR3_the_scan_is_given_in_colour_with_its_resolution(self):
        from PIL import Image
        p = self.dir / "big.jpg"
        Image.new("RGB", (3000, 2000), (0, 0, 255)).save(p, dpi=(300, 300))
        im, opts = ma._page_image(p)
        self.assertEqual((im.mode, im.size, opts.get("dpi")), ("RGB", (2400, 1600), (240, 240)))


if __name__ == "__main__":
    unittest.main(verbosity=2)
