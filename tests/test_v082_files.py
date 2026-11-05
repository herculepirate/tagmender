# Tests for 0.8.2's file-safety fixes: a track or companion another program saves while Apply
# writes it (the album is put back, that edit kept), an album undone by hand under another
# profile's record, a waiting plan of several albums remembered by its worst, one version of
# a split folder applied alone, a crash in a case-only rename of a rewritten cue sheet, Process
# Folder Again on a "~/" source, and a read-only track. Generated audio.
import os as _os, tempfile as _tempfile  # noqa: E402
_os.environ["XDG_CONFIG_HOME"] = _tempfile.mkdtemp(prefix="tm-config-")
_os.environ["XDG_STATE_HOME"] = _tempfile.mkdtemp(prefix="tm-state-")

import os
import sqlite3
import sys
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_executor import Base, audio  # noqa: E402
from test_fileops_crash import die, in_child  # noqa: E402
from test_split_versions import Folders  # noqa: E402
from test_watch_folders import DropBase  # noqa: E402

import exclusions as ex  # noqa: E402
import executor  # noqa: E402
import payload  # noqa: E402
import plan as pl  # noqa: E402
import profiles as pf  # noqa: E402
import tagio  # noqa: E402
import watcher  # noqa: E402


class ChangedWhileWritten(Base):
    """Another program saves the original while TagMender writes its copy: the album fails
    and is put back; the other program's edit is never overwritten or deleted."""

    def run_with_edit(self, prof):
        from mutagen.flac import FLAC
        p = pl.build([self.proposal()], prof)
        real, hit = tagio.write, []

        def write(path, *a, **k):
            if not hit:
                hit.append(1)
                f = FLAC(str(self.f1))
                f["lyrics"] = ["typed by the user just now"]
                f.save()
            return real(path, *a, **k)
        with mock.patch.object(tagio, "write", write):
            res = executor.Executor(p).run()
        self.assertTrue(hit)
        return p, res

    def test_in_place(self):
        _p, res = self.run_with_edit(self.profile())
        self.assertEqual(res["albums"][0]["status"], "failed")
        self.assertIn("changed while it was being written", res["albums"][0]["detail"])
        self.assertEqual(tagio.read(self.f1).get("lyrics"), ["typed by the user just now"])
        self.assertEqual(tagio.read(self.f2)["album"], ["Some Album"])          # (rolled back)
        self.assertFalse(list(self.album.glob(".tm-*")))

    def test_move(self):
        p, res = self.run_with_edit(self.profile(operation="move", destination=str(self.dest)))
        self.assertEqual(res["albums"][0]["status"], "failed")
        self.assertTrue(self.f1.exists())
        self.assertEqual(tagio.read(self.f1).get("lyrics"), ["typed by the user just now"])
        self.assertFalse(Path(p["albums"][0]["files"][0]["dst"]).exists())
        self.assertTrue(self.f2.exists())

    def test_companion_rewritten_and_moved(self):
        p = pl.build([self.proposal()], self.profile(operation="move", destination=str(self.dest)))
        cue = self.album / "rip.cue"
        real = executor._write_rewrite

        def rewrite(rw, src, stage):
            real(rw, src, stage)
            if Path(src) == cue:                       # saved again while its new text is written
                cue.write_text(cue.read_text() + "REM edited by hand\n")
        with mock.patch.object(executor, "_write_rewrite", rewrite):
            res = executor.Executor(p).run()
        self.assertEqual(res["albums"][0]["status"], "failed")
        self.assertTrue(cue.read_text().endswith("REM edited by hand\n"))
        self.assertTrue(self.f1.exists() and self.f2.exists())

    def test_unchanged_file_still_written(self):
        p = pl.build([self.proposal()], self.profile(operation="move", destination=str(self.dest)))
        res = executor.Executor(p).run()
        self.assertEqual(res["counts"], {"done": 1})


class UndoneUnderAnotherProfile(Base):
    def test_undone_album_recorded_by_another_profile_stays_undone(self):
        inbox = self.tmp / "inbox"
        folder = inbox / "Band - Album"
        files = [audio(folder / f"0{n} Song.mp3", 400 + n * 40, title=f"Song {n}", album="Album", track=f"{n}/2")
                 for n in (1, 2)]
        old = time.time() - 3600
        for p in [inbox, folder, *files]:
            os.utime(p, (old, old))
        db = sqlite3.connect(":memory:")
        db.execute("CREATE TABLE processed (folder TEXT PRIMARY KEY, profile TEXT, signature TEXT,"
                   " status TEXT, attempts INTEGER, next_try REAL, ts REAL, detail TEXT)")
        key = watcher.album_key(folder, files)
        db.execute("INSERT INTO processed VALUES (?,?,?,?,?,?,?,?)",
                   (key, "Other", watcher.shape(folder, files), "undone", 0, 0, time.time(), ""))
        prof = pf.from_dict({"name": "Inbox", "operation": "move", "destination": str(self.tmp / "library"),
                             "sources": [{"path": str(inbox), "watch": True, "recursive": True}],
                             "unattended": True, "watch": {"settle_minutes": 1}})
        go = watcher.Watcher([prof], once=True).due(folder, prof, db, key, files=files)
        self.assertEqual(go, (False, "undone"))


