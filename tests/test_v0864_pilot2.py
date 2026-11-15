"""0.8.64: pressings told apart by their matrices (EVI-8), discs counted by position prefixes
(MED-18), a hub close-up's matrix read (OCR-6). Synthetic data only."""
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "plugins_builtin", "discogs"))

import discogs_mapping as dm  # noqa: E402
import match_albums as ma  # noqa: E402
import match_core as mc  # noqa: E402


def _entry(rid, matrix, catno="88697655822", barcode="886976558222", conf=100.0, date=""):
    return {"cand": {"source": "discogs", "id": rid, "catnos": [catno], "barcodes": [barcode],
                     "country": "Europe", "formats": ["cd", "dvd"], "label": "Roadrunner",
                     "matrix": [("Matrix / Runout", m) for m in matrix], "date": date, "tracks": []},
            "confidence": conf, "clues": {}}


class EVI8MatricesTellPressingsApart(unittest.TestCase):
    def test_EVI8_disjoint_matrices_are_two_pressings(self):
        a = _entry("100", ["[Arvato logo] 54044125/88697655822-1 21"], date="2010-03-29")
        b = _entry("200", ["Sony DADC A0102309767-0102 13 A00"])
        self.assertTrue(mc._matrices_disjoint([a, b]))
        self.assertIsNone(mc.same_pressing_listed_twice([a, b]))

    def test_EVI8_shared_matrix_is_one_pressing_listed_twice(self):
        a = _entry("100", ["54044125/88697655822-1 21"], date="2010-03-29")
        b = _entry("200", ["[Arvato logo] 54044125/88697655822-1 21"])
        self.assertFalse(mc._matrices_disjoint([a, b]))
        pick = mc.same_pressing_listed_twice([a, b])
        self.assertIsNotNone(pick)
        self.assertEqual(pick[0]["cand"]["id"], "100")       # the fuller (dated) entry

    def test_EVI8_entry_without_matrix_keeps_old_rule(self):
        a = _entry("100", ["54044125/88697655822-1 21"], date="2010-03-29")
        b = _entry("200", [])
        self.assertFalse(mc._matrices_disjoint([a, b]))
        self.assertIsNotNone(mc.same_pressing_listed_twice([a, b]))

    def test_EVI8_catalogue_number_inside_matrix_is_not_a_shared_part(self):
        # both matrices hold the catalogue number; their plant numbers differ
        a = _entry("100", ["11111111/88697655822-1"])
        b = _entry("200", ["22222222/88697655822-1"])
        self.assertTrue(mc._matrices_disjoint([a, b]))

    def test_EVI8_plant_names_alone_are_no_matrix(self):
        a = _entry("100", ["Arvato Digital Services"])
        b = _entry("200", ["Sony DADC"])
        self.assertFalse(mc._matrices_disjoint([a, b]))


def _metalogy_like():
    tl = []
    for disc, n in ((1, 3), (2, 2), (3, 2), (4, 2)):
        tl += [{"type_": "track", "position": f"{disc}-{i:02d}", "title": f"Song {disc}.{i}",
                "duration": "4:00"} for i in range(1, n + 1)]
    tl.append({"type_": "heading", "position": "", "title": "Live", "duration": ""})
    tl += [{"type_": "track", "position": f"DVD-{i}", "title": f"Clip {i}", "duration": ""} for i in (1, 2)]
    return {"id": 1, "title": "Box", "artists": [{"name": "Band"}], "tracklist": tl,
            "formats": [{"name": "CD", "qty": "4", "descriptions": ["Compilation"]},
                        {"name": "DVD", "qty": "1", "descriptions": ["DVD-Video", "NTSC"]},
                        {"name": "Box Set", "qty": "1", "descriptions": []}]}


class MED18DiscsByPositionPrefix(unittest.TestCase):
    def test_MED18_prefixed_run_and_dvd_heading_count_five(self):
        self.assertEqual(ma.discogs_discs(_metalogy_like(), dm)[1], 5)

    def test_MED18_cd_rip_counts_its_four_cds(self):
        cand = ma.discogs_candidate(_metalogy_like(), dm)
        self.assertEqual(cand["disc_count"], 4)
        self.assertEqual(sorted({t["disc"] for t in cand["tracks"] if not t.get("video")}), [1, 2, 3, 4])

    def test_MED18_never_above_declared_formats(self):
        rel = _metalogy_like()
        rel["formats"] = [{"name": "CD", "qty": "2"}, {"name": "DVD", "qty": "1"}]
        self.assertLessEqual(ma.discogs_discs(rel, dm)[1], 3)

    def test_MED18_heading_split_release_unchanged(self):
        rel = {"tracklist": [{"type_": "heading", "title": "CD 1"},
                             {"type_": "track", "position": "1", "title": "a", "duration": "3:00"},
                             {"type_": "heading", "title": "CD 2"},
                             {"type_": "track", "position": "1", "title": "b", "duration": "3:00"}],
               "formats": [{"name": "CD", "qty": "2"}]}
        self.assertEqual(ma.discogs_discs(rel, dm)[1], 2)


@unittest.skipUnless(shutil.which("tesseract"), "tesseract not installed")
class OCR6HubCloseUp(unittest.TestCase):
    def test_OCR6_matrix_between_hub_and_rim_is_read(self):
        import numpy as np
        from PIL import Image, ImageDraw, ImageFont
        size, code = 1200, "A0102309767-0102 13 A00"
        r = size / 2 * 0.98
        r0, r1 = r * 0.74, r * 0.82
        n, rows = int(2 * np.pi * r1), int(r1 - r0)
        flat = Image.new("L", (n, rows), 255)
        try:
            font = ImageFont.truetype("DejaVuSans.ttf", int(rows * 0.7))
        except OSError:
            font = ImageFont.load_default()
        ImageDraw.Draw(flat).text((n // 8, 2), code, fill=0, font=font)
        fa = np.asarray(flat)
        yy, xx = np.mgrid[0:size, 0:size]
        cx = cy = size / 2
        rad = np.hypot(xx - cx, yy - cy)
        th = np.mod(np.arctan2(yy - cy, xx - cx), 2 * np.pi)
        img = np.full((size, size), 120, dtype=np.uint8)          # the disc fills the frame
        band = (rad >= r0) & (rad < r1)
        col = np.clip((th / (2 * np.pi) * n).astype(int), 0, n - 1)
        row = np.clip(((r1 - rad) / (r1 - r0) * rows).astype(int), 0, rows - 1)
        img[band] = np.where(fa[row[band], col[band]] < 128, 0, 230)
        img[rad < r * 0.3] = 250                                   # the hole and clamping ring
        d = tempfile.mkdtemp()
        try:
            p = os.path.join(d, "cdm.png")
            Image.fromarray(img).save(p)
            text = ma.ocr_disc_rings([p])
        finally:
            shutil.rmtree(d)
        self.assertIn("102309767", text, text)      # (its digits: a synthetic font may read 0 as Q)


if __name__ == "__main__":
    unittest.main()
