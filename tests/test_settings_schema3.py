# Tests for the schema 3 settings: public defaults, what validate refuses, profiles written
# before keeping their behaviour exactly (plans identical before and after the upgrade), the
# built-in settings' one-time note, this computer's settings (app_settings), and the fixes
# made with them (word limit 0, ALAC by codec, synced lyrics, web artists and genres).
import os as _os, tempfile as _tempfile  # noqa: E402
_os.environ["XDG_CONFIG_HOME"] = _tempfile.mkdtemp(prefix="tm-config-")
_os.environ["XDG_STATE_HOME"] = _tempfile.mkdtemp(prefix="tm-state-")

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
import urllib.parse as up
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
PLUGIN = Path(_os.environ.get("TAGMENDER_DISCOGS_PLUGIN") or "../picard-discogs-enrichment").expanduser()
_os.environ["TAGMENDER_DISCOGS_PLUGIN"] = str(PLUGIN)

import app_settings  # noqa: E402
import exclusions as ex  # noqa: E402
import logsetup  # noqa: E402
import naming  # noqa: E402
import profiles as pf  # noqa: E402

LENS = [10000, 20000, 30000]


def mb_release(rid, artist, title):
    return {"id": rid, "title": title, "date": "2005-03-01", "country": "DE", "status": "Official",
            "barcode": "0123456789012",
            "release-group": {"id": f"rg-{rid}", "primary-type": "Album", "secondary-types": [],
                              "first-release-date": "1999-01-01"},
            "text-representation": {"script": "Latn", "language": "eng"},
            "artist-credit": [{"name": artist, "joinphrase": "", "artist": {"id": "a-" + rid, "name": artist,
                                                                            "sort-name": artist}}],
            "label-info": [{"label": {"name": "Label MB"}, "catalog-number": "CAT-1"}], "relations": [],
            "media": [{"format": "CD", "position": 1, "tracks": [
                {"id": f"{rid}-t{i}", "position": i + 1, "title": f"Song Number {i}", "length": ms,
                 "recording": {"id": f"{rid}-r{i}", "title": f"Song Number {i}", "isrcs": []}}
                for i, ms in enumerate(LENS)]}]}


def dg_release(rid, artist, title):
    return {"id": rid, "title": title, "year": 2005, "released": "2005-03-01",
            "uri": f"https://www.discogs.com/release/{rid}", "artists": [{"name": artist, "id": 77}],
            "labels": [{"name": "Label DG", "catno": "CAT-9"}], "identifiers": [], "country": "Germany",
            "genres": ["Electronic"], "styles": ["Ambient"], "formats": [{"name": "CD", "descriptions": ["Album"]}],
            "tracklist": [{"type_": "track", "position": str(i + 1), "title": f"Song Number {i}", "duration": d}
                          for i, d in enumerate(("0:10", "0:20", "0:30"))]}


class FakeHttp:
    def __init__(self, mb=(), dg=()):
        self.db = None
        self.mb = {r["id"]: r for r in mb}
        self.dg = {r["id"]: r for r in dg}

    def get_json(self, url, host):
        if host == "mb":
            if "/ws/2/release/?" in url:
                q = up.unquote(url.split("query=")[1].split("&")[0])
                return {"releases": [{"id": r["id"], "track-count": 3} for r in self.mb.values() if r["title"] in q]}
            if "/ws/2/" in url and "/release/" in url:
                return self.mb.get(url.split("/release/")[1].split("?")[0])
            return {"releases": [], "recordings": [], "artists": []}
        if host == "discogs":
            if "/database/search" in url:
                q = up.unquote(url)
                return {"results": [{"id": r["id"], "title": f"{r['artists'][0]['name']} - {r['title']}",
                                     "year": "2005"} for r in self.dg.values() if r["title"] in q]}
            tail = url.rsplit("/", 1)[1]
            return self.dg.get(int(tail)) if tail.isdigit() else None
        return None


