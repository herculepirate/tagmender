# Tests for report.py (the run report), `tagmender report` and the app's Report sheet: each
# section, folder links, the issue words (missing tracks, two versions mixed, a split, a
# conflict, a release without track lengths), suggested releases with Apply, and the exports.
# Synthetic plans and jobs; the end-to-end test uses generated audio.
import os as _os, tempfile as _tempfile  # noqa: E402
_os.environ["XDG_CONFIG_HOME"] = _tempfile.mkdtemp(prefix="tm-config-")
_os.environ["XDG_STATE_HOME"] = _tempfile.mkdtemp(prefix="tm-state-")
_os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import contextlib
import csv
import io
import json
import sys
import time
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "tests"))
import test_engine as te  # noqa: E402
import engine  # noqa: E402
import executor  # noqa: E402
import plan as pl  # noqa: E402
import report  # noqa: E402
import tagmender  # noqa: E402

try:
    from PySide6.QtWidgets import QApplication
except ImportError:                                           # the app is optional
    QApplication = None

MB = "11111111-2222-3333-4444-555555555555"


def ident(tier="exact", status="identified", reason="", why=(), evidence=(), cands=(), provider="musicbrainz",
          rid=MB):
    return {"status": status, "provider": provider, "release_id": rid if status == "identified" else "",
            "url": f"https://musicbrainz.org/release/{rid}" if status == "identified" else "",
            "confidence": 90.0, "margin": None, "why": list(why), "evidence": list(evidence), "reason_codes": [],
            "provenance": {}, "provider_errors": {}, "tier": tier, "candidates": list(cands),
            **({"reason": reason} if reason else {})}


def cand(source="discogs", rid="4242", title="Band - Album (1999)", score=80.0, lengths=None):
    return {"source": source, "id": rid, "url": engine.release_url("mb" if source == "musicbrainz" else source, rid),
            "ref": rid if source == "musicbrainz" else f"discogs:{rid}", "score": score, "title": title,
            "lengths": lengths}


def album(folder, status="ready", tier="exact", n=3, op="tag", dst_dir=None, notes=(), **kw):
    files = [{"src": f"{folder}/{i:02d}.flac", "dst": f"{dst_dir or folder}/{i:02d}.flac",
              "op": op, "changes": {"album": ["A"]}, "diff": [{"field": "album", "action": "changed"}],
              "fingerprint": {}} for i in range(1, n + 1)]
    a = {"source_dir": folder, "status": status, "label": "", "rule": None,
         "identification": kw.pop("identification", None) or ident(tier),
         "release": {"album": [Path(folder).name], "albumartist": ["Band"]}, "files": files, "companions": [],
         "artwork": None, "conflicts": [], "skipped": [], "notes": list(notes),
         "album_files": [f["src"] for f in files]}
    a.update(kw)
    return a


def make_plan(albums, job="20261101-120000-abcd"):
    return {"version": pl.PLAN_VERSION, "job": job, "created": time.time(), "profile": "Library", "profile_data": {},
            "operation": "in_place", "roots": [], "albums": albums, "summary": {}}


def section(rep, key):
    return [r for r in rep["rows"] if r["section"] == key]


