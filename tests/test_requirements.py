# Tests for requirements.py (everything a user may set up) and where its notices are used:
# doctor, `tagmender setup`, the engine's run notes, CD lookups, the MusicBrainz account and
# AcoustID submissions. Synthetic keys only; nothing of this machine's settings is read.
import os as _os, tempfile as _tempfile  # noqa: E402
_os.environ["XDG_CONFIG_HOME"] = _tempfile.mkdtemp(prefix="tm-config-")
_os.environ["XDG_STATE_HOME"] = _tempfile.mkdtemp(prefix="tm-state-")

import argparse
import contextlib
import io
import json
import shutil
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
import credentials as cr  # noqa: E402
import exclusions as ex  # noqa: E402
import requirements as rq  # noqa: E402

SECRET = "SYNTHETIC-SECRET-1234567890"


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.saved = (ex.CONFIG_DIR, ex.STATE_DIR)
        ex.CONFIG_DIR, ex.STATE_DIR = self.tmp / "config", self.tmp / "state"
        self.env = mock.patch.dict(_os.environ, {})
        self.env.start()
        for k in cr.ENV.values():
            _os.environ.pop(k, None)

    def tearDown(self):
        self.env.stop()
        ex.CONFIG_DIR, ex.STATE_DIR = self.saved
        shutil.rmtree(self.tmp, ignore_errors=True)


class Registry(Base):
    def test_every_entry_is_complete(self):
        self.assertEqual(len(rq.ids()), len(set(rq.ids())))
        for e in rq.ENTRIES:
            self.assertIn(e.kind, rq.KINDS, e.id)
            self.assertIn(e.group, rq.GROUP_TITLES, e.id)
            for f in ("title", "enables", "without", "off"):
                self.assertTrue(getattr(e, f), (e.id, f))
            self.assertTrue(e.url.startswith("https://"), e.id)
            if e.kind == "key":
                self.assertIn(e.credential, cr.FILES, e.id)
                self.assertTrue(e.env and e.file, e.id)
            if e.kind in ("program", "module"):
                self.assertTrue(e.install, e.id)
        # every key credentials.py knows is in the registry
        self.assertEqual({e.credential for e in rq.ENTRIES if e.kind == "key"}, set(cr.FILES))
        for want in ("discogs", "acoustid", "acoustid_user", "mb_client_id", "mb_client_secret",
                     "musicbrainz_login", "gnudb", "fanart", "audiodb", "lastfm", "mojeek",
                     "marginalia", "tavily", "fpcalc", "tesseract", "pyzbar", "ffmpeg", "discid",
                     "qt_multimedia"):
            self.assertIn(want, rq.ids())

    def test_key_status_and_source(self):
        self.assertEqual((rq.status("discogs"), rq.source("discogs"), rq.ready("discogs")), (rq.NOT_SET, "", False))
        self.assertTrue(rq.nothing_set_up())
        rq.store("discogs", SECRET)
        self.assertEqual((rq.status("discogs"), rq.source("discogs"), rq.ready("discogs")), (rq.SET, "file", True))
        self.assertFalse(rq.nothing_set_up())
        self.assertEqual(rq.problem("discogs"), "")
        self.assertTrue(rq.remove("discogs"))
        self.assertFalse(rq.remove("discogs"))
        _os.environ["DISCOGS_TOKEN"] = SECRET
        self.assertEqual((rq.status("discogs"), rq.source("discogs")), (rq.SET, "environment"))

    def test_unsafe_file_permission(self):
        p = rq.store("gnudb", "someone@example.invalid")
        p.chmod(0o644)
        self.assertEqual(rq.status("gnudb"), rq.UNSAFE)
        self.assertFalse(rq.ready("gnudb"))
        self.assertFalse(rq.nothing_set_up())               # (something is there: Setup says fix it)
        text = rq.problem("gnudb")
        self.assertIn("chmod 600", text)
        self.assertIn(str(p), text)
        self.assertNotIn("someone@example.invalid", text)

    def test_login_status_never_reads_the_tokens(self):
        p = rq.path_of("musicbrainz_login")
        self.assertEqual(rq.status("musicbrainz_login"), rq.NOT_SET)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"refresh_token": SECRET, "username": "u"}))
        p.chmod(0o600)
        self.assertEqual(rq.status("musicbrainz_login"), rq.SET)
        p.chmod(0o640)
        self.assertEqual(rq.status("musicbrainz_login"), rq.UNSAFE)
        self.assertIn("chmod 600", rq.problem("musicbrainz_login"))
        self.assertNotIn(SECRET, json.dumps(rq.report()))

    def test_programs_and_modules(self):
        with mock.patch("shutil.which", return_value=None):
            self.assertEqual(rq.status("fpcalc"), rq.MISSING)
            text = rq.problem("fpcalc")
        self.assertIn("libchromaprint-tools", text)
        self.assertTrue(text.startswith("Fingerprints (AcoustID) are not used: fpcalc"))
        with mock.patch("shutil.which", return_value="/usr/bin/fpcalc"):
            self.assertEqual((rq.status("fpcalc"), rq.problem("fpcalc")), (rq.INSTALLED, ""))
        with mock.patch.object(rq, "_module_found", return_value=False):
            self.assertEqual(rq.status("discid"), rq.MISSING)
            self.assertIn("python-discid", rq.problem("discid"))
        with mock.patch.dict(sys.modules, {"discid": types.ModuleType("discid")}):
            self.assertEqual(rq.status("discid"), rq.INSTALLED)

    def test_notices_say_what_where_and_how(self):
        self.assertEqual(rq.notice("discogs", "app"), "Discogs is not used: no Discogs token. Add one in Setup "
                                                     "(get it at https://www.discogs.com/settings/developers).")
        self.assertIn("`tagmender setup discogs`", rq.notice("discogs"))
        self.assertIn("tagmender setup acoustid", rq.notice("acoustid", "cli"))
        self.assertNotIn("in Setup", rq.notice("acoustid", "cli"))
        self.assertIn("Installed application", rq.notice("mb_client_id"))
        self.assertIn("not logged in", rq.notice("musicbrainz_login"))
        for i in rq.ids():
            text = rq.notice(i, state=rq.NOT_SET if rq.entry(i).kind in ("key", "login") else rq.MISSING)
            self.assertTrue(text.endswith("."), i)
            self.assertNotIn("Picard", text, i)
        self.assertEqual(rq.problem("marginalia"), "")       # (the shared public key works unset)

    def test_report_and_store_never_hold_a_value(self):
        for name in cr.FILES:
            rq.store(name, f"{SECRET}-{name}")
        blob = json.dumps(rq.report())
        self.assertNotIn(SECRET, blob)
        self.assertTrue(all(r["status"] == rq.SET for r in rq.report() if r["kind"] == "key"))
        with self.assertRaises(ValueError):
            rq.store("fpcalc", "x")
        with self.assertRaises(ValueError):
            rq.store("discogs", "  ")

    def test_notice_once(self):
        said = []
        self.assertTrue(rq.notice_once("tavily", said.append))
        self.assertEqual(rq.notice_once("tavily", said.append), "")
        self.assertEqual(len(said), 1)


