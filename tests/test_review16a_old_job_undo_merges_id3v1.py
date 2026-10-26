# Sixteenth independent review (A): this test failed before its fix.
# Sixteenth independent review (A). A regression: passes with TM_REPO=prev.
# A job recorded before this version kept an MP3's ID3v2 backup as mutagen loads it - with
# the ID3v1 tag's fields merged in (load_v1=True: an ID3v1 comment, genre, year the ID3v2 tag
# lacks become COMM:ID3v1 Comment, TCON, TDRC frames) - and no "trailer". backup_tags now
# reads the ID3v2 tag with load_v1=False, and executor._same_backup only drops "trailer" and
# "flac_ends" for such a job: the ID3v2 blobs differ, so a file that is exactly as the backup
# has it no longer "matches its backup". Undo (_restore -> _tags_sig_backup_free) then rewrites
# it from the old record - and merges the ID3v1 fields into the file's ID3v2 tag. Here a plain
# move (no tag changed) of MP3s carrying an ID3v1 tag, undone after the upgrade: the files come
# back with frames they never had (COMM:ID3v1 Comment, TCON), and differ byte for byte.
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _review16a_common import V1, Base, append, bare_mp3, executor, pl, target  # noqa: E402

import tagio  # noqa: E402
from mutagen.id3 import ID3  # noqa: E402


def prev_backup_tags():
    """backup_tags as the previous version wrote job records (its record kept as it was)."""
    import tag_albums as ta
    return lambda path, blobs: ta.backup_tags(path, blobs, legacy=True)


class OldJobUndoMergesId3v1(Base):
    def job_as_before(self, fs, tg):
        prof = self.profile(operation="move", destination=str(self.dest))
        prop = self.proposal(source_dir=str(fs[0].parent), files=[str(f) for f in fs], targets=tg)
        p = pl.build([prop], prof)
        self.assertEqual([fe["diff"] for fe in p["albums"][0]["files"]], [[], []])     # a plain move
        orig = [f.read_bytes() for f in fs]
        res = executor.Executor(p).run()
        self.assertEqual(res["counts"], {"done": 1})
        # the job's journal as the previous version wrote it
        jd = executor.job_dir(res["job"])
        old_backup = prev_backup_tags()
        recs = []
        for o in orig:
            q = Path(tempfile.mkdtemp()) / "x.mp3"
            q.write_bytes(o)
            recs.append(old_backup(q, jd / "blobs"))
        lines = []
        for ln in (jd / "journal.jsonl").read_text().splitlines():
            e = json.loads(ln)
            if (e.get("backup") or {}).get("kind") == "tags":
                e["backup"]["rec"] = recs[e["idx"]]
            lines.append(json.dumps(e))
        (jd / "journal.jsonl").write_text("\n".join(lines) + "\n")
        out = executor.undo(res["job"])
        return orig, out

    def test_id3v2_and_id3v1(self):
        alb = self.lib / "In" / "x"
        fs = [bare_mp3(alb / f"0{n} t.mp3", 440 + 100 * n) for n in (1, 2)]
        tg = [target(1, "First"), target(2, "Second")]
        for f, t in zip(fs, tg):
            tagio.write(f, dict(t, tagmender=["Resolved"]))
            append(f, V1)
        self.assertNotIn("COMM:ID3v1 Comment:eng", ID3(str(fs[0]), load_v1=False))
        orig, out = self.job_as_before(fs, tg)
        self.check(fs, orig, out)

    def check(self, fs, orig, out):
        for f, o in zip(fs, orig):
            q = Path(tempfile.mkdtemp()) / "o.mp3"
            q.write_bytes(o)
            try:
                was = sorted(ID3(str(q), load_v1=False).keys())
            except Exception:
                was = None
            try:
                now = sorted(ID3(str(f), load_v1=False).keys())
            except Exception:
                now = None
            self.assertEqual(now, was, f"{f.name}: the ID3v1 fields went into its ID3v2 tag on undo: {out}")
            self.assertEqual(f.read_bytes(), o)


if __name__ == "__main__":
    unittest.main()
