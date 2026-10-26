"""Picard's "Preserve timestamps of tagged files" set in an enabled option profile is not
followed: profiles.Profile.preserve_timestamps() (profile "preserve_timestamps": null, the
default - "as Picard is set") reads it with picard_config.picard_bool, which looks at
[setting] only. Last round taught tag_settings/preserved_tags to read Picard 3's option
profiles (docs/PICARD_COVERAGE.md: "Picard 3's enabled option profiles override its settings
as in Picard"; "Preserve timestamps ... follows Picard's setting"), but not this setting.

PICARD_INI was written by Picard 3.0.2's own Config: [setting] preserve_timestamps=false,
one enabled profile "Keep" with preserve_timestamps true. Picard reads True
(config.setting["preserve_timestamps"]) and keeps each saved file's access and modification
times (File._save_and_rename -> _preserve_times). TagMender plans preserve_timestamps False:
every file it tags gets a new modification time (players and backup tools see the whole
library as changed). Fails with TM_REPO=prev too.
"""
import os
import unittest
from pathlib import Path

import _review27b_common as c
import picard_config
import plan as pl

PICARD_INI = '[application]\nversion=3.0.2.final0\n\n[profiles]\nuser_profile_settings=@Variant(\\0\\0\\0\\b\\0\\0\\0\\x1\\0\\0\\0\\xe\\0i\\0\\x64\\0-\\0K\\0\\x65\\0\\x65\\0p\\0\\0\\0\\b\\0\\0\\0\\x1\\0\\0\\0&\\0p\\0r\\0\\x65\\0s\\0\\x65\\0r\\0v\\0\\x65\\0_\\0t\\0i\\0m\\0\\x65\\0s\\0t\\0\\x61\\0m\\0p\\0s\\0\\0\\0\\x1\\x1)\nuser_profiles=@Variant(\\0\\0\\0\\t\\0\\0\\0\\x1\\0\\0\\0\\b\\0\\0\\0\\x4\\0\\0\\0\\xe\\0\\x65\\0n\\0\\x61\\0\\x62\\0l\\0\\x65\\0\\x64\\0\\0\\0\\x1\\x1\\0\\0\\0\\x4\\0i\\0\\x64\\0\\0\\0\\n\\0\\0\\0\\xe\\0i\\0\\x64\\0-\\0K\\0\\x65\\0\\x65\\0p\\0\\0\\0\\x10\\0p\\0o\\0s\\0i\\0t\\0i\\0o\\0n\\0\\0\\0\\x2\\0\\0\\0\\0\\0\\0\\0\\n\\0t\\0i\\0t\\0l\\0\\x65\\0\\0\\0\\n\\0\\0\\0\\b\\0K\\0\\x65\\0\\x65\\0p)\n\n[setting]\npreserve_timestamps=false\n'


class PreserveTimestampsFromProfile(unittest.TestCase):
    def setUp(self):
        self.ini = c.write_ini(PICARD_INI)
        os.environ["TAGMENDER_PICARD_INI"] = str(self.ini)
        picard_config._cache.clear()

    def tearDown(self):
        os.environ["TAGMENDER_PICARD_INI"] = os.path.join(os.path.dirname(str(self.ini)), "gone", "Picard.ini")
        picard_config._cache.clear()

    def test_profile_says_preserve(self):
        files = c.album(".mp3", 1)
        prof = c.profile(Path(files[0]).parent.parent)
        self.assertTrue(prof.preserve_timestamps(), "Picard's enabled profile turns Preserve timestamps on")

    def test_tagged_file_keeps_its_time(self):
        files = c.album(".mp3", 1)
        os.utime(files[0], ns=(1_000_000_000_000_000_000, 1_000_000_000_000_000_000))
        c.fresh_state()
        prof = c.profile(Path(files[0]).parent.parent)
        p = pl.build([c.proposal(files)], prof)
        res = c.executor.Executor(p).run()
        self.assertEqual(res.get("counts"), {"done": 1}, c.detail(p, res))
        self.assertEqual(os.stat(files[0]).st_mtime_ns, 1_000_000_000_000_000_000)


if __name__ == "__main__":
    unittest.main()
