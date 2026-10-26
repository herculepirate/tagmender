# Seventeenth independent review (A): this test failed before its fix.
# Seventeenth independent review (A). A regression: passes with TM_REPO=prev.
# A job recorded by the previous version keeps "trailer" (MP3) and "flac_ends" (FLAC) in its
# tag backups, but no "v". executor._same_backup now takes every record without "v" for one
# from before TagMender kept the trailers (legacy=True), and compares it with a legacy record
# of the file - which has no "trailer" / "flac_ends" (and merges an ID3v1 into the ID3v2
# view). So no MP3 or FLAC file of a job made by the previous version ever "matches its
# backup": undo of a plain move (nothing was written) rewrites every file from the record -
#  - an MP3 with an ID3v2.2 tag comes back with an ID3v2.3 tag (mutagen cannot write v2.2);
#  - every file gets a new modification time (players and Picard rescan the whole album);
#  - a FLAC file's Vorbis comments are rewritten (keys lower-cased, their order changed).
# The previous version leaves those files alone on undo: they are as the job found them.
# And an undo of such a job that was cut short (by the previous version: a file whose old tags
# it had put back, not yet journalled as done) is now refused for that file - "its tags or
# pictures were edited after the job; left as they are" (executor._undo: tags_sig differs and
# _matches_backup is False) - the job ends "undone with problems" and asks for --force.
import json
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _review17a_common import Base, as_prev_job, bare_mp3, executor, pl, tagio, target, v22_tag  # noqa: E402


class PrevJobUndoRewritesFiles(Base):
    def move_job(self, fs, tg):
        prof = self.profile(operation="move", destination=str(self.dest))
        prop = self.proposal(source_dir=str(fs[0].parent), files=[str(f) for f in fs], targets=tg)
        p = pl.build([prop], prof)
        self.assertEqual([fe["diff"] for fe in p["albums"][0]["files"]], [[] for _ in fs])   # a plain move
        orig = [(f.read_bytes(), os.stat(f).st_mtime_ns) for f in fs]
        res = executor.Executor(p).run()
        self.assertEqual(res["counts"], {"done": 1})
        as_prev_job(res["job"], {i: (o[0], f.suffix) for i, (f, o) in enumerate(zip(fs, orig))})
        out = executor.undo(res["job"])
        return orig, out

    def check(self, fs, orig, out):
        for f, (data, mt) in zip(fs, orig):
            self.assertTrue(f.exists(), out)
            self.assertEqual(f.read_bytes()[:4], data[:4], f"{f.name}: its tag changed kind on undo: {out}")
            self.assertEqual(f.read_bytes(), data, f"{f.name}: rewritten by the undo of a plain move: {out}")
            self.assertEqual(os.stat(f).st_mtime_ns, mt, f"{f.name}: rewritten by the undo of a plain move")

    def test_mp3_with_id3v22_tag(self):
        alb = self.lib / "In" / "x"
        fs = [bare_mp3(alb / f"0{n} t.mp3", 440 + 100 * n) for n in (1, 2)]
        for f in fs:
            f.write_bytes(v22_tag(tagmender=True) + f.read_bytes())
        orig, out = self.move_job(fs, self._targets_as_files(fs))
        self.check(fs, orig, out)

    def test_flac(self):
        fs = [self.f1, self.f2]
        for f in fs:
            tagio.write(f, {"tagmender": ["Resolved"]})
        orig, out = self.move_job(fs, self._targets_as_files(fs))
        self.check(fs, orig, out)

    def test_mp3_v24(self):
        alb = self.lib / "In" / "y"
        fs = [bare_mp3(alb / f"0{n} t.mp3", 440 + 100 * n) for n in (1, 2)]
        for f, t in zip(fs, [target(1, "First"), target(2, "Second")]):
            tagio.write(f, dict(t, tagmender=["Resolved"]))
        orig, out = self.move_job(fs, self._targets_as_files(fs))
        self.check(fs, orig, out)

    def test_undo_cut_short_then_finished(self):
        alb = self.lib / "In" / "z"
        fs = [bare_mp3(alb / f"0{n} t.mp3", 440 + 100 * n) for n in (1, 2)]
        for f in fs:
            tagio.write(f, {"title": ["Old"], "tagmender": ["Resolved"]})
        orig = [f.read_bytes() for f in fs]
        prop = self.proposal(source_dir=str(alb), files=[str(f) for f in fs],
                             targets=[target(1, "First"), target(2, "Second")])
        res = executor.Executor(pl.build([prop], self.profile())).run()
        self.assertEqual(res["counts"], {"done": 1})
        jd = executor.job_dir(res["job"])
        as_prev_job(res["job"], {i: (o, ".mp3") for i, o in enumerate(orig)})
        # the previous version's undo, cut short: file 1's old tags put back, not journalled as done
        fs[1].write_bytes(orig[1])
        with open(jd / "journal.jsonl", "a") as fh:
            fh.write(json.dumps({"ev": "undo_begin"}) + "\n")
            fh.write(json.dumps({"ev": "undo_file_begin", "key": [0, 1]}) + "\n")
        out = executor.undo(res["job"])
        self.assertFalse([x for x in out if x.startswith("PROBLEM")], out)
        self.assertEqual([f.read_bytes() for f in fs], orig)

    def _targets_as_files(self, fs):
        return [{k: v for k, v in tagio.read(f).items() if not k.startswith("~")} for f in fs]


if __name__ == "__main__":
    unittest.main()
