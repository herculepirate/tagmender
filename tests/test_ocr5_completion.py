# OCR-5 (docs/MATCHING_RULES.md): the digits under a barcode get their first digit completed by
# the check digit only from two six-digit groups read on ONE line. Readings of separate crops
# (each a line of its own) are never joined: two crops each reading the left group "006759"
# made an invented "2006759006759". Synthetic readings (tesseract stubbed): no library, no network.
import os as _os, tempfile as _tempfile  # noqa: E401
_os.environ["XDG_CONFIG_HOME"] = _tempfile.mkdtemp(prefix="tm-config-")
_os.environ["XDG_STATE_HOME"] = _tempfile.mkdtemp(prefix="tm-state-")
_os.environ.setdefault("TAGMENDER_TAGGER_INI", "/nonexistent")

import sys  # noqa: E402
import tempfile  # noqa: E402
import unittest  # noqa: E402
from pathlib import Path  # noqa: E402
from unittest import mock  # noqa: E402

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
import match_albums as ma  # noqa: E402
import match_core as mc  # noqa: E402


class OCR5(unittest.TestCase):
    def setUp(self):
        from PIL import Image
        self.tmp = tempfile.TemporaryDirectory()
        self.scan = Path(self.tmp.name) / "back.png"
        Image.new("RGB", (400, 300), (255, 255, 255)).save(self.scan)

    def tearDown(self):
        self.tmp.cleanup()

    def _digits(self, readings):
        """ocr_barcode_digits with one barcode found and its crops read as READINGS."""
        from PIL import Image
        patch = (Image.new("L", (400, 300), 255), (50, 50, 300, 150), False)
        with mock.patch.object(ma, "barcode_patches", return_value=[patch]), \
                mock.patch.object(ma, "_digit_band_reads", return_value=list(readings)):
            return ma.ocr_barcode_digits([str(self.scan)])

    def _completed(self, text):
        return [l[len(mc.COMPLETED):] for l in text.splitlines() if l.startswith(mc.COMPLETED)]

    def test_OCR5_groups_from_separate_crops_are_never_joined(self):
        # the left group read alone by two crops (the right half missed): no barcode invented
        text = self._digits(["006759", "006759", "0067595"])
        self.assertEqual(self._completed(text), [])
        self.assertNotIn("2006759006759", text)

    def test_OCR5_groups_split_over_lines_of_one_crop_are_not_joined(self):
        self.assertEqual(self._completed(self._digits(["006759\n\n556030"])), [])

    def test_OCR5_two_groups_on_one_line_are_still_completed(self):
        # "5 013929 020160" with its first digit lost: completed by the check digit
        self.assertEqual(self._completed(self._digits(["BS 013929 020160)"])), ["5013929020160"])
        self.assertEqual(self._completed(self._digits(["~013929-020160"])), ["5013929020160"])

    def test_OCR5_a_printed_code_on_one_line_is_completed_once(self):
        text = self._digits(["noise", "x 400675-955603 y"])
        self.assertEqual(self._completed(text), [mc.complete_ean("400675955603")])
        self.assertTrue(mc.valid_barcode(self._completed(text)[0]))


if __name__ == "__main__":
    unittest.main(verbosity=2)
