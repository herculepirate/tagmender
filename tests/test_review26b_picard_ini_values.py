"""Picard.ini values TagMender reads otherwise than Picard 3.0.2 (each checked with Picard's own
Config.from_file + run_config_upgrades + config.setting[...] on the same file):

1. Picard < 2.7 kept the translation locale as one string, artist_locale=de; Picard's upgrade
   hook (2.7.0dev4) turns it into artist_locales, later translation_locales ["de"]. TagMender
   only knows artist_locales/translation_locales and translates to "en" (picard_config.tag_settings).
2. True/false: Picard reads a boolean through QSettings' bool conversion - "1", "yes", "on",
   "True" are all true; TagMender takes only "true" (`raw.strip().lower() == "true"`), so
   use_genres=1 or track_ars=on is off.
3. Numbers out of range: Picard clamps max_genres / min_genre_usage to 0..100 on read
   (BoundedNumberOption); TagMender keeps 250, and a min_genre_usage of 250 % filters out every
   genre where Picard keeps the top one.
4. A Picard 3 Picard.ini with standardize_instruments=false and no standardize_vocals: Picard 3
   uses standardize_vocals' default (true; the copy is an upgrade hook for configs older than
   3.0.0dev6); TagMender copies standardize_instruments into it as for Picard 2.
"""
import unittest
from pathlib import Path

import _review26b_common as c
import picard_config


def settings(text):
    picard_config._cache.clear()
    return picard_config.tag_settings(path=Path(c.write_ini(text)))


class PicardIniValues(unittest.TestCase):
    def test_picard_25_artist_locale(self):
        s = settings("[application]\nversion=2.5.6.final0\n\n[setting]\nartist_locale=de\n"
                     "translate_artist_names=true\n")
        self.assertEqual(s["translation_locales"], ["de"])

    def test_booleans_as_qt_reads_them(self):
        s = settings("[application]\nversion=3.0.2.final0\n\n[setting]\nuse_genres=1\ntrack_ars=on\n"
                     "artists_genres=yes\n")
        self.assertEqual((s["use_genres"], s["track_ars"], s["artists_genres"]), (True, True, True))

    def test_numbers_clamped(self):
        s = settings("[application]\nversion=3.0.2.final0\n\n[setting]\nmax_genres=150\nmin_genre_usage=250\n")
        self.assertEqual((s["max_genres"], s["min_genre_usage"]), (100, 100))

    def test_picard3_vocals_default(self):
        s = settings("[application]\nversion=3.0.2.final0\n\n[setting]\nstandardize_instruments=false\n")
        self.assertTrue(s["standardize_vocals"])


if __name__ == "__main__":
    unittest.main()
