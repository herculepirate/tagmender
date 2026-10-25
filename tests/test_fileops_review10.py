# Tenth independent review of TagMender's file operations (code at 6659aaa). Each test
# asserts the correct behaviour and fails at 6659aaa.
#   cd /home/claude/tagmender && TAGMENDER_DISCOGS_PLUGIN=/home/claude/herculepirate/picard-discogs-enrichment \
#     TAGMENDER_COVERUP_DIR=/home/claude/herculepirate/picard-coverup python3 -W ignore <this file>
import os as _os, tempfile as _tempfile
_os.environ["XDG_CONFIG_HOME"] = _tempfile.mkdtemp(prefix="tm-config-")
_os.environ["XDG_STATE_HOME"] = _tempfile.mkdtemp(prefix="tm-state-")
import errno
import json
import os
import pathlib
import shutil
import sys
import time
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "tests"))
import executor  # noqa: E402
import filenames as fnm  # noqa: E402
import plan as pl  # noqa: E402
import tagio  # noqa: E402
from test_executor import Base, audio, target  # noqa: E402
from test_fileops_review7 import LOSSY, Lossy, groups, tree  # noqa: E402
from test_fileops_review8 import id3_in_flac  # noqa: E402


class Crash(BaseException):
    """Stands for the process dying (power loss): nothing catches it."""


# ---------------------------------------------------------------------------------------
# R10-01. Since 6659aaa staging copies end in ".part" (".tm-<job>-<hash>.flac.part",
# executor._stage_name, executor.py:354-361). tagio.mutagen_file was taught to read the
# format inside such a name, but executor._restore (executor.py:480-486) hands its staging
# copy ".tm-undo-tags-<hash>.flac.part" to tag_albums.restore_tags, which opens it with
# plain mutagen.File (tag_albums.py:276). With no ".flac" at the end mutagen goes by the
# header alone, and a FLAC file with an ID3v2 tag in front (EAC with an external encoder
# and "Add ID3 tag" ticked - the R8-01 case) is taken for an MP3: "can't sync to MPEG
# frame". Under the previous name (".part.flac") the extension won and this worked.
#   - undo of ANY job that wrote tags to such a file fails for that track, every time
#     ("undone with problems" for ever);
#   - a rollback cannot put the old tags back either: one failing track in an in-place
#     job leaves the album half-tagged ("rollback problems"), and undo cannot fix it.
class R10_01_Id3FlacRestoreThroughPartName(Base):
    def setUp(self):
        super().setUp()
        id3_in_flac(self.f1)
        self.before = tagio.read(self.f1)

    def test_undo_of_a_tag_job(self):
        p = pl.build([self.proposal()], self.profile())
        self.assertEqual(executor.Executor(p).run()["counts"], {"done": 1})
        out = executor.undo(p["job"])
        self.assertFalse([o for o in out if o.startswith("PROBLEM")], out)
        self.assertEqual(tagio.read(self.f1), self.before)

    def test_rollback_of_a_tag_job(self):
        p = pl.build([self.proposal()], self.profile())
        bad = executor._stage_name(self.f2, p["job"])
        real = tagio.write

        def write(path, changes, *a, **k):
            if Path(path) == bad:
                raise executor.StepError("02 b.flac: tag album did not read back as written")
            return real(path, changes, *a, **k)
        with mock.patch.object(tagio, "write", write):
            res = executor.Executor(p).run()
        self.assertEqual(res["counts"], {"failed": 1})
        self.assertNotIn("rollback problems", res["albums"][0]["detail"])
        self.assertEqual(tagio.read(self.f1), self.before, "the album is left half-tagged")


