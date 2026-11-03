# Twenty-first independent review (A): this test failed before its fix.
# Twenty-first independent review (A). A regression: passes with TM_REPO=prev.
# A fingerprint without "v2:" is now compared with tagio.read(legacy=True), which leaves out
# every ID3 comment with a description (COMM "comment::<desc>", "comment:deu:<desc>"), and reads
# all USLT frames as plain "lyrics" whatever their language and description. So for a job of an
# earlier version (fingerprint without prefix), an edit the user made after the job to such a
# frame - a comment written by Mp3tag/foobar2000 with a description ("Songs-DB_Custom1",
# "MusicMatch_Mood", "ripper"), an iTunes comment rewritten by iTunes - is invisible: _sig_is
# says "unchanged", undo puts the job's backup back over it, and the user's edit is gone
# without a word (no "edited after the job; left as they are"). The previous version read
# described comments in its fingerprint, saw the edit, and left the file alone.
# Fix: for a legacy fingerprint, also require that the frames the legacy reading cannot see are
# as the job left them - e.g. record nothing new, but compare the described COMM/USLT frames of
# the file with the backup's / the job's written tags (they are in the backup and the plan), or
# treat a file with any described COMM/USLT under a legacy fingerprint as edited (refuse
# without --force).
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _review21a_common import Base, executor, make, pl, tagio, target  # noqa: E402


def legacy_sig(f):
    """The fingerprint a version before 609b541 recorded (the file has no described frames)."""
    try:
        return executor._tags_sig(f, legacy=True)
    except TypeError:                                 # (the previous version: one reading)
        return executor._tags_sig(f)


class LegacySignatureMissesEdit(Base):
    def test_user_comment_after_old_job_survives_undo(self):
        from mutagen.id3 import COMM, ID3, TIT2
        f = make(self.lib / "A" / "x" / "01 t.mp3")
        i = ID3()
        i.add(TIT2(encoding=3, text=["Old"]))
        i.save(str(f))
        prop = self.proposal(source_dir=str(f.parent), files=[str(f)], targets=[target(1, "One")])
        res = executor.Executor(pl.build([prop], self.profile())).run()
        self.assertEqual(res["counts"], {"done": 1}, res)
        # the job as a version before 609b541 recorded it: its fingerprint without "v2:"
        jl = executor.job_dir(res["job"]) / "journal.jsonl"
        evs = [json.loads(x) for x in jl.read_text().splitlines() if x.strip()]
        n = 0
        for e in evs:
            if e.get("tags_sig"):
                e["tags_sig"] = legacy_sig(f)
                n += 1
        self.assertTrue(n)
        jl.write_text("".join(json.dumps(e) + "\n" for e in evs))
        # the user then adds a comment with a description (Mp3tag, foobar2000 ...)
        i = ID3(str(f))
        i.add(COMM(encoding=3, lang="eng", desc="Songs-DB_Custom1", text=["my rating: 5"]))
        i.save(str(f))
        out = executor.undo(res["job"])
        t = tagio.read(f)
        mine = [v for k, v in t.items() if k.startswith("comment")]
        self.assertEqual(mine, [["my rating: 5"]],
                         f"undo put the old tags back over the user's later edit without a word: {out}")


if __name__ == "__main__":
    unittest.main()
