# Tests for reasons.py, logsetup.py, credentials.py, doctor.py and their commands.
import os as _os, tempfile as _tempfile  # noqa: E402
_os.environ["XDG_CONFIG_HOME"] = _tempfile.mkdtemp(prefix="tm-config-")
_os.environ["XDG_STATE_HOME"] = _tempfile.mkdtemp(prefix="tm-state-")

import contextlib
import io
import json
import logging
import multiprocessing
import shutil
import stat
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
import credentials as cr  # noqa: E402
import doctor  # noqa: E402
import exclusions as ex  # noqa: E402
import logsetup  # noqa: E402
import reasons  # noqa: E402
import tagmender  # noqa: E402


def run(*argv, stdin=None):
    out, err = io.StringIO(), io.StringIO()
    saved = sys.stdin
    if stdin is not None:
        sys.stdin = io.StringIO(stdin)
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                code = tagmender.main(list(argv))
            except SystemExit as e:
                code = e.code
    finally:
        sys.stdin = saved
    return code, out.getvalue(), err.getvalue()


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.saved = (ex.CONFIG_DIR, ex.STATE_DIR)
        ex.CONFIG_DIR, ex.STATE_DIR = self.tmp / "config", self.tmp / "state"
        for k in ("DISCOGS_TOKEN", "TAGMENDER_ACOUSTID_KEY", "TAGMENDER_FANART_KEY"):
            _os.environ.pop(k, None)
        logsetup.reset()

    def tearDown(self):
        logsetup.reset()
        ex.CONFIG_DIR, ex.STATE_DIR = self.saved
        shutil.rmtree(self.tmp, ignore_errors=True)


class Reasons(unittest.TestCase):
    def test_old_free_text_gets_codes(self):
        self.assertEqual(reasons.encode("review: track count differs"), "incomplete_album: review: track count differs")
        self.assertEqual(reasons.classify("runner-up 123 within 0.0 points - likely another pressing; "
                                          "only 61% of durations agree"),
                         ["ambiguous_release", "duration_mismatch"])
        self.assertEqual(reasons.classify("not on MusicBrainz, Discogs, Deezer, iTunes or Bandcamp"), ["no_candidate"])
        self.assertEqual(reasons.classify("folder name says vinyl, release is CD"), ["conflicting_sources"])
        self.assertEqual(reasons.classify("something new"), ["other"])

    def test_coded_text_is_kept_and_parsed(self):
        v = "ambiguous_release,conflicting_sources: two pressings"
        self.assertEqual(reasons.encode(v), v)
        self.assertEqual(reasons.parse(v), (["ambiguous_release", "conflicting_sources"], "two pressings"))
        self.assertEqual(reasons.parse("review: track count differs")[0], ["incomplete_album"])
        self.assertEqual(reasons.encode(codes=["rate_limited"]), "rate_limited")

    def test_unknown_prefix_is_not_taken_for_codes(self):
        self.assertTrue(reasons.encode("review: x").startswith("other: review: x"))
        self.assertLessEqual(len(reasons.encode("x" * 1000)), reasons.MAX_LEN)

    def test_every_spec_code_exists(self):
        for c in ("no_candidate", "ambiguous_artist", "ambiguous_release", "conflicting_sources",
                  "web_fallback_only", "musicbrainz_unavailable", "discogs_unavailable", "rate_limited",
                  "unsupported_format"):
            self.assertIn(c, reasons.CODES)


class Logging(Base):
    def test_line_has_job_component_provider_and_no_secret(self):
        log = logsetup.setup("match", job="job-1")
        log.info("GET https://api.discogs.com/database/search?q=x&token=SECRET123 with key=ABC",
                 extra={"provider": "discogs"})
        log.warning("header Authorization: Discogs token=SECRET456; password: hunter2")
        try:
            raise ValueError("boom token=SECRET789")
        except ValueError:
            log.exception("failed")
        text = (logsetup.log_dir() / "tagmender.log").read_text()
        for s in ("SECRET123", "ABC", "SECRET456", "hunter2", "SECRET789"):
            self.assertNotIn(s, text)
        self.assertIn("job=job-1 match provider=discogs", text)
        self.assertIn("token=***", text)
        self.assertIn("WARNING", text)

    def test_rotates_at_the_limit_and_keeps_three_backups(self):
        log = logsetup.setup("t", max_bytes=2000, backups=3)
        for i in range(400):
            log.info("line %d %s", i, "x" * 40)
        files = sorted(p.name for p in logsetup.log_dir().glob("tagmender.log*") if p.suffix != ".lock")
        self.assertEqual(files, ["tagmender.log", "tagmender.log.1", "tagmender.log.2", "tagmender.log.3"])
        for p in logsetup.log_dir().glob("tagmender.log*"):
            self.assertLessEqual(p.stat().st_size, 2000)
        self.assertIn("line 399", (logsetup.log_dir() / "tagmender.log").read_text())

    def test_documented_limit(self):
        self.assertEqual(logsetup.MAX_BYTES, 100 * 1024 * 1024)
        self.assertGreaterEqual(logsetup.BACKUPS, 3)

    def test_levels(self):
        log = logsetup.setup("t", level="WARNING")
        log.info("quiet")
        log.error("loud")
        text = (logsetup.log_dir() / "tagmender.log").read_text()
        self.assertNotIn("quiet", text)
        self.assertIn("loud", text)