# ---------------------------------------------------------------------------------------
# R10-02. Companion SUBFOLDERS keep names that are not valid with Windows-compatible names.
# plan._companions (plan.py:373) puts a companion at dmap[anchor] / d.relative_to(anchor),
# the source subfolder's name as it is; only the file name goes through _fit_name
# (plan.py:656, "made safe for the destination (Windows-forbidden characters)"). With the
# default compat "windows", an album with "Artwork: Scans?/" (or "Scans \"Deluxe\"",
# "Booklet|Inlay") is planned into exactly that folder. Copying or moving to an exFAT/FAT
# USB stick (a "Car" copy profile) or a Windows/Samba share: the mkdir fails with EINVAL,
# the album fails and is rolled back - on every run, for good - while the plan said
# nothing; the music itself would have fitted.
class R10_02_CompanionSubfolderNamesNotSafe(Base):
    def setUp(self):
        super().setUp()
        self.sub = self.album / "Artwork: Scans?"
        self.sub.mkdir()
        (self.sub / "back.jpg").write_bytes(b"back")

    def plan(self):
        return pl.build([self.proposal()], self.profile(operation="copy", destination=str(self.dest)))

    def test_planned_names_are_safe(self):
        p = self.plan()
        for c in p["albums"][0]["companions"]:
            for part in Path(c["dst"]).relative_to(self.dest).parts:
                self.assertEqual(part, fnm.safe_component(part, "windows"), c["dst"])

    def test_copy_to_a_fat_stick(self):
        real = pathlib.Path.mkdir

        def mkdir(self_, *a, **k):                     # as vfat/exfat answer a forbidden name
            if any(ch in self_.name for ch in ':*?"<>|\\'):
                raise OSError(errno.EINVAL, "Invalid argument", str(self_))
            return real(self_, *a, **k)
        p = self.plan()
        with mock.patch.object(pathlib.Path, "mkdir", mkdir):
            res = executor.Executor(p).run()
        self.assertEqual(res["counts"], {"done": 1}, res["albums"])
        self.assertTrue(any(x.endswith("back.jpg") for x in tree(self.dest)), tree(self.dest))


# ---------------------------------------------------------------------------------------
# R10-03. CoverUp's "local first" rule (coverup_batch.local_front, artwork.CoverUp.front:
# artwork.py:97-100) returns a scan already in the album as the front: "front" is that
# file's own path, e.g. "Album/Scans/Front.jpg" (the usual EAC layout with no folder.jpg).
# With a move, the plan (plan.py:685-699) sees no image at the top of the new album folder
# and plans a cover file there from that front. But Executor.album (executor.py:639-642)
# runs the companion steps BEFORE cover_file: Scans/Front.jpg has already been moved when
# cover_file hashes it (executor.py:914-916) -> FileNotFoundError, the whole album is rolled
# back. Nothing changed for the next run, so the album fails on every run, for good.
class R10_03_LocalFrontMovedBeforeTheCoverFile(Base):
    def test_x(self):
        from PIL import Image
        scans = self.album / "Scans"
        scans.mkdir()
        Image.new("RGB", (1200, 1200), (9, 8, 7)).save(scans / "Front.jpg")
        prop = self.proposal(artwork={"front": str(scans / "Front.jpg"), "source": "coverup:local",
                                      "width": 1200, "height": 1200})
        p = pl.build([prop], self.profile(operation="move", destination=str(self.dest)))
        res = executor.Executor(p).run()
        self.assertEqual(res["counts"], {"done": 1}, res["albums"])
        home = self.dest / "Real Artist" / "2001 - Real Album"
        self.assertTrue((home / "Scans" / "Front.jpg").exists(), tree(self.dest))
        self.assertFalse(self.album.exists(), tree(self.lib))


# ---------------------------------------------------------------------------------------
# R10-04. Journal.read (executor.py:88-97) decodes the whole journal with
# Path.read_text(encoding="utf-8") and only then skips "a line cut short by a crash"
# (R4-06). Journal lines are written with ensure_ascii=False, so any album or folder name
# with a non-ASCII letter (Björk, Sigur Rós, Beyoncé, any Japanese title) puts multi-byte
# UTF-8 into them. Power lost while such a line is being written can cut it inside a
# character: read_text raises UnicodeDecodeError. Recover's own newline does not help (the
# broken byte stays in the middle). From then on pending_jobs() raises, so every
# Executor.run, `tagmender recover`, `tagmender jobs` and every watcher tick crashes - the
# library can never be processed again until the journal is repaired by hand.
class R10_04_JournalTornInsideAUtf8Character(Base):
    def test_x(self):
        t = [target(1, "First", artist="Björk"), target(2, "Second", artist="Björk")]
        p = pl.build([self.proposal(targets=t)], self.profile(operation="move", destination=str(self.dest)))
        real = executor.Journal.write

        def jw(j, **ev):
            if ev.get("ev") == "file" and ev.get("state") == "begin" and ev.get("idx") == 1:
                ev["t"] = time.time()
                line = (json.dumps(ev, ensure_ascii=False) + "\n").encode("utf-8")
                cut = line.index("ö".encode("utf-8")) + 1          # power lost inside the "ö"
                with open(j.path, "ab") as fh:
                    fh.write(line[:cut])
                    fh.flush()
                    os.fsync(fh.fileno())
                raise Crash()
            return real(j, **ev)
        with mock.patch.object(executor.Journal, "write", jw):
            with self.assertRaises(Crash):
                executor.Executor(p).run()
        try:
            pending = executor.pending_jobs()
            executor.recover(p["job"])
        except UnicodeDecodeError as exc:
            self.fail(f"the torn journal can never be read again: {exc}")
        self.assertEqual(pending, [p["job"]])
        self.assertEqual(executor.pending_jobs(), [])
        self.assertTrue(self.f1.exists() and self.f2.exists(), tree(self.tmp))