class Building(unittest.TestCase):
    def test_partial_album_with_missing_tracks_and_suggestions(self):
        a = album("/music/Band/Live", tier="partial", n=24,
                  identification=ident("partial", reason="ambiguous_release: two pressings fit",
                                       cands=[cand(), cand("musicbrainz", MB, "Band - Live (2001)")]),
                  notes=["tracks 1-8 of the release are not in this folder: each file is tagged as its own track, "
                         "with the release's totals, and TAGMENDER_MISSING lists the gaps"])
        rep = report.build(make_plan([a]))
        decide, done = section(rep, "decide"), section(rep, "done")
        self.assertEqual(len(decide), 1)
        self.assertIn("release is not proven", decide[0]["issue"])
        self.assertEqual(decide[0]["uri"], "file:///music/Band/Live")
        self.assertEqual([c["url"] for c in decide[0]["candidates"]],
                         ["https://www.discogs.com/release/4242", f"https://musicbrainz.org/release/{MB}"])
        self.assertIn("8 tracks of 32 missing: tagged the 24 present", done[0]["issue"])
        self.assertEqual(done[0]["candidates"], [])
        self.assertEqual(report.apply_command(decide[0], decide[0]["candidates"][0]),
                         "tagmender plan /music/Band/Live --release discogs:4242")

    def test_release_without_track_lengths_needs_a_decision(self):
        """The feared case: the best release lists no lengths - listed with its Discogs link and Apply."""
        a = album("/music/Band/Demo", status="unresolved", tier="unresolved",
                  identification=ident("unresolved", "unresolved", reason="duration_mismatch: durations unavailable",
                                       why=["durations unavailable"], cands=[cand(lengths=False)]))
        rep = report.build(make_plan([a]))
        d = section(rep, "decide")
        self.assertEqual(len(d), 1)
        self.assertIn(report.NO_LENGTHS, d[0]["issue"])
        self.assertEqual(d[0]["candidates"][0]["url"], "https://www.discogs.com/release/4242")
        self.assertIn("--release discogs:4242", report.text(rep))
        self.assertEqual(len(section(rep, "failed")), 1)            # (and it was not identified)

    def test_two_versions_mixed_and_split(self):
        mixed = album("/music/Band/Both", status="unresolved", tier="unresolved",
                      identification=ident("unresolved", "unresolved",
                                           reason="other: two versions of the album are mixed in this folder (10 "
                                                  "titles twice): split them into separate folders (not split for "
                                                  "you: lengths too close)"))
        why = [f'2 versions of this album were in one folder (MusicBrainz release {MB} "Album (Remaster 2024)": a)']
        v1 = album("/music/Band/Two", identification=ident(why=why), dst_dir="/music/Band/Album (Remaster 2024)",
                   op="move", split={"of": "/music/Band/Two", "index": 0, "count": 2,
                                     "folder": "/music/Band/Album (Remaster 2024)"})
        rep = report.build(make_plan([mixed, v1]))
        issues = {r["folder"]: r for r in section(rep, "decide")}
        self.assertIn("two versions mixed in one folder (lengths too close)", issues["/music/Band/Both"]["issue"])
        self.assertEqual(issues["/music/Band/Both"]["status"], "Mixed")
        self.assertIn("version 1 of 2 is Album (Remaster 2024)", issues["/music/Band/Two"]["issue"])
        self.assertEqual(issues["/music/Band/Two"]["album"], "Album (Remaster 2024)")
        self.assertEqual(report.apply_command(issues["/music/Band/Two"], issues["/music/Band/Two"]["candidates"][0]),
                         "")                                        # (one version's files: in the app)
        self.assertFalse(section(rep, "failed"))
        # applied: done, in its new folder, nothing left to decide
        done = report.build(make_plan([v1]), [(v1["source_dir"], "done", "Done", "", 1)], job={"status": "done"})
        self.assertFalse(section(done, "decide"))
        row = section(done, "done")[0]
        self.assertEqual(row["folder"], "/music/Band/Album (Remaster 2024)")
        self.assertIn("moved to /music/Band/Album (Remaster 2024)", row["issue"])

    def test_conflicts_failures_and_stopped_albums(self):
        blocked = album("/music/A", status="blocked", conflicts=["the folder Album already exists"])
        failed, never = album("/music/B"), album("/music/C")
        rep = report.build(make_plan([blocked, failed, never]),
                           [("/music/A", "blocked", "Blocked", "", 1),
                            ("/music/B", "failed", "Failed", "could not write 01.flac: permission denied", 2)],
                           job={"status": "partial"})
        f = {r["folder"]: r for r in section(rep, "failed")}
        self.assertEqual(f["/music/A"]["issue"], "blocked: the folder Album already exists")
        self.assertIn("permission denied", f["/music/B"]["issue"])
        self.assertEqual(f["/music/C"]["issue"], "stopped before this album was reached")
        self.assertFalse(section(rep, "done"))

    def test_done_words_and_excluded_left_out(self):
        a = album("/music/D", companions=[{"src": "/music/D/Scans/1.jpg", "dst": "/music/D/covers/1.jpg", "op": "move",
                                           "tidy": "covers"}])
        ex_ = album("/music/E", status="excluded")
        rep = report.build(make_plan([a, ex_]), [("/music/D", "done", "Done", "", 1),
                                                  ("/music/E", "excluded", "Ignored", "", 2)], job={"status": "done"})
        self.assertEqual([r["folder"] for r in rep["rows"]], ["/music/D"])
        self.assertEqual(rep["rows"][0]["issue"], "tags written to 3 files; 1 file into the covers folder")
        self.assertEqual(rep["counts"], {"decide": 0, "failed": 0, "done": 1})

    def test_undone_job_says_so(self):
        rep = report.build(make_plan([album("/music/D")]), [("/music/D", "done", "Done", "", 1)],
                           job={"status": "undone"})
        self.assertIn("undone", rep["note"])
        self.assertIn("undone", report.text(rep))

    def test_an_undone_job_links_where_the_albums_are_again(self):
        """Undo put the files back: each row's folder, link, files and Apply folder are the
        album's own folder, not the one the job moved it into (gone now)."""
        a = album("/in/A", op="move", dst_dir="/lib/Band/1994 - A", tier="partial",
                  identification=ident("partial", cands=[cand()]))
        rep = report.build(make_plan([a]), [("/in/A", "done", "Done", "", 1)], job={"status": "undone"})
        self.assertEqual(sorted(r["section"] for r in rep["rows"]), ["decide", "done"])
        for r in rep["rows"]:
            self.assertEqual((r["folder"], r["uri"], r["apply_folder"]), ("/in/A", "file:///in/A", "/in/A"))
            self.assertTrue(all(f.startswith("/in/A/") for f in r["files"]), r["files"])
        rep = report.build(make_plan([a]), [("/in/A", "done", "Done", "", 1)], job={"status": "done"})
        self.assertEqual({r["folder"] for r in rep["rows"]}, {"/lib/Band/1994 - A"})

    def test_executor_results_of_a_folder_name_that_is_not_utf8(self):
        odd = _os.fsdecode(b"/x/Art/Alb\xe9um")
        rep = report.build(make_plan([album(odd)]), [{"album": odd, "index": 0, "status": "done", "label": "Done",
                                                      "detail": ""}], job={"status": "done"})
        self.assertEqual([(r["section"], r["status"]) for r in rep["rows"]], [("done", "Done")])
        # (as the database stores it, and without an index: matched by folder either way)
        for rows in ([(pl.db_text(odd), "done", "Done", "", 1)], [{"album": odd, "status": "done"}]):
            rep = report.build(make_plan([album("/x/B"), album(odd)]), rows, job={"status": "done"})
            self.assertEqual({(r["folder"], r["section"]) for r in rep["rows"]},
                             {(odd, "done"), ("/x/B", "failed")}, rows)

    def test_an_unresolved_album_moved_aside_links_where_it_went(self):
        a = album("/in/Art/Alb", status="unresolved", tier="unresolved", op="move", dst_dir="/lib/_Unresolved/Alb",
                  identification=ident("unresolved", status="unresolved", reason="nothing", cands=[cand()]))
        rep = report.build(make_plan([a]), [("/in/Art/Alb", "done", "Done", "", 1)], job={"status": "done"})
        f = section(rep, "failed")[0]
        self.assertEqual((f["folder"], f["apply_folder"]), ("/lib/_Unresolved/Alb", "/lib/_Unresolved/Alb"))
        self.assertTrue(all(x.startswith("/lib/_Unresolved/Alb/") for x in f["files"]), f["files"])
        rep = report.build(make_plan([a]), [("/in/Art/Alb", "failed", "Failed", "no", 1)], job={"status": "partial"})
        self.assertEqual(section(rep, "failed")[0]["apply_folder"], "/in/Art/Alb")

    def test_apply_command_names_the_jobs_profile(self):
        row = {"source_dir": "/inbox/X", "split": False}
        self.assertEqual(report.apply_command(row, {"ref": "discogs:123"}, "My Library"),
                         "tagmender plan /inbox/X --release discogs:123 --profile 'My Library'")
        self.assertEqual(report.apply_command(row, {"ref": "discogs:123"}, "default"),
                         "tagmender plan /inbox/X --release discogs:123")

    def test_csv_cells_are_never_formulas(self):
        a = album("/music/-x-", release={"album": ["=1+1"]})
        rows = list(csv.reader(io.StringIO(report.csv_text(report.build(make_plan([a]))))))
        self.assertEqual(rows[1][1], "'=1+1")
        self.assertEqual(rows[1][2], "/music/-x-")
        self.assertEqual(report._cell("@x"), "'@x")
        self.assertEqual(report._cell("+1"), "'+1")
        self.assertEqual(report._cell("Band - Album"), "Band - Album")

    def test_a_file_that_is_not_a_plan(self):
        bad = Path(_tempfile.mkdtemp()) / "x.json"
        bad.write_text("[1, 2]")
        with self.assertRaises(ValueError):
            report.for_plan(bad)

    def test_attention_rows(self):
        rows = [{"folder": "/music/P", "codes": ["ambiguous_release"], "reason": "two pressings", "mark": "Partial"},
                {"folder": "/music/U", "codes": ["no_candidate"], "reason": "", "mark": "Unresolved"}]
        rep = report.for_attention(rows, look_up=False)
        self.assertEqual([r["folder"] for r in section(rep, "decide")], ["/music/P"])
        self.assertEqual(section(rep, "failed")[0]["issue"], "not found on MusicBrainz or Discogs")

    def test_candidates_from_the_matcher(self):
        out = {"best": {"source": "discogs", "id": 77, "artist": "Band", "title": "Album", "year": 1999,
                        "confidence": 71.5, "notes": ["durations unavailable (barcode decides)"]},
               "alts": ["discogs:77 71.5 Band - Album (1999)", f"mb:{MB} 70.0 Band - Album (2001)", "junk"]}
        got = engine.candidates_of(out)
        self.assertEqual([(c["source"], c["ref"], c["lengths"]) for c in got],
                         [("discogs", "discogs:77", False), ("musicbrainz", MB, None)])
        self.assertEqual(got[0]["title"], "Band - Album (1999)")
        self.assertEqual(engine.candidates_of(None), [])

    def test_missing_count(self):
        self.assertEqual(report._count("2, 4, 7-10"), 6)
        self.assertEqual(report._count("1:3, 2:5-6"), 3)


