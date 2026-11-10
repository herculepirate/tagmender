# Tests that each schema 3 setting (and this computer's settings) changes what TagMender does:
# Source (deluxe folders, the lossy library), Identification (tiers accepted, loose tracks,
# MusicBrainz down under MusicBrainz First), Tags (Discogs details and styles, dates, versions,
# comments, loudness), Artwork (cover size in tracks), Target (second-copy names, a file already
# there, renaming in place, cue sheets, USB-stick names, the mount guard, folders that cannot be
# written), File safety (backups kept app-wide, cleaned automatically, the jobs that lose Undo),
# scans (languages, how many), saved lookups, and Test Connection (connection.py, doctor).
import os as _os, tempfile as _tempfile  # noqa: E402
_os.environ["XDG_CONFIG_HOME"] = _tempfile.mkdtemp(prefix="tm-config-")
_os.environ["XDG_STATE_HOME"] = _tempfile.mkdtemp(prefix="tm-state-")

import io
import json
import shutil
import sqlite3
import sys
import time
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "tests"))

from test_settings_schema3 import PLUGIN, Base, FakeHttp, dg_release, tone  # noqa: E402

import app_settings  # noqa: E402
import engine  # noqa: E402
import levels  # noqa: E402
import match_albums as ma  # noqa: E402
import plan as planmod  # noqa: E402
import profiles as pf  # noqa: E402

FFMPEG = shutil.which("ffmpeg") is not None


def prop_for(files, album="Album A", artist="Band A", **kw):
    """An exact match of the files (made up), as the engine hands it to the planner."""
    targets = [{"album": [album], "albumartist": [artist], "artist": [artist], "title": [f"Song Number {i}"],
                "tracknumber": [str(i + 1)], "totaltracks": [str(len(files))], "discnumber": ["1"],
                "totaldiscs": ["1"], "originaldate": ["1999"], "date": ["2005"],
                "musicbrainz_albumid": ["mb-a"]} for i in range(len(files))]
    base = dict(source_dir=str(Path(files[0]).parent), files=[str(f) for f in files], targets=targets,
                status="identified", tier="exact", provider="musicbrainz", release_id="mb-a",
                confidence=99.0, margin=30.0)
    base.update(kw)
    return planmod.Proposal(**base)


def album(folder, n=2, ext=".flac", artist="Band A", title="Album A", **meta):
    files = []
    for i in range(n):
        f = Path(folder) / f"{i + 1:02d}{ext}"
        tone(f, i, title=f"Song Number {i}", album=title, albumartist=artist, artist=artist, track=str(i + 1),
             **meta)
        files.append(f)
    return files


def all_files(p):
    return [fe for a in p["albums"] for fe in a.get("files") or []]


