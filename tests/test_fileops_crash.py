# Crashes (a forked child killed with os._exit, as by SIGKILL or power loss), rollbacks,
# recover and undo, hard links, companions and names: cases from the second independent
# review of the file operations, each of which failed before its fix.
import os as _os, tempfile as _tempfile  # noqa: E402
_os.environ["XDG_CONFIG_HOME"] = _tempfile.mkdtemp(prefix="tm-config-")
_os.environ["XDG_STATE_HOME"] = _tempfile.mkdtemp(prefix="tm-state-")

import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "tests"))
import executor  # noqa: E402,F401
import plan as pl  # noqa: E402,F401
from test_executor import Base, audio, target  # noqa: E402,F401


def die():
    """Stands for SIGKILL / power loss: no except or finally block runs."""
    os._exit(9)


def in_child(fn):
    """Run fn in a forked child (so that die() kills only the child). Returns its exit code."""
    pid = os.fork()
    if pid == 0:
        try:
            fn()
        except BaseException as exc:          # noqa: BLE001
            sys.stderr.write(f"child raised {exc!r}\n")
            os._exit(3)
        os._exit(0)
    _, status = os.waitpid(pid, 0)
    return os.waitstatus_to_exitcode(status)


def parts(root):
    return sorted(str(p) for p in Path(root).rglob("*") if ".tm-" in p.name)




import errno
import shutil
import unittest
from pathlib import Path
from unittest import mock

import filenames as fnm
import payload
import tagio


class Crash(BaseException):
    pass


def no_change(test, prof, props=None):
    t1, t2 = target(1, "First"), target(2, "Second")
    for f, t in ((test.f1, t1), (test.f2, t2)):
        tagio.write(f, dict(t, tagmender=["Resolved"]))
    p = pl.build(props or [test.proposal(targets=[t1, t2])], prof)
    assert all(fe["op"] == "move" and not fe["changes"] for fe in p["albums"][0]["files"]), pl.preview(p)
    return p


# ---------------------------------------------------------------------------
# 1. recover ignores companion steps whose last state is "begin"
# ---------------------------------------------------------------------------
class B01_CompanionBeginStageLeftBehind(Base):
    def test_kill_while_staging_a_moved_companion(self):
        p = pl.build([self.proposal()], self.profile(operation="move", destination=str(self.dest)))

        def child():
            with mock.patch.object(executor.payload, "file_sha256", lambda *a: die()):
                executor.Executor(p).run()
        self.assertEqual(in_child(child), 9)
        executor.recover(p["job"])
        self.assertEqual(parts(self.lib) + parts(self.dest), [])

    def test_kill_while_staging_a_cue_rewritten_in_place(self):
        final = self.lib / "Real Artist" / "2001 - Real Album"
        a = audio(final / "01 a.flac", 500, album="Real Album")
        b = audio(final / "02 b.flac", 600, album="Real Album")
        (final / "rip.cue").write_text('FILE "01 a.flac" WAVE\nFILE "02 b.flac" WAVE\n')
        p = pl.build([self.proposal(files=[str(a), str(b)], source_dir=str(final))],
                     self.profile(operation="move", destination=str(self.lib)))
        assert [c["op"] for c in p["albums"][0]["companions"]] == ["rewrite"], p["albums"][0]["companions"]

        real = executor.Executor._stage_companion

        def staged_then_die(*a, **k):
            real(*a, **k)
            die()                                       # stage written, state still "begin"

        def child():
            with mock.patch.object(executor.Executor, "_stage_companion", staged_then_die):
                executor.Executor(p).run()
        self.assertEqual(in_child(child), 9)
        executor.recover(p["job"])
        self.assertEqual(parts(self.lib), [])


