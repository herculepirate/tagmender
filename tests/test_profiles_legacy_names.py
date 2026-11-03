# Older setting names still work: a profile with the "picard" section, "follow_picard" or the
# naming preset "picard" loads as one with "tagger", "follow_tagger_settings" and "standard",
# behaves the same, and is saved with today's names only. The older environment variable for
# the tagger settings file is read when the new one is not set. Synthetic data only.
import os as _os, tempfile as _tempfile  # noqa: E402
_os.environ["XDG_CONFIG_HOME"] = _tempfile.mkdtemp(prefix="tm-config-")
_os.environ["XDG_STATE_HOME"] = _tempfile.mkdtemp(prefix="tm-state-")
_os.environ["TAGMENDER_PICARD_INI"] = "/nonexistent"
_os.environ.pop("TAGMENDER_TAGGER_INI", None)

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
import exclusions as ex  # noqa: E402
import naming  # noqa: E402
import profiles as pf  # noqa: E402
import tagger_options  # noqa: E402
import tagger_settings  # noqa: E402

OLD = {"name": "Old", "version": 2, "follow_picard": False,
       "picard": {"standardize_artist_names": "none", "release_ars": False},
       "naming": {"preset": "picard"}}
NEW = {"name": "Old", "version": 2, "follow_tagger_settings": False,
       "tagger": {"standardize_artist_names": "none", "release_ars": False},
       "naming": {"preset": "standard"}}


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.saved = (ex.CONFIG_DIR, ex.STATE_DIR)
        ex.CONFIG_DIR, ex.STATE_DIR = self.tmp / "config", self.tmp / "state"

    def tearDown(self):
        ex.CONFIG_DIR, ex.STATE_DIR = self.saved

    def write(self, data):
        path = pf.path_of(data["name"])
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data), encoding="utf-8")
        return path


class OldNames(Base):
    def test_old_names_load_as_new(self):
        p = pf.from_dict(OLD)
        self.assertNotIn("picard", p.data)
        self.assertNotIn("follow_picard", p.data)
        self.assertFalse(p.data["follow_tagger_settings"])
        self.assertEqual(p.data["tagger"]["standardize_artist_names"], "none")
        self.assertEqual(p.data["naming"]["preset"], "standard")

    def test_old_names_behave_the_same(self):
        old, new = pf.from_dict(OLD), pf.from_dict(NEW)
        self.assertEqual(old.to_dict(), new.to_dict())
        self.assertEqual(old.picard_tags(), new.picard_tags())
        self.assertEqual(old.picard_tags()["standardize_artist_names"], "none")
        self.assertFalse(old.follows_tagger())
        self.assertEqual(naming.effective_template(old["naming"]), naming.effective_template(new["naming"]))
        self.assertEqual(naming.effective_template(old["naming"], editions=False), naming.PRESETS["standard"])

    def test_saving_writes_only_new_names(self):
        self.write(OLD)
        pf.save(pf.load("Old"))
        text = pf.path_of("Old").read_text(encoding="utf-8")
        data = json.loads(text)
        self.assertNotIn("picard", data)
        self.assertNotIn("follow_picard", data)
        self.assertIs(data.get("follow_tagger_settings", pf.DEFAULTS["follow_tagger_settings"]), False)
        self.assertEqual(data["tagger"], {"standardize_artist_names": "none", "release_ars": False})
        self.assertEqual(data["naming"]["preset"], "standard")
        # (a file without a version keeps the earlier defaults: profiles.keep_v2_behaviour)
        self.assertEqual(pf.load("Old").to_dict(), pf.from_dict(pf.keep_v2_behaviour(NEW)).to_dict())

    def test_migrate_renames_a_current_version_file(self):
        self.write(OLD)
        changed, backup = pf.migrate("Old")
        self.assertTrue(changed)
        self.assertTrue(backup.exists())
        self.assertIn('"picard"', backup.read_text(encoding="utf-8"))          # (the backup as it was)
        data = json.loads(pf.path_of("Old").read_text(encoding="utf-8"))
        self.assertNotIn("picard", data)
        self.assertEqual(data["naming"]["preset"], "standard")
        self.assertEqual(pf.migrate("Old"), (False, None))                   # (nothing left to change)

    def test_new_name_wins_over_old(self):
        data = dict(NEW, picard={"standardize_artist_names": "all"}, follow_picard=True)
        p = pf.from_dict(data)
        self.assertEqual(p.data["tagger"]["standardize_artist_names"], "none")
        self.assertFalse(p.follows_tagger())

    def test_validation_names_the_new_setting(self):
        problems = pf.validate({"name": "x", "picard": {"standardize_artist_names": "bogus"}})
        self.assertTrue(any(x.startswith("tagger.standardize_artist_names") for x in problems), problems)
        problems = pf.validate({"name": "x", "tagger": "not an object"})
        self.assertIn("tagger must be an object of tagger settings", problems)
        self.assertFalse(any("picard" in x.lower() for x in problems), problems)

    def test_old_preset_name_in_naming(self):
        self.assertEqual(naming.template_for({"preset": "picard"}), naming.PRESETS["standard"])
        self.assertNotIn("picard", naming.PRESETS)

    def test_follow_flag_read_from_a_plain_dict(self):
        self.assertFalse(tagger_options._follows({"follow_picard": False}))
        self.assertFalse(tagger_options._follows({"follow_tagger_settings": False}))
        self.assertTrue(tagger_options._follows({}))

    def test_examples_use_new_names(self):
        for name in pf.install_examples():
            text = pf.path_of(name).read_text(encoding="utf-8").lower()
            self.assertNotIn("picard", text)


class SettingsFile(Base):
    def ini(self, folder, name):
        path = Path(folder) / name
        path.write_text("[setting]\nstandardize_artist_names=false\n", encoding="utf-8")
        return path

    def test_new_variable_first_old_as_fallback(self):
        d = tempfile.mkdtemp()
        new, old = self.ini(d, "new.ini"), self.ini(d, "old.ini")
        with mock.patch.dict(_os.environ, {"TAGMENDER_TAGGER_INI": str(new), "TAGMENDER_PICARD_INI": str(old)}):
            self.assertEqual(tagger_settings.ini_path(), new)
        with mock.patch.dict(_os.environ, {"TAGMENDER_PICARD_INI": str(old)}):
            _os.environ.pop("TAGMENDER_TAGGER_INI", None)
            self.assertEqual(tagger_settings.ini_path(), old)
        with mock.patch.dict(_os.environ, {"TAGMENDER_TAGGER_INI": "/nonexistent", "TAGMENDER_PICARD_INI": str(old)}):
            self.assertIsNone(tagger_settings.ini_path())

    def test_source_label_never_names_the_file(self):
        with mock.patch.dict(_os.environ, {"TAGMENDER_TAGGER_INI": "/nonexistent"}):
            self.assertEqual(tagger_settings.source_label(), "the plugin's defaults (no tagger settings found)")


if __name__ == "__main__":
    unittest.main()
