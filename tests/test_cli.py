# Tests for tagmender.py, the single command (rules management and the rule tester).
import os as _os, tempfile as _tempfile  # noqa: E402
_os.environ["XDG_CONFIG_HOME"] = _tempfile.mkdtemp(prefix="tm-config-")
_os.environ["XDG_STATE_HOME"] = _tempfile.mkdtemp(prefix="tm-state-")

import contextlib
import io
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent   # the scripts sit above tests/
sys.path.insert(0, str(HERE))
import exclusions as ex  # noqa: E402
import tagmender  # noqa: E402


def audio(p, **tags):
    p.parent.mkdir(parents=True, exist_ok=True)
    meta = sum((["-metadata", f"{k}={v}"] for k, v in tags.items()), [])
    subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
                    *meta, str(p), "-y"], check=True)


def run(*argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            code = tagmender.main(list(argv))
        except SystemExit as e:
            code = e.code
    return code, out.getvalue(), err.getvalue()


class Cli(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.saved = (ex.CONFIG_DIR, ex.STATE_DIR)
        ex.CONFIG_DIR, ex.STATE_DIR = self.tmp / "config", self.tmp / "state"
        _os.environ.pop("TAGMENDER_PROFILE", None)

    def tearDown(self):
        ex.CONFIG_DIR, ex.STATE_DIR = self.saved
        _os.environ.pop("TAGMENDER_PROFILE", None)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_list_shows_the_default_rule(self):
        code, out, _ = run("rules", "list")
        self.assertEqual(code, 0)
        self.assertIn("no-vox", out)
        code, out, _ = run("rules", "list", "--json")
        self.assertEqual(json.loads(out)[0]["id"], "no-vox")

    def test_add_disable_enable_remove(self):
        code, out, _ = run("rules", "add", "--name", "Live albums", "--action", "skip", "--target", "album",
                           "--cond", "album", "contains", "(live)", "--cond", "folder", "contains", "[live]", "--any")
        self.assertEqual(code, 0, out)
        r = [r for r in ex.load_rules() if r.id == "live-albums"][0]
        self.assertEqual(r.when, {"any": [{"field": "album", "op": "contains", "value": "(live)"},
                                          {"field": "folder", "op": "contains", "value": "[live]"}]})
        self.assertEqual(run("rules", "disable", "live-albums")[0], 0)
        self.assertNotIn("live-albums", [r.id for r in ex.load_rules()])
        self.assertEqual(run("rules", "enable", "live-albums")[0], 0)
        self.assertEqual(run("rules", "remove", "live-albums")[0], 0)
        self.assertEqual(run("rules", "remove", "live-albums")[0], 1)

    def test_bad_rule_is_refused(self):
        code, _, err = run("rules", "add", "--name", "x", "--cond", "album", "regex", "([")
        self.assertEqual(code, 2)
        self.assertIn("Not added", err)
        code, _, _ = run("rules", "add", "--name", "x", "--cond", "colour", "contains", "red")
        self.assertEqual(code, 2)

    def test_profile_rules_and_disabling_a_global_rule_from_a_profile(self):
        run("--profile", "Lossy", "rules", "add", "--name", "mp3", "--cond", "format", "equals", "mp3")
        _os.environ.pop("TAGMENDER_PROFILE", None)                        # the command sets it for its tools
        self.assertEqual([r.id for r in ex.load_rules("Lossy")], ["no-vox", "mp3"])
        self.assertEqual([r.id for r in ex.load_rules()], ["no-vox"])
        code, out, _ = run("--profile", "Lossy", "rules", "disable", "no-vox")   # falls back to the global rule
        self.assertIn("[global]", out)

    def test_rule_tester_previews_and_changes_nothing(self):
        lib = self.tmp / "lib"
        audio(lib / "BTBAM" / "Colors" / "01.flac", album="Colors [No Vox]", albumartist="BTBAM")
        audio(lib / "BTBAM" / "Alaska" / "01.flac", album="Alaska", albumartist="BTBAM")
        audio(lib / "Mixed" / "a.mp3", album="Colors [No Vox]", albumartist="BTBAM")
        audio(lib / "Mixed" / "b.mp3", album="Alaska", albumartist="BTBAM")
        before = {p: p.stat().st_mtime for p in lib.rglob("*.*")}
        code, out, _ = run("rules", "test", str(lib), "--json")
        rep = json.loads(out)
        self.assertEqual((rep["files"], rep["excluded"]), (4, 2))
        by = {Path(f["folder"]).name: f for f in rep["folders"]}
        self.assertEqual(by["Colors"]["album"], "Ignored by no-vox")
        self.assertEqual(by["Mixed"]["album"], "partly excluded")
        self.assertEqual([i["file"] for i in by["Mixed"]["items"]], ["a.mp3"])
        self.assertNotIn("Alaska", by)
        self.assertEqual(before, {p: p.stat().st_mtime for p in lib.rglob("*.*")})
        self.assertEqual(ex.State().history(), [])                       # a preview records nothing
        # a disabled rule can still be tried out
        run("rules", "disable", "no-vox")
        rep = json.loads(run("rules", "test", str(lib), "--json", "--rule", "no-vox")[1])
        self.assertEqual(rep["excluded"], 2)

    def test_allow_is_exact_about_folders(self):
        lib = self.tmp / "lib"
        audio(lib / "Colors" / "01.flac", album="Colors [No Vox]")
        audio(lib / "Colors 2" / "01.flac", album="Colors II [No Vox]")
        cwd = _os.getcwd()
        _os.chdir(lib)
        try:
            run("rules", "allow", "Colors")                               # relative path is resolved
        finally:
            _os.chdir(cwd)
        rep = json.loads(run("rules", "test", str(lib), "--json")[1])
        self.assertEqual([Path(f["folder"]).name for f in rep["folders"]], ["Colors 2"])
        run("rules", "disallow", str(lib / "Colors"))
        rep = json.loads(run("rules", "test", str(lib), "--json")[1])
        self.assertEqual(rep["excluded"], 2)

    def test_tools_are_passed_through(self):
        code, out, _ = run("health", "--help")
        self.assertEqual(code, 0)
        self.assertIn("usage", out.lower())


if __name__ == "__main__":
    unittest.main()