class Doctor(Base):
    def test_registry_checks_grouped_with_notices_and_no_secret(self):
        import doctor
        rq.store("acoustid", SECRET)
        found = {c.name: c for c in doctor.registry_checks()}
        self.assertEqual((found["Discogs token"].status, found["Discogs token"].group), ("fail", rq.RECOMMENDED))
        self.assertIn("Discogs is not used", found["Discogs token"].fix)
        app = found["AcoustID application key"]
        self.assertEqual((app.status, app.detail), ("ok", "set (file)"))
        self.assertEqual(found["fanart.tv API key"].group, rq.OPTIONAL)
        self.assertFalse([n for n in found if "mojeek" in n])      # (web_search_checks says those)
        self.assertNotIn(SECRET, json.dumps([c.__dict__ for c in found.values()]))

    def test_cli_doctor_prints_the_groups(self):
        import doctor
        import tagmender
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()), \
                mock.patch.dict(_os.environ, {"TAGMENDER_TAGGER_INI": str(self.tmp / "none.ini")}):
            tagmender.cmd_doctor(argparse.Namespace(json=False, export=None, anonymize=False))
        text = out.getvalue()
        self.assertLess(text.index("Required for best results:"), text.index("Optional:"))
        self.assertLess(text.index("Optional:"), text.index("Other checks:"))
        self.assertIn("note: Discogs is not used: no Discogs token.", text)
        self.assertNotIn("Picard", text[:text.index("Other checks:")])

    def test_setup_walk_asks_keys_and_keeps_on_enter(self):
        import tagmender
        asked = []

        def ask(prompt, secret):
            asked.append(secret)
            return SECRET if not asked[:-1] else ""
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            tagmender._ask_setup(rq.entry("discogs"), rq, ask)          # typed: saved
            tagmender._ask_setup(rq.entry("discogs"), rq, ask)          # Enter: kept
            tagmender._ask_setup(rq.entry("gnudb"), rq, ask)            # (an e-mail: shown as typed)
            with mock.patch("shutil.which", return_value=None):
                tagmender._ask_setup(rq.entry("fpcalc"), rq, ask)       # a program: never asked
        self.assertEqual(asked, [True, True, False])
        self.assertEqual(cr.get("discogs"), SECRET)
        text = out.getvalue()
        self.assertNotIn(SECRET, text)
        self.assertIn("https://www.discogs.com/settings/developers", text)
        self.assertIn("[set (from file)]", text)
        self.assertIn("Install: sudo apt install libchromaprint-tools", text)

    def test_setup_names_unknown_item(self):
        import tagmender
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            code = tagmender.main(["setup", "nonsense"])
        self.assertEqual(code, 2)
        self.assertIn("unknown item", err.getvalue())