def tone(path, i, **meta):
    path.parent.mkdir(parents=True, exist_ok=True)
    codec = ["-c:a", meta.pop("_codec")] if "_codec" in meta else []
    args = sum((["-metadata", f"{k}={v}"] for k, v in meta.items()), [])
    subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i",
                    f"sine=frequency={300 + 50 * i}:duration={10 * (i + 1)}", "-ar", "8000", "-ac", "1",
                    *args, *codec, str(path), "-y"], check=True)


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.saved = (ex.CONFIG_DIR, ex.STATE_DIR)
        ex.CONFIG_DIR, ex.STATE_DIR = self.tmp / "config", self.tmp / "state"
        self.env = mock.patch.dict(_os.environ, {"TAGMENDER_TAGGER_INI": str(self.tmp / "no-tagger.ini"),
                                                 "DISCOGS_TOKEN": "test-token"})
        self.env.start()
        logsetup.reset()

    def tearDown(self):
        self.env.stop()
        logsetup.reset()
        ex.CONFIG_DIR, ex.STATE_DIR = self.saved
        shutil.rmtree(self.tmp, ignore_errors=True)

    def write_profile(self, data):
        f = pf.path_of(data["name"])
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps(data))
        return f


class PublicDefaults(Base):
    def test_a_new_profile_gets_the_public_defaults(self):
        p = pf.from_dict({"name": "New"})
        self.assertEqual(p.source(), "musicbrainz_preferred")
        self.assertFalse(p.follows_tagger())
        self.assertEqual(p["naming"]["preset"], "artist-album-track")
        self.assertEqual(p["filenames"]["max_album_words"], 0)
        self.assertEqual((p["subfolders"]["where"], p["subfolders"]["covers_name"], p["subfolders"]["misc_name"]),
                         ("album", "Artwork", "Extras"))
        self.assertEqual(p["artwork"]["artist"], {"enabled": False, "file": "artist.jpg", "embed": False})
        self.assertEqual(p["artwork"]["replace"], "never")
        self.assertEqual(p["artwork"]["sources"][0], "caa")
        self.assertFalse(p["providers"]["web"]["partial"])
        self.assertTrue(p["providers"]["acoustid"]["enabled"])
        self.assertIn(".nfo", p["companions"])
        self.assertIn("@eaDir", p["skip_folders"])
        self.assertNotIn("*/2delete/*", p["exclude_paths"])
        # kept as they were: status notes, nothing-identified albums
        self.assertTrue(p["mark_resolved"])
        self.assertEqual(p["unresolved"]["action"], "mark")
        self.assertEqual(pf.validate(p.data), [])

    def test_the_built_in_settings_are_the_public_defaults(self):
        self.assertEqual(pf.default().source(), "musicbrainz_preferred")
        self.assertEqual(pf.default()["naming"]["preset"], "artist-album-track")

    def test_a_new_profile_names_files_artist_year_album_track_title(self):
        p = pf.from_dict({"name": "New"})
        tpl = naming.effective_template(p["naming"], p["filenames"])
        tags = {"albumartist": ["Band"], "album": ["A Very Long Album Title With Many Words"],
                "title": ["The Night"], "tracknumber": ["3"], "discnumber": ["2"], "totaldiscs": ["2"],
                "originaldate": ["1991"]}
        self.assertEqual(naming.render(tpl, tags),
                         ["Band", "1991 - A Very Long Album Title With Many Words", "2-03 - The Night"])

    def test_genres_from_musicbrainz_on_unless_the_tagger_is_followed(self):
        self.assertTrue(pf.from_dict({"name": "x"}).tagger_overrides()["use_genres"])
        self.assertNotIn("use_genres", pf.from_dict({"name": "x", "follow_tagger_settings": True}).tagger_overrides())
        self.assertFalse(pf.from_dict({"name": "x", "genres": {"musicbrainz": False}}).tagger_overrides()["use_genres"])

    def test_countries_follow_the_region(self):
        p = pf.from_dict({"name": "x"})
        with mock.patch.dict(_os.environ, {"LC_ALL": "", "LC_MESSAGES": "", "LANG": "de_AT.UTF-8"}):
            self.assertEqual(p.preferred_countries(), ["AT", "XW", "XE"])
            self.assertEqual(p.preferred_countries(["GB"]), ["GB"])        # the tagger's list first
        with mock.patch.dict(_os.environ, {"LC_ALL": "", "LC_MESSAGES": "", "LANG": "C"}):
            self.assertEqual(p.preferred_countries(), ["XW", "XE", "US"])
        own = pf.from_dict({"name": "x", "matching": {"preferred_countries": ["jp", "us"]}})
        self.assertEqual(own.preferred_countries(["GB"]), ["JP", "US"])
        self.assertEqual(pf.from_dict({"name": "x", "matching": {"preferred_countries": "none"}})
                         .preferred_countries(["GB"]), [])

    def test_extra_careful_raises_and_never_lowers(self):
        p = pf.from_dict({"name": "x", "matching": {"extra_careful": True},
                          "thresholds": {"organize_min_confidence": 99}})
        self.assertEqual((p.threshold("auto_min_confidence"), p.threshold("organize_min_confidence")), (95.0, 99.0))
        self.assertEqual(pf.from_dict({"name": "x"}).threshold("auto_min_margin"), 8.0)


