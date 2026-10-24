# Tests for profiles.py and `tagmender profiles`.
import os as _os, tempfile as _tempfile  # noqa: E402
_os.environ["XDG_CONFIG_HOME"] = _tempfile.mkdtemp(prefix="tm-config-")
_os.environ["XDG_STATE_HOME"] = _tempfile.mkdtemp(prefix="tm-state-")

import contextlib
import io
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
import exclusions as ex  # noqa: E402
import profiles as pf  # noqa: E402
import tagmender  # noqa: E402


def run(*argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            code = tagmender.main(list(argv))
        except SystemExit as e:
            code = e.code
    return code, out.getvalue(), err.getvalue()


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.saved = (ex.CONFIG_DIR, ex.STATE_DIR)
        ex.CONFIG_DIR, ex.STATE_DIR = self.tmp / "config", self.tmp / "state"
        _os.environ.pop("TAGMENDER_PROFILE", None)

    def tearDown(self):
        ex.CONFIG_DIR, ex.STATE_DIR = self.saved
        shutil.rmtree(self.tmp, ignore_errors=True)


class Model(Base):
    def test_defaults_fill_everything_unsaid(self):
        p = pf.from_dict({"name": "x"})
        self.assertEqual(p["operation"], "in_place")
        self.assertEqual(p["primary"], "discogs")
        self.assertTrue(p.provider_on("discogs"))
        self.assertFalse(p.provider_on("acoustid"))
        self.assertEqual(p.policy("album"), "replace")

    def test_lossy_example_copies_lossy_only_never_transcodes(self):
        pf.install_examples()
        p = pf.load("Lossy")
        self.assertEqual(p["operation"], "copy")
        self.assertEqual(p["destination"], "/media/media1/music/lossy")
        self.assertTrue(p.wants_format("/a/01.mp3"))
        self.assertTrue(p.wants_format("/a/01.OPUS"))
        self.assertFalse(p.wants_format("/a/01.flac"))
        self.assertFalse([k for k in p.to_dict() if "transcod" in k or "convert" in k])

    def test_examples_never_overwrite_a_users_profile(self):
        pf.save(pf.from_dict({"name": "Lossy", "operation": "copy", "destination": "/elsewhere"}))
        self.assertEqual(pf.install_examples(), [])
        self.assertEqual(pf.load("Lossy")["destination"], "/elsewhere")

    def test_problems_are_named(self):
        bad = {"name": "b", "operation": "move", "primary": "spotify",
               "thresholds": {"auto_min_confidence": 90, "organize_min_confidence": 50},
               "field_policy": {"default": "replace", "fields": {"album": "overwrite"}},
               "formats": ["xyz"], "unresolved": {"action": "move"}}
        problems = " | ".join(pf.validate(bad))
        for text in ("needs a destination", "primary must be", "organize_min_confidence must not be below",
                     "field_policy.fields.album", "unknown format 'xyz'", "needs unresolved.move_to"):
            self.assertIn(text, problems)
        with self.assertRaises(pf.ProfileError):
            pf.from_dict(bad)

    def test_bad_exclusion_rule_in_profile_is_a_problem(self):
        problems = pf.validate({"name": "r", "exclusions": [
            {"id": "x", "name": "x", "action": "skip", "target": "file",
             "when": {"all": [{"field": "nonsense", "op": "contains", "value": "a"}]}}]})
        self.assertTrue(any("unknown field" in p for p in problems))

    def test_saved_file_holds_only_what_differs(self):
        pf.save(pf.from_dict({"name": "m", "operation": "copy", "destination": "/d",
                              "thresholds": {"organize_min_confidence": 97}}))
        data = json.loads(pf.path_of("m").read_text())
        self.assertEqual(data["thresholds"], {"organize_min_confidence": 97})
        self.assertNotIn("providers", data)
        self.assertEqual(pf.load("m")["thresholds"]["auto_min_confidence"], 90.0)

    def test_matcher_cfg_takes_identify_thresholds(self):
        p = pf.from_dict({"name": "t", "thresholds": {"auto_min_confidence": 93, "auto_min_margin": 5}})
        cfg = p.matcher_cfg({"auto_min_confidence": 90.0, "auto_min_margin": 8.0, "w_artist": 20})
        self.assertEqual((cfg["auto_min_confidence"], cfg["auto_min_margin"], cfg["w_artist"]), (93.0, 5.0, 20))

    def test_deepest_source_wins_when_roots_overlap(self):
        pf.save(pf.from_dict({"name": "all", "sources": [{"path": "/music"}]}))
        pf.save(pf.from_dict({"name": "amb", "sources": [{"path": "/music/ambient"}]}))
        self.assertEqual(pf.for_path("/music/ambient/A/2000 - B").name, "amb")
        self.assertEqual(pf.for_path("/music/extreme/A").name, "all")
        self.assertIsNone(pf.for_path("/other"))
        self.assertIsNone(pf.for_path("/musical"))         # a name prefix is not inside


class Migration(Base):
    def test_rules_only_profile_from_earlier_versions_loads_and_migrates(self):
        old = {"name": "Old", "exclusions": [{"id": "live", "name": "Live", "action": "skip", "target": "album",
                                              "when": {"all": [{"field": "album", "op": "contains",
                                                                "value": "(live)"}]}}]}
        path = pf.path_of("Old")
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps(old))
        p = pf.load("Old")
        self.assertEqual(p["operation"], "in_place")
        self.assertEqual(p["exclusions"][0]["id"], "live")
        changed, backup = pf.migrate("Old")
        self.assertTrue(changed and backup.exists())
        self.assertEqual(json.loads(path.read_text())["version"], pf.SCHEMA_VERSION)
        self.assertEqual([r.id for r in ex.load_rules("Old") if r.scope == "Old"], ["live"])
        self.assertEqual(pf.migrate("Old"), (False, None))

    def test_newer_file_is_refused_not_misread(self):
        with self.assertRaises(pf.ProfileError):
            pf.from_dict({"name": "n", "version": 99})

    def test_rules_added_to_a_profile_keep_its_other_settings(self):
        pf.save(pf.from_dict({"name": "P", "operation": "copy", "destination": "/d"}))
        ex.save_rule(ex.Rule.from_dict({"id": "x", "name": "x", "action": "skip", "target": "file",
                                        "when": {"all": [{"field": "album", "op": "contains", "value": "y"}]}}),
                     "P")
        p = pf.load("P")
        self.assertEqual((p["operation"], p["destination"], len(p["exclusions"])), ("copy", "/d", 1))