class Notices(Base):
    def test_engine_discogs_note_is_the_setup_notice(self):
        import engine
        import profiles as pf
        eng = engine.Engine(pf.from_dict({"name": "default", "providers": {"web": {"enabled": False}}}),
                            http=types.SimpleNamespace(db=None), ocr="off")
        self.assertEqual(eng.notes["discogs"], "missing_credentials: " + rq.notice("discogs", state=rq.NOT_SET))
        self.assertFalse(eng.use_discogs)

    def test_engine_art_key_notes_once_never_an_album_error(self):
        import artwork
        import engine
        import profiles as pf
        eng = engine.Engine(pf.from_dict({"name": "default", "providers": {"web": {"enabled": False}}}),
                            http=types.SimpleNamespace(db=None), ocr="off")
        cu = artwork.CoverUp.__new__(artwork.CoverUp)
        cu.s = {"providers_enabled": ["caa", "fanart", "audiodb", "local"], "fanart_api_key": "",
                "audiodb_api_key": "123", "lastfm_api_key": ""}
        self.assertEqual(cu.missing_keys(), ["fanart"])
        eng._art = [cu]
        eng._note_art_keys()
        eng._note_art_keys()
        self.assertEqual(eng.notes["fanart"], rq.notice("fanart", state=rq.NOT_SET))
        self.assertNotIn("audiodb", eng.notes)
        self.assertIn("fanart", engine.RUN_NOTES)

    def test_ocr_note_names_the_missing_program(self):
        import engine
        with mock.patch("shutil.which", return_value=None):
            note = engine.ocr_note("auto", ["tesseract binary missing -> sudo apt install tesseract-ocr"])
        self.assertIn("Tesseract OCR is not installed", note)
        self.assertIn("scans are not read", note)

    def test_web_engine_not_set_up(self):
        import match_albums as ma
        value, problem = ma.web_engine_setting("mojeek")
        self.assertEqual((value, problem), ("", rq.notice("mojeek", state=rq.NOT_SET)))
        rq.store("tavily", SECRET).chmod(0o644)
        value, problem = ma.web_engine_setting("tavily")
        self.assertEqual(value, "")
        self.assertIn("chmod 600", problem)
        self.assertNotIn(SECRET, problem)

    def test_gnudb_cd_account_and_acoustid_messages(self):
        import acoustid_submit as acs
        import cdlookup
        import match_albums as ma
        import mb_account as mba
        self.assertEqual(ma.gnudb_problem(), rq.notice("gnudb", state=rq.NOT_SET))
        self.assertIn("python-discid", cdlookup.NO_DISCID)
        with self.assertRaises(mba.AccountError) as e:
            mba.Account(http=lambda *a: (500, b"")).client()
        self.assertEqual(str(e.exception), rq.notice("mb_client_id", state=rq.NOT_SET))
        need = acs.missing()
        self.assertIn(rq.notice("acoustid", state=rq.NOT_SET), need)
        self.assertIn(rq.notice("acoustid_user", state=rq.NOT_SET), need)
        with self.assertRaises(acs.SubmitError) as e:
            acs.submit([], app=SECRET, user="")
        self.assertEqual(str(e.exception), rq.notice("acoustid_user", state=rq.NOT_SET))
        self.assertNotIn(SECRET, str(e.exception))