class Validate(Base):
    def problems(self, **over):
        return pf.validate(dict({"name": "x"}, **over))

    def test_thresholds_may_only_be_raised(self):
        for k, low in (("auto_min_confidence", 89), ("auto_min_margin", 5), ("replace_min_confidence", 80),
                       ("organize_min_confidence", 94.5)):
            self.assertTrue(any("must not be below" in x for x in self.problems(thresholds={k: low})), k)
        self.assertEqual(self.problems(thresholds={"organize_min_confidence": 97}), [])
        with self.assertRaises(pf.ProfileError):
            pf.from_dict({"name": "x", "thresholds": {"auto_min_confidence": 50}})

    def test_skip_patterns_that_hide_everything_or_the_profiles_own_folders(self):
        self.assertTrue(self.problems(skip_folders=["*"]))
        self.assertTrue(self.problems(skip_folders=["**"]))
        self.assertTrue(self.problems(exclude_paths=["*"]))
        self.assertTrue(self.problems(exclude_paths=["*/music/*"], sources=[{"path": "/srv/music"}]))
        self.assertTrue(self.problems(exclude_paths=["/mnt/lib*"], operation="copy", destination="/mnt/lib"))
        self.assertEqual(self.problems(skip_folders=["_*", "Downloads"]), [])

    def test_a_profile_never_names_code_to_run(self):
        for k in pf.CODE_KEYS:
            self.assertTrue(any("never names code" in x for x in self.problems(**{k: "/tmp/x"})), k)

    def test_new_settings_are_checked(self):
        self.assertTrue(self.problems(filenames={"max_album_words": 25}))
        self.assertTrue(self.problems(genres={"discogs_styles": "everywhere"}))
        self.assertTrue(self.problems(matching={"preferred_countries": ["Canada"]}))
        self.assertTrue(self.problems(on_existing="overwrite"))
        self.assertTrue(self.problems(companions=["cue"]))
        self.assertTrue(self.problems(providers={"web": {"enabled": True, "sources": ["google"]}}))
        self.assertEqual(self.problems(formats=["lossless"]), [])
        self.assertEqual(self.problems(formats=["alac", "mp4"]), [])


