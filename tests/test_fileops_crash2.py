# Crash points inside undo and rollback, hard links, covers, partial rollbacks, WAV/AIFF and
# Ogg FLAC: cases from the third independent review, each of which failed before its fix.
import os as _os, tempfile as _tempfile  # noqa: E402
_os.environ["XDG_CONFIG_HOME"] = _tempfile.mkdtemp(prefix="tm-config-")
_os.environ["XDG_STATE_HOME"] = _tempfile.mkdtemp(prefix="tm-state-")

import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "tests"))
import executor  # noqa: E402
import payload  # noqa: E402
import plan as pl  # noqa: E402
import tagio  # noqa: E402
from test_executor import Base, audio, target, CROSS_FS, SHM  # noqa: E402
from test_fileops_crash import die, in_child  # noqa: E402


def hidden(*roots):
    return sorted(str(p) for r in roots for p in Path(r).rglob("*") if ".tm-" in p.name)


def snapshot(f):
    return (tagio.read(f), payload.digest(f), f.stat().st_size)


class CrossFsBase(Base):
    def setUp(self):
        super().setUp()
        self.shm = Path(tempfile.mkdtemp(dir=SHM))
        self.addCleanup(shutil.rmtree, self.shm, True)


# ---------------------------------------------------------------------------
# R1. a crash inside _put_back between link and unlink leaves .tm-undo-*.part as a
#     hard link to the put-back file; the next _restore of that file opens the same
#     staging name with "wb" and truncates the music file itself
# ---------------------------------------------------------------------------
@unittest.skipUnless(CROSS_FS, "needs /dev/shm on another filesystem")
class R1_UndoStageHardLinkTruncates(CrossFsBase):
    def kill_undo_at(self, stage_of):
        before = {f: snapshot(f) for f in (self.f1, self.f2)}
        prof = self.profile(operation="move", destination=str(self.shm))
        p = pl.build([self.proposal()], prof)
        res = executor.Executor(p).run()
        self.assertEqual(res["counts"], {"done": 1}, res)
        real = os.unlink
        stage = executor._stage_name(stage_of, "undo-put")

        def unlink(path, *a, **k):
            if Path(path) == stage:
                die()                          # _link_then_unlink: linked, staging name not removed
            return real(path, *a, **k)

        def child():
            with mock.patch.object(executor.os, "unlink", unlink):
                executor.undo(p["job"])
        self.assertEqual(in_child(child), 9)
        return p, before

    def test_kill_in_undo_put_back_of_a_track_then_undo_again(self):
        p, before = self.kill_undo_at(self.f2)
        out = executor.undo(p["job"])
        for f in (self.f1, self.f2):
            self.assertTrue(f.exists(), out)
            self.assertEqual(snapshot(f), before[f], f"{f.name} damaged: {out}")
        self.assertEqual(hidden(self.lib, self.shm), [], out)

    def test_kill_in_undo_put_back_of_a_companion_then_undo_again(self):
        p, before = self.kill_undo_at(self.album / "rip.log")
        out = executor.undo(p["job"])
        self.assertEqual((self.album / "rip.log").read_text(), "EAC log", out)
        self.assertEqual(hidden(self.lib, self.shm), [], out)


    def test_kill_in_rollback_put_back_then_recover(self):
        before = {f: snapshot(f) for f in (self.f1, self.f2)}
        prof = self.profile(operation="move", destination=str(self.shm))
        p = pl.build([self.proposal()], prof)
        dst2 = Path(p["albums"][0]["files"][1]["dst"])
        dst2.parent.mkdir(parents=True)
        dst2.write_bytes(b"squatter")                      # file 2 fails: album 1 is rolled back
        real = os.unlink

        def unlink(path, *a, **k):
            if Path(path).name.startswith(".tm-undo") and Path(path).parent == self.album:
                die()
            return real(path, *a, **k)

        def child():
            with mock.patch.object(executor.os, "unlink", unlink):
                executor.Executor(p).run()
        self.assertEqual(in_child(child), 9)
        out = executor.recover(p["job"])
        self.assertTrue(self.f1.exists(), out)
        self.assertEqual(snapshot(self.f1), before[self.f1], f"01 a.flac damaged by recover: {out}")
        self.assertEqual(hidden(self.lib, self.shm), [], out)


