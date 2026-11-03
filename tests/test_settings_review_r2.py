# Settings, second review: an earlier profile still loads and does what it did (thresholds below
# today's floor read as the floor, 0 album words read as 5, English artist names, formats by
# extension, the built-in skip patterns never refuse its own folder, a dict kept in an older plan
# read as its file is), migrate never leaves a backup of a profile it cannot upgrade and carries
# on with the others, the watcher says which profile it skips, unjoined deluxe folders stay out
# of the album, Basic still removes comments, the notes give the threshold used, a lossy library
# is tidied in place, Discogs refusing while MusicBrainz is down leaves the album for a retry, and
# a machine's settings fill in what the machine started as.
import os as _os, tempfile as _tempfile  # noqa: E402
_os.environ["XDG_CONFIG_HOME"] = _tempfile.mkdtemp(prefix="tm-config-")
_os.environ["XDG_STATE_HOME"] = _tempfile.mkdtemp(prefix="tm-state-")

import io
import json
import logging
import sys
import types
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "tests"))

from test_settings_schema3 import Base  # noqa: E402

import app_settings  # noqa: E402
import engine  # noqa: E402
import levels  # noqa: E402
import match_albums as ma  # noqa: E402
import naming  # noqa: E402
import plan as planmod  # noqa: E402
import profiles as pf  # noqa: E402


class EarlierProfiles(Base):
    def test_thresholds_below_the_floor_are_read_as_the_floor(self):
        self.write_profile({"version": 2, "name": "Old",
                            "thresholds": {"auto_min_confidence": 85, "organize_min_confidence": 92}})
        p = pf.load("Old")                                   # (refused before)
        self.assertEqual(p["thresholds"]["auto_min_confidence"], 90)
        self.assertEqual(p["thresholds"]["organize_min_confidence"], 95)
        notes = pf.raised_thresholds({"version": 2, "thresholds": {"auto_min_confidence": 85}})
        self.assertEqual(notes, ["thresholds.auto_min_confidence 85 is read as 90, the lowest allowed"])
        self.assertTrue(any("must not be below" in x
                            for x in pf.validate({"name": "New", "thresholds": {"auto_min_confidence": 85}})))
        import tagmender
        out = io.StringIO()
        with redirect_stdout(out):
            self.assertEqual(tagmender.main(["profiles", "check", "Old"]), 0)
        self.assertIn("is read as 90", out.getvalue())

    def test_a_dict_kept_in_an_older_plan_reads_as_its_file(self):
        old = {"name": "Old", "version": 2, "operation": "in_place"}
        self.write_profile(old)
        self.assertEqual(pf.from_dict(dict(old), check=False).to_dict(), pf.load("Old").to_dict())
        p = pf.from_dict({"name": "Old", "version": 1})
        self.assertEqual(p["filenames"]["compat"], "windows")
        self.assertTrue(p["remove_empty_dirs"])

    def test_zero_album_words_was_five(self):
        p = pf.from_dict({"name": "Old", "version": 2, "filenames": {"max_album_words": 0}})
        self.assertEqual(p["filenames"]["max_album_words"], 5)
        self.assertEqual(pf.from_dict({"name": "New", "filenames": {"max_album_words": 0}})
                         ["filenames"]["max_album_words"], 0)

    def test_translated_artist_names_stay_english(self):
        data = {"name": "Old", "version": 2, "follow_tagger_settings": False,
                "tagger": {"translate_artist_names": True}}
        with mock.patch.object(pf, "system_language", return_value="de"):
            self.assertEqual(pf.from_dict(data).tagger_overrides()["translation_locales"], ["en"])
            new = dict(data, version=3)
            self.assertEqual(pf.from_dict(new).tagger_overrides()["translation_locales"], ["de"])
        self.assertNotIn("tagger", pf.keep_v2_behaviour({"name": "Plain", "version": 2}))

    def test_alac_and_aac_by_extension_only_for_an_earlier_profile(self):
        with mock.patch.object(pf, "audio_kind", side_effect=lambda p: "lossless" if "alac" in str(p) else "lossy"):
            old = pf.from_dict({"name": "Old", "version": 2, "formats": ["flac", "alac", "wav"]})
            self.assertFalse(old.wants_format("/x/alac.m4a"))
            self.assertTrue(old.wants_format("/x/a.flac"))
            self.assertFalse(pf.from_dict({"name": "Old", "version": 2, "formats": ["aac"]}).wants_format("/x/b.m4a"))
            new = pf.from_dict({"name": "New", "formats": ["flac", "alac"]})
            self.assertTrue(new.wants_format("/x/alac.m4a"))
            self.assertTrue(pf.from_dict({"name": "New", "formats": ["lossless"]}).wants_format("/x/alac.m4a"))

    def test_the_built_in_skip_patterns_never_refuse_the_profiles_own_folder(self):
        for v in (2, 3):
            data = {"name": "R", "version": v, "sources": [{"path": str(self.tmp / "lib" / "_unresolved")}]}
            self.assertEqual([x for x in pf.validate(pf.from_dict(data, check=False).data) if "exclude_paths" in x],
                             [], v)
        mine = {"name": "R", "sources": [{"path": str(self.tmp / "lib" / "Downloads")}],
                "exclude_paths": ["*/Downloads/*"]}
        self.assertTrue(any("own folder" in x for x in pf.validate(mine)))


