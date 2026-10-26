"""An option profile holding an option ticked but never given a value - the state Picard 3.0.2's
profile editor stores for every option you tick in a profile (ui/options/profiles.py
update_values_in_profile_options: `self.profile_settings[id][item] = None`) - makes
TagMender ignore EVERY option profile.

Picard keeps such an option as a null QVariant in user_profile_settings and skips it
(config.py ProfileConfigSection._get_profile_override: `settings[pkey] is not None`), using
the profile's other settings. picard_config._qt_variant raises ValueError on QVariant type 0
(an invalid/null variant), returns None for the whole map, profile_settings() gets {} and
_from_profiles finds nothing: [setting] is used throughout.

PICARD_INI was written by Picard 3.0.2's own Config (QSettings): one enabled profile
"Compilations" with va_name "Diverse", standardize_artist_names "none", and "use_genres"
ticked without a value. Picard reads va_name "Diverse" and standardize_artist_names "none"
(Config.from_file + run_config_upgrades + config.setting[...]); TagMender reads "VA Base" and
"all". User-visible: a Various Artists album gets album artist "VA Base", artist names are
standardized although the profile says not to. Fails with TM_REPO=prev too (profiles were
not read at all then).
"""
import unittest
from pathlib import Path

import _review27b_common as c
import mb_tags
import picard_config

PICARD_INI = '[application]\nversion=3.0.2.final0\n\n[profiles]\nuser_profile_settings=@Variant(\\0\\0\\0\\b\\0\\0\\0\\x1\\0\\0\\0\\x1e\\0i\\0\\x64\\0-\\0\\x43\\0o\\0m\\0p\\0i\\0l\\0\\x61\\0t\\0i\\0o\\0n\\0s\\0\\0\\0\\b\\0\\0\\0\\x3\\0\\0\\0\\x30\\0s\\0t\\0\\x61\\0n\\0\\x64\\0\\x61\\0r\\0\\x64\\0i\\0z\\0\\x65\\0_\\0\\x61\\0r\\0t\\0i\\0s\\0t\\0_\\0n\\0\\x61\\0m\\0\\x65\\0s\\0\\0\\0\\n\\0\\0\\0\\b\\0n\\0o\\0n\\0\\x65\\0\\0\\0\\x14\\0u\\0s\\0\\x65\\0_\\0g\\0\\x65\\0n\\0r\\0\\x65\\0s\\0\\0\\0\\0\\xff\\xff\\xff\\xff\\0\\0\\0\\xe\\0v\\0\\x61\\0_\\0n\\0\\x61\\0m\\0\\x65\\0\\0\\0\\n\\0\\0\\0\\xe\\0\\x44\\0i\\0v\\0\\x65\\0r\\0s\\0\\x65)\nuser_profiles=@Variant(\\0\\0\\0\\t\\0\\0\\0\\x1\\0\\0\\0\\b\\0\\0\\0\\x4\\0\\0\\0\\xe\\0\\x65\\0n\\0\\x61\\0\\x62\\0l\\0\\x65\\0\\x64\\0\\0\\0\\x1\\x1\\0\\0\\0\\x4\\0i\\0\\x64\\0\\0\\0\\n\\0\\0\\0\\x1e\\0i\\0\\x64\\0-\\0\\x43\\0o\\0m\\0p\\0i\\0l\\0\\x61\\0t\\0i\\0o\\0n\\0s\\0\\0\\0\\x10\\0p\\0o\\0s\\0i\\0t\\0i\\0o\\0n\\0\\0\\0\\x2\\0\\0\\0\\0\\0\\0\\0\\n\\0t\\0i\\0t\\0l\\0\\x65\\0\\0\\0\\n\\0\\0\\0\\x18\\0\\x43\\0o\\0m\\0p\\0i\\0l\\0\\x61\\0t\\0i\\0o\\0n\\0s)\n\n[setting]\nstandardize_artist_names=all\nva_name=VA Base\n'


def va_release():
    vac = c.credit(("Various Artists", c.VA, ""))
    a = c.artist("Björk")
    ac = c.credit(("Björk", a, ""))
    return c.release("profile-null-va", "Hits", vac,
                     [c.track(1, "Hyperballad", ac, c.recording("Hyperballad", ac))], secondary=("Compilation",))


class ProfileWithNullValue(unittest.TestCase):
    def setUp(self):
        self.ini = c.write_ini(PICARD_INI)
        picard_config._cache.clear()

    def test_profile_settings_still_win(self):
        s = picard_config.tag_settings(path=Path(self.ini))
        self.assertEqual({k: s[k] for k in ("va_name", "standardize_artist_names", "use_genres")},
                         {"va_name": "Diverse", "standardize_artist_names": "none", "use_genres": False})

    def test_album_artist_of_a_va_release(self):
        s = picard_config.tag_settings(path=Path(self.ini))
        self.assertEqual(mb_tags.release_tags(va_release(), s)[0]["albumartist"], ["Diverse"])


if __name__ == "__main__":
    unittest.main()