# ---------------------------------------------------------------------------
# R2/R3. _undo_relink is not resumable: a kill between os.link(src, stage) and
#        os.replace(stage, dst) makes every later undo fail on the staging name; a
#        kill after _put_back but before _restore finished leaves the job's new tags
#        on src, and the next undo links them over dst and calls the job undone
# ---------------------------------------------------------------------------
class R2_RelinkUndoResume(Base):
    def linked_job(self):
        prof = self.profile(operation="move", destination=str(self.dest))
        p0 = pl.build([self.proposal()], prof)
        dst1 = Path(p0["albums"][0]["files"][0]["dst"])
        dst1.parent.mkdir(parents=True)
        os.link(self.f1, dst1)                         # an *arr hard link already at the destination
        self.before = snapshot(self.f1)
        p = pl.build([self.proposal()], prof)
        self.assertTrue(p["albums"][0]["files"][0].get("linked"))
        res = executor.Executor(p).run()
        self.assertEqual(res["counts"], {"done": 1}, res)
        return p, dst1

    def test_kill_between_link_and_replace_then_undo_again(self):
        p, dst1 = self.linked_job()
        real = os.replace
        stage = executor._stage_name(dst1, "undo-link")

        def replace(a, b, *x, **k):
            if Path(a) == stage:
                die()
            return real(a, b, *x, **k)

        def child():
            with mock.patch.object(executor.os, "replace", replace):
                executor.undo(p["job"])
        self.assertEqual(in_child(child), 9)
        out = executor.undo(p["job"])
        self.assertFalse([o for o in out if o.startswith("PROBLEM")], out)
        self.assertTrue(os.path.samefile(self.f1, dst1), out)
        self.assertEqual(snapshot(self.f1), self.before, out)
        self.assertEqual(hidden(self.lib, self.dest), [], out)

    def test_kill_while_restoring_the_old_tags_then_undo_again(self):
        p, dst1 = self.linked_job()

        def child():
            real = executor._restore

            def restore(path, *a, **k):
                if Path(path) == self.f1:
                    die()                          # killed while writing the old tags back
                return real(path, *a, **k)
            with mock.patch.object(executor, "_restore", restore):
                executor.undo(p["job"])
        self.assertEqual(in_child(child), 9)
        self.assertTrue(self.f1.exists())                 # put back (with the job's tags), not restored
        out = executor.undo(p["job"])
        self.assertEqual(tagio.read(self.f1), self.before[0], f"old tags never put back: {out}")
        self.assertTrue(os.path.samefile(self.f1, dst1), out)


# ---------------------------------------------------------------------------
# R4. a hard-linked copy whose only change is the front cover: the planner turns its
#     op "none" into "tag" with dst != src, the executor runs it as "relink_tag", and
#     neither rollback nor undo knows that op: undo says "put back" and does nothing
# ---------------------------------------------------------------------------
class R4_RelinkTagNeverUndone(Base):
    def test_cover_embedded_into_a_linked_copy_is_undone(self):
        from PIL import Image
        t1, t2 = dict(target(1, "First"), tagmender=["Resolved"]), dict(target(2, "Second"), tagmender=["Resolved"])
        tagio.write(self.f1, t1)
        tagio.write(self.f2, t2)
        front = self.tmp / "front.jpg"
        Image.new("RGB", (600, 600), (1, 2, 3)).save(front)
        prof = self.profile(operation="copy", destination=str(self.dest))
        prop = self.proposal(targets=[t1, t2], artwork={"front": str(front), "source": "t"})
        p0 = pl.build([prop], prof)
        dst1 = Path(p0["albums"][0]["files"][0]["dst"])
        dst1.parent.mkdir(parents=True)
        os.link(self.f1, dst1)
        p = pl.build([prop], prof)
        fe = p["albums"][0]["files"][0]
        self.assertEqual((fe["op"], fe.get("linked")), ("copy", True))
        res = executor.Executor(p).run()
        self.assertEqual(res["counts"], {"done": 1}, res)
        self.assertTrue(tagio.read(dst1).get("~pictures"))            # the job wrote a new file there
        out = executor.undo(p["job"])
        self.assertFalse(tagio.read(dst1).get("~pictures"), f"undo left the job's file in place: {out}")
        self.assertTrue(os.path.samefile(self.f1, dst1), out)