class KeepV2Behaviour(Base):
    V2 = {"version": 2, "name": "Main", "operation": "copy", "destination": "/srv/lib",
          "artwork": {"artist": {"enabled": False}}, "naming": {"editions": "edition"}}

    def test_a_version_2_file_reads_as_version_2_did(self):
        self.write_profile(self.V2)
        p = pf.load("Main")
        self.assertEqual(p.source(), "discogs_preferred")
        self.assertTrue(p.follows_tagger())
        self.assertEqual(p["naming"], dict(pf.DEFAULTS["naming"], preset="tagmender", editions="edition",
                                           duplicate_suffix="code"))
        self.assertEqual(p["filenames"]["max_album_words"], 5)
        self.assertEqual(p["artwork"]["artist"], {"enabled": False, "file": "", "embed": True})   # its own kept
        self.assertEqual((p["artwork"]["replace"], p["artwork"]["sources"]), ("larger_only", ["coverup", "caa", "discogs"]))
        self.assertTrue(p["providers"]["web"]["partial"])
        self.assertFalse(p["providers"]["acoustid"]["enabled"])
        self.assertEqual(p["companions"], pf.COMPANIONS_V2)
        self.assertEqual(p["skip_folders"], ["_*", "2delete"])
        self.assertEqual(p["subfolders"]["where"], "disc")
        self.assertEqual(p.preferred_countries(), ["CA", "US", "XW", "XE", "GB", "DE"])
        self.assertNotIn("use_genres", p.tagger_overrides())

    def test_migrate_writes_the_nested_keys_and_keeps_a_backup(self):
        f = self.write_profile(self.V2)
        changed, backup = pf.migrate("Main")
        self.assertTrue(changed and backup.exists() and backup.name.startswith("Main.json.bak-"))
        self.assertEqual(json.loads(backup.read_text()), self.V2)
        d = json.loads(f.read_text())
        self.assertEqual(d["version"], 3)
        self.assertEqual(d["naming"]["preset"], "tagmender")
        self.assertEqual(d["artwork"]["artist"], {"file": "", "embed": True})    # (enabled: the default now)
        self.assertEqual(d["subfolders"], {"covers_name": "covers", "misc_name": "misc", "where": "disc"})
        self.assertEqual(d["filenames"]["max_album_words"], 5)
        self.assertEqual(pf.load("Main").to_dict(), pf.from_dict(pf.raw("Main")).to_dict())
        self.assertEqual(pf.migrate("Main"), (False, None))            # once

    def test_a_shipped_example_written_before_keeps_its_behaviour(self):
        # (an Inbox profile copied from the examples of an earlier version: version 1)
        self.write_profile({"version": 1, "name": "Inbox", "sources": [{"path": "/srv/in", "watch": True}],
                            "operation": "move", "destination": "/srv/lib",
                            "unresolved": {"action": "move", "move_to": "/srv/in/_unresolved"}})
        p = pf.load("Inbox")
        self.assertEqual((p.source(), p["naming"]["preset"], p["filenames"]["compat"]),
                         ("discogs_preferred", "tagmender", "windows"))

    def test_the_shipped_examples_validate_and_name_no_ones_folders(self):
        for f in sorted((HERE / "contrib" / "profiles").glob("*.json")):
            d = json.loads(f.read_text())
            if f.name == "Lossy.json":                       # (its library folder is yours to choose)
                self.assertEqual(pf.validate(d), ["operation 'copy' needs a destination"])
                self.assertEqual(d["destination"], "")
                d["destination"] = "/srv/music/Lossy"
            self.assertEqual(pf.validate(d), [], f.name)
            self.assertEqual(d["version"], pf.SCHEMA_VERSION, f.name)
            self.assertNotIn("/media/", f.read_text(), f.name)
            self.assertNotIn("/home/", f.read_text(), f.name)

    def test_names_of_a_version_2_profile_stay(self):
        p = pf.from_dict(pf.keep_v2_behaviour({"name": "x", "version": 2}))
        tpl = naming.effective_template(p["naming"], p["filenames"])
        self.assertEqual(tpl, naming.PRESETS["tagmender"])
        tags = {"albumartist": ["Band"], "album": ["A Very Long Album Title With Many Words"],
                "title": ["The Night"], "tracknumber": ["3"], "discnumber": ["2"], "totaldiscs": ["2"],
                "originaldate": ["1991"]}
        self.assertEqual(naming.render(tpl, tags), ["Band", "1991 - A Very Long Album Title", "Disc 02", "203.TN"])


