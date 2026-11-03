# Twenty-second independent review (A). A regression: passes with TM_REPO=prev.
# executor._sig_is (new in this round): for a fingerprint recorded before 609b541 (no "v2:"
# prefix) that matches the legacy=1 reading but not legacy=2, it now restores the job's backup
# onto a scratch copy and requires every described comment ("comment:<desc>", "lyrics:<desc>")
# to be the same in the backup as in the file now - on the reasoning that "that version never
# wrote them". But in a FLAC/Ogg/APE file a plain COMMENT whose text ends in "(...)" ("Exact
# Audio Copy (ripper)", "Remastered (2011)") IS a described comment to today's reader
# (comment:ripper), and the old version wrote and removed plain COMMENTs freely. So for an old
# job that wrote such a comment (the release's "comment" "Remastered (2011)"), or removed one,
# the backup and the file differ in "comment:2011" by the job's own doing: _sig_is says "edited since",
# and undo refuses to put the file back ("edited after the job; left as they are") - the
# user's old job can no longer be undone without --force. The previous version undid it.
# Fix: compare the described frames of the file with what the JOB left (the backup with the
# job's own changes applied, or the plan's after-values), not with the backup as it was before
# the job; or only apply the check to ID3 COMM/USLT frames with a description.
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _review22a_common import Base, encode, executor, pl, tagio, target  # noqa: E402


def legacy_sig(f):
    """The fingerprint a version before 609b541 recorded."""
    try:
        return executor._tags_sig(f, legacy=True)
    except TypeError:                                 # (the previous version: one reading)
        return executor._tags_sig(f)


class LegacyVorbisComment(Base):
    def test_old_job_that_wrote_a_comment_is_undone(self):
        from mutagen.flac import FLAC
        d = self.lib / "Artist A" / "2001 - Album One"
        f = encode(d / "01 a.flac", 440)
        x = FLAC(str(f))
        x["TITLE"] = ["a"]
        x.save()
        before = f.read_bytes()
        # the release's comment, as Picard's "comment" (a version before 609b541 wrote it as a
        # plain COMMENT, its text as it is)
        prop = self.proposal(source_dir=str(d), files=[str(f)],
                             targets=[target(1, "One", comment=["Remastered (2011)"])])
        res = executor.Executor(pl.build([prop], self.profile())).run()
        self.assertEqual(res["counts"], {"done": 1}, res)
        from mutagen.flac import FLAC as F
        self.assertEqual(F(str(f))["COMMENT"], ["Remastered (2011)"])
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
        out = executor.undo(res["job"])                 # nothing was edited since the job
        self.assertNotIn("COMMENT", FLAC(str(f)), f"the old job was not undone: {out}")
        self.assertEqual(f.read_bytes(), before, out)


if __name__ == "__main__":
    unittest.main()