# ---- the profile's own answers --------------------------------------------------------
class ProfileAnswers(Base):
    def test_tiers_accepted(self):
        for accept, want in (("partial", (True, True, True)), ("album", (True, True, False)),
                             ("exact", (True, False, False))):
            p = pf.from_dict({"name": "x", "identification": {"accept": accept}})
            self.assertEqual((p.accepts("exact"), p.accepts("album"), p.accepts("partial")), want, accept)

    def test_loose_tracks_and_deluxe_folders_switches(self):
        self.assertTrue(pf.from_dict({"name": "x"}).identifies_singles())
        self.assertFalse(pf.from_dict({"name": "x", "singles": {"identify": False}}).identifies_singles())
        self.assertTrue(pf.from_dict({"name": "x"}).joins_deluxe())
        self.assertFalse(pf.from_dict({"name": "x", "grouping": {"join_deluxe_folders": False}}).joins_deluxe())

    def test_discogs_details_follow_the_level(self):
        p = pf.from_dict({"name": "x"})
        s = p.discogs_detail_settings()
        self.assertEqual((s["performers"], s["companies"], s["identifiers"], s["marketplace"], s["import_notes"]),
                         (True, False, False, False, False))
        self.assertTrue(pf.from_dict({"name": "x", "tag_level": "audiophile"}).discogs_detail_settings()["identifiers"])
        e = p.enrichment()
        self.assertFalse(e["companies"])
        self.assertEqual(e["style_mode"], "separate")
        self.assertFalse(e["prefer_original_date"])
        # "tagger": the plugin's own setting stays (nothing set here)
        old = pf.from_dict({"name": "x", "discogs_details": {k: "tagger" for k in pf.DISCOGS_DETAILS},
                            "genres": {"discogs_styles": "tagger"}, "dates": {"date_tag": "tagger"}})
        self.assertEqual(old.discogs_detail_settings(), {})
        # an explicit discogs_enrichment entry wins over the profile's words
        own = pf.from_dict({"name": "x", "discogs_enrichment": {"companies": True}})
        self.assertTrue(own.enrichment()["companies"])

    def test_styles_and_dates_choices(self):
        both = pf.from_dict({"name": "x", "genres": {"discogs_styles": "both"}})
        self.assertEqual(both.enrichment()["style_mode"], "both")
        self.assertFalse(both.styles_fill_genre())
        self.assertTrue(pf.from_dict({"name": "x"}).styles_fill_genre())
        orig = pf.from_dict({"name": "x", "dates": {"date_tag": "original"}})
        self.assertTrue(orig.enrichment()["prefer_original_date"])
        self.assertTrue(orig.date_holds_original())

    def test_version_choices(self):
        p = pf.from_dict({"name": "x", "versions": {"style": "bracket", "extra_tags": False}})
        self.assertEqual(engine.version_choices(p), {"style": "bracket", "tags": False})
        self.assertEqual(engine.version_choices(pf.from_dict({"name": "x"})), {"style": "paren", "tags": True})

    def test_translated_names_follow_your_language(self):
        p = pf.from_dict({"name": "x", "tagger": {"translate_artist_names": True}})
        with mock.patch.dict(_os.environ, {"LC_ALL": "", "LC_MESSAGES": "", "LANG": "fr_CA.UTF-8"}):
            self.assertEqual(p.tagger_overrides()["translation_locales"], ["fr"])
        self.assertNotIn("translation_locales", pf.from_dict({"name": "x"}).tagger_overrides())

    def test_validate_refuses_bad_values(self):
        def problems(**over):
            return pf.validate(dict({"name": "x"}, **over))
        self.assertTrue(problems(destinations={"lossy": "", "mounted_only": "never"}))
        self.assertTrue(problems(on_unwritable="overwrite"))
        self.assertTrue(problems(filenames={"compat": "linux", "fat": True}))
        self.assertTrue(problems(artwork={"embed_max_side": 50}))
        self.assertTrue(problems(quality_action="move"))                       # (needs unresolved.move_to)
        self.assertEqual(problems(quality_action="move", unresolved={"action": "mark", "move_to": "/srv/aside"}), [])
        self.assertEqual(problems(destinations={"lossy": "/srv/lossy", "mounted_only": "always"},
                                  filenames={"compat": "windows", "fat": True}, artwork={"embed_max_side": 600},
                                  on_unwritable="skip_file", dates={"date_tag": "original"}), [])

    def test_a_version_2_profile_keeps_its_tagger_choices(self):
        self.write_profile({"version": 2, "name": "Old"})
        p = pf.load("Old")
        self.assertEqual(p["genres"]["discogs_styles"], "tagger")
        self.assertEqual(p["dates"]["date_tag"], "tagger")
        self.assertEqual(p.discogs_detail_settings(), {})


# ---- the engine's tag choices ---------------------------------------------------------------
class TagChoices(unittest.TestCase):
    def test_styles_fill_an_empty_genre_only(self):
        p = pf.from_dict({"name": "x"})
        t = engine.own_tag_choices({"discogs_style": ["Ambient", "Drone"]}, p)
        self.assertEqual(t["genre"], ["Ambient", "Drone"])
        t = engine.own_tag_choices({"genre": ["Rock"], "discogs_style": ["Ambient"]}, p)
        self.assertEqual(t["genre"], ["Rock"])
        own = pf.from_dict({"name": "x", "genres": {"discogs_styles": "own_tag"}})
        self.assertNotIn("genre", engine.own_tag_choices({"discogs_style": ["Ambient"]}, own))

    def test_date_holds_the_first_release_when_chosen(self):
        t = {"date": ["2005-03-01"], "originaldate": ["1999-01-01"]}
        self.assertEqual(engine.own_tag_choices(t, pf.from_dict({"name": "x"}))["date"], ["2005-03-01"])
        orig = pf.from_dict({"name": "x", "dates": {"date_tag": "original"}})
        self.assertEqual(engine.own_tag_choices(t, orig)["date"], ["1999-01-01"])

    def test_comments_removed_when_chosen(self):
        self.assertNotIn("~remove", engine.own_tag_choices({}, pf.from_dict({"name": "x"})))
        rm = pf.from_dict({"name": "x", "cleanup": {"comments": "remove"}})
        self.assertEqual(engine.own_tag_choices({}, rm)["~remove"], ["comment"])

    def test_version_style_and_tags(self):
        def shown(**kw):
            targets = [{"album": ["Nothing Remains"], "title": ["One"]}]
            with mock.patch.object(engine, "_mb_version", return_value=("remaster 2024", "Remaster 2024")), \
                    mock.patch.object(engine, "_mb_other_versions", return_value=True):
                engine.keep_version(None, targets, {"tracks": []}, ["x"], rel={"disambiguation": "remaster 2024"},
                                    **kw)
            return targets[0]
        self.assertEqual(shown()["album"], ["Nothing Remains (Remaster 2024)"])
        self.assertEqual(shown()["albumversion"], ["Remaster 2024"])
        self.assertEqual(shown(style="bracket")["album"], ["Nothing Remains [Remaster 2024]"])
        t = shown(style="tags_only", tags=False)
        self.assertEqual(t["album"], ["Nothing Remains"])
        self.assertNotIn("albumversion", t)
        self.assertNotIn("releasecomment", t)
        self.assertEqual(t["~albumversion"], ["Remaster 2024"])          # (still named for folders)

    def test_loudness_choices(self):
        rg = {"/a.opus": {"replaygain_track_gain": "1", "replaygain_album_gain": "2", "replaygain_album_peak": "1",
                          "r128_track_gain": "3", "r128_album_gain": "4"}}
        self.assertEqual(levels.loudness_choices(rg, {}), rg)
        out = levels.loudness_choices(rg, {"album_gain": False, "opus_r128": False})["/a.opus"]
        self.assertEqual(out, {"replaygain_track_gain": "1"})


