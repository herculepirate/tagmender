# Tests for exclusions.py: Ignore/Skip rules.
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent   # the scripts sit above tests/
sys.path.insert(0, str(HERE))
import exclusions as ex  # noqa: E402


def audio(p, **tags):
    p.parent.mkdir(parents=True, exist_ok=True)
    meta = sum((["-metadata", f"{k}={v}"] for k, v in tags.items()), [])
    subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
                    *meta, str(p), "-y"], check=True)


def rule(rid, action="ignore", target="album", **cond):
    return ex.Rule.from_dict({"id": rid, "name": rid, "action": action, "target": target,
                              "when": cond.get("when") or {"all": [{"field": cond.get("field", "album"),
                                                                     "op": cond.get("op", "contains"),
                                                                     "value": cond["value"]}]}})


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.saved = (ex.CONFIG_DIR, ex.STATE_DIR)
        ex.CONFIG_DIR, ex.STATE_DIR = self.tmp / "config", self.tmp / "state"
        self.state = ex.State()

    def tearDown(self):
        ex.CONFIG_DIR, ex.STATE_DIR = self.saved
        shutil.rmtree(self.tmp, ignore_errors=True)


class Matching(Base):
    def test_brackets_are_literal_and_case_is_ignored(self):
        r = rule("nv", value="[no vox]")
        self.assertTrue(ex.matches(r.when, {"album": ["Colors [No Vox]"]}))
        self.assertTrue(ex.matches(r.when, {"album": ["COLORS [NO VOX]"]}))
        self.assertFalse(ex.matches(r.when, {"album": ["Colors no vox"]}))      # no brackets: not the same text
        self.assertFalse(ex.matches(r.when, {"album": ["Colors [n]"]}))          # not a character class
        self.assertFalse(ex.matches(r.when, {"album": ["Colors"]}))

    def test_glob_and_regex_only_when_chosen(self):
        g = rule("g", op="glob", value="*[no vox]*")                            # glob: [..] is a set here
        self.assertTrue(ex.matches(g.when, {"album": ["x n y"]}))
        rx = rule("r", op="regex", value=r"\bno[ ._-]?vox\b")
        self.assertTrue(ex.matches(rx.when, {"album": ["Immutable (noVox)"]}))
        cs = ex.Rule.from_dict({"id": "c", "name": "c", "action": "skip", "target": "file",
                                "when": {"field": "title", "op": "equals", "value": "Intro", "case_sensitive": True}})
        self.assertFalse(ex.matches(cs.when, {"title": ["intro"]}))
        self.assertTrue(ex.matches(cs.when, {"title": ["Intro"]}))

    def test_and_or_and_not(self):
        r = ex.Rule.from_dict({"id": "x", "name": "x", "action": "skip", "target": "file", "when": {"all": [
            {"field": "format", "op": "equals", "value": "mp3"},
            {"any": [{"field": "genre", "op": "contains", "value": "podcast"},
                     {"field": "path", "op": "contains", "value": "/audiobooks/"}]},
            {"field": "artist", "op": "equals", "value": "Me", "not": True}]}})
        self.assertTrue(ex.matches(r.when, {"format": ["mp3"], "genre": ["Podcast"], "artist": ["Other"]}))
        self.assertFalse(ex.matches(r.when, {"format": ["flac"], "genre": ["Podcast"], "artist": ["Other"]}))
        self.assertFalse(ex.matches(r.when, {"format": ["mp3"], "genre": ["Podcast"], "artist": ["Me"]}))

    def test_bad_rules_are_refused(self):
        for bad in ({"field": "nope", "op": "contains", "value": "x"},
                    {"field": "album", "op": "like", "value": "x"},
                    {"field": "album", "op": "regex", "value": "(["}):
            with self.assertRaises(ex.RuleError):
                ex.Rule.from_dict({"id": "b", "name": "b", "action": "skip", "target": "file", "when": bad})

    def test_default_rule_replaces_the_built_in_no_vox(self):
        rules = ex.load_rules()
        self.assertEqual([r.id for r in rules], ["no-vox"])
        self.assertTrue((ex.CONFIG_DIR / "exclusions.json").exists())
        self.assertTrue(ex.set_enabled("no-vox", False))
        self.assertEqual(ex.load_rules(), [])
        self.assertEqual(len(ex.load_rules(include_disabled=True)), 1)