# ---------------------------------------------------------------------------
# 2. recover treats a file that appeared at a destination (the reason the album
#    failed and was rolled back) as the job's own commit, and deletes the source
# ---------------------------------------------------------------------------
class B02_RecoverDeletesSourceOfRolledBackAlbum(Base):
    def two_albums(self):
        other = self.lib / "Other" / "Album2"
        g = audio(other / "01.flac", 700, album="X", title="x")
        prof = self.profile(operation="move", destination=str(self.dest))
        return pl.build([self.proposal(),
                         self.proposal(source_dir=str(other), files=[str(g)],
                                       targets=[target(1, "Gee", album="Second Album")])], prof)

    def run_until_album_2_then_die(self, p):
        def child():
            executor.Executor(p, progress=lambda i, n, s: die() if i == 1 else None).run()
        self.assertEqual(in_child(child), 9)

    def test_companion_destination_taken_meanwhile(self):
        p = self.two_albums()
        log_dst = Path(next(c["dst"] for c in p["albums"][0]["companions"] if c["src"].endswith("rip.log")))
        log_dst.parent.mkdir(parents=True)
        log_dst.write_text("someone else's log")                 # appears after planning
        self.run_until_album_2_then_die(p)
        self.assertEqual((self.album / "rip.log").read_text(), "EAC log")   # album 1 was rolled back
        executor.recover(p["job"])
        self.assertTrue((self.album / "rip.log").exists(), "recover deleted the rolled-back album's rip.log")
        self.assertEqual(log_dst.read_text(), "someone else's log")

    def test_track_destination_taken_meanwhile_by_a_duplicate(self):
        p = self.two_albums()
        dst2 = Path(p["albums"][0]["files"][1]["dst"])
        dst2.parent.mkdir(parents=True)
        shutil.copy(self.f2, dst2)
        tagio.write(dst2, {"comment": ["someone's own copy"]})   # same audio, other tags
        self.run_until_album_2_then_die(p)
        self.assertTrue(self.f2.exists())                         # album 1 was rolled back
        executor.recover(p["job"])
        self.assertTrue(self.f2.exists(), "recover deleted the rolled-back album's track")


# ---------------------------------------------------------------------------
# 3. a commit completed by recover has no size/mtime/tags_sig: undo then deletes or
#    overwrites what the user changed after the job
# ---------------------------------------------------------------------------
class B03_RecoveredCommitLosesChangeChecks(Base):
    def crash_at_commit(self, p):
        real = executor.Journal.write

        def write(j, **ev):
            if ev.get("state") == "committed":
                raise Crash()
            return real(j, **ev)
        with mock.patch.object(executor.Journal, "write", write), self.assertRaises(Crash):
            executor.Executor(p).run()
        executor.recover(p["job"])

    def test_copy_edited_after_recover_is_deleted_by_undo(self):
        p = pl.build([self.proposal()], self.profile(operation="copy", destination=str(self.dest)))
        self.crash_at_commit(p)
        copy = next(self.dest.rglob("01.F.flac"))
        tagio.write(copy, {"comment": ["mine now"]})
        out = executor.undo(p["job"])
        self.assertTrue(copy.exists(), out)

    def test_tags_edited_after_recover_are_overwritten_without_force(self):
        p = pl.build([self.proposal()], self.profile())
        self.crash_at_commit(p)
        tagio.write(self.f1, {"comment": ["my own note"]})
        out = executor.undo(p["job"])
        self.assertEqual(tagio.read(self.f1).get("comment"), ["my own note"], out)


# ---------------------------------------------------------------------------
# 4. _same_file(src, dst) is taken to mean "only the letter case differs": a hard link
# ---------------------------------------------------------------------------
class B04_HardLinkTakenForCaseOnlyRename(Base):
    def test_destination_already_hard_linked_to_the_source(self):
        prof = self.profile(operation="move", destination=str(self.dest))
        p0 = no_change(self, prof)
        dst1 = Path(p0["albums"][0]["files"][0]["dst"])
        dst1.parent.mkdir(parents=True)
        os.link(self.f1, dst1)                       # e.g. a torrent client's hard link (Unraid/*arr)
        p = no_change(self, prof)
        self.assertEqual(p["albums"][0]["files"][0]["dst"], str(dst1))
        executor.Executor(p).run()
        self.assertEqual(parts(self.lib) + parts(self.dest), [], "hidden .tm-case link left behind")
        executor.undo(p["job"])
        self.assertTrue(self.f1.exists())
        self.assertTrue(dst1.exists(), "undo removed the user's pre-existing link")

    def test_kill_during_rollback_then_recover_then_undo(self):
        prof = self.profile(operation="move", destination=str(self.dest))
        p = no_change(self, prof)
        dst1, dst2 = (Path(fe["dst"]) for fe in p["albums"][0]["files"])
        dst2.parent.mkdir(parents=True)
        dst2.write_bytes(b"squatter")                    # file 2 fails: its name is taken
        real = os.unlink

        def unlink(path, *a, **k):
            if Path(path) == dst1:                        # rollback: link back done, unlink not
                die()
            return real(path, *a, **k)

        def child():
            with mock.patch.object(executor.os, "unlink", unlink):
                executor.Executor(p).run()
        self.assertEqual(in_child(child), 9)
        executor.recover(p["job"])
        out = executor.undo(p["job"])
        self.assertTrue(self.f1.exists(), out)
        self.assertEqual(parts(self.lib) + parts(self.dest), [], out)
        self.assertEqual(sorted(x.name for x in dst1.parent.iterdir()), [dst2.name], out)