# ---------------------------------------------------------------------------------------
# R10-05. Undo after edits splits the album. executor._undo (executor.py:1425-1440) puts
# every companion back first, without looking at whether the music it belongs to can be
# put back. When the tracks were edited after the job (ratings or a corrected title in
# foobar2000/Picard at their new place) undo rightly leaves the tracks alone - and still
# moves cover.jpg, rip.log and rip.cue back to the recreated OLD folder (the cue with its
# old text, naming files that are no longer there). The album keeps its music and loses
# its cover, log and cue to an orphan folder in the source library; running undo again
# does not bring them back (their steps are journalled as done). The same happens to a
# cue sheet rewritten in place for a rename within the library.
class R10_05_UndoTakesCompanionsFromMusicItLeaves(Base):
    def test_x(self):
        (self.album / "cover.jpg").write_bytes(b"front")
        p = pl.build([self.proposal()], self.profile(operation="move", destination=str(self.dest)))
        self.assertEqual(executor.Executor(p).run()["counts"], {"done": 1})
        for fe in p["albums"][0]["files"]:
            tagio.write(Path(fe["dst"]), {"rating": ["5"]})            # edited after the job
        out = executor.undo(p["job"])
        self.assertEqual(len([o for o in out if "edited after the job" in o]), 2, out)
        home = self.dest / "Real Artist" / "2001 - Real Album"
        self.assertEqual(sorted(x.name for x in home.iterdir()),
                         ["01.F.flac", "02.S.flac", "cover.jpg", "rip.cue", "rip.log"],
                         f"left in the old folder: {tree(self.lib)}")


# ---------------------------------------------------------------------------------------
# R10-06. Undo writes into an unmounted share. Applying a plan checks that the destination
# is the share planned (Executor.album, executor.py:604-611; plan.mounted for /media,
# /mnt, /run/media), but undo checks nothing:
#   - copy profile to a USB stick or NAS share ("Car", "Lossy") undone while it is not
#     mounted: the copies are not there, _undo_step unlinks with missing_ok
#     (executor.py:1068-1070, companions 1035-1037, covers 1418-1424), forgets them in the
#     copies table and journals the job "undone". Every copy stays on the stick, and the job
#     can never be undone again ("already undone");
#   - move from a share (downloads/inbox on the NAS) undone while that share is not mounted:
#     undo recreates the source folders (executor.py:1441-1443, 1098) on the system disk
#     under the empty mount point and renames the album there; it reports "undone". When the
#     share mounts again the album is hidden under it (and the system disk filled up).
class R10_06_UndoIntoAnUnmountedShare(Base):
    def share(self, name):
        self.media = self.tmp / "media"
        self.mp = self.media / name
        self.mp.mkdir(parents=True)
        self.on = True
        return self.mp

    def mounted(self):
        return mock.patch.object(pl.os.path, "ismount", lambda p: self.on and str(p) == str(self.mp))

    def unplug(self):
        os.rename(self.mp, self.tmp / "unplugged")
        self.mp.mkdir()
        self.on = False

    def replug(self):
        self.mp.rmdir()
        os.rename(self.tmp / "unplugged", self.mp)
        self.on = True

    def test_copy_undone_while_the_stick_is_out(self):
        shutil.rmtree(self.lib / "Some Artist")
        self.album = self.lib / "Some Artist" / "2001 - Some Album"
        self.f1 = audio(self.album / "01 a.mp3", 440, album="Some Album", title="a", artist="Some Artist")
        self.f2 = audio(self.album / "02 b.mp3", 550, album="Some Album", title="b", artist="Some Artist")
        stick = self.share("CAR")
        prof = self.profile(operation="copy", destination=str(stick), formats=LOSSY)
        with mock.patch.object(pl, "MOUNT_PARENTS", (str(self.media),)), self.mounted():
            p = pl.build([self.proposal()], prof)
            self.assertEqual(executor.Executor(p).run()["counts"], {"done": 1})
            self.assertEqual(len(tree(stick)), 2)
            self.unplug()
            try:
                executor.undo(p["job"])
            except executor.StepError:
                pass
            self.replug()
            try:
                out = executor.undo(p["job"])
            except executor.StepError as exc:
                out = [f"second undo refused: {exc}"]
        self.assertEqual(tree(stick), [], f"the copies stay on the stick: {out}")

    def test_move_undone_while_the_source_share_is_down(self):
        nas = self.share("nas")
        src_root = nas / "downloads"
        shutil.move(str(self.lib), str(src_root))
        album = src_root / "Some Artist" / "2001 - Some Album"
        files = [str(album / "01 a.flac"), str(album / "02 b.flac")]
        prof = self.profile(sources=[{"path": str(src_root)}], operation="move", destination=str(self.dest))
        with mock.patch.object(pl, "MOUNT_PARENTS", (str(self.media),)), self.mounted():
            p = pl.build([self.proposal(source_dir=str(album), files=files)], prof)
            self.assertEqual(executor.Executor(p).run()["counts"], {"done": 1})
            self.unplug()
            try:
                out = executor.undo(p["job"])
            except executor.StepError as exc:
                out = [str(exc)]
        self.assertEqual(tree(nas), [], f"written to the system disk under the mount point: {out}")
        self.assertEqual(len([x for x in tree(self.dest) if x.endswith(".flac")]), 2, out)


