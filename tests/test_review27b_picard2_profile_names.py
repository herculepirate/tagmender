"""A Picard 2 Picard.ini whose enabled option profile uses Picard 2's option names: the profile
is read for Picard 3's names only.

picard_config.tag_settings maps Picard 2's [setting] names ("standardize_artists" true/false
-> standardize_artist_names all/none; "artist_locales"/"artist_locale" -> translation_locales),
but last round's profile code (`for k in list(TAG_DEFAULTS) + list(FILE_DEFAULTS):
_from_profiles(cp, k)`) looks up only Picard 3 names in the profile. Picard 2.x (option
profiles since 2.7) reads its profile's "standardize_artists" and "artist_locales" like any
other setting; Picard 3 renames them inside every profile when it upgrades the file
(config_upgrade_hooks.convert_standardize_artists / rename_artist_locales "run on base config
+ all profile overrides").

PICARD_INI: written with Picard's QSettings code, [application] version 2.12.3: [setting]
standardize_artists=false; enabled profile "Std": standardize_artists true, artist_locales
["de"], translate_artist_names true. Picard 2 uses standardized artist names (Picard 3: "all")
and German aliases; TagMender gives "none" and ["en"]. Fails with TM_REPO=prev too.
"""
import unittest
from pathlib import Path

import _review27b_common as c
import picard_config

PICARD_INI = '[application]\nversion=2.12.3.final0\n\n[profiles]\nuser_profile_settings="@Variant(\\0\\0\\0\\b\\0\\0\\0\\x1\\0\\0\\0\\f\\0i\\0\\x64\\0-\\0S\\0t\\0\\x64\\0\\0\\0\\b\\0\\0\\0\\x3\\0\\0\\0\\x1c\\0\\x61\\0r\\0t\\0i\\0s\\0t\\0_\\0l\\0o\\0\\x63\\0\\x61\\0l\\0\\x65\\0s\\0\\0\\0\\t\\0\\0\\0\\x1\\0\\0\\0\\n\\0\\0\\0\\x4\\0\\x64\\0\\x65\\0\\0\\0&\\0s\\0t\\0\\x61\\0n\\0\\x64\\0\\x61\\0r\\0\\x64\\0i\\0z\\0\\x65\\0_\\0\\x61\\0r\\0t\\0i\\0s\\0t\\0s\\0\\0\\0\\x1\\x1\\0\\0\\0,\\0t\\0r\\0\\x61\\0n\\0s\\0l\\0\\x61\\0t\\0\\x65\\0_\\0\\x61\\0r\\0t\\0i\\0s\\0t\\0_\\0n\\0\\x61\\0m\\0\\x65\\0s\\0\\0\\0\\x1\\x1)"\nuser_profiles=@Variant(\\0\\0\\0\\t\\0\\0\\0\\x1\\0\\0\\0\\b\\0\\0\\0\\x4\\0\\0\\0\\xe\\0\\x65\\0n\\0\\x61\\0\\x62\\0l\\0\\x65\\0\\x64\\0\\0\\0\\x1\\x1\\0\\0\\0\\x4\\0i\\0\\x64\\0\\0\\0\\n\\0\\0\\0\\f\\0i\\0\\x64\\0-\\0S\\0t\\0\\x64\\0\\0\\0\\x10\\0p\\0o\\0s\\0i\\0t\\0i\\0o\\0n\\0\\0\\0\\x2\\0\\0\\0\\0\\0\\0\\0\\n\\0t\\0i\\0t\\0l\\0\\x65\\0\\0\\0\\n\\0\\0\\0\\x6\\0S\\0t\\0\\x64)\n\n[setting]\nstandardize_artists=false\nva_name=VA\n'


class Picard2ProfileNames(unittest.TestCase):
    def test_profile_with_picard2_names(self):
        ini = c.write_ini(PICARD_INI)
        picard_config._cache.clear()
        s = picard_config.tag_settings(path=Path(ini))
        self.assertEqual({k: s[k] for k in ("standardize_artist_names", "translation_locales", "translate_artist_names")},
                         {"standardize_artist_names": "all", "translation_locales": ["de"],
                          "translate_artist_names": True})


if __name__ == "__main__":
    unittest.main()
