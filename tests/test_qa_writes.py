# Tag writing, file operations, companions, recovery and undo: defects found by QA (0.8.12),
# each test failing before its fix.
import os as _os, tempfile as _tempfile  # noqa: E402
_os.environ["XDG_CONFIG_HOME"] = _tempfile.mkdtemp(prefix="tm-config-")
_os.environ["XDG_STATE_HOME"] = _tempfile.mkdtemp(prefix="tm-state-")

import os
import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_executor import Base, target, executor, payload, pl, tagio  # noqa: E402


def encode(path, freq, codec, **tags):
    path.parent.mkdir(parents=True, exist_ok=True)
    meta = sum((["-metadata", f"{k}={v}"] for k, v in tags.items()), [])
    subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", f"sine=frequency={freq}:duration=1",
                    "-c:a", codec, *meta, "-y", str(path)], check=True)
    return path


class TrueAudioWrites(Base):
    """A TrueAudio file (ID3, as the format table says) without an ID3 tag: its audio digest
    was "apev2:..." before the write and "mpeg:..." after it (the same bytes hashed, named by
    what follows the new ID3 header), so every tag write failed its audio check."""

    def test_digest_kind_kept_when_an_id3_tag_is_added(self):
        f = encode(self.tmp / "t.tta", 440, "tta")
        before = payload.digest(f)
        tagio.write(f, {"title": ["New"]})
        self.assertEqual(tagio.read(f)["title"], ["New"])
        self.assertEqual(payload.digest(f), before)

    def test_tag_job_and_undo(self):
        al = self.lib / "TTA" / "Album"
        fs = [encode(al / f"0{n} t.tta", 440 + 100 * n, "tta") for n in (1, 2)]
        digests = [payload.digest(f) for f in fs]
        before = [tagio.read(f) for f in fs]
        prop = self.proposal(source_dir=str(al), files=[str(f) for f in fs])
        res = executor.Executor(pl.build([prop], self.profile())).run()
        self.assertEqual(res["counts"], {"done": 1}, res)
        self.assertEqual([tagio.read(f)["title"] for f in fs], [["First"], ["Second"]])
        self.assertEqual([payload.digest(f) for f in fs], digests)
        out = executor.undo(res["job"])
        self.assertFalse([o for o in out if o.startswith("PROBLEM")], out)
        self.assertEqual([tagio.read(f) for f in fs], before)
        self.assertEqual([payload.digest(f) for f in fs], digests)


class TrueAudioJobOfAnEarlierVersion(Base):
    # a job of an earlier version on a TrueAudio file that already had an ID3 tag journalled its
    # audio as "mpeg:H"; read now as "apev2:H" (the same hash), undo left every file alone as
    # "holds different audio now" - and --force did not help
    def test_undone(self):
        al = self.lib / "TTA" / "Album"
        fs = [encode(al / f"0{n} t.tta", 440 + 100 * n, "tta") for n in (1, 2)]
        for f in fs:
            tagio.write(f, {"title": ["old"]})
        new_sniff = payload._sniff

        def old_sniff(fh):
            got = new_sniff(fh)
            fh.seek(0)
            return "mpeg" if fh.read(3) == b"ID3" and got == "apev2" else got
        payload._sniff = old_sniff
        try:
            self.assertTrue(payload.digest(fs[0]).startswith("mpeg:"))
            res = executor.Executor(pl.build([self.proposal(source_dir=str(al), files=[str(f) for f in fs])],
                                             self.profile())).run()
        finally:
            payload._sniff = new_sniff
        self.assertEqual(res["counts"], {"done": 1})
        out = executor.undo(res["job"])
        self.assertFalse([x for x in out if "PROBLEM" in x], out)
        self.assertEqual([tagio.read(f).get("title") for f in fs], [["old"], ["old"]])

    def test_same(self):
        self.assertTrue(payload.same("apev2:ab", "mpeg:ab"))
        self.assertTrue(payload.same("flac:ab", "flac:ab"))
        self.assertFalse(payload.same("apev2:ab", "mpeg:cd"))
        self.assertFalse(payload.same("flac:ab", "mpeg:ab"))
        self.assertFalse(payload.same(None, "mpeg:ab"))
        self.assertTrue(payload.same(None, None))


class ApeCustomNamesAsTheWriterStoresThem(Base):
    # holds() checked a custom name title-cased while the writer stores it as named: TAG, ID3 and
    # a ligature passed and the album failed at Apply
    def test_holds_agrees_with_the_write(self):
        f = encode(self.tmp / "x.wv", 440, "wavpack")
        for field in ("~custom:TAG", "~custom:ID3", "~custom:\ufb01x", "~custom:Mood", "mytag", "~custom:x"):
            held = tagio.holds(f, field)
            try:
                tagio.write(f, {field: ["v"]})
                wrote = True
            except Exception:
                wrote = False
            self.assertEqual(held, wrote, field)