@unittest.skipUnless(shutil.which("ffmpeg"), "needs ffmpeg")
class PlanBeforeAndAfterUpgrade(Base):
    """A version 2 profile file plans exactly the same before `profiles migrate` and after."""

    def setUp(self):
        super().setUp()
        if not (PLUGIN / "discogs_mapping.py").exists():
            self.skipTest(f"Discogs Enrichment not found at {PLUGIN}")
        self.lib, self.dest = self.tmp / "lib", self.tmp / "dest"
        self.dest.mkdir()
        for rel, artist, title, ext in (("Band A/2005 - Album A", "Band A", "Album A", ".flac"),
                                        ("Band D/2005 - Album D", "Band D", "Album D", ".m4a"),
                                        ("_Singles/Band C/2005 - Album C", "Band C", "Album C", ".flac"),
                                        ("2delete/Band A/2005 - Album A", "Band A", "Album A", ".flac")):
            for i in range(3):
                meta = {"title": f"Song Number {i}", "album": title, "albumartist": artist, "track": str(i + 1)}
                if ext == ".m4a":
                    meta["_codec"] = "alac"
                tone(self.lib / rel / f"{i + 1:02d}{ext}", i, **meta)
        (self.lib / "Band A/2005 - Album A/back.jpg").write_bytes(b"x")
        (self.lib / "Band A/2005 - Album A/rip.log").write_text("log")
        (self.lib / "Band A/2005 - Album A/notes.nfo").write_text("nfo")
        self.http = FakeHttp(mb=[mb_release("mb-a", "Band A", "Album A"), mb_release("mb-c", "Band C", "Album C"),
                                 mb_release("mb-d", "Band D", "Album D")],
                             dg=[dg_release(905, "Band D", "Album D")])

    def plan(self, name):
        import engine
        eng = engine.Engine(pf.load(name), http=self.http)
        p = eng.plan(self.lib)
        drop = {"job", "created", "profile_data", "version", "matcher_version", "id"}

        def clean(x):
            if isinstance(x, dict):
                return {k: clean(v) for k, v in x.items() if k not in drop and k not in ("ino", "ctime_ns", "mtime_ns")}
            return [clean(v) for v in x] if isinstance(x, list) else x
        return clean(p)

    def test_same_plan_and_the_names_version_2_gave(self):
        self.write_profile({"version": 2, "name": "Copy", "operation": "copy", "destination": str(self.dest),
                            "sources": [{"path": str(self.lib)}], "providers": {"web": {"enabled": False}},
                            "artwork": {"enabled": False}, "subfolders": {"covers": True, "misc": True}})
        before = self.plan("Copy")
        pf.migrate("Copy")
        after = self.plan("Copy")
        self.assertEqual(before, after)
        dsts = sorted(c["dst"] for a in before["albums"] for c in a.get("files") or [] if c.get("dst"))
        self.assertTrue(dsts)
        self.assertTrue(all("/_Singles/" not in d and "/2delete/" not in d for d in dsts))
        # Discogs preferred, its year, the short names
        self.assertIn(str(self.dest / "Band D" / "2005 - Album D" / "01.SN.m4a"), dsts)

    def test_a_new_profile_walks_underscore_folders_and_uses_full_names(self):
        self.write_profile({"version": 3, "name": "New", "operation": "copy", "destination": str(self.dest),
                            "sources": [{"path": str(self.lib)}], "providers": {"web": {"enabled": False}},
                            "artwork": {"enabled": False}, "source": "musicbrainz_only"})
        p = self.plan("New")
        dirs = {a["source_dir"] for a in p["albums"]}
        self.assertIn(str(self.lib / "_Singles/Band C/2005 - Album C"), dirs)
        dsts = {c["dst"] for a in p["albums"] for c in a.get("files") or [] if c.get("dst")}
        self.assertIn(str(self.dest / "Band D" / "1999 - Album D" / "01 - Song Number 0.m4a"), dsts)