# ---------------------------------------------------------------------------
# 5. undo of companions never checks whether they changed after the job
# ---------------------------------------------------------------------------
class B05_CompanionUndoDiscardsLaterEdits(Base):
    def test_moved_cue_edited_after_the_job(self):
        p = pl.build([self.proposal()], self.profile(operation="move", destination=str(self.dest)))
        executor.Executor(p).run()
        cue = next(self.dest.rglob("rip.cue"))
        cue.write_text(cue.read_text() + "REM my correction\n")
        out = executor.undo(p["job"])
        found = [x for x in self.tmp.rglob("*.cue") if "REM my correction" in x.read_text()]
        self.assertTrue(found, f"the edited cue sheet is gone: {out}")

    def test_copied_log_edited_after_the_job(self):
        p = pl.build([self.proposal()], self.profile(operation="copy", destination=str(self.dest)))
        executor.Executor(p).run()
        log = next(self.dest.rglob("rip.log"))
        log.write_text("my notes")
        out = executor.undo(p["job"])
        self.assertTrue(log.exists(), out)


# ---------------------------------------------------------------------------
# 6. create_begin: recover adopts whatever file is at the path now
# ---------------------------------------------------------------------------
class B06_RecoverAdoptsUsersCover(Base):
    def test_cover_put_there_after_a_crash_is_deleted_by_undo(self):
        from PIL import Image
        front = self.tmp / "front.jpg"
        Image.new("RGB", (600, 600), (1, 2, 3)).save(front)
        p = pl.build([self.proposal(artwork={"front": str(front), "source": "t"})], self.profile())
        cover = Path(p["albums"][0]["artwork"]["file"])
        real = executor._copy_fsync

        def copy(a, b):
            if Path(a) == front:
                die()
            return real(a, b)

        def child():
            with mock.patch.object(executor, "_copy_fsync", copy):
                executor.Executor(p).run()
        self.assertEqual(in_child(child), 9)
        self.assertFalse(cover.exists())
        cover.write_bytes(b"my own scan")                # before `tagmender recover` is run
        executor.recover(p["job"])
        executor.undo(p["job"])
        self.assertTrue(cover.exists(), "undo deleted the user's cover.jpg")


# ---------------------------------------------------------------------------
# 7. staging files left behind when a commit or a staging write fails
# ---------------------------------------------------------------------------
class B07_StageLeftAfterFailure(Base):
    def test_in_place_replace_refused(self):
        p = pl.build([self.proposal()], self.profile())
        real = os.replace

        def replace(a, b, *x, **k):
            if Path(b) == self.f2 and "undo" not in Path(a).name:
                raise PermissionError(errno.EACCES, "Permission denied (file open on the share)", str(b))
            return real(a, b, *x, **k)
        with mock.patch.object(executor.os, "replace", replace):
            res = executor.Executor(p).run()
        self.assertEqual(res["counts"], {"failed": 1})
        self.assertEqual(executor.recover(p["job"]), [f"job {p['job']} finished normally; nothing to recover"])
        self.assertEqual(parts(self.lib), [], "a full-size hidden staging copy stays in the album folder")

    def test_disk_full_while_staging_the_cover_file(self):
        from PIL import Image
        front = self.tmp / "front.jpg"
        Image.new("RGB", (600, 600), (1, 2, 3)).save(front)
        p = pl.build([self.proposal(artwork={"front": str(front), "source": "t"})], self.profile())
        real = shutil.copyfileobj

        def full(a, b, n=0):
            if Path(a.name) == front:
                b.write(a.read(100))
                raise OSError(errno.ENOSPC, "No space left on device")
            return real(a, b, n)
        with mock.patch.object(shutil, "copyfileobj", full):
            res = executor.Executor(p).run()
        self.assertEqual(res["counts"], {"failed": 1})
        self.assertEqual(parts(self.lib), [])


