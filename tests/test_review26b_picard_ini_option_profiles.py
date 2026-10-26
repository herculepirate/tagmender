"""Picard 3 option profiles are not read. Picard 3.0.2 keeps the settings of an enabled option
profile in Picard.ini's [profiles] section (user_profiles, user_profile_settings) and they
override [setting] for every option marked in_profile - which is every setting TagMender
follows (standardize_artist_names, va_name, the genre options, translation, release/track
relationships, list_of_scripts, preserved_tags, write_id3v23 ...). picard_config.tag_settings
(and preserved_tags) read [setting] only, so a user whose profile says "Various Artists name:
Diverse" and "Standardize artist names: none" gets "VA Base" and "all" from TagMender.
(doctor.py mentions "a Picard option profile overriding these is not read"; docs/
PICARD_COVERAGE.md says the settings are read from Picard.ini with no such exception.)

PICARD_INI below was written by Picard 3.0.2's own Config (QSettings) - one enabled profile
"Compilations", one disabled profile "Old"; Picard reads va_name "Diverse",
standardize_artist_names "none", max_genres 4 from it (checked with Config.from_file +
run_config_upgrades + config.setting[...]).
"""
import unittest
from pathlib import Path

import _review26b_common as c
import mb_tags
import picard_config

PICARD_INI = '[application]\nversion=3.0.2.final0\n\n[profiles]\nuser_profile_settings=@Variant(\\0\\0\\0\\b\\0\\0\\0\\x2\\0\\0\\0H\\0\\x33\\0\\x66\\0\\x30\\0\\x64\\0\\x36\\0\\x61\\0\\x34\\0\\x39\\0-\\0\\x37\\0\\x62\\0\\x38\\0\\x65\\0-\\0\\x34\\0\\x62\\0\\x30\\0\\x63\\0-\\0\\x39\\0\\x61\\0\\x35\\0\\x36\\0-\\0\\x33\\0\\x61\\0\\x31\\0\\x64\\0\\x38\\0\\x66\\0\\x30\\0\\x66\\0\\x32\\0\\x63\\0\\x31\\0\\x31\\0\\0\\0\\b\\0\\0\\0\\x3\\0\\0\\0\\x14\\0m\\0\\x61\\0x\\0_\\0g\\0\\x65\\0n\\0r\\0\\x65\\0s\\0\\0\\0\\x2\\0\\0\\0\\x4\\0\\0\\0\\x30\\0s\\0t\\0\\x61\\0n\\0\\x64\\0\\x61\\0r\\0\\x64\\0i\\0z\\0\\x65\\0_\\0\\x61\\0r\\0t\\0i\\0s\\0t\\0_\\0n\\0\\x61\\0m\\0\\x65\\0s\\0\\0\\0\\n\\0\\0\\0\\b\\0n\\0o\\0n\\0\\x65\\0\\0\\0\\xe\\0v\\0\\x61\\0_\\0n\\0\\x61\\0m\\0\\x65\\0\\0\\0\\n\\0\\0\\0\\xe\\0\\x44\\0i\\0v\\0\\x65\\0r\\0s\\0\\x65\\0\\0\\0H\\0\\x39\\0\\x62\\0\\x32\\0\\x65\\0\\x31\\0\\x63\\0\\x37\\0\\x37\\0-\\0\\x35\\0\\x64\\0\\x34\\0\\x30\\0-\\0\\x34\\0\\x63\\0\\x30\\0\\x65\\0-\\0\\x38\\0\\x66\\0\\x31\\0\\x66\\0-\\0\\x32\\0\\x65\\0\\x36\\0\\x63\\0\\x34\\0\\x66\\0\\x37\\0\\x64\\0\\x39\\0\\x61\\0\\x33\\0\\x30\\0\\0\\0\\b\\0\\0\\0\\x2\\0\\0\\0\\x30\\0s\\0t\\0\\x61\\0n\\0\\x64\\0\\x61\\0r\\0\\x64\\0i\\0z\\0\\x65\\0_\\0\\x61\\0r\\0t\\0i\\0s\\0t\\0_\\0n\\0\\x61\\0m\\0\\x65\\0s\\0\\0\\0\\n\\0\\0\\0\\x14\\0v\\0\\x61\\0r\\0i\\0\\x61\\0t\\0i\\0o\\0n\\0s\\0\\0\\0\\xe\\0v\\0\\x61\\0_\\0n\\0\\x61\\0m\\0\\x65\\0\\0\\0\\n\\0\\0\\0 \\0\\x44\\0i\\0s\\0\\x61\\0\\x62\\0l\\0\\x65\\0\\x64\\0 \\0p\\0r\\0o\\0\\x66\\0i\\0l\\0\\x65)\nuser_profiles=@Variant(\\0\\0\\0\\b\\0\\0\\0\\x4\\0\\0\\0\\xe\\0\\x65\\0n\\0\\x61\\0\\x62\\0l\\0\\x65\\0\\x64\\0\\0\\0\\x1\\x1\\0\\0\\0\\x4\\0i\\0\\x64\\0\\0\\0\\n\\0\\0\\0H\\0\\x33\\0\\x66\\0\\x30\\0\\x64\\0\\x36\\0\\x61\\0\\x34\\0\\x39\\0-\\0\\x37\\0\\x62\\0\\x38\\0\\x65\\0-\\0\\x34\\0\\x62\\0\\x30\\0\\x63\\0-\\0\\x39\\0\\x61\\0\\x35\\0\\x36\\0-\\0\\x33\\0\\x61\\0\\x31\\0\\x64\\0\\x38\\0\\x66\\0\\x30\\0\\x66\\0\\x32\\0\\x63\\0\\x31\\0\\x31\\0\\0\\0\\x10\\0p\\0o\\0s\\0i\\0t\\0i\\0o\\0n\\0\\0\\0\\x2\\0\\0\\0\\0\\0\\0\\0\\n\\0t\\0i\\0t\\0l\\0\\x65\\0\\0\\0\\n\\0\\0\\0\\x18\\0\\x43\\0o\\0m\\0p\\0i\\0l\\0\\x61\\0t\\0i\\0o\\0n\\0s), @Variant(\\0\\0\\0\\b\\0\\0\\0\\x4\\0\\0\\0\\xe\\0\\x65\\0n\\0\\x61\\0\\x62\\0l\\0\\x65\\0\\x64\\0\\0\\0\\x1\\0\\0\\0\\0\\x4\\0i\\0\\x64\\0\\0\\0\\n\\0\\0\\0H\\0\\x39\\0\\x62\\0\\x32\\0\\x65\\0\\x31\\0\\x63\\0\\x37\\0\\x37\\0-\\0\\x35\\0\\x64\\0\\x34\\0\\x30\\0-\\0\\x34\\0\\x63\\0\\x30\\0\\x65\\0-\\0\\x38\\0\\x66\\0\\x31\\0\\x66\\0-\\0\\x32\\0\\x65\\0\\x36\\0\\x63\\0\\x34\\0\\x66\\0\\x37\\0\\x64\\0\\x39\\0\\x61\\0\\x33\\0\\x30\\0\\0\\0\\x10\\0p\\0o\\0s\\0i\\0t\\0i\\0o\\0n\\0\\0\\0\\x2\\0\\0\\0\\x1\\0\\0\\0\\n\\0t\\0i\\0t\\0l\\0\\x65\\0\\0\\0\\n\\0\\0\\0\\x6\\0O\\0l\\0\\x64)\n\n[setting]\nmax_genres=2\nstandardize_artist_names=all\nuse_genres=true\nva_name=VA Base\n'


def va_release():
    vac = c.credit(("Various Artists", c.VA, ""))
    a = c.artist("Björk")
    ac = c.credit(("Björk", a, ""))
    return c.release("profile-va", "Hits", vac, [c.track(1, "Hyperballad", ac, c.recording("Hyperballad", ac))],
                     secondary=("Compilation",))


class OptionProfile(unittest.TestCase):
    def setUp(self):
        self.ini = c.write_ini(PICARD_INI)
        picard_config._cache.clear()

    def test_profile_settings_win(self):
        s = picard_config.tag_settings(path=Path(self.ini))
        got = {k: s[k] for k in ("va_name", "standardize_artist_names", "max_genres")}
        self.assertEqual(got, {"va_name": "Diverse", "standardize_artist_names": "none", "max_genres": 4})

    def test_album_artist_of_a_va_release(self):
        s = picard_config.tag_settings(path=Path(self.ini))
        t = mb_tags.release_tags(va_release(), s)[0]
        self.assertEqual(t["albumartist"], ["Diverse"], "the enabled profile's Various Artists name")


if __name__ == "__main__":
    unittest.main()