class Albums(Base):
    def test_album_wide_exclusion(self):
        d = self.tmp / "m" / "BTBAM" / "2007 - Colors"
        audio(d / "01.flac", album="Colors [No Vox]", albumartist="BTBAM", title="Foam Born")
        audio(d / "02.flac", album="Colors [No Vox]", albumartist="BTBAM", title="Informal Gluttony")
        dec = ex.evaluate(ex.audio_files(d), [rule("nv", value="[no vox]")])
        self.assertTrue(dec.all_excluded)
        self.assertEqual({e.label for e in dec.excluded.values()}, {"Ignored"})

    def test_mixed_folder_excludes_only_that_album(self):
        d = self.tmp / "m" / "Unsorted"
        audio(d / "a.mp3", album="Colors [No Vox]", albumartist="BTBAM")
        audio(d / "b.mp3", album="Colors [No Vox]", albumartist="BTBAM")
        audio(d / "c.mp3", album="Alaska", albumartist="BTBAM")
        dec = ex.evaluate(ex.audio_files(d), [rule("nv", value="[no vox]")])
        self.assertEqual(sorted(p.name for p in dec.excluded), ["a.mp3", "b.mp3"])
        self.assertEqual([p.name for p in dec.kept], ["c.mp3"])

    def test_file_rule_only_takes_the_file(self):
        d = self.tmp / "m" / "A" / "2001 - B"
        audio(d / "01.flac", album="B", title="Intro")
        audio(d / "02.flac", album="B", title="Song")
        dec = ex.evaluate(ex.audio_files(d), [rule("intro", "skip", "file", field="title", op="equals", value="intro")])
        self.assertEqual(list(p.name for p in dec.excluded), ["01.flac"])
        self.assertEqual(dec.excluded[d / "01.flac"].label, "Skipped")

    def test_rule_matching_metadata_found_by_identification(self):
        d = self.tmp / "m" / "Band" / "2015 - Coma Ecliptic"
        audio(d / "01.flac")                                                    # no tags at all
        r = [rule("nv", value="[no vox]")]
        self.assertFalse(ex.evaluate(ex.audio_files(d), r).excluded)            # nothing to go on yet
        dec = ex.evaluate(ex.audio_files(d), r, release={"album": ["Coma Ecliptic [No Vox]"]})
        self.assertTrue(dec.all_excluded)
        self.assertEqual(dec.summary().where, "identified release")

    def test_process_anyway(self):
        d = self.tmp / "m" / "B" / "2007 - Colors"
        audio(d / "01.flac", album="Colors [No Vox]")
        r = [rule("nv", value="[no vox]")]
        self.assertTrue(ex.gate(d, "job1", r, state=self.state).all_excluded)
        self.assertFalse(ex.gate(d, "job2", r, process_anyway=True, state=self.state).excluded)
        self.state.allow(d)                                                     # remembered override
        self.assertFalse(ex.gate(d, "job3", r, state=self.state).excluded)
        self.state.disallow(d)
        self.assertTrue(ex.gate(d, "job4", r, state=self.state).all_excluded)


class Watching(Base):
    def test_unchanged_ignored_album_is_not_queued_again(self):
        d = self.tmp / "inbox" / "Colors"
        audio(d / "01.flac", album="Colors [No Vox]")
        r = [rule("nv", value="[no vox]")]
        files = ex.audio_files(d)
        ex.gate(d, "watch-1", r, state=self.state)
        sig, rh = ex.signature(files), ex.rules_hash(r)
        self.assertEqual(self.state.still_ignored(d, sig, rh), "nv")
        # the rules change: look again
        r2 = [rule("nv", value="[instrumental]")]
        self.assertIsNone(self.state.still_ignored(d, sig, ex.rules_hash(r2)))
        # the files change (retagged): look again
        audio(d / "01.flac", album="Colors")
        os.utime(d / "01.flac", (1, 1))
        self.assertIsNone(self.state.still_ignored(d, ex.signature(ex.audio_files(d)), rh))

    def test_skips_are_not_remembered_but_are_in_the_history(self):
        d = self.tmp / "inbox" / "Podcast"
        audio(d / "01.mp3", album="Show", genre="Podcast")
        r = [rule("pod", "skip", "album", field="genre", value="podcast")]
        ex.gate(d, "watch-1", r, state=self.state)
        self.assertEqual(self.state.ignored(), [])
        self.assertEqual(self.state.history()[0][3], "Skipped")


class Profiles(Base):
    def test_profile_rules_add_to_global_ones(self):
        ex.save_rule(rule("live", "skip", "album", value="(live)"), scope="Lossy")
        self.assertEqual([r.id for r in ex.load_rules()], ["no-vox"])
        self.assertEqual([r.id for r in ex.load_rules("Lossy")], ["no-vox", "live"])
        os.environ["TAGMENDER_PROFILE"] = "Lossy"
        try:
            self.assertEqual(len(ex.load_rules()), 2)
        finally:
            del os.environ["TAGMENDER_PROFILE"]
        self.assertTrue(ex.remove_rule("live", "Lossy"))


if __name__ == "__main__":
    unittest.main()