# ---------------------------------------------------------------------------
# 8. undo interrupted while writing a companion back: the next undo takes the
#    half-written file for the put-back one
# ---------------------------------------------------------------------------
class B08_InterruptedUndoOfCompanion(Base):
    def test_kill_while_undo_copies_the_saved_cue_back(self):
        cue_before = (self.album / "rip.cue").read_text()
        p = pl.build([self.proposal()], self.profile(operation="move", destination=str(self.dest)))
        executor.Executor(p).run()
        real = shutil.copyfileobj

        def half(a, b, n=0):
            if "companions" in str(a.name):
                b.write(a.read(10))
                b.flush()
                die()
            return real(a, b, n)

        def child():
            with mock.patch.object(shutil, "copyfileobj", half):
                executor.undo(p["job"])
        self.assertEqual(in_child(child), 9)
        out = executor.undo(p["job"])
        self.assertEqual((self.album / "rip.cue").read_text(), cue_before, out)
        self.assertEqual([x for x in self.dest.rglob("*") if x.is_file()], [], out)


# ---------------------------------------------------------------------------
# 9. plan: look-alike folders within one plan
# ---------------------------------------------------------------------------
class B09_LookAlikeFoldersInOnePlan(Base):
    def test_two_albums_whose_artist_differs_only_in_case(self):
        other = self.lib / "Foo bar" / "Second"
        g = audio(other / "01.flac", 700, album="Second", title="x")
        prof = self.profile(operation="copy", destination=str(self.dest))      # compat: windows (default)
        props = [self.proposal(targets=[target(1, "First", artist="Foo Bar"), target(2, "Second", artist="Foo Bar")]),
                 self.proposal(source_dir=str(other), files=[str(g)],
                               targets=[target(1, "Gee", album="Second Album", artist="Foo bar")])]
        p = pl.build(props, prof)
        executor.Executor(p).run()
        self.assertEqual(len(list(self.dest.iterdir())), 1, sorted(x.name for x in self.dest.iterdir()))


# ---------------------------------------------------------------------------
# 10. plan: an unresolved album already in unresolved.move_to is nested again
# ---------------------------------------------------------------------------
class B10_UnresolvedNestedAgain(Base):
    def test_album_already_in_move_to(self):
        hold = self.lib / "Unresolved"
        alb = hold / "Band" / "Album"
        f = audio(alb / "01.flac", 300)
        prof = self.profile(unresolved={"action": "move", "move_to": str(hold)})
        prop = self.proposal(source_dir=str(alb), files=[str(f)], targets=[], status="unresolved",
                             confidence=0, reason="no candidate")
        fe = pl.build([prop], prof)["albums"][0]["files"][0]
        self.assertEqual(fe["dst"], str(f), "moved one level deeper on every retry")


# ---------------------------------------------------------------------------
# 11. plan: a folder below the album folder that is the destination itself
# ---------------------------------------------------------------------------
class B11_DestinationFolderInsideSource(Base):
    def test_existing_destination_folder_is_nested_into_itself(self):
        loose = self.lib / "Real Artist"                       # the album's tracks lie in the artist folder
        a = audio(loose / "01 a.flac", 500, album="Real Album")
        b = audio(loose / "02 b.flac", 600, album="Real Album")
        final = loose / "2001 - Real Album"
        final.mkdir()
        (final / "folder.jpg").write_bytes(b"cover the user put there")
        p = pl.build([self.proposal(source_dir=str(loose), files=[str(a), str(b)])],
                     self.profile(operation="move", destination=str(self.lib)))
        self.assertEqual(Path(p["albums"][0]["files"][0]["dst"]).parent, final)
        executor.Executor(p).run()
        self.assertTrue((final / "folder.jpg").exists(),
                        [str(x.relative_to(self.lib)) for x in self.lib.rglob("folder.jpg")])


