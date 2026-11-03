# Eighteenth independent review (A): this test failed before its fix.
# Eighteenth independent review (A). Not a regression: the previous version does the same.
# (Found next to the backup/restore area: the job's own write, and the audio check meant to
# catch it.) A fragmented MP4 with more than one fragment (moov, then moof + mdat pairs, each
# tfhd holding the absolute offset of its audio): the job's tag write grows moov, which moves
# every fragment, but mutagen (MP4Tags.__update_offsets: atoms[b"moof"] is only the FIRST moof)
# updates the base_data_offset of the first fragment only. Every later fragment now points the
# growth (about 1.6 KB) before its audio: the file plays its first fragment, then garbage /
# decoder errors. The executor's check after writing (payload.digest == before) hashes only
# the mdat contents, which are unchanged, so the job reports "done". Undo does not bring the
# file back either (today: fragment 1 is then off, see test_review18a_fragmented_m4a_undo_
# breaks_audio.py; the previous version: fragments 2.. stay off).
# Fix: refuse to write tags to an MP4 that has moof atoms (or fix up every moof/tfhd and
# mfra/tfra after mutagen's save), and make payload.digest for MP4 cover the sample offsets
# (stco/co64/tfhd + trun data offsets resolved to the bytes they point at) - or decode-check.
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _review18a_common import Base, decoded, encode, executor, pl, target, tfhd_bases  # noqa: E402

FRAGS = ["-c:a", "aac", "-movflags", "frag_keyframe+empty_moov", "-frag_duration", "500000"]


class FragmentedM4aJobBreaksAudio(Base):
    def test_tag_job_and_its_undo(self):
        fs = [encode(self.lib / "A" / "m" / f"0{n} t.m4a", 440 + 100 * n, *FRAGS, duration=3) for n in (1, 2)]
        orig = [f.read_bytes() for f in fs]
        played = [decoded(f) for f in fs]
        self.assertGreater(len(tfhd_bases(orig[0])), 1)
        prop = self.proposal(source_dir=str(fs[0].parent), files=[str(f) for f in fs],
                             targets=[target(1, "First"), target(2, "Second")])
        res = executor.Executor(pl.build([prop], self.profile())).run()
        if res["counts"] == {"done": 1}:
            for f, want in zip(fs, played):
                self.assertTrue(all(m == b for m, b in tfhd_bases(f.read_bytes())),
                                f"{f.name}: fragments point away from their audio after the job")
                self.assertEqual(decoded(f), want, f"{f.name}: the job 'done' and the file no longer plays")
            out = executor.undo(res["job"])
            self.assertEqual([f.read_bytes() for f in fs], orig, out)


if __name__ == "__main__":
    unittest.main()