class Cli(Base):
    def test_examples_list_show_check(self):
        self.assertEqual(run("profiles", "examples")[0], 0)
        code, out, _ = run("profiles", "list")
        self.assertIn("Lossy", out)
        self.assertIn("/media/media1/music/lossy", out)
        code, out, _ = run("profiles", "list", "--json")
        self.assertEqual(json.loads(out)[0]["operation"], "copy")
        code, out, _ = run("profiles", "show", "Lossy", "--full")
        self.assertEqual(json.loads(out)["providers"]["discogs"]["enabled"], True)
        self.assertEqual(run("profiles", "check")[0], 0)

    def test_new_and_set_validate_before_writing(self):
        code, _, err = run("profiles", "new", "Mine", "--set", "operation=move")
        self.assertEqual(code, 1)
        self.assertIn("needs a destination", err)
        self.assertFalse(pf.path_of("Mine").exists())
        self.assertEqual(run("profiles", "new", "Mine", "--set", "operation=move", "--set",
                             "destination=/media/x")[0], 0)
        self.assertEqual(run("profiles", "set", "Mine", "thresholds.organize_min_confidence=98",
                             'formats=["flac"]')[0], 0)
        p = pf.load("Mine")
        self.assertEqual((p["thresholds"]["organize_min_confidence"], p["formats"]), (98, ["flac"]))
        code, _, err = run("profiles", "set", "Mine", "operation=teleport")
        self.assertEqual(code, 1)
        self.assertEqual(pf.load("Mine")["operation"], "move")

    def test_check_reports_a_broken_file(self):
        pf.profiles_dir().mkdir(parents=True)
        pf.path_of("Broken").write_text("{not json")
        code, out, _ = run("profiles", "check")
        self.assertEqual(code, 1)
        self.assertIn("not valid JSON", out)


if __name__ == "__main__":
    unittest.main()