# ---------------------------------------------------------------------------
# R5. an album whose rollback met a problem is marked album_rolled_back; undo skips
#     every rolled-back album, so the step that was never rolled back is never undone
# ---------------------------------------------------------------------------
class R5_RollbackProblemNeverUndone(Base):
    def test_track_left_at_destination_is_put_back_by_undo(self):
        prof = self.profile(operation="move", destination=str(self.dest))
        t1, t2 = target(1, "First"), target(2, "Second")
        for f, t in ((self.f1, t1), (self.f2, t2)):
            tagio.write(f, dict(t, tagmender=["Resolved"]))
        p = pl.build([self.proposal(targets=[t1, t2])], prof)
        dst1, dst2 = (Path(fe["dst"]) for fe in p["albums"][0]["files"])
        dst2.parent.mkdir(parents=True)
        dst2.write_bytes(b"squatter")                        # file 2 fails
        real = os.link

        def link(a, b, *x, **k):
            if Path(b) == self.f1:                            # moving file 1 back: share busy, once
                raise PermissionError(16, "Device or resource busy", str(b))
            return real(a, b, *x, **k)
        with mock.patch.object(executor.os, "link", link):
            res = executor.Executor(p).run()
        self.assertIn("rollback problems", res["albums"][0]["detail"])
        self.assertFalse(self.f1.exists())
        out = executor.undo(p["job"])                          # the share is fine again
        self.assertTrue(self.f1.exists(), f"01 a.flac never put back: {out}")
        self.assertFalse(dst1.exists(), out)


# ---------------------------------------------------------------------------
# R6. a destination that is a hard link to the source whose name differs only in
#     letter case (case-sensitive filesystem): the planner does not mark it "linked"
#     (fnm.fold equal), the executor takes it for a case-only rename, and
#     rename(tmp, dst) on two links of one inode does nothing: a hidden .tm-case-*
#     link stays in the album folder; undo then removes the user's own "01.F.flac"
# ---------------------------------------------------------------------------
class R6_CaseVariantHardLink(Base):
    def test_case_variant_hard_link_at_destination(self):
        final = self.lib / "Real Artist" / "2001 - Real Album"
        final.mkdir(parents=True)
        t1, t2 = dict(target(1, "First"), tagmender=["Resolved"]), dict(target(2, "Second"), tagmender=["Resolved"])
        a = final / "01.f.flac"
        b = final / "02.S.flac"
        shutil.move(str(self.f1), a)
        shutil.move(str(self.f2), b)
        tagio.write(a, t1)
        tagio.write(b, t2)
        os.link(a, final / "01.F.flac")                 # the same file under a second, case-variant name
        prof = self.profile(operation="move", destination=str(self.lib))
        p = pl.build([self.proposal(files=[str(a), str(b)], source_dir=str(final), targets=[t1, t2])], prof)
        fe = p["albums"][0]["files"][0]
        self.assertEqual((fe["dst"], fe["op"]), (str(final / "01.F.flac"), "move"))
        res = executor.Executor(p).run()
        self.assertEqual(res["counts"], {"done": 1}, res)
        self.assertEqual(hidden(self.lib), [], "hidden .tm-case link left in the album folder")
        executor.undo(p["job"])
        self.assertTrue(a.exists())
        self.assertTrue((final / "01.F.flac").exists(), "undo removed the user's pre-existing link")
        self.assertEqual(hidden(self.lib), [])


# ---------------------------------------------------------------------------
# R7. WAV/AIFF count as "id3" (tagio.family), so _backup takes a tag snapshot:
#     tag_albums.backup_tags saves the file's _WaveID3/_IFFID3 into an empty temp
#     file, which mutagen parses as RIFF/FORM and fails ("Header size < 8"): every
#     album holding a tagged WAV/AIFF fails before anything is written. (Behind it,
#     restore_tags would write the snapshot back with a bare ID3.save(path).)
# ---------------------------------------------------------------------------
class R7_UndoOfWavTags(Base):
    def make(self, name, codec):
        import subprocess
        f = self.album / name
        subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
                        *codec, "-y", str(f)], check=True)
        tagio.write(f, {"album": ["Some Album"], "title": ["a"], "artist": ["Some Artist"]})   # e.g. by Picard
        return f

    def check(self, f, magic):
        before = (f.read_bytes()[:4], tagio.read(f), payload.digest(f))
        prop = self.proposal(files=[str(f)], targets=[target(1, "First")])
        p = pl.build([prop], self.profile())
        res = executor.Executor(p).run()
        self.assertEqual(res["counts"], {"done": 1}, res)
        out = executor.undo(p["job"])
        self.assertFalse([o for o in out if o.startswith("PROBLEM")], out)
        self.assertEqual(f.read_bytes()[:4], magic, f"{f.name} no longer starts with {magic!r}: {out}")
        self.assertEqual((tagio.read(f), payload.digest(f)), before[1:], out)

    def test_wav(self):
        self.check(self.make("01 a.wav", ["-c:a", "pcm_s16le"]), b"RIFF")

    def test_aiff(self):
        self.check(self.make("01 a.aiff", ["-c:a", "pcm_s16be"]), b"FORM")


