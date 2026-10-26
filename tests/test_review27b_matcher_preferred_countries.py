"""The matcher does not read Picard's "Preferred release countries" the way the tagger does
(match_albums.picard_preferred_countries, used by engine.Engine.__init__ (engine.py:165),
validate.py, inbox.py and match_albums' CLI):

1. It reads one fixed path, Flatpak's ~/.var/app/org.musicbrainz.Picard/config/MusicBrainz/
   Picard.ini - not $TAGMENDER_PICARD_INI nor picard_config.ini_path()'s other candidates
   (a native Picard's ~/.config/MusicBrainz/Picard.ini), which every other Picard setting
   (tag_settings, preserved tags, the RELEASECOUNTRY pick in mb_tags) is read from.
2. A list of ONE country - the usual case ("GB") - is written by Picard 3.0.2 (QSettings) in
   Qt's binary form "@Variant(...)"; the function returns [] for anything starting with "@".
3. An enabled option profile's countries are not read.

In every case the matcher falls back to TagMender's own order (CA, US, XW, XE, GB, DE): among
pressings that score the same (match_core.choose_pressing -> prefer_country) a UK user's GB
pressing loses to the US/CA one Picard would not prefer, while the RELEASECOUNTRY written
follows Picard's list. The Picard.ini texts below were written by Picard 3.0.2's own Config.
Fails with TM_REPO=prev too.
"""
import os
import tempfile
import unittest

import _review27b_common as c
import match_albums as ma
import picard_config

INI = {'one': '[application]\nversion=3.0.2.final0\n\n[setting]\npreferred_release_countries=@Variant(\\0\\0\\0\\t\\0\\0\\0\\x1\\0\\0\\0\\n\\0\\0\\0\\x4\\0G\\0\\x42)\n', 'two': '[application]\nversion=3.0.2.final0\n\n[setting]\npreferred_release_countries=GB, JP\n', 'prof': '[application]\nversion=3.0.2.final0\n\n[profiles]\nuser_profile_settings=@Variant(\\0\\0\\0\\b\\0\\0\\0\\x1\\0\\0\\0\\n\\0i\\0\\x64\\0-\\0U\\0S\\0\\0\\0\\b\\0\\0\\0\\x1\\0\\0\\0\\x36\\0p\\0r\\0\\x65\\0\\x66\\0\\x65\\0r\\0r\\0\\x65\\0\\x64\\0_\\0r\\0\\x65\\0l\\0\\x65\\0\\x61\\0s\\0\\x65\\0_\\0\\x63\\0o\\0u\\0n\\0t\\0r\\0i\\0\\x65\\0s\\0\\0\\0\\t\\0\\0\\0\\x2\\0\\0\\0\\n\\0\\0\\0\\x4\\0U\\0S\\0\\0\\0\\n\\0\\0\\0\\x4\\0X\\0\\x45)\nuser_profiles=@Variant(\\0\\0\\0\\t\\0\\0\\0\\x1\\0\\0\\0\\b\\0\\0\\0\\x4\\0\\0\\0\\xe\\0\\x65\\0n\\0\\x61\\0\\x62\\0l\\0\\x65\\0\\x64\\0\\0\\0\\x1\\x1\\0\\0\\0\\x4\\0i\\0\\x64\\0\\0\\0\\n\\0\\0\\0\\n\\0i\\0\\x64\\0-\\0U\\0S\\0\\0\\0\\x10\\0p\\0o\\0s\\0i\\0t\\0i\\0o\\0n\\0\\0\\0\\x2\\0\\0\\0\\0\\0\\0\\0\\n\\0t\\0i\\0t\\0l\\0\\x65\\0\\0\\0\\n\\0\\0\\0\\x4\\0U\\0S)\n\n[setting]\npreferred_release_countries=GB, JP\n'}


class PreferredCountries(unittest.TestCase):
    def setUp(self):
        self.home = os.environ.get("HOME")
        os.environ["HOME"] = tempfile.mkdtemp(prefix="tm27B-home-")      # (no Picard.ini of this machine)
        picard_config._cache.clear()

    def tearDown(self):
        if self.home is not None:
            os.environ["HOME"] = self.home
        os.environ["TAGMENDER_PICARD_INI"] = os.path.join(tempfile.mkdtemp(), "no-Picard.ini")
        picard_config._cache.clear()

    def test_reads_the_picard_ini_tagmender_uses(self):
        os.environ["TAGMENDER_PICARD_INI"] = str(c.write_ini(INI["two"]))
        self.assertEqual(picard_config.tag_settings()["preferred_release_countries"], ["GB", "JP"])
        self.assertEqual(ma.picard_preferred_countries(), ["GB", "JP"])

    def test_one_country(self):
        ini = c.write_ini(INI["one"])
        self.assertEqual(ma.picard_preferred_countries(str(ini)), ["GB"])

    def test_option_profile(self):
        ini = c.write_ini(INI["prof"])
        self.assertEqual(ma.picard_preferred_countries(str(ini)), ["US", "XE"])


if __name__ == "__main__":
    unittest.main()