# ---------------------------------------------------------------------------
# 12. filenames.safe_component
# ---------------------------------------------------------------------------
class B12_SafeComponent(unittest.TestCase):
    def test_reserved_name_after_leading_dots_or_spaces_are_removed(self):
        for text in (".aux", " con", "...Nul"):
            got = fnm.safe_component(text, "windows")
            self.assertNotRegex(got, r"(?i)^(con|prn|aux|nul|com\d|lpt\d)(\..*)?$", (text, got))

    def test_all_dots_name_is_hidden_with_linux_names(self):
        got = fnm.safe_component("...", "linux")
        self.assertFalse(got.startswith("."), got)


# ---------------------------------------------------------------------------
# 13. owner and group of files TagMender re-creates (it runs as root on Unraid)
# ---------------------------------------------------------------------------
@unittest.skipUnless(os.geteuid() == 0, "needs root")
class B13_Owner(Base):
    def setUp(self):
        super().setUp()
        for x in self.album.iterdir():
            os.chown(x, 99, 100)                               # nobody:users, as on an Unraid share

    def test_undo_of_in_place_tagging_keeps_the_owner(self):
        p = pl.build([self.proposal()], self.profile())
        executor.Executor(p).run()
        self.assertEqual((os.stat(self.f1).st_uid, os.stat(self.f1).st_gid), (99, 100))
        executor.undo(p["job"])
        self.assertEqual((os.stat(self.f1).st_uid, os.stat(self.f1).st_gid), (99, 100))

    def test_moved_companions_keep_the_owner(self):
        p = pl.build([self.proposal()], self.profile(operation="move", destination=str(self.dest)))
        executor.Executor(p).run()
        log = next(self.dest.rglob("rip.log"))
        self.assertEqual((os.stat(log).st_uid, os.stat(log).st_gid), (99, 100))





class B04b_HardLinkUndo(Base):
    def test_undo_removes_the_pre_existing_link(self):
        """Tags change too (the staging path): the job replaces the user's linked file at dst."""
        prof = self.profile(operation="move", destination=str(self.dest))
        p0 = pl.build([self.proposal()], prof)
        dst1 = Path(p0["albums"][0]["files"][0]["dst"])
        dst1.parent.mkdir(parents=True)
        os.link(self.f1, dst1)
        p = pl.build([self.proposal()], prof)
        self.assertEqual(p["albums"][0]["files"][0]["dst"], str(dst1))
        res = executor.Executor(p).run()
        self.assertEqual(res["counts"], {"done": 1}, res)
        executor.undo(p["job"])
        self.assertTrue(self.f1.exists())
        self.assertTrue(dst1.exists(), "the user's file at the destination was there before the job")


class B14_FolderAboveAnotherAlbum(Base):
    def test_files_in_a_folder_holding_another_album_below_stay(self):
        extras = self.album / "Extras"
        audio(extras / "Bonus Disc" / "01.flac", 650, album="Bonus", title="other album")
        (extras / "about the bonus.nfo").write_text("belongs with the bonus album")
        p = pl.build([self.proposal()], self.profile(operation="move", destination=str(self.dest)))
        moved = [Path(c["src"]).name for c in p["albums"][0]["companions"] if "Extras" in c["src"]]
        self.assertEqual(moved, [], "Extras holds music; its non-companion files must stay")


class B15_PlaylistWithBOM(Base):
    def test_first_entry_of_a_bom_playlist_is_rewritten(self):
        (self.album / "album.m3u8").write_bytes("﻿01 a.flac\n02 b.flac\n".encode("utf-8"))
        p = pl.build([self.proposal()], self.profile(operation="move", destination=str(self.dest)))
        executor.Executor(p).run()
        text = next(self.dest.rglob("album.m3u8")).read_text(encoding="utf-8-sig")
        self.assertEqual(text.splitlines(), ["01.F.flac", "02.S.flac"])


if __name__ == "__main__":
    unittest.main()