class StagingNamesOfALongName(Base):
    # Python 3.10/3.11 stat a glob pattern without wildcards: for a name near the 255-byte limit
    # the earlier versions' staging name raised "File name too long" and undo stopped
    def test_no_staging_file_rather_than_an_error(self):
        import errno
        from unittest import mock
        dst = self.lib / ("x" * 240 + ".flac")

        def too_long(self_, pattern):
            raise OSError(errno.ENAMETOOLONG, "File name too long", pattern)
        with mock.patch.object(Path, "glob", too_long):
            self.assertEqual(executor._all_stage_names(dst, "job1"), [])


class UnquotedCueFile(Base):
    """A cue sheet naming a file without quotes (FILE 01.flac WAVE: allowed for a name without
    spaces, and read so by the matcher) kept the old name after the files were renamed."""

    def test_move_rewrites_unquoted_names_and_undo_puts_the_sheet_back(self):
        os.rename(self.f1, self.album / "01_a.flac")
        self.f1 = self.album / "01_a.flac"
        old = 'FILE 01_a.flac WAVE\r\n  TRACK 01 AUDIO\r\nFILE "02 b.flac" WAVE\r\n  TRACK 02 AUDIO\r\n'
        (self.album / "rip.cue").write_bytes(old.encode())
        prof = self.profile(operation="move", destination=str(self.dest))
        p = pl.build([self.proposal(files=[str(self.f1), str(self.f2)])], prof)
        res = executor.Executor(p).run()
        self.assertEqual(res["counts"], {"done": 1}, res)
        new_dir = self.dest / "Real Artist" / "2001 - Real Album"
        self.assertEqual((new_dir / "rip.cue").read_bytes().decode(),
                         'FILE 01.F.flac WAVE\r\n  TRACK 01 AUDIO\r\nFILE "02.S.flac" WAVE\r\n  TRACK 02 AUDIO\r\n')
        executor.undo(p["job"])
        self.assertEqual((self.album / "rip.cue").read_bytes().decode(), old)

    def test_new_name_with_spaces_is_quoted(self):
        cue = self.tmp / "x.cue"
        cue.write_text("FILE a.flac WAVE\n")
        self.assertEqual(pl._cue_rewrites(cue, {"a.flac": "01 A b.flac"}), ('FILE "01 A b.flac" WAVE\n', "utf-8"))


class SheetNamesInAnotherUnicodeForm(Base):
    """A cue sheet or playlist naming a file in the other Unicode form than the file's name
    on disk (written on a Mac: "Café" composed in the sheet, decomposed in the copied file
    names, or the reverse) kept the old name when the file was renamed."""

    def test_rewritten_either_way(self):
        import unicodedata
        nfc, nfd = (unicodedata.normalize(f, "01 Café.flac") for f in ("NFC", "NFD"))
        cue = self.tmp / "x.cue"
        for in_sheet, on_disk in ((nfc, nfd), (nfd, nfc)):
            cue.write_text(f'FILE "{in_sheet}" WAVE\n', encoding="utf-8")
            self.assertEqual(pl._cue_rewrites(cue, {on_disk: "01 New.flac"}), ('FILE "01 New.flac" WAVE\n', "utf-8"))
            cue.write_text(f"#EXTM3U\n{in_sheet}\n", encoding="utf-8")
            self.assertEqual(pl._cue_rewrites(cue, {on_disk: "01 New.flac"}), ("#EXTM3U\n01 New.flac\n", "utf-8"))

    def test_move_rewrites_the_sheet(self):
        import unicodedata
        f1 = self.album / unicodedata.normalize("NFD", "01 Café.flac")
        os.rename(self.f1, f1)
        old = f'FILE "{unicodedata.normalize("NFC", f1.name)}" WAVE\nFILE "02 b.flac" WAVE\n'
        (self.album / "rip.cue").write_text(old, encoding="utf-8")
        p = pl.build([self.proposal(files=[str(f1), str(self.f2)])],
                     self.profile(operation="move", destination=str(self.dest)))
        res = executor.Executor(p).run()
        self.assertEqual(res["counts"], {"done": 1}, res)
        cue = next(self.dest.rglob("rip.cue"))
        self.assertEqual(cue.read_text(encoding="utf-8"), 'FILE "01.F.flac" WAVE\nFILE "02.S.flac" WAVE\n')
        executor.undo(p["job"])
        self.assertEqual((self.album / "rip.cue").read_text(encoding="utf-8"), old)