class Cli(te.Base):
    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = tagmender.main(list(argv))
        return code, out.getvalue(), err.getvalue()

    def fake_job(self, albums, rows, job="20261101-130000-beef", status="done"):
        plan = make_plan(albums, job)
        executor.job_dir(job).mkdir(parents=True)
        pl.save(plan, executor.job_dir(job) / "plan.json")
        executor._record_job(job, profile="Library", started=time.time(), status=status)
        for r in rows:
            executor._record_album(job, *r)
        return job

    def test_plan_report_exports_and_exit_code(self):
        path = self.tmp / "plan.json"
        pl.save(make_plan([album("/music/Band/Live", tier="partial",
                                 identification=ident("partial", cands=[cand()]))]), path)
        h, c = self.tmp / "r.html", self.tmp / "r.csv"
        code, out, err = self.run_cli("report", str(path), "--html", str(h), "--csv", str(c))
        self.assertEqual(code, tagmender.EXIT_PARTIAL)             # (an album needs your decision)
        self.assertIn("Needs your decision (1)", out)
        self.assertIn("link:   file:///music/Band/Live", out)
        self.assertIn("Ready to apply (1)", out)
        self.assertIn("Report saved to", err)
        page = h.read_text(encoding="utf-8")
        self.assertIn("href='file:///music/Band/Live'", page)
        self.assertIn("href='https://www.discogs.com/release/4242'", page)
        rows = list(csv.DictReader(io.StringIO(c.read_text(encoding="utf-8"))))
        self.assertEqual(rows[0]["section"], "Needs your decision")
        self.assertEqual(rows[0]["apply"], "tagmender plan /music/Band/Live --release discogs:4242 --profile Library")
        code, out, _e = self.run_cli("report", str(path), "--json")
        self.assertEqual(json.loads(out)["counts"], {"decide": 1, "failed": 0, "done": 1})

    def test_a_folder_name_that_is_not_utf8_is_saved_and_exported(self):
        path = self.tmp / "odd.json"
        odd = _os.fsdecode(b"/x/Art/Alb\xe9um")
        pl.save(make_plan([album(odd, tier="partial", identification=ident("partial", cands=[cand()]))]), path)
        h, c = self.tmp / "o.html", self.tmp / "o.csv"
        code, _out, err = self.run_cli("report", str(path), "--html", str(h), "--csv", str(c))
        self.assertEqual(code, tagmender.EXIT_PARTIAL, err)
        self.assertIn("Alb\\xe9um", h.read_text(encoding="utf-8"))
        self.assertIn("file:///x/Art/Alb%E9um", h.read_text(encoding="utf-8"))
        self.assertIn("Alb\\xe9um", c.read_text(encoding="utf-8"))
        self.assertFalse([x for x in self.tmp.iterdir() if x.name.endswith(".part")])
        with mock.patch.object(Path, "write_bytes", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                report.save(report.for_plan(path), self.tmp / "new.html", "html")
        self.assertFalse((self.tmp / "new.html").exists())

    def test_job_report_newest_by_default(self):
        self.fake_job([album("/music/D")], [("/music/D", "done", "Done", "")])
        code, out, _e = self.run_cli("report")
        self.assertEqual(code, tagmender.EXIT_OK)
        self.assertIn("Report of job 20261101-130000-beef", out)
        self.assertIn("Done (1)", out)

    def test_a_plan_file_holding_something_else_is_a_message(self):
        bad = self.tmp / "x.json"
        bad.write_text("[1, 2]")
        code, _o, err = self.run_cli("report", str(bad))
        self.assertEqual(code, tagmender.EXIT_ERR)
        self.assertIn("No report: x.json is not a plan", err)

    def test_run_ends_with_the_reports_counts(self):
        d = self.album("Band Z", "Album Z")
        p = self.engine(te.FakeHttp(dg=[te.dg_release(905, "Band Z", "Album Z")])).plan(d)
        args = mock.Mock(yes=True, json=False)
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            tagmender._execute(p, args)
        self.assertRegex(out.getvalue(), r"Report: 0 needs your decision, 0 could not finish, 1 done\. "
                                         r"See it with: tagmender report \d{8}-\d{6}-[0-9a-f]+")

    def test_no_job(self):
        code, _o, err = self.run_cli("report")
        self.assertEqual(code, tagmender.EXIT_ERR)
        self.assertIn("No report", err)
        code, _o, err = self.run_cli("report", "no-such-job")
        self.assertEqual(code, tagmender.EXIT_ERR)

    def test_real_plan_and_apply(self):
        """A generated album planned, applied and reported: done, its folder linked; the plan
        carries the matcher's suggestions."""
        d = self.album("Band Z", "Album Z")
        p = self.engine(te.FakeHttp(dg=[te.dg_release(905, "Band Z", "Album Z")])).plan(d)
        cands = p["albums"][0]["identification"]["candidates"]
        self.assertEqual(cands[0]["ref"], "discogs:905")
        res = executor.Executor(p).run()
        rep = report.for_job(res["job"])
        self.assertEqual(rep["counts"], {"decide": 0, "failed": 0, "done": 1})
        self.assertEqual(rep["rows"][0]["uri"], Path(d).as_uri())


@unittest.skipIf(QApplication is None, "PySide6 not installed")
class Sheet(te.Base):
    @classmethod
    def setUpClass(cls):
        import gui
        cls.gui = gui
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        super().setUp()
        self.win = self.gui.Main()
        self.win.resize(1180, 760)
        self.win.show()

    def tearDown(self):
        end = time.time() + 30
        while time.time() < end and any(self.gui._alive(th) for pg in self.win.pages.values()
                                        for th, _w in getattr(pg, "_threads", [])):
            self.app.processEvents()
            time.sleep(0.02)
        self.win.close()
        super().tearDown()

    def wait(self, cond, timeout=30):
        end = time.time() + timeout
        while time.time() < end:
            self.app.processEvents()
            if cond():
                return True
            time.sleep(0.02)
        return False

    def sheet(self, rep):
        from gui import report as greport
        return greport.open_report(self.win, rep)

    def test_rows_links_and_exports(self):
        from gui import report as greport
        from gui import sheets
        a = album("/music/Band/Live", tier="partial", identification=ident("partial", cands=[cand(), cand(rid="99")]))
        dlg = self.sheet(report.build(make_plan([a])))
        tops = [dlg.tree.topLevelItem(i).text(0) for i in range(dlg.tree.topLevelItemCount())]
        self.assertEqual(tops, ["Needs your decision (1)", "Could not finish (0)", "Ready to apply (1)"])
        row = dlg.tree.topLevelItem(0).child(0)
        self.assertEqual(row.text(2)[:20], "the album is known b")
        self.assertEqual(len(dlg.apply_buttons), 2)
        with mock.patch.object(greport.QDesktopServices, "openUrl", return_value=True) as opened:
            dlg.links[0].linkActivated.emit("file:///music/Band/Live")
        self.assertEqual(opened.call_args[0][0].toString(), "file:///music/Band/Live")
        out = self.tmp / "x.html"
        with mock.patch.object(sheets, "save_file", return_value=str(out)):
            self.assertEqual(dlg.export("html"), str(out))
        self.assertIn("file:///music/Band/Live", out.read_text(encoding="utf-8"))
        # (a save that fails on its text, not the disk, is a message too, never an error in the app)
        with mock.patch.object(sheets, "save_file", return_value=str(out)), \
                mock.patch.object(report, "save", side_effect=UnicodeEncodeError("utf-8", "x", 0, 1, "bad")), \
                mock.patch.object(sheets, "warning") as warned:
            self.assertEqual(dlg.export("csv"), "")
        self.assertTrue(warned.called)

    def test_tag_text_is_shown_as_it_is_and_framing_is_translated(self):
        from gui import i18n
        from gui import report as greport
        a = album("/music/<b>B</b>", tier="partial", identification=ident("partial", cands=[cand()]),
                  release={"album": ["<img src='x'>"], "albumartist": ["<b>B</b>"]})
        dlg = self.sheet(report.build(make_plan([a])))
        self.assertEqual(dlg.detail.textFormat(), greport.Qt.PlainText)
        row = dlg.tree.topLevelItem(0).child(0)
        dlg.tree.setCurrentItem(row)
        self.assertIn("<b>B</b> - <img src='x'>", dlg.detail.text())
        self.assertIn("&lt;b&gt;B&lt;/b&gt;", row.toolTip(0))
        self.assertTrue(row.toolTip(0).startswith("<qt>"))
        self.assertEqual(report.UNDONE_NOTE, greport.NOTE_WORDS[0])
        self.assertEqual(report.RUNNING_NOTE, greport.NOTE_WORDS[1])
        self.assertEqual(report.ATTENTION_TITLE, greport.ATTENTION_WORDS)

        class Fake:
            def translate(self, _ctx, text, _dis=None):
                return {"Report of job {job} ({profile})": "Bericht {job} ({profile})", "Partial": "Teilweise",
                        report.UNDONE_NOTE: "Rückgängig gemacht."}.get(text, "")
        with mock.patch.object(i18n, "_own", Fake()):
            self.assertEqual(greport.heading_words({"applied": True, "job": "j1", "profile": "p"}), "Bericht j1 (p)")
            self.assertEqual(greport.status_words("Partial"), "Teilweise")
            self.assertEqual(greport.note_words(report.UNDONE_NOTE), "Rückgängig gemacht.")
        self.assertEqual(greport.heading_words({"applied": False, "job": "j1", "profile": "p"}),
                         report.heading({"applied": False, "job": "j1", "profile": "p"}))

    def test_apply_opens_the_report_of_what_was_done(self):
        from gui import report as greport
        d = self.album("Band Y", "Album Y")
        ov = self.win.pages["Overview"]
        ov.plan = self.engine(te.FakeHttp(dg=[te.dg_release(906, "Band Y", "Album Y")])).plan(d)
        res = executor.Executor(ov.plan).run()
        opened = []
        with mock.patch.object(greport, "open_report", lambda win, rep: opened.append(rep)):
            ov.applied(res)
        self.assertEqual(len(opened), 1)
        self.assertEqual((opened[0]["job"], opened[0]["counts"]["done"]), (res["job"], 1))

    def test_apply_on_a_split_version_previews_that_version_alone(self):
        rep = {"rows": [], "counts": {"decide": 0, "failed": 0, "done": 0}, "applied": False}
        dlg = self.sheet(rep)
        ov = self.win.pages["Overview"]
        got = []
        with mock.patch.object(ov, "preview_release", lambda *a, **k: got.append((a, k)) or True):
            row = {"split": True, "files": ["/m/A/01 (1).flac"], "apply_folder": "/m/A", "source_dir": "/m/A"}
            self.assertTrue(dlg.apply(row, {"ref": MB}))
        self.assertEqual(got, [(("/m/A", MB, ["/m/A/01 (1).flac"]), {"only": True})])
        with mock.patch.object(ov, "set_path", return_value=True) as sp:
            self.assertTrue(ov.preview_release("/m/A", MB, ["/m/A/01 (1).flac"], only=True))
        self.assertEqual(sp.call_args[0][1], {_os.path.realpath("/m/A"): [{"ref": MB, "files": ["/m/A/01 (1).flac"],
                                                                          "only": True}]})

    def test_apply_previews_and_writes_nothing_until_confirmed(self):
        d = self.album("Band Z", "Album Z")
        before = {f.name: f.read_bytes() for f in d.iterdir()}
        a = album(str(d), status="unresolved", tier="unresolved",
                  identification=ident("unresolved", "unresolved", reason="no_candidate: not found",
                                       cands=[cand(rid="905")]))
        dlg = self.sheet(report.build(make_plan([a])))
        ov = self.win.pages["Overview"]
        http = te.FakeHttp(dg=[te.dg_release(905, "Band Z", "Album Z")])
        import match_albums as ma
        from gui import sheets
        with mock.patch.object(ma, "Http", lambda db, cfg, token: http):
            dlg.apply_buttons[0].click()
            self.assertTrue(self.wait(lambda: ov.plan is not None and not ov.running))
        self.assertEqual(ov.forced, {_os.path.realpath(str(d)): "discogs:905"})
        self.assertEqual(ov.plan["albums"][0]["identification"]["release_id"], "905")
        self.assertIs(self.win.stack.currentWidget(), ov)
        self.assertEqual({f.name: f.read_bytes() for f in d.iterdir()}, before)     # (only previewed)
        with mock.patch.object(sheets, "confirm", return_value=False) as asked:
            ov.apply()                                              # (Apply asks; "no" writes nothing)
        asked.assert_called_once()
        self.assertEqual({f.name: f.read_bytes() for f in d.iterdir()}, before)

    def test_reachable_from_jobs_and_attention(self):
        from gui import report as greport
        job = "20261101-140000-cafe"
        executor.job_dir(job).mkdir(parents=True)
        pl.save(make_plan([album("/music/D")], job), executor.job_dir(job) / "plan.json")
        executor._record_job(job, profile="Library", started=time.time(), status="done")
        executor._record_album(job, "/music/D", "done", "Done", "")
        jobs = self.win.pages["Jobs"]
        self.win.go("Jobs")
        jobs.reload()
        jobs.table.selectRow(0)
        self.assertTrue(self.win.act("show_report").isEnabled())
        opened = []
        with mock.patch.object(greport, "open_report", lambda win, rep: opened.append(rep)):
            jobs.report_btn.click()
            self.assertTrue(self.wait(lambda: opened))
            self.assertEqual(opened[0]["job"], job)
            att = self.win.pages["Needs attention"]
            self.win.go("Needs attention")
            att.show_rows([{"folder": "/music/U", "codes": ["no_candidate"], "reason": "", "files": 3,
                            "mark": "Unresolved"}])
            self.assertTrue(att.report_btn.isEnabled())
            att.report_btn.click()
            self.assertTrue(self.wait(lambda: len(opened) == 2))
        self.assertEqual(opened[1]["rows"][0]["folder"], "/music/U")


if __name__ == "__main__":
    unittest.main()
