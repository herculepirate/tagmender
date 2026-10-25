# File operations under stress: long names, crashes at every commit point, failed
# verification, disc sets, companion folders, cue sheets per disc, case-only renames,
# pruned jobs, names that would hide a folder, timestamps.
import os as _os, tempfile as _tempfile  # noqa: E402
_os.environ["XDG_CONFIG_HOME"] = _tempfile.mkdtemp(prefix="tm-config-")
_os.environ["XDG_STATE_HOME"] = _tempfile.mkdtemp(prefix="tm-state-")

import os
import sys
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "tests"))
import executor  # noqa: E402
import filenames as fnm  # noqa: E402
import payload  # noqa: E402
import plan as pl  # noqa: E402
import profiles as pf  # noqa: E402
import tagio  # noqa: E402
from test_executor import Base, audio, target  # noqa: E402


class Crash(BaseException):
    """Stands for the process dying: not an Exception, so nothing catches it."""


def files_named(root, pattern):
    return sorted(p for p in Path(root).rglob(pattern))


class LongNames(Base):
    def test_in_place_tagging_of_a_file_whose_name_is_near_the_limit(self):
        long = self.album / ("x" * 245 + ".flac")                # 250 bytes: legal, near the 255 limit
        audio(long, 700, title="old")
        prop = self.proposal(files=[str(long)], targets=[target(1, "New")])
        p = pl.build([prop], self.profile())
        res = executor.Executor(p).run()
        self.assertEqual(res["counts"], {"done": 1}, res)
        self.assertEqual(tagio.read(long)["title"], ["New"])
        self.assertEqual(executor.undo(p["job"])[-1:], [f"put back {long.name}"])
        self.assertEqual(tagio.read(long)["title"], ["old"])


class Crashes(Base):
    """A crash between two steps of a commit must leave something recover/undo can finish."""

    def leftovers(self):
        return [p for p in self.tmp.rglob("*.part")]

    def test_crash_after_in_place_commit_before_journal_then_recover_and_undo(self):
        prof = self.profile()
        p = pl.build([self.proposal()], prof)
        real = executor.Journal.write

        def write(j, **ev):
            if ev.get("state") == "committed":
                raise Crash()
            return real(j, **ev)
        with mock.patch.object(executor.Journal, "write", write), self.assertRaises(Crash):
            executor.Executor(p).run()
        self.assertEqual(tagio.read(self.f1)["album"], ["Real Album"])       # the rename had happened
        executor.recover(p["job"])
        executor.undo(p["job"])
        self.assertEqual(tagio.read(self.f1)["album"], ["Some Album"])      # undo found it
        self.assertEqual(self.leftovers(), [])

    def test_crash_inside_place_between_link_and_unlink(self):
        prof = self.profile(operation="copy", destination=str(self.dest))
        p = pl.build([self.proposal()], prof)
        real = os.unlink

        def unlink(path, *a, **k):
            if str(path).endswith(".part"):
                raise Crash()
            return real(path, *a, **k)
        with mock.patch.object(executor.os, "unlink", unlink), self.assertRaises(Crash):
            executor.Executor(p).run()
        executor.recover(p["job"])
        self.assertEqual(self.leftovers(), [])
        executor.undo(p["job"])
        self.assertEqual([x for x in self.dest.rglob("*") if x.is_file()], [])   # the copy is gone again
        self.assertTrue(self.f1.exists())

    def test_crash_in_a_rename_move_between_link_and_unlink(self):
        prof = self.profile(operation="move", destination=str(self.dest))
        prop = self.proposal(targets=[target(1, "First", album="Some Album"),
                                      target(2, "Second", album="Some Album")])
        p = pl.build([prop], prof)
        # tags already as planned for the first file: a plain rename
        for fe in p["albums"][0]["files"]:
            fe["changes"] = {}
        real = os.unlink

        def unlink(path, *a, **k):
            if Path(path) == self.f1:
                raise Crash()
            return real(path, *a, **k)
        with mock.patch.object(executor.os, "unlink", unlink), self.assertRaises(Crash):
            executor.Executor(p).run()
        executor.recover(p["job"])
        audio_now = [x for x in self.tmp.rglob("*.flac")]
        self.assertEqual(len(audio_now), 2, audio_now)                        # no duplicate left
        executor.undo(p["job"])
        self.assertTrue(self.f1.exists() and self.f2.exists())
        self.assertEqual(len([x for x in self.tmp.rglob("*.flac")]), 2)

    def test_crash_after_rename_move_before_journal(self):
        prof = self.profile(operation="move", destination=str(self.dest))
        p = pl.build([self.proposal()], prof)
        for fe in p["albums"][0]["files"]:
            fe["changes"] = {}
        real = executor.Journal.write

        def write(j, **ev):
            if ev.get("state") == "committed":
                raise Crash()
            return real(j, **ev)
        with mock.patch.object(executor.Journal, "write", write), self.assertRaises(Crash):
            executor.Executor(p).run()
        self.assertFalse(self.f1.exists())
        executor.recover(p["job"])
        executor.undo(p["job"])
        self.assertTrue(self.f1.exists(), "undo did not move the file back")


