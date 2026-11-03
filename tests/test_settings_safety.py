# This computer's settings and the profile settings kept safe: a settings file that cannot be
# read never cleans up backups or moves files by guesswork, Keep Undo carried over from
# profiles is always a choice the app offers, one stored value that stopped working never
# blocks another change, renamed duplicates folders stay skipped, the unidentified albums'
# folder is skipped by its path, the earlier built-in settings follow the tagger as before,
# shares under /srv and /net count as mounts, the web sources a profile chose are the ones
# asked, and Test Connection never shows a key and survives a broken answer.
import os as _os, tempfile as _tempfile  # noqa: E402
_os.environ["XDG_CONFIG_HOME"] = _tempfile.mkdtemp(prefix="tm-config-")
_os.environ["XDG_STATE_HOME"] = _tempfile.mkdtemp(prefix="tm-state-")

import http.client
import io
import json
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

from test_settings_schema3 import Base, tone  # noqa: E402

import app_settings  # noqa: E402
import connection  # noqa: E402
import credentials  # noqa: E402
import engine  # noqa: E402
import executor  # noqa: E402
import plan as planmod  # noqa: E402
import profiles as pf  # noqa: E402
import web_tag  # noqa: E402


def existing_install():
    (pf.ex.STATE_DIR).mkdir(parents=True, exist_ok=True)
    (pf.ex.STATE_DIR / "state.db").write_bytes(b"")


def old_job(name, days):
    """A finished job DAYS old, with its undo backups."""
    d = executor.jobs_dir() / name
    (d / "blobs").mkdir(parents=True)
    (d / "blobs" / "x").write_bytes(b"backup")
    t = time.time() - days * 86400
    (d / "journal.jsonl").write_text(json.dumps({"ev": "begin", "t": t}) + "\n" +
                                     json.dumps({"ev": "end", "t": t}) + "\n")
    return d


class UnreadableSettings(Base):
    def test_a_broken_file_never_cleans_up_or_moves_by_guesswork(self):
        existing_install()
        self.assertFalse(app_settings.load()["backups"]["auto_clean"])
        f = app_settings.path()
        f.write_text(f.read_text().replace('"keep_days": 30', '"keep_days": 90,'))   # a stray comma
        self.assertIn("could not be read", app_settings.read_problem())
        s = app_settings.load()
        self.assertFalse(s["backups"]["auto_clean"])
        self.assertEqual(s["duplicates"]["folder_name"], "2delete")
        self.assertEqual(s["skip_folders"], ["_*", "2delete"])
        job = old_job("20200101-000000-x", 90)
        with mock.patch.object(executor.Journal, "read",
                               lambda self: [{"ev": "end", "t": time.time() - 90 * 86400}]):
            self.assertEqual(executor.auto_prune(), [])
        self.assertTrue((job / "blobs" / "x").exists())
        broken = f.read_text()
        with self.assertRaises(ValueError):
            app_settings.set_value("player.skip_seconds", 10)
        with self.assertRaises(app_settings.Unreadable):
            app_settings.save(dict(app_settings.DEFAULTS))
        self.assertEqual(f.read_text(), broken)                          # never saved over

    def test_a_hand_written_file_fills_in_this_machines_first_settings(self):
        existing_install()
        app_settings.path().parent.mkdir(parents=True, exist_ok=True)
        app_settings.path().write_text(json.dumps({"version": 1, "player": {"skip_seconds": 15}}))
        s = app_settings.load()
        self.assertEqual(s["player"]["skip_seconds"], 15)
        self.assertFalse(s["backups"]["auto_clean"])
        self.assertEqual(s["duplicates"]["folder_name"], "2delete")
        old_job("20200101-000000-y", 90)
        self.assertEqual(executor.auto_prune(), [])        # (the file never asked for cleaning)

    def test_a_clean_file_that_asks_for_cleaning_still_cleans(self):
        app_settings.save(dict(app_settings.DEFAULTS))
        self.assertIs(app_settings.stored("backups.auto_clean"), True)
        with mock.patch.object(executor, "prune", return_value=["j"]) as pr:
            self.assertEqual(executor.auto_prune(), ["j"])
        pr.assert_called_once_with(30)


class KeepDaysCarriedOver(Base):
    def test_a_profiles_retention_becomes_a_choice_never_shorter(self):
        for days, want in ((60, 90), (14, 30), (7, 7), (365, 0), (0, 0)):
            with self.subTest(days=days):
                for f in pf.profiles_dir().glob("*.json") if pf.profiles_dir().exists() else []:
                    f.unlink()
                app_settings.path().unlink(missing_ok=True)
                self.write_profile({"version": 2, "name": "Main", "backup_retention_days": days})
                self.assertEqual(app_settings.load()["backups"]["keep_days"], want)
                self.assertEqual(app_settings.validate(app_settings.load()), [])
                app_settings.set_value("duplicates.folder_name", "Dupes")


