# Twenty-eighth independent review (A): Windows compatibility and Delete empty directories
# now follow Picard.ini when a profile does not set them. A profile saved before (version 1),
# which left them out because they were the defaults, keeps those defaults - its file names
# never change by themselves - and a profile saved now keeps what it says.
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

os.environ["XDG_CONFIG_HOME"] = tempfile.mkdtemp(prefix="tm28A-config-")
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp(prefix="tm28A-state-")
INI = Path(tempfile.mkdtemp(prefix="tm28A-ini-")) / "Picard.ini"
INI.write_text("[application]\nversion=3.0.2.final0\n\n[setting]\nwindows_compatibility=false\n"
               "delete_empty_dirs=false\n")
os.environ["TAGMENDER_PICARD_INI"] = str(INI)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import picard_config  # noqa: E402
import profiles  # noqa: E402


class OldProfileDefaults(unittest.TestCase):
    def setUp(self):
        picard_config._cache.clear()

    def test_version_1_profile_keeps_the_old_defaults(self):
        path = profiles.path_of("Lossy")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"version": 1, "name": "Lossy"}))
        p = profiles.load("Lossy")
        self.assertEqual((p["filenames"]["compat"], p["remove_empty_dirs"]), ("windows", True))

    def test_new_profile_follows_picard_and_keeps_it_once_saved(self):
        p = profiles.from_dict({"name": "Fresh"})
        self.assertEqual((p["filenames"]["compat"], p["remove_empty_dirs"]), ("linux", False))
        saved = profiles.minimal(p)
        self.assertEqual((saved["filenames"]["compat"], saved["remove_empty_dirs"]), ("linux", False))


if __name__ == "__main__":
    unittest.main()