# ---- identification --------------------------------------------------------------------------
@unittest.skipUnless(FFMPEG, "needs ffmpeg")
class Identification(Base):
    def test_deluxe_folder_joined_only_when_on(self):
        lib = self.tmp / "lib"
        for d, nums in (("Band/Album", (1, 2, 3)), ("Band/Album (Deluxe Edition)", (4, 5))):
            for n in nums:
                tone(lib / d / f"{n:02d}.flac", n, title=f"T{n}", album="Album", albumartist="Band", artist="Band",
                     track=str(n), TRACKTOTAL="5")
        on = engine.Engine(pf.from_dict({"name": "x"}), http=FakeHttp())
        off = engine.Engine(pf.from_dict({"name": "x", "grouping": {"join_deluxe_folders": False}}), http=FakeHttp())
        self.assertEqual(len(on.albums_in(lib)), 1)
        self.assertEqual(len(off.albums_in(lib)), 2)

    def _identify(self, prof, out):
        e = engine.Engine(prof, http=FakeHttp())
        folder = self.tmp / "lib" / "Band A" / "Album A"
        album(folder, 1)
        g = e.albums_in(folder)[0]
        with mock.patch.object(ma, "process", return_value=out), \
                mock.patch.object(engine.Engine, "_album_only", return_value="album-only") as only, \
                mock.patch.object(engine.Engine, "_single", return_value=None) as single, \
                mock.patch.object(ma, "fingerprint_ready", return_value=True):
            e._identify(g)
        return only.called, single.called

    def test_album_only_tagged_only_when_accepted(self):
        out = {"status": "review", "album_only": {"tied": []}, "best": {"source": "mb", "id": "x"}}
        with mock.patch.object(engine.Engine, "_identified", return_value=object()):
            self.assertTrue(self._identify(pf.from_dict({"name": "x"}), out)[0])
            self.assertFalse(self._identify(pf.from_dict({"name": "y", "identification": {"accept": "exact"}}), out)[0])

    def test_loose_track_fingerprinted_only_when_on(self):
        out = {"status": "nomatch"}
        self.assertTrue(self._identify(pf.from_dict({"name": "x"}), out)[1])
        self.assertFalse(self._identify(pf.from_dict({"name": "y", "singles": {"identify": False}}), out)[1])


class DownHttp(FakeHttp):
    """MusicBrainz never answers; Discogs does."""

    def get_json(self, url, host):
        if host == "mb":
            raise ma.ProviderUnavailable("mb", "outage", "HTTP 503")
        return super().get_json(url, host)

    def get_text(self, url, host, key=None, headers=None):
        if host == "mb":
            raise ma.ProviderUnavailable("mb", "outage", "HTTP 503")
        raise ma.ProviderUnavailable(host, "outage", "not here")


@unittest.skipUnless(FFMPEG, "needs ffmpeg")
class MusicBrainzDown(Base):
    def setUp(self):
        super().setUp()
        if not (PLUGIN / "discogs_mapping.py").exists():
            self.skipTest(f"Discogs Enrichment not found at {PLUGIN}")
        self.lib = self.tmp / "lib"
        for i in range(3):
            tone(self.lib / "Band D" / "2005 - Album D" / f"{i + 1:02d}.flac", i, title=f"Song Number {i}",
                 album="Album D", albumartist="Band D", artist="Band D", track=str(i + 1))

    def plan(self, source):
        prof = pf.from_dict({"name": "x", "source": source, "providers": {"web": {"enabled": False}},
                             "artwork": {"enabled": False}})
        e = engine.Engine(prof, http=DownHttp(dg=[dg_release(905, "Band D", "Album D")]))
        return e.plan(self.lib)

    def test_preferred_down_holds_the_album_and_says_so(self):       # HF-4
        p = self.plan("musicbrainz_preferred")
        a = p["albums"][0]
        self.assertEqual(a["identification"]["status"], "unresolved", a["identification"])
        self.assertIn("musicbrainz", a["identification"]["provider_errors"])
        self.assertIn("held and tried again later", p["provider_notes"]["musicbrainz"])

    def test_discogs_preferred_carries_on_with_discogs(self):
        p = self.plan("discogs_preferred")
        a = p["albums"][0]
        self.assertEqual(a["identification"]["status"], "identified", a["identification"])
        self.assertTrue(a["identification"]["provider"].startswith("discogs"))
        self.assertIn("carries on with Discogs", p["provider_notes"]["musicbrainz"])

    def test_without_discogs_the_album_waits_for_a_retry(self):
        p = self.plan("musicbrainz_only")
        a = p["albums"][0]
        self.assertNotEqual(a["identification"]["status"], "identified")
        self.assertIn("musicbrainz", a["identification"]["provider_errors"])
        self.assertIn("try again later", p["provider_notes"]["musicbrainz"])


