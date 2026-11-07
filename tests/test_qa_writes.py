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


if __name__ == "__main__":
    unittest.main()
