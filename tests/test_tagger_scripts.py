"""Picard's tagger scripts (Options > Scripting) and the deletions Picard makes."""
import os as _os, tempfile as _tempfile  # noqa: E402
_os.environ.setdefault("XDG_CONFIG_HOME", _tempfile.mkdtemp(prefix="tm-config-"))
_os.environ.setdefault("XDG_STATE_HOME", _tempfile.mkdtemp(prefix="tm-state-"))
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))
from test_engine import Base, FakeHttp, mb_release  # noqa: E402
import engine  # noqa: E402
import executor  # noqa: E402
import tagio  # noqa: E402

MBID = "0a8e97fd-4f6a-4bd7-8e4b-6d3a0b2ad2f1"


class Scripts(Base):
    def run_plan(self, picard, **extra):
        d = self.album("Band", "Album", **extra)
        rel = mb_release(MBID, "Band", "Album")
        rel["disambiguation"] = "deluxe"
        e = self.engine(FakeHttp(mb=[rel]), picard=picard)
        e.force = {_os.path.realpath(d): MBID}
        return d, e.plan(d)

    def target_of(self, p, n=0):
        return p["albums"][0]["files"][n]["changes"]

    def test_scripts_run_with_picards_variables(self):
        script = "$set(album,%album% [%_releasecomment%])$set(comment,%_absolutetracknumber%/%_totalalbumtracks%)"
        d, p = self.run_plan({"enable_tagger_scripts": True, "list_of_scripts": [[0, "x", True, script]]})
        ch = self.target_of(p, 1)
        self.assertEqual(ch["album"], ["Album [deluxe]"])
        self.assertEqual(ch["comment"], ["2/4"])               # (the DVD's video track counts too)

    def test_disabled_scripts_do_not_run(self):
        script = "$set(album,X)"
        d, p = self.run_plan({"enable_tagger_scripts": False, "list_of_scripts": [script]})
        self.assertNotIn("album", self.target_of(p) or {})
        d, p = self.run_plan({"enable_tagger_scripts": True, "list_of_scripts": [[0, "x", False, script]]})
        self.assertNotIn("album", self.target_of(p) or {})

    def test_delete_removes_the_tag(self):
        d, p = self.run_plan({"enable_tagger_scripts": True, "list_of_scripts": ["$delete(label)"]},
                             label="Old Label", mood="calm")
        ch = self.target_of(p)
        self.assertIn("label", ch)
        self.assertIsNone(ch["label"])
        res = executor.Executor(p).run()
        self.assertEqual(res["counts"], {"done": 1}, res)
        self.assertNotIn("label", tagio.read(sorted(d.glob("*.flac"))[0]))

    def test_broken_script_is_skipped(self):
        d, p = self.run_plan({"enable_tagger_scripts": True, "list_of_scripts": ["$set(album", "$set(genre,Jazz)"]})
        self.assertEqual(self.target_of(p).get("genre"), ["Jazz"])

    def test_compilation_flag_removed_on_a_release_by_one_artist(self):
        d, p = self.run_plan({}, compilation="1")
        self.assertIsNone(self.target_of(p).get("compilation", "absent"))

    def test_compilation_flag_kept_when_preserved(self):
        d, p = self.run_plan({}, compilation="1")
        self.assertIsNone(self.target_of(p).get("compilation", "absent"))
        prof_policy = {"field_policy": {"default": "replace", "fields": {"compilation": "preserve"}}}
        d2 = self.album("Band", "Album", compilation="1", folder="2005 - Album (2)")
        e = self.engine(FakeHttp(mb=[mb_release(MBID, "Band", "Album")]), **prof_policy)
        e.force = {_os.path.realpath(d2): MBID}
        a = e.plan(d2)["albums"][0]
        self.assertNotIn("compilation", a["files"][0]["changes"] or {})


if __name__ == "__main__":
    unittest.main()