class BuiltinNote(Base):
    def test_once_on_an_earlier_install_never_on_a_new_one(self):
        self.assertEqual(pf.builtin_changed_note(), "")               # a new install
        shutil.rmtree(self.tmp / "config", ignore_errors=True)
        (self.tmp / "state").mkdir(parents=True, exist_ok=True)
        (self.tmp / "state" / "state.db").write_bytes(b"")           # an earlier install
        notes = []
        prof = pf.load_or_builtin("default", note=notes.append)
        self.assertEqual(prof.name, "default")
        self.assertEqual(len(notes), 1)
        self.assertIn("save-previous-builtin", notes[0])
        pf.load_or_builtin(None, note=notes.append)
        self.assertEqual(len(notes), 1)                               # once

    def test_the_previous_built_in_settings_as_a_profile(self):
        path = pf.save_previous_builtin()
        p = pf.load(pf.PREVIOUS_BUILTIN_NAME)
        self.assertTrue(path.exists())
        self.assertEqual((p.source(), p["naming"]["preset"], p["artwork"]["artist"]["embed"]),
                         ("discogs_preferred", "tagmender", True))
        self.assertEqual(p["operation"], "in_place")
        with self.assertRaises(pf.ProfileError):
            pf.save_previous_builtin()

    def test_the_cli_says_so_once(self):
        import contextlib
        import io
        import tagmender
        (self.tmp / "state").mkdir(parents=True, exist_ok=True)
        (self.tmp / "state" / "state.db").write_bytes(b"")
        lib = self.tmp / "empty"
        lib.mkdir()
        errs = []
        for _ in range(2):
            err = io.StringIO()
            with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
                tagmender.main(["scan", str(lib)])
            errs.append(err.getvalue())
        self.assertIn("built-in settings", errs[0])
        self.assertNotIn("built-in settings", errs[1])


class AppSettings(Base):
    def test_a_new_install_gets_the_public_defaults(self):
        s = app_settings.load()
        self.assertEqual(s["duplicates"]["folder_name"], "_Duplicates")
        self.assertTrue(s["backups"]["auto_clean"])
        self.assertTrue(s["diagnostics"]["hide_names"])
        self.assertTrue((ex.CONFIG_DIR / "settings.json").exists())    # decided once
        self.assertEqual(app_settings.validate(s, libraries=[]), [])

    def test_an_earlier_install_keeps_its_behaviour(self):
        self.write_profile({"version": 2, "name": "Keep", "backup_retention_days": 90})
        s = app_settings.load()
        self.assertEqual(s["duplicates"]["folder_name"], "2delete")
        self.assertFalse(s["backups"]["auto_clean"])
        self.assertEqual(s["backups"]["keep_days"], 90)
        self.assertEqual(s["skip_folders"], ["_*", "2delete"])
        self.assertEqual(s["network"]["refresh_days"], 0)
        skips = app_settings.tool_skipper()
        self.assertTrue(skips("_old") and skips("2delete") and skips(".hidden"))
        self.assertFalse(skips("Band"))

    def test_a_new_install_skips_only_its_own_and_recycle_bins(self):
        skips = app_settings.tool_skipper()
        self.assertTrue(skips("_Duplicates") and skips("_unresolved") and skips("@eaDir") and skips("#recycle"))
        self.assertFalse(skips("_Singles"))

    def test_backup_folder_on_a_library_or_a_share_is_refused(self):
        self.assertTrue(app_settings.backup_location_problems("/srv/music/backups", libraries=["/srv/music"]))
        self.assertTrue(app_settings.backup_location_problems("/srv", libraries=["/srv/music"]))
        with mock.patch.object(app_settings, "_mount_of", return_value=("/mnt/nas", "cifs")):
            self.assertTrue(any("network" in x for x in
                                app_settings.backup_location_problems("/mnt/nas/bk", libraries=[])))
        with mock.patch.object(app_settings, "_mount_of", return_value=("/", "ext4")):
            self.assertTrue(any("mounted" in x for x in
                                app_settings.backup_location_problems("/media/usb/bk", libraries=[])))
            self.assertEqual(app_settings.backup_location_problems(str(self.tmp / "bk"), libraries=[]), [])

    def test_bad_values_are_refused(self):
        for name, value in (("backups.keep_days", 45), ("duplicates.folder_name", "a/b"),
                            ("skip_folders", ["*"]), ("ocr.effort", "max"), ("plugins.folder", "relative")):
            with self.assertRaises(ValueError, msg=name):
                app_settings.set_value(name, value)
        app_settings.set_value("duplicates.folder_name", "Dupes")
        self.assertEqual(app_settings.duplicates_folder(), "Dupes")