class ThisRound(Base):
    def test_install_commands_work_on_debian_and_ubuntu(self):
        # the commands the Setup page copies, doctor prints and the notices quote
        self.assertEqual(rq.entry("pyzbar").install, "sudo apt install python3-pyzbar")
        self.assertNotIn("python3-discid", rq.entry("discid").install)
        self.assertIn("libdiscid0", rq.entry("discid").install)
        self.assertIn("pip install --user --break-system-packages discid", rq.entry("discid").install)
        self.assertIn("PySide6-Addons", rq.entry("qt_multimedia").install)
        self.assertIn("--break-system-packages", rq.entry("qt_multimedia").install)

    def test_doctor_rows_use_the_public_titles(self):
        import doctor
        names = {c.name for c in doctor.registry_checks()}
        for want in ("Discogs token", "AcoustID application key", "AcoustID user key", "gnudb e-mail",
                     "fanart.tv API key", "TheAudioDB API key", "Last.fm API key", "MusicBrainz client ID"):
            self.assertIn(want, names)
        self.assertFalse({"gnudb key", "discogs key", "fanart key", "audiodb key", "lastfm key"} & names)

    def test_audiodb_without_a_key_uses_the_shared_test_key(self):
        self.assertEqual(rq.status("audiodb"), rq.NOT_SET)
        self.assertTrue(rq.ready("audiodb"))
        self.assertEqual(rq.problem("audiodb"), "")
        self.assertNotIn("not used", rq.notice("audiodb", state=rq.NOT_SET))
        self.assertIn("shared test key", rq.notice("audiodb", state=rq.NOT_SET))
        rq.store("audiodb", SECRET).chmod(0o644)
        self.assertFalse(rq.ready("audiodb"))
        self.assertIn("chmod 600", rq.problem("audiodb"))

    def test_app_notices_are_translated_cli_ones_are_not(self):
        said = []

        def fake_tr(text):
            said.append(text)
            return "<" + text + ">"
        with mock.patch.object(rq, "translate", fake_tr):
            app = rq.notice("discogs", "app", rq.NOT_SET)
            cli = rq.notice("discogs", "cli", rq.NOT_SET)
        self.assertIn(rq.NOTICE_KEY, said)
        self.assertEqual(app, "<<Discogs is not used>: no <Discogs token>. Add one in <Setup> (<get it at> "
                              "https://www.discogs.com/settings/developers).>")
        self.assertEqual(cli, "Discogs is not used: no Discogs token. Add one in tagmender setup discogs "
                              "(get it at https://www.discogs.com/settings/developers).")
        # a translation that names a placeholder that does not exist: the English one
        with mock.patch.object(rq, "translate", lambda text: text.replace("{url}", "{nothing}")):
            self.assertIn("https://www.discogs.com/settings/developers", rq.notice("discogs", "app", rq.NOT_SET))

    def test_doctor_reads_the_same_tagger_settings_file(self):
        import doctor
        ini = self.tmp / "tagger.ini"
        ini.write_text("[setting]\nclear_existing_tags=true\npreserved_tags=\n")
        with mock.patch.dict(_os.environ, {"TAGMENDER_TAGGER_INI": str(ini)}):
            found = {c.name: c for c in doctor.checks()}
        self.assertEqual(found["other tagger keeps TAGMENDER tags"].status, "warn")
        with mock.patch.dict(_os.environ, {"TAGMENDER_TAGGER_INI": str(self.tmp / "none.ini")}):
            found = {c.name: c for c in doctor.checks()}
        self.assertNotIn("other tagger keeps TAGMENDER tags", found)
        self.assertIn("no tagger settings found", found["tagging settings"].detail)

    def test_dupes_fingerprint_says_what_is_missing(self):
        import dupes_fp
        root = self.tmp / "lib"
        root.mkdir()
        err = io.StringIO()
        with mock.patch("shutil.which", return_value=None), contextlib.redirect_stderr(err), \
                contextlib.redirect_stdout(io.StringIO()):
            dupes_fp.main([str(root), "--fingerprint", "--out", str(self.tmp / "d.csv"),
                           "--cache-db", str(self.tmp / "c.db")])
        text = err.getvalue()
        self.assertIn("fpcalc (Chromaprint) is not installed", text)
        self.assertIn("no AcoustID application key", text)
        self.assertIn("tagmender setup acoustid", text)
        self.assertEqual(text.count("no AcoustID application key"), 1)

    def test_inbox_without_discogs_token_says_what_and_where(self):
        import inbox
        with self.assertRaises(SystemExit) as e, contextlib.redirect_stderr(io.StringIO()):
            inbox.main(["--inbox", str(self.tmp / "in"), "--library", str(self.tmp / "lib"),
                        "--out-dir", str(self.tmp / "out"),
                        "--once"])
        self.assertEqual(str(e.exception.code), "This tool needs a Discogs token. "
                         + rq.notice("discogs", "cli", rq.NOT_SET))


if __name__ == "__main__":
    unittest.main()