class OneBadValue(Base):
    def test_a_plugin_folder_since_removed_blocks_no_other_change(self):
        app_settings.save(dict(app_settings.DEFAULTS))
        folder = self.tmp / "plugins"
        folder.mkdir()
        app_settings.set_value("plugins.folder", str(folder))
        folder.rmdir()
        app_settings.set_value("notifications.finished", False)
        self.assertFalse(app_settings.get("notifications.finished"))
        with self.assertRaises(ValueError):                    # (the folder itself: still checked)
            app_settings.set_value("plugins.folder", str(self.tmp / "nowhere"))

    def test_tesseract_packs_with_an_underscore(self):
        app_settings.save(dict(app_settings.DEFAULTS))
        app_settings.set_value("ocr.languages", ["eng", "chi_sim", "chi_tra"])
        self.assertEqual(app_settings.get("ocr.languages"), ["eng", "chi_sim", "chi_tra"])
        self.assertTrue(app_settings.validate({"ocr": {"languages": ["en g"]}}))


class DuplicatesRenamed(Base):
    def test_the_old_name_stays_skipped(self):
        app_settings.save(dict(app_settings.DEFAULTS))
        prof = pf.from_dict({"name": "New"})
        self.assertTrue(prof.folder_skipper()("_Duplicates"))
        app_settings.set_value("duplicates.folder_name", "Dupes")
        self.assertEqual(app_settings.get("duplicates.previous_names"), ["_Duplicates"])
        for skips in (prof.folder_skipper(), app_settings.tool_skipper()):
            self.assertTrue(skips("_Duplicates"))
            self.assertTrue(skips("Dupes"))
            self.assertFalse(skips("Album"))
        app_settings.set_value("duplicates.folder_name", "_Duplicates")      # (back again: once)
        self.assertEqual(app_settings.get("duplicates.previous_names"), ["_Duplicates", "Dupes"])


class MoveToSkippedByPath(Base):
    def test_an_album_folder_with_the_same_name_is_still_found(self):
        lib = self.tmp / "lib"
        for album in ("Review", "Live"):
            for i in (0, 1):
                tone(lib / "Artist" / album / f"0{i + 1}.flac", i, title=f"T{i}", album=album, artist="Artist")
        aside = self.tmp / "elsewhere" / "Review"
        prof = pf.from_dict({"name": "M", "unresolved": {"action": "move", "move_to": str(aside)}})
        self.assertFalse(prof.folder_skipper()("Review"))
        found = sorted(g.folder.name for g in engine.Engine(prof).albums_in(lib))
        self.assertEqual(found, ["Live", "Review"])
        inside = pf.from_dict({"name": "M2", "unresolved": {"action": "move", "move_to": str(lib / "Artist" / "Review")}})
        self.assertEqual([g.folder.name for g in engine.Engine(inside).albums_in(lib)], ["Live"])


class PreviousBuiltin(Base):
    def test_it_follows_the_tagger_as_the_old_built_in_did(self):
        ini = self.tmp / "tagger.ini"
        ini.write_text("[setting]\nwindows_compatibility=false\ndelete_empty_dirs=false\n")
        with mock.patch.dict(_os.environ, {"TAGMENDER_TAGGER_INI": str(ini), "TAGMENDER_PICARD_INI": str(ini)}):
            d = pf.previous_builtin().data
        self.assertEqual((d["filenames"]["compat"], d["remove_empty_dirs"]), ("linux", False))

    def test_the_note_says_underscore_folders_are_walked_now(self):
        existing_install()
        self.write_profile({"version": 2, "name": "Old"})
        app_settings.path().unlink(missing_ok=True)
        self.assertIn("start with _", pf.builtin_changed_note())


class ProfileChecks(unittest.TestCase):
    def test_lyrics_switches_must_be_true_or_false(self):
        p = pf.validate({"name": "x", "lyrics": {"enabled": "no", "synced": "no", "embed": "yes", "sidecar": 3}})
        for k in ("enabled", "synced", "embed", "sidecar"):
            self.assertIn(f"lyrics.{k} must be true or false", p)

    def test_retry_numbers(self):
        self.assertEqual(pf.validate({"name": "x", "retry": {"after_days": 30, "max_attempts": 30}}), [])
        p = pf.validate({"name": "x", "retry": {"after_days": -1, "max_attempts": 0}})
        self.assertTrue(any(x.startswith("retry.after_days") for x in p))
        self.assertTrue(any(x.startswith("retry.max_attempts") for x in p))

    def test_move_aside_on_a_copy_profile_is_allowed_and_holds(self):
        # (the editor says a profile that copies holds such an album: plan.py does that)
        p = pf.validate({"name": "x", "operation": "copy", "destination": "/srv/music", "quality_action": "move",
                         "unresolved": {"action": "move", "move_to": "/srv/aside"}})
        self.assertEqual(p, [])


