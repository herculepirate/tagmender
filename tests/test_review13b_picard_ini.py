# Thirteenth independent review (Picard code): this test failed before its fix.
"""picard_config.tag_settings reads Picard.ini as Picard (Qt's QSettings, IniFormat) wrote it.

The ini text below is exactly what PyQt6's QSettings (Picard 3's config store) writes for
those values: a list of ONE string is stored as "@Variant(...)" (Qt's binary form, so it reads
back as a list), and a string holding a comma (or leading/trailing spaces) is quoted.
_qt_list returns [] for anything starting with "@", and strings are only strip()ped.

Picard 2 had no "standardize_vocals" (vocals followed "standardize_instruments"; Picard 3's
upgrade copies it, config_upgrade_hooks.copy_standardize_instruments_to_vocals) and kept the
translation locales in "artist_locales" (renamed "translation_locales" by
rename_artist_locales)."""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _review13b_common as _common  # noqa: E402,F401

import picard_config as pc  # noqa: E402

PICARD3_INI = r"""[application]
version=3.0.2

[setting]
preferred_release_countries=@Variant(\0\0\0\t\0\0\0\x1\0\0\0\n\0\0\0\x4\0G\0\x42)
translate_artist_names=true
translation_locales=@Variant(\0\0\0\t\0\0\0\x1\0\0\0\n\0\0\0\x4\0j\0\x61)
va_name="Various, Artists"
"""

PICARD2_INI = """[application]
version=2.12.3

[setting]
standardize_instruments=false
translate_artist_names=true
artist_locales=de, fr
"""


def ini(text):
    p = Path(tempfile.mkdtemp()) / "Picard.ini"
    p.write_text(text, encoding="utf-8")
    return p


class Picard3Ini(unittest.TestCase):
    def test_one_translation_locale(self):
        self.assertEqual(pc.tag_settings(path=ini(PICARD3_INI))["translation_locales"], ["ja"])

    def test_one_preferred_release_country(self):
        self.assertEqual(pc.tag_settings(path=ini(PICARD3_INI))["preferred_release_countries"], ["GB"])

    def test_various_artists_name_with_a_comma(self):
        self.assertEqual(pc.tag_settings(path=ini(PICARD3_INI))["va_name"], "Various, Artists")


class Picard2Ini(unittest.TestCase):
    def test_vocals_follow_instruments(self):
        self.assertFalse(pc.tag_settings(path=ini(PICARD2_INI))["standardize_vocals"])

    def test_artist_locales(self):
        self.assertEqual(pc.tag_settings(path=ini(PICARD2_INI))["translation_locales"], ["de", "fr"])


if __name__ == "__main__":
    unittest.main()
