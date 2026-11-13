"""BAR-10: Discogs barcodes are written as their digits, the form Discogs's own barcode search
finds exactly (checked: "724352081127", "72435-20811-27" and "0724352081127" give the same
release; the spaced "7 24352 08112 7" matches loosely)."""
import os as _os, tempfile as _tempfile  # noqa: E402
_os.environ.setdefault("XDG_CONFIG_HOME", _tempfile.mkdtemp(prefix="tm-config-"))
_os.environ.setdefault("XDG_STATE_HOME", _tempfile.mkdtemp(prefix="tm-state-"))
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import engine  # noqa: E402


class BAR10_BarcodeDigits(unittest.TestCase):
    def test_punctuated_forms_become_digits(self):
        for v, want in (("72435-20811-27", "724352081127"), ("7 24352 08112 7", "724352081127"),
                        ("5 013929 711730", "5013929711730"), ("0-7567-81838-2-9", "075678183829"),
                        ("4 009910 524923", "4009910524923"), ("12345678", "12345678")):
            self.assertEqual(engine.barcode_digits(v), want, v)

    def test_other_text_stays(self):
        for v in ("none", "D131674", "See notes", "12-34", ""):
            self.assertEqual(engine.barcode_digits(v), v, v)

    def test_lists_written_once(self):
        self.assertEqual(engine.barcode_digits(["5 099943 193424", "5099943193424"]), ["5099943193424"])
        self.assertEqual(engine.barcode_digits(["724352081127", "none"]), ["724352081127", "none"])

    def test_discogs_parts_writes_digits(self):
        class DM:
            def extract_discogs_fields(self, det):
                return {"album": "A", "barcode": "72435-20811-27"}
        orig_shape, orig_dedupe = engine.shape_fields, engine.discogs_without_repeats
        engine.shape_fields = lambda album, s, dm: album
        engine.discogs_without_repeats = lambda det, dm: det
        try:
            import match_albums as ma
            o1, o2, o3 = ma.discogs_one_track_movements, ma.drop_video_tracks, ma.discogs_candidate
            ma.discogs_one_track_movements = lambda det: det
            ma.drop_video_tracks = lambda raw, det: []
            ma.discogs_candidate = lambda det, dm: {"tracks": [], "disc_count": 1}
            try:
                album, _tracks, _n = engine.discogs_parts({"tracklist": []}, DM(), None,
                                                          {"prefer_original_date": False})
            finally:
                ma.discogs_one_track_movements, ma.drop_video_tracks, ma.discogs_candidate = o1, o2, o3
        finally:
            engine.shape_fields, engine.discogs_without_repeats = orig_shape, orig_dedupe
        self.assertEqual(album["barcode"], "724352081127")


if __name__ == "__main__":
    unittest.main()