class Migrate(Base):
    def test_a_profile_that_cannot_be_upgraded_is_left_alone_and_the_rest_go_on(self):
        self.write_profile({"version": 2, "name": "A1"})
        self.write_profile({"version": 2, "name": "T2", "operation": "copy"})      # (no destination)
        self.write_profile({"version": 2, "name": "Z9"})
        import tagmender
        out = io.StringIO()
        with redirect_stdout(out):
            self.assertEqual(tagmender.main(["profiles", "migrate"]), 1)
        self.assertIn("T2: not upgraded", out.getvalue())
        self.assertEqual(json.loads(pf.path_of("Z9").read_text())["version"], pf.SCHEMA_VERSION)
        self.assertEqual(json.loads(pf.path_of("T2").read_text())["version"], 2)
        self.assertEqual(list(pf.profiles_dir().glob("T2.json.bak-*")), [])

    def test_the_watcher_says_which_profile_it_skips(self):
        import watcher
        self.write_profile({"version": 3, "name": "Bad", "operation": "copy"})
        log = logging.getLogger("tm-test-watch")
        with self.assertLogs(log, "WARNING") as seen:
            self.assertEqual(watcher.Watcher._watched_profiles(log), [])
        self.assertIn("Bad", seen.output[0])


class DeluxeNotJoined(Base):
    def test_the_bonus_folder_stays_out_of_the_album(self):
        lib = self.tmp / "lib"
        main, bonus = lib / "2019 - Heretics", lib / "2019 - Heretics (Deluxe Edition)"
        for d, n in ((main, "01.flac"), (bonus, "04.flac")):
            d.mkdir(parents=True)
            (d / n).write_bytes(b"x")
        e = object.__new__(engine.Engine)
        g = engine.Group(main, [main / "01.flac"], [])
        with mock.patch.object(ma, "deluxe_sibling", return_value=bonus):
            e.profile = pf.from_dict({"name": "D", "grouping": {"join_deluxe_folders": False}})
            self.assertEqual(e._outside(g), [bonus / "04.flac"])
            e.profile = pf.from_dict({"name": "D"})
            self.assertEqual(e._outside(g), [])


class Notes(Base):
    def test_basic_still_removes_comments(self):
        for level in ("basic", "enthusiast"):
            p = pf.from_dict({"name": "C", "tag_level": level, "cleanup": {"comments": "remove"}})
            t = levels.restrict(engine.own_tag_choices({"title": ["x"]}, p), p)
            self.assertEqual(t.get("~remove"), ["comment"], level)
        p = pf.from_dict({"name": "C", "tag_level": "basic", "cleanup": {"comments": "keep"}})
        self.assertFalse(levels.restrict({"title": ["x"], "~remove": ["comment"]}, p).get("~remove"))

    def test_the_note_gives_the_threshold_used(self):
        p = pf.from_dict({"name": "E", "matching": {"extra_careful": True}})
        self.assertEqual(planmod._organize_floor_words(p), "98 (Extra-careful matching)")
        self.assertEqual(planmod._organize_floor_words(pf.from_dict({"name": "E"})), "95")

    def test_an_album_in_the_lossy_library_is_tidied_in_place(self):
        main, lossy = self.tmp / "main", self.tmp / "lossy"
        p = pf.from_dict({"name": "M", "operation": "move", "destination": str(main),
                          "destinations": {"lossy": str(lossy)}}, check=False)
        obj = planmod.Planner.__new__(planmod.Planner)
        obj.profile = p
        for lib, ext in ((main, "flac"), (lossy, "mp3")):
            f = str(lib / "A" / "B" / f"01.{ext}")
            self.assertTrue(obj._tidies_here({"files": [{"src": f, "dst": f}]}), lib)
        f = str(self.tmp / "inbox" / "A" / "01.mp3")
        self.assertFalse(obj._tidies_here({"files": [{"src": f, "dst": f}]}))


class DiscogsRefusedWhileMusicBrainzIsDown(Base):
    def test_the_album_is_left_for_a_retry(self):
        e = object.__new__(engine.Engine)

        class Http:
            def get_json(self, url, host):
                if host == "mb":
                    raise ma.ProviderUnavailable("mb", "outage", "HTTP 503")
                raise ma.AuthError("Discogs rejected the token (HTTP 401)")
        e.http, e.use_mb, e.use_discogs, e.token, e.dm = Http(), True, False, "tok", types.SimpleNamespace()
        e.profile = pf.from_dict({"name": "P"})
        e.log, e.notes = logging.getLogger("tm-test"), {}
        e._note_mb_down = lambda exc: None
        out = {"best": {"mb_id": "abc", "discogs_id": "123", "source": "mb"}}
        with self.assertRaises(ma.ProviderUnavailable):
            e._identified(out, {"tracks": []}, ["a.flac"], {})


class MachineSettings(Base):
    def test_a_new_install_fills_in_the_public_defaults(self):
        self.assertEqual(app_settings.load()["started_as"], "new")
        self.write_profile({"name": "Mine"})                 # (now it looks like an earlier install)
        app_settings.path().write_text(json.dumps({"version": 1, "started_as": "new", "backups": {"keep_days": 30}}))
        s = app_settings.load()
        self.assertEqual(s["duplicates"]["folder_name"], "_Duplicates")
        self.assertTrue(s["diagnostics"]["hide_names"])
        self.assertEqual(s["network"]["refresh_days"], 30)

    def test_both_built_in_duplicates_folders_are_always_skipped(self):
        app_settings.load()
        app_settings.set_value("duplicates.folder_name", "Copies")
        self.assertTrue({"Copies", "_Duplicates", "2delete"} <= app_settings.duplicates_folders())


class NamingRenders(unittest.TestCase):
    def test_the_name_settings_change_the_names(self):
        st = {"ascii_filenames": True, "replace_spaces_with_underscores": True}
        comps = naming.render("%artist%/%title%", {"artist": ["Ada Lark"], "title": ["Ünder"]}, st, "linux")
        self.assertEqual(comps, ["Ada_Lark", "Under"])


if __name__ == "__main__":
    unittest.main()
