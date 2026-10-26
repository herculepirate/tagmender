# Eighteenth independent review (A): this test failed before its fix.
# Eighteenth independent review (A). A regression: passes with TM_REPO=prev.
# A record of today's kind keeps an MP4 file's whole moov atom (tags AND the stco/co64 tables
# that hold the absolute file offset of every audio chunk), and undo (restore_tags ->
# _put_moov) writes it back over whatever moov the file has now, wherever it is. When the
# file's layout changed after the job while its audio did not - the user "optimised it for
# streaming" (qt-faststart, ffmpeg -movflags +faststart, MP4Box, iTunes / foobar2000 rewriting
# the file with the moov in front) - the old offsets are written into a moov that now sits in
# front of the audio: every chunk offset is wrong and the file no longer plays. Nothing stops it:
#  - executor undo (no --force): the tags are as the job left them (tags_sig equal), the audio
#    data is unchanged (payload.digest hashes only the mdat contents), so the file is "put back";
#  - tag_albums.undo (enrich.py / web_tag.py / Picard runs) restores without any check.
# The previous version wrote only the old tags (mutagen updates the offsets): audio intact.
# Fix: restore the moov raw only when the file around it is still as the job left it (same moov
# offset and mdat offsets as recorded - store them in the record), else write the tags through
# mutagen (as kind 2 did); or rebase the old stco/co64/tfhd values onto the current mdat
# positions before writing. A decode or "offsets land in mdat" check after the restore would
# also catch it.
import json
import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _review18a_common import Base, atoms, decoded, encode, run_and_undo, ta, tagio  # noqa: E402


def faststart(f):
    """What qt-faststart (ffmpeg's tool) does: the moov moved in front of the audio."""
    tmp = f.with_name(".fs-" + f.name)
    subprocess.run(["qt-faststart", str(f), str(tmp)], check=True, capture_output=True)
    tmp.replace(f)


def top(f):
    return [k for k, _p, _n, d in atoms(f.read_bytes()) if d == 0]


class M4aMoovRelocatedUndoBreaksAudio(Base):
    def files(self):
        fs = [encode(self.lib / "A" / "m" / f"0{n} t.m4a", 440 + 100 * n, "-c:a", "aac") for n in (1, 2)]
        for f in fs:
            self.assertEqual(top(f)[-1], b"moov")                  # ffmpeg's default: moov at the end
        return fs

    def test_executor_undo(self):
        fs = self.files()
        played = [decoded(f) for f in fs]
        before = {}

        def between(job):
            for f in fs:
                before[f] = tagio.read(f)
                faststart(f)
                self.assertEqual(top(f)[1], b"moov")
                self.assertEqual(tagio.read(f), before[f])          # the tags: as the job left them

        _res, out = run_and_undo(self, fs, between=between)
        for f, want in zip(fs, played):
            self.assertEqual(decoded(f), want, f"{f.name}: no longer plays after undo: {out}")

    def test_tag_albums_undo(self):
        # a run as enrich.py / web_tag.py records it, then the file optimised, then --undo
        fs = self.files()
        played = [decoded(f) for f in fs]
        out_dir = self.tmp / "runs"
        run_dir = out_dir / "run-1"
        (run_dir / "blobs").mkdir(parents=True)
        recs = [{"path": str(p), "new_path": str(p), "key": "", "backup": ta.backup_tags(p, run_dir / "blobs")}
                for p in fs]
        (run_dir / "journal.json").write_text(json.dumps([{"album": str(fs[0].parent), "mbid": None,
                                                           "files": recs, "extras": [], "moves": []}]))
        for f in fs:
            tagio.write(f, {"genre": ["Rock"], "comment": ["x" * 3000]})
            faststart(f)
        self.assertEqual(ta.undo(out_dir, "1"), 0)
        for f, want in zip(fs, played):
            self.assertEqual(decoded(f), want, f"{f.name}: no longer plays after undo")


if __name__ == "__main__":
    unittest.main()
