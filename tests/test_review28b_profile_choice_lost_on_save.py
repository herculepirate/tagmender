"""A profile's own "remove_empty_dirs" / "filenames.compat" is dropped when the profile is
saved while Picard.ini happens to agree with it - the profile then follows Picard again, so
a later change in Picard overrides the choice the profile made.

Last round's fix (profiles.py:390 _picard_defaults) made those two defaults follow
Picard.ini, and profiles.minimal (profiles.py:452-453) now writes only what differs from
those Picard-derived defaults. A value equal to Picard's current setting is therefore not
written: {"remove_empty_dirs": false} saved while Picard's "Delete empty directories" is off
becomes {} on disk. When Picard's option is turned on later (or Picard.ini is reset, or the
profile is used on another machine), the profile deletes empty folders it was set never to
delete; likewise a profile set to Windows-compatible names loses it while Picard has Windows
compatibility on, and gets Linux names once Picard's is turned off. docs/PICARD_COVERAGE.md:
"follows Picard's setting unless the profile sets remove_empty_dirs" / "filenames.compat".
`profiles.migrate`, `install_examples` and every save from the app go through minimal().

Regression: passes with TM_REPO=prev (defaults there were fixed, so false / a non-default
was always written).
"""
import json
import os
import unittest

import _review28b_common as c
import picard_config
import profiles as pf


def ini(**settings):
    body = "".join(f"{k}={v}\n" for k, v in settings.items())
    p = c.write_ini("[application]\nversion=3.0.2.final0\n\n[setting]\n" + body)
    os.environ["TAGMENDER_PICARD_INI"] = str(p)
    picard_config._cache.clear()


class ProfileChoiceLostOnSave(unittest.TestCase):
    def setUp(self):
        c.fresh_state()

    def tearDown(self):
        os.environ["TAGMENDER_PICARD_INI"] = "/nonexistent/tm28B/Picard.ini"
        picard_config._cache.clear()

    def test_remove_empty_dirs_false_kept(self):
        ini(delete_empty_dirs="false")
        path = pf.save(pf.from_dict({"name": "keepdirs", "remove_empty_dirs": False}))
        ini(delete_empty_dirs="true")                    # Picard's option turned on later
        self.assertIs(pf.load("keepdirs")["remove_empty_dirs"], False,
                      f"the profile said never delete empty folders; saved as {json.loads(path.read_text())}")

    def test_windows_compat_kept(self):
        ini(windows_compatibility="true")
        path = pf.save(pf.from_dict({"name": "win", "filenames": {"compat": "windows"}}))
        ini(windows_compatibility="false")
        self.assertEqual(pf.load("win")["filenames"]["compat"], "windows",
                         f"the profile said Windows-compatible names; saved as {json.loads(path.read_text())}")


if __name__ == "__main__":
    unittest.main()