class FailedVerification(Base):
    def test_move_whose_new_file_does_not_verify_leaves_no_copy_behind(self):
        prof = self.profile(operation="move", destination=str(self.dest))
        p = pl.build([self.proposal()], prof)
        real = payload.digest
        dests = {fe["dst"] for fe in p["albums"][0]["files"]}

        def digest(path):
            d = real(path)
            return ("bad" + str(d)) if str(path) in dests else d
        with mock.patch.object(executor.payload, "digest", digest):
            res = executor.Executor(p).run()
        self.assertEqual(res["counts"], {"failed": 1})
        self.assertNotIn("rollback problems", res["albums"][0]["detail"])
        self.assertEqual([x for x in self.dest.rglob("*") if x.is_file()], [])
        self.assertTrue(self.f1.exists() and self.f2.exists())


class DiscSets(Base):
    def disc_set(self, root):
        a = audio(root / "Disc 01" / "01.flac", 500, album="Box", title="a")
        b = audio(root / "Disc 02" / "01.flac", 600, album="Box", title="b")
        return a, b

    def test_unresolved_disc_set_keeps_its_album_folder(self):
        box = self.lib / "Band" / "2005 - Box"
        a, b = self.disc_set(box)
        hold = self.tmp / "hold"
        hold.mkdir()
        prof = self.profile(unresolved={"action": "move", "move_to": str(hold), "allow_partial": False})
        prop = pl.Proposal(source_dir=str(box), files=[str(a), str(b)], status="unresolved",
                           reason="no_candidate: nothing", reason_codes=["no_candidate"])
        entry = pl.build([prop], prof)["albums"][0]
        self.assertEqual(sorted(fe["dst"] for fe in entry["files"]),
                         [str(hold / "2005 - Box" / "Disc 01" / "01.flac"),
                          str(hold / "2005 - Box" / "Disc 02" / "01.flac")])

    def test_moved_album_takes_its_scans_logs_and_per_disc_cue_sheets(self):
        box = self.lib / "Band" / "2005 - Box"
        a, b = self.disc_set(box)
        (box / "Disc 01" / "rip.log").write_text("EAC log 1")
        (box / "Disc 01" / "rip.cue").write_text('FILE "01.flac" WAVE\n  TRACK 01 AUDIO\n')
        (box / "Disc 02" / "rip.cue").write_text('FILE "01.flac" WAVE\n  TRACK 01 AUDIO\n')
        (box / "Disc 01" / "covers").mkdir()
        (box / "Disc 01" / "covers" / "Digi.jpg").write_bytes(b"jpg")
        (box / "Scans").mkdir()
        (box / "Scans" / "Back.jpg").write_bytes(b"back")
        prof = self.profile(operation="move", destination=str(self.dest))
        prop = self.proposal(source_dir=str(box), files=[str(a), str(b)],
                             targets=[target(1, "Alpha", album="Box", discnumber=["1"], totaldiscs=["2"]),
                                      target(1, "Beta", album="Box", discnumber=["2"], totaldiscs=["2"])])
        p = pl.build([prop], prof)
        self.assertEqual(p["albums"][0]["status"], "ready", pl.preview(p))
        res = executor.Executor(p).run()
        self.assertEqual(res["counts"], {"done": 1}, res)
        new = Path(p["albums"][0]["files"][0]["dst"]).parent.parent      # .../2001 - Box/
        self.assertEqual((new / "Disc 01" / "rip.log").read_text(), "EAC log 1")
        self.assertEqual((new / "Disc 01" / "covers" / "Digi.jpg").read_bytes(), b"jpg")
        self.assertEqual((new / "Scans" / "Back.jpg").read_bytes(), b"back")
        self.assertIn('FILE "101.A.flac"', (new / "Disc 01" / "rip.cue").read_text())
        self.assertIn('FILE "201.B.flac"', (new / "Disc 02" / "rip.cue").read_text())
        self.assertFalse(box.exists(), list(box.rglob("*")) if box.exists() else "")
        executor.undo(p["job"])
        self.assertEqual((box / "Scans" / "Back.jpg").read_bytes(), b"back")
        self.assertEqual((box / "Disc 02" / "rip.cue").read_text(), 'FILE "01.flac" WAVE\n  TRACK 01 AUDIO\n')
        self.assertTrue(a.exists() and b.exists())

    def test_a_subfolder_holding_other_music_stays(self):
        alb = self.lib / "Band" / "2005 - Main"
        a = audio(alb / "01.flac", 500, title="a")
        audio(alb / "Bonus" / "01.flac", 650, title="other album")   # not part of this album
        (alb / "Bonus" / "notes.txt").write_text("bonus notes")
        prof = self.profile(operation="move", destination=str(self.dest))
        p = pl.build([self.proposal(source_dir=str(alb), files=[str(a)], targets=[target(1, "A", album="Main")])],
                     prof)
        self.assertFalse([c for c in p["albums"][0]["companions"] if "Bonus" in c["src"]])