class SharesUnderSrv(unittest.TestCase):
    def test_srv_and_net_need_something_mounted(self):
        with mock.patch.object(planmod, "FSTAB", "/nonexistent/fstab"), \
                mock.patch.object(planmod.os.path, "ismount", return_value=False):
            for folder in ("/srv/music", "/net/nas/music", "/mnt/music"):
                self.assertFalse(planmod.mounted(folder), folder)
        with mock.patch.object(planmod, "FSTAB", "/nonexistent/fstab"), \
                mock.patch.object(planmod.os.path, "ismount", side_effect=lambda p: str(p) == "/srv/music"):
            self.assertTrue(planmod.mounted("/srv/music/A"))


class WebSources(unittest.TestCase):
    def test_only_the_chosen_sources_are_asked(self):
        asked = []

        def src(name):
            return name, lambda http, a, t: asked.append(name) or []
        fake = tuple(src(n) for n in ("Deezer", "iTunes", "Bandcamp", "Wikipedia"))
        album = {"artist": "A", "title": "B", "tag_urls": ["https://x.bandcamp.com/album/b"]}
        with mock.patch.object(web_tag, "SOURCES", fake), \
                mock.patch.object(web_tag, "bandcamp_album", side_effect=lambda h, u: asked.append("linked")):
            web_tag.album_candidates(None, album, sources=["wikipedia", "deezer"])
            self.assertEqual(asked, ["Wikipedia", "Deezer"])        # (no linked Bandcamp page either)
            asked.clear()
            web_tag.album_candidates(None, album)
            self.assertEqual(asked, ["linked", "Deezer", "iTunes", "Bandcamp", "Wikipedia"])

    def test_the_engine_passes_the_profiles_list(self):
        prof = pf.from_dict({"name": "W", "providers": {"web": {"enabled": True, "sources": ["itunes"]}}})
        self.assertEqual(engine.Engine(prof)._web_sources(), ["itunes"])


class Answer:
    def __init__(self, body=b"{}", status=200):
        self.body, self.status = body, status

    def read(self, n=-1):
        return self.body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class TestConnection(Base):
    def test_a_token_with_a_line_break_is_never_shown(self):
        secret = "FAKESECRET123\nsecondline"
        with mock.patch.object(credentials, "get", side_effect=lambda n: secret if n == "discogs" else ""):
            r = connection.test("discogs")
        self.assertEqual(r.state, connection.REFUSED)
        self.assertNotIn("FAKESECRET", r.detail)

    def test_an_error_text_never_reaches_the_result(self):
        def raise_value(req, timeout=None):
            raise ValueError(f"Invalid header value {req.get_header('Authorization')!r}")
        with mock.patch.object(credentials, "get", side_effect=lambda n: "FAKESECRET" if n == "discogs" else ""):
            r = connection.test("discogs", opener=raise_value)
        self.assertEqual(r.state, connection.UNREACHABLE)
        self.assertNotIn("FAKESECRET", r.detail)

    def test_a_broken_answer_is_unreachable_not_a_crash(self):
        def broken(req, timeout=None):
            if "musicbrainz" in req.full_url:
                raise http.client.IncompleteRead(b"")
            if "coverartarchive" in req.full_url:
                raise http.client.BadStatusLine("x")
            return Answer()
        with mock.patch.object(credentials, "get", return_value=""):
            got = {r.service: r.state for r in connection.test_all(opener=broken)}
            import doctor
            doctor.connection_checks(opener=broken)
        self.assertEqual(got["musicbrainz"], connection.UNREACHABLE)
        self.assertEqual(got["coverartarchive"], connection.UNREACHABLE)
        self.assertEqual(got["discogs"], connection.NO_KEY)

    def test_one_fully_retried_failure_shows_down(self):
        db_path = self.tmp / "cache.sqlite"
        connection.record("mb", "URLError: Name or service not known", db_path)
        self.assertEqual(connection.status(db_path), {"musicbrainz": "down"})
        connection.record("mb", None, db_path)
        self.assertEqual(connection.status(db_path), {})

    def test_the_cover_art_archive_is_seen_during_a_run(self):
        import artwork

        def busy(req, timeout=None):
            raise urllib.error.HTTPError(req.full_url, 503, "busy", {}, io.BytesIO(b""))
        with mock.patch.object(artwork.urllib.request, "urlopen", busy):
            with self.assertRaises(urllib.error.HTTPError):
                artwork._get("https://coverartarchive.org/release/x/front")
        db_path = pf.ex.STATE_DIR / "cache.sqlite"
        self.assertEqual(connection.status(db_path), {"coverartarchive": "busy"})

        def missing(req, timeout=None):
            raise urllib.error.HTTPError(req.full_url, 404, "none", {}, io.BytesIO(b""))
        with mock.patch.object(artwork.urllib.request, "urlopen", missing):
            with self.assertRaises(urllib.error.HTTPError):
                artwork._get("https://coverartarchive.org/release/y/front")
        self.assertEqual(connection.status(db_path), {})              # (404: it answered)
        con = sqlite3.connect(db_path)
        self.assertEqual(con.execute("SELECT fails FROM provider_health WHERE host='coverartarchive'").fetchone(),
                         (0,))
        con.close()


if __name__ == "__main__":
    unittest.main()