class CustomTagNamesTheFormatCannotStore(Base):
    """A custom tag (a tagger script's $set(ключ,...)) named so the format cannot store it - a
    Vorbis comment or APEv2 item outside ASCII or with "=", an MP4 atom beyond Latin-1 - was
    planned and shown, then failed the whole album at Apply with an empty error (mutagen's
    ValueError). Like Picard (supports_tag), the plan leaves such a tag out."""

    BAD = {"ключ": ["v"], "a=b": ["v"]}

    def test_holds(self):
        for name in ("x.flac", "x.ogg", "x.opus"):
            self.assertFalse(tagio.holds(name, "ключ"))
            self.assertFalse(tagio.holds(name, "a=b"))
            self.assertFalse(tagio.holds(name, "~custom:Ключ"))
            self.assertTrue(tagio.holds(name, "MyTag".lower()))
            self.assertTrue(tagio.holds(name, "performer:гитара"))       # (the role goes into the value)
        self.assertFalse(tagio.holds("x.wv", "ключ"))
        self.assertFalse(tagio.holds("x.m4a", "ключ"))
        self.assertTrue(tagio.holds("x.m4a", "ünï"))
        self.assertTrue(tagio.holds("x.mp3", "ключ"))                    # (TXXX holds any name)

    def test_album_is_written_without_them(self):
        prop = self.proposal(targets=[target(1, "First", **self.BAD), target(2, "Second", **self.BAD)])
        p = pl.build([prop], self.profile())
        self.assertEqual(p["albums"][0]["status"], "ready")
        for fe in p["albums"][0]["files"]:
            self.assertFalse(set(fe["changes"]) & set(self.BAD), fe["changes"])
        res = executor.Executor(p).run()
        self.assertEqual(res["counts"], {"done": 1}, res)
        self.assertEqual(tagio.read(self.f1)["title"], ["First"])

    def test_mp3_keeps_them(self):
        from test_executor import audio
        m = audio(self.album / "03 c.mp3", 700, album="Some Album", title="c")
        prop = self.proposal(files=[str(m)], targets=[target(1, "First", **self.BAD)])
        res = executor.Executor(pl.build([prop], self.profile())).run()
        self.assertEqual(res["counts"], {"done": 1}, res)
        self.assertEqual(tagio.read(m).get("ключ"), ["v"])


class JournalWriteFailsOnce(Base):
    """A journal write that fails once (a full or flaky state disk) right after a step had
    changed a file: the step was not yet among the album's steps to put back, so the album was
    reported "put back as it was" while the file kept its new tags (in place: and undo, which
    knew nothing of it, could never restore them), or a moved, copied or created file stayed
    at the destination beside the original. The step counts as the album's before its journal
    line now."""

    def tree(self):
        return {str(p): p.read_bytes() for r in (self.lib, self.dest) for p in r.rglob("*") if p.is_file()}

    def plan(self, op):
        from PIL import Image
        front = self.tmp / "front.jpg"
        Image.new("RGB", (60, 60), (1, 2, 3)).save(front)
        kw = {} if op == "in_place" else {"operation": op, "destination": str(self.dest)}
        return pl.build([self.proposal(artwork={"front": str(front), "source": "t"},
                                       sidecars={str(self.f1): "[00:00.00]la"})],
                        self.profile(artwork={"enabled": True, "embed_front": True, "files": True}, **kw))

    def test_each_step_is_put_back(self):
        from unittest import mock
        import errno
        real = executor.Journal.write
        cases = [("in_place", ("file", "committed")), ("in_place", ("cover_file", None)),
                 ("in_place", ("new_file", None)), ("move", ("file", "committed")),
                 ("move", ("file", "source_removed")), ("move", ("companion", "committed")),
                 ("move", ("dir_created", None)), ("copy", ("file", "committed")),
                 ("copy", ("companion", "committed")), ("copy", ("new_file", None))]
        for op, (ev_, state) in cases:
            with self.subTest(op=op, ev=ev_, state=state):
                self.tearDown()
                self.setUp()
                p = self.plan(op)
                before = self.tree()
                hit = []

                def jw(j, **ev):
                    if not hit and ev.get("ev") == ev_ and (state is None or ev.get("state") == state):
                        hit.append(ev)
                        raise OSError(errno.EIO, "journal write failed")
                    return real(j, **ev)
                with mock.patch.object(executor.Journal, "write", jw):
                    res = executor.Executor(p).run()
                self.assertTrue(hit)
                self.assertEqual(res["counts"], {"failed": 1}, res)
                self.assertIn("album put back as it was", res["albums"][0]["detail"])
                self.assertEqual(self.tree(), before)
                self.assertEqual([x for x in self.dest.rglob("*")], [])
                self.assertEqual([x for x in self.lib.rglob(".tm-*")], [])