# ---------------------------------------------------------------------------
# P1. two albums landing in one folder (two copies of one release): the first gets a
#     new cover.jpg (artwork "file"), the second's own cover.jpg companion is planned
#     to the same name - artwork files are never claimed, so the planner misses it and
#     the second album fails at its companion step
# ---------------------------------------------------------------------------
class P1_CoverFileVersusOtherAlbumsCompanion(Base):
    def test_both_albums_are_done(self):
        from PIL import Image
        front = self.tmp / "front.jpg"
        Image.new("RGB", (60, 60), (1, 2, 3)).save(front)
        twin = self.lib / "Some Artist" / "2001 - Some Album (copy)"
        g1 = audio(twin / "01 a.flac", 445, album="Some Album", title="a")
        g2 = audio(twin / "02 b.flac", 555, album="Some Album", title="b")
        (twin / "cover.jpg").write_bytes(b"the twin's own scan")
        prof = self.profile(operation="move", destination=str(self.dest))
        p = pl.build([self.proposal(artwork={"front": str(front), "source": "t"}),
                      self.proposal(source_dir=str(twin), files=[str(g1), str(g2)])], prof)
        self.assertEqual([a["status"] for a in p["albums"]], ["ready", "ready"], pl.preview(p))
        res = executor.Executor(p).run()
        self.assertEqual(res["counts"], {"done": 2}, res["albums"][1]["detail"])


# ---------------------------------------------------------------------------
# P2. one file under two names (an *arr hard link: library + downloads) planned as two
#     albums: the library name claims the destination, so the downloads name is not
#     seen as "already there" (fe["linked"]) but gets a suffixed name next to it - the
#     same track now shows twice in the album folder
# ---------------------------------------------------------------------------
class P2_HardLinkPairInOnePlan(Base):
    def test_no_second_copy_in_the_library(self):
        t1, t2 = dict(target(1, "First"), tagmender=["Resolved"]), dict(target(2, "Second"), tagmender=["Resolved"])
        final = self.lib / "Real Artist" / "2001 - Real Album"
        final.mkdir(parents=True)
        a, b = final / "01.F.flac", final / "02.S.flac"
        shutil.move(str(self.f1), a)
        shutil.move(str(self.f2), b)
        tagio.write(a, t1)
        tagio.write(b, t2)
        dl = self.lib / "downloads" / "Real Artist - Real Album (2001) [FLAC]"
        dl.mkdir(parents=True)
        os.link(a, dl / "01 a.flac")
        os.link(b, dl / "02 b.flac")
        prof = self.profile(operation="move", destination=str(self.lib))
        props = [self.proposal(source_dir=str(final), files=[str(a), str(b)], targets=[t1, t2]),
                 self.proposal(source_dir=str(dl), files=[str(dl / "01 a.flac"), str(dl / "02 b.flac")],
                               targets=[t1, t2])]
        p = pl.build(props, prof)
        executor.Executor(p).run()
        self.assertEqual(sorted(x.name for x in final.iterdir()), ["01.F.flac", "02.S.flac"], pl.preview(p))


# ---------------------------------------------------------------------------
# P3. EMBEDDABLE counts .ogg/.oga as embeddable, but embed_front has no branch for
#     Ogg FLAC (tagio reads and writes it as "vorbis"): an album holding one fails
#     entirely when a front cover is to be embedded
# ---------------------------------------------------------------------------
class P3_OggFlacCover(Base):
    def test_ogg_flac_album_with_a_cover(self):
        import subprocess
        from PIL import Image
        f = self.album / "01 a.oga"
        subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
                        "-c:a", "flac", "-f", "ogg", "-y", str(f)], check=True)
        front = self.tmp / "front.jpg"
        Image.new("RGB", (60, 60), (1, 2, 3)).save(front)
        p = pl.build([self.proposal(files=[str(f)], targets=[target(1, "First")],
                                    artwork={"front": str(front), "source": "t"})], self.profile())
        self.assertEqual(p["albums"][0]["artwork"]["embed_into"], [str(f)])
        res = executor.Executor(p).run()
        self.assertEqual(res["counts"], {"done": 1}, res["albums"][0]["detail"])


if __name__ == "__main__":
    unittest.main()
