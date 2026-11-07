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