class ApplyPendingWorst(Base):
    def test_plan_of_two_albums_remembered_by_the_worst(self):
        prof = self.profile(name="Inbox")
        pf.save(prof)
        a2 = self.lib / "Some Artist" / "2002 - Disc Two"
        g1 = audio(a2 / "01 c.flac", 660, album="X", title="c")
        g2 = audio(a2 / "02 d.flac", 770, album="X", title="d")
        p = pl.build([self.proposal(), self.proposal(source_dir=str(a2), files=[str(g1), str(g2)],
                                                     release_id="778")], prof)
        self.assertEqual([a["status"] for a in p["albums"]], ["ready", "ready"])
        p["watch"] = {"key": "KEY", "folder": str(self.lib), "files": [], "deep": True}
        watcher.plans_dir().mkdir(parents=True, exist_ok=True)
        path = watcher.plans_dir() / f"{p['job']}.json"
        pl.save(p, path)
        tagio.write(self.f1, {"comment": ["edited after the plan"]})       # album 0: changed since
        now = time.time()
        res = watcher.apply_pending(path)
        self.assertEqual([r["status"] for r in res["albums"]], ["conflict", "done"])
        db = sqlite3.connect(ex.STATE_DIR / "state.db")
        status, nxt = db.execute("SELECT status, next_try FROM processed WHERE folder='KEY'").fetchone()
        self.assertEqual(status, "conflict")
        self.assertIn(status, watcher.RETRIED)
        self.assertGreaterEqual(nxt, now + watcher.PROVIDER_RETRY[0])       # due again after the delay


class SplitSubset(Folders):
    def test_one_version_alone_is_not_applied(self):
        top = self.mixed_folder()
        p = self.planned(top)
        self.assertEqual([a["status"] for a in p["albums"]], ["ready", "ready"])
        mover = next(a for a in p["albums"] if any(c["op"] == "move" for c in a["companions"]))
        before = self.tree(top.parent)
        res = executor.Executor(dict(p, albums=[mover])).run()
        self.assertEqual(res["albums"][0]["status"], "conflict")
        self.assertIn("other version", res["albums"][0]["detail"])
        self.assertEqual(self.tree(top.parent), before)
        p2 = dict(p, job=p["job"] + "-b")
        res = executor.Executor(p2).run()
        self.assertEqual([r["status"] for r in res["albums"]], ["done", "done"])
        for a in p["albums"]:
            folder = Path(a["split"]["folder"])
            self.assertTrue((folder / "cover.jpg").exists(), folder)


class CaseOnlyCueCrash(Base):
    def test_crash_before_the_case_rename_puts_the_cue_text_back(self):
        p = pl.build([self.proposal()], self.profile(operation="move", destination=str(self.dest)))
        cue = next(c for c in p["albums"][0]["companions"] if c["src"].endswith(".cue"))
        self.assertTrue(cue.get("rewrite"))
        # on a case-insensitive share the sheet is renamed "rip.cue" -> "Rip.cue" as it is rewritten
        cue["op"], cue["dst"] = "move", str(Path(cue["src"]).with_name("Rip.cue"))
        before = (self.album / "rip.cue").read_text()
        real_case_only, real_rename = executor._case_only, executor._rename_case

        def case_only(x, y):
            return (Path(x).name.lower() == Path(y).name.lower() and Path(x).name != Path(y).name) or \
                real_case_only(x, y)

        def rename(s, d):
            if str(s).endswith(".cue"):
                die()
            return real_rename(s, d)

        def child():
            with mock.patch.object(executor, "_case_only", case_only), \
                    mock.patch.object(executor, "_rename_case", rename):
                executor.Executor(p).run()
        self.assertEqual(in_child(child), 9)
        executor.recover(p["job"])
        self.assertEqual((self.album / "rip.cue").read_text(), before)
        self.assertTrue(self.f1.exists() and self.f2.exists())


class ForgetTilde(DropBase):
    def test_process_again_on_a_home_relative_source(self):
        with mock.patch.dict(os.environ, {"HOME": str(self.tmp)}):
            prof = self.profile(operation="in_place",
                                sources=[{"path": "~/inbox", "watch": True, "recursive": True}])
            self.drop("Band X", "Album X")
            watcher.Watcher([pf.load("Inbox")], once=True).tick()
            db = sqlite3.connect(ex.STATE_DIR / "state.db")
            self.assertTrue(db.execute("SELECT folder FROM processed").fetchall())
            self.assertEqual(watcher.forget(prof["sources"][0]["path"]), 1)
            self.assertFalse(db.execute("SELECT folder FROM processed").fetchall())


@unittest.skipIf(os.geteuid() == 0, "root writes read-only files anyway")
class ReadOnlyTrack(Base):
    def test_read_only_track_is_written_and_stays_read_only(self):
        os.chmod(self.f1, 0o444)
        d1 = payload.digest(self.f1)
        p = pl.build([self.proposal()], self.profile())
        self.assertEqual(p["albums"][0]["status"], "ready")
        res = executor.Executor(p).run()
        self.assertEqual(res["counts"], {"done": 1}, res["albums"])
        self.assertEqual(tagio.read(self.f1)["album"], ["Real Album"])
        self.assertEqual(payload.digest(self.f1), d1)
        self.assertEqual(os.stat(self.f1).st_mode & 0o777, 0o444)
        self.assertFalse(list(self.album.glob(".tm-*")))
        executor.undo(p["job"])
        self.assertEqual(tagio.read(self.f1)["album"], ["Some Album"])
        self.assertEqual(payload.digest(self.f1), d1)
        self.assertEqual(os.stat(self.f1).st_mode & 0o777, 0o444)


if __name__ == "__main__":
    unittest.main()