class CueSheets(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.d = Path(tempfile.mkdtemp())

    def test_chained_renames_are_not_applied_twice(self):
        c = self.d / "a.cue"
        c.write_text('FILE "01.flac" WAVE\nFILE "02.flac" WAVE\n')
        new, enc = pl._cue_rewrites(c, {"01.flac": "02.flac", "02.flac": "03.flac"})
        self.assertEqual(new, 'FILE "02.flac" WAVE\nFILE "03.flac" WAVE\n')

    def test_playlist_lines_with_folders_and_backslashes(self):
        m = self.d / "a.m3u"
        m.write_text("#EXTM3U\r\nDisc 01\\01.flac\r\nDisc 02/01.flac\r\n")
        new, _ = pl._cue_rewrites(m, {"Disc 01/01.flac": "Disc 01/101.A.flac",
                                      "Disc 02/01.flac": "Disc 02/201.B.flac"})
        self.assertEqual(new, "#EXTM3U\r\nDisc 01/101.A.flac\r\nDisc 02/201.B.flac\r\n")

    def test_cp1252_sheet_keeps_its_encoding_or_is_left_alone(self):
        c = self.d / "a.cue"
        c.write_bytes('TITLE "Caf\u00e9"\nFILE "01.flac" WAVE\n'.encode("cp1252"))
        new, enc = pl._cue_rewrites(c, {"01.flac": "01 Caf\u00e9.flac"})
        self.assertEqual(enc, "cp1252")
        self.assertIn('FILE "01 Caf\u00e9.flac"', new)
        new, enc = pl._cue_rewrites(c, {"01.flac": "01 \u6771\u4eac.flac"})
        self.assertIsNone(new)                                     # cannot be written in cp1252


class Undo(Base):
    def test_undo_of_a_pruned_job_is_refused_before_anything_changes(self):
        prof = self.profile(operation="move", destination=str(self.dest))
        p = pl.build([self.proposal()], prof)
        executor.Executor(p).run()
        jd = executor.job_dir(p["job"])
        with mock.patch.object(executor.time, "time", return_value=10 ** 11):
            self.assertEqual(executor.prune(1), [p["job"]])
        before = sorted(str(x) for x in self.tmp.rglob("*.flac"))
        with self.assertRaises(executor.StepError):
            executor.undo(p["job"])
        self.assertEqual(sorted(str(x) for x in self.tmp.rglob("*.flac")), before)
        self.assertTrue((jd / "PRUNED").exists())


class CaseOnlyRename(Base):
    def test_rename_that_only_changes_case_on_a_case_insensitive_share(self):
        src = self.album / "01 a.flac"
        dst = self.album / "01 A.flac"
        # a case-insensitive filesystem says the new name exists and is the same file
        real_link = os.link

        def link(a, b, *x, **k):
            if str(a).casefold() == str(b).casefold():
                raise FileExistsError(17, "File exists")
            return real_link(a, b, *x, **k)
        with mock.patch.object(executor.os, "link", link), \
                mock.patch.object(executor, "_same_file", lambda a, b: str(a).casefold() == str(b).casefold()):
            executor._place_move(src, dst)
        self.assertTrue(dst.exists())
        self.assertFalse(src.exists())


class Names(unittest.TestCase):
    def test_leading_dots_never_hide_a_folder_or_file(self):
        path, _ = fnm.make_path("/lib", ["Metallica", "1988 - ...And Justice for All", "...Intro"], ".flac", {})
        self.assertFalse([part for part in path.parts[1:] if part.startswith(".")], path)
        self.assertEqual(path.parts[-2], "1988 - ...And Justice for All")
        self.assertTrue(path.name.endswith(".flac"))

    def test_shortening_never_leaves_an_empty_or_reserved_component(self):
        path, _ = fnm.make_path("/lib", ["_" * 300, "CON " + "x" * 300, "t"], ".flac",
                                {"max_dir_bytes": 40})
        self.assertEqual(len(path.parts), 5, path)                     # /, lib, two folders, file
        for part in path.parts[2:4]:
            self.assertTrue(part.strip(" ."), path)
            self.assertNotRegex(part, r"(?i)^(con|prn|aux|nul)(\..*)?$")


class Timestamps(Base):
    def test_preserve_timestamps_keeps_the_modification_time(self):
        os.utime(self.f1, (1_600_000_000, 1_600_000_000))
        prop = self.proposal()
        prof = self.profile(preserve_timestamps=True)
        p = pl.build([prop], prof)
        executor.Executor(p).run()
        self.assertEqual(tagio.read(self.f1)["album"], ["Real Album"])
        self.assertEqual(int(os.stat(self.f1).st_mtime), 1_600_000_000)


if __name__ == "__main__":
    unittest.main()