class Fixes(Base):
    def test_word_limit_0_is_the_full_title(self):
        tpl = naming.effective_template({"preset": "tagmender"}, {"max_album_words": 0})
        self.assertNotIn("$words(%album%", tpl)
        self.assertIn("$words(%album%,3)", naming.effective_template({"preset": "tagmender"}, {"max_album_words": 3}))
        tpl = naming.effective_template({"preset": "all-music"}, {"max_album_words": 0})
        tags = {"albumartist": ["B"], "album": ["One Two Three Four Five Six Seven"], "title": ["X"],
                "tracknumber": ["1"], "originaldate": ["2001"]}
        self.assertEqual(naming.render(tpl, tags)[1], "2001 - One Two Three Four Five Six Seven")

    @unittest.skipUnless(shutil.which("ffmpeg"), "needs ffmpeg")
    def test_alac_in_m4a_is_lossless_and_aac_is_not(self):
        import levels
        alac, aac = self.tmp / "a.m4a", self.tmp / "b.m4a"
        tone(alac, 0, title="a", _codec="alac")
        tone(aac, 0, title="b", _codec="aac")
        self.assertTrue(levels.is_lossless(alac))
        self.assertFalse(levels.is_lossless(aac))
        lossless, lossy = pf.from_dict({"name": "x", "formats": ["lossless"]}), \
            pf.from_dict({"name": "y", "formats": ["lossy"]})
        self.assertTrue(lossless.wants_format(alac) and lossy.wants_format(aac))
        self.assertFalse(lossless.wants_format(aac) or lossy.wants_format(alac))
        self.assertTrue(pf.from_dict({"name": "z", "formats": ["alac"]}).wants_format(alac))
        self.assertTrue(pf.from_dict({"name": "z", "formats": ["m4a"]}).wants_format(alac))   # by name, as before

    def test_synced_lyrics_setting_is_read(self):
        import levels

        class H:
            def get_json(self, url, host):
                return {"syncedLyrics": "[00:01.00] la", "plainLyrics": "la"}
        target = {"artist": ["A"], "title": ["T"], "album": ["B"]}
        self.assertEqual(levels.lyrics_for(H(), target, 10), ("[00:01.00] la", True))
        self.assertEqual(levels.lyrics_for(H(), target, 10, synced=False), ("la", False))

    def test_a_band_named_with_commas_is_one_artist(self):
        import web_tag
        cand = {"artist": "Emerson, Lake & Palmer", "title": "Tarkus", "year": 1971}
        with mock.patch.object(web_tag, "mb_artist_id", return_value="elp-id"):
            self.assertEqual(web_tag.album_artists(object(), cand, {}), ["Emerson, Lake & Palmer"])
        with mock.patch.object(web_tag, "mb_artist_id", return_value=""):
            self.assertEqual(web_tag.album_artists(object(), dict(cand, artist="A & B"), {}), ["A", "B"])

    def test_web_genres_are_separate_values(self):
        import engine
        import web_tag
        self.assertEqual(web_tag.genre_list("Rock; Pop; rock"), ["Rock", "Pop"])
        self.assertEqual(web_tag.genre_list(["Folk, World, & Country"]), ["Folk, World, & Country"])
        p = pf.from_dict({"name": "x"})
        self.assertEqual(engine.web_genres(p, {"source": "bandcamp", "genre": ["Berlin", "drone"]}), [])
        self.assertEqual(engine.web_genres(p, {"source": "deezer", "genre": ["Rock"]}), ["Rock"])
        old = pf.from_dict(pf.keep_v2_behaviour({"name": "x", "version": 2}))
        self.assertEqual(engine.web_genres(old, {"source": "bandcamp", "genre": ["drone"]}), ["drone"])

    def test_one_list_of_audio_files(self):
        import audit_untagged
        import tag_albums
        self.assertIs(tag_albums.AUDIO_EXT, ex.AUDIO_EXT)
        self.assertIs(audit_untagged.AUDIO_EXT, ex.AUDIO_EXT)

    def test_the_covers_folder_name_is_the_tools_setting(self):
        import tag_albums
        old, new = self.tmp / "old", self.tmp / "new"
        (old / "Scans").mkdir(parents=True)
        (old / "Scans" / "back.jpg").write_bytes(b"x")
        new.mkdir()
        tag_albums.tidy_artwork(str(old), str(new), [], covers_name="Artwork")
        self.assertTrue((new / "Artwork" / "Scans" / "back.jpg").exists())


if __name__ == "__main__":
    unittest.main()
