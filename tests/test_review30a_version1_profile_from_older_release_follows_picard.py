# Thirtieth independent review (A): a profile file saved by any release before review 28 (all
# of them wrote "version": 1) silently changes meaning: its unset Windows compatibility and
# Delete empty directories now follow Picard's settings, though the release that saved it used
# Windows-safe names and removed empty folders whatever Picard said.
#
# profiles.raw (profiles.py:426) now fills those two in only for files with no "version"
# (< 1); the comment says a version-1 file "is read as the release that saved it last read it:
# following Picard when unset". That holds only for the one release that followed Picard with
# schema 1 (review 28). Every earlier release also wrote "version": 1 (SCHEMA_VERSION was 1
# from review 12 on) and saved only what differed from DEFAULTS (minimal()), so their files
# never name filenames.compat or remove_empty_dirs - and they meant "windows" and True. With
# Picard's "Windows compatibility" off (common on Linux), the next run of such a move/copy
# profile names files differently - "AC/DC: Live?" folders and files get ":" and "?" instead
# of "_" - so every album with such a character is moved/renamed again (or copied a second
# time), and empty source folders stay behind. The previous version (version < 2) kept them.
# (Checked: review-27's profiles.minimal() writes {"version": 1, "name", "sources",
# "operation", "destination"} for a default move profile; its compat was "windows".)
# Regression: passes with TM_REPO=prev.
# Fix: a version-1 file is ambiguous - keep "< 2" (Windows-safe and remove, as all but one
# release meant), or tell the two apart (the review-28 release wrote a marker, or migrate()
# stamps the version-2 meaning explicitly when the user confirms).
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _review30a_common as c  # noqa: E402
import picard_config  # noqa: E402
import profiles as pf  # noqa: E402


class Version1Profile(unittest.TestCase):
    def setUp(self):
        ini = Path(tempfile.mkdtemp(prefix="tm30A-ini-")) / "Picard.ini"
        ini.write_text("[application]\nversion=3.0.2.final0\n\n[setting]\nwindows_compatibility=false\n"
                       "delete_empty_dirs=false\n", encoding="utf-8")
        self.old_ini = os.environ["TAGMENDER_PICARD_INI"]
        os.environ["TAGMENDER_PICARD_INI"] = str(ini)
        picard_config._cache.clear()

    def tearDown(self):
        os.environ["TAGMENDER_PICARD_INI"] = self.old_ini
        picard_config._cache.clear()

    def test_profile_saved_by_an_older_release_keeps_windows_names(self):
        tmp = Path(tempfile.mkdtemp(prefix="tm30A-prof-"))
        # as a release before review 28 saved a default move profile (profiles.minimal)
        data = {"version": 1, "name": "Library", "sources": [{"path": str(tmp / "in")}], "operation": "move",
                "destination": str(tmp / "lib")}
        path = pf.path_of("Library")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data), encoding="utf-8")
        p = pf.load("Library")
        self.assertEqual((p["filenames"]["compat"], p["remove_empty_dirs"]), ("windows", True),
                         "a profile file the older release used with Windows-safe names and empty "
                         "folders removed now follows Picard's settings")


if __name__ == "__main__":
    unittest.main()