def _writer(state_dir, n):
    ex.STATE_DIR = Path(state_dir)
    log = logsetup.setup("w", job=f"p{_os.getpid()}", max_bytes=20_000, backups=3)
    for i in range(n):
        log.info("proc %d line %04d %s", _os.getpid(), i, "y" * 60)


class SharedLog(Base):
    def test_processes_share_the_file_without_losing_or_tearing_lines(self):
        procs = [multiprocessing.get_context("fork").Process(target=_writer, args=(str(ex.STATE_DIR), 150))
                 for _ in range(4)]
        for p in procs:
            p.start()
        for p in procs:
            p.join(60)
        lines = []
        for f in logsetup.log_dir().glob("tagmender.log*"):
            if f.suffix != ".lock":
                lines += f.read_text().splitlines()
        whole = [ln for ln in lines if ln.endswith("y" * 60)]
        self.assertEqual(len(whole), len(lines))                 # no torn lines
        # 4 x 150 lines of ~130 bytes = ~78 KB over 4 files of 20 KB: some were rotated away,
        # but every surviving file stays under the limit
        for f in logsetup.log_dir().glob("tagmender.log*"):
            self.assertLessEqual(f.stat().st_size, 20_000)
        self.assertGreater(len(lines), 300)


class Credentials(Base):
    def test_env_then_private_file(self):
        self.assertEqual(cr.get("discogs"), "")
        p = cr.store("discogs", "filetoken")
        self.assertEqual(stat.S_IMODE(p.stat().st_mode), 0o600)
        self.assertEqual((cr.get("discogs"), cr.source("discogs")), ("filetoken", "file"))
        _os.environ["DISCOGS_TOKEN"] = "envtoken"
        try:
            self.assertEqual((cr.get("discogs"), cr.source("discogs")), ("envtoken", "environment"))
        finally:
            del _os.environ["DISCOGS_TOKEN"]

    def test_file_others_can_read_is_refused(self):
        p = cr.store("discogs", "t")
        p.chmod(0o644)
        with self.assertRaises(cr.CredentialError):
            cr.get("discogs")

    def test_setup_reads_stdin_for_scripts(self):
        code, out, _ = run("setup", "--stdin", "acoustid", stdin="KEY42\n")
        self.assertEqual(code, 0)
        self.assertNotIn("KEY42", out)
        self.assertEqual(cr.get("acoustid"), "KEY42")

    def test_coverup_takes_keys_picard_lacks_but_never_overrides_picard(self):
        import coverup_batch as cb
        cr.store("fanart", "FAN")
        cr.store("discogs", "DISC")
        merged = cb.with_tagmender_keys({"discogs_token": "FROM_PICARD"})
        self.assertEqual((merged["fanart_api_key"], merged["discogs_token"]), ("FAN", "FROM_PICARD"))


class Doctor(Base):
    def test_reports_missing_token_as_failure_with_a_fix(self):
        found = {c.name: c for c in doctor.checks()}
        self.assertEqual(found["discogs key"].status, "fail")
        self.assertIn("tagmender setup", found["discogs key"].fix)
        self.assertEqual(found["python"].status, "ok")
        cr.store("discogs", "SECRET-TOKEN")
        found = {c.name: c for c in doctor.checks()}
        self.assertEqual(found["discogs key"].status, "ok")
        self.assertNotIn("SECRET-TOKEN", json.dumps([c.__dict__ for c in found.values()]))

    def test_unmounted_destination_is_flagged(self):
        import profiles as pf
        pf.save(pf.from_dict({"name": "L", "operation": "copy", "destination": str(self.tmp / "nowhere")}))
        found = {c.name: c for c in doctor.checks()}
        self.assertEqual(found["profile L destination"].status, "warn")

    def test_export_has_no_secrets_and_anonymizes(self):
        cr.store("discogs", "SECRET-TOKEN")
        log = logsetup.setup("x")
        log.info("looked at /media/unraidmedia2/music/Some Band/2001 - Album/01.flac token=SECRET-TOKEN")
        import profiles as pf
        pf.save(pf.from_dict({"name": "Lib", "sources": [{"path": "/media/unraidmedia2/music/Some Band"}]}))
        logsetup.reset()
        z = doctor.export(self.tmp, anonymize_paths=True)
        with zipfile.ZipFile(z) as zf:
            blob = "".join(zf.read(n).decode() for n in zf.namelist())
            names = zf.namelist()
        self.assertNotIn("SECRET-TOKEN", blob)
        self.assertNotIn("Some Band", blob)
        self.assertNotIn("unraidmedia2", blob)
        self.assertIn("/media/", blob)
        self.assertIn(".flac", blob)
        self.assertIn("checks.json", names)
        self.assertFalse([n for n in names if "token" in n or "key" in n])

    def test_command_json_and_exit_code(self):
        code, out, _ = run("doctor", "--json")
        self.assertEqual(code, 1)                      # no Discogs token here
        self.assertTrue(any(c["name"] == "python" for c in json.loads(out)))


if __name__ == "__main__":
    unittest.main()