# ---------------------------------------------------------------------------------------
# R10-07. The R9-05 fix is incomplete. engine._one_compilation (engine.py:76-79) counts
# tracks without an album artist as one compilation "when their folder is named after the
# album" by a SUBSTRING test: fold(title) in fold(folder name). Demo tracks of different
# bands, each tagged album "Demo", in a "Demos/" folder ("demo" in "demos"), or "Live"
# tracks of two artists in "Delivery/" ("live" in "delivery"), are merged into one group
# again - exactly what R9-05 set out to stop: matched as one album that neither release
# fits, never as two singles, and moved together to _unresolved by the Inbox profile.
class R10_07_CompilationByFolderNameSubstring(Base):
    def test_x(self):
        shutil.rmtree(self.lib / "Some Artist")
        for folder, title in (("Demos", "Demo"), ("Delivery", "Live")):
            d = self.lib / folder
            audio(d / "Band A - Song.mp3", 440, album=title, artist="Band A", title="Song")
            audio(d / "Band B - Tune.mp3", 550, album=title, artist="Band B", title="Tune")
        gs = groups(self.profile(), self.lib)
        got = sorted((g.folder.name, len(g.files)) for g in gs)
        self.assertEqual(got, [("Delivery", 1), ("Delivery", 1), ("Demos", 1), ("Demos", 1)],
                         [[f.name for f in g.files] for g in gs])


# ---------------------------------------------------------------------------------------
# R10 (suspicion, confirmed here): the artwork the engine fetches for each plan
# (STATE_DIR/staging/<job>) was never deleted; `tagmender jobs prune` now removes old ones.
class R10_08_ArtworkStagingPruned(Base):
    def test_x(self):
        import exclusions as ex
        old = ex.STATE_DIR / "staging" / "20200101-000000-aaaa"
        new = ex.STATE_DIR / "staging" / "20990101-000000-bbbb"
        for d in (old, new):
            d.mkdir(parents=True)
            (d / "front.jpg").write_bytes(b"x")
        t = time.time() - 90 * 86400
        for x in (old / "front.jpg", old):
            os.utime(x, (t, t))
        waiting = ex.STATE_DIR / "staging" / "20200101-000000-cccc"     # its plan is still waiting
        waiting.mkdir()
        (waiting / "front.jpg").write_bytes(b"x")
        for x in (waiting / "front.jpg", waiting):
            os.utime(x, (t, t))
        (ex.STATE_DIR / "plans").mkdir(parents=True, exist_ok=True)
        (ex.STATE_DIR / "plans" / "20200101-000000-cccc-1.json").write_text("{}")
        executor.prune(30)
        self.assertFalse(old.exists())
        self.assertTrue((new / "front.jpg").exists())
        self.assertTrue((waiting / "front.jpg").exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