# ---- the planner -------------------------------------------------------------------------------
@unittest.skipUnless(FFMPEG, "needs ffmpeg")
class Target(Base):
    def setUp(self):
        super().setUp()
        self.lib, self.dest = self.tmp / "lib", self.tmp / "dest"
        self.dest.mkdir()

    def copy_profile(self, **over):
        return pf.from_dict(dict({"name": "x", "operation": "copy", "destination": str(self.dest),
                                  "sources": [{"path": str(self.lib)}], "artwork": {"enabled": False}}, **over))

    def test_lossy_albums_go_to_the_lossy_library(self):
        lossy = self.tmp / "lossy"
        lossy.mkdir()
        mp3 = album(self.lib / "Band A" / "MP3", 2, ".mp3")
        flac = album(self.lib / "Band A" / "FLAC", 2, ".flac")
        prof = self.copy_profile(destinations={"lossy": str(lossy), "mounted_only": "auto"})
        p = planmod.build([prop_for(mp3), prop_for(flac)], prof)
        dsts = [fe["dst"] for fe in all_files(p)]
        self.assertTrue(any(d.startswith(str(lossy) + "/") and d.endswith(".mp3") for d in dsts), dsts)
        self.assertTrue(any(d.startswith(str(self.dest) + "/") and d.endswith(".flac") for d in dsts), dsts)
        # without a lossy library, both go to the destination
        p = planmod.build([prop_for(mp3)], self.copy_profile())
        self.assertTrue(all(fe["dst"].startswith(str(self.dest)) for fe in all_files(p)))

    def test_each_source_folder_files_into_its_own_library(self):
        ambient_in, extreme_in = self.tmp / "in" / "ambient", self.tmp / "in" / "extreme"
        ambient = self.dest / "ambient"
        ambient.mkdir(parents=True)
        a = album(ambient_in / "Band A" / "Calm", 2)
        e = album(extreme_in / "Band B" / "Loud", 2)
        prof = self.copy_profile(sources=[{"path": str(ambient_in), "destination": str(ambient)},
                                          {"path": str(extreme_in)}])            # (none: the profile's)
        self.assertEqual(pf.validate(prof.data), [])
        p = planmod.build([prop_for(a), prop_for(e)], prof)
        by_src = {al["source_dir"]: [fe["dst"] for fe in al["files"]] for al in p["albums"]}
        self.assertTrue(all(d.startswith(str(ambient) + "/") for d in by_src[str(a[0].parent)]), by_src)
        self.assertTrue(all(d.startswith(str(self.dest) + "/") and not d.startswith(str(ambient) + "/")
                            for d in by_src[str(e[0].parent)]), by_src)
        self.assertEqual(prof.filing_roots(), [str(self.dest), str(ambient)])
        # a source's library that is not there blocks only its albums, and is never created
        gone = self.tmp / "unmounted"
        prof = self.copy_profile(sources=[{"path": str(ambient_in), "destination": str(gone)}])
        p = planmod.build([prop_for(a)], prof)
        self.assertEqual(p["albums"][0]["status"], "blocked")
        self.assertFalse(gone.exists())
        self.assertTrue(pf.validate(dict(prof.data, sources=[{"path": str(ambient_in), "destination": "rel/x"}])))

    def _taken(self, **over):
        files = album(self.lib / "Band A" / "Album A", 1)
        there = self.dest / "Band A" / "1999 - Album A" / "01 - Song Number 0.flac"
        there.parent.mkdir(parents=True, exist_ok=True)
        there.write_bytes(b"another file")
        return planmod.build([prop_for(files)], self.copy_profile(**over))["albums"][0], there

    def test_a_second_copy_is_named_with_a_number(self):
        a, there = self._taken()
        self.assertEqual(a["files"][0]["dst"], str(there.with_name("01 - Song Number 0 (2).flac")))

    def test_a_second_copy_with_a_short_code_when_chosen(self):
        a, there = self._taken(naming={"preset": "artist-album-track", "duplicate_suffix": "code"})
        self.assertRegex(a["files"][0]["dst"], r"01 - Song Number 0 ~[0-9a-f]{4}\.flac$")

    def test_skip_or_ask_when_a_file_is_already_there(self):
        a, _ = self._taken(on_existing="skip")
        self.assertEqual(a["files"], [])
        self.assertTrue(any(s["rule"] == "already there" for s in a["skipped"]))
        a, _ = self._taken(on_existing="ask")
        self.assertEqual(a["status"], "blocked")
        self.assertTrue(any("choose which to keep" in c for c in a["conflicts"]))
        a, there = self._taken(on_existing="ask", unattended=True)       # (nobody to ask: both kept)
        self.assertEqual(a["files"][0]["dst"], str(there.with_name("01 - Song Number 0 (2).flac")))

    def test_rename_in_place(self):
        files = album(self.lib / "Band A" / "Album A", 2)
        p = planmod.build([prop_for(files)], pf.from_dict({"name": "x"}))
        self.assertTrue(all(fe["dst"] == fe["src"] for fe in all_files(p)))
        p = planmod.build([prop_for(files)], pf.from_dict({"name": "x", "rename_in_place": True}))
        fe = all_files(p)[0]
        self.assertEqual(fe["dst"], str(files[0].parent / "01 - Song Number 0.flac"))
        self.assertEqual(fe["op"], "move")

    def test_rename_in_place_applies_and_undoes(self):
        import executor
        import payload
        files = album(self.lib / "Band A" / "Album A", 2)
        digests = [payload.digest(f) for f in files]
        p = planmod.build([prop_for(files)], pf.from_dict({"name": "x", "rename_in_place": True}))
        res = executor.Executor(p).run()
        self.assertEqual(res["status"], "done", res)
        folder = files[0].parent
        self.assertEqual(sorted(x.name for x in folder.iterdir()), ["01 - Song Number 0.flac",
                                                                    "02 - Song Number 1.flac"])
        self.assertEqual(payload.digest(folder / "01 - Song Number 0.flac"), digests[0])    # (the same audio)
        problems = [x for x in executor.undo(res["job"]) if x.startswith("PROBLEM")]
        self.assertEqual(problems, [])
        self.assertEqual(sorted(x.name for x in folder.iterdir()), ["01.flac", "02.flac"])

    def test_a_numbered_second_copy_applies(self):
        import executor
        a, there = self._taken()
        p = planmod.build([prop_for([Path(a["files"][0]["src"])])], self.copy_profile())
        res = executor.Executor(p).run()
        self.assertEqual(res["status"], "done", res)
        self.assertEqual(there.read_bytes(), b"another file")                 # (never touched)
        self.assertTrue(there.with_name("01 - Song Number 0 (2).flac").exists())
        # planned again: the copy is found, nothing new
        again = planmod.build([prop_for([Path(a["files"][0]["src"])])], self.copy_profile())
        self.assertEqual([fe["dst"] for fe in all_files(again)], [str(there.with_name("01 - Song Number 0 (2).flac"))])

    def test_cue_sheets_kept_as_they_are_when_chosen(self):
        files = album(self.lib / "Band A" / "Album A", 2)
        cue = files[0].parent / "album.cue"
        cue.write_text('FILE "01.flac" WAVE\n  TRACK 01 AUDIO\nFILE "02.flac" WAVE\n  TRACK 02 AUDIO\n')
        a = planmod.build([prop_for(files)], self.copy_profile())["albums"][0]
        self.assertTrue(any(c.get("rewrite") for c in a["companions"] if c["src"] == str(cue)))
        a = planmod.build([prop_for(files)], self.copy_profile(companions_rewrite_cues=False))["albums"][0]
        self.assertFalse(any(c.get("rewrite") for c in a["companions"] if c["src"] == str(cue)))
        self.assertTrue(any("not rewritten" in n for n in a["notes"]))

    @unittest.skipIf(hasattr(_os, "geteuid") and _os.geteuid() == 0, "root writes everywhere")
    def test_a_folder_that_cannot_be_written(self):
        files = album(self.lib / "Band A" / "Album A", 2)
        folder = files[0].parent
        folder.chmod(0o555)
        try:
            a = planmod.build([prop_for(files)], pf.from_dict({"name": "x"}))["albums"][0]
            self.assertEqual(a["status"], "blocked")
            self.assertTrue(any("cannot be written" in c for c in a["conflicts"]))
            a = planmod.build([prop_for(files)], pf.from_dict({"name": "x", "on_unwritable": "skip_file"}))["albums"][0]
            self.assertEqual(a["status"], "skipped")
            self.assertEqual(len([s for s in a["skipped"] if s["rule"] == "cannot be written"]), 2)
            # a copy only reads its source: nothing to refuse
            a = planmod.build([prop_for(files)], self.copy_profile())["albums"][0]
            self.assertNotEqual(a["status"], "blocked")
        finally:
            folder.chmod(0o755)

    def test_usb_stick_names_keep_paths_short(self):
        pl = planmod.Planner(pf.from_dict({"name": "x", "filenames": {"compat": "windows", "fat": True}}))
        lim = pl._name_limits(self.dest)
        self.assertLessEqual(lim["max_path_chars"], len(planmod.mount_point(self.dest).rstrip("/")) + 255)
        self.assertEqual(planmod.Planner(pf.from_dict({"name": "x"}))._name_limits(self.dest)["max_path_chars"], 4096)

    def test_held_for_review_when_it_looks_converted(self):
        files = album(self.lib / "Band A" / "Album A", 1)
        pr = prop_for(files)
        pr.suspect_lossy = "transcoded: everything above 16 kHz is cut"
        a = planmod.build([pr], pf.from_dict({"name": "x", "quality_action": "hold"}))["albums"][0]
        self.assertEqual(a["status"], "blocked")
        self.assertTrue(any("held for review" in c for c in a["conflicts"]))
        a = planmod.build([prop_for(files)], pf.from_dict({"name": "x", "quality_action": "hold"}))["albums"][0]
        self.assertNotEqual(a["status"], "blocked")

    def test_moved_aside_when_it_looks_converted(self):
        files = album(self.lib / "Band A" / "Album A", 1)
        aside = self.tmp / "aside"
        aside.mkdir()
        prof = pf.from_dict({"name": "x", "quality_action": "move",
                             "unresolved": {"action": "mark", "move_to": str(aside)}})
        pr = prop_for(files)
        pr.suspect_lossy = "transcoded"
        e = engine.Engine(prof, http=FakeHttp())
        with mock.patch.object(levels, "items", return_value={}):
            e.apply_level(pr)
        self.assertEqual(pr.status, "unresolved")
        a = planmod.build([pr], prof)["albums"][0]
        self.assertTrue(all(fe["dst"].startswith(str(aside) + "/") for fe in a["files"]), a)