class AlbumFolderLinkedFromOutside(Base):
    """An album folder inside the library that is a symbolic link to a folder outside the
    profile's folders: in place, the plan was "ready" and Apply failed (the write is refused
    there); a move took the files out of the outside folder - removing them there - though
    writes stay inside the plan's folders. The plan says so now, and the executor refuses to
    remove a source outside them; a copy, which only reads the source, still goes."""

    def link_album(self):
        import shutil
        import tempfile
        outside = Path(tempfile.mkdtemp(prefix="outside-"))
        self.addCleanup(shutil.rmtree, outside, True)
        real = outside / "Album"
        shutil.move(str(self.album), real)
        os.symlink(real, self.album)
        return real

    def prop(self):
        return self.proposal(files=[str(self.album / "01 a.flac"), str(self.album / "02 b.flac")])

    def test_plan_blocks_in_place_and_move(self):
        real = self.link_album()
        before = {p.name: p.read_bytes() for p in real.iterdir()}
        for kw in ({}, {"operation": "move", "destination": str(self.dest)}):
            p = pl.build([self.prop()], self.profile(**kw))
            self.assertEqual(p["albums"][0]["status"], "blocked", kw)
            self.assertIn("outside the folders this profile may change", p["albums"][0]["conflicts"][0])
            executor.Executor(p).run()
        self.assertEqual({p.name: p.read_bytes() for p in real.iterdir()}, before)

    def test_executor_refuses_to_move_out_of_it(self):
        p = pl.build([self.prop()], self.profile(operation="move", destination=str(self.dest)))
        self.assertEqual(p["albums"][0]["status"], "ready")
        real = self.link_album()                   # (linked away after the plan was made)
        before = {p.name: p.read_bytes() for p in real.iterdir()}
        res = executor.Executor(p).run()
        self.assertEqual(res["counts"], {"failed": 1}, res)
        self.assertIn("outside the folders", res["albums"][0]["detail"])
        self.assertEqual({p.name: p.read_bytes() for p in real.iterdir()}, before)
        self.assertEqual([x for x in self.dest.rglob("*.*")], [])

    def test_copy_still_goes(self):
        real = self.link_album()
        res = executor.Executor(pl.build([self.prop()], self.profile(operation="copy",
                                                                     destination=str(self.dest)))).run()
        self.assertEqual(res["counts"], {"done": 1}, res)
        self.assertEqual(len(list(real.glob("*.flac"))), 2)
        self.assertEqual(len(list(self.dest.rglob("*.flac"))), 2)


@unittest.skipIf(os.geteuid() == 0, "root writes into read-only folders")
class UndoFolderCannotBeMade(Base):
    """Undo of a move whose emptied source folder could not be made again (its parent
    read-only now) ended in an uncaught PermissionError: no other file of the job went back,
    and nothing recorded the undo as partial. It is a problem of its own now; the rest goes
    back, and undo again (when the folder can be made) finishes."""

    def test_problem_reported_and_second_undo_finishes(self):
        other = self.lib / "Other" / "Album2"
        g = self.album.parent.parent / "Other" / "Album2" / "01 g.flac"
        from test_executor import audio
        audio(g, 700, album="X", title="g")
        before = {p: p.read_bytes() for p in self.lib.rglob("*") if p.is_file()}
        prof = self.profile(operation="move", destination=str(self.dest))
        p = pl.build([self.proposal(),
                      self.proposal(source_dir=str(other), files=[str(g)],
                                    targets=[target(1, "Gee", album="Second Album")])], prof)
        res = executor.Executor(p).run()
        self.assertEqual(res["counts"], {"done": 2}, res)
        self.assertFalse((self.lib / "Some Artist").exists())
        self.assertFalse((self.lib / "Other").exists())
        os.mkdir(self.lib / "Other")              # (the second album's folder can be made again)
        self.lib.chmod(0o555)                     # the first's cannot
        try:
            out = executor.undo(p["job"])
        finally:
            self.lib.chmod(0o755)
        self.assertTrue(g.exists(), out)          # the other album went back
        self.assertTrue(any("could not be made again" in o for o in out if o.startswith("PROBLEM")), out)
        self.assertTrue(any(self.dest.rglob("01*.flac")))   # the first stays where the job put it
        out = executor.undo(p["job"])
        self.assertFalse([o for o in out if o.startswith("PROBLEM")], out)
        self.assertEqual({p: p.read_bytes() for p in self.lib.rglob("*") if p.is_file() and p.suffix != ".flac"},
                         {p: b for p, b in before.items() if p.suffix != ".flac"})
        self.assertEqual(sorted(p for p in self.lib.rglob("*.flac")), sorted(p for p in before if p.suffix == ".flac"))
        self.assertEqual(list(self.dest.rglob("*.*")), [])


if __name__ == "__main__":
    unittest.main()
