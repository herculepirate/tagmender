# Eighteenth independent review (A): this test failed before its fix.
# Eighteenth independent review (A). A regression: passes with TM_REPO=prev.
# The tag backup's blobs (tag_albums._blob) are written with Path.write_bytes and never fsynced,
# while the journal line that refers to them is (Journal.write: os.fsync). After a power loss
# during or soon after the job, the journal can be on disk with a blob that is empty or cut
# short (delayed allocation: a new file's data is not on disk until written back). Today's
# records restore those blobs byte for byte with no check that a blob still is what its name
# (its SHA-1) says:
#  - an M4A file's moov blob, empty: _put_moov cuts the moov atom out of the file - its tags,
#    sample tables and codec setup. Undo says "put back", the audio check passes (it hashes only
#    mdat), and the file no longer plays;
#  - an MP3 file's ID3v2 blob, empty: _put_head strips the tag - every tag the file had before
#    the job is gone, and undo says "put back".
# The previous version parsed its blobs before writing (ID3(tmp) raised on a damaged one; MP4
# values were in the journal itself) and left the file alone, reporting a problem.
# Fix: fsync each blob (and the blobs folder) before the journal line that names it; in
# restore_tags check sha1(blob) == its name before writing anything (refuse: "backup damaged"),
# and for raw heads check the blob parses (ID3 header size, fLaC + last-block flag, moov atom
# size == blob length).
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _review18a_common import Base, decoded, encode, executor, run_and_undo, tagio, target  # noqa: E402


def power_loss(job):
    """What a power cut can leave of blobs written without fsync: empty files."""
    for b in (executor.job_dir(job) / "blobs").iterdir():
        b.write_bytes(b"")


class DamagedBlobUndoBreaksFile(Base):
    def test_m4a(self):
        fs = [encode(self.lib / "A" / "m" / f"0{n} t.m4a", 440 + 100 * n, "-c:a", "aac") for n in (1, 2)]
        played = [decoded(f) for f in fs]
        _res, out = run_and_undo(self, fs, between=power_loss)
        for f, want in zip(fs, played):
            self.assertEqual(decoded(f), want, f"{f.name}: no longer plays after undo: {out}")

    def test_mp3_tags(self):
        fs = [encode(self.lib / "A" / "p" / f"0{n} t.mp3", 440 + 100 * n, "-c:a", "libmp3lame") for n in (1, 2)]
        for n, f in enumerate(fs):
            tagio.write(f, dict(target(n + 1, f"Mine {n + 1}", album="My Album"), comment=["my notes"]))
        mine = [tagio.read(f) for f in fs]
        _res, out = run_and_undo(self, fs, between=power_loss)
        for f, m in zip(fs, mine):
            if not any(x.startswith("PROBLEM") and f.name in x for x in out):
                self.assertEqual(tagio.read(f).get("comment"), m.get("comment"),
                                 f"{f.name}: undo said it put the file back, and its tags are gone: {out}")


if __name__ == "__main__":
    unittest.main()