class MountGuard(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(_tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_a_folder_fstab_mounts_must_be_mounted(self):
        share = self.tmp / "nas"
        (share / "music").mkdir(parents=True)
        fstab = self.tmp / "fstab"
        fstab.write_text(f"UUID=1 / ext4 defaults 0 1\n//nas/music {share} cifs noauto 0 0\nnone swap swap 0 0\n")
        with mock.patch.object(planmod, "FSTAB", str(fstab)):
            self.assertEqual(planmod.fstab_mounts(), [str(share)])
            self.assertFalse(planmod.mounted(share / "music"))
            with mock.patch.object(planmod.os.path, "ismount", side_effect=lambda p: str(p) == str(share)):
                self.assertTrue(planmod.mounted(share / "music"))
            self.assertTrue(planmod.mounted(self.tmp / "elsewhere"))

    def test_always_needs_a_mount_of_its_own(self):
        with mock.patch.object(planmod, "FSTAB", str(self.tmp / "none")), \
                mock.patch.object(planmod, "mount_point", return_value="/"):
            self.assertTrue(planmod.mounted(self.tmp))
            self.assertFalse(planmod.mounted(self.tmp, "always"))


# ---- artwork -----------------------------------------------------------------------------------
class CoverSize(unittest.TestCase):
    def test_the_profiles_size_for_covers_in_tracks(self):
        from PIL import Image
        import artwork
        tmp = Path(_tempfile.mkdtemp())
        try:
            big = tmp / "front.png"
            Image.new("RGB", (2000, 1500), "teal").save(big)

            class P:
                embed_side = 600
            got = artwork._finish(P(), {"front": str(big)}, [])
            with Image.open(got["embed"]) as im:
                self.assertEqual(max(im.size), 600)
            P.embed_side = artwork.EMBED_MAX_SIDE
            got = artwork._finish(P(), {"front": str(big)}, [])
            with Image.open(got["embed"]) as im:
                self.assertEqual(max(im.size), 1200)
            self.assertEqual(artwork.profile_embed_side({"embed_max_side": 0}), 0)
            self.assertEqual(artwork.embed_cap(pf.from_dict({"name": "x", "artwork": {"embed_max_side": 800}})), 800)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


# ---- this computer's settings ---------------------------------------------------------------
class ComputerSettings(Base):
    def test_backups_kept_and_cleaned_as_this_computer_says(self):
        import executor
        app_settings.save(dict(app_settings.DEFAULTS))
        self.assertEqual(executor.retention_days(), 30)
        jobs = executor.jobs_dir()
        old = jobs / "20200101-000000-aaaa"
        (old / "blobs").mkdir(parents=True)
        (old / "journal.jsonl").write_text(json.dumps({"ev": "end", "t": time.time() - 40 * 86400}) + "\n")
        self.assertEqual(executor.would_prune(30), [old.name])
        self.assertEqual(executor.would_prune(90), [])
        app_settings.set_value("backups.auto_clean", False)
        self.assertEqual(executor.auto_prune(), [])
        self.assertTrue((old / "blobs").exists())
        app_settings.set_value("backups.keep_days", 0)               # (for ever: never cleaned)
        app_settings.set_value("backups.auto_clean", True)
        self.assertEqual(executor.auto_prune(), [])
        app_settings.set_value("backups.keep_days", 30)
        self.assertIn(old.name, executor.auto_prune())
        self.assertFalse((old / "blobs").exists())

    def test_scan_languages_and_effort(self):
        have = ["eng", "deu", "rus", "jpn"]
        with mock.patch.dict(_os.environ, {"LC_ALL": "", "LC_MESSAGES": "", "LANG": "de_DE.UTF-8"}):
            self.assertEqual(app_settings.ocr_languages(have, app_settings.DEFAULTS), "eng+deu")
            self.assertEqual(app_settings.ocr_languages(["eng"], app_settings.DEFAULTS), "eng")
        old = app_settings._merge(app_settings.DEFAULTS, app_settings.EXISTING_INSTALL)
        self.assertEqual(app_settings.ocr_languages(have, old), "eng+rus+jpn")
        self.assertEqual(app_settings.ocr_images(app_settings._merge(app_settings.DEFAULTS,
                                                                     {"ocr": {"effort": "thorough"}})), 40)
        self.assertEqual(app_settings.ocr_images(app_settings.DEFAULTS), 15)

    def test_saved_lookups_refreshed_when_processed_again(self):
        app_settings.save(dict(app_settings.DEFAULTS))
        e = engine.Engine(pf.from_dict({"name": "x"}), cache=str(self.tmp / "cache.sqlite"))
        self.assertEqual(e.http.max_age, 30 * 86400)
        app_settings.set_value("network.refresh_days", 0)
        e = engine.Engine(pf.from_dict({"name": "x"}), cache=str(self.tmp / "cache2.sqlite"))
        self.assertFalse(e.http.max_age)


# ---- Test Connection ----------------------------------------------------------------------------
class Answer(io.BytesIO):
    def __init__(self, body=b"{}", status=200):
        super().__init__(body)
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


MB_ANSWER = b'{"id": "89ad4ac3-39f7-470e-963a-56509c546377", "name": "Various Artists"}'


def opener(table, seen=None):
    """urlopen's stand-in: table maps a part of the address to (status, body) or an exception."""
    def open_(req, timeout=None):
        if seen is not None:
            seen.append(req)
        url = req.full_url
        for part, (code, body) in table.items():
            if part in url:
                if isinstance(code, Exception):
                    raise code
                if code >= 400:
                    raise urllib.error.HTTPError(url, code, "x", {}, io.BytesIO(body))
                return Answer(body, code)
        raise urllib.error.URLError("no such host")
    return open_


class Connection(Base):
    def setUp(self):
        super().setUp()
        import connection
        self.c = connection
        self.env2 = mock.patch.dict(_os.environ, {"DISCOGS_TOKEN": "", "TAGMENDER_ACOUSTID_KEY": ""})
        self.env2.start()

    def tearDown(self):
        self.env2.stop()
        super().tearDown()

    def test_each_answer_in_plain_words(self):
        c = self.c
        ok = opener({"musicbrainz.org": (200, MB_ANSWER), "coverartarchive.org": (404, b"")})
        r = c.test("musicbrainz", ok)
        self.assertEqual(r.state, c.OK)
        self.assertTrue(c.words(r).startswith("answered in"))
        self.assertEqual(c.test("coverartarchive", ok).state, c.OK)              # (404: it answered)
        busy = c.test("musicbrainz", opener({"musicbrainz.org": (503, b"")}))
        self.assertEqual((busy.state, c.words(busy)), (c.BUSY, "busy (503)"))
        down = c.test("musicbrainz", opener({}))
        self.assertEqual((down.state, c.words(down)), (c.UNREACHABLE, "unreachable"))
        self.assertEqual(c.test("musicbrainz", opener({"musicbrainz.org": (TimeoutError("slow"), b"")})).state,
                         c.UNREACHABLE)

    def test_a_refusal_or_an_error_page_is_not_an_answer(self):
        c = self.c
        for code, body in ((403, b""), (407, b""), (200, b"<html>log in to the Wi-Fi</html>"), (200, b"{}")):
            r = c.test("musicbrainz", opener({"musicbrainz.org": (code, body)}))
            self.assertEqual(r.state, c.UNREACHABLE, (code, body))
        self.assertEqual(c.test("discogs", opener({"api.discogs.com": (200, b"<html>login</html>")})).state,
                         c.UNREACHABLE)
        self.assertEqual(c.test("coverartarchive", opener({"coverartarchive.org": (400, b"")})).state, c.OK)

    def test_keys_accepted_refused_or_not_set(self):
        c = self.c
        self.assertEqual(c.test("discogs", opener({"api.discogs.com": (200, b"{}")})).state, c.NO_KEY)
        seen = []
        with mock.patch.dict(_os.environ, {"DISCOGS_TOKEN": "tok-123"}):
            bad = c.test("discogs", opener({"oauth/identity": (401, b"{}")}, seen))
            good = c.test("discogs", opener({"oauth/identity": (200, b"{}")}))
        self.assertEqual((bad.state, c.words(bad)), (c.REFUSED, "key refused"))
        self.assertEqual(good.state, c.OK)
        self.assertNotIn("tok-123", bad.detail + good.detail)                     # (never shown)
        self.assertEqual(seen[0].get_header("Authorization"), "Discogs token=tok-123")
        refused = json.dumps({"status": "error", "error": {"code": 4, "message": "invalid API key"}}).encode()
        missing = json.dumps({"status": "error", "error": {"code": 3, "message": "invalid fingerprint"}}).encode()
        with mock.patch.dict(_os.environ, {"TAGMENDER_ACOUSTID_KEY": "k"}):
            self.assertEqual(c.test("acoustid", opener({"acoustid.org": (400, refused)})).state, c.REFUSED)
            self.assertEqual(c.test("acoustid", opener({"acoustid.org": (400, missing)})).state, c.OK)
        self.assertEqual(c.test("acoustid", opener({"acoustid.org": (400, missing)})).state, c.NO_KEY)

    def test_the_user_agent_names_the_app_never_a_person(self):
        import re
        import artwork
        import mb_account
        import web_tag
        seen = []
        self.c.test("musicbrainz", opener({"musicbrainz.org": (200, MB_ANSWER)}, seen))
        ua = seen[0].get_header("User-agent")
        pat = r"TagMender/[0-9][^ ]* \( https://github\.com/[\w-]+/tagmender \)"
        for agent in (ua, ma.USER_AGENT, artwork.USER_AGENT, mb_account.USER_AGENT, web_tag.USER_AGENT):
            self.assertRegex(agent, pat)
            self.assertNotIn("@", agent)

    def test_doctor_reports_each_service(self):
        import doctor
        found = doctor.connection_checks(opener({"musicbrainz.org": (200, MB_ANSWER), "api.discogs.com": (503, b""),
                                                 "coverartarchive.org": (400, b"")}))
        by = {c.name: c for c in found}
        self.assertEqual(by["MusicBrainz connection"].status, "ok")
        self.assertEqual(by["Discogs connection"].status, "warn")
        self.assertIn("busy", by["Discogs connection"].detail)
        self.assertEqual(by["AcoustID connection"].status, "warn")             # (no answer at all)
        import tagmender
        import argparse
        ap = argparse.ArgumentParser()
        tagmender.doctor_parser(ap.add_subparsers())
        self.assertTrue(ap.parse_args(["doctor", "--connection"]).connection)

    def test_the_runs_own_requests_say_busy_or_down(self):
        db_path = self.tmp / "cache.sqlite"
        db = sqlite3.connect(db_path)
        db.execute("CREATE TABLE provider_health (host TEXT PRIMARY KEY, fails INT, last_error TEXT, "
                   "last_fail REAL, last_ok REAL)")
        now = time.time()
        db.executemany("INSERT INTO provider_health VALUES (?,?,?,?,?)",
                       [("mb", 2, "HTTP 503", now - 10, now - 100), ("discogs", 4, "URLError: no route", now - 5, 0),
                        ("acoustid", 1, "HTTP 500", now - 5, now - 1), ("coverartarchive", 5, "x", now - 9999, 0)])
        db.commit()
        db.close()
        st = self.c.status(db_path, now=now)
        self.assertEqual(st, {"musicbrainz": "busy", "discogs": "down"})
        self.assertIn("MusicBrainz is busy", self.c.status_words(st))
        self.assertEqual(self.c.status(self.tmp / "nothing.sqlite"), {})

    def test_a_refused_request_or_one_slow_cover_is_not_down(self):
        db_path = self.tmp / "cache2.sqlite"
        self.c.record("mb", "HTTP 400", db_path)              # (it answered: that request was refused)
        self.assertEqual(self.c.status(db_path), {})
        self.c.record("mb", "HTTP 429", db_path)
        self.assertEqual(self.c.status(db_path), {"musicbrainz": "busy"})
        self.c.record("coverartarchive", "TimeoutError", db_path)     # (a cover is never retried)
        self.assertNotIn("coverartarchive", self.c.status(db_path))
        for _ in range(2):
            self.c.record("coverartarchive", "TimeoutError", db_path)
        self.assertEqual(self.c.status(db_path)["coverartarchive"], "down")


if __name__ == "__main__":
    unittest.main()
